"""Фронт в общем графе: страницы, цепочка и шов (G15).

Главный вопрос вехи — «как связана страница с сервисом .NET» — обязан
отвечаться **одним путём**, где шов виден звеном, а не схлопнут в прямое
ребро «страница → эндпоинт».
"""

from pathlib import Path

from docpipe.graph import GraphIndex, compute, path
from docpipe.graph.entrypoints import entry_key
from docpipe.graph.web import collect
from docpipe.model import (
    DocNode,
    Manifest,
    Member,
    ParserVersions,
    RouteEntry,
    SourceSpan,
    Symbol,
    Usage,
    WebCall,
)
from docpipe.route import RouteKey


def web_symbol(name: str, path_: str, members: tuple[str, ...] = ()) -> Symbol:
    return Symbol(
        fqn=f"{path_.removesuffix('.ts')}.{name}",
        name=name,
        type_kind="class",
        namespace="",
        module="tr-p",
        members=[
            Member(name=item, kind="method", signature=f"{item}()", line=1, end_line=2)
            for item in members
        ],
        sources=[SourceSpan(path=path_, start=1, end=20)],
    )


def web_node(
    symbol: Symbol,
    kind: str,
    *,
    routes: tuple[RouteEntry, ...] = (),
    uses: tuple[Usage, ...] = (),
    calls: tuple[WebCall, ...] = (),
) -> DocNode:
    return DocNode(
        id=f"type:src#{symbol.fqn}",
        kind=kind,
        template="page" if kind == "page" else "service",
        title=symbol.name,
        doc_path=f"docs/{symbol.name}.md",
        module="tr-p",
        domain="tr-p",
        symbol=symbol,
        signature_hash="sha256:0",
        routes=list(routes),
        uses=list(uses),
        web_calls=list(calls),
    )


def web_manifest(*nodes: DocNode) -> Manifest:
    return Manifest(
        schema_version="2.0",
        ruleset_version="тест",
        parser=ParserVersions(tree_sitter="0.0"),
        nodes=list(nodes),
    )


def chain_manifest() -> Manifest:
    component = web_symbol("ListComponent", "src/app/list.component.ts", ("save",))
    service = web_symbol("DebtService", "src/app/debt.service.ts", ("insert",))
    return web_manifest(
        web_node(
            component,
            "page",
            routes=(
                RouteEntry(
                    component=component.fqn,
                    path="models",
                    source="src/app/routes.ts",
                    table="routes",
                ),
            ),
            uses=(Usage(target=service.fqn, member="insert", via="save", action="[Debt] Save"),),
        ),
        web_node(
            service,
            "api-service",
            calls=(
                WebCall(
                    file="src/app/debt.service.ts",
                    line=10,
                    key=RouteKey(http_method="POST", route="api/debts/insert"),
                    confidence="high",
                    member="insert",
                ),
            ),
        ),
    )


ENDPOINT = entry_key("http_endpoint", "POST api/debts/insert")


# ──────────────────────────────────────────────────────────────────────────────
# Страница как точка входа
# ──────────────────────────────────────────────────────────────────────────────


def test_page_is_an_entry_point_anchored_on_the_route() -> None:
    """Якорь страницы — маршрут, а не имя класса: переименование компонента
    якорь не меняет, а URL знает пользователь."""
    nodes, edges, report = collect(chain_manifest(), {ENDPOINT})
    pages = [node for node in nodes if node.kind == "entry_point"]
    assert [node.key for node in pages] == [entry_key("page", "models")]
    assert pages[0].attributes["route"] == "models"
    assert report.pages == 1


def test_page_without_a_route_is_counted_not_invented() -> None:
    """Маршрут не собрался — страница существует, но точкой входа стать
    не может: якоря нет, и выдумывать его нельзя."""
    component = web_symbol("Broken", "src/app/broken.component.ts")
    manifest = web_manifest(
        web_node(
            component,
            "page",
            routes=(
                RouteEntry(
                    component=component.fqn,
                    path="",
                    source="src/app/routes.ts",
                    table="routes",
                    route_unresolved=True,
                ),
            ),
        )
    )
    nodes, _, report = collect(manifest, set())
    assert report.pages_without_route == 1
    assert not [node for node in nodes if node.kind == "entry_point"]


# ──────────────────────────────────────────────────────────────────────────────
# Цепочка и шов
# ──────────────────────────────────────────────────────────────────────────────


def test_chain_is_edges_not_a_collapsed_link() -> None:
    """Вопрос «почему страница зовёт этот эндпоинт» отвечается путём.
    Схлопнув цепочку, инструмент отвечал бы «потому что» — и ничего больше."""
    _, edges, report = collect(chain_manifest(), {ENDPOINT})
    chain = [edge for edge in edges if edge.kind == "calls"]
    assert len(chain) == 1
    assert chain[0].source.endswith("ListComponent.save")
    assert chain[0].target.endswith("DebtService.insert")
    # Тип экшена живёт на ребре: тот же метод сервиса зовут и обработчик,
    # и компонент напрямую.
    assert chain[0].attributes["action"] == "[Debt] Save"
    assert report.chain_edges == 1


def test_seam_connects_the_calling_member_to_the_endpoint() -> None:
    _, edges, report = collect(chain_manifest(), {ENDPOINT})
    seam = [edge for edge in edges if edge.kind == "crosses"]
    assert len(seam) == 1
    assert seam[0].source.endswith("DebtService.insert")
    assert seam[0].target == ENDPOINT
    assert report.seams == 1


def test_call_without_an_endpoint_is_a_named_number() -> None:
    """Несведённый маршрут идёт в отчёт: молча выброшенный вызов через месяц
    неотличим от вызова, которого не было."""
    _, edges, report = collect(chain_manifest(), set())
    assert not [edge for edge in edges if edge.kind == "crosses"]
    assert report.unmatched_calls == 1
    assert report.examples["вызов фронта без эндпоинта"]


def test_discriminator_is_on_the_edge_not_in_the_match() -> None:
    """Различитель («один маршрут на много смыслов») входит в ключ вызова,
    но не участвует в сопоставлении с эндпоинтом."""
    service = web_symbol("ItemsService", "src/app/items.service.ts", ("models",))
    manifest = web_manifest(
        web_node(
            service,
            "api-service",
            calls=(
                WebCall(
                    file="src/app/items.service.ts",
                    line=5,
                    key=RouteKey(
                        http_method="POST", route="api/items/query", discriminator="Models"
                    ),
                    confidence="high",
                    member="models",
                ),
            ),
        )
    )
    endpoint = entry_key("http_endpoint", "POST api/items/query")
    _, edges, _ = collect(manifest, {endpoint})
    seam = [edge for edge in edges if edge.kind == "crosses"]
    assert seam and seam[0].target == endpoint
    assert seam[0].attributes["discriminator"] == "Models"


# ──────────────────────────────────────────────────────────────────────────────
# Веха 4: страница и сервис .NET в одном пути
# ──────────────────────────────────────────────────────────────────────────────


def test_path_from_page_to_backend_shows_the_seam_as_a_link() -> None:
    from docpipe.graph.model import GraphEdge, GraphNode

    nodes, edges, _ = collect(chain_manifest(), {ENDPOINT})
    backend = (
        GraphNode(key=ENDPOINT, kind="entry_point", name="POST api/debts/insert"),
        GraphNode(
            key="src/Api/Controller.cs#Controller",
            kind="type",
            name="Controller",
            file="src/Api/Controller.cs",
        ),
        GraphNode(
            key="src/Api/Controller.cs#Controller.Insert",
            kind="member",
            name="Insert",
            owner="Controller",
            file="src/Api/Controller.cs",
        ),
    )
    index = GraphIndex(
        nodes=tuple(nodes) + backend,
        edges=tuple(edges)
        + (
            GraphEdge(
                kind="dispatches",
                source=ENDPOINT,
                target="src/Api/Controller.cs#Controller.Insert",
                via="entrypoint:manifest",
            ),
        ),
    )
    steps = path(index, entry_key("page", "models"), "src/Api/Controller.cs#Controller.Insert")
    kinds = [step.kind for step in steps]
    assert "crosses" in kinds, kinds
    assert steps[-1].target == "src/Api/Controller.cs#Controller.Insert"

    # И то же самое читается из достижимости, без обхода.
    reachability = compute(index)
    assert "src/Api/Controller.cs#Controller.Insert" in reachability.reached_by(
        entry_key("page", "models")
    )


def test_web_module_has_no_parser_vocabulary() -> None:
    text = Path("docpipe/graph/web.py").read_text(encoding="utf-8").lower()
    assert "codebase-memory" not in text
    assert "cypher" not in text
