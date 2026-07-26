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


def extract_registrations(calls: list[Node], path: str) -> list[DiRegistration]:
    """Регистрации DI из найденных вызовов. Порядок — `(line, service_type, impl_type)`."""
    found: list[DiRegistration] = []

    for call in calls:
        name, type_arguments = _called_name(call)
        match = _METHOD.match(name)
        if match is None:
            continue

        types = _types(call, type_arguments)
        if types is None:
            continue

        service_type, impl_type, confidence = types
        found.append(
            DiRegistration(
                service_type=service_type,
                impl_type=impl_type,
                lifetime=_LIFETIMES[match.group(1)],
                confidence=confidence,
                file=path,
                line=call.start_point[0] + 1,
            )
        )

    found.sort(key=lambda r: (r.line, r.service_type, r.impl_type or ""))
    return found
