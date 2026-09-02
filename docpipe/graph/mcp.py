"""MCP-сервер: доставка форм вопроса агенту (G12).

Протокол реализован руками, без библиотеки, и это решение, а не лень.
Ограничение среды записано в плане трижды: прогон без установки зависимостей.
Сервер, которому нужен `pip install`, в закрытом контуре не запустится ни разу,
а весь протокол здесь — три метода JSON-RPC поверх stdio.

Три свойства, каждое из которых ломается молча:

- **набор инструментов повторяет набор форм один в один.** Расхождение
  запрещено: это два описания одного, и второе отстанет;
- **сервер сверяет поколение индекса на каждом запросе.** Сборка подменяет
  файл атомарно; сервер, держащий в памяти прежний индекс, отвечал бы
  по устаревшему без единого признака;
- **каждый ответ несёт признак неполноты**, относящийся к этому ответу.
"""

import json
import sys
from pathlib import Path
from typing import Any, Final, TextIO

from docpipe.graph.api import affects, card, overview, reaches, resolve, why
from docpipe.graph.api import path as path_form
from docpipe.graph.model import GraphIndex, GraphMeta
from docpipe.graph.reach import Reachability
from docpipe.graph.store import IndexVersionError, read_index, read_meta, read_reach

PROTOCOL: Final[str] = "2024-11-05"

# Инструкция «как здесь работать». Без неё агент зовёт не то и заключает,
# что инструмент бесполезен.
INSTRUCTIONS: Final[str] = """
Инструмент отвечает на вопросы о СВЯЗЯХ в репозитории: что достигает точка
входа, какие точки входа затронет изменение, как связаны две сущности.

Как работать:

1. начинайте с `docpipe_resolve`, если знаете имя приблизительно. Ответ
   называет, ЧЕМ совпало, — по этому видно, стоит ли переформулировать;
2. `docpipe_overview` и `docpipe_why` работают и без собранного индекса:
   это разведка и git. С них начинают на незнакомом репозитории;
3. `docpipe_reaches` и `docpipe_affects` читают предвычисленное, обхода
   не делают и потому быстры;
4. `docpipe_path` — единственная форма, которая обходит граф.

Чему верить без перепроверки: рёбрам вызова внутри одного языка и связям,
объявленным реестром. Чему верить с оглядкой: связям, полученным по имени
типа (в ответе они помечены уверенностью ниже единицы) и именам таблиц,
выведенным по соглашению.

Когда идти грепом: если ответ содержит признак неполноты с большим числом
в нужной вам категории, или если узел помечен как общий компонент —
инструмент честно говорит, что анализ не сужает.
""".strip()


class Server:
    """Состояние сервера: индекс и его поколение.

    Индекс перечитывается, когда поколение сменилось. Поколение выводится
    из содержимого, поэтому «сменилось» означает именно «пересобрали
    с другим результатом», а не «переписали тот же файл».
    """

    def __init__(self, index_path: Path, root: Path) -> None:
        self.index_path = index_path
        self.root = root
        self.generation = ""
        self.index: GraphIndex | None = None
        self.meta: GraphMeta | None = None
        self.reach: Reachability | None = None

    def refresh(self) -> str | None:
        """Перечитать индекс, если он сменился. Возвращает сообщение об ошибке."""
        if not self.index_path.is_file():
            return (
                f"индекса нет: {self.index_path}. Соберите его: "
                "docpipe graph build --root <репозиторий>"
            )
        try:
            meta = read_meta(self.index_path)
        except IndexVersionError as error:
            return str(error)
        except (OSError, ValueError) as error:
            return f"индекс не читается: {error}"
        if meta.generation != self.generation or self.index is None:
            self.index = read_index(self.index_path)
            self.reach = read_reach(self.index_path)
            self.meta = meta
            self.generation = meta.generation
        return None


def tools() -> list[dict[str, Any]]:
    """Описания инструментов. Один в один с формами вопроса."""
    text = {"type": "string"}
    return [
        {
            "name": "docpipe_resolve",
            "description": (
                "Что это такое, если известно только приблизительное имя. "
                "Ответ называет, чем совпало: точно, началом, подстрокой "
                "или триграммами."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"query": text},
                "required": ["query"],
            },
        },
        {
            "name": "docpipe_card",
            "description": "Что это за узел: вид, модуль, документ, рёбра, веерность.",
            "inputSchema": {
                "type": "object",
                "properties": {"node": text},
                "required": ["node"],
            },
        },
        {
            "name": "docpipe_reaches",
            "description": (
                "Что достигает эта точка входа: код, таблицы, швы. "
                "Читает предвычисленное, обхода не делает."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"root": text},
                "required": ["root"],
            },
        },
        {
            "name": "docpipe_affects",
            "description": (
                "Какие точки входа затронет изменение. Принимает и ключи узлов, "
                "и пути файлов — вывод `git diff --name-only` подходит как есть."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"keys": {"type": "array", "items": text}},
                "required": ["keys"],
            },
        },
        {
            "name": "docpipe_path",
            "description": "Как связаны две сущности. Единственная форма, которая обходит граф.",
            "inputSchema": {
                "type": "object",
                "properties": {"source": text, "target": text, "depth": {"type": "integer"}},
                "required": ["source", "target"],
            },
        },
        {
            "name": "docpipe_overview",
            "description": (
                "Что это за репозиторий: чем собран, что читать первым, где центр. "
                "Работает без собранного индекса — это разведка, а не граф."
            ),
            "inputSchema": {"type": "object", "properties": {"recon": text}},
        },
        {
            "name": "docpipe_why",
            "description": (
                "Что происходило с этим файлом: коммиты, авторы, исправления. "
                "Работает без индекса — это git."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"file": text, "limit": {"type": "integer"}},
                "required": ["file"],
            },
        },
    ]


def call(server: Server, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Выполнить форму. Отсутствие индекса — внятная ошибка, а не пустой ответ."""
    # Имя проверяется ДО индекса: неизвестный инструмент — ошибка протокола,
    # а не состояние репозитория, и отвечать на него «индекса нет» значит
    # отправить вызывающего чинить не то.
    if name not in {tool["name"] for tool in tools()}:
        return {"error": f"инструмент {name!r} неизвестен; список — в tools/list"}

    if name == "docpipe_overview":
        recon = arguments.get("recon")
        return overview(server.root, Path(recon) if recon else None)
    if name == "docpipe_why":
        return why(server.root, str(arguments.get("file", "")), int(arguments.get("limit", 10)))

    problem = server.refresh()
    if problem is not None:
        return {"error": problem}
    assert server.index is not None and server.meta is not None and server.reach is not None

    if name == "docpipe_resolve":
        return resolve(server.index_path, server.index, str(arguments.get("query", "")))
    if name == "docpipe_card":
        return card(server.index, server.meta, server.reach, str(arguments.get("node", "")))
    if name == "docpipe_reaches":
        return reaches(server.index, server.meta, server.reach, str(arguments.get("root", "")))
    if name == "docpipe_affects":
        keys = [str(value) for value in arguments.get("keys", [])]
        return affects(server.index, server.meta, server.reach, keys)
    if name == "docpipe_path":
        return path_form(
            server.index,
            str(arguments.get("source", "")),
            str(arguments.get("target", "")),
            int(arguments.get("depth", 12)),
        )
    return {"error": f"инструмент {name!r} известен, но не разобран: это дефект сервера"}


def handle(server: Server, request: dict[str, Any]) -> dict[str, Any] | None:
    """Обработать один запрос JSON-RPC. `None` — уведомление, ответа не нужно."""
    method = request.get("method", "")
    request_id = request.get("id")

    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "docpipe-graph", "version": "1"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        parameters = request.get("params", {})
        answer = call(server, parameters.get("name", ""), parameters.get("arguments", {}) or {})
        result = {
            "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False, indent=2)}],
            "isError": "error" in answer,
        }
    elif method.startswith("notifications/"):
        return None
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"метод {method!r} не поддержан"},
        }

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(
    server: Server, stream_in: TextIO | None = None, stream_out: TextIO | None = None
) -> None:
    """Цикл stdio. Одна строка — один JSON-RPC."""
    source = stream_in or sys.stdin
    sink = stream_out or sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        answer = handle(server, request)
        if answer is not None:
            sink.write(json.dumps(answer, ensure_ascii=False) + "\n")
            sink.flush()
