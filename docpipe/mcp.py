"""Общий протокол MCP поверх stdio (S26): одна реализация на все серверы `docpipe`.

Протокол реализован руками, без библиотеки, и это решение, а не лень.
Ограничение среды записано в плане трижды: прогон без установки зависимостей.
Сервер, которому нужен `pip install`, в закрытом контуре не запустится ни разу,
а весь протокол здесь — четыре метода JSON-RPC поверх stdio.

Серверов два (`graph serve`, `setup serve`), а протокол один: вторая копия
разошлась бы с первой на первой же правке — ровно так разошлись три копии
`_prepare` шага 2. Сервер описывает себя набором инструментов (`ToolSet`),
всё остальное — здесь.

Три свойства, каждое из которых ломается молча:

- **исключение внутри инструмента — ответ с `isError`, а не конец цикла.**
  Пока `call` не был обёрнут, `limit: "abc"` выходил из цикла `ValueError`,
  и агент терял все инструменты до перезапуска сессии — а читал это как
  «сервер не отвечает», то есть как поломку не там, где она есть;
- **запрос не той формы — ошибка JSON-RPC `-32600`, а не `AttributeError`.**
  `params: null` и запрос-список (пакет JSON-RPC) убивали сервер так же;
- **версия протокола согласуется с клиентом.** Захардкоженная версия
  отвечала новому клиенту старой, и он либо отключался, либо молча
  выключал то, чего в старой версии нет.
"""

import contextlib
import json
import sys
import traceback
from typing import Any, Final, Protocol, TextIO

from docpipe import __version__

# По возрастанию: последняя — та, что отдаётся клиенту с незнакомой версией.
# Спецификация MCP велит в этом случае назвать свою последнюю, а клиент сам
# решит, разговаривать ли на ней.
SUPPORTED_PROTOCOLS: Final[tuple[str, ...]] = ("2024-11-05", "2025-03-26", "2025-06-18")

# Коды JSON-RPC 2.0. Разбор строки (-32700) кодом не отвечается: на битую
# строку ответа нет вовсе (см. `serve`).
INVALID_REQUEST: Final[int] = -32600
METHOD_NOT_FOUND: Final[int] = -32601
INVALID_PARAMS: Final[int] = -32602
INTERNAL_ERROR: Final[int] = -32603


class ToolSet(Protocol):
    """Чем сервер отличается от другого сервера: имя, инструкция, инструменты.

    Имя и инструкция — свойства только для чтения, а не изменяемые атрибуты:
    иначе реализация с константой класса (`Final`) не подошла бы под протокол
    по правилам mypy, хотя ничего в него не пишет.
    """

    @property
    def server_name(self) -> str: ...

    @property
    def instructions(self) -> str: ...

    def tools(self) -> list[dict[str, Any]]: ...

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def negotiate(requested: object) -> str:
    """Версия протокола для ответа на `initialize`: версия клиента, если она
    поддержана, иначе последняя поддерживаемая."""
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOLS:
        return requested
    return SUPPORTED_PROTOCOLS[-1]


def handle(toolset: ToolSet, request: Any) -> dict[str, Any] | None:
    """Обработать один запрос JSON-RPC. `None` — ответа не нужно (уведомление).

    Из функции не выходит ни одно исключение `Exception`: ошибка инструмента
    становится ответом с `isError`, ошибка остального — `-32603`. Трассировка
    в обоих случаях идёт в stderr, потому что stdout — канал протокола.
    """
    if not isinstance(request, dict):
        # Пакет JSON-RPC (список) сюда тоже попадает: MCP 2025-06-18 пакеты
        # убрал, а разбирать их ради одной версии из трёх — второй цикл.
        return _error(None, INVALID_REQUEST, f"запрос — не объект JSON, а {_kind(request)}")
    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str):
        return _error(request_id, INVALID_REQUEST, "у запроса нет строкового поля method")
    params = request.get("params", {})
    if not isinstance(params, dict):
        # `params: null` — не «параметров нет», а нарушение формы: JSON-RPC
        # разрешает только объект или отсутствие поля.
        return _error(request_id, INVALID_REQUEST, f"params — не объект, а {_kind(params)}")

    # Уведомление — запрос без `id`. Ответа на него не бывает даже с ошибкой:
    # клиенту не с чем его сопоставить.
    notification = "id" not in request
    if method.startswith("notifications/"):
        return None

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _error(request_id, INVALID_PARAMS, f"name — не строка, а {_kind(name)}")
        if not isinstance(arguments, dict):
            return _error(
                request_id, INVALID_PARAMS, f"arguments — не объект, а {_kind(arguments)}"
            )
        result = _call_tool(toolset, name, arguments)
    else:
        try:
            outcome = _answer(toolset, method, params)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            return _error(
                request_id,
                INTERNAL_ERROR,
                f"внутренняя ошибка сервера на {method}: {type(error).__name__}: {error}",
            )
        if outcome is None:
            if notification:
                return None
            return _error(request_id, METHOD_NOT_FOUND, f"метод {method!r} не поддержан")
        result = outcome

    if notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(
    toolset: ToolSet, stream_in: TextIO | None = None, stream_out: TextIO | None = None
) -> None:
    """Цикл stdio. Одна строка — один JSON-RPC."""
    source = stream_in or sys.stdin
    sink = stream_out or sys.stdout
    for number, raw in enumerate(source, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            # Ответа нет: `id` у битой строки не прочесть, а ответ с `id: null`
            # клиент сопоставить не сможет. Но и молчать нельзя — иначе
            # «клиент шлёт не то» неотличимо от «сервер завис».
            print(
                f"{toolset.server_name}: строка {number} — не JSON, пропущена: {error}",
                file=sys.stderr,
            )
            continue
        answer = handle(toolset, request)
        if answer is not None:
            sink.write(_encode(answer) + "\n")
            sink.flush()


def _answer(toolset: ToolSet, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Ответ на метод, кроме `tools/call`. `None` — метод неизвестен."""
    if method == "initialize":
        return {
            "protocolVersion": negotiate(params.get("protocolVersion")),
            "capabilities": {"tools": {}},
            # Версия — настоящая версия пакета, а не литерал: захардкоженная
            # строка в рукопожатии (так у движка разбора) обманывает любую
            # сверку версий по ней.
            "serverInfo": {"name": toolset.server_name, "version": __version__},
            "instructions": toolset.instructions,
        }
    if method == "tools/list":
        return {"tools": toolset.tools()}
    if method == "ping":
        return {}
    return None


def _call_tool(toolset: ToolSet, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Вызвать инструмент; исключение — ответ с `isError`, а не выход из цикла."""
    try:
        # stdout — канал протокола: строка, напечатанная инструментом
        # (`print` в функции, которую делит с CLI), разорвала бы поток
        # JSON-RPC посреди ответа. Уводим её в stderr.
        with contextlib.redirect_stdout(sys.stderr):
            answer = toolset.call(name, arguments)
        # Сериализация — внутри `try`: ответ с `Path` или множеством падает
        # здесь, а не в цикле, и это тоже ошибка инструмента.
        text = json.dumps(answer, ensure_ascii=False, indent=2)
        is_error = "error" in answer
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        # Та же форма `{"error": …}`, что у ошибок, которые инструмент
        # возвращает сам: агент читает один формат, а не два.
        message = f"внутренняя ошибка инструмента {name}: {type(error).__name__}: {error}"
        text = json.dumps({"error": message}, ensure_ascii=False, indent=2)
        is_error = True
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _encode(answer: dict[str, Any]) -> str:
    """Строка ответа. Несериализуемый ответ — `-32603`, а не выход из цикла.

    Ответы инструментов сериализуются раньше, в `_call_tool`; сюда доходит
    только то, что собрал протокол, — например, описание инструмента
    с несериализуемым значением.
    """
    try:
        return json.dumps(answer, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        traceback.print_exc(file=sys.stderr)
        fallback = _error(
            answer.get("id"), INTERNAL_ERROR, f"ответ не сериализуется в JSON: {error}"
        )
        return json.dumps(fallback, ensure_ascii=False)


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _kind(value: object) -> str:
    """Имя типа JSON для сообщения: агенту `NoneType` ничего не скажет."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "логическое значение"
    if isinstance(value, list):
        return "массив"
    if isinstance(value, str):
        return "строка"
    if isinstance(value, int | float):
        return "число"
    return type(value).__name__
