"""Объявленные швы в графе (G16 п. 4, Р2).

Между языками вызовов нет — есть сообщение по литералу. Литерал приходит
из реестра, а не из разбора: сторона, которая его пишет, часто собирает
строку из кусков, и статический поиск даёт ноль.

До этих тестов `SeamRecord` был форматом, который никто не читает: записи
объявлялись, проверялись и печатались, а в графе не появлялось ничего.
"""

from docpipe.arch.model import ArchRegistry, SeamRecord, Source
from docpipe.graph.entrypoints import entry_key
from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.graph.seams import declared


def seam(**fields: object) -> SeamRecord:
    base: dict[str, object] = {
        "key": "api/models/{id}/forecast",
        "seam_kind": "http_route",
        "http_method": "GET",
        "literal": "/api/models/{id}/forecast",
        "source": Source(file="svc/forecast.py", record="ForecastClient.load"),
        "provenance": "manual",
    }
    base.update(fields)
    return SeamRecord.model_validate(base)


def registry(*records: SeamRecord) -> ArchRegistry:
    return ArchRegistry(records=list(records))


def endpoint(method: str = "GET", route: str = "api/models/{}/forecast") -> GraphNode:
    return GraphNode(
        key=entry_key("http_endpoint", f"{method} {route}"),
        kind="entry_point",
        name=f"{method} {route}",
        source="manifest",
        attributes={"entry_kind": "http_endpoint"},
    )


PY_NODES = (
    GraphNode(
        key="svc/forecast.py#ForecastClient",
        kind="type",
        name="ForecastClient",
        file="svc/forecast.py",
        lang="python",
    ),
    GraphNode(
        key="svc/forecast.py#ForecastClient.load",
        kind="member",
        name="load",
        owner="ForecastClient",
        file="svc/forecast.py",
        lang="python",
    ),
)


def test_declared_seam_joins_two_languages() -> None:
    """Питон дотягивается до эндпоинта .NET через объявленный литерал."""
    nodes = PY_NODES + (endpoint(),)
    made_nodes, made_edges, report = declared(registry(seam()), nodes)

    assert [node.kind for node in made_nodes] == ["seam"]
    key = made_nodes[0].key
    assert key == "seam:http_route:api/models/{}/forecast"
    assert ("svc/forecast.py#ForecastClient.load", key) in [
        (edge.source, edge.target) for edge in made_edges
    ]
    assert (key, endpoint().key) in [(edge.source, edge.target) for edge in made_edges]
    assert report.both_sides == 1


def test_route_forms_normalize_to_one_seam() -> None:
    """`{id}`, `:id` и `${id}` — один и тот же маршрут.

    Иначе стороны не сойдутся ровно там, где они и должны сойтись:
    подстановку каждая сторона пишет по-своему.
    """
    nodes = PY_NODES + (endpoint(),)
    for literal in (
        "/api/models/{id}/forecast",
        "/api/models/:id/forecast",
        "api/models/${id}/forecast",
    ):
        _, _, report = declared(registry(seam(literal=literal)), nodes)
        assert report.both_sides == 1, literal


def test_http_method_is_part_of_the_match() -> None:
    """`GET api/x` и `POST api/x` — разные точки входа."""
    nodes = PY_NODES + (endpoint(method="POST"),)
    _, _, report = declared(registry(seam(http_method="GET")), nodes)
    assert report.both_sides == 0
    assert report.calling_only == 1

    _, _, report = declared(registry(seam(http_method="")), nodes)
    assert report.both_sides == 1, "без глагола сходятся все глаголы маршрута"


def test_symbol_in_the_source_gives_member_granularity() -> None:
    """Что записал человек, то и становится зовущей стороной."""
    nodes = PY_NODES + (endpoint(),)
    _, edges, _ = declared(registry(seam()), nodes)
    calling = [edge.source for edge in edges if edge.target.startswith("seam:")]
    assert calling == ["svc/forecast.py#ForecastClient.load"]


def test_without_a_symbol_the_type_calls_not_every_member() -> None:
    """Пустой `record` — файл целиком, и зовёт **тип**.

    Ребро от каждого члена файла было бы уверенным неверным ребром
    на ровном месте: литерал написан в одном из них.
    """
    nodes = PY_NODES + (endpoint(),)
    _, edges, _ = declared(
        registry(seam(source=Source(file="svc/forecast.py"))),
        nodes,
    )
    calling = [edge.source for edge in edges if edge.target.startswith("seam:")]
    assert calling == ["svc/forecast.py#ForecastClient"]


def test_one_sided_seam_is_counted_not_dropped() -> None:
    """Шов, у которого в графе только одна сторона, — состояние работы.

    Питона может не быть в индексе вовсе (движок не дошёл, файл отсеян),
    и молча потерять объявленный шов нельзя: он тогда неотличим от «шва нет».
    """
    _, edges, report = declared(registry(seam()), (endpoint(),))
    assert report.answering_only == 1
    assert [edge.source for edge in edges] == ["seam:http_route:api/models/{}/forecast"]

    _, _, report = declared(registry(seam()), PY_NODES)
    assert report.calling_only == 1

    _, _, report = declared(registry(seam()), ())
    assert report.dangling == 1


def test_literal_recorded_on_the_answering_side_does_not_call_itself() -> None:
    """Человек увидел маршрут в контроллере, а не в клиенте.

    Без проверки вышло бы ребро «контроллер зовёт собственный эндпоинт» —
    уверенное неверное ребро из ничего.
    """
    controller = GraphNode(
        key="api/C.cs#ModelsController",
        kind="type",
        name="ModelsController",
        file="api/C.cs",
    )
    member = GraphNode(
        key="api/C.cs#ModelsController.Forecast",
        kind="member",
        name="Forecast",
        owner="ModelsController",
        file="api/C.cs",
    )
    root = endpoint()
    edges = (
        GraphEdge(
            kind="dispatches",
            source=root.key,
            target=member.key,
            via="entrypoint:manifest",
            confidence=1.0,
        ),
    )
    _, made, report = declared(
        registry(seam(source=Source(file="api/C.cs", record="ModelsController.Forecast"))),
        (controller, member, root),
        edges,
    )
    assert report.answering_only == 1
    assert all(not edge.target.startswith("seam:") for edge in made)


def test_queue_and_topic_look_for_their_own_entry_kinds() -> None:
    """`queue` и `topic` — разные слова у разных платформ для одного механизма."""
    root = GraphNode(
        key=entry_key("kafka_topic", "forecast-requests"),
        kind="entry_point",
        name="forecast-requests",
        source="registry",
        attributes={"entry_kind": "kafka_topic"},
    )
    record = seam(
        key="forecast-requests",
        seam_kind="queue",
        literal="forecast-requests",
        http_method="",
    )
    _, _, report = declared(registry(record), PY_NODES + (root,))
    assert report.both_sides == 1


def test_no_registry_and_no_seams_produce_nothing() -> None:
    """Репозиторий без объявленных швов — законное состояние, а не пустой отчёт."""
    assert declared(None, PY_NODES) == ([], [], declared(None, PY_NODES)[2])
    _, _, report = declared(registry(), PY_NODES)
    assert report.declared == 0
