"""Предвычисленная достижимость, веерность и отчёт (G07, G08).

Тесты держат три свойства: цикл не вешает вычисление, `binds` не является
путём исполнения, а узел выше порога веерности отвечает «анализ не сужает»
вместо списка процессов.
"""

from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.graph import (
    GraphEdge,
    GraphIndex,
    GraphMeta,
    GraphNode,
    compute,
    path,
    read_reach,
    shared_components,
    write_index,
)
from docpipe.graph.report import health, render

runner = CliRunner()


def entry(name: str) -> GraphNode:
    return GraphNode(
        key=f"entry:job:{name}",
        kind="entry_point",
        name=name,
        source="registry",
        attributes={"entry_kind": "job"},
    )


def member(owner: str, name: str) -> GraphNode:
    return GraphNode(
        key=f"src/{owner}.cs#{owner}.{name}",
        kind="member",
        name=name,
        owner=owner,
        file=f"src/{owner}.cs",
    )


def type_node(name: str) -> GraphNode:
    return GraphNode(key=f"src/{name}.cs#{name}", kind="type", name=name, file=f"src/{name}.cs")


def data_node(name: str) -> GraphNode:
    return GraphNode(key=f"data:{name}", kind="data", name=name.upper(), source="registry")


def edge(kind: str, source: str, target: str) -> GraphEdge:
    return GraphEdge(kind=kind, source=source, target=target, via="тест")


def chain_index() -> GraphIndex:
    nodes = (
        entry("ночная"),
        type_node("Runner"),
        member("Runner", "Run"),
        type_node("Service"),
        member("Service", "Do"),
        data_node("orders"),
    )
    edges = (
        edge("dispatches", "entry:job:ночная", "src/Runner.cs#Runner.Run"),
        edge("calls", "src/Runner.cs#Runner.Run", "src/Service.cs#Service.Do"),
        edge("touches", "src/Service.cs#Service", "data:orders"),
    )
    return GraphIndex(nodes=nodes, edges=edges)


# ──────────────────────────────────────────────────────────────────────────────
# Достижимость
# ──────────────────────────────────────────────────────────────────────────────


def test_root_reaches_the_whole_chain_including_data() -> None:
    """Член достигнут — достигнут и его тип, а через тип и его таблицы.

    Огрубление в сторону «достижимо больше» названо в отчёте: обращения
    к данным объявлены на типе, а не на члене.
    """
    reachability = compute(chain_index())
    reached = set(reachability.reached_by("entry:job:ночная"))
    assert "src/Service.cs#Service.Do" in reached
    assert "data:orders" in reached


def test_binds_is_not_a_path_of_execution() -> None:
    """`binds` — это «кто мог бы позвать», а не «зовёт».

    Включить его значило бы объявить достижимым весь код, куда ведёт хоть
    одна регистрация.
    """
    index = GraphIndex(
        nodes=(
            entry("ночная"),
            type_node("IService"),
            type_node("Service"),
            member("Service", "Do"),
        ),
        edges=(
            edge("dispatches", "entry:job:ночная", "src/IService.cs#IService"),
            edge("binds", "src/IService.cs#IService", "src/Service.cs#Service"),
        ),
    )
    reachability = compute(index)
    assert "src/Service.cs#Service" not in reachability.reached_by("entry:job:ночная")


def test_cycle_does_not_hang_and_both_nodes_are_reachable() -> None:
    """Циклы в графе вызовов есть всегда: рекурсия, взаимные вызовы.

    Конденсация SCC для того и нужна — без неё обход зациклится или потеряет
    половину цепочки.
    """
    index = GraphIndex(
        nodes=(entry("ночная"), type_node("A"), member("A", "One"), member("A", "Two")),
        edges=(
            edge("dispatches", "entry:job:ночная", "src/A.cs#A.One"),
            edge("calls", "src/A.cs#A.One", "src/A.cs#A.Two"),
            edge("calls", "src/A.cs#A.Two", "src/A.cs#A.One"),
        ),
    )
    reachability = compute(index)
    reached = set(reachability.reached_by("entry:job:ночная"))
    assert {"src/A.cs#A.One", "src/A.cs#A.Two"} <= reached
    assert reachability.component_size["src/A.cs#A.One"] == 2


def test_fanout_counts_roots_and_marks_shared_components() -> None:
    nodes = [type_node("Shared"), member("Shared", "Log")]
    edges = []
    for number in range(6):
        nodes.append(entry(f"корень{number}"))
        edges.append(edge("dispatches", f"entry:job:корень{number}", "src/Shared.cs#Shared.Log"))
    index = GraphIndex(nodes=tuple(nodes), edges=tuple(edges))
    reachability = compute(index)
    assert reachability.fanout("src/Shared.cs#Shared.Log") == 6
    shared = dict(shared_components(reachability, index.nodes, threshold=3))
    # Тип попадает в список вместе с членом: член достигнут — достигнут
    # и его тип, и «общим» оказывается весь узел, а не только метод.
    assert shared["src/Shared.cs#Shared.Log"] == 6
    assert shared["src/Shared.cs#Shared"] == 6


def test_reachability_is_stable_to_edge_order() -> None:
    index = chain_index()
    reversed_index = GraphIndex(
        nodes=tuple(reversed(index.nodes)), edges=tuple(reversed(index.edges))
    )
    assert compute(index).masks == compute(reversed_index).masks


def test_roots_of_names_the_entry_points() -> None:
    reachability = compute(chain_index())
    assert reachability.roots_of("data:orders") == ["entry:job:ночная"]


# ──────────────────────────────────────────────────────────────────────────────
# Путь
# ──────────────────────────────────────────────────────────────────────────────


def test_path_is_restored_and_depth_is_respected() -> None:
    index = chain_index()
    found = path(index, "entry:job:ночная", "data:orders")
    assert [step.target for step in found][-1] == "data:orders"
    assert path(index, "entry:job:ночная", "data:orders", depth=1) == []


# ──────────────────────────────────────────────────────────────────────────────
# Хранение
# ──────────────────────────────────────────────────────────────────────────────


def test_masks_survive_the_round_trip(tmp_path: Path) -> None:
    """Порядок корней хранится вместе с масками: прочитать маску, не зная
    порядка, нельзя — числа совпадут, а смысл будет чужой."""
    index = chain_index()
    reachability = compute(index)
    target = tmp_path / "graph.db"
    write_index(target, index, GraphMeta(generation="", roots=reachability.roots), reachability)
    loaded = read_reach(target)
    assert loaded.roots == reachability.roots
    assert loaded.masks == reachability.masks
    assert loaded.roots_of("data:orders") == ["entry:job:ночная"]


# ──────────────────────────────────────────────────────────────────────────────
# Отчёт
# ──────────────────────────────────────────────────────────────────────────────


def test_report_is_deterministic_and_timeless() -> None:
    """Отчёт кладут в ревью и сравнивают между прогонами: времени в нём нет."""
    index = chain_index()
    reachability = compute(index)
    meta = GraphMeta(generation="sha256:x", repo="демо", counts={"nodes": 6, "edges": 3})
    first = render(index, meta, reachability, 50)
    second = render(index, meta, reachability, 50)
    assert first == second
    assert "Точки входа" in first
    assert "Общие компоненты" in first
    assert "20" not in first.split("\n")[2]  # ни года, ни даты в шапке


def test_report_names_roots_without_code() -> None:
    index = GraphIndex(nodes=(entry("сирота"),))
    meta = GraphMeta(generation="sha256:x")
    text = render(index, meta, compute(index), 50)
    assert "узел кода не найден" in text
    assert "Состояние работы, а не дефект" in text


def test_health_says_when_there_is_nothing_to_count() -> None:
    """«Категорий нет» и «всё разрешилось» — разные утверждения."""
    assert "не «всё разрешилось»" in health(GraphMeta(generation="sha256:x"))


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def prepared_index(tmp_path: Path) -> Path:
    index = chain_index()
    reachability = compute(index)
    target = tmp_path / "graph.db"
    write_index(
        target,
        index,
        GraphMeta(generation="", repo="демо", roots=reachability.roots, report={"дыра": 3}),
        reachability,
    )
    return target


def test_cli_report_and_health(tmp_path: Path) -> None:
    target = prepared_index(tmp_path)
    report = runner.invoke(app, ["graph", "report", str(target)])
    assert report.exit_code == 0, report.output
    assert "Таблица точек входа" in report.output

    state = runner.invoke(app, ["graph", "health", str(target)])
    assert state.exit_code == 0, state.output
    assert "дыра" in state.output


def test_cli_reaches_and_affects(tmp_path: Path) -> None:
    target = prepared_index(tmp_path)
    reaches = runner.invoke(app, ["graph", "reaches", "ночная", "--index", str(target)])
    assert reaches.exit_code == 0, reaches.output
    assert "ORDERS" in reaches.output

    affects = runner.invoke(app, ["graph", "affects", "ORDERS", "--index", str(target)])
    assert affects.exit_code == 0, affects.output
    assert "ночная" in affects.output


def test_cli_affects_refuses_to_answer_for_a_shared_component(tmp_path: Path) -> None:
    """Узел выше порога — это не «затронуто триста процессов», а признание,
    что анализ не сужает."""
    target = prepared_index(tmp_path)
    result = runner.invoke(
        app, ["graph", "affects", "ORDERS", "--index", str(target), "--fanout", "0"]
    )
    assert result.exit_code == 0, result.output
    assert "ОБЩИЙ КОМПОНЕНТ" in result.output
    assert "не сужает" in result.output


def test_cli_reaches_names_candidates_when_nothing_matches(tmp_path: Path) -> None:
    """Пустой ответ запрещён: называется, что искали и что рядом."""
    target = prepared_index(tmp_path)
    result = runner.invoke(app, ["graph", "reaches", "нет-такого", "--index", str(target)])
    assert result.exit_code == 1
    assert "Известные корни" in result.output


def test_cli_path_prints_the_chain(tmp_path: Path) -> None:
    target = prepared_index(tmp_path)
    result = runner.invoke(
        app, ["graph", "path", "entry:job:ночная", "data:orders", "--index", str(target)]
    )
    assert result.exit_code == 0, result.output
    assert "-->" in result.output
