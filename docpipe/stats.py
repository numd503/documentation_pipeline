"""Счётчики и проверки манифеста — инструменты настройки правил.

Цифры здесь нужны не для отчётности, а для работы: `unclassified` показывает,
каких правил не хватает, и по нему набор и набирается. Поэтому в этот счётчик
не должно попадать ничего лишнего — иначе он перестаёт быть сигналом.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from docpipe.classify import Ruleset, classify, is_excluded
from docpipe.model import DocNode, Manifest, Symbol

# Сколько строк показывать в каждом срезе по неклассифицированным.
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
UNCLASSIFIED = "unclassified"
EXCLUDED = "excluded"
NOT_ENROLLED = "not_enrolled"


@dataclass(frozen=True)
class Stats:
    """Счётчики прогона и подсказки для настройки правил."""

    counts: dict[str, int]
    total: int
    breakdown: dict[str, list[tuple[str, int]]] = field(default_factory=dict)


def collect_stats(
    index: dict[str, Symbol],
    nodes: list[DocNode],
    ruleset: Ruleset,
    enrolled: set[str] | None = None,
) -> Stats:
    """Посчитать символы по видам и собрать срезы по непокрытым.

    `interface_covered` выделен из `unclassified` намеренно: интерфейс, у которого
    есть задокументированная реализация, — это осознанное решение документировать
    реализацию, а не пробел в правилах. В eShopOnWeb таких 9 из 199, и без
    отдельной категории они засоряли бы главный сигнал настройки.

    `not_enrolled` — символы модулей, которые вообще не входят в документацию.
    Считать их неклассифицированными нельзя: правила к ним и не применялись,
    а в `unclassified` они дают ложный сигнал «допишите правил». На semantic-kernel
    это 1258 символов из 1258 — то есть весь счётчик был бы мусором.
    """
    documented_bases = {
        fqn for node in nodes if node.symbol for fqn in node.symbol.base_type_closure
    }

    counts: Counter[str] = Counter()
    rest: list[Symbol] = []

    for symbol in index.values():
        if enrolled is not None and symbol.module not in enrolled:
            counts[NOT_ENROLLED] += 1
            continue
        if is_excluded(symbol, ruleset):
            counts[EXCLUDED] += 1
            continue

        classification = classify(symbol, ruleset)
        if classification is not None:
            counts[classification.kind] += 1
        elif symbol.type_kind == "interface" and symbol.fqn in documented_bases:
            counts[INTERFACE_COVERED] += 1
        else:
            counts[UNCLASSIFIED] += 1
            rest.append(symbol)

    return Stats(counts=dict(counts), total=len(index), breakdown=_breakdown(rest))


def stats_from_manifest(manifest: Manifest) -> Stats:
    """Счётчики по готовому манифесту.

    `unclassified` здесь быть не может: в манифест попадают только узлы,
    то есть уже классифицированное. Это не потеря, а следствие — считать
    непокрытое можно только имея индекс символов целиком.
    """
    counts = Counter(node.kind for node in manifest.nodes)
    return Stats(counts=dict(counts), total=len(manifest.nodes))


def _breakdown(symbols: list[Symbol]) -> dict[str, list[tuple[str, int]]]:
    """Срезы по неклассифицированным символам.

    Одного счётчика для настройки мало: «7142 неклассифицировано» не говорит,
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


def format_stats(stats: Stats, total_label: str = "total symbols") -> str:
    """Таблица счётчиков. Виды идут по алфавиту, служебные категории — после них."""
    special = [INTERFACE_COVERED, UNCLASSIFIED, EXCLUDED, NOT_ENROLLED]
    kinds = sorted(kind for kind in stats.counts if kind not in special)
    order = kinds + [kind for kind in special if kind in stats.counts]

    width = max(18, *(len(kind) for kind in order), len(total_label))
    lines = [f"{'kind':<{width}}  {'count':>5}", f"{'-' * width}  {'-' * 5}"]
    lines += [f"{kind:<{width}}  {stats.counts[kind]:>5}" for kind in order]
    lines += [f"{'-' * width}  {'-' * 5}", f"{total_label:<{width}}  {stats.total:>5}"]
    return "\n".join(lines)


def format_breakdown(stats: Stats) -> str:
    """Срезы по неклассифицированным — подсказка, какие правила писать."""
    if not stats.breakdown:
        return ""

    blocks = ["", "Чего не хватает правилам (топ по неклассифицированным):"]
    for title, rows in stats.breakdown.items():
        blocks.append(f"\n  {title}:")
        blocks += [f"    {count:6}  {name}" for name, count in rows]
    return "\n".join(blocks)


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
