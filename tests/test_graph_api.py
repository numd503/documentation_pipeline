"""Библиотека форм вопроса (G11).

Универсальный обход наружу не выпускается: формы перечислены, и каждая
названа вопросом пользователя. Здесь проверяются свойства, которые
отличают ответ от отписки: признак неполноты, восстановимое усечение
и честное «не разрешилась вот эта сторона».
"""

import ast
from pathlib import Path

from docpipe.graph import GraphEdge, GraphIndex, GraphMeta, GraphNode, compute, write_index
from docpipe.graph.api import affects, card, overview, path, reaches, resolve, why
from docpipe.graph.search import entries


def entry(name: str) -> GraphNode:
    return GraphNode(
        key=f"entry:job:{name}",
        kind="entry_point",
        name=name,
        source="registry",
        attributes={"entry_kind": "job"},
    )


def member(owner: str, name: str, file: str | None = None) -> GraphNode:
    source = file or f"src/{owner}.cs"
    return GraphNode(
        key=f"{source}#{owner}.{name}",
        kind="member",
        name=name,
        owner=owner,
        file=source,
        attributes={"module": "src/App/App.csproj"},
    )


def sample() -> GraphIndex:
    return GraphIndex(
        nodes=(
            entry("ночная"),
            GraphNode(key="src/Runner.cs#Runner", kind="type", name="Runner", file="src/Runner.cs"),
            member("Runner", "Run"),
            GraphNode(key="data:orders", kind="data", name="ORDERS", source="registry"),
        ),
        edges=(
            GraphEdge(
                kind="dispatches",
                source="entry:job:ночная",
                target="src/Runner.cs#Runner.Run",
                via="тест",
            ),
            GraphEdge(
                kind="touches", source="src/Runner.cs#Runner", target="data:orders", via="тест"
            ),
        ),
    )


def prepared(tmp_path: Path) -> tuple[Path, GraphIndex, GraphMeta, object]:
    index = sample()
    reachability = compute(index)
    target = tmp_path / "graph.db"
    meta = GraphMeta(
        generation="",
        repo="демо",
        roots=reachability.roots,
        report={"корней связано: узел кода не найден": 2, "рёбер довершено по регистрации": 1},
    )
    stored = write_index(target, index, meta, reachability, entries(index))
    return target, index, stored, reachability


# ──────────────────────────────────────────────────────────────────────────────
# Признак неполноты и усечение
# ──────────────────────────────────────────────────────────────────────────────


def test_every_answer_carries_its_own_incompleteness(tmp_path: Path) -> None:
    """Признак неполноты относится к ЭТОМУ ответу, а не к общей сводке:
    общая сводка на вопрос «чему здесь нельзя верить» не отвечает."""
    target, index, meta, reachability = prepared(tmp_path)
    answer = reaches(index, meta, reachability, "entry:job:ночная")
    assert "incomplete" in answer
    assert answer["incomplete"]["categories"]


def test_truncation_is_recoverable(tmp_path: Path) -> None:
    """«Показано 20 из 300» без способа увидеть остальные заставляет звать
    инструмент заново с другими параметрами — то есть угадывать."""
    nodes = [entry("ночная")]
    edges = []
    for number in range(40):
        nodes.append(member("Runner", f"M{number}"))
        edges.append(
            GraphEdge(
                kind="dispatches",
                source="entry:job:ночная",
                target=f"src/Runner.cs#Runner.M{number}",
                via="тест",
            )
        )
    index = GraphIndex(nodes=tuple(nodes), edges=tuple(edges))
    reachability = compute(index)
    answer = reaches(index, GraphMeta(generation=""), reachability, "entry:job:ночная")
    truncated = answer["reached"]["member"]["truncated"]
    assert truncated["of"] == 40
    assert truncated["how_to_see_the_rest"]


# ──────────────────────────────────────────────────────────────────────────────
# Формы
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_returns_candidates_with_how_they_matched(tmp_path: Path) -> None:
    target, index, _, _ = prepared(tmp_path)
    answer = resolve(target, index, "ночная")
    assert answer["candidates"][0]["matched_how"]


def test_card_says_when_the_node_is_unknown(tmp_path: Path) -> None:
    _, index, meta, reachability = prepared(tmp_path)
    answer = card(index, meta, reachability, "нет-такого")
    assert answer["found"] is False
    assert "resolve" in answer["note"]


def test_affects_accepts_file_paths(tmp_path: Path) -> None:
    """Вывод `git diff --name-only` — основной сценарий в PR; требовать
    перевода путей в ключи значит требовать знания, которого у вызывающего
    нет."""
    _, index, meta, reachability = prepared(tmp_path)
    answer = affects(index, meta, reachability, ["src/Runner.cs"])
    assert answer["found"] is True
    assert answer["entry_points"]["total"] == 1


def test_affects_refuses_to_list_for_a_shared_component(tmp_path: Path) -> None:
    _, index, meta, reachability = prepared(tmp_path)
    answer = affects(index, meta, reachability, ["src/Runner.cs"], threshold=0)
    assert answer["shared_components"]
    assert answer["entry_points"]["total"] == 0
    assert "не строится намеренно" in answer["note"]


def test_affects_names_what_it_did_not_find(tmp_path: Path) -> None:
    _, index, meta, reachability = prepared(tmp_path)
    answer = affects(index, meta, reachability, ["выдуманный/файл.cs"])
    assert answer["found"] is False
    assert answer["unknown"] == ["выдуманный/файл.cs"]


def test_path_names_the_unresolved_side(tmp_path: Path) -> None:
    """«Связь не найдена» при неразрешённой стороне запрещено: ответ обязан
    называть, какая именно сторона не разрешилась."""
    _, index, _, _ = prepared(tmp_path)
    answer = path(index, "entry:job:ночная", "нет-такого")
    assert answer["found"] is False
    assert answer["unresolved_side"] == ["нет-такого"]
    assert "не отсутствует связь" in answer["note"]


def test_path_returns_the_chain(tmp_path: Path) -> None:
    _, index, _, _ = prepared(tmp_path)
    answer = path(index, "entry:job:ночная", "data:orders")
    assert answer["found"] is True
    assert answer["steps"][-1]["to"] == "data:orders"


# ──────────────────────────────────────────────────────────────────────────────
# Формы без графа
# ──────────────────────────────────────────────────────────────────────────────


def test_overview_works_without_an_index(tmp_path: Path) -> None:
    """`overview` — первое, что инструмент вообще может сказать о незнакомом
    репозитории: он считается из разведки, а не из графа."""
    answer = overview(Path("."), script=Path("tools/recon.py"))
    assert answer["available"] is True
    assert answer["stacks"]
    assert "разведка" in answer["note"]


def test_overview_says_what_to_run_when_there_is_nothing(tmp_path: Path) -> None:
    answer = overview(tmp_path, script=tmp_path / "нет-скрипта.py")
    assert answer["available"] is False
    assert "recon.py" in answer["note"]


def test_why_works_without_an_index() -> None:
    answer = why(Path("."), "docpipe/model.py", limit=3)
    assert answer["available"] is True
    assert answer["commits"]
    assert "без графа" in answer["note"]


# ──────────────────────────────────────────────────────────────────────────────
# Ни одна форма, кроме `path`, не обходит граф
# ──────────────────────────────────────────────────────────────────────────────


def test_only_path_walks_the_graph() -> None:
    """Если запрос требует обхода — нарушена архитектура, а не «медленно
    работает». Проверка структурная: обход зовётся ровно из одной функции.
    """
    tree = ast.parse(Path("docpipe/graph/api.py").read_text(encoding="utf-8"))
    walkers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "walk"
            for inner in ast.walk(node)
        )
    ]
    assert walkers == ["path"]


# ------------------------------------------------------------------------------------------
# Ноль точек входа обязан быть объяснён (Р7)
# ------------------------------------------------------------------------------------------


def _composition_index() -> tuple[GraphIndex, GraphMeta]:
    nodes = (GraphNode(key="Config.cs#Services", kind="type", name="Services", file="Config.cs"),)
    index = GraphIndex(nodes=nodes, edges=())
    meta = GraphMeta(
        generation="x",
        roots=(),
        composition_roots=("Config.cs", "Program.cs"),
    )
    return index, meta


def test_zero_entry_points_in_a_composition_root_says_why() -> None:
    """Ноль по файлу сборки контейнера читается как «ничего не задето»."""
    index, meta = _composition_index()
    answer = affects(index, meta, compute(index), ["Config.cs"])
    assert answer["found"] is True
    assert answer["entry_points"]["total"] == 0
    assert "сборку контейнера" in answer["note"]


def test_composition_root_without_nodes_is_still_answered() -> None:
    """`Program.cs` на top-level statements узлов не даёт вовсе.

    «Не найдено» здесь формально верно и практически ложно: именно этот файл
    меняется чаще прочих, и именно он задевает всё.
    """
    index, meta = _composition_index()
    answer = affects(index, meta, compute(index), ["Program.cs"])
    assert answer["found"] is False
    assert answer["composition_roots"] == ["Program.cs"]
    assert "задевает всё" in answer["note"]


def test_zero_entry_points_elsewhere_says_something_else() -> None:
    """Ноль вне композиционного корня объясняется по-другому — и тоже объясняется."""
    nodes = (GraphNode(key="Dead.cs#Dead", kind="type", name="Dead", file="Dead.cs"),)
    index = GraphIndex(nodes=nodes, edges=())
    meta = GraphMeta(generation="x", roots=())
    answer = affects(index, meta, compute(index), ["Dead.cs"])
    assert "ни одна точка входа" in answer["note"]


def test_page_limit_is_a_property_of_the_presentation() -> None:
    """Усечение списка не должно менять ответ на вопрос «сколько».

    Проверка на влитых правках читает список целиком, и если бы предел
    был зашит, она объявляла бы пропуском то, что не поместилось.
    """
    nodes = [GraphNode(key="Code.cs#Code", kind="type", name="Code", file="Code.cs")]
    edges = []
    for number in range(25):
        key = f"entry:http:get {number:02d}"
        nodes.append(GraphNode(key=key, kind="entry_point", name=f"GET {number:02d}"))
        edges.append(
            GraphEdge(
                kind="dispatches",
                source=key,
                target="Code.cs#Code",
                via="entry:manifest",
                confidence=1.0,
            )
        )
    index = GraphIndex(nodes=tuple(nodes), edges=tuple(edges))
    meta = GraphMeta(
        generation="x", roots=tuple(sorted(n.key for n in nodes if n.kind == "entry_point"))
    )
    answer = affects(index, meta, compute(index), ["Code.cs"], limit=1_000)
    assert answer["entry_points"]["total"] == 25
    assert len(answer["entry_points"]["items"]) == 25
