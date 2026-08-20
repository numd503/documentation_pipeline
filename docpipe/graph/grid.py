"""Грид-сервисы как шов .NET ↔ .NET (G06).

Вызов через кластер устроен иначе, чем обычный: сервис регистрируется под
именем, а зовётся через прокси по этому имени. Для графа это **шов**, а не
данные и не обычный вызов: между вызывающим и реализацией нет ни одного
ребра, которое видел бы разбор, — есть имя, которое обе стороны обязаны знать.

**Что делает разбор и что остаётся нам.** Вызов `GetServiceProxy<IFoo>().Bar()`
разбор видит как обращение к члену интерфейса `IFoo.Bar` — интерфейс лежит
в репозитории, и до него цепочка доходит. Дальше он останавливается: связи
«интерфейс → зарегистрированная в кластере реализация» в коде не существует
структурно, она объявлена реестром. Эту связь и делаем мы.

Отсюда же следует, чего здесь **нет**: имя сервиса, собранное из кусков,
не разрешается — такой вызов идёт в отчёт несведённых швов, а не теряется.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.keys import normalize_identifier

VIA_PROXY: Final[str] = "grid:proxy"


@dataclass(frozen=True)
class GridReport:
    services: int = 0
    linked: int = 0
    seams: int = 0
    without_contract: int = 0
    examples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_counts(self) -> dict[str, int]:
        return {
            "грид-сервисов объявлено": self.services,
            "грид-сервисов связано с кодом": self.linked,
            "швов через кластер": self.seams,
            "грид-сервисов без контракта в коде": self.without_contract,
        }


def seams(
    nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
) -> tuple[list[GraphEdge], GridReport]:
    """Рёбра `crosses` от вызывающего члена к члену реализации.

    Контракт ищется двумя способами, и оба нужны: **по имени сервиса**
    (в реестре сервис часто назван именем интерфейса) и **по наследованию**
    реализации. Первый работает там, где интерфейс есть, второй — там,
    где имя сервиса произвольно (`CalcDxExcel` при классе
    `DXExcelCalcService`), а контракт виден по тому, что реализация
    наследует.
    """
    by_key = {node.key: node for node in nodes}
    types_by_name: dict[str, list[str]] = {}
    members: dict[tuple[str, str], str] = {}
    for node in nodes:
        if node.kind == "type":
            types_by_name.setdefault(normalize_identifier(node.name), []).append(node.key)
        elif node.kind == "member":
            owner = f"{node.file}#{node.owner}" if node.file else node.owner
            members[(owner, normalize_identifier(node.name))] = node.key

    inherits: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind == "inherits":
            inherits.setdefault(edge.source, []).append(edge.target)

    dispatched: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind == "dispatches":
            dispatched.setdefault(edge.source, []).append(edge.target)

    produced: list[GraphEdge] = []
    existing = {(edge.kind, edge.source, edge.target) for edge in edges}
    services = linked = built = without_contract = 0
    missing: list[str] = []

    for node in nodes:
        if node.kind != "entry_point" or node.attributes.get("entry_kind") != "grid_service":
            continue
        services += 1

        implementations = [
            key for key in dispatched.get(node.key, []) if by_key.get(key, node).kind == "type"
        ]
        if not implementations:
            missing.append(f"{node.name}: узла кода нет")
            continue
        linked += 1

        # Контракт: тип с именем сервиса либо интерфейсы, которые наследует
        # реализация. Оба множества складываются — реестр называет сервис
        # как ему удобно, а наследование говорит о том же по-другому.
        contracts: set[str] = set()
        ref = node.attributes.get("ref") or node.name
        contracts.update(types_by_name.get(normalize_identifier(ref), []))
        for implementation in implementations:
            contracts.update(inherits.get(implementation, []))
        contracts.difference_update(implementations)

        if not contracts:
            without_contract += 1
            missing.append(f"{node.name}: контракт в коде не найден")
            continue

        for edge in edges:
            if edge.kind != "calls":
                continue
            target = by_key.get(edge.target)
            if target is None or target.kind != "member":
                continue
            owner = f"{target.file}#{target.owner}" if target.file else target.owner
            if owner not in contracts:
                continue
            for implementation in implementations:
                member = members.get((implementation, normalize_identifier(target.name)))
                identity = ("crosses", edge.source, member or "")
                if member is None or identity in existing:
                    continue
                existing.add(identity)
                produced.append(
                    GraphEdge(
                        kind="crosses",
                        source=edge.source,
                        target=member,
                        via=VIA_PROXY,
                        confidence=round(1 / len(implementations), 3),
                        attributes={"service": ref},
                    )
                )
                built += 1

    report = GridReport(
        services=services,
        linked=linked,
        seams=built,
        without_contract=without_contract,
        examples={"грид-сервисы без пары": tuple(sorted(missing)[:10])},
    )
    return sorted(produced, key=lambda edge: (edge.source, edge.target)), report
