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
from typing import Final

from docpipe.arch.adapters.declared import DEFAULT_KINDS
from docpipe.business.model import Anchor, Catalog

# Мост между словарями: аналитик пишет `table` и `kafka`, реестр объявляет
# `list` и `kafka_topic`. Пара, которой здесь нет, просто никогда
# не разрешится — и это будет выглядеть как «инструмент не нашёл».
from docpipe.business.resolve import REGISTRY_KIND
from docpipe.graph.model import GraphNode
from docpipe.keys import normalize_identifier
from docpipe.route import normalize_route

# Якоря, которые адресуют точку входа маршрутом, и вид корня графа под ними.
# Сравниваются они маршрутом, а не именем корня: имя у страницы —
# «Заголовок (маршрут)», у эндпоинта — «GET маршрут», и с якорем оно
# не совпадёт никогда. Метод в якорь `http` не входит (`entry-guide.md`):
# документ о маршруте описывает и GET, и PUT на нём.
ROUTE_ANCHORS: Final[dict[str, str]] = {"page": "page", "http": "http_endpoint"}

# Виды якорей, у которых в графе по умолчанию есть точки входа. Выводится
# из умолчаний адаптера реестров, а не пишется руками: третий словарь про то же
# самое разошёлся бы с ними на первой новой паре. Таблица в графе — узел
# данных, топик — шов; якорь такого вида граф проверить не может, и назвать
# его «без точки входа» значило бы объявить находкой собственную слепоту.
CHECKED_KINDS: Final[frozenset[str]] = frozenset(
    {
        registry
        for registry in REGISTRY_KIND.values()
        if DEFAULT_KINDS.get(registry, "").startswith("entry_point:")
    }
    | set(ROUTE_ANCHORS)
)


@dataclass(frozen=True)
class CoverageReport:
    entry_points: int = 0
    covered: int = 0
    uncovered_by_kind: dict[str, int] = field(default_factory=dict)
    documents: int = 0
    anchors: int = 0
    anchors_without_entry_point: tuple[str, ...] = ()
    uncovered_examples: tuple[str, ...] = ()
    # Якоря видов, точек входа которых в графе нет: раздел фронта, тип,
    # таблица, топик. Не находка и не покрытие — их проверяет `business lint`.
    anchors_unchecked: tuple[str, ...] = ()
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
            "якорей вне графа": len(self.anchors_unchecked),
            "точек входа со спецификацией": self.with_spec,
            "спецификаций названо": len(self.specs),
        }
        for kind, number in sorted(self.uncovered_by_kind.items()):
            counts[f"не описано, вид {kind}"] = number
        return counts


def _entry_identity(node: GraphNode) -> tuple[str, str]:
    """Пара «вид + ключ», по которой корень графа сходится с якорем каталога."""
    entry_kind = node.attributes.get("entry_kind", "")
    for anchor_kind, root_kind in ROUTE_ANCHORS.items():
        if entry_kind == root_kind:
            return anchor_kind, normalize_route(node.attributes.get("route", ""))
    kind = node.attributes.get("registry_kind") or entry_kind
    ref = node.attributes.get("ref") or node.name
    return kind, normalize_identifier(ref)


def _anchor_identity(anchor: Anchor) -> tuple[str, str]:
    """Та же пара со стороны документа: вид через мост словарей, ключ — по виду."""
    if anchor.kind in ROUTE_ANCHORS:
        return anchor.kind, normalize_route(anchor.ref)
    return REGISTRY_KIND.get(anchor.kind, anchor.kind), normalize_identifier(anchor.ref)


def coverage(nodes: tuple[GraphNode, ...], catalog: Catalog) -> CoverageReport:
    """Сопоставить корни графа с якорями бизнес-каталога."""
    roots = [node for node in nodes if node.kind == "entry_point"]
    # Список, а не один корень: на маршруте бывает несколько методов,
    # и якорь `http` покрывает их все.
    by_identity: dict[tuple[str, str], list[GraphNode]] = {}
    for node in roots:
        by_identity.setdefault(_entry_identity(node), []).append(node)
    # Вид проверяем, если граф держит его точками входа по умолчанию или если
    # корни такого вида есть: адаптер реестров настраивается, и топик,
    # объявленный точкой входа, проверяется как точка входа.
    checked = CHECKED_KINDS | {kind for kind, _ in by_identity}

    covered: set[str] = set()
    anchors = 0
    dangling: list[str] = []
    unchecked: list[str] = []

    for document in catalog.docs:
        for anchor in document.anchors:
            anchors += 1
            identity = _anchor_identity(anchor)
            found = by_identity.get(identity, [])
            if not found:
                if identity[0] not in checked:
                    unchecked.append(f"{document.id}: {anchor.kind} {anchor.ref}")
                # Якорь может быть законно неразрешим: процесс начинается
                # в чужой команде, и такой якорь помечен `verify: false`.
                elif anchor.verify:
                    dangling.append(f"{document.id}: {anchor.kind} {anchor.ref}")
                continue
            covered.update(node.key for node in found)

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
        anchors_unchecked=tuple(sorted(unchecked)),
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
    if report.anchors_unchecked:
        lines.append(
            f"Якорей, которые граф не проверяет: {len(report.anchors_unchecked)} — "
            "таких точек входа в графе нет (разделы, типы, таблицы, топики); "
            "их проверяет `business lint`:"
        )
        lines.extend(f"  {item}" for item in report.anchors_unchecked[:10])
        if len(report.anchors_unchecked) > 10:
            lines.append(f"  … и ещё {len(report.anchors_unchecked) - 10}")
        lines.append("")
    lines.append(
        "Непокрытые точки входа — состояние работы, а не дефект: отчёт печатается "
        "всегда и кода возврата не меняет, пока порог не задан явно."
    )
    lines.append("")
    return "\n".join(lines)
