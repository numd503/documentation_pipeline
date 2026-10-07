"""Общий протокол MCP (S26).

Тесты держат то, что ломалось молча: исключение в инструменте убивало сервер
целиком, и агент терял все инструменты до перезапуска сессии; запрос не той
формы выходил из цикла `AttributeError`; версия протокола была литералом.
Набор инструментов здесь — подставной: протокол не знает, чей он.
"""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from docpipe import __version__
from docpipe.graph.mcp import GraphTools, Server
from docpipe.mcp import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    SUPPORTED_PROTOCOLS,
    ToolSet,
    handle,
    negotiate,
    serve,
)


class FakeTools:
    """Набор инструментов, у каждого из которых своя поломка."""

    server_name = "docpipe-проверка"
    instructions = "проверочный сервер"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def tools(self) -> list[dict[str, Any]]:
        return [{"name": name, "inputSchema": {"type": "object"}} for name in self.names()]

    def names(self) -> list[str]:
        return ["boom", "echo", "noisy", "opaque", "refuse"]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        if name == "boom":
            raise ValueError("invalid literal for int() with base 10: 'abc'")
        if name == "noisy":
            print("шум из инструмента")
            return {"ok": True}
        if name == "opaque":
            return {"path": Path("где-то")}
        if name == "refuse":
            return {"error": "отказ по существу"}
        return {"echo": arguments}


class BrokenList(FakeTools):
    """Сломан не инструмент, а описание набора: протокол обязан пережить и это."""

    def tools(self) -> list[dict[str, Any]]:
        raise RuntimeError("описания не собрались")


class OpaqueList(FakeTools):
    def tools(self) -> list[dict[str, Any]]:
        return [{"name": "x", "inputSchema": {"default": object()}}]


def rpc(method: str, request_id: int = 1, **params: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def answer_of(toolset: ToolSet, request: Any) -> dict[str, Any]:
    answer = handle(toolset, request)
    assert answer is not None
    return answer


def tool_text(answer: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(answer["result"]["content"][0]["text"])
    return payload


def run_loop(toolset: ToolSet, *lines: str) -> list[dict[str, Any]]:
    sink = io.StringIO()
    serve(toolset, io.StringIO("".join(line + "\n" for line in lines)), sink)
    return [json.loads(line) for line in sink.getvalue().splitlines()]


# ──────────────────────────────────────────────────────────────────────────────
# Изоляция ошибок
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_exception_is_an_error_answer(capsys: pytest.CaptureFixture[str]) -> None:
    """Исключение — ответ `isError` с типом и сообщением, трассировка — в stderr.

    Тип в тексте нужен агенту: «ValueError: invalid literal» говорит, что
    неверен аргумент, и агент перезовёт с другим, а не бросит инструмент.
    """
    answer = answer_of(FakeTools(), rpc("tools/call", name="boom", arguments={}))
    assert answer["result"]["isError"] is True
    assert tool_text(answer)["error"] == (
        "внутренняя ошибка инструмента boom: "
        "ValueError: invalid literal for int() with base 10: 'abc'"
    )
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "ValueError" in err


def test_loop_survives_a_failing_tool() -> None:
    """Следующий запрос в том же цикле обслуживается: сервер жив."""
    toolset = FakeTools()
    answers = run_loop(
        toolset,
        json.dumps(rpc("tools/call", 1, name="boom", arguments={})),
        json.dumps(rpc("tools/call", 2, name="echo", arguments={"x": 1})),
    )
    assert [answer["id"] for answer in answers] == [1, 2]
    assert answers[0]["result"]["isError"] is True
    assert answers[1]["result"]["isError"] is False
    assert tool_text(answers[1]) == {"echo": {"x": 1}}
    assert toolset.calls == ["boom", "echo"]


def test_graph_tool_with_a_bad_argument_keeps_the_server(tmp_path: Path) -> None:
    """Случай из ловушки плана: `limit: "abc"` у формы графа ронял сервер."""
    server = Server(tmp_path / "graph.db", Path("."))
    bad = {"file": "x", "limit": "abc"}
    answers = run_loop(
        GraphTools(server),
        json.dumps(rpc("tools/call", 1, name="docpipe_why", arguments=bad)),
        json.dumps(rpc("tools/list", 2)),
    )
    assert answers[0]["result"]["isError"] is True
    assert "docpipe_why: ValueError" in tool_text(answers[0])["error"]
    assert answers[1]["id"] == 2
    assert answers[1]["result"]["tools"]


def test_unserializable_tool_answer_is_an_error() -> None:
    """`Path` в ответе падал бы при записи в поток — уже вне инструмента."""
    answer = answer_of(FakeTools(), rpc("tools/call", name="opaque", arguments={}))
    assert answer["result"]["isError"] is True
    assert "opaque: TypeError" in tool_text(answer)["error"]


def test_error_returned_by_the_tool_keeps_its_text() -> None:
    """Ошибка, которую инструмент вернул сам, — не внутренняя: текст его."""
    answer = answer_of(FakeTools(), rpc("tools/call", name="refuse", arguments={}))
    assert answer["result"]["isError"] is True
    assert tool_text(answer) == {"error": "отказ по существу"}


def test_tool_output_on_stdout_does_not_reach_the_channel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stdout — канал протокола: `print` в инструменте разорвал бы поток JSON-RPC."""
    answer = answer_of(FakeTools(), rpc("tools/call", name="noisy", arguments={}))
    assert tool_text(answer) == {"ok": True}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "шум из инструмента" in captured.err


def test_failing_tool_list_is_an_internal_error(capsys: pytest.CaptureFixture[str]) -> None:
    answers = run_loop(BrokenList(), json.dumps(rpc("tools/list", 1)), json.dumps(rpc("ping", 2)))
    assert answers[0]["error"]["code"] == INTERNAL_ERROR
    assert "RuntimeError: описания не собрались" in answers[0]["error"]["message"]
    assert answers[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}
    assert "Traceback" in capsys.readouterr().err


def test_unserializable_protocol_answer_is_an_internal_error() -> None:
    answers = run_loop(OpaqueList(), json.dumps(rpc("tools/list", 7)), json.dumps(rpc("ping", 8)))
    assert answers[0]["id"] == 7
    assert answers[0]["error"]["code"] == INTERNAL_ERROR
    assert answers[1]["id"] == 8


# ──────────────────────────────────────────────────────────────────────────────
# Форма запроса
# ──────────────────────────────────────────────────────────────────────────────


def test_null_params_is_an_invalid_request() -> None:
    """`params: null` выходил из цикла `AttributeError` на `.get`."""
    request = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": None}
    answer = answer_of(FakeTools(), request)
    assert answer["id"] == 5
    assert answer["error"]["code"] == INVALID_REQUEST


def test_list_request_is_an_invalid_request() -> None:
    """Пакет JSON-RPC (список) — `-32600` с `id: null`: прочесть `id` негде."""
    answer = answer_of(FakeTools(), [rpc("ping", 1), rpc("ping", 2)])
    assert answer["id"] is None
    assert answer["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize(
    "request_",
    [
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "method": 42},
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": ["позиционно"]},
        "строка",
        17,
    ],
)
def test_malformed_requests_are_invalid(request_: Any) -> None:
    assert answer_of(FakeTools(), request_)["error"]["code"] == INVALID_REQUEST


def test_loop_survives_malformed_requests() -> None:
    answers = run_loop(
        FakeTools(),
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}),
        json.dumps([rpc("ping", 2)]),
        json.dumps(rpc("ping", 3)),
    )
    assert [answer.get("error", {}).get("code") for answer in answers] == [
        INVALID_REQUEST,
        INVALID_REQUEST,
        None,
    ]
    assert answers[2]["id"] == 3


@pytest.mark.parametrize(
    "params",
    [{"name": 5, "arguments": {}}, {"name": "echo", "arguments": ["позиционно"]}],
)
def test_malformed_call_parameters_are_invalid_params(params: dict[str, Any]) -> None:
    toolset = FakeTools()
    answer = answer_of(toolset, rpc("tools/call", **params))
    assert answer["error"]["code"] == INVALID_PARAMS
    assert toolset.calls == []


def test_null_arguments_mean_no_arguments() -> None:
    """`arguments: null` клиенты шлют для инструментов без аргументов."""
    answer = answer_of(FakeTools(), rpc("tools/call", name="echo", arguments=None))
    assert tool_text(answer) == {"echo": {}}


def test_broken_line_gets_no_answer_but_a_note(capsys: pytest.CaptureFixture[str]) -> None:
    """На битую строку ответа нет — `id` не прочесть, — но и молчать нельзя."""
    answers = run_loop(FakeTools(), "{не json", json.dumps(rpc("ping", 4)))
    assert [answer["id"] for answer in answers] == [4]
    err = capsys.readouterr().err
    assert "строка 1" in err
    assert "docpipe-проверка" in err


def test_notification_of_an_unknown_method_gets_no_answer() -> None:
    """Уведомлению не отвечают даже ошибкой: клиенту не с чем её сопоставить."""
    assert handle(FakeTools(), {"jsonrpc": "2.0", "method": "чего-то/нет"}) is None


def test_unknown_method_with_id_is_method_not_found() -> None:
    assert answer_of(FakeTools(), rpc("чего-то/нет"))["error"]["code"] == METHOD_NOT_FOUND


# ──────────────────────────────────────────────────────────────────────────────
# Версия протокола
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", SUPPORTED_PROTOCOLS)
def test_supported_client_version_is_echoed(version: str) -> None:
    result = answer_of(FakeTools(), rpc("initialize", protocolVersion=version))["result"]
    assert result["protocolVersion"] == version


@pytest.mark.parametrize("version", ["1999-01-01", "2099-12-31", None, 20250618])
def test_unknown_client_version_gets_the_latest(version: object) -> None:
    params = {} if version is None else {"protocolVersion": version}
    result = answer_of(FakeTools(), rpc("initialize", **params))["result"]
    assert result["protocolVersion"] == SUPPORTED_PROTOCOLS[-1] == "2025-06-18"


def test_negotiate_matches_the_plan() -> None:
    assert negotiate("2025-06-18") == "2025-06-18"
    assert negotiate("1999-01-01") == "2025-06-18"


def test_initialize_names_the_toolset_and_the_real_version() -> None:
    """Версия в рукопожатии — версия пакета, а не литерал: литерал обманывает
    любую сверку по нему (так у движка разбора, CLAUDE.md)."""
    result = answer_of(FakeTools(), rpc("initialize"))["result"]
    assert result["serverInfo"] == {"name": "docpipe-проверка", "version": __version__}
    assert result["instructions"] == "проверочный сервер"
    assert result["capabilities"] == {"tools": {}}


def test_graph_server_speaks_the_shared_protocol(tmp_path: Path) -> None:
    """Сервер графа — набор инструментов общего протокола, а не своя копия."""
    toolset = GraphTools(Server(tmp_path / "graph.db", tmp_path))
    result = answer_of(toolset, rpc("initialize", protocolVersion="2025-03-26"))["result"]
    assert result["serverInfo"]["name"] == "docpipe-graph"
    assert result["protocolVersion"] == "2025-03-26"


def test_protocol_lives_in_one_module() -> None:
    """Второй сервер не копирует протокол: копия разошлась бы с этой на первой
    же правке. Признак копии — словарь JSON-RPC за пределами `docpipe/mcp.py`."""
    shared = Path("docpipe/mcp.py")
    for path in Path("docpipe").rglob("*.py"):
        if path == shared:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ('"jsonrpc"', "protocolVersion", "-32600", "-32601"):
            assert marker not in text, f"{path}: копия протокола MCP — «{marker}»"
