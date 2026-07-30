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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from docpipe.classify import Ruleset, classify, exclusion_of
from docpipe.model import DocNode, Manifest, Symbol

# Сколько строк показывать в каждом срезе по нерешённому.
_TOP = 15

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

INTERFACE_COVERED = "interface_covered"
UNDECIDED = "undecided"
NOT_DOCUMENTED = "not_documented"
NOT_ENROLLED = "not_enrolled"

# Категории, которые не являются видами сущностей. Отделены, потому что попадают
# в разные части отчёта: виды — в таблицу видов, эти четыре — в блок решений.
_SPECIAL = (NOT_DOCUMENTED, UNDECIDED, INTERFACE_COVERED, NOT_ENROLLED)


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
    documented_bases = {
        fqn for node in nodes if node.symbol for fqn in node.symbol.base_type_closure
    }

    counts: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    reasons: dict[str, str] = {}
    rest: list[Symbol] = []

    for symbol in index.values():
        if enrolled is not None and symbol.module not in enrolled:
            counts[NOT_ENROLLED] += 1
            continue
        if (exclusion := exclusion_of(symbol, ruleset)) is not None:
            counts[NOT_DOCUMENTED] += 1
            skipped[exclusion.id] += 1
            reasons[exclusion.id] = exclusion.reason
            continue

        classification = classify(symbol, ruleset)
        if classification is not None:
            counts[classification.kind] += 1
        elif symbol.type_kind == "interface" and symbol.fqn in documented_bases:
            counts[INTERFACE_COVERED] += 1
        else:
            counts[UNDECIDED] += 1
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
    какие правила писать. Эти пять срезов говорят — на ABP базовые типы сразу
    показывают `ITransientDependency` (997 типов, больше, чем покрывает весь
    набор по умолчанию), а модули — что половина непокрытого это тесты и примеры.
    """
    if not symbols:
        return {}

    sections = {
        "модули": _top(Path(symbol.module).stem for symbol in symbols),
        "окончания имён": _top(
            next((suffix for suffix in _SUFFIXES if symbol.name.endswith(suffix)), "(прочее)")
            for symbol in symbols
        ),
        "базовые типы": _top(base for symbol in symbols for base in symbol.base_type_closure),
        "атрибуты": _top(attribute.name for symbol in symbols for attribute in symbol.attributes),
        "namespace": _top(symbol.namespace or "(глобальный)" for symbol in symbols),
    }
    # Пустой срез (например, ни у одного непокрытого типа нет атрибутов)
    # только зашумляет вывод.
    return {title: rows for title, rows in sections.items() if rows}


def _top(values: Iterable[str]) -> list[tuple[str, int]]:
    return Counter(values).most_common(_TOP)


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


def format_breakdown(stats: Stats) -> str:
    """Срезы по нерешённому — за что зацепиться, чтобы принять решение."""
    if not stats.breakdown:
        return ""

    blocks = ["Решение не принято — за что зацепиться (топ):"]
    for title, rows in stats.breakdown.items():
        blocks.append(f"\n  {title}:")
        blocks += [f"    {count:6}  {name}" for name, count in rows]
    return "\n".join(blocks)


def format_report(stats: Stats) -> str:
    """Полный отчёт прогона: решения, виды, причины отсева, нерешённое.

    Собран здесь, а не в `cli`, чтобы порядок блоков проверялся тестом, а не
    поддерживался на глаз: он несёт смысл — от состояния к деталям и только
    потом к тому, что осталось сделать.
    """
    blocks = [format_decisions(stats), format_kinds(stats), format_skipped(stats)]
    blocks.append(format_breakdown(stats))
    return "\n\n".join(block for block in blocks if block)


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
