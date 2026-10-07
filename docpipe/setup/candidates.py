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

import json
import posixpath
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.config import DocpipeConfig
from docpipe.dotnet.di import is_standard_method
from docpipe.dotnet.facts import bare_type
from docpipe.emit import ScanResult, dispatch_name, dispatch_names, split_type_arguments
from docpipe.hashing import stable_json_dumps
from docpipe.model import (
    Construction,
    DocNode,
    Manifest,
    Member,
    RegistrationCall,
    SourceSpan,
    Symbol,
)
from docpipe.setup.context import InputError, SetupContext
from docpipe.web.absorb import FEATURE_KIND, PAGE_KIND, reachable_from
from docpipe.web.calls import (
    ArgFact,
    BuilderUse,
    CallScan,
    CandidateCall,
    RawCall,
    RegistryCall,
    ResolvedCall,
    address_positions,
    discriminator_of,
    query_parameters,
    registry_rules,
)
from docpipe.web.overrides import Overrides
from docpipe.web.pages import index_by_fqn
from docpipe.web.tree import registry_calls

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

# Раздел без маршрута: каталог, где лежат общие узлы — до каждого дотягиваются
# хотя бы две страницы — хотя бы двух видов. Порога по страницам сверх двух
# нет по той же причине, что у поглощения (`web/absorb.py`): «общий» — это
# «появился второй потребитель». Два вида — признак «своё состояние и свои
# сервисы»: один общий сервис в `shared/services/` — общий сервис, а не раздел.
MIN_PAGES: Final = 2
MIN_KINDS: Final = 2

# На сколько каталогов вверх подниматься от файла общего узла. Раздел обычно
# раскладывают на `state/`, `api/`, `services/` под одним каталогом — это
# шаг-два; выше трёх кандидатом становился бы каталог всего приложения.
MAX_LEVELS: Final = 3

_TOP_RECEIVERS: Final = 3
_TOP_PACKAGES: Final = 3
_EXAMPLES: Final = 3

# Маршрутов в кандидате показывается не больше: каталог, до которого
# дотягиваются все страницы приложения, иначе съел бы ответ инструмента
# (правило 5 плана). Сколько их всего — `page_count`.
_PAGES_SHOWN: Final = 10


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


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
# Кандидаты в `web.registry_calls`
# --------------------------------------------------------------------------------------

# Маршрут, который зовут хотя бы дважды: одним вызовом «много смыслов»
# не покажешь.
MIN_REGISTRY_CALLS: Final = 2

# Сколько разных значений поля назвать: у реестра платформы имён списков
# бывают десятки, а вопрос задаётся по первым.
_VALUES: Final = 10

REGISTRY_LIMITS: Final = (
    "тело читается только из объектного литерала второго аргумента; "
    "`HttpParams` и `{ params: … }` не разбираются",
    "вызов с невосстановленным адресом в счёт не идёт: маршрута у него нет "
    "(`calls_unresolved` у `web scan --stats`)",
)


class RegistryCallCandidate(_Base):
    """Поле тела или параметр query, которое у одного маршрута меняется от вызова к вызову.

    `api/items/query` с `listInnerName: 'users'` и `'models'` — один эндпоинт
    платформы на много смыслов, и ключ «метод + маршрут» склеивает их в одну
    точку (CLAUDE.md, «Один маршрут на много смыслов»). Признак — **разные
    значения одного поля**, а не повтор маршрута: шесть вызовов журнала
    аудита с переменной в теле — повтор, и смысла в нём не меняется ничего.

    `route` — в той форме, в какой его пишут в `registry_calls.route`: после
    `url_rewrite` модуля. Правило сверяется именно с ней, и маршрут, как он
    написан в коде, при преобразовании префикса не сработает никогда.

    `values` — литеральные значения (до десяти, по имени), `values_total` —
    сколько их всего. `nonliteral` — вызовы, где значение есть, но задано
    выражением (`${type}` в query, `{ listInnerName: name }` в теле): такое
    значение считается ещё одним, отличным от литералов, — обёртка
    `byType(type)` и есть главная форма обращения к реестру, а различителя
    прогон из неё не достанет.

    `calls` — вызовов группы (глагол + маршрут). `route_calls` и
    `unresolved_when_configured` — по **всем** вызовам маршрута, любого
    глагола и модуля: правило `registry_calls` глагола и модуля не знает,
    и записанное, оно оставит без различителя ровно столько вызовов
    (`registry_unresolved` у `web scan --stats`).
    """

    route: str
    http_method: str
    where: Literal["body", "query"]
    name: str
    values: list[str]
    values_total: int
    nonliteral: int
    calls: int
    route_calls: int
    modules: list[str]
    configured: bool
    unresolved_when_configured: int
    examples: list[str]


class RegistryCallCandidates(_Base):
    """Отчёт `setup candidates registry-calls`.

    `limits` — чего разбор не видит. Без них пустой список читался бы как
    «обращений к реестру нет», а не «их нет в тех формах, которые читаются».

    `calls_resolved` и `calls_unresolved` — база, из которой строятся
    кандидаты. На открытых репозиториях до обёрток и построителей адреса
    (S18–S19) восстановлено 2 вызова из 81 (squidex) и 0 из 50
    (ever-traduora): ноль кандидатов там значит «маршрутов не видно»,
    а не «реестра нет», и без базы эти два ответа неразличимы.
    """

    schema_version: Literal["1.0"] = "1.0"
    calls_resolved: int
    calls_unresolved: int
    total: int
    offset: int
    limits: list[str]
    items: list[RegistryCallCandidate]


type _Where = Literal["body", "query"]


@dataclass
class _Field:
    """Что известно об одном поле внутри группы вызовов."""

    values: set[str]
    nonliteral: int
    # (не литерал, файл, строка): примеры — сначала вызовы с литералом,
    # по ним видно, какие имена списков зовут.
    seen: list[tuple[bool, str, int]]


def _fields_of(raw: RawCall) -> list[tuple[_Where, str, str | None]]:
    """Поля вызова: `(где, имя, литерал)`; `None` — значение задано выражением."""
    literal, substituted = query_parameters(raw.url or "")
    found: list[tuple[_Where, str, str | None]] = [
        ("body", name, value) for name, value in raw.body_fields.items()
    ]
    found += [("body", name, None) for name in raw.body_nonliteral]
    found += [("query", name, value) for name, value in literal.items()]
    found += [("query", name, None) for name in substituted]
    return found


def registry_call_candidates(
    calls: CallScan,
    settings: DocpipeConfig,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> RegistryCallCandidates:
    """Кандидаты в `web.registry_calls` по восстановленным вызовам прогона `web`.

    Группа — глагол и маршрут построенного ключа **без** различителя:
    с настроенным правилом ключи разошлись бы по значениям, и кандидат
    пропал бы ровно тогда, когда его надо пометить «уже в настройке».
    Модули в группу не входят: правило пишется на маршрут, и вопрос
    о платформенном эндпоинте, который зовут семь фронтов, — один вопрос.

    Кандидат — поле группы от двух вызовов, у которого значений хотя бы два;
    значение-выражение считается одним значением, отличным от литералов.
    Порядок — `(-values_total, -calls, route, http_method, where, name)`.
    """
    rules = registry_rules(registry_calls(settings))

    by_route: defaultdict[str, list[ResolvedCall]] = defaultdict(list)
    groups: defaultdict[tuple[str, str], list[ResolvedCall]] = defaultdict(list)
    for item in calls.resolved:
        by_route[item.call.key.route].append(item)
        groups[(item.call.key.http_method, item.call.key.route)].append(item)

    found: list[RegistryCallCandidate] = []
    for (http_method, route), items in groups.items():
        if len(items) < MIN_REGISTRY_CALLS:
            continue
        fields: dict[tuple[_Where, str], _Field] = {}
        for item in items:
            for where, name, value in _fields_of(item.raw):
                known = fields.setdefault((where, name), _Field(set(), 0, []))
                if value is None:
                    known.nonliteral += 1
                else:
                    known.values.add(value)
                known.seen.append((value is None, item.raw.file, item.raw.line))

        for (where, name), known in fields.items():
            if len(known.values) + (1 if known.nonliteral else 0) < 2:
                continue
            # Счёт «без различителя» — той же функцией, что у прогона:
            # число обязано совпасть с `registry_unresolved` после записи правила.
            rule = RegistryCall(route=route, discriminator_in=where, name=name)
            effective = rules.get(route)
            found.append(
                RegistryCallCandidate(
                    route=route,
                    http_method=http_method,
                    where=where,
                    name=name,
                    values=sorted(known.values)[:_VALUES],
                    values_total=len(known.values),
                    nonliteral=known.nonliteral,
                    calls=len(items),
                    route_calls=len(by_route[route]),
                    modules=sorted({item.module for item in items}),
                    configured=effective is not None
                    and (effective.discriminator_in, effective.name) == (where, name),
                    unresolved_when_configured=sum(
                        1 for other in by_route[route] if not discriminator_of(other.raw, rule)
                    ),
                    examples=[f"{path}:{line}" for _, path, line in sorted(known.seen)[:_EXAMPLES]],
                )
            )

    found.sort(key=lambda c: (-c.values_total, -c.calls, c.route, c.http_method, c.where, c.name))
    return RegistryCallCandidates(
        calls_resolved=len(calls.resolved),
        calls_unresolved=len(calls.unresolved),
        total=len(found),
        offset=offset,
        limits=list(REGISTRY_LIMITS),
        items=_page(found, limit, offset),
    )


def registry_rule_text(item: RegistryCallCandidate) -> str:
    """Элемент списка `web.registry_calls` одной строкой YAML (потоковая запись).

    Маршрут и имя — в кавычках JSON: `{}` в маршруте (`api/lists/{}/items`)
    и `[` в имени (`filter[name]`) внутри потоковой записи YAML иначе
    читаются как вложенная структура.
    """
    route = json.dumps(item.route, ensure_ascii=False)
    name = json.dumps(item.name, ensure_ascii=False)
    return f"{{route: {route}, discriminator: {{in: {item.where}, name: {name}}}}}"


def format_registry_calls(report: RegistryCallCandidates) -> str:
    lines = [
        f"Кандидаты в web.registry_calls: {report.total} "
        f"(маршрут с одним глаголом, вызовов не меньше {MIN_REGISTRY_CALLS}, "
        "поле тела или параметр query с разными значениями).",
        f"Вызовов восстановлено {report.calls_resolved}, "
        f"не восстановлено {report.calls_unresolved}: невосстановленные в счёт не идут.",
    ]
    for item in report.items:
        mark = "  [уже в registry_calls]" if item.configured else ""
        more = " …" if item.values_total > len(item.values) else ""
        across = (
            f"; у маршрута всего вызовов {item.route_calls} (другие глаголы или модули)"
            if item.route_calls != item.calls
            else ""
        )
        lines += [
            "",
            f"{item.http_method} {item.route} — {item.where}.{item.name}{mark}",
            f"  значения ({item.values_total}): {', '.join(item.values) or '—'}{more}; "
            f"выражением: {item.nonliteral}",
            f"  вызовов {item.calls}{across}; "
            f"с правилом без различителя останется {item.unresolved_when_configured}",
            f"  модули: {', '.join(item.modules) or '—'}",
            f"  в web.registry_calls: {registry_rule_text(item)}",
            f"  примеры: {', '.join(item.examples)}",
        ]

    lines += ["", "Ограничения разбора:"]
    lines += [f"  - {text}" for text in report.limits]
    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Кандидаты в разделы без маршрута (`features` в `pages.yaml`)
# --------------------------------------------------------------------------------------


class FeatureCandidate(_Base):
    """Каталог, похожий на раздел без маршрута: своё состояние и сервисы, общие для страниц.

    Без раздела общий узел остаётся отдельным документом: страницей он
    не поглощается, потому что до него дотягиваются несколько (`web/absorb.py`).
    Объявить раздел может только человек (`docs/pages.md`, P16), а вопрос
    «этот каталог — раздел?» строится отсюда.

    `nodes` и `kinds` — общие узлы под каталогом (до каждого дотягиваются
    две страницы и больше) и их виды. Именно они делают каталог кандидатом.

    `pages` — по маршруту на страницу (наименьший, если их несколько),
    до десяти; страницы — те, что дотягиваются до любого узла под каталогом:
    ровно этот список после объявления напечатает раздел «Откуда
    открывается» в документе раздела (`materialize/build.py`). Сколько
    страниц всего — `page_count`. Невосстановленный маршрут — с `?` впереди,
    как в `web pages --format csv`.

    `declared` — каталог уже накрыт `features[].path` из `pages.yaml`: это
    сам объявленный каталог или лежит под ним.

    `examples` — до трёх `файл:строка` общих узлов.
    """

    path: str
    pages: list[str]
    page_count: int
    nodes: int
    kinds: dict[str, int]
    declared: bool
    examples: list[str]


class FeatureCandidates(_Base):
    """Отчёт `setup candidates features`.

    `pages_total` и `shared_nodes` — база, от которой считаются кандидаты.
    Достижимость считается от страниц, и без них молчит: ноль кандидатов
    в репозитории, где таблица роутов не собралась, неотличим от
    репозитория, где разделов нет.
    """

    schema_version: Literal["1.0"] = "1.0"
    pages_total: int
    shared_nodes: int
    total: int
    offset: int
    items: list[FeatureCandidate]


def _source(node: DocNode) -> SourceSpan | None:
    """Где узел объявлен: первый из `sources`, как у границы раздела (`web/absorb.py`)."""
    if node.symbol is None or not node.symbol.sources:
        return None
    return node.symbol.sources[0]


def _within(path: str, directory: str) -> bool:
    """`path` — сам каталог или лежит под ним, по границе сегмента.

    Подстрокой нельзя: `…/orders` накрыл бы `…/orders-report`, — та же
    ловушка, что у `Feature.prefix`. Пустой каталог — корень репозитория.
    """
    return not directory or path == directory or path.startswith(directory + "/")


def _ancestors(directory: str, root: str) -> Iterator[str]:
    """Каталог файла и его предки: не выше `MAX_LEVELS` шагов и не выше корня модуля.

    Корень модуля — ключ модуля фронта (`Symbol.module`): каталог-граница,
    к которому привязан файл. Выше него лежит чужой модуль или корень
    репозитория, и раздел оттуда объединил бы два приложения. Пустой путь
    кандидатом не бывает: `features[].path` пустым не объявить.
    """
    current = directory
    for _ in range(MAX_LEVELS + 1):
        if not current:
            return
        yield current
        if current == root or not _within(current, root):
            return
        current = posixpath.dirname(current)


def _route_of(page: DocNode) -> str:
    """Маршрут страницы для человека: наименьший из её маршрутов, `/orders/{}`."""
    routes = sorted(
        ("?" if entry.route_unresolved else "") + "/" + entry.path for entry in page.routes
    )
    return routes[0] if routes else page.title


def feature_candidates(
    manifest: Manifest,
    overrides: Overrides,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> FeatureCandidates:
    """Кандидаты в `features` по достижимости страниц манифеста фронта.

    Достижимость — `absorb.reachable_from`, тот же расчёт, которым
    поглощение решает «одна страница или несколько» и документ раздела
    считает «Откуда открывается». В `web/pages.py` есть ещё две
    (по зависимостям и по парам с глубиной) — с ними кандидат разошёлся бы
    с тем, что потом поглотит объявленный раздел.

    Считается по `uses`, а не по `absorbed_by`: пустое `absorbed_by` значит
    и «две страницы», и «ни одной», а объявленный раздел его меняет —
    кандидат перестал бы видеть сам себя сразу после объявления.

    Для каждого общего узла — каталог его файла и предки (`_ancestors`):
    первый, где общие узлы оказались двух видов и больше, — кандидат.
    Признак — разнообразие **видов**, а не имена каталогов: `state/` и `api/`
    не стандарт (в `WebWorkspace` это `services/`, `state/`,
    `cf-api/resources/`), а виды зависят от правил `web`.

    Порядок — по пути: вложенные кандидаты (`src/app` и `src/app/orders`)
    стоят рядом, и видно, что один накрывает другой. Признака, который ставил
    бы один каталог выше другого, у этого вида нет — решают страницы и виды.
    """
    by_fqn = index_by_fqn(manifest)
    pages = sorted(
        (node for node in manifest.nodes if node.kind == PAGE_KIND), key=lambda node: node.id
    )

    # Узел → страницы, которые до него дотягиваются, и где он объявлен.
    reached_by: defaultdict[str, set[str]] = defaultdict(set)
    sources: dict[str, SourceSpan] = {}
    kinds: dict[str, str] = {}
    roots: dict[str, str] = {}
    for page in pages:
        for fqn in reachable_from(page, by_fqn):
            node = by_fqn.get(fqn)
            # Страница внутрь раздела не уходит (у неё свой якорь), у узла
            # раздела нет символа; узел без файла каталогу не принадлежит.
            if node is None or node.kind in (PAGE_KIND, FEATURE_KIND):
                continue
            source = _source(node)
            if source is None or node.symbol is None:
                continue
            reached_by[node.id].add(page.id)
            sources[node.id] = source
            kinds[node.id] = node.kind
            roots[node.id] = node.symbol.module

    shared = sorted(node_id for node_id, owners in reached_by.items() if len(owners) >= MIN_PAGES)

    def shared_under(directory: str) -> list[str]:
        return [node_id for node_id in shared if _within(sources[node_id].path, directory)]

    paths: set[str] = set()
    for node_id in shared:
        for directory in _ancestors(posixpath.dirname(sources[node_id].path), roots[node_id]):
            if len({kinds[other] for other in shared_under(directory)}) >= MIN_KINDS:
                paths.add(directory)
                break

    routes = {page.id: _route_of(page) for page in pages}
    found: list[FeatureCandidate] = []
    for directory in sorted(paths):
        inside = shared_under(directory)
        opened = {
            page_id
            for node_id, owners in reached_by.items()
            if _within(sources[node_id].path, directory)
            for page_id in owners
        }
        located = sorted((sources[node_id].path, sources[node_id].start) for node_id in inside)
        found.append(
            FeatureCandidate(
                path=directory,
                pages=sorted(routes[page_id] for page_id in opened)[:_PAGES_SHOWN],
                page_count=len(opened),
                nodes=len(inside),
                kinds=dict(sorted(Counter(kinds[node_id] for node_id in inside).items())),
                declared=any(
                    (directory + "/").startswith(feature.prefix) for feature in overrides.features
                ),
                examples=[f"{path}:{line}" for path, line in located[:_EXAMPLES]],
            )
        )

    return FeatureCandidates(
        pages_total=len(pages),
        shared_nodes=len(shared),
        total=len(found),
        offset=offset,
        items=_page(found, limit, offset),
    )


def format_features(report: FeatureCandidates) -> str:
    lines = [
        f"Кандидаты в разделы (features в pages.yaml): {report.total} "
        f"(каталоги, где общие узлы — до каждого дотягиваются не меньше {MIN_PAGES} страниц — "
        f"не меньше {MIN_KINDS} видов; подъём от файла не выше {MAX_LEVELS} каталогов "
        "и корня модуля)."
    ]
    if report.pages_total == 0:
        lines.append(
            "Страниц нет: достижимость считается от страниц, и ноль кандидатов ничего "
            "не говорит. Сначала — `docpipe web pages`: таблица роутов не собралась "
            "или фронта в корне нет."
        )
    elif report.pages_total < MIN_PAGES:
        lines.append("Страница одна: общих узлов не бывает, и кандидатов не будет.")
    else:
        lines.append(f"Страниц {report.pages_total}; общих узлов {report.shared_nodes}.")

    for item in report.items:
        mark = "  [уже объявлен в pages.yaml]" if item.declared else ""
        kinds = ", ".join(f"{kind} {count}" for kind, count in sorted(item.kinds.items()))
        more = " …" if item.page_count > len(item.pages) else ""
        lines += [
            "",
            f"{item.path}{mark}",
            f"  общих узлов {item.nodes}: {kinds}",
            f"  открывается со страниц ({item.page_count}): {', '.join(item.pages)}{more}",
            f"  примеры: {', '.join(item.examples)}",
        ]
        if not item.declared:
            lines.append(f'  в pages.yaml: features[].path: "{item.path}" (name, reason — ваши)')

    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Кандидаты в обёртки HTTP (`http-wrappers`) и построители адреса (`url-builders`)
# --------------------------------------------------------------------------------------

WRAPPER_LIMITS: Final = (
    "кандидат — вызов члена, не являющийся вызовом `HttpClient`, у которого аргумент "
    "похож на адрес: литерал, шаблон или `const`, начинающиеся с `/`, `api/`, `http://`, "
    "`https://`; объект с полем `url`; значение построителя адреса (см. `url-builders`)",
    "вызов обёртки с адресом-параметром функции или выражением (`link.href` напрямую, "
    "без построителя) кандидатом не становится: на адрес он не похож, и в `calls` "
    "группы такие вызовы не входят",
    "группа, результат которой идёт адресом в другой вызов, — построитель адреса: "
    "она в `url-builders`, а здесь названа в `builders`",
)


def _qualified(receiver: str, method: str) -> str:
    """`HTTP.getVersioned`; у функции без получателя — просто имя."""
    return f"{receiver}.{method}" if receiver else method


type _Group = tuple[str, str]


def _builder_groups(builder_uses: list[BuilderUse]) -> set[_Group]:
    """Построители адреса прогона: вызовы, хоть раз построившие адрес.

    Признак — употребление, а не имя: результат идёт первым аргументом
    `HttpClient` или аргументом-адресом вызова-кандидата (`BuilderUse`).
    """
    return {(use.receiver, use.method) for use in builder_uses}


def _wrapper_positions(call: CandidateCall, builders: set[_Group]) -> list[str]:
    """Позиции адреса в вызове-кандидате: похожие на адрес и построенные построителем.

    Второе — гипермедиа через построитель: `url` при `const url =
    this.apiUrl.buildUrl(link.href)` на адрес не похож, но построен тем же
    вызовом, что и адреса `HttpClient`, — и это адрес.
    """
    found = address_positions(call.args)
    found += [
        str(index)
        for index, arg in enumerate(call.args)
        if arg.callee in builders and str(index) not in found
    ]
    return found


class HttpWrapperCandidate(_Base):
    """Получатель и метод, через которые идут вызовы с аргументом-адресом.

    `HTTP.getVersioned(this.http, url)` и `this.rest.request({ url })` не видны
    ни одному счётчику прогона: глагола `HttpClient` у них нет. Это и есть
    «вызовы, которых не видно вовсе» — здесь они посчитаны. Но обёртку
    по имени не распознать (`get<T>(path, default)` у squidex — чтение
    состояния), поэтому решение — за человеком, а в разбор обёртка входит
    объявлением (`web.http_wrappers`, S19).

    `calls` — вызовов группы с адресом хотя бы в одной позиции. `positions` —
    где адрес: `1` — второй позиционный аргумент, `0.url` — поле `url`
    первого; `[позиция, вызовов]` от частых к редким. Номер обязателен:
    у `getVersioned` первым идёт сам `HttpClient`.

    `configured` — обёртка уже объявлена; ключа `web.http_wrappers` до S19
    нет, и отметка до неё всегда `false`.
    """

    receiver: str
    method: str
    calls: int
    positions: list[tuple[str, int]]
    files: int
    configured: bool
    examples: list[str]


class HttpWrapperCandidates(_Base):
    """Отчёт `setup candidates http-wrappers`.

    `http_calls` — вызовов `HttpClient`, которые прогон видит (восстановленных
    и нет), `wrapper_calls` — вызовов-кандидатов во всех группах списка:
    без пары «видно / не видно» число кандидатов не с чем сравнить.
    `builders` — группы, отнесённые к построителям адреса (их результат —
    адрес другого вызова), чтобы их отсутствие в списке не читалось как
    «не найдено». `limits` — чего отбор не видит.
    """

    schema_version: Literal["1.0"] = "1.0"
    http_calls: int
    wrapper_calls: int
    builders: list[str]
    total: int
    offset: int
    limits: list[str]
    items: list[HttpWrapperCandidate]


def _positions(rows: list[list[str]]) -> list[tuple[str, int]]:
    """Позиции адреса по вызовам: `[позиция, вызовов]`, от частых к редким."""
    counter: Counter[str] = Counter(position for row in rows for position in set(row))
    return _top(counter, len(counter))


def _wrapper_groups(
    candidate_calls: list[CandidateCall], builders: set[_Group]
) -> dict[_Group, list[tuple[CandidateCall, list[str]]]]:
    """Вызовы-кандидаты с позициями адреса, по группам; построители и вызовы без адреса — нет."""
    groups: defaultdict[_Group, list[tuple[CandidateCall, list[str]]]] = defaultdict(list)
    for call in candidate_calls:
        if (call.receiver, call.method) in builders:
            continue
        positions = _wrapper_positions(call, builders)
        if positions:
            groups[(call.receiver, call.method)].append((call, positions))
    return dict(groups)


def http_wrapper_candidates(
    candidate_calls: list[CandidateCall],
    builder_uses: list[BuilderUse],
    calls: CallScan,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> HttpWrapperCandidates:
    """Кандидаты в `web.http_wrappers` по вызовам-кандидатам прогона `web`.

    Группа — `(receiver, method)` как написаны. Группа, которая хоть раз
    построила адрес другого вызова (`builder_uses`), — построитель, а не
    обёртка: `this.apiUrl.buildUrl('/api/apps')` сам похож на вызов-обёртку
    с аргументом-адресом, и без отсева встал бы первым там, где вызовов через
    него больше всего. Порядок — `(-calls, receiver, method)`.
    """
    builders = _builder_groups(builder_uses)
    groups = _wrapper_groups(candidate_calls, builders)

    found: list[HttpWrapperCandidate] = []
    for (receiver, method), items in groups.items():
        located = sorted((call.file, call.line) for call, _ in items)
        found.append(
            HttpWrapperCandidate(
                receiver=receiver,
                method=method,
                calls=len(items),
                positions=_positions([positions for _, positions in items]),
                files=len({call.file for call, _ in items}),
                configured=False,
                examples=[f"{path}:{line}" for path, line in located[:_EXAMPLES]],
            )
        )

    found.sort(key=lambda c: (-c.calls, c.receiver, c.method))
    seen = {(call.receiver, call.method) for call in candidate_calls}
    return HttpWrapperCandidates(
        http_calls=len(calls.calls) + len(calls.unresolved),
        wrapper_calls=sum(item.calls for item in found),
        builders=sorted(_qualified(*group) for group in builders & seen),
        total=len(found),
        offset=offset,
        limits=list(WRAPPER_LIMITS),
        items=_page(found, limit, offset),
    )


def _pairs_text(pairs: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name} ×{count}" for name, count in pairs)


def format_http_wrappers(report: HttpWrapperCandidates) -> str:
    lines = [
        f"Кандидаты в web.http_wrappers: {report.total} "
        "(вызовы члена с аргументом-адресом, не являющиеся вызовом HttpClient).",
        f"Вызовов HttpClient прогон видит {report.http_calls}; вызовов с аргументом-адресом "
        f"вне HttpClient — {report.wrapper_calls}: прогон их не видит, пока обёртка "
        "не объявлена, а среди групп бывают и не обёртки.",
        "Обёртку по имени не распознать: кандидат — находка, решение — человеку.",
    ]
    if report.builders:
        lines.append(
            "Построители адреса (см. url-builders), здесь не показаны: "
            + ", ".join(report.builders)
            + "."
        )
    for item in report.items:
        mark = "  [уже в web.http_wrappers]" if item.configured else ""
        lines += [
            "",
            f"{_qualified(item.receiver, item.method)}{mark}",
            f"  вызовов {item.calls}, файлов {item.files}",
            f"  адрес в аргументе: {_pairs_text(item.positions)}",
            f"  примеры: {', '.join(item.examples)}",
        ]

    lines += ["", "Ограничения отбора:"]
    lines += [f"  - {text}" for text in report.limits]
    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


class UrlBuilderCandidate(_Base):
    """Вызов, которым построен адрес: `const url = this.apiUrl.buildUrl(…)`, затем `get(url)`.

    Значения у такого адреса нет — невосстановленный вызов с причиной
    «значение переменной — вызов `apiUrl.buildUrl(…)`», — а путь лежит
    в аргументе построителя. Объявленный построитель (S19) берёт адрес оттуда.

    `uses` — адресов, построенных этим вызовом; `http_calls` — из них у прямых
    вызовов `HttpClient`, `through` — у вызовов-кандидатов в обёртки
    (`[обёртка, адресов]`, три частых): основная форма squidex — построитель
    внутри обёртки. `positions` — аргументы построителя, похожие на адрес
    (`[позиция, адресов]`): у гипермедиа (`buildUrl(link.href)`) такого
    аргумента нет, и `uses` больше суммы позиций.

    `configured` — построитель уже объявлен; ключа `web.url_builders` до S19
    нет, и отметка до неё всегда `false`.
    """

    receiver: str
    method: str
    uses: int
    http_calls: int
    through: list[tuple[str, int]]
    positions: list[tuple[str, int]]
    files: int
    configured: bool
    examples: list[str]


class UrlBuilderCandidates(_Base):
    """Отчёт `setup candidates url-builders`."""

    schema_version: Literal["1.0"] = "1.0"
    total: int
    offset: int
    items: list[UrlBuilderCandidate]


def url_builder_candidates(
    candidate_calls: list[CandidateCall],
    builder_uses: list[BuilderUse],
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> UrlBuilderCandidates:
    """Кандидаты в `web.url_builders`: группа `(receiver, method)` построителя.

    Построитель — группа из `builder_uses` (`_builder_groups`). Употребления —
    адреса прямых вызовов `HttpClient` и аргументы вызовов-кандидатов
    в обёртки, построенные им, включая гипермедиа: тот же отбор, что
    у `http-wrappers`, — иначе две сводки одного прогона разошлись бы
    в числе одних и тех же вызовов. Порядок — `(-uses, receiver, method)`.
    """
    builders = _builder_groups(builder_uses)
    # (файл, строка, обёртка или "", аргумент-адрес)
    uses: defaultdict[_Group, list[tuple[str, int, str, ArgFact]]] = defaultdict(list)
    for use in builder_uses:
        if not use.through:
            uses[(use.receiver, use.method)].append((use.file, use.line, "", use.arg))
    for group, calls in _wrapper_groups(candidate_calls, builders).items():
        for call, _ in calls:
            for arg in call.args:
                if arg.callee is not None and arg.callee in builders:
                    uses[arg.callee].append((call.file, call.line, _qualified(*group), arg))

    found: list[UrlBuilderCandidate] = []
    for (receiver, method), built in uses.items():
        located = sorted((path, line) for path, line, _, _ in built)
        found.append(
            UrlBuilderCandidate(
                receiver=receiver,
                method=method,
                uses=len(built),
                http_calls=sum(1 for _, _, through, _ in built if not through),
                through=_top(Counter(through for _, _, through, _ in built if through), _EXAMPLES),
                positions=_positions([address_positions(arg.args) for _, _, _, arg in built]),
                files=len({path for path, _, _, _ in built}),
                configured=False,
                examples=[f"{path}:{line}" for path, line in located[:_EXAMPLES]],
            )
        )

    found.sort(key=lambda c: (-c.uses, c.receiver, c.method))
    return UrlBuilderCandidates(total=len(found), offset=offset, items=_page(found, limit, offset))


def format_url_builders(report: UrlBuilderCandidates) -> str:
    lines = [
        f"Кандидаты в web.url_builders: {report.total} "
        "(вызовы, результат которых — адрес вызова HttpClient или вызова-кандидата в обёртки)."
    ]
    for item in report.items:
        mark = "  [уже в web.url_builders]" if item.configured else ""
        lines += [
            "",
            f"{_qualified(item.receiver, item.method)}{mark}",
            f"  адресов построено {item.uses}: у вызовов HttpClient {item.http_calls}, "
            f"через обёртки {item.uses - item.http_calls}"
            + (f" ({_pairs_text(item.through)})" if item.through else ""),
            f"  путь в аргументе: {_pairs_text(item.positions) or 'не похож на адрес ни разу'}",
            f"  файлов {item.files}; примеры: {', '.join(item.examples)}",
        ]

    lines.append("")
    lines.append(_page_line(report.total, report.offset, len(report.items)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Вид кандидатов → прогон → отчёт
# --------------------------------------------------------------------------------------

# Объединение отчётов всех видов: новый вид дописывает сюда свою модель,
# а в `_KINDS` — свою функцию.
type CandidateReport = (
    DiMethodCandidates
    | DispatchCandidates
    | RegistryCallCandidates
    | FeatureCandidates
    | HttpWrapperCandidates
    | UrlBuilderCandidates
)


# Прежнее имя входа кандидатов. Прогоны собирает `SetupContext` (S23):
# у кандидатов была своя сборка шага 1, и вторая команда `setup` завела бы
# третью — ровно так однажды разошлись три копии `_prepare`.
CandidateInputs = SetupContext


def _di_methods(inputs: SetupContext, limit: int, offset: int) -> CandidateReport:
    return di_method_candidates(inputs.scan, inputs.settings, limit=limit, offset=offset)


def _dispatch_interfaces(inputs: SetupContext, limit: int, offset: int) -> CandidateReport:
    return dispatch_candidates(inputs.scan, inputs.settings, limit=limit, offset=offset)


# Все виды фронта берут прогон `web` у контекста — тот же, что у `web scan`:
# правила фронта, `pages.yaml` (`web.pages`), кэш; прогон один на все виды.
# Виду `features` нужен и ручной состав: без объявленных разделов отметка
# `declared` не значила бы ничего. Видам вызовов (`registry-calls`,
# `http-wrappers`, `url-builders`) он не нужен — на вызовы не влияет, — но
# и не мешает: без `web.pages` правила пустые, а битый названный файл — та же
# ошибка настройки, на которой упал бы `web scan`.


def _registry_calls(inputs: SetupContext, limit: int, offset: int) -> CandidateReport:
    return registry_call_candidates(inputs.web.calls, inputs.settings, limit=limit, offset=offset)


def _features(inputs: SetupContext, limit: int, offset: int) -> CandidateReport:
    return feature_candidates(inputs.web.manifest, inputs.overrides, limit=limit, offset=offset)


def _http_wrappers(inputs: SetupContext, limit: int, offset: int) -> CandidateReport:
    web = inputs.web
    return http_wrapper_candidates(
        web.candidate_calls, web.builder_uses, web.calls, limit=limit, offset=offset
    )


def _url_builders(inputs: SetupContext, limit: int, offset: int) -> CandidateReport:
    web = inputs.web
    return url_builder_candidates(web.candidate_calls, web.builder_uses, limit=limit, offset=offset)


_KINDS: Final[dict[str, Callable[[SetupContext, int, int], CandidateReport]]] = {
    "di-methods": _di_methods,
    "dispatch-interfaces": _dispatch_interfaces,
    "features": _features,
    "http-wrappers": _http_wrappers,
    "registry-calls": _registry_calls,
    "url-builders": _url_builders,
}

KINDS: Final[tuple[str, ...]] = tuple(sorted(_KINDS))


def candidates(
    kind: str, inputs: SetupContext, *, limit: int = DEFAULT_LIMIT, offset: int = 0
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
    if isinstance(report, RegistryCallCandidates):
        return format_registry_calls(report)
    if isinstance(report, FeatureCandidates):
        return format_features(report)
    if isinstance(report, HttpWrapperCandidates):
        return format_http_wrappers(report)
    if isinstance(report, UrlBuilderCandidates):
        return format_url_builders(report)
    return format_di_methods(report)


def candidates_json(report: CandidateReport) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))
