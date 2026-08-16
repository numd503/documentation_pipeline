"""HTTP-вызовы фронта: маршрут, честная оценка уверенности и обращения к реестру.

Ядро требования «связь фронт↔бэк». Замер на боевом модуле: 79 вызовов, из них
26 литералов, 27 шаблонов, 21 «переменная», и почти все «переменные» —
одна и та же схема, разрешаемая двумя формами констант.

Разделение на два шага принципиальное. `extract_calls` записывает **факты**
о вызове и от конфигурации не зависит — его результат можно кэшировать.
`build_calls` интерпретирует их с учётом `web.url_rewrite` и `web.registry_calls`:
смена настройки обязана менять ключи, не заставляя перечитывать исходники.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Literal

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from docpipe.model import Confidence, WebCall
from docpipe.route import RewriteRule, normalize_route, route_key

_LANGUAGE = Language(tsts.language_typescript())
_PARSER = Parser(_LANGUAGE)
_QUERIES_DIR = Path(__file__).parent / "queries"

# Имена получателей, при которых вызов считается HTTP-вызовом. Сравниваются
# в нижнем регистре и по последнему сегменту: `this.http`, `_http`,
# `this.httpClient`, `httpService`.
#
# Список — единственный фильтр, отделяющий 79 настоящих вызовов от 327
# посторонних. Расширять его нужно осознанно: каждое добавленное имя
# затягивает в отчёт всё, что так называется.
HTTP_RECEIVERS = frozenset({"http", "_http", "httpclient", "_httpclient", "httpservice"})

# Методы `HttpClient`, дающие глагол. `request(method, url)` не поддержан
# намеренно: первый аргумент там не URL, и обработка «через раз» дала бы
# вызовы с маршрутом `get`.
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration"})

_WHITESPACE = re.compile(r"\s+")


@cache
def _query(name: str) -> Query:
    return Query(_LANGUAGE, (_QUERIES_DIR / name).read_text(encoding="utf-8"))


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _compact(node: Node | None) -> str:
    return _WHITESPACE.sub(" ", _text(node)).strip()


# --------------------------------------------------------------------------------------
# Факты о вызове
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawCall:
    """Вызов, как он записан в коде, до применения конфигурации.

    `url` хранится **вместе с query-строкой**: различитель обращения к реестру
    (`?listInnerName=models`) живёт именно там, а нормализация маршрута его
    отбрасывает. Отбросить его здесь значило бы склеить все справочники
    в один ключ ещё до того, как конфигурация успеет что-то сказать.
    """

    file: str
    line: int
    http_method: str
    url: str | None = None
    confidence: Confidence = "high"
    reason: str = ""
    expression: str = ""
    body_fields: dict[str, str] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.url is not None


def _literal_value(node: Node) -> str | None:
    """Значение узла, если это строковый литерал. Иначе `None`.

    Шаблон без подстановок — тоже литерал: в Angular так пишут пути.
    """
    if node.type == "string":
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    if node.type == "template_string" and not any(
        c.type == "template_substitution" for c in node.children
    ):
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    return None


def _owning_class(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in _CLASS_NODES:
            return current
        current = current.parent
    return None


def _module_constants(root: Node) -> dict[str, str]:
    """Литеральные константы уровня файла: `export const auditUrl = '…'`.

    Через одну такую в боевом модуле идут десять вызовов одного сервиса.
    """
    found: dict[str, str] = {}
    for declarator in QueryCursor(_query("calls.scm")).captures(root).get("constant", []):
        # Локальные `const` внутри тел методов сюда тоже попадают, и это
        # правильно: `const url = '/api/x'; this.http.get(url)` — та же схема.
        name = _text(declarator.child_by_field_name("name"))
        value_node = declarator.child_by_field_name("value")
        if not name or value_node is None:
            continue
        value = _literal_value(value_node)
        if value is not None:
            found.setdefault(name, value)
    return found


def _field_constants(root: Node) -> dict[int, dict[str, str]]:
    """Литеральные поля классов: id класса -> {`this.baseUrl`: '/api/ml/…'}.

    По классу, а не по файлу: два сервиса в одном файле законно объявляют
    `baseUrl` с разными значениями, и общая таблица подставила бы в вызов
    чужую базу — молча и правдоподобно.
    """
    found: defaultdict[int, dict[str, str]] = defaultdict(dict)
    for definition in QueryCursor(_query("calls.scm")).captures(root).get("field", []):
        owner = _owning_class(definition)
        value_node = definition.child_by_field_name("value")
        name = _text(definition.child_by_field_name("name"))
        if owner is None or value_node is None or not name:
            continue
        value = _literal_value(value_node)
        if value is not None:
            found[owner.id][f"this.{name}"] = value
    return dict(found)


def _receiver_name(function: Node) -> str:
    """Последний сегмент получателя: `this.http` -> `http`, `_http` -> `_http`."""
    receiver = function.child_by_field_name("object")
    if receiver is None:
        return ""
    if receiver.type == "member_expression":
        return _text(receiver.child_by_field_name("property"))
    if receiver.type == "identifier":
        return _text(receiver)
    return ""


def _resolve_expression(node: Node, constants: dict[str, str]) -> str | None:
    """Литеральное значение выражения, если его удаётся восстановить."""
    direct = _literal_value(node)
    if direct is not None:
        return direct
    if node.type in ("identifier", "member_expression"):
        return constants.get(_compact(node))
    return None


def _from_template(node: Node, constants: dict[str, str]) -> tuple[str | None, str]:
    """Шаблонная строка -> `(url, причина отказа)`.

    Подстановка, значение которой удалось восстановить, подставляется литералом;
    остальные становятся `{}`.

    Отдельный случай — подстановка **в начале** строки: это база, а не сегмент
    пути. `` `${this.baseUrl}/saveAlternative` `` без разрешения базы даёт
    `{}/savealternative`, что не совпадёт ни с чем; такой вызов обязан быть
    невосстановленным, а не ключом-пустышкой.
    """
    parts: list[str] = []
    leading_unresolved = False

    for child in node.children:
        if child.type == "string_fragment":
            parts.append(_text(child))
        elif child.type == "template_substitution":
            inner = next((c for c in child.named_children), None)
            value = _resolve_expression(inner, constants) if inner is not None else None
            if value is None:
                if not parts:
                    leading_unresolved = True
                parts.append("{}")
            else:
                parts.append(value)

    if leading_unresolved:
        return None, "база в начале шаблона не восстановлена"
    return "".join(parts), ""


def _from_concatenation(node: Node, constants: dict[str, str]) -> tuple[str | None, str]:
    """Конкатенация `'api/x/' + id` -> `api/x/{}`. Без литеральной части — отказ."""
    parts: list[str] = []
    has_literal = False

    # Развёртка дерева конкатенации в порядке текста: `a + b + c` — это
    # `(a + b) + c`, и обход слева направо обязан дать именно `a, b, c`.
    stack = [node]
    flat: list[Node] = []
    while stack:
        current = stack.pop()
        if current.type == "binary_expression":
            left = current.child_by_field_name("left")
            right = current.child_by_field_name("right")
            if left is not None and right is not None:
                stack.extend([right, left])
                continue
        flat.append(current)

    for item in flat:
        value = _resolve_expression(item, constants)
        if value is None:
            parts.append("{}")
        else:
            has_literal = True
            parts.append(value)

    if not has_literal:
        return None, "конкатенация без литеральной части"
    return "".join(parts), ""


def _body_fields(node: Node) -> dict[str, str]:
    """Литеральные поля объекта-тела запроса: `{ listInnerName: 'users', … }`."""
    if node.type != "object":
        return {}
    found: dict[str, str] = {}
    for pair in node.named_children:
        if pair.type != "pair":
            continue
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if key is None or value is None:
            continue
        literal = _literal_value(value)
        if literal is not None:
            found[_literal_value(key) or _text(key)] = literal
    return found


def extract_calls(root: Node, path: str) -> list[RawCall]:
    """Факты о HTTP-вызовах одного файла. От конфигурации не зависят."""
    captures = QueryCursor(_query("calls.scm")).captures(root)
    module_constants = _module_constants(root)
    field_constants = _field_constants(root)

    found: list[RawCall] = []
    for call in captures.get("call", []):
        function = call.child_by_field_name("function")
        if function is None:
            continue

        method = _text(function.child_by_field_name("property")).lower()
        if method not in HTTP_METHODS:
            continue
        if _receiver_name(function).lower() not in HTTP_RECEIVERS:
            continue

        arguments = call.child_by_field_name("arguments")
        first = next((c for c in arguments.named_children), None) if arguments else None
        line = call.start_point[0] + 1
        if first is None:
            found.append(
                RawCall(
                    file=path,
                    line=line,
                    http_method=method.upper(),
                    reason="вызов без аргументов",
                )
            )
            continue

        owner = _owning_class(call)
        constants = dict(module_constants)
        constants.update(field_constants.get(owner.id, {}) if owner is not None else {})

        url, confidence, reason = _first_argument(first, constants)
        body = (
            arguments.named_children[1] if arguments and len(arguments.named_children) > 1 else None
        )

        found.append(
            RawCall(
                file=path,
                line=line,
                http_method=method.upper(),
                url=url,
                confidence=confidence,
                reason=reason,
                expression=_compact(first),
                body_fields=_body_fields(body) if body is not None else {},
            )
        )

    found.sort(key=lambda item: (item.line, item.http_method, item.expression))
    return found


def _first_argument(node: Node, constants: dict[str, str]) -> tuple[str | None, Confidence, str]:
    """Первый аргумент вызова -> `(url, уверенность, причина отказа)`."""
    literal = _literal_value(node)
    if literal is not None:
        return literal, "high", ""

    if node.type == "template_string":
        url, reason = _from_template(node, constants)
        return url, "high", reason

    if node.type == "binary_expression":
        url, reason = _from_concatenation(node, constants)
        return url, "medium", reason

    if node.type in ("identifier", "member_expression"):
        value = constants.get(_compact(node))
        if value is not None:
            return value, "high", ""
        return None, "high", "значение переменной не восстановлено"

    return None, "high", "выражение не восстановлено"


def scan_calls(source: bytes, path: str) -> list[RawCall]:
    """Разобрать файл и вытащить факты о вызовах."""
    return extract_calls(_PARSER.parse(source).root_node, path)


# --------------------------------------------------------------------------------------
# Интерпретация: конфигурация превращает факты в ключи
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryCall:
    """Маршрут платформы, у которого смысл вызова определяет не маршрут.

    `api/items/query` с `listInnerName` в теле и `api/items?listInnerName=…`
    в query — один маршрут на много смыслов. Ключ «метод + маршрут» склеил бы
    обращения к пользователям, к моделям и к справочникам в одну точку.
    """

    route: str
    discriminator_in: Literal["body", "query"]
    name: str
    kind: str = ""


@dataclass(frozen=True)
class CallScan:
    """Итог: что восстановлено, что нет и где смысл остался неизвестен.

    `registry_unresolved` — подмножество `calls`: маршрут известен, а различитель
    нет. Это состояние работы, а не дефект, и печатается оно числом.

    Инвариант: `len(calls) + len(unresolved)` равно общему числу найденных
    вызовов. Именно он делает число честным — иначе «восстановлено 58»
    не значит ничего.
    """

    calls: list[WebCall] = field(default_factory=list)
    unresolved: list[RawCall] = field(default_factory=list)
    registry_unresolved: list[WebCall] = field(default_factory=list)


def _query_value(url: str, name: str) -> str | None:
    """Значение параметра query-строки. `{}` (подстановка) значением не считается."""
    _, separator, query = url.partition("?")
    if not separator:
        return None
    for item in query.split("&"):
        key, has_value, value = item.partition("=")
        if key == name and has_value and value and "{}" not in value:
            return value
    return None


def build_calls(
    raw: list[RawCall],
    *,
    rewrite: RewriteRule | None = None,
    registry: list[RegistryCall] | None = None,
) -> CallScan:
    """Превратить факты в ключи связи с учётом конфигурации."""
    registry_by_route = {normalize_route(item.route): item for item in (registry or ())}

    calls: list[WebCall] = []
    unresolved: list[RawCall] = []
    registry_unresolved: list[WebCall] = []

    for item in raw:
        if item.url is None:
            unresolved.append(item)
            continue

        route = normalize_route(item.url, rewrite=rewrite)
        rule = registry_by_route.get(route)
        discriminator = ""
        if rule is not None:
            if rule.discriminator_in == "body":
                discriminator = item.body_fields.get(rule.name, "")
            else:
                discriminator = _query_value(item.url, rule.name) or ""

        call = WebCall(
            file=item.file,
            line=item.line,
            key=route_key(item.http_method, item.url, rewrite=rewrite, discriminator=discriminator),
            confidence=item.confidence,
        )
        calls.append(call)
        if rule is not None and not discriminator:
            registry_unresolved.append(call)

    return CallScan(calls=calls, unresolved=unresolved, registry_unresolved=registry_unresolved)
