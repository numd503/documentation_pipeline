"""Кандидаты в ключи настройки: из чего строится вопрос человеку.

Ключи вроде `di_methods` нельзя заполнить умолчанием: имя, придуманное по
одному репозиторию, на другом совпадёт не с тем вызовом. Но и оставить их
пустыми нельзя молча — без `di_methods` на squidex видно 67 регистраций
из 421, и ноль незаметен. Поэтому инструмент не угадывает значение, а
считает факты, по которым его предлагает агент, и человек решает.

Каждый вид кандидатов — функция над результатом прогона, возвращающая
модель отчёта, и форматтер текста. `candidates()` — единая точка входа
для CLI и сервера настройки (S27): вид → нужный прогон → отчёт.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig, ScopeConflict, resolve_input
from docpipe.dotnet.di import is_standard_method
from docpipe.dotnet.facts import bare_type
from docpipe.emit import ScanResult, dispatch_name, dispatch_names, split_type_arguments
from docpipe.emit import run as run_scan
from docpipe.hashing import stable_json_dumps
from docpipe.model import Construction, Member, RegistrationCall, SourceSpan, Symbol

# Длина страницы по умолчанию. Агент контура обрезает вывод инструмента,
# и «таких нет» без `total` неотличимо от «не показали» (правило 5 плана).
DEFAULT_LIMIT: Final = 20

# Метод, названный с типом хотя бы дважды. Один вызов — не обёртка, которую
# стоит заводить в настройку, а лямбда-форма без типа регистрацией не станет
# и с ключом (`dotnet/di.py`, `_types`).
MIN_CALLS_WITH_TYPES: Final = 2

# База, которую стоит назвать интерфейсом диспетчеризации: хотя бы две
# реализации и два разных типа-запроса, объявленных в репозитории. Одна
# реализация — не диспетчеризация, а наследование; типы-запросы извне
# (`IClassFixture<WebApplicationFactory<Program>>`) — не запросы репозитория.
MIN_IMPLEMENTATIONS: Final = 2
MIN_REQUEST_TYPES: Final = 2

_TOP_RECEIVERS: Final = 3
_TOP_PACKAGES: Final = 3
_EXAMPLES: Final = 3


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputError(Exception):
    """Вход прогона негоден: неизвестный вид, отрицательные `limit`/`offset`, файл правил.

    Отдельный класс, а не `ValueError`: CLI отвечает на него кодом 2
    с сообщением, а любое другое исключение — это сбой, и прятать его
    под «ошибкой конфигурации» значит потерять трассировку.
    """


# --------------------------------------------------------------------------------------
# Кандидаты в `di_methods`
# --------------------------------------------------------------------------------------


class DiMethodCandidate(_Base):
    """Метод `Add*`, похожий на самодельную обёртку регистрации.

    `receiver_overlap` — доля вызовов, у которых получатель тот же, на котором
    в этом репозитории зовут стандартные `Add*`. Признак из данных
    репозитория, а не список имён: `AddSingletonAs` на squidex зовут
    на `services` в 271 случае из 275, `AddField` — на `schema`, `AddDays` —
    на дате.

    `declared_in` — необязательная подсказка: главную обёртку по объявлениям
    не найти, на squidex `AddSingletonAs` объявлен в пакете, а 41 объявленный
    `AddSquidex*(this IServiceCollection …)` — агрегаторы, а не обёртки.
    """

    method: str
    calls: int
    calls_with_types: int
    files: int
    receivers: list[tuple[str, int]]
    receiver_overlap: float
    declared_in: str | None = None
    configured: bool
    examples: list[str]


class DiMethodCandidates(_Base):
    """Отчёт `setup candidates di-methods`.

    `standard_calls` и `standard_receivers` — база, от которой считается
    `receiver_overlap`. Без неё ноль пересечения у всех кандидатов
    неотличим от репозитория, где стандартных регистраций нет вовсе: тогда
    признак молчит, и порядок решает только число вызовов с типом.
    """

    schema_version: Literal["1.0"] = "1.0"
    standard_calls: int
    standard_receivers: list[tuple[str, int]]
    total: int
    offset: int
    items: list[DiMethodCandidate]


def _top(counter: Counter[str], size: int) -> list[tuple[str, int]]:
    """Самые частые значения: по убыванию счёта, при равенстве — по имени."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:size]


def _member_file(symbol: Symbol, member: Member) -> str | None:
    """Файл, в котором объявлен член.

    У члена своего пути нет: при слиянии `partial` он теряется
    (`dotnet/resolve.py`). Ищется по строке среди частей типа; строки двух
    частей из разных файлов могут перекрыться, и тогда берётся меньший путь —
    подсказке хватает детерминизма, а точность здесь не обещана.
    """
    spans = [span for span in symbol.sources if span.start <= member.line <= span.end]
    if spans:
        return min(span.path for span in spans)
    return min((span.path for span in symbol.sources), default=None)


def _extension_declarations(index: dict[str, Symbol]) -> dict[str, str]:
    """Имя метода-расширения → файл объявления (меньший путь, если их несколько).

    Признак расширения — `this ` в `Member.signature` при модификаторе
    `static`. `Graph.identity.parameter_types` для этого не годится: он
    выбрасывает `this` (`graph/identity.py`), и расширение там неотличимо
    от обычного статического метода.
    """
    found: dict[str, str] = {}
    for symbol in index.values():
        for member in symbol.members:
            if member.kind != "method" or "static" not in member.modifiers:
                continue
            if "this " not in member.signature:
                continue
            path = _member_file(symbol, member)
            if path is None:
                continue
            known = found.get(member.name)
            if known is None or path < known:
                found[member.name] = path
    return found


def _typed(call: RegistrationCall) -> bool:
    return call.type_args > 0 or call.typeof_args > 0


def _page[T](items: list[T], limit: int, offset: int) -> list[T]:
    """Страница списка. `limit = 0` — до конца, как у `symbols --limit 0`."""
    return items[offset:] if limit == 0 else items[offset : offset + limit]


def di_method_candidates(
    scan: ScanResult,
    settings: DocpipeConfig,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> DiMethodCandidates:
    """Кандидаты в `di_methods` по вызовам `Add*` этого прогона.

    Стандартные имена (`AddScoped`, `TryAddSingleton`…) в кандидаты не идут —
    они известны без настройки, — но по ним считается база получателей.
    Порядок — `(-round(receiver_overlap, 2), -calls_with_types, method)`:
    пересечение отделяет обёртку от `AddField`, число вызовов с типом —
    частую обёртку от редкой.
    """
    standard: Counter[str] = Counter()
    standard_calls = 0
    by_method: defaultdict[str, list[tuple[str, RegistrationCall]]] = defaultdict(list)
    for path, call in scan.registration_calls:
        if is_standard_method(call.method):
            standard_calls += 1
            # Неизвестный получатель базой не считается: пустая строка
            # совпала бы с любым другим неизвестным и дала бы пересечение
            # там, где о получателе не известно ничего.
            if call.receiver:
                standard[call.receiver] += 1
        else:
            by_method[call.method].append((path, call))

    configured = frozenset(settings.di_method_names)
    declarations = _extension_declarations(scan.index)

    found: list[DiMethodCandidate] = []
    for method, calls in by_method.items():
        with_types = sum(1 for _, call in calls if _typed(call))
        if with_types < MIN_CALLS_WITH_TYPES:
            continue
        overlap = sum(1 for _, call in calls if call.receiver in standard) / len(calls)
        # Примеры — сначала вызовы с типом: кандидат стоит в списке из-за них,
        # и три лямбды подряд показали бы человеку не ту форму.
        shown = sorted(calls, key=lambda item: (not _typed(item[1]), item[0], item[1].line))
        found.append(
            DiMethodCandidate(
                method=method,
                calls=len(calls),
                calls_with_types=with_types,
                files=len({path for path, _ in calls}),
                receivers=_top(Counter(call.receiver for _, call in calls), _TOP_RECEIVERS),
                receiver_overlap=round(overlap, 4),
                declared_in=declarations.get(method),
                configured=method in configured,
                examples=[f"{path}:{call.line}" for path, call in shown[:_EXAMPLES]],
            )
        )

    found.sort(key=lambda c: (-round(c.receiver_overlap, 2), -c.calls_with_types, c.method))
    return DiMethodCandidates(
        standard_calls=standard_calls,
        standard_receivers=_top(standard, _TOP_RECEIVERS),
        total=len(found),
        offset=offset,
        items=_page(found, limit, offset),
    )


def _receivers_text(receivers: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name or '—'} {count}" for name, count in receivers)


def format_di_methods(report: DiMethodCandidates) -> str:
    lines = [
        f"Кандидаты в di_methods: {report.total} "
        f"(нестандартные Add*, названные с типом не меньше {MIN_CALLS_WITH_TYPES} раз)."
    ]
    if report.standard_calls:
        lines.append(
            f"Стандартные регистрации: {report.standard_calls}; "
            f"получатели: {_receivers_text(report.standard_receivers) or '—'}."
        )
    else:
        lines.append(
            "Стандартных регистраций нет: пересечение получателей у всех кандидатов "
            "нулевое и ничего не говорит, порядок задаёт только число вызовов с типом."
        )

    for item in report.items:
        mark = "  [уже в di_methods]" if item.configured else ""
        lines += [
            "",
            f"{item.method}{mark}",
            f"  вызовов {item.calls}, с типом {item.calls_with_types}, файлов {item.files}; "
            f"пересечение получателей {item.receiver_overlap:.2f}",
            f"  получатели: {_receivers_text(item.receivers)}",
            f"  объявлен: {item.declared_in or 'не в репозитории'}",
            f"  примеры: {', '.join(item.examples)}",
        ]

    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


def _page_line(total: int, offset: int, shown: int) -> str:
    """Строка продолжения: без неё обрезанный список читается как полный."""
    if total == 0:
        return "Кандидатов нет."
    if shown == 0:
        return f"Показано 0 из {total}: --offset {offset} за концом списка."
    line = f"Показаны {offset + 1}–{offset + shown} из {total}."
    if offset + shown < total:
        line += f" Дальше: --offset {offset + shown}."
    return line


# --------------------------------------------------------------------------------------
# Кандидаты в `dispatch_interfaces`
# --------------------------------------------------------------------------------------


class DispatchCandidate(_Base):
    """Обобщённая база, похожая на интерфейс диспетчеризации по типу запроса.

    `interface` — FQN, если база объявлена в репозитории (`resolved`), иначе
    имя без квалификатора: внешний `IRequestHandler` резолву не поддаётся
    принципиально. В `dispatch_interfaces` пишется имя без квалификатора
    в обоих случаях — так ключ читает прогон (`emit.dispatch_name`).

    `exclusivity` — доля типов-запросов, которые встречаются первым
    аргументом обобщённой базы **только** у этой головы. Обобщённых баз-шумов
    больше, чем обработчиков (на eShopOnWeb `Specification` 8,
    `IEntityTypeConfiguration` 7 против двух `IRequestHandler`), и отделяет
    их именно это: запрос встречается аргументом только у своего
    обработчика, а сущность `Order` — у нескольких разных баз.

    `sent` — сколько раз тип-запрос создают (`new X(…)`) вне классов-
    реализаций этой головы: запрос, которого не отправляет никто, ребра
    диспетчеризации не даст и с ключом.

    `handler_members` — методы реализаций, в сигнатуре которых есть их
    тип-запрос, от частых к редким. Граф ведёт ребро диспетчеризации в метод
    `Handle` (`graph/binding.py`), а у `HandleEventAsync` и `Consume` — в тип;
    здесь это видно человеку до того, как он решит.

    `packages` — до трёх `PackageReference` модулей с реализациями, чьё
    пространство имён реализации импортируют (`using`), от частых к редким;
    у объявленной в репозитории базы — пусто. Пустой список у внешней
    базы не значит «библиотеки нет»: `Directory.*.props` разбор `.csproj`
    не читает (`dotnet/csproj.py`).

    `requests` — первые по имени типы-запросы: по ним человек отвечает
    на вопрос, а `IEntityTypeConfiguration` с запросами `Customer, Order`
    объясняет себя без открытия файлов.
    """

    interface: str
    resolved: bool
    implementations: int
    request_types: int
    exclusivity: float
    sent: int
    handler_members: list[str]
    packages: list[str]
    requests: list[str]
    configured: bool
    examples: list[str]


class DispatchCandidates(_Base):
    """Отчёт `setup candidates dispatch-interfaces`."""

    schema_version: Literal["1.0"] = "1.0"
    total: int
    offset: int
    items: list[DispatchCandidate]


def _generic_base(raw: str) -> tuple[str, list[str]] | None:
    """Голова и аргументы первой группы `<…>`: `I<A, B<C>>` → (`I`, [`A`, `B<C>`]).

    Скобка ищется парная, а не последняя: у
    `EndpointBaseAsync.WithRequest<A>.WithActionResult<B>` аргумент головы —
    `A`, а срез до последней `>` дал бы `A>.WithActionResult<B`. Голова —
    текст до первой `<`, как у `emit.collect_dispatch`: отметка «уже
    в настройке» обязана говорить о той же голове, которую найдёт прогон.
    """
    start = raw.find("<")
    if start <= 0:
        return None
    depth = 0
    for position in range(start, len(raw)):
        if raw[position] == "<":
            depth += 1
        elif raw[position] == ">":
            depth -= 1
            if depth == 0:
                arguments = split_type_arguments(raw[start + 1 : position])
                return (raw[:start].strip(), arguments) if arguments else None
    return None


def _inside(spans: list[SourceSpan], path: str, line: int) -> bool:
    return any(span.path == path and span.start <= line <= span.end for span in spans)


def _handler_members(symbol: Symbol, requests: set[str]) -> set[str]:
    """Методы, в сигнатуре которых есть один из типов-запросов этой реализации.

    Только методы: конструктор, принимающий запрос, назвал бы «обработчиком»
    имя самого класса, а точкой входа диспетчера он не бывает. Граница
    слова обязательна: `CreateOrderResult` запросом `CreateOrder` не является.
    """
    if not requests:
        return set()
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(requests))) + r")\b")
    return {
        member.name
        for member in symbol.members
        if member.kind == "method" and pattern.search(member.signature)
    }


def _imported_packages(
    symbol: Symbol, packages_of: dict[str, list[str]], usings: dict[str, list[str]]
) -> tuple[set[str], set[str]]:
    """`PackageReference` модуля реализации, пространство имён которых она импортирует.

    Не все пакеты модуля: их десятки, и первые три по алфавиту назвали бы
    на eShopOnWeb `Ardalis.ListStartupServices, Ardalis.Specification,
    AutoMapper…` вместо `MediatR` — подсказка о библиотеке, указывающая
    не на неё, хуже пустой.

    Два ответа: точное совпадение (`using MediatR` ↔ `MediatR`, `using
    Ardalis.Specification.Builder` ↔ `Ardalis.Specification`) и пакет
    внутри импортированного пространства (`using MassTransit` ↔
    `MassTransit.RabbitMQ`: модуль ссылается на транспорт, интерфейс живёт
    в основе). Второе слабее: `using System` с ним совпадает с каждым
    `System.*`, поэтому оно идёт в ход, только когда точных нет.
    """
    imported = {namespace for span in symbol.sources for namespace in usings.get(span.path, [])}
    exact: set[str] = set()
    inner: set[str] = set()
    for package in packages_of.get(symbol.module, []):
        if any(ns == package or ns.startswith(package + ".") for ns in imported):
            exact.add(package)
        elif any(package.startswith(ns + ".") for ns in imported):
            inner.add(package)
    return exact, inner


# Голова базы: FQN или имя и признак «объявлена в репозитории». Признак —
# часть ключа: FQN объявленной базы и имя внешней совпасть не должны,
# но склеить их молча было бы хуже, чем показать две строки.
type _Head = tuple[str, bool]


def dispatch_candidates(
    scan: ScanResult,
    settings: DocpipeConfig,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> DispatchCandidates:
    """Кандидаты в `dispatch_interfaces` по обобщённым базам классов этого прогона.

    Реализация — класс, не абстрактный: абстрактный обработчик запроса
    не обслуживает, его обслуживает наследник, а у наследника своя голова
    (`CommandHandler<CreateOrder>`), и именно её найдёт прогон — ключ
    сверяется с прямыми базами. Голова группируется по FQN, если база
    объявлена в репозитории, иначе по имени.

    Тип-запрос — первый аргумент базы, объявленный в репозитории
    и не совпадающий с самим классом и его параметрами-дженериками:
    `Money : IEquatable<Money>` и CRTP-база `Entity<Order>` у `Order` —
    не диспетчеризация, а у value object отправок (`new Money(…)`) больше,
    чем у любого запроса, и без этого отсева они шли бы первыми.

    Порядок — `(-exclusivity, -sent, -implementations, interface)`.
    """
    fqns = {symbol.fqn for symbol in scan.index.values()}
    declared = {symbol.name for symbol in scan.index.values()}

    heads: defaultdict[_Head, dict[str, set[str]]] = defaultdict(dict)
    for key, symbol in scan.index.items():
        if symbol.type_kind != "class" or "abstract" in symbol.modifiers:
            continue
        own = {symbol.name, *symbol.type_parameters}
        # Списки параллельны (`dotnet/resolve.py`): по позиции известны и
        # текст базы с аргументами, и её FQN, если резолв удался.
        for raw, base in zip(symbol.base_types_raw, symbol.base_types, strict=True):
            parsed = _generic_base(raw)
            if parsed is None:
                continue
            written, arguments = parsed
            resolved = base in fqns
            head = (base if resolved else dispatch_name(written), resolved)
            requests = heads[head].setdefault(key, set())
            request = bare_type(arguments[0])
            if request in declared and request not in own:
                requests.add(request)

    # Чьим аргументом встречается тип-запрос — по всем головам, включая
    # не прошедшие порог: `Specification<Order>` с одной реализацией тоже
    # говорит, что `Order` — не запрос `IEntityTypeConfiguration`.
    owners: defaultdict[str, set[_Head]] = defaultdict(set)
    for head, implementations in heads.items():
        for requests in implementations.values():
            for request in requests:
                owners[request].add(head)

    created: defaultdict[str, list[tuple[str, Construction]]] = defaultdict(list)
    for path, construction in scan.constructions:
        created[construction.type_name].append((path, construction))

    packages_of = {
        module.project_file: module.package_references for module in scan.manifest.modules
    }
    configured = dispatch_names(settings.dispatch_interface_names)

    found: list[DispatchCandidate] = []
    for (interface, resolved), implementations in heads.items():
        requests = {request for handled in implementations.values() for request in handled}
        if len(implementations) < MIN_IMPLEMENTATIONS or len(requests) < MIN_REQUEST_TYPES:
            continue
        keys = sorted(implementations)
        symbols = [scan.index[key] for key in keys]
        spans = [span for symbol in symbols for span in symbol.sources]
        members: Counter[str] = Counter()
        exact: Counter[str] = Counter()
        inner: Counter[str] = Counter()
        for key, symbol in zip(keys, symbols, strict=True):
            members.update(_handler_members(symbol, implementations[key]))
            # Пакет объявленной базы не ищется: она не из пакета.
            if not resolved:
                strong, weak = _imported_packages(symbol, packages_of, scan.usings)
                exact.update(strong)
                inner.update(weak)
        packages = exact or inner
        located = sorted(
            (symbol.sources[0].path, symbol.sources[0].start)
            for symbol in symbols
            if symbol.sources
        )
        exclusive = sum(1 for request in requests if len(owners[request]) == 1)
        found.append(
            DispatchCandidate(
                interface=interface,
                resolved=resolved,
                implementations=len(implementations),
                request_types=len(requests),
                exclusivity=round(exclusive / len(requests), 4),
                # Отправка внутри реализации — сам обработчик создаёт свой
                # запрос (повтор, проброс дальше), а не вызывающий код.
                sent=sum(
                    1
                    for request in requests
                    for path, construction in created[request]
                    if not _inside(spans, path, construction.line)
                ),
                handler_members=[name for name, _ in _top(members, len(members))],
                packages=[name for name, _ in _top(packages, _TOP_PACKAGES)],
                requests=sorted(requests)[:_EXAMPLES],
                configured=dispatch_name(interface) in configured,
                examples=[f"{path}:{line}" for path, line in located[:_EXAMPLES]],
            )
        )

    found.sort(key=lambda c: (-c.exclusivity, -c.sent, -c.implementations, c.interface, c.resolved))
    return DispatchCandidates(total=len(found), offset=offset, items=_page(found, limit, offset))


def _packages_text(item: DispatchCandidate) -> str:
    if item.resolved:
        return "— (база объявлена в репозитории)"
    if item.packages:
        return ", ".join(item.packages)
    # Пустой список — не «библиотеки нет»: пакет мог прийти из props.
    return (
        "не видны: ни один PackageReference не совпал с using реализаций "
        "(Directory.*.props разбор не читает)"
    )


def format_dispatch_interfaces(report: DispatchCandidates) -> str:
    lines = [
        f"Кандидаты в dispatch_interfaces: {report.total} "
        f"(обобщённые базы классов: реализаций не меньше {MIN_IMPLEMENTATIONS}, "
        f"типов-запросов из репозитория не меньше {MIN_REQUEST_TYPES})."
    ]
    for item in report.items:
        mark = "  [уже в dispatch_interfaces]" if item.configured else ""
        where = "объявлен в репозитории" if item.resolved else "внешний тип"
        more = " …" if item.request_types > len(item.requests) else ""
        lines += [
            "",
            f"{item.interface}{mark}",
            f"  реализаций {item.implementations}, типов-запросов {item.request_types}, "
            f"исключительность {item.exclusivity:.2f}, отправок {item.sent}",
            f"  в ключ: {dispatch_name(item.interface)} ({where})",
            f"  запросы: {', '.join(item.requests)}{more}",
            f"  методы с запросом в сигнатуре: {', '.join(item.handler_members) or 'нет'}",
            f"  пакеты: {_packages_text(item)}",
            f"  примеры: {', '.join(item.examples)}",
        ]

    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Вид кандидатов → прогон → отчёт
# --------------------------------------------------------------------------------------

# Объединение отчётов всех видов: следующие задачи (S13–S14) дописывают сюда
# свои модели, а в `_KINDS` — свои функции.
type CandidateReport = DiMethodCandidates | DispatchCandidates


@dataclass(frozen=True)
class CandidateInputs:
    """Что нужно любому виду: корень, настройка и откуда она прочитана.

    `config` — путь к `docpipe.yaml`: от его каталога разрешаются входы
    (`resolve_input`), и без него сервер настройки нашёл бы другой набор
    правил, чем CLI, позванный из того же каталога.
    """

    root: Path
    settings: DocpipeConfig
    config: Path | None = None
    use_cache: bool = True


def _scan(inputs: CandidateInputs) -> ScanResult:
    """Прогон шага 1 тем же путём, что у `scan`: те же правила, тот же кэш."""
    try:
        ruleset = load_ruleset(resolve_input(inputs.settings.rules, inputs.config), "dotnet")
    except (OSError, ValueError) as exc:
        raise InputError(f"набор правил не читается: {exc}") from exc
    cache_dir = inputs.root / inputs.settings.cache_dir if inputs.use_cache else None
    try:
        return run_scan(inputs.root, inputs.settings, ruleset, cache_dir)
    except ScopeConflict as exc:
        # Противоречие `enrolled`/`not_enrolled` — ошибка настройки, как и у `scan`.
        raise InputError(str(exc)) from exc


def _di_methods(inputs: CandidateInputs, limit: int, offset: int) -> CandidateReport:
    return di_method_candidates(_scan(inputs), inputs.settings, limit=limit, offset=offset)


def _dispatch_interfaces(inputs: CandidateInputs, limit: int, offset: int) -> CandidateReport:
    return dispatch_candidates(_scan(inputs), inputs.settings, limit=limit, offset=offset)


_KINDS: Final[dict[str, Callable[[CandidateInputs, int, int], CandidateReport]]] = {
    "di-methods": _di_methods,
    "dispatch-interfaces": _dispatch_interfaces,
}

KINDS: Final[tuple[str, ...]] = tuple(sorted(_KINDS))


def candidates(
    kind: str, inputs: CandidateInputs, *, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> CandidateReport:
    """Кандидаты одного вида. Неизвестный вид и отрицательная страница — `InputError`."""
    build = _KINDS.get(kind)
    if build is None:
        raise InputError(f"неизвестный вид кандидатов {kind!r}; известны: {', '.join(KINDS)}")
    if limit < 0 or offset < 0:
        raise InputError("limit и offset не бывают отрицательными")
    return build(inputs, limit, offset)


def format_candidates(report: CandidateReport) -> str:
    """Текст для человека. Вид отчёта определяется моделью, а не флагом."""
    if isinstance(report, DispatchCandidates):
        return format_dispatch_interfaces(report)
    return format_di_methods(report)


def candidates_json(report: CandidateReport) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))
