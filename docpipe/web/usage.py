"""Обращения к членам зависимостей: ребро графа вызовов.

Отвечает на вопрос, который отличает документ страницы от инвентаря: не
«страница внедрила `ModelService`», а «страница зовёт `ModelService.byId`».
На боевом модуле разница между этими утверждениями — 51 эндпоинт против
единиц, и 43 эндпоинта из 61 попадали ровно на четыре страницы разом именно
потому, что список собирался по внедрению.

Разрешается **только однозначное**. Локальная переменная, глубокая цепочка,
получатель из выражения — всё это идёт в счётчики, а не в рёбра: ложное ребро
втянет в страницу чужой сервис и породит неверный раздел документа, а пропуск
всего лишь оставит раздел короче.

Чего этот разбор не видит и видеть не может: вызовы из шаблона. `(click)="save()"`
и `service.ready$ | async` живут в `.html`, грамматика Angular-шаблонов сюда
не входит. Ограничение печатается числом файлов с шаблонами, а не
обнаруживается потом по расхождению.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Query, QueryCursor

_LANGUAGE = Language(tsts.language_typescript())
_QUERIES_DIR = Path(__file__).parent / "queries"

# Функция внедрения Angular: `= inject(ModelService)`.
_INJECT = "inject"

_WHITESPACE = re.compile(r"\s+")


@cache
def _query(name: str) -> Query:
    return Query(_LANGUAGE, (_QUERIES_DIR / name).read_text(encoding="utf-8"))


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class RawUsage:
    """Обращение, как оно записано: получатель по имени, без типа.

    Тип получателя здесь неизвестен намеренно: разрешать его нужно таблицей
    импортов файла, а она собирается на уровень выше и на весь прогон разом.
    """

    file: str
    line: int
    receiver: str
    method: str


def extract_usages(root: Node, path: str) -> list[RawUsage]:
    """Факты об обращениях к членам получателей в одном файле.

    Берутся две формы: `this.x.method(…)` и `x.method(…)`. Всё остальное
    пропускается:

    - `this.a.b.method()` — цепочка через чужое поле, получатель неизвестен;
    - `this.a.method().pipe()` — у внешнего вызова получатель это вызов,
      и ребро даёт только внутренний;
    - `arr.map(...)`, `Math.max(...)` — получатель не резолвится в зависимость
      и отсеется на следующем шаге по типу, а не здесь по имени.

    Опциональный доступ (`this.a?.method()`) — та же форма дерева, поэтому
    ловится тем же запросом и даёт одно ребро, а не ноль.
    """
    found: list[RawUsage] = []
    for call in QueryCursor(_query("usage.scm")).captures(root).get("call", []):
        function = call.child_by_field_name("function")
        if function is None or function.type != "member_expression":
            continue

        method = _text(function.child_by_field_name("property"))
        receiver = function.child_by_field_name("object")
        if not method or receiver is None:
            continue

        name = _receiver(receiver)
        if name:
            found.append(
                RawUsage(file=path, line=call.start_point[0] + 1, receiver=name, method=method)
            )

    found.sort(key=lambda item: (item.line, item.receiver, item.method))
    return found


def _receiver(node: Node) -> str:
    """Имя получателя, если оно однозначно. Иначе пустая строка."""
    if node.type == "identifier":
        return _text(node)
    if node.type == "member_expression":
        # Только `this.x`: `this.a.b` — цепочка через чужое поле, и тип `b`
        # известен лишь после разбора типа `a`, которого здесь нет.
        target = node.child_by_field_name("object")
        if target is not None and target.type == "this":
            return _text(node.child_by_field_name("property"))
    return ""


def injected_fields(root: Node) -> list[tuple[int, str, str]]:
    """Поля, внедрённые функцией: `private readonly api = inject(ModelService)`.

    Возвращает тройки `(строка, имя поля, имя типа)`. Строка нужна вызывающему,
    чтобы отнести поле к своему классу: два сервиса в одном файле законно
    объявляют одноимённое поле с разными типами, и таблица на файл подставила бы
    чужой — молча и правдоподобно.
    """
    found: list[tuple[int, str, str]] = []
    for field in QueryCursor(_query("usage.scm")).captures(root).get("field", []):
        name = _text(field.child_by_field_name("name"))
        value = field.child_by_field_name("value")
        if not name or value is None or value.type != "call_expression":
            continue
        function = value.child_by_field_name("function")
        arguments = value.child_by_field_name("arguments")
        if function is None or _text(function) != _INJECT or arguments is None:
            continue
        argument = next((child for child in arguments.named_children), None)
        if argument is not None and argument.type == "identifier":
            found.append((field.start_point[0] + 1, name, _text(argument)))
    return sorted(found)


def constructor_bindings(signature: str) -> list[tuple[str, str]]:
    """`constructor(private http: HttpClient, route: ActivatedRoute)` -> пары.

    Порядок в TypeScript обратный C#: там `Тип имя`, здесь `имя: Тип`. Общая
    функция из `tree.parameter_types` не подходит — она разберёт `private`
    как тип и выдаст зависимость от модификатора.

    Дженерик и объединение срезаются по первому небуквенному символу:
    `Store<AppState>` — это `Store`, а не его параметр.
    """
    inner = signature.partition("(")[2].rpartition(")")[0]
    if not inner.strip():
        return []

    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in inner:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))

    bindings: list[tuple[str, str]] = []
    for part in parts:
        name, _, annotation = part.partition(":")
        # Имя параметра — последнее слово до двоеточия: перед ним стоят
        # модификаторы (`private readonly`) и декораторы (`@Inject(TOKEN)`).
        words = _WHITESPACE.sub(" ", name).strip().split(" ")
        parameter = words[-1] if words else ""
        type_name = re.split(r"[^\w$.]", annotation.strip(), maxsplit=1)[0] if annotation else ""
        if parameter and type_name:
            bindings.append((parameter, type_name))
    return bindings


def parameter_types(signature: str) -> list[str]:
    """Только типы — вход для зависимостей узла."""
    return [type_name for _, type_name in constructor_bindings(signature)]
