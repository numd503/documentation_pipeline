"""Регистрации в DI-контейнере из вызовов `services.Add*`.

Видно только то, что записано синтаксически. Динамические регистрации —
сканирование сборок, конвенции через рефлексию, маркерные интерфейсы вроде
`ITransientDependency` — сюда не попадают принципиально: вызова нет, значит
и разбирать нечего. Маркерные интерфейсы ловятся правилами классификации
по базовому типу (T14), а не здесь.

Узлы вызовов приходят снаружи: запрос выполняет `parser.py`, которому
и принадлежит вся работа с tree-sitter. Здесь остаётся только смысл.
"""

import re

from tree_sitter import Node

from docpipe.model import Confidence, DiRegistration, Lifetime

_METHOD = re.compile(r"^(?:Try)?Add(Scoped|Singleton|Transient|HostedService)$")

# Вид времени жизни ищется в имени самодельного метода подстрокой: имена вроде
# `AddSingletonAs` или `AddCashflowScoped` его называют, и другого источника
# у нас нет. Не назвал — `unknown`, а не догадка: неверное время жизни
# в отчёте выглядит как факт.
_LIFETIME_MARKERS: tuple[tuple[str, Lifetime], ...] = (
    ("Singleton", "singleton"),
    ("Scoped", "scoped"),
    ("Transient", "transient"),
    ("HostedService", "hosted"),
)

_LIFETIMES: dict[str, Lifetime] = {
    "Scoped": "scoped",
    "Singleton": "singleton",
    "Transient": "transient",
    "HostedService": "hosted",
}


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _called_name(call: Node) -> tuple[str, Node | None]:
    """Имя вызванного метода и его список типов-аргументов, если он есть.

    Имя лежит в поле `name` у `member_access_expression` и бывает двух видов:
    `identifier` (`services.AddScoped(...)`) либо `generic_name`
    (`services.AddScoped<IFoo, Foo>(...)`). `type_argument_list` во втором случае
    принадлежит **`generic_name`**, а не `invocation_expression`.

    Получатель может быть цепочкой (`builder.Services`) — это вложенный
    `member_access_expression`, на разбор имени не влияющий.
    """
    access = call.child_by_field_name("function")
    if access is None:
        return "", None

    name = access.child_by_field_name("name")
    if name is None:
        return "", None
    if name.type == "generic_name":
        arguments = next((c for c in name.children if c.type == "type_argument_list"), None)
        return _text(name.children[0]), arguments
    return _text(name), None


def _typeof_arguments(call: Node) -> list[Node | None]:
    """Аргументы вызова: узел типа для `typeof(X)`, `None` для всего остального.

    `typeof` — полноценная форма регистрации, а не запасной вариант:
    `TryAddTransient(typeof(IKernelAccessor<>), typeof(KernelAccessor<>))`.
    В ABP и eShopOnWeb так записаны 47 регистраций из 267 — больше, чем
    двумя типами-аргументами.
    """
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []

    found: list[Node | None] = []
    for argument in (c for c in arguments.named_children if c.type == "argument"):
        value = argument.named_children[0] if argument.named_children else None
        if value is not None and value.type == "typeof_expression":
            found.append(value.child_by_field_name("type"))
        else:
            found.append(None)
    return found


def _types(call: Node, type_arguments: Node | None) -> tuple[str, str | None, Confidence] | None:
    """`(service_type, impl_type, confidence)` или `None`, если тип не назван.

    Порядок разбора:

    - два типа-аргумента (`AddScoped<IFoo, Foo>`) — сервис и реализация;
    - один (`AddTransient<Foo>`) — тип регистрируется сам на себя;
    - `typeof(X), typeof(Y)` — то же, что два типа-аргумента, и такая же
      уверенность: имена записаны явно;
    - `typeof(X)` — то же, что один;
    - `typeof(X), <что-то ещё>` — сервис известен, реализация вычисляется
      в рантайме, `confidence = "medium"`.

    Всё остальное — лямбда, переменная, приведение типа — **пропускается**.
    План предлагал в этом случае брать «service_type из текста», но текстом
    там оказывается тело лямбды: в манифест попал бы мусор вида
    `sp => sp.GetRequiredService<X>()` в поле типа. Регистрация, не называющая
    ни одного типа, документации всё равно ничего не даёт. Таких в ABP
    и eShopOnWeb 50 из 267.
    """
    if type_arguments is not None:
        names = [_text(node) for node in type_arguments.named_children]
        if len(names) >= 2:
            return names[0], names[1], "high"
        if len(names) == 1:
            return names[0], names[0], "high"
        return None

    typeofs = _typeof_arguments(call)
    if not typeofs or typeofs[0] is None:
        return None

    service = _text(typeofs[0])
    if len(typeofs) == 1:
        return service, service, "high"
    if typeofs[1] is not None:
        return service, _text(typeofs[1]), "high"
    return service, None, "medium"


def _fluent_services(call: Node) -> list[str]:
    """Интерфейсы, названные продолжением цепочки: `.As<I>()`, `.AsOptional<I>()`.

    Самодельные обёртки почти всегда устроены так: `AddSingletonAs<X>()`
    называет реализацию тип-аргументом, а интерфейс приезжает следующим
    звеном. Разбор одного вызова видит только `X` и записывает регистрацию
    типа на себя — в squidex таких 310 из 361, то есть связывание по
    контейнеру не работает там вовсе, показывая при этом непустое число.

    Цепочка в дереве растёт наружу: `((s.AddSingletonAs<X>()).As<I>()).As<J>()`.
    Поэтому идём вверх по родителям, а не вниз по детям, и берём только те
    звенья, у которых есть тип-аргумент: `AsSelf()` без него — это и есть
    регистрация на себя, менять её нечем.
    """
    services: list[str] = []
    current = call
    while True:
        access = current.parent
        if access is None or access.type != "member_access_expression":
            break
        outer = access.parent
        if outer is None or outer.type != "invocation_expression":
            break
        name = access.child_by_field_name("name")
        if name is None:
            break
        if name.type == "generic_name" and _text(name.children[0]).startswith("As"):
            arguments = next((c for c in name.children if c.type == "type_argument_list"), None)
            if arguments is not None:
                types = [c for c in arguments.named_children if c.type != "comment"]
                if len(types) == 1:
                    services.append(_text(types[0]))
        current = outer
    return services


def _lifetime_of(name: str) -> Lifetime:
    for marker, lifetime in _LIFETIME_MARKERS:
        if marker in name:
            return lifetime
    return "unknown"


def extract_registrations(
    calls: list[Node], path: str, extra_methods: frozenset[str] = frozenset()
) -> list[DiRegistration]:
    """Регистрации DI из найденных вызовов. Порядок — `(line, service_type, impl_type)`.

    `extra_methods` — самодельные обёртки репозитория (`AddSingletonAs`,
    `AddCashflowServices`). Список приходит из конфигурации и пуст по
    умолчанию: угадывать имена нельзя — правило, придуманное по одному
    репозиторию, на другом сработает не на том вызове. Без него репозиторий
    со своей обёрткой даёт ноль регистраций, и это ноль без сообщения:
    в squidex так не видно **ни одной** из 300+ регистраций `AddSingletonAs`,
    а отчёт показывает 67 — те, что записаны стандартной формой.

    Фамильная черта таких обёрток — продолжение цепочки: у `AddSingletonAs<T>()`
    интерфейс называет `.As<I>()` следующим звеном, а не тип-аргумент. Здесь
    оно не разбирается: такой вызов даёт регистрацию типа на себя, что
    и попадает в отчёт отдельной строкой.
    """
    found: list[DiRegistration] = []

    for call in calls:
        name, type_arguments = _called_name(call)
        match = _METHOD.match(name)
        if match is None and name not in extra_methods:
            continue

        types = _types(call, type_arguments)
        if types is None:
            continue

        service_type, impl_type, confidence = types
        # Продолжение цепочки называет интерфейс у самодельных обёрток.
        # Смотрим его только там, где регистрация вышла «на себя»: у формы
        # с двумя типами оба уже названы, и звено `.As<…>()` после неё
        # означало бы что-то другое, а не замену сервиса.
        #
        # Условие именно «сервис равен реализации», а не «реализация не
        # названа»: один тип-аргумент даёт пару `(X, X)`, а не `(X, None)`,
        # и проверка на `None` не срабатывала **никогда** — при этом
        # тридцать пять регистраций всё же разрешались (те, где тип назван
        # через `typeof`), так что число выглядело живым.
        chained = _fluent_services(call) if service_type == impl_type else []
        if chained:
            for service in sorted(set(chained)):
                found.append(
                    DiRegistration(
                        service_type=service,
                        impl_type=service_type,
                        lifetime=_LIFETIMES[match.group(1)] if match else _lifetime_of(name),
                        confidence=confidence,
                        file=path,
                        line=call.start_point[0] + 1,
                    )
                )
            continue
        found.append(
            DiRegistration(
                service_type=service_type,
                impl_type=impl_type,
                lifetime=_LIFETIMES[match.group(1)] if match else _lifetime_of(name),
                confidence=confidence,
                file=path,
                line=call.start_point[0] + 1,
            )
        )

    found.sort(key=lambda r: (r.line, r.service_type, r.impl_type or ""))
    return found
