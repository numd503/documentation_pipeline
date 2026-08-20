"""Фронт в общем графе: страницы, цепочка и шов с бэкендом (G15).

Разбор фронта — наш, и это решение, а не инерция: цепочка NGXS
(«компонент диспатчит → стейт обрабатывает → сервис зовёт»), спред
импортированных массивов роутов и различитель «один маршрут на много
смыслов» уже реализованы и проверены на боевом модуле, а сторонний разбор
этих конструкций не знает. Поэтому его рёбра по TypeScript в индекс
не проецируются вовсе (правило модели графа), а узлы и рёбра фронта
приходят отсюда.

Три вещи, которые здесь делаются и каждая из которых ломается молча:

- **страница — точка входа графа.** Её якорь — маршрут, а не имя класса:
  переименование компонента якорь не меняет, а URL — это то, что знает
  пользователь;
- **цепочка лежит рёбрами, а не схлопнута.** Вопрос «почему страница зовёт
  этот эндпоинт» отвечается путём, как на бэке. Схлопнув цепочку в одно
  ребро «страница → эндпоинт», мы бы отвечали «потому что» — и ничего больше;
- **шов сводится по ключу маршрута**, тому же самому, что у эндпоинта .NET.
  Различитель («один маршрут на много смыслов») входит в ключ вызова,
  но **не** участвует в сопоставлении с эндпоинтом: правило переносится
  из `web link` без изменений.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.graph.entrypoints import entry_key
from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.keys import normalize_route_reference
from docpipe.model import Manifest, Symbol

VIA_CHAIN: Final[str] = "web:usage"
VIA_SEAM: Final[str] = "web:route"


@dataclass(frozen=True)
class WebReport:
    pages: int = 0
    chain_edges: int = 0
    seams: int = 0
    unmatched_calls: int = 0
    pages_without_route: int = 0
    # Совпавшие ключи: один маршрут у двух компонентов, два члена с одним
    # именем. Законно и там и там, но знать об этом надо.
    shared_keys: int = 0
    examples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_counts(self) -> dict[str, int]:
        return {
            "страниц фронта": self.pages,
            "рёбер цепочки фронта": self.chain_edges,
            "швов фронт → бэкенд": self.seams,
            "вызовов фронта без эндпоинта": self.unmatched_calls,
            "страниц без собранного маршрута": self.pages_without_route,
            "узлов фронта с совпавшим ключом": self.shared_keys,
        }


def _node_key(symbol: Symbol) -> str:
    source = symbol.sources[0].path if symbol.sources else ""
    return f"{source}#{symbol.name}" if source else symbol.name


def collect(
    web: Manifest, backend_entry_points: set[str]
) -> tuple[list[GraphNode], list[GraphEdge], WebReport]:
    """Узлы и рёбра фронта плюс швы с бэкендом."""
    # Узлы собираются в словарь по ключу, а не в список. Причина измерена
    # на открытом репозитории: один маршрут бывает у двух компонентов
    # (макет и содержимое), а у класса бывают два члена с одним именем
    # (поле и геттер). И то и другое законно, и падать на этом индекс
    # не должен — как не должен и молча терять второй узел.
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    by_fqn: dict[str, str] = {}
    duplicates = 0
    pages = 0
    without_route = 0
    unmatched: list[str] = []
    seams = 0
    chain = 0

    for document in web.nodes:
        symbol = document.symbol
        if symbol is None:
            continue
        key = _node_key(symbol)
        by_fqn[symbol.fqn] = key
        file = symbol.sources[0].path if symbol.sources else ""
        nodes[key] = GraphNode(
            key=key,
            kind="type",
            name=symbol.name,
            file=file,
            lang="typescript",
            source="web",
            attributes={
                "web_kind": document.kind,
                "module": document.module,
                "doc_node": document.id,
                "fqn": symbol.fqn,
            },
        )
        for item in symbol.members:
            member_key = f"{key}.{item.name}"
            if member_key in nodes:
                duplicates += 1
                continue
            nodes[member_key] = GraphNode(
                key=member_key,
                kind="member",
                name=item.name,
                owner=symbol.name,
                file=file,
                lang="typescript",
                source="web",
                attributes={"module": document.module},
            )

    for document in web.nodes:
        symbol = document.symbol
        if symbol is None:
            continue
        key = by_fqn[symbol.fqn]

        # Страница: якорь — маршрут. Маршрут не собрался — страница всё равно
        # существует, но точкой входа стать не может: якоря нет.
        if document.kind == "page":
            routes = [route for route in document.routes if not route.route_unresolved]
            if not routes:
                without_route += 1
            for route in routes:
                page_key = entry_key("page", route.path)
                if page_key not in nodes:
                    pages += 1
                    nodes[page_key] = GraphNode(
                        key=page_key,
                        kind="entry_point",
                        name=f"{document.title} ({route.path})",
                        file=symbol.sources[0].path if symbol.sources else "",
                        source="web",
                        attributes={
                            "entry_kind": "page",
                            "route": route.path,
                            "table": route.table,
                            "source_record": route.source,
                            "doc_node": document.id,
                            "module": document.module,
                        },
                    )
                else:
                    # Один маршрут на два компонента: макет и содержимое.
                    # Узел остаётся один — маршрут и есть якорь, — а ребро
                    # диспетчеризации добавляется ко второму компоненту тоже.
                    duplicates += 1
                edges.append(
                    GraphEdge(
                        kind="dispatches",
                        source=page_key,
                        target=key,
                        via="entrypoint:web",
                    )
                )

        # Цепочка «компонент → экшен → стейт → сервис»: ребро между членами,
        # тип экшена — на ребре, а не на вызове.
        for use in document.uses:
            target = by_fqn.get(use.target)
            if target is None:
                continue
            source_key = f"{key}.{use.via}" if use.via else key
            edges.append(
                GraphEdge(
                    kind="calls",
                    source=source_key,
                    target=f"{target}.{use.member}",
                    via=VIA_CHAIN,
                    attributes={"action": use.action} if use.action else {},
                )
            )
            chain += 1

        # Шов: ключ маршрута тот же, что у эндпоинта .NET. Различитель
        # в сопоставлении не участвует — он различает смысл вызова,
        # а не эндпоинт.
        for call in document.web_calls:
            endpoint = entry_key(
                "http_endpoint", normalize_route_reference(call.key.http_method, call.key.route)
            )
            caller = f"{key}.{call.member}" if call.member else key
            if endpoint in backend_entry_points:
                edges.append(
                    GraphEdge(
                        kind="crosses",
                        source=caller,
                        target=endpoint,
                        via=VIA_SEAM,
                        attributes=(
                            {"discriminator": call.key.discriminator}
                            if call.key.discriminator
                            else {}
                        ),
                    )
                )
                seams += 1
            else:
                unmatched.append(f"{call.key.http_method} {call.key.route} ← {call.file}")

    known = set(nodes)
    edges = [edge for edge in edges if edge.source in known or edge.kind == "crosses"]
    report = WebReport(
        pages=pages,
        chain_edges=chain,
        seams=seams,
        unmatched_calls=len(unmatched),
        pages_without_route=without_route,
        shared_keys=duplicates,
        examples={"вызов фронта без эндпоинта": tuple(sorted(unmatched)[:10])},
    )
    return (
        sorted(nodes.values(), key=lambda node: node.key),
        sorted(edges, key=lambda edge: (edge.kind, edge.source, edge.target)),
        report,
    )
