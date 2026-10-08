"""Необъяснённое в области: `setup status` (S24).

Одна команда отвечает, что в области ещё без решения и что сломано, — это
и есть проверяемое состояние Р-6 плана настройки: в области нет находки без
решения и нет дефектов. Остановку решает человек; команда показывает, на чём
он останавливается.

Отчёт из трёх частей:

- **находки** (`findings`) — места без решения и дефекты, по коду из
  `FINDING_CODES`: число, кластеры с примерами и где лежит решение. Код —
  стабильный, его читают каталог вопросов интервью (S28) и карта настройки
  (`docs/setup-map.md`, колонка «Находка»);
- **охват** (`coverage`) — сколько чего досталось каждому решению настройки:
  шаблону `exclude`, записи `enrolled`, правилу отсева и правилу-победителю,
  записям шва, `pages.yaml`, правилам владения. В отчёт идёт сумма, разбивку
  «файл → сколько решено» отдаёт `decision_coverage`, а её вместе со всеми
  местами каждой находки — `status_detail`: они нужны ревью (S25);
- **вне области** (`out_of_scope`) — что решено не брать: модули под
  `not_enrolled`, фронты под `exclude`, их символы.

Принятого состояния здесь нет (П-1): его роль играет коммит настройки.
Сравнение — только между своими прогонами агента: `--out` пишет отчёт,
`--baseline` берёт из такого файла `previous`.

Три правила держат ответ честным:

- **только прогоны в памяти** (`SetupContext`): агент правит настройку
  и сразу зовёт команду, а манифест на диске собран прошлой настройкой —
  отчёт по нему показал бы, что правка ничего не изменила;
- **отказ одного прогона — дефект, а не падение**: шаг 2 без скелетов,
  неоднозначный `pages.yaml`, нечитаемый набор правил дают `docs.unavailable`
  и `load.errors`, а остальные находки агенту нужны и тогда;
- **шов считается, когда у него обе стороны**: без фронта каждый эндпоинт —
  «без вызывающего», и решения у такой находки нет.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from docpipe.classify import REQUIRE_PUBLIC, Ruleset
from docpipe.config import scope_entry, scope_of
from docpipe.configcheck import check_config
from docpipe.discovery import WEB_FIELDS, file_field, in_scope, is_excluded, normalize_scope
from docpipe.emit import ScanResult, dispatch_name, exclude_globs
from docpipe.explain import ANY, Row, select
from docpipe.hashing import stable_json_dumps
from docpipe.materialize.ownership import Ownership, owner_of
from docpipe.materialize.plan import shadowed_docs
from docpipe.model import DocNode, Manifest, Symbol
from docpipe.route import normalize_route
from docpipe.setup.candidates import (
    DEFAULT_LIMIT,
    declined_calls,
    http_wrapper_candidates,
    http_wrapper_places,
)
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.explain import covering_root, shown_root
from docpipe.setup.link import clusters_of, places_of
from docpipe.stats import NOT_ENROLLED, UNDECIDED, enrolled_keys, last_word, plural
from docpipe.step2 import Step2Error, Step2Inputs
from docpipe.web.absorb import PAGE_KIND
from docpipe.web.calls import builder_for, name_matches, wrapper_matches
from docpipe.web.link import LinkReport, backend_keys
from docpipe.web.overrides import MANUAL_SOURCE
from docpipe.web.pages import NOTE_EMPTY_ROUTE, NOTE_NO_FEATURES, NOTE_UNANCHORABLE
from docpipe.web.pages import build_report as build_pages_report
from docpipe.web.tree import WebScanResult

SCHEMA_VERSION: Final = "1.0"

# Примеров в кластере. Больше — и сводка снова становится поштучным списком.
EXAMPLES: Final = 3

# Кластеров в тексте на находку: дальше — `--format json` или команда-источник.
TEXT_CLUSTERS: Final = 3

Category = Literal["decision", "defect"]


@dataclass(frozen=True)
class FindingCode:
    """Код находки: категория, заголовок и где лежит решение.

    `decision` — место без решения человека: закрывается записью настройки
    (`decision_home`) с причиной. `defect` — сломано: решением «не беру»
    не закрывается, только починкой (`decision_home` — «—»).
    """

    code: str
    category: Category
    title: str
    decision_home: str


# Порядок — порядок находок в отчёте: сначала решения по области и символам,
# потом шов, страницы, документы и владение, в конце дефекты. Код — стабильный
# ключ: его читают каталог вопросов (S28) и тест карты настройки (S09).
FINDING_CODES: Final[tuple[FindingCode, ...]] = (
    FindingCode(
        "scope.module_undecided",
        "decision",
        "модуль без решения об области",
        "docpipe.yaml: `enrolled` или `not_enrolled` с причиной",
    ),
    FindingCode(
        "scope.front_undecided",
        "decision",
        "фронт разведки вне `web.roots` и не под `exclude`",
        "docpipe.yaml: `web.roots` или `exclude` с причиной",
    ),
    FindingCode(
        "dotnet.undecided",
        "decision",
        "символ .NET без решения",
        "rules.yaml, секция `dotnet`: правило классификации или отсев с причиной",
    ),
    FindingCode(
        "web.undecided",
        "decision",
        "символ фронта без решения",
        "rules.yaml, секция `web`: правило классификации или отсев с причиной",
    ),
    FindingCode(
        "link.calls_unresolved",
        "decision",
        "вызов фронта, адрес не восстановлен",
        "docpipe.yaml: `web.http_wrappers`, `web.url_builders` или `link.unresolvable`",
    ),
    FindingCode(
        "link.calls_invisible",
        "decision",
        "вызов через необъявленную обёртку: прогон его не видит",
        "docpipe.yaml: `web.http_wrappers` или `web.not_wrappers` с причиной",
    ),
    FindingCode(
        "link.calls_without_endpoint",
        "decision",
        "вызов фронта без эндпоинта",
        "docpipe.yaml: `web.url_rewrite` или `link.external_targets`",
    ),
    FindingCode(
        "link.endpoints_without_caller",
        "decision",
        "эндпоинт без вызывающего",
        "docpipe.yaml: `link.external_callers`",
    ),
    FindingCode(
        "link.almost",
        "decision",
        "связь «почти»: ключи различаются только числом {}",
        "docpipe.yaml: `web.url_rewrite`",
    ),
    FindingCode(
        "link.module_without_rewrite",
        "decision",
        "модуль фронта с вызовами без записи `web.url_rewrite`",
        "docpipe.yaml: `web.url_rewrite` (пустая запись — «проверено, преобразования нет»)",
    ),
    FindingCode(
        "link.registry_unresolved",
        "decision",
        "обращение к реестру без различителя",
        "docpipe.yaml: `web.registry_calls`",
    ),
    FindingCode(
        "pages.route_unresolved",
        "decision",
        "страница: маршрут части записей не собран",
        "pages.yaml: `add` с маршрутом",
    ),
    FindingCode(
        "pages.unanchorable",
        "decision",
        "страница без собранного маршрута: якорь не поставить",
        "pages.yaml: `add` с маршрутом или `remove`",
    ),
    FindingCode(
        "pages.layout",
        "decision",
        "страница похожа на layout: признаков функционала нет",
        "pages.yaml: `remove`",
    ),
    FindingCode(
        "pages.stale_overrides",
        "decision",
        "правило pages.yaml ни на что не легло",
        "pages.yaml: правка или удаление правила",
    ),
    FindingCode(
        "docs.orphan",
        "decision",
        "документ без узла",
        "`docpipe docs adopt` или удаление файла",
    ),
    FindingCode(
        "owners.unowned",
        "decision",
        "документ без владельца",
        "ownership.yaml: правило",
    ),
    FindingCode(
        "owners.not_configured",
        "decision",
        "владение не настроено: владельцев не назначает никто",
        "docpipe.yaml: `ownership` и файл правил владения",
    ),
    FindingCode(
        "parse.errors",
        "decision",
        "файл разобран с ошибками и не дал ни одного объявления",
        "docpipe.yaml: `exclude` с причиной или правка исходника",
    ),
    FindingCode("config.problems", "defect", "проблема настройки (`config check`)", "—"),
    FindingCode("docs.broken", "defect", "документ не читается: структура испорчена", "—"),
    FindingCode(
        "docs.shadowed", "defect", "файл документа есть, а обход документов его не видит", "—"
    ),
    FindingCode("link.duplicate_endpoints", "defect", "один ключ у двух узлов бэкенда", "—"),
    FindingCode("load.errors", "defect", "прогон не собрался или собрался с ошибками", "—"),
    FindingCode("docs.unavailable", "defect", "план шага 2 не собрался", "—"),
)

CODES: Final[dict[str, FindingCode]] = {item.code: item for item in FINDING_CODES}


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Cluster(_Base):
    """Мест находки с одним значением ключа `slice`: `module`, `reason`, `directory`, ….

    `examples` — до трёх мест: FQN символа, `файл:строка  что там`, путь документа.
    """

    slice: str
    key: str
    count: int
    examples: list[str]


class Finding(_Base):
    """Находка: код, сколько мест, сколько было в базе сравнения и где решение.

    `previous` — число той же находки в `--baseline`; `None` — сравнения нет.
    `clusters` — до `--limit` кластеров каждого среза, `clusters_total` — всего
    кластеров до усечения: без него усечённый список читался бы как полный.
    """

    code: str
    category: Category
    title: str
    count: int
    previous: int | None
    decision_home: str
    clusters: list[Cluster]
    clusters_total: int


class Coverage(_Base):
    """Охват решения настройки: сколько единиц кода оно решило во всём репозитории.

    `id` — файл, ключ и значение (шаблон, `id` правила, модуль, маршрут) —
    стабилен, пока запись не правят: по нему `--baseline` находит `previous`.
    Единица `count` — по ключу (файлы, модули, символы, вызовы, узлы), как
    у `setup explain`. Ноль — решение, которое не решило ничего.
    """

    id: str
    file: str
    key: str
    value: str
    count: int
    previous: int | None
    reason: str


class OutOfScope(_Base):
    """Что решено не брать: модули под `not_enrolled`, фронты под `exclude`, их символы."""

    modules: int
    fronts: int
    symbols: int
    examples: list[str]


class SetupStatus(_Base):
    """Ответ `setup status`.

    `findings` — находки с `count > 0` в порядке `FINDING_CODES`, а при
    `--baseline` ещё и исчезнувшие с прошлого прогона (`count: 0`, `previous`
    больше нуля): без них правка, закрывшая находку, не была бы видна разницей.
    `unexplained` и `defects` — сумма мест в находках решений и дефектов.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    findings: list[Finding]
    coverage: list[Coverage]
    out_of_scope: OutOfScope
    without_reason: list[str]
    unexplained: int
    defects: int


class CoverageDetail(_Base):
    """Охват решения с разбивкой «файл → сколько решено» — в памяти, для ревью (S25).

    В отчёт идёт только `count`. Разбивка: символы — по каждому файлу
    `sources`, вызовы — по файлу вызова, эндпоинты — по файлу действия,
    модули — по `.csproj`, узлы — по файлам символа. Поэтому у символа
    из двух файлов сумма разбивки больше `count`: решён один символ, а
    касается решение двух файлов.
    """

    id: str
    file: str
    key: str
    value: str
    reason: str
    count: int
    files: dict[str, int]


def decision_id(file: str, key: str, value: str) -> str:
    """Стабильный `id` решения: файл, ключ и значение через `::`."""
    return f"{file}::{key}::{value}"


# --------------------------------------------------------------------------------------
# Прогоны: каждый может не собраться, и это находка, а не отказ
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Step:
    """Решения по символам одного шага: тот же `decide`, что у `--stats` и `symbols`."""

    key: Literal["dotnet", "web"]
    manifest: Manifest
    index: dict[str, Symbol]
    ruleset: Ruleset
    rules_file: Path
    rows: list[Row]


@dataclass
class _Runs:
    """Прогоны контекста, собранные с изоляцией отказов.

    `cached_property` не запоминает исключение: второе обращение к упавшему
    прогону повторило бы его целиком. Поэтому каждый прогон берётся здесь
    один раз, а отказ — строкой в `load_errors` или `plan_errors`.
    """

    scan: ScanResult | None = None
    web: WebScanResult | None = None
    link: LinkReport | None = None
    steps: list[_Step] = field(default_factory=list)
    plans: dict[str, Step2Inputs] = field(default_factory=dict)
    plan_errors: dict[str, str] = field(default_factory=dict)
    ownership: Ownership | None = None
    ownership_error: str = ""
    load_errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def seam(self) -> bool:
        """У шва обе стороны: модули .NET и модули фронта.

        Без фронта каждый эндпоинт — «без вызывающего», без бэка каждый вызов —
        «без эндпоинта», и решения у таких находок нет: на `SampleSolution`
        их было бы столько, сколько действий в контроллерах.
        """
        return (
            self.link is not None
            and self.scan is not None
            and self.web is not None
            and bool(self.scan.manifest.modules)
            and bool(self.web.manifest.modules)
        )


def _attempt[T](get: Callable[[], T], step: str, errors: list[tuple[str, str]]) -> T | None:
    try:
        return get()
    except InputError as exc:
        errors.append((step, str(exc)))
        return None


def _gather(ctx: SetupContext) -> _Runs:
    runs = _Runs()
    runs.scan = _attempt(lambda: ctx.scan, "dotnet", runs.load_errors)
    runs.web = _attempt(lambda: ctx.web, "web", runs.load_errors)
    if runs.scan is not None and runs.web is not None:
        runs.link = ctx.link

    if runs.scan is not None:
        scan = runs.scan
        enrolled = enrolled_keys(scan.manifest, "cs")
        runs.steps.append(
            _step("dotnet", scan.manifest, scan.index, ctx.ruleset, ctx.rules_file, enrolled)
        )
    if runs.web is not None:
        web = runs.web
        enrolled = enrolled_keys(web.manifest, "ts")
        runs.steps.append(
            _step("web", web.manifest, web.index, ctx.web_ruleset, ctx.web_rules_file, enrolled)
        )

    plans: tuple[tuple[str, object, Callable[[], Step2Inputs]], ...] = (
        ("dotnet", runs.scan, lambda: ctx.plan),
        ("web", runs.web, lambda: ctx.web_plan),
    )
    for step, run, plan in plans:
        if run is None:
            continue
        try:
            runs.plans[step] = plan()
        except Step2Error as exc:
            runs.plan_errors[step] = exc.message

    try:
        runs.ownership = ctx.ownership
    except Step2Error as exc:
        runs.ownership_error = exc.message
    return runs


def _step(
    key: Literal["dotnet", "web"],
    manifest: Manifest,
    index: dict[str, Symbol],
    ruleset: Ruleset,
    rules_file: Path,
    enrolled: set[str],
) -> _Step:
    rows = select(index, manifest.nodes, ruleset, enrolled, state=ANY).rows
    return _Step(key, manifest, index, ruleset, rules_file, rows)


# --------------------------------------------------------------------------------------
# Охват решений
# --------------------------------------------------------------------------------------


@dataclass
class _Entry:
    file: str
    key: str
    value: str
    reason: str
    count: int = 0
    files: Counter[str] = field(default_factory=Counter)


class _Ledger:
    """Решения настройки и их охват: одна запись настройки — одна строка.

    Решение объявляется до подсчёта (`declare`), чтобы запись, не решившая
    ничего, осталась в охвате с нулём: ради такого нуля охват и заведён.
    Повтор записи в одном файле — одна строка, причина — у первой: так их
    сводит и `setup explain` (`_Decisions`).
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], _Entry] = {}

    def declare(self, file: str, key: str, value: str, reason: str = "") -> None:
        self._entries.setdefault((file, key, value), _Entry(file, key, value, reason))

    def hit(self, file: str, key: str, value: str, paths: Iterable[str], count: int = 1) -> None:
        """Засчитать записи `count` единиц кода; разбивка — по файлам `paths`, по разу."""
        entry = self._entries.get((file, key, value))
        if entry is None:
            return
        entry.count += count
        entry.files.update(set(paths))

    def details(self) -> list[CoverageDetail]:
        return [
            CoverageDetail(
                id=decision_id(entry.file, entry.key, entry.value),
                file=entry.file,
                key=entry.key,
                value=entry.value,
                reason=entry.reason,
                count=entry.count,
                files=dict(sorted(entry.files.items())),
            )
            for _, entry in sorted(self._entries.items())
        ]


def _sources(symbol: Symbol | None) -> list[str]:
    return [source.path for source in symbol.sources] if symbol is not None else []


def _cover_files(ctx: SetupContext, ledger: _Ledger) -> None:
    """Шаблоны `exclude` (файлы, которые они отсекли) и записи корней (файлы, которые накрыли).

    Тот же счёт, что у `setup explain`: файл под двумя шаблонами — у обоих,
    корень — самая длинная накрывшая запись своего шага. Встроенный отсев
    и умолчание корней — не решения человека, их здесь нет.
    """
    settings, config = ctx.settings, ctx.config_label
    found = ctx.discovered
    for pattern in settings.exclude_entries:
        ledger.declare(config, "exclude", pattern.glob, pattern.reason)
    for path, globs in (found.excluded or {}).items():
        for glob in set(globs):
            ledger.hit(config, "exclude", glob, [path])

    explicit = {
        "roots": "roots" in settings.model_fields_set,
        "web.roots": "roots" in settings.web.model_fields_set,
    }
    entries = {"roots": settings.roots, "web.roots": settings.web.root_paths}
    reasons = {item.path: item.reason for item in settings.web.root_entries}
    for key in ("roots", "web.roots"):
        if not explicit[key]:
            continue
        for entry in entries[key]:
            ledger.declare(
                config, key, shown_root(entry), reasons.get(entry, "") if key == "web.roots" else ""
            )
    kept = [
        *found.cs_files,
        *found.csproj_files,
        *found.sln_files,
        *found.sql_files,
        *found.ts_files,
        *found.html_files,
        *found.web_project_files,
    ]
    for path in kept:
        key = "web.roots" if file_field(path.rsplit("/", 1)[-1]) in WEB_FIELDS else "roots"
        if not explicit[key]:
            continue
        covering = covering_root(path, entries[key])
        if covering is not None:
            ledger.hit(config, key, shown_root(covering), [path])


def _cover_scope(ctx: SetupContext, scan: ScanResult, ledger: _Ledger) -> None:
    """Записи `enrolled` (только явного) и `not_enrolled`: модули, область которых они решили.

    Решила запись, совпавшая первой в порядке файла (`scope_entry`), — как
    `by` у модуля в `setup explain`: запись, ни разу не ставшая первой,
    не решила ничего.
    """
    settings, config = ctx.settings, ctx.config_label
    explicit = settings.enrolled_is_explicit
    if explicit:
        for enrolled in settings.enrolled_entries:
            ledger.declare(config, "enrolled", enrolled.glob, enrolled.reason)
    for dropped in settings.not_enrolled_entries:
        ledger.declare(config, "not_enrolled", dropped.glob, dropped.reason)
    for module in scan.manifest.modules:
        scope = scope_of(module.project_file, settings)
        entry = scope_entry(module.project_file, settings)
        if entry is None or (scope == "enrolled" and not explicit):
            continue
        ledger.hit(config, scope, entry.glob, [module.project_file])


def _cover_rules(step: _Step, ledger: _Ledger) -> None:
    """Правила отсева и классификации: символы, которые они **выиграли** (S07).

    По победам, а не по совпадениям: правило, совпавшее со всем и нигде
    не выигравшее, не решило ничего. `require_public` — решение файла правил
    со своим `id`, как в `Stats.skipped`.
    """
    file = step.rules_file.as_posix()
    excluded, ruled = f"{step.key}.exclude", f"{step.key}.rules"
    for rule in step.ruleset.exclude.rules:
        ledger.declare(file, excluded, rule.id, rule.reason)
    if step.ruleset.exclude.require_public:
        ledger.declare(file, excluded, REQUIRE_PUBLIC.id, REQUIRE_PUBLIC.reason)
    for classification in step.ruleset.rules:
        ledger.declare(file, ruled, classification.id)
    for row in step.rows:
        decision = row.decision
        if decision.exclusion is not None:
            ledger.hit(file, excluded, decision.exclusion.id, _sources(row.symbol))
        elif decision.winner_rule is not None:
            ledger.hit(file, ruled, decision.winner_rule, _sources(row.symbol))


def _cover_dotnet(ctx: SetupContext, scan: ScanResult, ledger: _Ledger) -> None:
    """`di_methods` (вызовы, ставшие регистрацией) и `dispatch_interfaces` (обработчики)."""
    settings, config = ctx.settings, ctx.config_label
    for wrapper in settings.di_method_entries:
        ledger.declare(config, "di_methods", wrapper.name, wrapper.reason)
    names = set(settings.di_method_names)
    for path, call in scan.registration_calls:
        # Лямбда-форма без типа регистрацией не становится и с ключом (`dotnet/di.py`).
        if call.method in names and (call.type_args or call.typeof_args):
            ledger.hit(config, "di_methods", call.method, [path])

    interfaces = settings.dispatch_interface_entries
    for interface in interfaces:
        ledger.declare(config, "dispatch_interfaces", interface.name, interface.reason)
    for handler in scan.manifest.dispatch_handlers:
        for interface in interfaces:
            if dispatch_name(interface.name) == handler.interface:
                ledger.hit(config, "dispatch_interfaces", interface.name, [handler.file])


def _cover_calls(ctx: SetupContext, web: WebScanResult, ledger: _Ledger) -> None:
    """Записи вызовов фронта: `web.*` (`url_rewrite`, обёртки, «не обёртки»…), `unresolvable`.

    Сравнение — тем же, что у прогона: правило модуля — `rewrite_for` по
    модулю, по правилам которого построен ключ; обёртка — `wrapper_matches`,
    тело обёртки — `name_matches`, построитель — `builder_for`. «Какая
    запись сделала этот вызов» и «к чему запись применилась» обязаны
    отвечать одинаково.
    """
    settings, config = ctx.settings, ctx.config_label
    for rewrite in settings.web.url_rewrite:
        ledger.declare(config, "web.url_rewrite", rewrite.module, rewrite.reason)
    for item in web.calls.resolved:
        found = settings.web.rewrite_for(item.module)
        if found is not None:
            ledger.hit(config, "web.url_rewrite", found.module, [item.call.file])

    registry = [(normalize_route(rule.route), rule) for rule in settings.web.registry_calls]
    for _, rule in registry:
        ledger.declare(config, "web.registry_calls", rule.route, rule.reason)
    for call in web.calls.calls:
        for route, rule in registry:
            if route == call.key.route:
                ledger.hit(config, "web.registry_calls", rule.route, [call.file])

    wrappers, builders = settings.web.http_wrappers, settings.web.url_builders
    for wrapper in wrappers:
        ledger.declare(config, "web.http_wrappers", wrapper.label, wrapper.reason)
    for builder in builders:
        ledger.declare(config, "web.url_builders", builder.label, builder.reason)
    raws = [*(item.raw for item in web.calls.resolved), *web.calls.unresolved]
    for raw in raws:
        receiver, _, method = raw.wrapper.rpartition(".")
        for wrapper in wrappers:
            if raw.wrapper and wrapper_matches(wrapper, receiver, method):
                ledger.hit(config, "web.http_wrappers", wrapper.label, [raw.file])
        callee = raw.address.callee if raw.address is not None else None
        built = builder_for(callee, builders) if raw.builder and callee is not None else None
        if built is not None:
            ledger.hit(config, "web.url_builders", built.label, [raw.file])
    for raw in web.calls.inside_wrappers:
        parameter = raw.address.parameter if raw.address is not None else None
        for wrapper in wrappers:
            if parameter is not None and name_matches(wrapper, parameter.function):
                ledger.hit(config, "web.http_wrappers", wrapper.label, [raw.file])

    # «Не обёртка» решает о вызове-кандидате: охват — вызовы, которые без неё
    # стояли бы в `link.calls_invisible`, тем же `declined_calls`, что у отчёта.
    declined = settings.web.not_wrappers
    for refusal in declined:
        ledger.declare(config, "web.not_wrappers", refusal.label, refusal.reason)
    for index, candidate in declined_calls(web.candidate_calls, web.builder_uses, declined):
        ledger.hit(config, "web.not_wrappers", declined[index].label, [candidate.file])

    # `link.unresolvable` решает сам по себе: сведение со стороной .NET ему не нужно.
    for unresolvable in settings.link.unresolvable:
        ledger.declare(config, "link.unresolvable", unresolvable.label, unresolvable.reason)
    for unresolved in web.manifest.unresolved_calls:
        decided = settings.link.unresolvable_for(unresolved.file)
        if decided is not None:
            ledger.hit(config, "link.unresolvable", decided[1].label, [unresolved.file])


def _cover_link(ctx: SetupContext, report: LinkReport, ledger: _Ledger) -> None:
    """`link.external_targets` и `link.external_callers` — только по сведению двух сторон.

    Запись решает о конце **без пары**, и совпадение у связанного вызова
    не решило ничего: отличить их может только отчёт связи.
    """
    settings, config = ctx.settings, ctx.config_label
    targets = settings.link.external_targets
    callers = settings.link.external_callers
    for target in targets:
        ledger.declare(config, "link.external_targets", target.label, target.reason)
    for caller in callers:
        ledger.declare(config, "link.external_callers", caller.label, caller.reason)
    for call in report.external_targets:
        label = targets[call.decision.index].label
        ledger.hit(config, "link.external_targets", label, [call.file])
    for endpoint in report.external_callers:
        label = callers[endpoint.decision.index].label
        ledger.hit(config, "link.external_callers", label, [endpoint.file])


def _cover_pages(ctx: SetupContext, web: WebScanResult, ledger: _Ledger) -> None:
    """Записи `pages.yaml`: страницы и узлы, которых они касаются, — как у `setup explain`."""
    pages_file = ctx.pages_file
    if pages_file is None:
        return
    file, overrides = pages_file.as_posix(), ctx.overrides
    by_fqn = {symbol.fqn: symbol for symbol in web.index.values()}

    for rule in overrides.add:
        ledger.declare(file, "add", rule.component, rule.reason)
        if rule.component in by_fqn:
            ledger.hit(file, "add", rule.component, _sources(by_fqn[rule.component]))

    # Снятие по маршруту ложится на компоненты этого маршрута — из таблицы
    # роутов и из добавлений, как у `apply_overrides`.
    entries = [
        *((entry.path, entry.component, entry.route_unresolved) for entry in web.routes.entries),
        *((rule.normalized, rule.component, False) for rule in overrides.add),
    ]
    for removal in overrides.remove:
        value = removal.component or f"/{removal.normalized}"
        ledger.declare(file, "remove", value, removal.reason)
        if removal.component:
            hits = {removal.component} & by_fqn.keys()
        else:
            hits = {
                component
                for path, component, unresolved in entries
                if path == removal.normalized and not unresolved and component in by_fqn
            }
        if hits:
            paths = [path for fqn in sorted(hits) for path in _sources(by_fqn[fqn])]
            ledger.hit(file, "remove", value, paths, count=len(hits))

    for feature in overrides.features:
        ledger.declare(file, "features", feature.name, feature.reason)
        inner = [
            node
            for node in web.manifest.nodes
            if node.symbol
            and node.symbol.sources
            and node.symbol.sources[0].path.startswith(feature.prefix)
            and node.kind != PAGE_KIND
        ]
        if inner:
            paths = [path for node in inner for path in _sources(node.symbol)]
            ledger.hit(file, "features", feature.name, paths, count=len(inner))


def _documented(runs: _Runs) -> list[DocNode]:
    """Узлы со своим документом — оба манифеста; поглощённые страницей своего не имеют."""
    nodes = [node for step in runs.steps for node in step.manifest.nodes if not node.absorbed_by]
    return sorted(nodes, key=lambda node: node.id)


def _cover_owners(ctx: SetupContext, runs: _Runs, ledger: _Ledger) -> None:
    """Правила владения: узлы с документом, которые они выиграли."""
    ownership, ownership_file = runs.ownership, ctx.ownership_file
    if ownership is None or ownership_file is None:
        return
    file = ownership_file.as_posix()
    for rule in ownership.rules:
        ledger.declare(file, "rules", rule.id)
    for node in _documented(runs):
        winner = owner_of(node, ownership).winner
        if winner is not None:
            ledger.hit(file, "rules", winner.id, _sources(node.symbol))


def _coverage(ctx: SetupContext, runs: _Runs) -> list[CoverageDetail]:
    """Охват решений по прогонам, которые собрались.

    Решения прогона, который не собрался, в охват не идут вовсе: ноль у них
    читался бы как «решение не решило ничего», а это неизвестно — о прогоне
    говорит `load.errors`.
    """
    ledger = _Ledger()
    _cover_files(ctx, ledger)
    for step in runs.steps:
        _cover_rules(step, ledger)
    if runs.scan is not None:
        _cover_scope(ctx, runs.scan, ledger)
        _cover_dotnet(ctx, runs.scan, ledger)
    if runs.web is not None:
        _cover_calls(ctx, runs.web, ledger)
        _cover_pages(ctx, runs.web, ledger)
    if runs.link is not None:
        _cover_link(ctx, runs.link, ledger)
    _cover_owners(ctx, runs, ledger)
    return ledger.details()


def decision_coverage(ctx: SetupContext) -> list[CoverageDetail]:
    """Охват каждого решения настройки с разбивкой по файлам — для ревью (S25).

    Тот же подсчёт, что у `coverage` отчёта `setup status`: ревью берёт
    отсюда, какие решения применились к новому коду, а `setup status` —
    сумму. Прогоны — из контекста; второй вызов на том же контексте
    их не повторяет.
    """
    return _coverage(ctx, _gather(ctx))


def _without_reason(ctx: SetupContext) -> list[str]:
    """`id` решений без причины — по настройке, а не по прогонам.

    Подсказка, а не находка: короткая форма законна (Р-2 плана), но через
    полгода «почему так» будет некому ответить. Считается по файлу настройки,
    чтобы отказ прогона не прятал записи. Ключи — те, у которых место для
    причины есть, а сама она необязательна: у правил классификации и владения
    поля нет, у `not_enrolled`, отсева в правилах, `pages.yaml` и секции
    `link` причина обязательна при загрузке.
    """
    settings, config = ctx.settings, ctx.config_label
    found: list[tuple[str, str]] = []
    if settings.enrolled_is_explicit:
        found += [("enrolled", item.glob) for item in settings.enrolled_entries if not item.reason]
    found += [("exclude", item.glob) for item in settings.exclude_entries if not item.reason]
    found += [("di_methods", item.name) for item in settings.di_method_entries if not item.reason]
    found += [
        ("dispatch_interfaces", item.name)
        for item in settings.dispatch_interface_entries
        if not item.reason
    ]
    if "roots" in settings.web.model_fields_set:
        found += [
            ("web.roots", shown_root(item.path))
            for item in settings.web.root_entries
            if not item.reason
        ]
    web = settings.web
    found += [("web.url_rewrite", item.module) for item in web.url_rewrite if not item.reason]
    found += [("web.registry_calls", item.route) for item in web.registry_calls if not item.reason]
    found += [("web.http_wrappers", item.label) for item in web.http_wrappers if not item.reason]
    found += [("web.url_builders", item.label) for item in web.url_builders if not item.reason]
    return sorted({decision_id(config, key, value) for key, value in found})


# --------------------------------------------------------------------------------------
# Находки
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingPlace:
    """Место находки для ревью (S25): пример — как в кластере — и файлы, которых оно касается.

    В отчёт `setup status` места не идут: там кластеры с тремя примерами.
    Ревью нужны все места, чтобы узнать, лежит ли место в новом файле.
    Мест нет у находок, которые к файлу не привязаны (`config.problems`,
    `load.errors`, `docs.unavailable`, `pages.stale_overrides`): ревью
    к новому коду их не относит.
    """

    example: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class _Found:
    """Находка до сравнения с базой: код, число, кластеры, сколько их всего и все места."""

    code: str
    count: int
    clusters: list[Cluster]
    total: int
    places: tuple[FindingPlace, ...] = ()


def _grouped(
    slice_: str, items: Sequence[tuple[str, str]], limit: int
) -> tuple[list[Cluster], int]:
    """Кластеры среза: места с одним ключом, порядок — `(-count, key)`.

    Примеры — первые три места в порядке `items`: его задаёт вызывающий
    (файл и строка, FQN), а не порядок обхода.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for key, example in items:
        groups[key].append(example)
    ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    clusters = [
        Cluster(slice=slice_, key=key, count=len(examples), examples=examples[:EXAMPLES])
        for key, examples in ordered
    ]
    return (clusters[:limit] if limit else clusters), len(clusters)


def _found(
    code: str,
    slices: Sequence[tuple[str, Sequence[tuple[str, str]]]],
    limit: int,
    count: int | None = None,
    places: Iterable[FindingPlace] = (),
) -> _Found:
    """Находка по срезам. `count` по умолчанию — мест первого среза.

    `places` — все места с файлами, для ревью; в кластеры они не идут.
    """
    clusters: list[Cluster] = []
    total = 0
    for slice_, items in slices:
        shown, all_of = _grouped(slice_, items, limit)
        clusters += shown
        total += all_of
    number = len(slices[0][1]) if count is None else count
    return _Found(code, number, clusters, total, tuple(places))


def _in_file(paths: Iterable[str]) -> list[FindingPlace]:
    """Места, у которых пример — сам файл: модуль, документ, файл с ошибкой разбора."""
    return [FindingPlace(path, (path,)) for path in paths]


def _located(file: str, line: int, text: str) -> FindingPlace:
    """Место вызова: `файл:строка  что там` — та же форма, что у примеров кластеров шва."""
    return FindingPlace(f"{file}:{line}  {text}", (file,))


def _node_files(manifest: Manifest) -> dict[str, tuple[str, ...]]:
    """Файлы символа каждого узла манифеста: место находки об узле — его исходники."""
    return {node.id: tuple(_sources(node.symbol)) for node in manifest.nodes}


def _parent(path: str) -> str:
    """Каталог файла; у файла в корне — `.`."""
    parent = PurePosixPath(path).parent.as_posix()
    return parent or "."


def _fronts(ctx: SetupContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Фронты разведки: `(без решения, под exclude)`.

    Фронт под шаблоном `exclude` — решён: обход его не читает. Вне `web.roots`
    и не под `exclude` — без решения. Сверяется файл объявления фронта
    (`angular.json`, `project.json`): шаг `web` находит модуль по нему, и фронт,
    чей файл вне корней, обход не видит, даже если его исходники внутри.
    При умолчании `web.roots: ["."]` вне корней не бывает ничего.
    """
    globs = exclude_globs(ctx.settings)
    roots = normalize_scope(ctx.settings.web.root_paths)
    undecided: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for front in ctx.projects["fronts"]:
        config = str(front["config"])
        if is_excluded(config, globs):
            excluded.append(front)
        elif not in_scope(config, roots):
            undecided.append(front)
    return undecided, excluded


def _scope_findings(ctx: SetupContext, runs: _Runs, limit: int) -> list[_Found]:
    """Модули без решения об области (только при явном `enrolled`) и фронты вне корней."""
    found: list[_Found] = []
    if runs.scan is not None:
        undecided = sorted(
            module.project_file
            for module in runs.scan.manifest.modules
            if scope_of(module.project_file, ctx.settings) == "undecided"
        )
        items = [(_parent(_parent(path)), path) for path in undecided]
        found.append(
            _found(
                "scope.module_undecided", [("directory", items)], limit, places=_in_file(undecided)
            )
        )
    fronts, _ = _fronts(ctx)
    items = [(str(front["path"]), str(front["config"])) for front in fronts]
    found.append(
        _found(
            "scope.front_undecided",
            [("front", items)],
            limit,
            places=_in_file(config for _, config in items),
        )
    )
    return found


def _symbol_findings(runs: _Runs, limit: int) -> list[_Found]:
    """Символы без решения: по модулю и по последнему слову имени (как срезы `--stats`)."""
    found: list[_Found] = []
    for step in runs.steps:
        rows = [row for row in step.rows if row.decision.state == UNDECIDED]
        by_module = [(row.symbol.module, row.symbol.fqn) for row in rows]
        by_word = [(word, row.symbol.fqn) for row in rows if (word := last_word(row.symbol.name))]
        found.append(
            _found(
                f"{step.key}.undecided",
                [("module", by_module), ("last_word", by_word)],
                limit,
                count=len(rows),
                places=(FindingPlace(row.symbol.fqn, tuple(_sources(row.symbol))) for row in rows),
            )
        )
    return found


def _link_found(
    code: str, runs: _Runs, ctx: SetupContext, category: str, limit: int
) -> _Found | None:
    """Находка шва кластерами `setup link` (S21): та же сводка, своей копии нет."""
    if runs.link is None or runs.scan is None:
        return None
    report = clusters_of(
        runs.link, ctx.settings, backend_keys(runs.scan.manifest), category=category, limit=limit
    )
    clusters = [
        Cluster(
            slice=report.by,
            key=cluster.key,
            count=cluster.count,
            examples=[f"{item.file}:{item.line}  {item.text}" for item in cluster.examples],
        )
        for cluster in report.clusters
    ]
    places = [_located(item.file, item.line, item.text) for item in places_of(runs.link, category)]
    return _Found(code, report.places, clusters, report.total, tuple(places))


def _seam_findings(ctx: SetupContext, runs: _Runs, limit: int) -> list[_Found | None]:
    """Шов фронт↔.NET: вызовы, которых не видно, и концы без пары.

    Факты одного фронта — невосстановленные, невидимые, обращения к реестру —
    считаются при любом бэке. Концы без пары, модуль без `url_rewrite`
    и дубли ключей — только когда у шва обе стороны (`_Runs.seam`).
    """
    found: list[_Found | None] = [
        _link_found("link.calls_unresolved", runs, ctx, "calls_unresolved", limit)
    ]
    web = runs.web
    if web is not None:
        candidates = http_wrapper_candidates(
            web.candidate_calls,
            web.builder_uses,
            web.calls,
            wrappers=ctx.settings.web.http_wrappers,
            not_wrappers=ctx.settings.web.not_wrappers,
            limit=0,
        )
        invisible = [item for item in candidates.items if not item.configured]
        # Места — того же отсева, что кандидаты: группа под `web.not_wrappers`
        # снята и из числа, и из мест, иначе ревью нашло бы её в новом файле.
        located = http_wrapper_places(
            web.candidate_calls, web.builder_uses, not_wrappers=ctx.settings.web.not_wrappers
        )
        clusters = [
            Cluster(
                slice="wrapper",
                key=f"{item.receiver}.{item.method}" if item.receiver else item.method,
                count=item.calls,
                examples=item.examples[:EXAMPLES],
            )
            for item in invisible
        ]
        found.append(
            _Found(
                "link.calls_invisible",
                sum(item.calls for item in invisible),
                clusters[:limit] if limit else clusters,
                len(clusters),
                tuple(
                    FindingPlace(f"{file}:{line}", (file,))
                    for item in invisible
                    for file, line in located.get((item.receiver, item.method), [])
                ),
            )
        )
        registry = sorted(web.calls.registry_unresolved, key=lambda call: (call.file, call.line))
        items = [
            (
                call.key.route,
                f"{call.file}:{call.line}  {call.key.http_method} {call.key.route}",
            )
            for call in registry
        ]
        found.append(
            _found(
                "link.registry_unresolved",
                [("route", items)],
                limit,
                places=(
                    _located(call.file, call.line, f"{call.key.http_method} {call.key.route}")
                    for call in registry
                ),
            )
        )

    if not runs.seam or runs.link is None or runs.web is None:
        return found
    for code, category in (
        ("link.calls_without_endpoint", "calls_without_endpoint"),
        ("link.endpoints_without_caller", "endpoints_without_caller"),
        ("link.almost", "almost"),
    ):
        found.append(_link_found(code, runs, ctx, category, limit))

    # Место вызова — `(file, line)`: вызов у двух узлов файла (S16, п. 6) — одно место.
    places: dict[str, set[tuple[str, int, str]]] = defaultdict(set)
    for node in runs.web.manifest.nodes:
        for call in node.web_calls:
            text = f"{call.key.http_method} {call.key.route}"
            places[node.module].add((call.file, call.line, text))
    for unresolved in runs.web.manifest.unresolved_calls:
        text = f"{unresolved.http_method} {unresolved.expression}".strip()
        places[unresolved.module].add((unresolved.file, unresolved.line, text))
    # Число находки — модулей, число кластера — мест вызова в модуле: правило
    # одно на модуль, а порядок кластеров — от модуля, где вызовов больше.
    modules: list[Cluster] = []
    for module in runs.link.unconfigured_modules:
        shown = sorted(places[module])[:EXAMPLES]
        modules.append(
            Cluster(
                slice="module",
                key=module,
                count=len(places[module]),
                examples=[f"{file}:{line}  {text}" for file, line, text in shown],
            )
        )
    modules.sort(key=lambda cluster: (-cluster.count, cluster.key))
    found.append(
        _Found(
            "link.module_without_rewrite",
            len(runs.link.unconfigured_modules),
            modules[:limit] if limit else modules,
            len(modules),
            tuple(
                _located(file, line, text)
                for module in sorted(runs.link.unconfigured_modules)
                for file, line, text in sorted(places[module])
            ),
        )
    )

    node_files = _node_files(runs.scan.manifest) if runs.scan is not None else {}
    duplicates: list[tuple[str, str]] = []
    doubled: list[FindingPlace] = []
    for item in runs.link.duplicate_endpoints:
        route, nodes = f"{item.http_method} {item.route}", ", ".join(item.nodes)
        duplicates.append((route, nodes))
        files = sorted({path for node in item.nodes for path in node_files.get(node, ())})
        doubled.append(FindingPlace(f"{route}  {nodes}", tuple(files)))
    found.append(_found("link.duplicate_endpoints", [("route", duplicates)], limit, places=doubled))
    return found


def _page_text(title: str, routes: Iterable[tuple[str, bool]]) -> str:
    """`ListComponent: /models, ?/models` — маршрут без `?` собран, с `?` — нет."""
    shown = ", ".join(("?" if unresolved else "") + "/" + path for path, unresolved in routes)
    return f"{title}: {shown}"


def _page_findings(runs: _Runs, limit: int) -> list[_Found]:
    """Страницы по заметкам `web pages` и протухшие правила `pages.yaml`.

    `pages.route_unresolved` — страница с якорем, у которой часть записей
    маршрута не собрана, а своей записи `pages.yaml` нет: объявленный руками
    маршрут и есть решение по такой записи. Страница, у которой не собрано
    ничего, — `pages.unanchorable`, не дважды.

    `pages.layout` — страница без признаков функционала (`NOTE_NO_FEATURES`)
    или на пустом маршруте **и без членов**. Одного пустого маршрута мало:
    на squidex на `/` стои́т `HomePageComponent` — экран входа с членами
    и шаблоном, настоящая страница. Закрыть такую находку можно только
    снятием (`remove`), а снять её было бы ошибкой: записи «проверено,
    это страница» в `pages.yaml` нет (`add` того же маршрута — протухшее
    правило `add-redundant`).
    """
    if runs.web is None:
        return []
    report = build_pages_report(runs.web.manifest)
    pages = sorted(report.pages, key=lambda page: (page.module, page.title, page.node_id))

    def text(page: Any) -> str:
        return _page_text(page.title, ((r.path, r.unresolved) for r in page.routes))

    def items(selected: Iterable[Any]) -> list[tuple[str, str]]:
        return [(page.module, text(page)) for page in selected]

    node_files = _node_files(runs.web.manifest)

    def located(selected: Iterable[Any]) -> list[FindingPlace]:
        return [FindingPlace(text(page), node_files.get(page.node_id, ())) for page in selected]

    unanchorable = [page for page in pages if NOTE_UNANCHORABLE in page.notes]
    partial = [
        page
        for page in pages
        if NOTE_UNANCHORABLE not in page.notes
        and any(route.unresolved for route in page.routes)
        and not any(route.source == MANUAL_SOURCE for route in page.routes)
    ]
    layout = [
        page
        for page in pages
        if NOTE_NO_FEATURES in page.notes or (NOTE_EMPTY_ROUTE in page.notes and not page.members)
    ]
    stale = sorted(runs.web.overrides.stale, key=lambda rule: (rule.kind, rule.key, rule.reason))
    return [
        _found(
            "pages.route_unresolved", [("module", items(partial))], limit, places=located(partial)
        ),
        _found(
            "pages.unanchorable",
            [("module", items(unanchorable))],
            limit,
            places=located(unanchorable),
        ),
        _found("pages.layout", [("module", items(layout))], limit, places=located(layout)),
        _found(
            "pages.stale_overrides",
            [("kind", [(rule.kind, rule.describe()) for rule in stale])],
            limit,
        ),
    ]


def _document_findings(ctx: SetupContext, runs: _Runs, limit: int) -> list[_Found]:
    """Сироты, испорченные и невидимые документы — по плану шага 2 каждого манифеста.

    Документ фронта — сирота для плана .NET и наоборот: дерево документов
    у них общее. Поэтому сирота — документ, который сиротой считают **оба**
    плана; если какой-то план не собрался, сирот не считаем вовсе (о плане
    говорит `docs.unavailable`). Невидимый файл (`shadowed_docs`) — свой
    дефект, а не ещё один испорченный.
    """
    plans = runs.plans
    shadowed = sorted(
        {
            path
            for inputs in plans.values()
            for path in shadowed_docs(ctx.root, inputs.manifest, inputs.existing)
        }
    )
    broken = sorted(
        {
            doc.doc_path
            for inputs in plans.values()
            for doc in inputs.plan.documents
            if doc.status == "broken"
        }
        - set(shadowed)
    )
    orphans: list[str] = []
    if {"dotnet", "web"} <= plans.keys():
        orphan_sets = [
            {doc.doc_path for doc in plans[step].plan.documents if doc.status == "orphan"}
            for step in ("dotnet", "web")
        ]
        orphans = sorted(orphan_sets[0] & orphan_sets[1])

    def items(paths: list[str]) -> list[tuple[str, str]]:
        return [(_parent(path), path) for path in paths]

    return [
        _found("docs.orphan", [("directory", items(orphans))], limit, places=_in_file(orphans)),
        _found("docs.broken", [("directory", items(broken))], limit, places=_in_file(broken)),
        _found("docs.shadowed", [("directory", items(shadowed))], limit, places=_in_file(shadowed)),
    ]


def _owner_findings(ctx: SetupContext, runs: _Runs, limit: int) -> list[_Found]:
    """Документы без владельца; без ключа `ownership` — одна находка «не настроено» на все."""
    documented = _documented(runs)

    def located(nodes: list[DocNode]) -> list[FindingPlace]:
        # Пример — путь документа, файлы — исходники узла: новым становится код, а не документ.
        return [FindingPlace(node.doc_path, tuple(_sources(node.symbol))) for node in nodes]

    if not ctx.settings.ownership:
        items = [(node.module, node.doc_path) for node in documented]
        return [
            _found("owners.not_configured", [("module", items)], limit, places=located(documented))
        ]
    if runs.ownership is None:
        # Правила не читаются: это `docs.unavailable`, владельцев здесь не посчитать.
        return []
    ownership = runs.ownership
    unowned = [node for node in documented if owner_of(node, ownership).team is None]
    items = [(node.module, node.doc_path) for node in unowned]
    return [_found("owners.unowned", [("module", items)], limit, places=located(unowned))]


def _parse_findings(runs: _Runs, limit: int) -> list[_Found]:
    """Файлы, разобранные с ошибками и не давшие ни одного объявления, — обоих шагов."""
    metas = [run.meta for run in (runs.scan, runs.web) if run is not None]
    files = sorted({path for meta in metas for path in meta.parse_error_files})
    return [
        _found(
            "parse.errors",
            [("directory", [(_parent(path), path) for path in files])],
            limit,
            places=_in_file(files),
        )
    ]


def _defects(ctx: SetupContext, runs: _Runs, limit: int) -> list[_Found]:
    """Дефекты настройки и прогонов: `config check`, ошибки загрузки, план шага 2."""
    report = check_config(ctx.settings, ctx.config, ctx.root, Path.cwd())
    problems = sorted(
        ((item.code, f"{item.key}: {item.message}") for item in report.problems),
    )

    loaded = list(runs.load_errors)
    warnings = sorted({line for inputs in runs.plans.values() for line in inputs.warnings})
    loaded += [("business", line) for line in warnings]

    unavailable = sorted(runs.plan_errors.items())
    unavailable += [
        (step, error) for step, inputs in sorted(runs.plans.items()) for error in inputs.plan.errors
    ]
    if runs.ownership_error and not runs.plans and not runs.plan_errors:
        # Ни один план не пробовали (оба прогона упали) — иначе о правилах
        # владения уже сказал план, который на них не собрался.
        unavailable.append(("ownership", runs.ownership_error))

    return [
        _found("config.problems", [("code", problems)], limit),
        _found("load.errors", [("step", sorted(loaded))], limit),
        _found("docs.unavailable", [("step", unavailable)], limit),
    ]


def _findings(ctx: SetupContext, runs: _Runs, limit: int) -> list[_Found]:
    found: list[_Found | None] = [
        *_scope_findings(ctx, runs, limit),
        *_symbol_findings(runs, limit),
        *_seam_findings(ctx, runs, limit),
        *_page_findings(runs, limit),
        *_document_findings(ctx, runs, limit),
        *_owner_findings(ctx, runs, limit),
        *_parse_findings(runs, limit),
        *_defects(ctx, runs, limit),
    ]
    order = {item.code: index for index, item in enumerate(FINDING_CODES)}
    present = [item for item in found if item is not None]
    unknown = sorted({item.code for item in present} - order.keys())
    if unknown:
        raise ValueError(f"код находки вне FINDING_CODES: {', '.join(unknown)}")
    return sorted(present, key=lambda item: order[item.code])


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def _out_of_scope(ctx: SetupContext, runs: _Runs, limit: int) -> OutOfScope:
    """Решённое «не брать»: модули под `not_enrolled`, фронты под `exclude`, их символы."""
    modules = (
        sorted(
            module.project_file
            for module in runs.scan.manifest.modules
            if scope_of(module.project_file, ctx.settings) == "not_enrolled"
        )
        if runs.scan is not None
        else []
    )
    _, fronts = _fronts(ctx)
    examples = [*modules, *(str(front["path"]) for front in fronts)]
    return OutOfScope(
        modules=len(modules),
        fronts=len(fronts),
        symbols=sum(
            1 for step in runs.steps for row in step.rows if row.decision.state == NOT_ENROLLED
        ),
        examples=examples[:limit] if limit else examples,
    )


def _compared(found: list[_Found], baseline: SetupStatus | None) -> list[Finding]:
    """Находки с `previous` из базы; исчезнувшие с прошлого прогона — с нулём.

    Без базы `previous` — `None`. С базой находка, которой там не было, —
    `previous: 0`: в базу идут только находки с местами.
    """
    before = {item.code: item.count for item in baseline.findings} if baseline else {}
    current = {item.code: item for item in found}
    findings: list[Finding] = []
    for code in FINDING_CODES:
        item = current.get(code.code)
        count = item.count if item is not None else 0
        previous = before.get(code.code, 0) if baseline is not None else None
        if count == 0 and not previous:
            continue
        findings.append(
            Finding(
                code=code.code,
                category=code.category,
                title=code.title,
                count=count,
                previous=previous,
                decision_home=code.decision_home,
                clusters=item.clusters if item is not None and count else [],
                clusters_total=item.total if item is not None and count else 0,
            )
        )
    return findings


@dataclass(frozen=True)
class StatusDetail:
    """Отчёт `setup status` и то, что в отчёт не идёт, — для ревью (S25).

    `coverage` — охват с разбивкой по файлам (как у `decision_coverage`),
    `places` — все места каждой находки отчёта с их файлами. Собраны тем же
    прогоном, что и отчёт: ревью сопоставляет с новыми файлами ровно то,
    что отчёт посчитал, а не второй подсчёт.
    """

    status: SetupStatus
    coverage: list[CoverageDetail]
    places: dict[str, tuple[FindingPlace, ...]]


def status_detail(ctx: SetupContext, *, limit: int = DEFAULT_LIMIT) -> StatusDetail:
    """Отчёт `setup status` вместе с разбивкой охвата по файлам и всеми местами находок."""
    status, coverage, found = _build(ctx, None, limit)
    places = {item.code: item.places for item in found if item.count}
    return StatusDetail(status, coverage, places)


def build_status(
    ctx: SetupContext, *, baseline: SetupStatus | None = None, limit: int = DEFAULT_LIMIT
) -> SetupStatus:
    """Что в области без решения и что сломано — по прогонам контекста в памяти.

    `baseline` — прошлый отчёт этой же команды (`--out`): из него `previous`
    у находок и охвата. `limit` — кластеров на срез находки и примеров
    вне области; `0` — все. Охват не усекается: он база сравнения следующего
    прогона, и обрезанный потерял бы `previous` у остального.
    """
    return _build(ctx, baseline, limit)[0]


def _build(
    ctx: SetupContext, baseline: SetupStatus | None, limit: int
) -> tuple[SetupStatus, list[CoverageDetail], list[_Found]]:
    """Отчёт, охват с разбивкой и находки с местами — одним прогоном."""
    if limit < 0:
        raise InputError("limit не бывает отрицательным")
    runs = _gather(ctx)
    found = _findings(ctx, runs, limit)
    details = _coverage(ctx, runs)
    findings = _compared(found, baseline)
    before = {item.id: item.count for item in baseline.coverage} if baseline else {}
    coverage = [
        Coverage(
            id=item.id,
            file=item.file,
            key=item.key,
            value=item.value,
            count=item.count,
            previous=before.get(item.id),
            reason=item.reason,
        )
        for item in details
    ]
    status = SetupStatus(
        findings=findings,
        coverage=coverage,
        out_of_scope=_out_of_scope(ctx, runs, limit),
        without_reason=_without_reason(ctx),
        unexplained=sum(item.count for item in findings if item.category == "decision"),
        defects=sum(item.count for item in findings if item.category == "defect"),
    )
    return status, details, found


def load_baseline(path: Path) -> SetupStatus:
    """Прошлый отчёт `setup status --out` — база сравнения. Не читается — `InputError`.

    Файл, собранный другой версией отчёта, — тоже отказ: `previous`, взятый
    из чужой формы, был бы правдоподобным и неверным.
    """
    try:
        return SetupStatus.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise InputError(
            f"база сравнения {path} не читается как отчёт setup status: {exc}"
        ) from exc


def status_json(report: SetupStatus) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))


def write_status(report: SetupStatus, path: Path) -> None:
    """Записать отчёт для `--baseline` следующего прогона. Каталог создаётся."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(status_json(report), encoding="utf-8")


# --------------------------------------------------------------------------------------
# Печать
# --------------------------------------------------------------------------------------


def _difference(count: int, previous: int | None) -> str:
    if previous is None:
        return ""
    if previous == count:
        return " (без изменений)"
    return f" (было {previous}, {count - previous:+d})"


def format_status(report: SetupStatus) -> str:
    """Текст для человека: сводка, затем по находке — заголовок, число, разница, кластеры."""
    out = report.out_of_scope
    lines = [
        f"В области: {plural(report.unexplained, 'находка', 'находки', 'находок')} без решения, "
        f"{plural(report.defects, 'дефект', 'дефекта', 'дефектов')}; "
        f"вне области: модулей {out.modules}, фронтов {out.fronts}."
    ]
    if not report.findings:
        lines += ["", "Находок нет: в области всё решено, дефектов нет."]
    for finding in report.findings:
        mark = "ДЕФЕКТ: " if finding.category == "defect" else ""
        lines += [
            "",
            f"{mark}{finding.title} ({finding.code}): "
            f"{finding.count}{_difference(finding.count, finding.previous)}",
        ]
        if finding.decision_home != "—":
            lines.append(f"  решение: {finding.decision_home}")
        for cluster in finding.clusters[:TEXT_CLUSTERS]:
            examples = "; ".join(cluster.examples)
            lines.append(
                f"  {cluster.slice} {cluster.key or '(пусто)'} — {cluster.count}: {examples}"
            )
        hidden = finding.clusters_total - min(len(finding.clusters), TEXT_CLUSTERS)
        if hidden > 0:
            lines.append(f"  … и ещё кластеров: {hidden} (--format json)")

    idle = [item for item in report.coverage if item.count == 0]
    lines += [
        "",
        f"Решений настройки: {len(report.coverage)}, без охвата: {len(idle)}"
        + (f" — {', '.join(item.id for item in idle[:TEXT_CLUSTERS])}" if idle else "")
        + ("…" if len(idle) > TEXT_CLUSTERS else "")
        + ".",
    ]
    if report.without_reason:
        lines.append(
            f"Без причины (подсказка, не находка): {len(report.without_reason)} — "
            + ", ".join(report.without_reason[:TEXT_CLUSTERS])
            + ("…" if len(report.without_reason) > TEXT_CLUSTERS else "")
            + "."
        )
    if out.symbols or out.examples:
        lines.append(
            f"Вне области символов: {out.symbols}"
            + (f"; {', '.join(out.examples)}" if out.examples else "")
            + "."
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "CODES",
    "FINDING_CODES",
    "Cluster",
    "Coverage",
    "CoverageDetail",
    "Finding",
    "FindingCode",
    "FindingPlace",
    "OutOfScope",
    "SetupStatus",
    "StatusDetail",
    "build_status",
    "decision_coverage",
    "decision_id",
    "format_status",
    "load_baseline",
    "status_detail",
    "status_json",
    "write_status",
]
