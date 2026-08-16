"""Цепочка NGXS: компонент диспатчит → стейт обрабатывает → сервис зовёт.

Названный пробел F14. Без него у страницы, которая ходит за данными
через стор, нет ни одного эндпоинта — при том, что в боевом модуле это
основная форма похода за данными, и «вызовов ноль» читалось бы как
«документировать нечего».

Звено, которого не хватало, уже лежит в манифесте: у члена стейта записан
атрибут `Action` с именем класса экшена. Поэтому связывание идёт резолвом
имени через таблицу импортов файла — надёжнее, чем по строке `type`, которую
разбор полей не берёт вовсе. Сама строка при этом нужна отдельно: она попадает
в `WebCall.via_action`, потому что переживает переименование класса.

Результат этой задачи — **рёбра того же графа**, что и у P02: страница
обращается к члену стейта, помеченному `@Action`. Отдельный вид связи
потребовал бы второго обхода в каждой задаче, которая ходит по графу.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Query, QueryCursor

_LANGUAGE = Language(tsts.language_typescript())
_QUERIES_DIR = Path(__file__).parent / "queries"

DISPATCH = "dispatch"

# Формы выборки из стора. `selectSignal` — форма новых версий; включена,
# потому что стоит ноль (имя метода) и её отсутствие означало бы потерянную
# половину связей на репозитории, который на неё перешёл.
SELECTORS = frozenset({"select", "selectSnapshot", "selectOnce", "selectSignal"})

# Декоратор поля, объявляющий выборку: `@Select(DebtState.items) items$`.
SELECT_DECORATOR = "Select"

# Поле экшена, несущее его тип.
_TYPE_FIELD = "type"

_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration"})


@cache
def _query(name: str) -> Query:
    return Query(_LANGUAGE, (_QUERIES_DIR / name).read_text(encoding="utf-8"))


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _literal(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    if node.type == "template_string" and not any(
        c.type == "template_substitution" for c in node.children
    ):
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    return None


@dataclass(frozen=True)
class RawDispatch:
    """`this.store.dispatch(new LoadInnerDebts(id))` — имя класса экшена."""

    file: str
    line: int
    action: str


@dataclass(frozen=True)
class RawSelect:
    """`@Select(DebtState.items)` или `store.select(DebtState.items)`."""

    file: str
    line: int
    owner: str  # имя класса стейта, как записано
    member: str  # имя селектора


def extract_dispatches(root: Node, path: str) -> list[RawDispatch]:
    """Диспатчи в файле.

    Массив разворачивается: `dispatch([new A(), new B()])` — одна форма
    для разбора и два ребра. Реализация, берущая первый аргумент как объект,
    на массиве дала бы ноль, и это ровно та форма, которой пишут пакетную
    загрузку экрана.
    """
    found: list[RawDispatch] = []
    for call in QueryCursor(_query("store.scm")).captures(root).get("call", []):
        function = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if function is None or arguments is None:
            continue
        if _text(function.child_by_field_name("property")) != DISPATCH:
            continue

        for argument in arguments.named_children:
            found.extend(
                RawDispatch(file=path, line=call.start_point[0] + 1, action=created)
                for created in _created(argument)
            )

    found.sort(key=lambda item: (item.line, item.action))
    return found


def _created(node: Node) -> list[str]:
    """Имена классов, создаваемых в выражении: `new A()` и `[new A(), new B()]`."""
    if node.type == "new_expression":
        constructor = node.child_by_field_name("constructor")
        if constructor is None or constructor.type != "identifier":
            return []
        name = _text(constructor)
        return [name] if name else []
    if node.type == "array":
        found: list[str] = []
        for element in node.named_children:
            found.extend(_created(element))
        return found
    return []


def extract_selects(root: Node, path: str) -> list[RawSelect]:
    """Выборки из стора: декоратором и вызовом.

    Обе формы дают одно и то же ребро «страница смотрит в этот стейт»,
    поэтому и собираются вместе: документ страницы должен назвать состояние
    независимо от того, каким из двух способов его читают.
    """
    captures = QueryCursor(_query("store.scm")).captures(root)
    found: list[RawSelect] = []

    for decorator in captures.get("decorator", []):
        call = next((child for child in decorator.named_children), None)
        if call is None or call.type != "call_expression":
            continue
        if _text(call.child_by_field_name("function")) != SELECT_DECORATOR:
            continue
        arguments = call.child_by_field_name("arguments")
        argument = next((c for c in arguments.named_children), None) if arguments else None
        pair = _member_pair(argument)
        if pair is not None:
            found.append(
                RawSelect(
                    file=path, line=decorator.start_point[0] + 1, owner=pair[0], member=pair[1]
                )
            )

    for call in captures.get("call", []):
        function = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if function is None or arguments is None:
            continue
        if _text(function.child_by_field_name("property")) not in SELECTORS:
            continue
        argument = next((c for c in arguments.named_children), None)
        pair = _member_pair(argument)
        if pair is not None:
            found.append(
                RawSelect(file=path, line=call.start_point[0] + 1, owner=pair[0], member=pair[1])
            )

    found.sort(key=lambda item: (item.line, item.owner, item.member))
    return found


def _member_pair(node: Node | None) -> tuple[str, str] | None:
    """`DebtState.items` -> `('DebtState', 'items')`. Иное — `None`."""
    if node is None or node.type != "member_expression":
        return None
    owner = node.child_by_field_name("object")
    member = node.child_by_field_name("property")
    if owner is None or owner.type != "identifier" or member is None:
        return None
    return _text(owner), _text(member)


def action_types(root: Node) -> dict[str, str]:
    """Класс экшена -> строка его типа: `LoadInnerDebts` -> `[Inner Debt] Load`.

    Именно строка переживает переименование класса, поэтому в `via_action`
    идёт она. Класса без такого поля это не отменяет — просто у него не будет
    литерала, и вызывающий подставит имя класса.
    """
    found: dict[str, str] = {}
    for field in QueryCursor(_query("store.scm")).captures(root).get("field", []):
        if _text(field.child_by_field_name("name")) != _TYPE_FIELD:
            continue
        value = _literal(field.child_by_field_name("value"))
        owner = _owning_class(field)
        if value is None or owner is None:
            continue
        name = _text(owner.child_by_field_name("name"))
        if name:
            found.setdefault(name, value)
    return found


def _owning_class(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in _CLASS_NODES:
            return current
        current = current.parent
    return None
