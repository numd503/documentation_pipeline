"""Счётчики и проверки манифеста — инструменты настройки правил.

Отчёт показывает **состояние решений**, а не «топ непокрытого». Про каждый
символ решение либо принято — «документируем как X» или «не документируем,
потому что Y», — либо нет, и тогда он попадает в `undecided`. Это единственное
число, которое обязано идти к нулю, и единственное, по которому видно, что
настройка набора правил закончена.

Различие не косметическое. Пока «решено не документировать» было безымянным
счётчиком `excluded`, а всё остальное — `unclassified`, отчёт не мог отличить
«посмотрели и решили, что не надо» от «ещё не смотрели»: второго состояния
в модели не было. Поэтому цифра оставалась большой всегда, и прибавление
одного нового типа в репозитории в ней не было видно.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from docpipe.classify import ExcludeRule, Ruleset, classify, exclusion_of
from docpipe.hashing import camel_words
from docpipe.model import DocNode, Lang, Manifest, RunMeta, Symbol

# Сколько строк показывать в каждом срезе по нерешённому по умолчанию.
# Переопределяется флагом `--top`: на репозитории с сотнями проектов срез
# из пятнадцати строк выглядит полным списком, и остальное не видно.
TOP = 15

# Окончания имён, по которым в .NET обычно и опознают вид сущности.
_SUFFIXES = (
    "Service",
    "Handler",
    "Manager",
    "Factory",
    "Client",
    "Builder",
    "Provider",
    "Repository",
    "Store",
    "Processor",
    "Validator",
    "Job",
    "Worker",
    "Module",
    "Controller",
    "Endpoint",
    "Middleware",
    "Filter",
    "Converter",
    "Extensions",
    "Attribute",
    "Exception",
    "Command",
    "Query",
    "Event",
    "Adapter",
    "Strategy",
)

DOCUMENTED = "documented"
INTERFACE_COVERED = "interface_covered"

# Символ, который документируется внутри документа страницы (P07). Решение,
# а не пробел: отдельного файла у него нет, но описан он будет — разделом
# «Логика» или «Состояние» той страницы, которая одна до него дотягивается.
# Отдельная категория нужна ровно затем же, зачем `interface_covered`:
# не засорять `undecided`, единственное число, которое обязано идти к нулю.
PAGE_COVERED = "page_covered"
UNDECIDED = "undecided"
NOT_DOCUMENTED = "not_documented"
NOT_ENROLLED = "not_enrolled"

# Человеческие названия состояний. В отчёте и в выборке символов они обязаны
# совпадать: иначе `--state undecided` и строка «решение не принято» выглядят
# как разные вещи, хотя это одно и то же.
STATE_TITLES: dict[str, str] = {
    DOCUMENTED: "документируем",
    NOT_DOCUMENTED: "не документируем",
    NOT_ENROLLED: "вне области",
    INTERFACE_COVERED: "интерфейс с реализацией",
    PAGE_COVERED: "документируется внутри страницы",
    UNDECIDED: "решение не принято",
}

# Категории, которые не являются видами сущностей. Отделены, потому что попадают
# в разные части отчёта: виды — в таблицу видов, эти четыре — в блок решений.
_SPECIAL = (NOT_DOCUMENTED, UNDECIDED, INTERFACE_COVERED, NOT_ENROLLED, PAGE_COVERED)


@dataclass(frozen=True)
class Decision:
    """Что решено про один символ.

    Одна реализация на отчёт и на выборку символов. Разные означали бы, что
    `--stats` говорит «шесть без решения», а `symbols --state undecided`
    показывает пять или семь, — и доверять было бы нельзя ни тому, ни другому.
    """

    state: str
    kind: str | None = None
    exclusion: ExcludeRule | None = None
    matched_rules: list[str] = field(default_factory=list)

    # Заголовок страницы, внутри документа которой символ описывается.
    # Заполнен только у `page_covered`: без имени страницы состояние
    # неотличимо от «документируем где-то там».
    page: str = ""

    # Правило, давшее вид (`Classification.winner`). У отсеянного пусто:
    # его решение — `exclusion`, и смешивать две секции в одном поле значило
    # бы считать охват отсева и классификации одним числом.
    winner_rule: str | None = None


def documented_base_types(nodes: list[DocNode]) -> set[str]:
    """FQN всех базовых типов документируемых узлов — вход для `interface_covered`."""
    return {fqn for node in nodes if node.symbol for fqn in node.symbol.base_type_closure}


def absorbed_page_refs(nodes: list[DocNode]) -> dict[str, tuple[str, str]]:
    """FQN -> `(id, заголовок)` страницы, поглотившей узел.

    Заголовок — для человека, `id` — для того, кто пойдёт дальше по манифесту:
    заголовок — имя класса, и уникальным он не обязан быть (одноимённые
    компоненты в разных модулях). Страница, которой в манифесте нет,
    подписывается своим `id`: пустой заголовок читался бы как «страницы нет».

    Считается по манифесту, а не по правилам: поглощение — свойство графа
    вызовов, и правило о нём ничего не знает.
    """
    titles = {node.id: node.title for node in nodes}
    return {
        node.symbol.fqn: (node.absorbed_by, titles.get(node.absorbed_by, node.absorbed_by))
        for node in nodes
        if node.symbol and node.absorbed_by
    }


def absorbed_pages(nodes: list[DocNode]) -> dict[str, str]:
    """FQN -> заголовок страницы, поглотившей узел. Вход для `page_covered`.

    Производная от `absorbed_page_refs`, а не вторая реализация: заголовок
    в отчёте и пара в выборке символов обязаны называть одну страницу.
    """
    return {fqn: title for fqn, (_, title) in absorbed_page_refs(nodes).items()}


def decide(
    symbol: Symbol,
    ruleset: Ruleset,
    enrolled: set[str] | None = None,
    documented_bases: frozenset[str] | set[str] = frozenset(),
    absorbed: dict[str, str] | None = None,
) -> Decision:
    """Состояние одного символа. Порядок проверок значим и повторяться не должен.

    Сначала область (`enrolled`), потом отсев, потом правила: правила к символу
    вне области не применяются вовсе, а отсев отменяет классификацию, даже если
    подходящее правило есть.
    """
    if enrolled is not None and symbol.module not in enrolled:
        return Decision(state=NOT_ENROLLED)

    if (exclusion := exclusion_of(symbol, ruleset)) is not None:
        return Decision(state=NOT_DOCUMENTED, exclusion=exclusion)

    if (classification := classify(symbol, ruleset)) is not None:
        page = (absorbed or {}).get(symbol.fqn, "")
        return Decision(
            state=PAGE_COVERED if page else DOCUMENTED,
            kind=classification.kind,
            matched_rules=classification.matched_rules,
            page=page,
            winner_rule=classification.winner,
        )

    if symbol.type_kind == "interface" and symbol.fqn in documented_bases:
        return Decision(state=INTERFACE_COVERED)

    return Decision(state=UNDECIDED)


def enrolled_keys(manifest: Manifest, lang: Lang) -> set[str]:
    """Включённые модули в той форме, в какой их сверяет `decide` с `Symbol.module`.

    У .NET это путь `.csproj` (`project_file`), у фронта — ключ модуля, то есть
    `id` без префикса `module:`: `project_file` там — `angular.json`, общий
    у нескольких проектов workspace. Перепутать формы — получить `not_enrolled`
    на всём дереве без единой ошибки, поэтому выражение одно на `symbols`,
    `web scan --stats`, `setup status` и сервер настройки.
    """
    if lang == "cs":
        return {module.project_file for module in manifest.modules if module.enrolled}
    return {module.id.removeprefix("module:") for module in manifest.modules if module.enrolled}


@dataclass(frozen=True)
class Stats:
    """Счётчики прогона и подсказки для настройки правил."""

    counts: dict[str, int]
    total: int
    breakdown: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    skipped: list[tuple[str, str, int]] = field(default_factory=list)
    """`(id правила, причина, сколько символов)` — по убыванию счётчика, затем по id.

    Порядок задан явно, потому что таблица идёт человеку на глаза и попадает
    в журнал: сортировка по счётчику ставит наверх решение, которое отсекает
    больше всего, — то есть то, которое стоит перечитать первым.
    """


def collect_stats(
    index: dict[str, Symbol],
    nodes: list[DocNode],
    ruleset: Ruleset,
    enrolled: set[str] | None = None,
) -> Stats:
    """Посчитать символы по видам и собрать срезы по непокрытым.

    `interface_covered` выделен из `undecided` намеренно: интерфейс, у которого
    есть задокументированная реализация, — это осознанное решение документировать
    реализацию, а не пробел в правилах. В eShopOnWeb таких 9 из 199, и без
    отдельной категории они засоряли бы главный сигнал настройки. Решение здесь
    принимает инструмент, а не человек, поэтому в отчёте категория и подписана так.

    `not_enrolled` — символы модулей, которые вообще не входят в документацию.
    Считать их нерешёнными нельзя: решение по ним принято, просто в другом файле
    (`enrolled` в `docpipe.yaml`), и правила к ним не применялись. На
    semantic-kernel это 1258 символов из 1258 — то есть весь счётчик был бы мусором.
    """
    documented_bases = documented_base_types(nodes)

    counts: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    reasons: dict[str, str] = {}
    rest: list[Symbol] = []

    absorbed = absorbed_pages(nodes)

    for symbol in index.values():
        decision = decide(symbol, ruleset, enrolled, documented_bases, absorbed)
        # Документируемые считаются по видам: вид — это и есть содержание решения.
        # Поглощённые — по состоянию: у них вид есть, но своего документа нет,
        # и в таблице видов они бы обещали файлы, которых не будет.
        counts[
            decision.state if decision.state in _SPECIAL else (decision.kind or decision.state)
        ] += 1
        if decision.exclusion is not None:
            skipped[decision.exclusion.id] += 1
            reasons[decision.exclusion.id] = decision.exclusion.reason
        if decision.state == UNDECIDED:
            rest.append(symbol)

    return Stats(
        counts=dict(counts),
        total=len(index),
        breakdown=_breakdown(rest),
        skipped=[
            (rule_id, reasons[rule_id], count)
            for rule_id, count in sorted(skipped.items(), key=lambda item: (-item[1], item[0]))
        ],
    )


def stats_from_manifest(manifest: Manifest) -> Stats:
    """Счётчики по готовому манифесту.

    Состояния решений здесь быть не может: в манифест попадают только узлы,
    то есть то, про что решено «документируем». Это не потеря, а следствие —
    считать нерешённое можно только имея индекс символов целиком.
    """
    counts = Counter(node.kind for node in manifest.nodes)
    return Stats(counts=dict(counts), total=len(manifest.nodes))


def _breakdown(symbols: list[Symbol]) -> dict[str, list[tuple[str, int]]]:
    """Срезы по символам, про которые решения нет.

    Одного счётчика для настройки мало: «7142 без решения» не говорит,
    какие правила писать. Эти шесть срезов говорят — на ABP базовые типы сразу
    показывают `ITransientDependency` (997 типов, больше, чем покрывает весь
    набор по умолчанию), а модули — что половина непокрытого это тесты и примеры.

    «Последнее слово» стоит рядом с «окончаниями имён», а не вместо них.
    Окончания знают только зашитый словарь .NET, и конвенции проекта
    (`*Rq`, `*Dm`) и фронта (`Component`, `Guard`, `State`) уходят у них
    в «(прочее)» — на боевом репозитории 3446 типов, за которые не зацепиться.
    Последнее слово берётся из самого имени и словаря не требует.
    """
    if not symbols:
        return {}

    sections = {
        "модули": _top(Path(symbol.module).stem for symbol in symbols),
        "окончания имён": _top(
            next((suffix for suffix in _SUFFIXES if symbol.name.endswith(suffix)), "(прочее)")
            for symbol in symbols
        ),
        "последнее слово": _top(word for symbol in symbols if (word := last_word(symbol.name))),
        "базовые типы": _top(base for symbol in symbols for base in symbol.base_type_closure),
        "атрибуты": _top(attribute.name for symbol in symbols for attribute in symbol.attributes),
        "namespace": _top(symbol.namespace or "(глобальный)" for symbol in symbols),
    }
    # Пустой срез (например, ни у одного непокрытого типа нет атрибутов)
    # только зашумляет вывод.
    return {title: rows for title, rows in sections.items() if rows}


def last_word(name: str) -> str:
    """Последнее слово CamelCase имени; пусто, если зацепиться не за что.

    Одиночное `I` словом не считается: это префикс интерфейса, а не конвенция
    имени, и строка «I» в срезе ни на какое правило не указывает. Цифры
    остаются при слове (`Migration20240101`) — так режет `camel_words`,
    общая с именем файла документа.
    """
    words = camel_words(name)
    word = words[-1] if words else ""
    return "" if word == "I" else word


def _top(values: Iterable[str]) -> list[tuple[str, int]]:
    """Счётчик по убыванию, при равенстве — по имени.

    Явная сортировка, а не `most_common()`: тот при равных счётчиках сохраняет
    порядок вставки, то есть порядок обхода индекса. Срез идёт человеку на глаза
    и в журнал, и переставляться между прогонами он не должен.

    Обрезка здесь не делается: сколько строк показать — вопрос вывода, и `--top`
    иначе пришлось бы протаскивать через весь конвейер до `run()`.
    """
    return sorted(Counter(values).items(), key=lambda item: (-item[1], item[0]))


# --------------------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------------------


def kind_counts(stats: Stats) -> dict[str, int]:
    """Только виды сущностей, без служебных категорий.

    Отдельная функция, потому что вопрос «какие ключи здесь виды» задают три
    места: блок решений, таблица видов и счётчики сидкара. Три ответа на него
    разошлись бы на первой новой категории.
    """
    return {kind: count for kind, count in stats.counts.items() if kind not in _SPECIAL}


def plural(count: int, one: str, few: str, many: str) -> str:
    """Число с существительным: 1 правило, 2 правила, 5 правил.

    Отчёт читает человек, и «1 правил» в нём выглядит дефектом инструмента.
    """
    if count % 100 not in (11, 12, 13, 14):
        if count % 10 == 1:
            return f"{count} {one}"
        if count % 10 in (2, 3, 4):
            return f"{count} {few}"
    return f"{count} {many}"


def format_decisions(stats: Stats) -> str:
    """Состояние решений: сколько решено и сколько ещё нет.

    Значима последняя строка. Всё, что выше неё, уже решено и перечитывать это
    не нужно; работа — ровно в «решение не принято», и когда там ноль, настройка
    набора правил закончена. Нулевые строки не показываются: категория, в которую
    не попал ни один символ, только уводит взгляд от той, в которую попали.
    """
    kinds = kind_counts(stats)
    undecided = stats.counts.get(UNDECIDED, 0)

    rows = [
        (
            "документируем",
            sum(kinds.values()),
            plural(len(kinds), "вид", "вида", "видов"),
        ),
        (
            "не документируем",
            stats.counts.get(NOT_DOCUMENTED, 0),
            plural(len(stats.skipped), "решение", "решения", "решений"),
        ),
        ("вне области", stats.counts.get(NOT_ENROLLED, 0), "enrolled в docpipe.yaml"),
        ("интерфейс с реализацией", stats.counts.get(INTERFACE_COVERED, 0), "решил инструмент"),
        (
            "документируется внутри страницы",
            stats.counts.get(PAGE_COVERED, 0),
            "решил инструмент: одна страница",
        ),
    ]
    last = (
        ("РЕШЕНИЕ НЕ ПРИНЯТО", undecided, "<- это и есть работа")
        if undecided
        else ("решение не принято", 0, "решены все символы")
    )
    shown = [row for row in rows if row[1]] + [last]

    width = max(len(label) for label, _, _ in shown)
    lines = [f"Решения по {plural(stats.total, 'символу', 'символам', 'символам')}:", ""]
    lines += [f"  {label:<{width}}  {count:>6}  {hint}" for label, count, hint in shown[:-1]]
    lines.append(f"  {'-' * (width + 8)}")
    lines.append(f"  {last[0]:<{width}}  {last[1]:>6}  {last[2]}")
    return "\n".join(lines)


def format_kinds(stats: Stats, total_label: str | None = None) -> str:
    """Таблица видов сущностей, по алфавиту.

    Служебные категории здесь не показываются: они в блоке решений, и дублировать
    их значило бы предлагать человеку сверять две таблицы об одном и том же.
    Итоговая строка печатается только там, где она равна сумме, — то есть по
    манифесту; в прогоне сумма видов меньше числа символов, и `total` в этой
    таблице выглядел бы ошибкой.
    """
    kinds = sorted(kind_counts(stats))
    if not kinds:
        return ""

    width = max(18, *(len(kind) for kind in kinds), len(total_label or ""))
    lines = [f"{'kind':<{width}}  {'count':>5}", f"{'-' * width}  {'-' * 5}"]
    lines += [f"{kind:<{width}}  {stats.counts[kind]:>5}" for kind in kinds]
    if total_label is not None:
        lines += [f"{'-' * width}  {'-' * 5}", f"{total_label:<{width}}  {stats.total:>5}"]
    return "\n".join(lines)


def format_skipped(stats: Stats) -> str:
    """Таблица «не документируем»: по какому решению и почему.

    Заодно единственный способ увидеть правило-заглушку. Широкое условие
    с причиной «разберусь потом» обнулит нерешённое, ничего не решив, и заметно
    это только здесь — по одному решению с неправдоподобно большим счётчиком.
    """
    if not stats.skipped:
        return ""

    width = max(len(rule_id) for rule_id, _, _ in stats.skipped)
    lines = ["Не документируем — по какому решению:"]
    lines += [
        f"  {count:6}  {rule_id:<{width}}  {reason}" for rule_id, reason, count in stats.skipped
    ]
    return "\n".join(lines)


def format_breakdown(stats: Stats, top: int = TOP) -> str:
    """Срезы по нерешённому — за что зацепиться, чтобы принять решение.

    Обрезанный срез помечается остатком: «и ещё 476». Без этого на репозитории
    с сотнями проектов топ-15 выглядит полным списком, и половина работы
    остаётся невидимой.
    """
    if not stats.breakdown:
        return ""

    blocks = ["Решение не принято — за что зацепиться (топ):"]
    for title, rows in stats.breakdown.items():
        blocks.append(f"\n  {title}:")
        blocks += [f"    {count:6}  {name}" for name, count in rows[:top]]
        if len(rows) > top:
            blocks.append(
                f"    {'':6}  и ещё {plural(len(rows) - top, 'строка', 'строки', 'строк')}"
            )
    return "\n".join(blocks)


def format_report(stats: Stats, top: int = TOP) -> str:
    """Полный отчёт прогона: решения, виды, причины отсева, нерешённое.

    Собран здесь, а не в `cli`, чтобы порядок блоков проверялся тестом, а не
    поддерживался на глаз: он несёт смысл — от состояния к деталям и только
    потом к тому, что осталось сделать.
    """
    blocks = [format_decisions(stats), format_kinds(stats), format_skipped(stats)]
    blocks.append(format_breakdown(stats, top))
    return "\n\n".join(block for block in blocks if block)


# --------------------------------------------------------------------------------------
# Отчёт структурой: `scan --stats --format json`, `web scan --stats --format json`
# --------------------------------------------------------------------------------------

STATS_SCHEMA_VERSION: Final = "1.0"

BreakdownKey = Literal[
    "modules", "suffixes", "last_words", "base_types", "attributes", "namespaces"
]

# Срезы `Stats.breakdown` называются по-русски: ключи идут в текст отчёта
# и на них опираются тесты. В JSON ключи латинские и стабильные, поэтому
# соответствие — таблица здесь, а не переименование словаря. Срез, забытый
# в таблице, выпал бы из JSON молча; `build_stats_report` на нём отказывает.
BREAKDOWN_KEYS: Final[dict[str, BreakdownKey]] = {
    "модули": "modules",
    "окончания имён": "suffixes",
    "последнее слово": "last_words",
    "базовые типы": "base_types",
    "атрибуты": "attributes",
    "namespace": "namespaces",
}

# Ключи блока решений. `documented` — сумма по видам: в `Stats.counts`
# документируемые лежат по видам, а не одним числом.
DECISION_KEYS: Final[tuple[str, ...]] = (
    DOCUMENTED,
    NOT_DOCUMENTED,
    UNDECIDED,
    INTERFACE_COVERED,
    PAGE_COVERED,
    NOT_ENROLLED,
)


class _Report(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Slice(_Report):
    """Срез по нерешённому. `items` усечены до `--top`, `total` — число строк до усечения.

    Без `total` усечённый срез неотличим от полного: агент контура прочтёт
    «топ-15» как «их пятнадцать» — ровно то, от чего текст защищает строкой
    «и ещё 476».
    """

    total: int
    items: list[tuple[str, int]]


class SkippedRule(_Report):
    """Решение «не документируем»: какое правило, почему и сколько символов отсеяло."""

    rule_id: str
    reason: str
    count: int


class StaleOverride(_Report):
    """Правило `pages.yaml`, не легшее ни на что. Только у фронта."""

    kind: str
    key: str
    reason: str


class ScopeInfo(_Report):
    """Чем неполон прогон .NET: скоуп и файлы вне скоупа, взятые из кэша или потерянные.

    Числа скоуп-прогона без этой пометки выглядят как числа всего репозитория,
    а граф наследования у него собран не целиком.
    """

    partial: bool
    restored_from_cache: int
    missing_from_cache: int


class StatsReport(_Report):
    """Состояние решений прогона структурой — то же, что текст `--stats`.

    Виды и особые состояния разведены: в `Stats.counts` они лежат одним
    словарём, и потребитель JSON иначе обязан был бы знать, какие ключи
    там не виды. `decisions` содержит все шесть ключей, нулевые тоже:
    отсутствие ключа читалось бы как «не посчитано».
    """

    schema_version: Literal["1.0"] = STATS_SCHEMA_VERSION
    lang: Lang
    total: int
    decisions: dict[str, int]
    kinds: list[tuple[str, int]]
    skipped: list[SkippedRule]
    breakdown: dict[BreakdownKey, Slice]
    stale_overrides: list[StaleOverride]
    scope: ScopeInfo | None
    parse_error_files: list[str]


class _Stale(Protocol):
    """То, что отчёт берёт у протухшего правила `pages.yaml`.

    Протокол, а не `web.overrides.StaleRule`: счётчики — общий слой, и
    зависимость от шага `web` развернула бы стрелку между модулями.
    """

    @property
    def kind(self) -> str: ...

    @property
    def key(self) -> str: ...

    @property
    def reason(self) -> str: ...


def scope_info(manifest: Manifest, meta: RunMeta) -> ScopeInfo:
    """Пометка о неполноте прогона .NET — из манифеста и сидкара этого же прогона."""
    return ScopeInfo(
        partial=manifest.partial is not None,
        restored_from_cache=meta.stats.get("restored_from_cache", 0),
        missing_from_cache=meta.stats.get("missing_from_cache", 0),
    )


def build_stats_report(
    stats: Stats,
    *,
    lang: Lang,
    top: int = TOP,
    stale: Sequence[_Stale] = (),
    scope: ScopeInfo | None = None,
    parse_error_files: Sequence[str] = (),
) -> StatsReport:
    """Отчёт `--stats` моделью. Его же позовёт инструмент сервера настройки.

    `top` усекает срезы так же, как в тексте; `total` каждого среза — до
    усечения. Все шесть срезов присутствуют всегда, пустые — с нулём: агенту
    «среза нет» и «в срезе ничего» незачем различать по наличию ключа.
    """
    if top < 0:
        raise ValueError(f"top не бывает отрицательным: {top}")
    if unknown := sorted(set(stats.breakdown) - set(BREAKDOWN_KEYS)):
        raise ValueError(f"срез без латинского ключа в BREAKDOWN_KEYS: {', '.join(unknown)}")

    kinds = kind_counts(stats)
    decisions = {key: stats.counts.get(key, 0) for key in DECISION_KEYS}
    decisions[DOCUMENTED] = sum(kinds.values())

    return StatsReport(
        lang=lang,
        total=stats.total,
        decisions=decisions,
        kinds=sorted(kinds.items(), key=lambda item: item[0]),
        skipped=[
            SkippedRule(rule_id=rule_id, reason=reason, count=count)
            for rule_id, reason, count in stats.skipped
        ],
        breakdown={
            key: Slice(
                total=len(rows := stats.breakdown.get(title, [])),
                items=rows[:top],
            )
            for title, key in BREAKDOWN_KEYS.items()
        },
        stale_overrides=sorted(
            (StaleOverride(kind=rule.kind, key=rule.key, reason=rule.reason) for rule in stale),
            key=lambda rule: (rule.kind, rule.key, rule.reason),
        ),
        scope=scope,
        parse_error_files=sorted(parse_error_files),
    )


# --------------------------------------------------------------------------------------
# Проверка манифеста
# --------------------------------------------------------------------------------------


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def validate_manifest(
    manifest: Manifest, parse_error_files: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """Проверить инварианты манифеста. Возвращает `(ошибки, предупреждения)`.

    Схемой дело не ограничивается: каждый из этих инвариантов однажды
    нарушался на реальном коде и приводил к молчаливой потере документов.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for label, values in (
        ("узлов", [node.id for node in manifest.nodes]),
        ("модулей", [module.id for module in manifest.modules]),
    ):
        if duplicates := _duplicates(values):
            errors.append(
                f"повторяющиеся id {label}: {', '.join(duplicates[:5])}"
                + (f" и ещё {len(duplicates) - 5}" if len(duplicates) > 5 else "")
            )

    # Два узла, пишущие в один файл, — это потеря документа на шаге 2.
    if duplicates := _duplicates([node.doc_path for node in manifest.nodes]):
        errors.append(
            f"повторяющиеся doc_path: {', '.join(duplicates[:5])}"
            + (f" и ещё {len(duplicates) - 5}" if len(duplicates) > 5 else "")
        )

    # Разбор сломался так, что тип исчез целиком. Единственный внешний признак.
    if parse_error_files:
        errors.append(
            f"файлы разобраны с ошибками и не дали ни одного типа "
            f"({len(parse_error_files)}): {', '.join(parse_error_files[:5])}"
        )

    # Не ошибка: такие файлы просто никогда не компилируются вместе.
    suspicious = _multi_source_without_partial(manifest.nodes)
    if suspicious:
        warnings.append(
            f"типы объявлены в нескольких файлах без модификатора partial "
            f"({len(suspicious)}): {', '.join(suspicious[:5])}"
        )

    return errors, warnings


def _multi_source_without_partial(nodes: list[DocNode]) -> list[str]:
    return sorted(
        node.symbol.fqn
        for node in nodes
        if node.symbol and len(node.symbol.sources) > 1 and "partial" not in node.symbol.modifiers
    )
