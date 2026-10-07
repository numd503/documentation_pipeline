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
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Final, Literal, NamedTuple

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

    `body_nonliteral` — поля объекта-тела, у которых значение есть, но не
    строковый литерал: `{ listInnerName: name }`, `{ listInnerName }`.
    Различителем прогон их не возьмёт, но поле, которое в одном вызове
    `'users'`, а в другом — параметр, меняется от вызова к вызову, и это
    признак обращения к реестру (`setup candidates registry-calls`). Без
    списка тело было бы слепо к форме, которую query видит по `{}`.
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


class _Resolved(NamedTuple):
    """Значение выражения или причина, по которой его нет.

    `call` — вызов, которым инициализирован `const` имени (`const url =
    this.apiUrl.buildUrl(…)`): значения у такого имени нет, но построитель
    адреса виден, и факт об обёртке (`ArgFact.callee`) берёт его отсюда.
    """

    value: str | None
    reason: str = ""
    call: Node | None = None


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
                return _Resolved(None, REASON_PARAMETER)
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
        """`this.x` -> литеральное поле своего класса; остальные — не восстановлены."""
        receiver = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
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
    """

    kind: ArgKind
    text: str
    value: str | None = None
    fields: dict[str, "ArgFact"] = field(default_factory=dict)
    callee: tuple[str, str] | None = None
    args: tuple["ArgFact", ...] = ()


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


def _arg_fact(node: Node, scope: _Scope, depth: int = 0) -> ArgFact:
    """Аргумент -> факт. Вложенные вызовы и объекты — не глубже `_ARG_DEPTH`."""
    text = _compact(node)
    literal = _literal_value(node)
    if literal is not None:
        return ArgFact("literal", text, literal)
    if node.type == "template_string":
        return ArgFact("template", text, _from_template(node, scope)[0])
    if node.type in ("identifier", "member_expression"):
        resolved = scope.value(node)
        if resolved.call is None:
            return ArgFact("identifier", text, resolved.value)
        return ArgFact(
            "identifier",
            text,
            resolved.value,
            callee=_callee(resolved.call),
            args=_call_args(resolved.call, scope, depth),
        )
    if node.type == "call_expression":
        return ArgFact("call", text, callee=_callee(node), args=_call_args(node, scope, depth))
    if node.type == "object":
        return ArgFact("object", text, fields=_object_fields(node, scope, depth))
    if node.type == "binary_expression":
        return ArgFact("other", text, _from_concatenation(node, scope)[0])
    return ArgFact("other", text)


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
            resolved = scope.value(child)
            found[_text(child)] = ArgFact("identifier", _text(child), resolved.value)
            continue
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None or key.type == "computed_property_name":
            continue
        found[_literal_value(key) or _text(key)] = _arg_fact(value, scope, depth + 1)
    return found


def _http_call(path: str, line: int, method: str, nodes: list[Node], scope: _Scope) -> RawCall:
    """Факт о вызове `HttpClient`: адрес — первый аргумент, тело — второй."""
    if not nodes:
        return RawCall(file=path, line=line, http_method=method, reason=REASON_NO_ARGUMENTS)
    first = nodes[0]
    body = nodes[1] if len(nodes) > 1 else None
    url, confidence, reason = _first_argument(first, scope)
    return RawCall(
        file=path,
        line=line,
        http_method=method,
        url=url,
        confidence=confidence,
        reason=reason,
        expression=_compact(first),
        body_fields=_body_fields(body) if body is not None else {},
        body_nonliteral=_body_nonliteral(body) if body is not None else (),
    )


def extract_call_facts(root: Node, path: str) -> CallFacts:
    """Факты о вызовах одного файла: HTTP-вызовы, кандидаты в обёртки, построители.

    От конфигурации не зависят. Вызов `HttpClient` (`HTTP_METHODS` при
    получателе из `HTTP_RECEIVERS`) — `RawCall`; любой другой вызов члена
    с аргументом-адресом (`address_positions`) или с аргументом — результатом
    вызова (`built_by_call`) — `CandidateCall`.

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
            found.append(_http_call(path, line, name.upper(), nodes, scope))
            address = _arg_fact(nodes[0], scope) if nodes else None
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
        if not address_positions(args) and not any(built_by_call(arg) for arg in args):
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


def _first_argument(node: Node, scope: _Scope) -> tuple[str | None, Confidence, str]:
    """Первый аргумент вызова -> `(url, уверенность, причина отказа)`."""
    literal = _literal_value(node)
    if literal is not None:
        return literal, "high", ""

    if node.type == "template_string":
        return _from_template(node, scope)

    if node.type == "binary_expression":
        url, reason = _from_concatenation(node, scope)
        return url, "medium", reason

    if node.type in ("identifier", "member_expression"):
        resolved = scope.value(node)
        return resolved.value, "high", resolved.reason

    return None, "high", REASON_EXPRESSION


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

    Инвариант: `len(calls) + len(unresolved)` равно общему числу найденных
    вызовов. Именно он делает число честным — иначе «восстановлено 58»
    не значит ничего.
    """

    calls: list[WebCall] = field(default_factory=list)
    unresolved: list[RawCall] = field(default_factory=list)
    registry_unresolved: list[WebCall] = field(default_factory=list)
    resolved: list[ResolvedCall] = field(default_factory=list)


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


def build_calls(
    raw: list[RawCall],
    *,
    rewrite: RewriteRule | None = None,
    registry: list[RegistryCall] | None = None,
    module: str = "",
) -> CallScan:
    """Превратить факты в ключи связи с учётом конфигурации.

    `module` только подписывает пары `resolved`: правило модуля приходит
    готовым в `rewrite`.
    """
    registry_by_route = registry_rules(registry)

    calls: list[WebCall] = []
    unresolved: list[RawCall] = []
    registry_unresolved: list[WebCall] = []
    resolved: list[ResolvedCall] = []

    for item in raw:
        if item.url is None:
            unresolved.append(item)
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
    )
