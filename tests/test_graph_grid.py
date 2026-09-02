"""Грид-сервисы как шов .NET ↔ .NET (G06).

Между вызывающим и реализацией нет ни одного ребра, которое видел бы разбор:
есть имя, которое обе стороны обязаны знать. Разбор доводит вызов до члена
интерфейса и останавливается; связь «интерфейс → зарегистрированная
реализация» объявлена реестром, и делаем её мы.
"""

from docpipe.graph.grid import seams
from docpipe.graph.model import GraphEdge, GraphNode


def service(name: str, ref: str) -> GraphNode:
    return GraphNode(
        key=f"entry:grid_service:{ref.lower()}",
        kind="entry_point",
        name=name,
        source="registry",
        attributes={"entry_kind": "grid_service", "ref": ref},
    )


def type_node(name: str, file: str | None = None) -> GraphNode:
    source = file or f"src/{name}.cs"
    return GraphNode(key=f"{source}#{name}", kind="type", name=name, file=source)


def member(owner: str, name: str, file: str | None = None) -> GraphNode:
    source = file or f"src/{owner}.cs"
    return GraphNode(
        key=f"{source}#{owner}.{name}", kind="member", name=name, owner=owner, file=source
    )


def edge(kind: str, source: str, target: str) -> GraphEdge:
    return GraphEdge(kind=kind, source=source, target=target, via="тест")


def cluster(ref: str = "IBlockFiles") -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    nodes = (
        service("Файлы блоков", ref),
        type_node("IBlockFiles"),
        member("IBlockFiles", "Load"),
        type_node("BlockFilesService"),
        member("BlockFilesService", "Load"),
        type_node("Caller"),
        member("Caller", "Run"),
    )
    edges = (
        edge(
            "dispatches",
            f"entry:grid_service:{ref.lower()}",
            "src/BlockFilesService.cs#BlockFilesService",
        ),
        edge(
            "inherits",
            "src/BlockFilesService.cs#BlockFilesService",
            "src/IBlockFiles.cs#IBlockFiles",
        ),
        edge("calls", "src/Caller.cs#Caller.Run", "src/IBlockFiles.cs#IBlockFiles.Load"),
    )
    return nodes, edges


def test_proxy_call_becomes_a_seam_to_the_implementation() -> None:
    """Путь «член → грид-сервис → член» восстанавливается и виден как ребро
    шва, а не как прямой вызов."""
    nodes, edges = cluster()
    produced, report = seams(nodes, edges)
    assert len(produced) == 1
    assert produced[0].kind == "crosses"
    assert produced[0].source == "src/Caller.cs#Caller.Run"
    assert produced[0].target == "src/BlockFilesService.cs#BlockFilesService.Load"
    assert produced[0].attributes["service"] == "IBlockFiles"
    assert report.seams == 1


def test_contract_is_found_by_inheritance_when_the_name_differs() -> None:
    """Имя сервиса не обязано совпадать с именем интерфейса: `CalcDxExcel`
    при классе `DXExcelCalcService` — норма, и контракт виден по тому,
    что реализация наследует."""
    nodes, edges = cluster(ref="CalcDxExcel")
    produced, report = seams(nodes, edges)
    assert len(produced) == 1
    assert report.linked == 1


def test_service_without_a_code_node_is_counted_not_dropped() -> None:
    nodes = (service("Пропавший", "IGone"),)
    produced, report = seams(nodes, ())
    assert produced == []
    assert report.services == 1
    assert report.linked == 0
    assert report.examples["грид-сервисы без пары"]


def test_service_without_a_contract_is_named() -> None:
    """Реализация есть, интерфейса нет: шов построить не из чего, и это
    отдельное число, а не молчание."""
    nodes = (
        service("Без контракта", "Solo"),
        type_node("SoloService"),
        member("SoloService", "Run"),
    )
    edges = (edge("dispatches", "entry:grid_service:solo", "src/SoloService.cs#SoloService"),)
    produced, report = seams(nodes, edges)
    assert produced == []
    assert report.without_contract == 1


def test_existing_edge_is_not_duplicated() -> None:
    nodes, edges = cluster()
    edges = (
        *edges,
        edge(
            "crosses", "src/Caller.cs#Caller.Run", "src/BlockFilesService.cs#BlockFilesService.Load"
        ),
    )
    produced, _ = seams(nodes, edges)
    assert produced == []
