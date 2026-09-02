"""Точки входа как объекты графа (G03).

Корень графа — первоклассная сущность, а не артефакт бизнес-слоя. Ключ —
то, что обязан знать вызывающий: идентификатор workflow, имя джоба, имя
грид-сервиса, маршрут. Бизнес-название — необязательная метка сверху,
и точка входа существует независимо от того, есть ли она.

**Два источника, и смешивать их в одном обходе нельзя.**

- **реестр (R03)** даёт то, чего в коде не существует структурно: workflow,
  джобы, обработчики событий. Литерала конкретного workflow в C# нет вовсе,
  и связь «имя в реестре → класс» делаем мы, и только мы;
- **манифест (L1)** даёт то, что видно в коде: HTTP-эндпоинты контроллеров.

Если источники слить, отсутствие реестра станет неотличимо от «точек входа
нет» — а это два совершенно разных состояния настройки.

**Корень без узла кода — состояние работы, а не дефект.** Он попадает
в отчёт и не роняет прогон: правило перенесено из бизнес-слоя без изменений.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.arch.model import ArchRegistry, EntryPointRecord
from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.keys import normalize_identifier, normalize_route_reference
from docpipe.model import Manifest

# Как связан корень с кодом. Три состояния, и различать их обязательно:
# «связан точно» и «связан по короткому имени» — разные утверждения,
# а «не связан» — не дефект.
LINK_EXACT: Final[str] = "по полному имени"
LINK_SHORT: Final[str] = "по короткому имени типа"
LINK_NONE: Final[str] = "узел кода не найден"


@dataclass(frozen=True)
class EntryPointReport:
    from_registry: int = 0
    from_manifest: int = 0
    linked: dict[str, int] = field(default_factory=dict)
    unlinked_examples: tuple[str, ...] = ()
    # Отсутствие декларативных источников — состояние, которое обязано быть
    # названо вслух: пустой вывод здесь запрещён так же, как в поиске (Р7).
    registry_present: bool = False

    def as_counts(self) -> dict[str, int]:
        counts = {
            "корней из реестра": self.from_registry,
            "корней из манифеста": self.from_manifest,
        }
        for state, number in sorted(self.linked.items()):
            counts[f"корней связано: {state}"] = number
        return counts


def entry_key(entry_kind: str, key: str) -> str:
    """Ключ узла точки входа.

    Отдельное пространство от узлов кода: `entry:job:ночная-переиндексация`
    и класс, который её выполняет, — разные узлы, и путать их нельзя.
    Один и тот же класс обслуживает несколько корней, и наоборот.
    """
    return f"entry:{entry_kind}:{normalize_identifier(key)}"


def from_registry(registry: ArchRegistry) -> list[GraphNode]:
    """Корни, объявленные декларативно."""
    nodes: list[GraphNode] = []
    for record in registry.records:
        if not isinstance(record, EntryPointRecord):
            continue
        attributes = {
            "entry_kind": record.entry_kind,
            "source_file": record.source.file,
            "source_record": record.source.record,
            "provenance": record.provenance,
            **record.attributes,
        }
        if record.impl:
            attributes["impl"] = ", ".join(record.impl)
        if record.touches:
            attributes["touches"] = ", ".join(record.touches)
        nodes.append(
            GraphNode(
                key=entry_key(record.entry_kind, record.key),
                kind="entry_point",
                name=record.name or record.key,
                file=record.source.file,
                source="registry",
                attributes={name: value for name, value in attributes.items() if value},
            )
        )
    return sorted(nodes, key=lambda node: node.key)


def from_manifest(manifest: Manifest) -> list[GraphNode]:
    """Корни, видные в коде: HTTP-эндпоинты контроллеров.

    Ключ собирается тем же правилом, что и ключ маршрута в реестре: метод
    и нормализованный путь. Отдельная нормализация здесь разошлась бы
    с реестровой, и половина эндпоинтов не сошлась бы со швами.
    """
    nodes: dict[str, GraphNode] = {}
    for node in manifest.nodes:
        for endpoint in node.endpoints:
            key = entry_key(
                "http_endpoint", normalize_route_reference(endpoint.http_method, endpoint.route)
            )
            if key in nodes:
                continue
            nodes[key] = GraphNode(
                key=key,
                kind="entry_point",
                name=f"{endpoint.http_method} {endpoint.route}".strip(),
                file=node.doc_path,
                source="manifest",
                attributes={
                    "entry_kind": "http_endpoint",
                    "http_method": endpoint.http_method,
                    "route": endpoint.route,
                    "doc_node": node.id,
                    "member": endpoint.member,
                    "module": node.module,
                },
            )
    return sorted(nodes.values(), key=lambda item: item.key)


def link(
    entries: list[GraphNode], code: tuple[GraphNode, ...], manifest: Manifest | None
) -> tuple[list[GraphEdge], EntryPointReport]:
    """Связать корни с узлами кода.

    Ребро `dispatches`: диспетчеризация по данным — реестр называет класс,
    универсальный раннер его запускает. Это не вызов: вызывающего в коде нет
    вовсе, и записывать такую связь как `calls` значило бы утверждать то,
    чего нет.
    """
    by_fqn: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    for node in code:
        if node.kind != "type":
            continue
        fqn = node.attributes.get("fqn")
        if fqn:
            by_fqn.setdefault(normalize_identifier(fqn), node.key)
        by_name.setdefault(normalize_identifier(node.name), []).append(node.key)

    members_by_owner: dict[tuple[str, str], str] = {}
    for node in code:
        if node.kind == "member":
            owner = node.owner.rsplit(".", 1)[-1]
            members_by_owner.setdefault(
                (normalize_identifier(owner), normalize_identifier(node.name)), node.key
            )

    edges: list[GraphEdge] = []
    states: dict[str, int] = {}
    unlinked: list[str] = []

    for entry in entries:
        targets: list[tuple[str, str, float]] = []
        impl = entry.attributes.get("impl", "")
        for name in [part.strip() for part in impl.split(",") if part.strip()]:
            exact = by_fqn.get(normalize_identifier(name))
            if exact:
                targets.append((exact, LINK_EXACT, 1.0))
                continue
            short = normalize_identifier(name.rsplit(".", 1)[-1])
            candidates = by_name.get(short, [])
            # Несколько типов с одним коротким именем — не повод выбрать
            # первого: уверенность падает, и число видно в отчёте.
            for candidate in candidates:
                targets.append((candidate, LINK_SHORT, round(1 / len(candidates), 3)))

        if not targets and entry.attributes.get("member") and manifest is not None:
            owner = _owner_of(entry.attributes.get("doc_node", ""), manifest)
            member = entry.attributes["member"]
            found = members_by_owner.get(
                (normalize_identifier(owner), normalize_identifier(member))
            )
            if found:
                targets.append((found, LINK_EXACT, 1.0))

        if not targets:
            states[LINK_NONE] = states.get(LINK_NONE, 0) + 1
            unlinked.append(entry.key)
            continue

        for target, state, confidence in targets:
            states[state] = states.get(state, 0) + 1
            edges.append(
                GraphEdge(
                    kind="dispatches",
                    source=entry.key,
                    target=target,
                    via=f"entrypoint:{entry.source}",
                    confidence=confidence,
                )
            )

    report = EntryPointReport(
        from_registry=sum(1 for entry in entries if entry.source == "registry"),
        from_manifest=sum(1 for entry in entries if entry.source == "manifest"),
        linked=states,
        unlinked_examples=tuple(sorted(unlinked)[:10]),
        registry_present=any(entry.source == "registry" for entry in entries),
    )
    return sorted(edges, key=lambda edge: (edge.source, edge.target)), report


def _owner_of(doc_node: str, manifest: Manifest) -> str:
    for node in manifest.nodes:
        if node.id == doc_node and node.symbol is not None:
            return node.symbol.name
    return ""
