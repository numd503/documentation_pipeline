"""Точки входа и бизнес-документы: кто кого покрывает (G17, часть).

Отчёт отвечает на два вопроса и оба обязан печатать всегда: **какие точки
входа не описаны** и **какие документы ссылаются на то, чего в графе нет**.

Первое — состояние работы: точка входа без документа не дефект, а пункт
списка. Второе — уже находка: якорь, который никуда не разрешается,
через месяц неотличим от опечатки.

**Стрелка одна: техника ссылается на бизнес.** Бизнес-документ не знает
ни ключа узла, ни пути документа; он объявляет якорь — вид и `ref`, — и связь
строится сопоставлением с записями реестра, а не ссылкой из документа в граф.
Обратная стрелка вернула бы схему, которая уже была признана
неподдерживаемой: рефакторинг ломал бы бизнес-документ.
"""

from dataclasses import dataclass, field

from docpipe.business.model import Catalog

# Мост между словарями: аналитик пишет `table` и `kafka`, реестр объявляет
# `list` и `kafka_topic`. Пара, которой здесь нет, просто никогда
# не разрешится — и это будет выглядеть как «инструмент не нашёл».
from docpipe.business.resolve import REGISTRY_KIND
from docpipe.graph.model import GraphNode
from docpipe.keys import normalize_identifier


@dataclass(frozen=True)
class CoverageReport:
    entry_points: int = 0
    covered: int = 0
    uncovered_by_kind: dict[str, int] = field(default_factory=dict)
    documents: int = 0
    anchors: int = 0
    anchors_without_entry_point: tuple[str, ...] = ()
    uncovered_examples: tuple[str, ...] = ()
    # Спецификации (G17 п. 3): идентификатор живёт **на технической стороне**,
    # в атрибуте записи реестра. Стрелка та же, что у бизнес-слоя: техника
    # ссылается на чужую систему, а не чужая система на наши ключи, — иначе
    # переименование класса ломало бы спецификацию.
    with_spec: int = 0
    specs: tuple[str, ...] = ()
    without_spec_examples: tuple[str, ...] = ()

    def as_counts(self) -> dict[str, int]:
        counts = {
            "точек входа всего": self.entry_points,
            "точек входа описано документом": self.covered,
            "бизнес-документов": self.documents,
            "якорей в документах": self.anchors,
            "якорей без точки входа": len(self.anchors_without_entry_point),
            "точек входа со спецификацией": self.with_spec,
            "спецификаций названо": len(self.specs),
        }
        for kind, number in sorted(self.uncovered_by_kind.items()):
            counts[f"не описано, вид {kind}"] = number
        return counts


def _entry_identity(node: GraphNode) -> tuple[str, str]:
    """Пара «вид реестра + якорь», по которой сходятся граф и каталог."""
    kind = node.attributes.get("registry_kind") or node.attributes.get("entry_kind", "")
    ref = node.attributes.get("ref") or node.name
    return kind, normalize_identifier(ref)


def coverage(nodes: tuple[GraphNode, ...], catalog: Catalog) -> CoverageReport:
    """Сопоставить корни графа с якорями бизнес-каталога."""
    roots = [node for node in nodes if node.kind == "entry_point"]
    by_identity: dict[tuple[str, str], GraphNode] = {}
    for node in roots:
        by_identity.setdefault(_entry_identity(node), node)

    covered: set[str] = set()
    anchors = 0
    dangling: list[str] = []

    for document in catalog.docs:
        for anchor in document.anchors:
            anchors += 1
            registry_kind = REGISTRY_KIND.get(anchor.kind, anchor.kind)
            identity = (registry_kind, normalize_identifier(anchor.ref))
            found = by_identity.get(identity)
            if found is None:
                # Якорь может быть законно неразрешим: процесс начинается
                # в чужой команде, и такой якорь помечен `verify: false`.
                if anchor.verify:
                    dangling.append(f"{document.id}: {anchor.kind} {anchor.ref}")
                continue
            covered.add(found.key)

    uncovered: dict[str, int] = {}
    examples: list[str] = []
    for node in roots:
        if node.key in covered:
            continue
        kind = node.attributes.get("entry_kind", "—")
        uncovered[kind] = uncovered.get(kind, 0) + 1
        if len(examples) < 20:
            examples.append(f"{kind}: {node.name}")

    # Идентификатор спецификации — свободный атрибут записи реестра
    # (`attributes: {spec: CF-SPEC-42}`). Отдельного поля в схеме нет
    # намеренно: чужая система именуется по-своему, и поле, придуманное
    # под одну из них, второй не подойдёт.
    spec_of = {node.key: node.attributes.get("spec", "") for node in roots}
    specs = sorted({value for value in spec_of.values() if value})
    without_spec = [
        f"{node.attributes.get('entry_kind', '—')}: {node.name}"
        for node in roots
        if not spec_of.get(node.key)
    ]

    return CoverageReport(
        entry_points=len(roots),
        covered=len(covered),
        uncovered_by_kind=uncovered,
        documents=len(catalog.docs),
        anchors=anchors,
        anchors_without_entry_point=tuple(sorted(dangling)[:20]),
        uncovered_examples=tuple(examples),
        with_spec=sum(1 for value in spec_of.values() if value),
        specs=tuple(specs),
        without_spec_examples=tuple(sorted(without_spec)[:20]),
    )


def format_coverage(report: CoverageReport) -> str:
    """Отчёт для человека. Красным по умолчанию не бывает."""
    share = report.covered / report.entry_points if report.entry_points else 1.0
    lines = [
        f"Точек входа: {report.entry_points}, описано документом: {report.covered} ({share:.0%})",
        f"Бизнес-документов: {report.documents}, якорей в них: {report.anchors}",
        f"Со спецификацией: {report.with_spec}, названо спецификаций: {len(report.specs)}",
        "",
    ]
    if report.entry_points and not report.specs:
        # Ноль спецификаций — законное состояние, а не пустая строка отчёта:
        # на репозитории, где спецификаций нет, их не должно быть и здесь.
        lines.append(
            "Спецификаций не названо ни у одной точки входа. Это законно: "
            "идентификатор ставится атрибутом `spec` у записи реестра, "
            "и репозиторий без внешних спецификаций его не заполняет."
        )
        lines.append("")
    if report.uncovered_by_kind:
        lines.append("Не описано, по видам:")
        for kind, number in sorted(
            report.uncovered_by_kind.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"  {kind}: {number}")
        lines.append("")
        lines.append("Примеры:")
        lines.extend(f"  {example}" for example in report.uncovered_examples[:10])
        lines.append("")
    if report.anchors_without_entry_point:
        lines.append("Якоря, которым не нашлось точки входа (это уже находка, а не работа):")
        lines.extend(f"  {item}" for item in report.anchors_without_entry_point)
        lines.append("")
    lines.append(
        "Непокрытые точки входа — состояние работы, а не дефект: отчёт печатается "
        "всегда и кода возврата не меняет, пока порог не задан явно."
    )
    lines.append("")
    return "\n".join(lines)
