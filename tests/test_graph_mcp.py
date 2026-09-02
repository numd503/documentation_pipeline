"""MCP-сервер (G12).

Протокол реализован руками: сервер, которому нужен `pip install`, в закрытом
контуре не запустится ни разу. Тесты проверяют то, что ломается молча:
набор инструментов совпадает с набором форм, отсутствие индекса — внятная
ошибка, а поколение сверяется на каждом запросе.
"""

import ast
import io
import json
from pathlib import Path

from docpipe.graph import GraphEdge, GraphIndex, GraphMeta, GraphNode, compute, write_index
from docpipe.graph.mcp import Server, handle, serve, tools
from docpipe.graph.search import entries


def index_with(name: str) -> GraphIndex:
    return GraphIndex(
        nodes=(
            GraphNode(
                key="entry:job:ночная",
                kind="entry_point",
                name=name,
                source="registry",
                attributes={"entry_kind": "job"},
            ),
            GraphNode(key="src/A.cs#A", kind="type", name="A", file="src/A.cs"),
        ),
        edges=(
            GraphEdge(
                kind="dispatches", source="entry:job:ночная", target="src/A.cs#A", via="тест"
            ),
        ),
    )


def build(path: Path, name: str = "Ночная переоценка") -> None:
    index = index_with(name)
    reachability = compute(index)
    write_index(
        path,
        index,
        GraphMeta(generation="", repo="демо", roots=reachability.roots),
        reachability,
        entries(index),
    )


def request(server: Server, method: str, **params: object) -> dict:
    answer = handle(server, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert answer is not None
    return answer


def call(server: Server, name: str, **arguments: object) -> dict:
    answer = request(server, "tools/call", name=name, arguments=arguments)
    return json.loads(answer["result"]["content"][0]["text"])


# ──────────────────────────────────────────────────────────────────────────────
# Протокол
# ──────────────────────────────────────────────────────────────────────────────


def test_initialize_carries_the_instruction(tmp_path: Path) -> None:
    """Без инструкции «как здесь работать» агент зовёт не то и заключает,
    что инструмент бесполезен."""
    server = Server(tmp_path / "graph.db", tmp_path)
    result = request(server, "initialize")["result"]
    assert result["protocolVersion"]
    assert "docpipe_resolve" in result["instructions"]
    assert "грепом" in result["instructions"]


def test_tools_match_the_forms_one_to_one() -> None:
    """Расхождение набора инструментов с набором форм запрещено: это два
    описания одного, и второе отстанет."""
    forms = {
        node.name
        for node in ast.walk(ast.parse(Path("docpipe/graph/api.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    names = {tool["name"].removeprefix("docpipe_") for tool in tools()}
    assert names == forms


def test_notification_gets_no_answer(tmp_path: Path) -> None:
    server = Server(tmp_path / "graph.db", tmp_path)
    assert handle(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_an_error(tmp_path: Path) -> None:
    server = Server(tmp_path / "graph.db", tmp_path)
    answer = request(server, "чего-то/нет")
    assert answer["error"]["code"] == -32601


# ──────────────────────────────────────────────────────────────────────────────
# Индекс
# ──────────────────────────────────────────────────────────────────────────────


def test_missing_index_names_the_build_command(tmp_path: Path) -> None:
    """Сервер не собирает граф сам: отсутствие индекса — внятная ошибка
    с командой сборки."""
    server = Server(tmp_path / "graph.db", tmp_path)
    answer = call(server, "docpipe_reaches", root="entry:job:ночная")
    assert "graph build" in answer["error"]


def test_forms_without_a_graph_work_without_an_index(tmp_path: Path) -> None:
    """Отсутствие индекса не мешает `why`: это git, а не граф."""
    server = Server(tmp_path / "graph.db", Path("."))
    answer = call(server, "docpipe_why", file="docpipe/model.py", limit=2)
    assert answer["available"] is True


def test_generation_is_checked_on_every_request(tmp_path: Path) -> None:
    """Сборка подменяет файл атомарно; сервер, держащий прежний индекс,
    отвечал бы по устаревшему без единого признака."""
    path = tmp_path / "graph.db"
    build(path, "Первое имя")
    server = Server(path, tmp_path)
    first = call(server, "docpipe_reaches", root="entry:job:ночная")
    assert first["name"] == "Первое имя"

    build(path, "Второе имя")
    second = call(server, "docpipe_reaches", root="entry:job:ночная")
    assert second["name"] == "Второе имя"


def test_call_of_an_unknown_tool_says_where_the_list_is(tmp_path: Path) -> None:
    server = Server(tmp_path / "graph.db", tmp_path)
    answer = call(server, "docpipe_выдумка")
    assert "tools/list" in answer["error"]


def test_stdio_loop_answers_line_by_line(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    build(path)
    server = Server(path, tmp_path)
    source = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        + "\n"
        + "не json\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
    )
    sink = io.StringIO()
    serve(server, source, sink)
    answers = [json.loads(line) for line in sink.getvalue().splitlines()]
    # Битая строка не роняет цикл и не порождает ответа, уведомление — тоже.
    assert len(answers) == 1
    assert answers[0]["id"] == 1
