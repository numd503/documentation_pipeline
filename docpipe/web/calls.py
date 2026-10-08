"""HTTP-вызовы фронта: маршрут, честная оценка уверенности и обращения к реестру.

Ядро требования «связь фронт↔бэк». Замер на боевом модуле: 79 вызовов, из них
26 литералов, 27 шаблонов, 21 «переменная», и почти все «переменные» —
одна и та же схема, разрешаемая двумя формами констант.

Разделение на два шага принципиальное. `extract_calls` (и полный вариант
`extract_call_facts`) записывает **факты** о вызове и от конфигурации не
зависит — его результат можно кэшировать. `build_calls` интерпретирует их
с учётом `web.url_rewrite` и `web.registry_calls`: смена настройки обязана
менять ключи, не заставляя перечитывать исходники.

Факты для обёрток (`CandidateCall`, `BuilderUse`, S18) — тоже извлечение:
вызов члена с аргументом, похожим на адрес, записывается без знания о том,
объявлена ли такая обёртка. Применять объявленные обёртки — дело
интерпретации (S19); перенос этого в извлечение сделал бы его результат
зависимым от настройки, и кэш, если его когда-нибудь заведут, отдавал бы
ключи по старым правилам.
"""

import re
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Final, Literal, NamedTuple

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from docpipe.config import (
    ArgRef,
    HttpWrapper,
    NotWrapper,
    UrlBuilder,
    method_matches,
    receiver_key,
)
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

    `body_nonliteral` — поля объекта-тела, у которых значение есть, но не
    строковый литерал: `{ listInnerName: name }`, `{ listInnerName }`.
    Различителем прогон их не возьмёт, но поле, которое в одном вызове
    `'users'`, а в другом — параметр, меняется от вызова к вызову, и это
    признак обращения к реестру (`setup candidates registry-calls`). Без
    списка тело было бы слепо к форме, которую query видит по `{}`.

    `address` — факт об аргументе-адресе (`ArgFact`): у прямого вызова —
    первый аргумент, у вызова через обёртку — аргумент из записи обёртки.
    По нему интерпретация находит построитель адреса (`callee`) и тело
    обёртки (`parameter`), не перечитывая исходник. `wrapper` и `builder` —
    через что вызов прошёл, как написано в коде (`HTTP.getVersioned`,
    `apiUrl.buildUrl`); заполняет их интерпретация (S19), извлечение — никогда.
    """

    file: str
    line: int
    http_method: str
    url: str | None = None
    confidence: Confidence = "high"
    reason: str = ""
    expression: str = ""
    body_fields: dict[str, str] = field(default_factory=dict)
    body_nonliteral: tuple[str, ...] = ()
    address: "ArgFact | None" = None
    wrapper: str = ""
    builder: str = ""

    @property
    def resolved(self) -> bool:
        return self.url is not None

    @property
    def via(self) -> str:
        """`HTTP.getVersioned`, `apiUrl.buildUrl` или оба через запятую, обёртка первой."""
        return ", ".join(part for part in (self.wrapper, self.builder) if part)


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


# --------------------------------------------------------------------------------------
# Откуда берётся значение имени
# --------------------------------------------------------------------------------------

# Причины отказа. Формулировки стабильны: по ним группирует невосстановленные
# сводка шва, и каждая называет **свой** вид, чтобы гипермедиа, изменяемое
# поле и построитель адреса не сливались в одно «значение не восстановлено».
REASON_VARIABLE: Final = "значение переменной не восстановлено"
REASON_PARAMETER: Final = "значение переменной — параметр функции"
REASON_REASSIGNED: Final = "переменная присваивается после объявления"
REASON_MUTABLE_FIELD: Final = "поле присваивается вне инициализатора"
REASON_EXPRESSION: Final = "выражение не восстановлено"
REASON_TEMPLATE_BASE: Final = "база в начале шаблона не восстановлена"
REASON_CONCAT_BASE: Final = "база в начале конкатенации не восстановлена"
REASON_CONCAT_NO_LITERAL: Final = "конкатенация без литеральной части"
REASON_NO_ARGUMENTS: Final = "вызов без аргументов"
# Вызов через объявленную обёртку или построитель (S19): чего нет в его аргументах.
REASON_WRAPPER_NO_ADDRESS: Final = "в объявленном аргументе обёртки нет адреса"
REASON_WRAPPER_METHOD: Final = "метод HTTP из аргумента обёртки не восстановлен"
REASON_WRAPPER_NO_VERB: Final = "в имени обёртки нет глагола HTTP"
REASON_BUILDER_NO_PATH: Final = "в объявленном аргументе построителя нет пути"

# Узлы, у которых есть свои параметры и своя область имён. Оба имени
# функции-выражения перечислены намеренно: версии грамматики называют её
# по-разному, а сравнение строк с несуществующим типом ничего не ломает.
_FUNCTION_NODES = frozenset(
    {
        "function_declaration",
        "function_expression",
        "function",
        "arrow_function",
        "method_definition",
        "generator_function_declaration",
        "generator_function",
    }
)
_DECLARATION_NODES = frozenset({"lexical_declaration", "variable_declaration"})
_ASSIGNMENT_NODES = frozenset({"assignment_expression", "augmented_assignment_expression"})
_NESTED_CLASS_NODES = _CLASS_NODES | {"class"}


@dataclass(frozen=True)
class ParameterRef:
    """Адрес — параметр функции: какой функции, какой по счёту и какое его поле.

    `getVersioned(http, url) { http.get(url) }` — `("getVersioned", 1, "")`;
    `request(config) { this.http.get(config.url) }` и `request({ url })` —
    `("request", 0, "url")`. По этой записи тело объявленной обёртки отличается
    от невосстановленного вызова продукта (S19): адрес тела — ровно тот
    параметр, который запись обёртки называет адресом. `function` — имя
    функции как в коде; у безымянной стрелки — пустая строка.
    """

    function: str
    index: int
    field: str = ""


class _Resolved(NamedTuple):
    """Значение выражения или причина, по которой его нет.

    `call` — вызов, которым инициализирован `const` имени (`const url =
    this.apiUrl.buildUrl(…)`): значения у такого имени нет, но построитель
    адреса виден, и факт об обёртке (`ArgFact.callee`) берёт его отсюда.
    `parameter` — имя оказалось параметром функции (или полем параметра).
    """

    value: str | None
    reason: str = ""
    call: Node | None = None
    parameter: ParameterRef | None = None


def _pattern_names(node: Node | None) -> set[str]:
    """Имена, которые связывает образец: `url`, `{ url }`, `[a, b]`, `...rest`.

    Значение по умолчанию и ключ образца имён не связывают: в `{ url = base }`
    связано `url`, а `base` — чужое имя, и пропустить его сюда значило бы
    объявить затенённым то, что затенено не было.
    """
    if node is None:
        return set()
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        return {_text(node)}
    found: set[str] = set()
    for index, child in enumerate(node.children):
        if child.is_named and node.field_name_for_child(index) not in ("right", "key", "type"):
            found |= _pattern_names(child)
    return found


def _parameter_names(function: Node) -> set[str]:
    """Имена параметров функции, включая единственный параметр стрелки без скобок."""
    single = function.child_by_field_name("parameter")
    if single is not None:
        return _pattern_names(single)
    parameters = function.child_by_field_name("parameters")
    found: set[str] = set()
    for parameter in parameters.named_children if parameters is not None else ():
        found |= _pattern_names(parameter.child_by_field_name("pattern"))
    return found


def _function_name(function: Node) -> str:
    """Имя функции, как его видит вызывающий: объявление, метод, стрелка в `const` или поле.

    У тела обёртки squidex это `export function getVersioned(…)` внутри
    `export module HTTP`, у abp — метод класса `request(…)`; стрелка,
    присвоенная полю или константе, берёт имя того, чему присвоена.
    Безымянная стрелка (`items.map(url => …)`) — пустая строка: такую
    функцию не зовут по имени, и обёрткой она быть не может.
    """
    name = function.child_by_field_name("name")
    if name is not None:
        return _text(name)
    parent = function.parent
    if parent is None:
        return ""
    if parent.type in ("variable_declarator", "public_field_definition"):
        target = parent.child_by_field_name("name")
        return _text(target) if target is not None and target.type != "object_pattern" else ""
    if parent.type == "pair":
        key = parent.child_by_field_name("key")
        return (_literal_value(key) or _text(key)) if key is not None else ""
    if parent.type in _ASSIGNMENT_NODES:
        left = parent.child_by_field_name("left")
        if left is not None and left.type == "member_expression":
            return _text(left.child_by_field_name("property"))
        return _text(left) if left is not None and left.type == "identifier" else ""
    return ""


def _pattern_key(pattern: Node, name: str) -> str:
    """Поле объекта-параметра, которое связывает имя: `{ url }`, `{ url: u }`, `{ url = x }`."""
    for child in pattern.named_children:
        if child.type == "shorthand_property_identifier_pattern" and _text(child) == name:
            return name
        if child.type == "object_assignment_pattern":
            left = child.child_by_field_name("left")
            if left is not None and _text(left) == name:
                return name
        if child.type == "pair_pattern":
            key, value = child.child_by_field_name("key"), child.child_by_field_name("value")
            if (
                key is not None
                and value is not None
                and value.type == "identifier"
                and _text(value) == name
            ):
                return _literal_value(key) or _text(key)
    return ""


def _parameter_ref(function: Node, name: str) -> ParameterRef | None:
    """Какой по счёту параметр функции связывает имя; сложный образец — `None`.

    Номер считается так же, как позиция аргумента у вызова: без комментариев
    и без псевдопараметра `this` TypeScript — его при вызове не передают.
    Вложенный образец (`[a, { url }]`) не разбирается: тело с таким адресом
    останется невосстановленным вызовом, а не исчезнет молча.
    """
    owner = _function_name(function)
    single = function.child_by_field_name("parameter")
    if single is not None:
        return ParameterRef(owner, 0) if single.type == "identifier" else None
    parameters = function.child_by_field_name("parameters")
    index = 0
    for parameter in parameters.named_children if parameters is not None else ():
        if parameter.type not in ("required_parameter", "optional_parameter"):
            continue
        pattern = parameter.child_by_field_name("pattern")
        if pattern is None or pattern.type == "this":
            continue
        if name in _pattern_names(pattern):
            if pattern.type == "identifier":
                return ParameterRef(owner, index)
            key = _pattern_key(pattern, name) if pattern.type == "object_pattern" else ""
            return ParameterRef(owner, index, key) if key else None
        index += 1
    return None


def _declarators(scope: Node) -> Iterator[Node]:
    """Объявления переменных прямо в этой области: блок, файл, заголовок `for`.

    `export const x = …` уровня файла лежит внутри `export_statement`, и без
    этого шага константа модуля, объявленная с `export`, не нашлась бы вовсе.
    """
    for child in scope.named_children:
        declaration = (
            child.child_by_field_name("declaration") if child.type == "export_statement" else child
        )
        if declaration is not None and declaration.type in _DECLARATION_NODES:
            yield from (
                item for item in declaration.named_children if item.type == "variable_declarator"
            )


def _assigned(scope: Node, name: str) -> bool:
    """Присваивают ли имени где-нибудь в области: `url = …`, `url += …`, `url++`."""
    stack = [scope]
    while stack:
        current = stack.pop()
        target = (
            current.child_by_field_name("left")
            if current.type in _ASSIGNMENT_NODES
            else current.child_by_field_name("argument")
            if current.type == "update_expression"
            else None
        )
        if target is not None and target.type == "identifier" and _text(target) == name:
            return True
        stack.extend(current.named_children)
    return False


def _assigned_fields(owner: Node) -> set[str]:
    """Поля, которым присваивают в теле класса: `this.fileSource = src` -> `fileSource`.

    Вложенный класс пропускается: `this` в нём — другой объект, и его
    присваивания к полям внешнего класса отношения не имеют.
    """
    found: set[str] = set()
    stack = list(owner.named_children)
    while stack:
        current = stack.pop()
        if current.type in _NESTED_CLASS_NODES:
            continue
        target = (
            current.child_by_field_name("left")
            if current.type in _ASSIGNMENT_NODES
            else current.child_by_field_name("argument")
            if current.type == "update_expression"
            else None
        )
        if target is not None and target.type == "member_expression":
            receiver = target.child_by_field_name("object")
            if receiver is not None and receiver.type == "this":
                found.add(_text(target.child_by_field_name("property")))
        stack.extend(current.named_children)
    return found


def _field_constants(root: Node) -> dict[int, dict[str, _Resolved]]:
    """Литеральные поля классов: id класса -> {`baseUrl`: '/api/ml/…'}.

    По классу, а не по файлу: два сервиса в одном файле законно объявляют
    `baseUrl` с разными значениями, и общая таблица подставила бы в вызов
    чужую базу — молча и правдоподобно.

    Поле с литералом — константа, только если ему не присваивают вне
    инициализатора. `public fileSource = ''` с `this.fileSource = src` в методе
    давало вызов `GET ''`: ключ, который выглядит настоящим и «почти» совпадает
    с любым маршрутом из одних параметров. `readonly` не освобождает от
    проверки: в конструкторе такое поле присвоить можно, и значение
    инициализатора тогда так же неверно.
    """
    found: defaultdict[int, dict[str, _Resolved]] = defaultdict(dict)
    assigned: dict[int, set[str]] = {}
    for definition in QueryCursor(_query("calls.scm")).captures(root).get("field", []):
        owner = _owning_class(definition)
        value_node = definition.child_by_field_name("value")
        name = _text(definition.child_by_field_name("name"))
        if owner is None or value_node is None or not name:
            continue
        value = _literal_value(value_node)
        if value is None:
            continue
        if owner.id not in assigned:
            assigned[owner.id] = _assigned_fields(owner)
        found[owner.id][name] = (
            _Resolved(None, REASON_MUTABLE_FIELD)
            if name in assigned[owner.id]
            else _Resolved(value)
        )
    return dict(found)


def _call_reason(call: Node) -> str:
    """Причина для `const url = this.apiUrl.buildUrl(…)`: значение — вызов.

    Имя вызова стоит в причине, а не только в выражении: по причине группирует
    сводка шва, и один построитель адреса даёт одну группу, а не пятьдесят.
    """
    function = call.child_by_field_name("function")
    if function is not None and function.type == "member_expression":
        receiver = _receiver_name(function)
        method = _text(function.child_by_field_name("property"))
        return f"значение переменной — вызов `{f'{receiver}.' if receiver else ''}{method}(…)`"
    if function is not None and function.type == "identifier":
        return f"значение переменной — вызов `{_text(function)}(…)`"
    return "значение переменной — вызов"


class _Scope:
    """Значения имён одного файла в точке вызова.

    Порядок поиска — лексический, как у компилятора: `const` ближайшего
    охватывающего блока, затем внешних блоков, затем уровня файла; `this.x` —
    поле своего класса. `const` **другой** функции не виден никогда. Раньше
    константы собирались по всему файлу через `setdefault`, и первый литерал
    `const url` подставлялся во все `this.http.get(url)` файла: на squidex
    `help.service.ts:42` получил маршрут метода со строки 45, и ни один
    счётчик этого не показал.
    """

    def __init__(self, root: Node) -> None:
        self._fields = _field_constants(root)

    def value(self, node: Node) -> _Resolved:
        literal = _literal_value(node)
        if literal is not None:
            return _Resolved(literal)
        # `{ url }` в объекте-запросе — то же имя, что `url`: его значение
        # ищется по тем же областям.
        if node.type in ("identifier", "shorthand_property_identifier"):
            return self._identifier(node)
        if node.type == "member_expression":
            return self._member(node)
        return _Resolved(None, REASON_EXPRESSION)

    def _identifier(self, site: Node) -> _Resolved:
        """Имя -> его объявление, вверх по областям от точки использования.

        Объявление в той же функции ниже вызова — «занятое» имя (TDZ):
        внешнее объявление с тем же именем им затенено, и брать его нельзя.
        Через границу функции порядок не важен: метод зовут после того,
        как файл выполнился, поэтому `const` модуля под классом законен.
        """
        name = _text(site)
        crossed_function = False
        scope = site.parent
        while scope is not None:
            if scope.type in _FUNCTION_NODES and name in _parameter_names(scope):
                return _Resolved(None, REASON_PARAMETER, parameter=_parameter_ref(scope, name))
            if scope.type == "for_in_statement" and name in _pattern_names(
                scope.child_by_field_name("left")
            ):
                return _Resolved(None, REASON_VARIABLE)
            if scope.type == "catch_clause" and name in _pattern_names(
                scope.child_by_field_name("parameter")
            ):
                return _Resolved(None, REASON_VARIABLE)

            for declarator in _declarators(scope):
                if name not in _pattern_names(declarator.child_by_field_name("name")):
                    continue
                if crossed_function or declarator.start_byte < site.start_byte:
                    return self._binding(declarator, scope, name)
                return _Resolved(None, REASON_VARIABLE)

            if scope.type in _FUNCTION_NODES:
                crossed_function = True
            scope = scope.parent
        return _Resolved(None, REASON_VARIABLE)

    @staticmethod
    def _binding(declarator: Node, scope: Node, name: str) -> _Resolved:
        """Значение объявленной переменной: литерал, вызов или отказ."""
        name_node = declarator.child_by_field_name("name")
        value_node = declarator.child_by_field_name("value")
        if name_node is None or name_node.type != "identifier" or value_node is None:
            # Деструктуризация или объявление без инициализатора.
            return _Resolved(None, REASON_VARIABLE)

        # `let` и `var` — константа, только пока им ничего не присваивают:
        # иначе значение инициализатора — лишь одно из возможных.
        declaration = declarator.parent
        keyword = declaration.children[0].type if declaration is not None else ""
        if keyword != "const" and _assigned(scope, name):
            return _Resolved(None, REASON_REASSIGNED)

        literal = _literal_value(value_node)
        if literal is not None:
            return _Resolved(literal)
        if value_node.type == "call_expression":
            return _Resolved(None, _call_reason(value_node), value_node)
        return _Resolved(None, REASON_VARIABLE)

    def _member(self, node: Node) -> _Resolved:
        """`this.x` -> литеральное поле своего класса; остальные — не восстановлены.

        `config.url` при параметре `config` — тоже не восстановлено, с той же
        причиной, но с отметкой «поле параметра»: так выглядит тело обёртки
        с объектом-запросом, и по отметке его узнаёт объявленная обёртка.
        """
        receiver = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if receiver is not None and receiver.type == "identifier" and prop is not None:
            root = self._identifier(receiver).parameter
            if root is None or root.field:
                return _Resolved(None, REASON_VARIABLE)
            return _Resolved(None, REASON_VARIABLE, parameter=replace(root, field=_text(prop)))
        owner = _owning_class(node)
        if receiver is None or receiver.type != "this" or prop is None or owner is None:
            return _Resolved(None, REASON_VARIABLE)
        return self._fields.get(owner.id, {}).get(_text(prop), _Resolved(None, REASON_VARIABLE))


# --------------------------------------------------------------------------------------
# Формы первого аргумента
# --------------------------------------------------------------------------------------


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


def _glued_tail(previous: Node, so_far: str) -> bool:
    """Прилипла ли последняя подстановка к концу литерального сегмента пути.

    `` `api/apps/search${buildQuery(q)}` `` — построитель query-строки, а не
    сегмент: `{}` на его месте дал бы `api/apps/search{}`, который не совпадёт
    ни точно, ни «почти» (`_fixed_segments` отбрасывает только сегменты,
    целиком равные `{}`). Подстановка после `?` или `#` сюда не относится:
    query и так срежет нормализация, и догадки там нет — уверенность падать
    не должна. А различитель реестра (`listInnerName=${type}`) остаётся `{}`
    и в факте вызова: по нему видно, что смысл задан подстановкой, а не забыт.
    """
    return (
        previous.type == "string_fragment"
        and not so_far.endswith("/")
        and "?" not in so_far
        and "#" not in so_far
    )


def _from_template(node: Node, scope: _Scope) -> tuple[str | None, Confidence, str]:
    """Шаблонная строка -> `(url, уверенность, причина отказа)`.

    Подстановка, значение которой удалось восстановить, подставляется литералом;
    остальные становятся `{}`.

    Отдельный случай — подстановка **в начале** строки: это база, а не сегмент
    пути. `` `${this.baseUrl}/saveAlternative` `` без разрешения базы даёт
    `{}/savealternative`, что не совпадёт ни с чем; такой вызов обязан быть
    невосстановленным, а не ключом-пустышкой.

    Второй — подстановка, прилипшая к концу последнего сегмента (`_glued_tail`):
    она отбрасывается, а уверенность падает до `medium` — что там построитель
    query, а не часть имени сегмента, это догадка.

    Правка здесь, а не в общей `route.normalize_route`: ту делит сторона .NET,
    и у неё `{}` в конце сегмента — параметр маршрута.
    """
    pieces = [c for c in node.children if c.type in ("string_fragment", "template_substitution")]
    parts: list[str] = []
    confidence: Confidence = "high"

    for index, child in enumerate(pieces):
        if child.type == "string_fragment":
            parts.append(_text(child))
            continue
        inner = next(iter(child.named_children), None)
        value = scope.value(inner).value if inner is not None else None
        if value is not None:
            parts.append(value)
        elif not parts:
            return None, "high", REASON_TEMPLATE_BASE
        elif index == len(pieces) - 1 and _glued_tail(pieces[index - 1], "".join(parts)):
            confidence = "medium"
        else:
            parts.append("{}")

    return "".join(parts), confidence, ""


def _from_concatenation(node: Node, scope: _Scope) -> tuple[str | None, str]:
    """Конкатенация `'api/x/' + id` -> `api/x/{}`.

    Отказ в трёх случаях: оператор не `+` (`url || 'api/x'` — выбор, а не
    склейка), нет ни одной восстановленной части, и не восстановлена первая
    часть. Последнее — та же база, что у шаблона: `this.base + '/api/apps'`
    давал `{}/api/apps` — ключ, который не совпадёт ни с чем, но выглядит
    настоящим.
    """
    operator = node.child_by_field_name("operator")
    if operator is None or operator.type != "+":
        return None, REASON_EXPRESSION

    # Развёртка дерева конкатенации в порядке текста: `a + b + c` — это
    # `(a + b) + c`, и обход слева направо обязан дать именно `a, b, c`.
    # Разворачивается только `+`: у `'a' + b * c` правая часть — одно значение.
    stack = [node]
    flat: list[Node] = []
    while stack:
        current = stack.pop()
        if current.type == "binary_expression":
            left = current.child_by_field_name("left")
            right = current.child_by_field_name("right")
            inner = current.child_by_field_name("operator")
            if left is not None and right is not None and inner is not None and inner.type == "+":
                stack.extend([right, left])
                continue
        flat.append(current)

    values = [
        _from_template(item, scope)[0]
        if item.type == "template_string"
        else scope.value(item).value
        for item in flat
    ]
    if all(value is None for value in values):
        return None, REASON_CONCAT_NO_LITERAL
    if values[0] is None:
        return None, REASON_CONCAT_BASE
    return "".join("{}" if value is None else value for value in values), ""


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


def _body_nonliteral(node: Node) -> tuple[str, ...]:
    """Поля объекта-тела со значением-выражением: `{ a: name }`, `{ a }` -> (`a`,).

    Вычисляемый ключ (`[k]: …`), спред и метод в счёт не идут: имени поля
    у них нет, и правило `registry_calls` на них не напишешь.
    """
    if node.type != "object":
        return ()
    found: set[str] = set()
    for child in node.named_children:
        if child.type == "shorthand_property_identifier":
            found.add(_text(child))
            continue
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None or key.type == "computed_property_name":
            continue
        if _literal_value(value) is None:
            found.add(_literal_value(key) or _text(key))
    return tuple(sorted(found))


# --------------------------------------------------------------------------------------
# Факты для обёрток и построителей адреса (S18)
# --------------------------------------------------------------------------------------

# Начала значения, по которым аргумент вызова «похож на адрес». Список
# короткий намеренно: каждое начало затягивает в кандидаты всё, что так
# начинается (`/` — уже и `router.navigateByUrl('/apps')`), а решает
# человек — кандидат только находка.
ADDRESS_PREFIXES: Final = ("/", "api/", "http://", "https://")

# Поле объекта-запроса, в котором обёртка держит адрес:
# `restService.request({ method: 'GET', url: '/api/…' })` у abp.
URL_FIELD: Final = "url"

# Глубина разбора вложенных вызовов и объектов в аргументе. Построителю
# адреса хватает одной ступени (`const url = this.apiUrl.buildUrl('/api/x')`);
# глубже — `.pipe(map(…), catchError(…))`, и стоимость растёт без пользы.
_ARG_DEPTH: Final = 2

ArgKind = Literal["literal", "template", "identifier", "object", "call", "other"]


@dataclass(frozen=True)
class ArgFact:
    """Аргумент вызова: как записан и что о его значении известно без настройки.

    `kind` — форма записи: `identifier` — имя или обращение к полю (`url`,
    `this.baseUrl`, `link.href`), `other` — всё прочее (конкатенация, стрелка,
    `new`). `value` — восстановленное значение: литерал, шаблон с `{}`
    на месте подстановок, `const` и поле по правилам S16, конкатенация;
    `None` — не восстановлено.

    `callee` и `args` — у вызова (`this.apiUrl.buildUrl('/api/x')`)
    и у имени, чей `const` инициализирован вызовом (`url` при `const url =
    this.apiUrl.buildUrl(…)`): значения у такого имени нет, а построитель
    виден. `callee` — `(последний сегмент получателя, метод)`; у функции без
    получателя — `("", имя)`. `fields` — поля объектного литерала.

    `confidence` и `reason` — то же, что дал бы этот аргумент первым у вызова
    `HttpClient`: значение с уверенностью или причина отказа (`REASON_*`).
    Вызов через обёртку (S19) становится вызовом с ними, и невосстановленный
    адрес обёртки называет причину теми же словами, что прямой вызов.
    `parameter` — значение есть параметр функции (или его поле): так
    выглядит адрес в теле обёртки.
    """

    kind: ArgKind
    text: str
    value: str | None = None
    fields: dict[str, "ArgFact"] = field(default_factory=dict)
    callee: tuple[str, str] | None = None
    args: tuple["ArgFact", ...] = ()
    confidence: Confidence = "high"
    reason: str = ""
    parameter: ParameterRef | None = None


@dataclass(frozen=True)
class CandidateCall:
    """Вызов члена, который не HTTP-вызов, но аргумент у него может быть адресом.

    `HTTP.getVersioned(this.http, url)`, `this.rest.request({ url: '/api/x' })` —
    такие вызовы не попадали ни в один счётчик: глагола `HttpClient` у них нет.
    Обёртку по имени не распознать (`get<T>(path, default)` у squidex — чтение
    состояния, без всякого HTTP), поэтому это факт, а не вызов: в разбор
    обёртка входит объявлением (`web.http_wrappers`, S19), никогда — догадкой.

    Записывается, если хотя бы один аргумент похож на адрес
    (`address_positions`) **или** его значение — результат вызова
    (`built_by_call`). Второе — ради гипермедии через построитель:
    `HTTP.requestVersioned(this.http, link.method, url)` при
    `const url = this.apiUrl.buildUrl(link.href)` на адрес не похож ничем,
    а так записаны все 47 видимых вызовов `requestVersioned` у squidex и все
    16 `this.http.request`. Построитель ли этот вызов, известно только
    по всему прогону (`BuilderUse`), поэтому решают кандидаты, а факт хранит
    и такие вызовы.

    Третье условие (S19) — аргументом передан сам `HttpClient`
    (`_client_argument`): иначе объявленная обёртка не увидела бы свой вызов
    с адресом-параметром функции, с `link.href` напрямую или с шаблоном,
    внутри которого построитель (squidex, `assets.service.ts:230`), — молча.
    Отбор кандидатов от этого не меняется: группа там требует позиции адреса.

    `receiver` — последний сегмент получателя как написан (`HTTP`, `rest`,
    `this` у вызова собственного метода); `method` — имя как написано.
    """

    file: str
    line: int
    receiver: str
    method: str
    args: tuple[ArgFact, ...]


@dataclass(frozen=True)
class BuilderUse:
    """Адрес вызова построен вызовом: `const url = this.apiUrl.buildUrl(…)`, затем `get(url)`.

    `file` и `line` — внешнего вызова (того, кому адрес передан); `receiver`
    и `method` — построителя; `arg` — аргумент-адрес внешнего вызова
    (`callee` и `args` в нём — построитель и его аргументы). `through` —
    пусто у прямого вызова `HttpClient`, `HTTP.getVersioned` у вызова-кандидата
    в обёртки: основная форма squidex — построитель **внутри** обёртки.
    """

    file: str
    line: int
    receiver: str
    method: str
    arg: ArgFact
    through: str = ""


@dataclass(frozen=True)
class CallFacts:
    """Все факты о вызовах одного файла: HTTP-вызовы и находки для обёрток."""

    calls: list[RawCall] = field(default_factory=list)
    candidates: list[CandidateCall] = field(default_factory=list)
    builders: list[BuilderUse] = field(default_factory=list)


def looks_like_address(value: str | None) -> bool:
    """Начинается ли значение как адрес: `/…`, `api/…`, `http://…`, `https://…`.

    После начала обязан быть хоть один знак: голый `'/'` — это
    `path.startsWith('/')`, `parts.join('/')`, `url.split('/')`, и без этого
    условия каждый такой вызов встал бы в кандидаты в обёртки.
    """
    if value is None:
        return False
    lowered = value.lower()
    return any(
        lowered.startswith(prefix) and len(lowered) > len(prefix) for prefix in ADDRESS_PREFIXES
    )


def _direct_address(arg: ArgFact) -> bool:
    """Значение аргумента само похоже на адрес (литерал, шаблон, `const`, конкатенация)."""
    return arg.kind != "object" and looks_like_address(arg.value)


def built_address(arg: ArgFact) -> bool:
    """Аргумент — значение вызова, у которого есть аргумент-адрес: построитель.

    Без этого основная форма squidex (`HTTP.getVersioned(this.http, url)`
    при `const url = this.apiUrl.buildUrl("api/…")`, 31 вызов `HTTP.*`)
    не стала бы кандидатом ни разу: у самого `url` значения нет.
    """
    return arg.callee is not None and any(_direct_address(inner) for inner in arg.args)


def built_by_call(arg: ArgFact) -> bool:
    """Значение аргумента — результат вызова: `url` при `const url = f(…)` или `a.b(…)` прямо.

    Прямой вызов — только вызов члена: функция без получателя прямо
    в аргументе — это `map(…)`, `catchError(…)` в каждом `.pipe(…)`, и все
    они записались бы фактами впустую. У имени, связанного с вызовом, такого
    шума нет, и функция без получателя там допустима.
    """
    if arg.callee is None:
        return False
    return arg.kind == "identifier" or (arg.kind == "call" and bool(arg.callee[0]))


def address_positions(args: Sequence[ArgFact]) -> list[str]:
    """Позиции аргументов-адресов: `1` — второй позиционный, `0.url` — поле первого.

    Номер нужен всегда: у `HTTP.getVersioned(this.http, url)` адрес второй,
    первым идёт сам `HttpClient`, и объявление обёртки «первый аргумент — адрес»
    дало бы маршрут `this.http`. Поле объекта — `url` с любым значением
    (`{ url }` тоже) или поле, значение которого само похоже на адрес.
    """
    found: list[str] = []
    for index, arg in enumerate(args):
        if _direct_address(arg) or built_address(arg):
            found.append(str(index))
        elif arg.kind == "object":
            found.extend(
                f"{index}.{name}"
                for name, value in sorted(arg.fields.items())
                if name == URL_FIELD or _direct_address(value)
            )
    return found


def _arguments(call: Node) -> list[Node]:
    """Аргументы вызова без комментариев.

    Комментарий — именованный узел грамматики и стоит среди аргументов:
    `get(/* версия */ this.http, url)` сдвинул бы позицию адреса на единицу,
    и объявление обёртки по номеру аргумента разошлось бы с кодом.
    """
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _candidate_receiver(function: Node) -> str:
    """Получатель вызова-кандидата: последний сегмент, `this`/`super` — словом.

    В отличие от `_receiver_name`, `this` здесь назван: `this.get('/api/x')` —
    обёртка, объявленная в самом классе (или в его базе), и группа без
    получателя склеила бы её с любым вызовом неизвестного объекта.
    Получатель-выражение (`inject(X).request(…)`, `items[0].get(…)`) — пустая
    строка: объявить обёртку на такой получатель нечем.
    """
    receiver = function.child_by_field_name("object")
    if receiver is None:
        return ""
    if receiver.type == "member_expression":
        return _text(receiver.child_by_field_name("property"))
    if receiver.type in ("identifier", "this", "super"):
        return _text(receiver)
    return ""


def _callee(call: Node) -> tuple[str, str]:
    """`(получатель, метод)` вызова; у функции без получателя — `("", имя)`."""
    function = call.child_by_field_name("function")
    if function is not None and function.type == "member_expression":
        return _candidate_receiver(function), _text(function.child_by_field_name("property"))
    if function is not None and function.type == "identifier":
        return "", _text(function)
    return "", ""


def _named_fact(node: Node, text: str, scope: _Scope, depth: int) -> ArgFact:
    """Имя или обращение к полю: значение по областям S16, построитель и параметр."""
    resolved = scope.value(node)
    call = resolved.call
    return ArgFact(
        "identifier",
        text,
        resolved.value,
        callee=_callee(call) if call is not None else None,
        args=_call_args(call, scope, depth) if call is not None else (),
        reason=resolved.reason,
        parameter=resolved.parameter,
    )


def _arg_fact(node: Node, scope: _Scope, depth: int = 0) -> ArgFact:
    """Аргумент -> факт. Вложенные вызовы и объекты — не глубже `_ARG_DEPTH`.

    Значение, уверенность и причина — те же, что у первого аргумента вызова
    `HttpClient` (`_http_call` берёт их отсюда): одна функция на обе роли,
    иначе адрес через обёртку разошёлся бы с прямым на первой же форме.
    """
    text = _compact(node)
    literal = _literal_value(node)
    if literal is not None:
        return ArgFact("literal", text, literal)
    if node.type == "template_string":
        value, confidence, reason = _from_template(node, scope)
        return ArgFact("template", text, value, confidence=confidence, reason=reason)
    if node.type in ("identifier", "member_expression"):
        return _named_fact(node, text, scope, depth)
    if node.type == "call_expression":
        return ArgFact(
            "call",
            text,
            callee=_callee(node),
            args=_call_args(node, scope, depth),
            reason=REASON_EXPRESSION,
        )
    if node.type == "object":
        return ArgFact(
            "object", text, fields=_object_fields(node, scope, depth), reason=REASON_EXPRESSION
        )
    if node.type == "binary_expression":
        value, reason = _from_concatenation(node, scope)
        return ArgFact("other", text, value, confidence="medium", reason=reason)
    return ArgFact("other", text, reason=REASON_EXPRESSION)


def _call_args(call: Node, scope: _Scope, depth: int) -> tuple[ArgFact, ...]:
    if depth >= _ARG_DEPTH:
        return ()
    return tuple(_arg_fact(item, scope, depth + 1) for item in _arguments(call))


def _object_fields(node: Node, scope: _Scope, depth: int) -> dict[str, ArgFact]:
    """Поля объектного литерала: `{ url: '/api/x', method }` -> `url`, `method`.

    Вычисляемый ключ, спред и метод пропускаются: имени поля у них нет,
    и позицию `0.поле` на них не объявишь.
    """
    if depth >= _ARG_DEPTH:
        return {}
    found: dict[str, ArgFact] = {}
    for child in node.named_children:
        if child.type == "shorthand_property_identifier":
            found[_text(child)] = _named_fact(child, _text(child), scope, depth + 1)
            continue
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None or key.type == "computed_property_name":
            continue
        found[_literal_value(key) or _text(key)] = _arg_fact(value, scope, depth + 1)
    return found


def _http_call(
    path: str, line: int, method: str, nodes: list[Node], address: ArgFact | None
) -> RawCall:
    """Факт о вызове `HttpClient`: адрес — первый аргумент (`address`), тело — второй."""
    if address is None:
        return RawCall(file=path, line=line, http_method=method, reason=REASON_NO_ARGUMENTS)
    body = nodes[1] if len(nodes) > 1 else None
    return RawCall(
        file=path,
        line=line,
        http_method=method,
        url=address.value,
        confidence=address.confidence,
        reason=address.reason,
        expression=address.text,
        body_fields=_body_fields(body) if body is not None else {},
        body_nonliteral=_body_nonliteral(body) if body is not None else (),
        address=address,
    )


def _client_argument(node: Node) -> bool:
    """Аргумент — сам `HttpClient`: `this.http`, `http`, `this.httpClient`.

    Вызов, которому передают клиента, — почти наверняка обёртка над ним
    (`HTTP.getVersioned(this.http, url)` у squidex). Без этого признака
    факт о таком вызове пишется, только если адрес похож на адрес: вызов
    обёртки с адресом-параметром, с `link.href` напрямую или с шаблоном,
    внутри которого построитель, объявленная обёртка не увидела бы — молча.

    Имя с заглавной — класс, а не экземпляр: `injector.get(HttpClient)`
    у abp передаёт токен внедрения, и обёрткой такой вызов не бывает.
    """
    if node.type == "identifier":
        name = _text(node)
        return not name[:1].isupper() and name.lower() in HTTP_RECEIVERS
    if node.type == "member_expression":
        receiver = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        return (
            receiver is not None
            and receiver.type == "this"
            and _text(prop).lower() in HTTP_RECEIVERS
        )
    return False


def extract_call_facts(root: Node, path: str) -> CallFacts:
    """Факты о вызовах одного файла: HTTP-вызовы, кандидаты в обёртки, построители.

    От конфигурации не зависят. Вызов `HttpClient` (`HTTP_METHODS` при
    получателе из `HTTP_RECEIVERS`) — `RawCall`; любой другой вызов члена
    с аргументом-адресом (`address_positions`), с аргументом — результатом
    вызова (`built_by_call`) или с самим `HttpClient` в аргументах
    (`_client_argument`) — `CandidateCall`.

    Граница проходит по паре «получатель + метод», а не по одному получателю:
    `HTTP.getVersioned(…)` в нижнем регистре — `http`, и фильтр по одному
    получателю выбросил бы именно обёртки squidex.
    """
    captures = QueryCursor(_query("calls.scm")).captures(root)
    scope = _Scope(root)

    found: list[RawCall] = []
    candidates: list[CandidateCall] = []
    builders: list[BuilderUse] = []
    for call in captures.get("call", []):
        function = call.child_by_field_name("function")
        if function is None:
            continue
        name = _text(function.child_by_field_name("property"))
        nodes = _arguments(call)
        line = call.start_point[0] + 1

        if name.lower() in HTTP_METHODS and _receiver_name(function).lower() in HTTP_RECEIVERS:
            address = _arg_fact(nodes[0], scope) if nodes else None
            found.append(_http_call(path, line, name.upper(), nodes, address))
            # Любой вызов, построивший адрес `HttpClient`, — построитель: адрес
            # там и так на первом месте, и аргумент-адрес у самого построителя
            # не обязателен (`buildUrl(link.href)` — гипермедиа, но через него).
            if address is not None and address.callee is not None:
                receiver, method = address.callee
                builders.append(BuilderUse(path, line, receiver, method, address))
            continue

        receiver = _candidate_receiver(function)
        if not receiver or not nodes:
            continue
        args = tuple(_arg_fact(node, scope) for node in nodes)
        if (
            not address_positions(args)
            and not any(built_by_call(arg) for arg in args)
            and not any(_client_argument(node) for node in nodes)
        ):
            continue
        candidates.append(CandidateCall(path, line, receiver, name, args))
        # У кандидата построитель — только тот, чей вызов сам похож на адрес:
        # `dto.toJSON()` в соседнем аргументе `requestVersioned` адреса не строит.
        builders.extend(
            BuilderUse(path, line, arg.callee[0], arg.callee[1], arg, f"{receiver}.{name}")
            for arg in args
            if arg.callee is not None and built_address(arg)
        )

    found.sort(key=lambda item: (item.line, item.http_method, item.expression))
    candidates.sort(key=lambda item: (item.line, item.receiver, item.method))
    builders.sort(key=lambda item: (item.line, item.receiver, item.method, item.through))
    return CallFacts(calls=found, candidates=candidates, builders=builders)


def extract_calls(root: Node, path: str) -> list[RawCall]:
    """Факты о HTTP-вызовах одного файла. От конфигурации не зависят."""
    return extract_call_facts(root, path).calls


def scan_calls(source: bytes, path: str) -> list[RawCall]:
    """Разобрать файл и вытащить факты о вызовах."""
    return extract_calls(_PARSER.parse(source).root_node, path)


def scan_call_facts(source: bytes, path: str) -> CallFacts:
    """Разобрать файл и вытащить все факты о вызовах, включая находки для обёрток."""
    return extract_call_facts(_PARSER.parse(source).root_node, path)


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
class ResolvedCall:
    """Восстановленный вызов вместе с фактом, из которого он построен.

    Ключ вызова query-строку и поля тела уже потерял: нормализация маршрута
    их отбрасывает, а в `WebCall` остаётся только различитель **настроенного**
    правила. Вопрос «какое поле у этого маршрута меняется от вызова к вызову»
    (`setup candidates registry-calls`) задаётся до настройки, поэтому ответ
    на него — в паре «факт → ключ». Только в памяти: `RawCall` не кэшируется
    и в манифест не идёт.

    `module` — имя модуля фронта, по правилам которого построен ключ
    (`web.url_rewrite` у каждого модуля свой).
    """

    raw: RawCall
    call: WebCall
    module: str = ""


@dataclass(frozen=True)
class CallScan:
    """Итог: что восстановлено, что нет и где смысл остался неизвестен.

    `registry_unresolved` — подмножество `calls`: маршрут известен, а различитель
    нет. Это состояние работы, а не дефект, и печатается оно числом.

    `resolved` параллелен `calls`: тот же порядок, `resolved[i].call is calls[i]`.

    `inside_wrappers` — тела объявленных обёрток (S19): вызов `HttpClient`
    в функции с именем обёртки, адрес которого — её параметр-адрес. Вызов
    продукта здесь не потерян: он восстановлен там, где обёртку зовут.

    Инвариант: `len(calls) + len(unresolved) + len(inside_wrappers)` равно
    общему числу найденных вызовов — прямых и через объявленные обёртки.
    Именно он делает число честным — иначе «восстановлено 58» не значит ничего.
    """

    calls: list[WebCall] = field(default_factory=list)
    unresolved: list[RawCall] = field(default_factory=list)
    registry_unresolved: list[WebCall] = field(default_factory=list)
    resolved: list[ResolvedCall] = field(default_factory=list)
    inside_wrappers: list[RawCall] = field(default_factory=list)


def query_parameters(url: str) -> tuple[dict[str, str], frozenset[str]]:
    """Параметры query-строки: литеральные значения и имена со значением-подстановкой.

    Литерал — то, что прогон возьмёт различителем: первое непустое значение
    без `{}`. Подстановка (`listInnerName={}` из `` `…=${type}` ``) — имя,
    у которого значение есть, но задано выражением; имя с литералом сюда
    не попадает, даже если в другом месте строки оно стоит с подстановкой.
    Имя с `{}` и параметр без значения (`?flag`, `?a=`) не идут никуда:
    правило на них не напишешь.
    """
    _, separator, query = url.partition("?")
    if not separator:
        return {}, frozenset()
    literal: dict[str, str] = {}
    substituted: set[str] = set()
    for item in query.split("&"):
        key, has_value, value = item.partition("=")
        if not key or "{}" in key:
            continue
        if has_value and value and "{}" not in value:
            literal.setdefault(key, value)
        elif "{}" in value:
            substituted.add(key)
    return literal, frozenset(substituted - literal.keys())


def _query_value(url: str, name: str) -> str | None:
    """Значение параметра query-строки. `{}` (подстановка) значением не считается."""
    return query_parameters(url)[0].get(name)


def registry_rules(registry: list[RegistryCall] | None) -> dict[str, RegistryCall]:
    """Маршрут → правило ровно так, как их сверяет прогон.

    Маршрут правила нормализуется **без** `url_rewrite`, маршрут вызова —
    с преобразованием своего модуля. Поэтому правило пишут в форме после
    преобразования: `api/items/query` при `add_prefix: /gw` не сработает
    никогда, и это будет выглядеть как «инструмент не нашёл».

    Два правила на один маршрут — действует последнее: загрузка повтор
    маршрута не отвергает. Отметка «уже в настройке» у кандидатов читает
    этот же словарь, чтобы значить «прогон это правило применит».
    """
    return {normalize_route(item.route): item for item in (registry or ())}


def discriminator_of(item: RawCall, rule: RegistryCall) -> str:
    """Различитель, который правило даст этому вызову; пустая строка — не найден."""
    if rule.discriminator_in == "body":
        return item.body_fields.get(rule.name, "")
    return _query_value(item.url or "", rule.name) or ""


# Схема в начале строки: `https://`, `http://`, `wss://`. Ищется **в начале**,
# а не где угодно: `api/redirect?to=https://x` — относительный адрес.
_ABSOLUTE_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://(?P<authority>[^/?#]*)")


def url_host(url: str) -> str:
    """Хост абсолютного адреса в нижнем регистре; у относительного — пустая строка.

    Ключ связи хоста не содержит (`normalize_route` его срезает), и без этого
    поля вызов `https://ext.example.org/feed.json` неотличим от обращения
    к своему `feed.json`: «вызов без эндпоинта» внутри своего бэка. Хост
    нужен правилу внешнего адресата, а не сопоставлению. Порт и учётные
    данные отбрасываются: правило пишут на хост, а не на `host:8443`.
    """
    match = _ABSOLUTE_URL.match(url.strip())
    if match is None:
        return ""
    authority = match.group("authority").rpartition("@")[2]
    if authority.startswith("["):
        # IPv6: двоеточия внутри скобок — часть адреса, а не порт.
        closing = authority.find("]")
        host = authority[: closing + 1] if closing != -1 else authority
    else:
        host = authority.partition(":")[0]
    return host.lower()


# --------------------------------------------------------------------------------------
# Обёртки и построители адреса (S19): записи человека превращают факты в вызовы
# --------------------------------------------------------------------------------------


class WrapperConflict(ValueError):
    """Вызов совпал с двумя записями `web.http_wrappers` — отказ прогона.

    Точные `method` загрузка сверяет сама; пересечение регулярок видно только
    на вызове. Выбрать «первую в файле» — то же правило порядка, от которого
    отказались в классификации: перестановка двух записей меняла бы ключи.
    `ValueError` — ошибка настройки, код 2, как у опечатки в `docpipe.yaml`.
    """

    def __init__(
        self, call: "CandidateCall", rules: Sequence[HttpWrapper], message: str = ""
    ) -> None:
        self.call = call
        labels = ", ".join(sorted(rule.label for rule in rules))
        super().__init__(
            message
            or f"web.http_wrappers: вызов {call.receiver}.{call.method} ({call.file}:{call.line})"
            f" совпал с несколькими записями: {labels}. Запись на вызов одна — сузьте"
            " `method_regex` или оставьте одну"
        )


class NotWrapperConflict(WrapperConflict):
    """Вызов совпал и с обёрткой, и с «не обёрткой» (`web.not_wrappers`) — отказ прогона.

    Загрузка отвергает такую пару, когда её видно по записям (точное имя или
    одинаковая регулярка); пересечение двух разных регулярок видно только
    на вызове. Подкласс `WrapperConflict`: три места, ловящие отказ прогона
    (`web scan`, `symbols --lang ts`, `SetupContext.web`), ловят его той же
    веткой — «ошибка конфигурации», а не «ручной состав страниц».
    """

    def __init__(self, call: "CandidateCall", wrapper: HttpWrapper, declined: NotWrapper) -> None:
        super().__init__(
            call,
            [wrapper],
            f"вызов {call.receiver}.{call.method} ({call.file}:{call.line}) совпал и с обёрткой"
            f" {wrapper.label} (web.http_wrappers), и с «не обёрткой» {declined.label}"
            " (web.not_wrappers). Вызов либо обёртка, либо нет — сузьте `method_regex`"
            " или оставьте одну запись",
        )


def name_matches(rule: HttpWrapper | NotWrapper, name: str) -> bool:
    """Имя метода (или функции-тела) совпало с записью: `method` точно, `method_regex` целиком."""
    return method_matches(rule.method, rule.method_regex, name)


def wrapper_matches(rule: HttpWrapper | NotWrapper, receiver: str, method: str) -> bool:
    """Вызов `receiver.method` — эта запись. Получатель — последний сегмент без регистра.

    Одно сравнение у обёртки и у «не обёртки»: иначе запись `window.open`
    совпадала бы в одном ключе и не совпадала в другом.
    """
    return receiver_key(receiver) == receiver_key(rule.receiver) and name_matches(rule, method)


def not_wrapper_for(
    receiver: str, method: str, rules: Sequence[NotWrapper]
) -> tuple[int, NotWrapper] | None:
    """Первая запись `web.not_wrappers`, накрывшая вызов, и её номер (с нуля).

    Первая в порядке файла, как у секции `link`: исход у всех «не обёрток»
    один, и спорят они только о причине; точный повтор отвергла загрузка.
    """
    return next(
        (
            (index, rule)
            for index, rule in enumerate(rules)
            if wrapper_matches(rule, receiver, method)
        ),
        None,
    )


def wrapper_for(call: CandidateCall, wrappers: Sequence[HttpWrapper]) -> HttpWrapper | None:
    """Запись обёртки, с которой совпал вызов; две и больше — `WrapperConflict`."""
    matched = [rule for rule in wrappers if wrapper_matches(rule, call.receiver, call.method)]
    if len(matched) > 1:
        raise WrapperConflict(call, matched)
    return matched[0] if matched else None


def builder_for(callee: tuple[str, str], builders: Sequence[UrlBuilder]) -> UrlBuilder | None:
    """Запись построителя для вызова `(получатель, метод)`; повтор отвергла загрузка."""
    receiver, method = callee
    return next(
        (
            rule
            for rule in builders
            if receiver_key(rule.receiver) == receiver_key(receiver) and rule.method == method
        ),
        None,
    )


def _argument(args: Sequence[ArgFact], ref: ArgRef) -> ArgFact | None:
    """Аргумент по записи: позиция и, если названо, поле объектного литерала."""
    if ref.arg >= len(args):
        return None
    found = args[ref.arg]
    if not ref.field:
        return found
    return found.fields.get(ref.field) if found.kind == "object" else None


# Слово имени: `getVersioned` → `get`, `GetItems` → `Get`, `get_items` → `get`.
_NAME_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def leading_verb(name: str) -> str:
    """Ведущий глагол HTTP в имени метода в верхнем регистре; глагола нет — пустая строка.

    Слово, а не префикс: `getter` и `options` в `optionsMenu` — разные
    вещи, и `getter` глаголом не считается.
    """
    word = next(iter(_NAME_WORD.findall(name)), "").lower()
    return word.upper() if word in HTTP_METHODS else ""


def _wrapper_method(call: CandidateCall, rule: HttpWrapper) -> tuple[str, str, str]:
    """Метод вызова через обёртку: `(метод, причина отказа, выражение)`."""
    source = rule.http_method
    if source.fixed:
        return source.fixed.upper(), "", ""
    if source.from_name:
        verb = leading_verb(call.method)
        return (verb, "", "") if verb else ("", REASON_WRAPPER_NO_VERB, call.method)
    assert source.arg is not None  # ровно один способ — проверено загрузкой
    found = _argument(call.args, ArgRef(arg=source.arg, field=source.field))
    value = (found.value or "").upper() if found is not None else ""
    if value.lower() in HTTP_METHODS:
        return value, "", ""
    return "", REASON_WRAPPER_METHOD, found.text if found is not None else ""


def _qualified(receiver: str, method: str) -> str:
    return f"{receiver}.{method}" if receiver else method


def with_builder(raw: RawCall, builders: Sequence[UrlBuilder]) -> RawCall:
    """Адрес — значение объявленного построителя: путь из его аргумента `path`.

    Срабатывает только у невосстановленного адреса, чей факт — вызов
    построителя (`const url = this.apiUrl.buildUrl('/api/apps')` или вызов
    прямо в аргументе). Путь из данных (`buildUrl(link.href)`) остаётся
    невосстановленным — с причиной и выражением **пути**, а не `url`:
    после объявления построителя вопрос уже не в нём.
    """
    address = raw.address
    if raw.url is not None or address is None or address.callee is None:
        return raw
    rule = builder_for(address.callee, builders)
    if rule is None:
        return raw
    builder = _qualified(*address.callee)
    path = _argument(address.args, rule.path)
    if path is None:
        return replace(raw, reason=REASON_BUILDER_NO_PATH, builder=builder)
    if path.value is None:
        return replace(raw, reason=path.reason, expression=path.text, builder=builder)
    return replace(raw, url=path.value, confidence=path.confidence, reason="", builder=builder)


def through_wrapper(
    call: CandidateCall, rule: HttpWrapper, builders: Sequence[UrlBuilder] = ()
) -> RawCall:
    """Вызов через объявленную обёртку -> вызов с адресом и методом по записи.

    Дальше он идёт обычным путём (`url_rewrite`, `registry_calls`,
    нормализация) — вместе с прямыми вызовами. Причина отказа по адресу
    важнее причины по методу: у гипермедиа (`link.method`, `link.href`)
    не восстановлено оба, и называть надо адрес — как у прямого вызова.
    """
    method, method_reason, method_text = _wrapper_method(call, rule)
    wrapper = _qualified(call.receiver, call.method)
    address = _argument(call.args, rule.url)
    if address is None:
        return RawCall(
            file=call.file,
            line=call.line,
            http_method=method,
            reason=REASON_WRAPPER_NO_ADDRESS,
            wrapper=wrapper,
        )
    raw = with_builder(
        RawCall(
            file=call.file,
            line=call.line,
            http_method=method,
            url=address.value,
            confidence=address.confidence,
            reason=address.reason,
            expression=address.text,
            address=address,
            wrapper=wrapper,
        ),
        builders,
    )
    if raw.url is not None and not method:
        return replace(
            raw, url=None, confidence="high", reason=method_reason, expression=method_text
        )
    return raw


def inside_wrapper(raw: RawCall, wrappers: Sequence[HttpWrapper]) -> bool:
    """Невосстановленный вызов — тело объявленной обёртки.

    Имя охватывающей функции совпало с записью, и адрес — ровно тот её
    параметр (с полем), который запись называет адресом: `http.get(url)`
    в `getVersioned(http, url)` при `url: {arg: 1}`. Получатель записи здесь
    не сверяется: у тела в `export module HTTP` получателя нет, а у метода
    класса получатель на месте вызова — имя поля (`rest`), а не класса.
    """
    parameter = raw.address.parameter if raw.address is not None else None
    if raw.url is not None or parameter is None or not parameter.function:
        return False
    return any(
        name_matches(rule, parameter.function)
        and rule.url.arg == parameter.index
        and rule.url.field == parameter.field
        for rule in wrappers
    )


def build_calls(
    raw: list[RawCall],
    *,
    rewrite: RewriteRule | None = None,
    registry: list[RegistryCall] | None = None,
    module: str = "",
    candidates: Sequence[CandidateCall] = (),
    wrappers: Sequence[HttpWrapper] = (),
    builders: Sequence[UrlBuilder] = (),
    not_wrappers: Sequence[NotWrapper] = (),
) -> CallScan:
    """Превратить факты в ключи связи с учётом конфигурации.

    `module` только подписывает пары `resolved`: правило модуля приходит
    готовым в `rewrite`.

    Обёртки и построители (S19) применяются здесь, а не в извлечении:
    вызов-кандидат, совпавший с записью `web.http_wrappers`, становится
    вызовом (`through_wrapper`), адрес от объявленного построителя получает
    путь (`with_builder`), тело обёртки уходит в `inside_wrappers`. Без
    записей результат байт в байт прежний.

    `not_wrappers` здесь только сверяются: вызов, совпавший и с обёрткой,
    и с «не обёрткой», — `NotWrapperConflict` (S24b). На ключи они не влияют.
    """
    registry_by_route = registry_rules(registry)

    interpreted = [with_builder(item, builders) for item in raw]
    converted: list[RawCall] = []
    for candidate in candidates:
        wrapper = wrapper_for(candidate, wrappers)
        if wrapper is None:
            continue
        declined = not_wrapper_for(candidate.receiver, candidate.method, not_wrappers)
        if declined is not None:
            raise NotWrapperConflict(candidate, wrapper, declined[1])
        converted.append(through_wrapper(candidate, wrapper, builders))
    if converted:
        # Порядок файла и строки, как у прямых вызовов: тот же ключ, которым
        # упорядочены факты одного файла, — существующие вызовы не переставятся.
        interpreted = sorted(
            [*interpreted, *converted],
            key=lambda item: (item.file, item.line, item.http_method, item.expression),
        )

    calls: list[WebCall] = []
    unresolved: list[RawCall] = []
    registry_unresolved: list[WebCall] = []
    resolved: list[ResolvedCall] = []
    inside: list[RawCall] = []

    for item in interpreted:
        if item.url is None:
            (inside if inside_wrapper(item, wrappers) else unresolved).append(item)
            continue

        route = normalize_route(item.url, rewrite=rewrite)
        rule = registry_by_route.get(route)
        discriminator = discriminator_of(item, rule) if rule is not None else ""

        call = WebCall(
            file=item.file,
            line=item.line,
            key=route_key(item.http_method, item.url, rewrite=rewrite, discriminator=discriminator),
            confidence=item.confidence,
            host=url_host(item.url),
            via=item.via,
        )
        calls.append(call)
        resolved.append(ResolvedCall(raw=item, call=call, module=module))
        if rule is not None and not discriminator:
            registry_unresolved.append(call)

    return CallScan(
        calls=calls,
        unresolved=unresolved,
        registry_unresolved=registry_unresolved,
        resolved=resolved,
        inside_wrappers=inside,
    )
