"""Таблица роутов Angular: дерево страниц и якорь страницы.

Якорь страницы — **маршрут**, а не имя компонента: `/models/loader/quiz` знает
пользователь, на него ведёт закладка, его же пишет аналитик в бизнес-документе.
`LoaderQuizComponent` не знает никто снаружи, и переименование класса не должно
ничего ломать.

Главная особенность здесь межфайловая. В боевом модуле `loadChildren` — ноль,
`RouterModule.forRoot/forChild` — ноль, а 26 записей `path:` собраны **спредом
импортированного массива** из `routesPath/*.ts`. Разбор одного `app.routes.ts`
даёт два пути из двадцати шести, и молча: ошибок разбора нет, узлы просто
не появляются. Поэтому обе межфайловые формы обязательны.
"""

import posixpath
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from docpipe.model import RouteEntry
from docpipe.route import normalize_route
from docpipe.web.resolve import (
    ResolveContext,
    module_fqn,
    resolve_exported,
    resolve_name,
    resolve_specifier,
)

_LANGUAGE = Language(tsts.language_typescript())
_PARSER = Parser(_LANGUAGE)
_QUERIES_DIR = Path(__file__).parent / "queries"

# Аннотации типа, по которым массив считается таблицей роутов. Опознание
# по содержимому («массив объектов с ключом `path`») ловило бы любой конфиг
# меню, а таких в модуле больше, чем таблиц роутов.
_ROUTE_TYPES = frozenset({"Routes", "Route[]", "Array<Route>"})

# Ключи ветки, которые не порождают страницу.
_WILDCARD = "**"


@cache
def _query(name: str) -> Query:
    return Query(_LANGUAGE, (_QUERIES_DIR / name).read_text(encoding="utf-8"))


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _literal(node: Node | None) -> str | None:
    """Значение строкового литерала без кавычек. Не литерал — `None`."""
    if node is None:
        return None
    if node.type == "string":
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    if node.type == "template_string" and not any(
        c.type == "template_substitution" for c in node.children
    ):
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    return None


def _pair(obj: Node, key: str) -> Node | None:
    """Значение поля объекта по имени ключа."""
    for pair in obj.named_children:
        if pair.type != "pair":
            continue
        name = pair.child_by_field_name("key")
        if name is not None and (_literal(name) or _text(name)) == key:
            return pair.child_by_field_name("value")
    return None


@dataclass(frozen=True)
class RouteTable:
    """Массив роутов: файл, имя константы и узел массива."""

    file: str
    name: str
    node: Node


@dataclass
class RouteScan:
    """Собранные страницы.

    Невосстановленный маршрут — **состояние**, а не отсутствие узла: пропав
    из вывода, такая ветка занизила бы знаменатель покрытия страниц, и отчёт
    показал бы успех там, где его нет.
    """

    entries: list[RouteEntry] = field(default_factory=list)

    @property
    def unresolved(self) -> int:
        return sum(1 for entry in self.entries if entry.route_unresolved)


class _Trees:
    """Разобранные деревья по требованию. Один файл разбирается один раз."""

    def __init__(self, sources: dict[str, bytes]) -> None:
        self._sources = sources
        self._trees: dict[str, Node] = {}

    def root(self, path: str) -> Node | None:
        if path not in self._trees:
            source = self._sources.get(path)
            if source is None:
                return None
            self._trees[path] = _PARSER.parse(source).root_node
        return self._trees[path]


def _tables_of(root: Node, path: str) -> list[RouteTable]:
    """Таблицы роутов, объявленные в файле."""
    found: list[RouteTable] = []
    for declarator in QueryCursor(_query("routes.scm")).captures(root).get("table", []):
        annotation = declarator.child_by_field_name("type")
        value = declarator.child_by_field_name("value")
        name = _text(declarator.child_by_field_name("name"))
        if value is None or value.type != "array" or not name:
            continue
        if _text(annotation).removeprefix(":").strip() not in _ROUTE_TYPES:
            continue
        found.append(RouteTable(file=path, name=name, node=value))
    return found


def _pairs_named(root: Node, key: str) -> list[Node]:
    """Значения всех пар объекта с заданным ключом, где бы они ни лежали."""
    found: list[Node] = []
    for pair in QueryCursor(_query("routes.scm")).captures(root).get("pair", []):
        name = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if value is not None and _text(name) == key:
            found.append(value)
    return found


def _referenced_names(root: Node) -> set[str]:
    """Имена таблиц, на которые ссылается этот файл: спред и `loadChildren`.

    Нужны, чтобы отличить корневую таблицу от дочерней. Опознавать корень
    по `provideRouter`/`RouterModule.forRoot` нельзя: в боевом модуле обоих
    нет вовсе, а дерево при этом собрано.
    """
    captures = QueryCursor(_query("routes.scm")).captures(root)
    names = {_text(node) for node in captures.get("spread", [])}
    names.update(_lazy_name(value) for value in _pairs_named(root, "loadChildren"))
    return {name for name in names if name}


def _lazy_name(value: Node) -> str:
    """Имя, которое возвращает ленивая стрелка.

    `() => import('./x').then(m => m.ROUTES)` — здесь две стрелки, и нужна
    внутренняя: тело внешней это `import(…).then(…)`, у которого свойство
    называется `then`. Реализация, берущая первое попавшееся свойство,
    подставит `then` и в имя таблицы, и в имя компонента — правдоподобно
    и целиком неверно.
    """
    stack = [value]
    while stack:
        current = stack.pop()
        if current.type == "arrow_function":
            body = current.child_by_field_name("body")
            if body is not None and body.type == "member_expression":
                return _text(body.child_by_field_name("property"))
            if body is not None and body.type == "identifier":
                return _text(body)
        stack.extend(current.children)
    return ""


def _component_fqn(name: str, file: str, ctx: ResolveContext) -> str:
    """FQN компонента, если его объявление удалось найти; иначе имя как есть."""
    declared = resolve_name(name, file, ctx)
    return f"{module_fqn(declared)}.{name}" if declared else name


def _lazy_target(value: Node, file: str, ctx: ResolveContext) -> tuple[str | None, str]:
    """`() => import('./x').then(m => m.Y)` -> `(файл, имя)`.

    Короткая форма `() => Y` тоже сюда: файла в ней нет, а имя есть.
    """
    specifier: str | None = None
    name = _lazy_name(value)

    stack = [value]
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            function = current.child_by_field_name("function")
            arguments = current.child_by_field_name("arguments")
            if function is not None and _text(function) == "import" and arguments is not None:
                literal = next(
                    (_literal(c) for c in arguments.named_children if _literal(c) is not None), None
                )
                specifier = specifier or literal
        stack.extend(current.children)

    if specifier is None:
        return None, name

    target = resolve_specifier(specifier, file, ctx.config, ctx.files)
    return target.path, name


def _array_of(name: str, file: str, trees: _Trees, ctx: ResolveContext) -> RouteTable | None:
    """Найти массив роутов по имени, разрешив имя в файл-объявление."""
    declared = resolve_name(name, file, ctx)
    return _table_in(declared, name, trees) if declared else None


def _table_in(file: str, name: str, trees: _Trees) -> RouteTable | None:
    root = trees.root(file)
    if root is None:
        return None
    return next((table for table in _tables_of(root, file) if table.name == name), None)


def _join(prefix: str, segment: str) -> str:
    return posixpath.join(prefix, segment) if prefix else segment


def _walk(
    table: RouteTable,
    prefix: str,
    scan: RouteScan,
    trees: _Trees,
    ctx: ResolveContext,
    visited: set[tuple[str, str]],
) -> None:
    """Обойти таблицу, накапливая полный путь."""
    marker = (table.file, table.name)
    if marker in visited:
        return
    visited.add(marker)

    for element in table.node.named_children:
        if element.type != "object":
            continue
        _branch(element, prefix, table.file, scan, trees, ctx, visited)


def _branch(
    element: Node,
    prefix: str,
    file: str,
    scan: RouteScan,
    trees: _Trees,
    ctx: ResolveContext,
    visited: set[tuple[str, str]],
) -> None:
    path_node = _pair(element, "path")
    segment = _literal(path_node)

    if path_node is not None and segment is None:
        # Сегмент собран выражением: дойти по таблице сюда можно, а путь
        # собрать нельзя. Это состояние, а не отсутствие узла.
        scan.entries.append(
            RouteEntry(
                path=prefix,
                component=_component_of(element, file, ctx) or "",
                route_unresolved=True,
            )
        )
        return

    if segment == _WILDCARD or _pair(element, "redirectTo") is not None:
        # Ни то, ни другое страницей не является: `**` — заглушка, а редирект
        # ведёт на страницу, которая уже посчитана по своему маршруту.
        return

    full = _join(prefix, segment or "")

    component = _component_of(element, file, ctx)
    if component:
        scan.entries.append(RouteEntry(path=normalize_route(full), component=component))

    children = _pair(element, "children")
    if children is not None and children.type == "array":
        _children(children, full, file, scan, trees, ctx, visited)

    lazy = _pair(element, "loadChildren")
    if lazy is not None:
        target, name = _lazy_target(lazy, file, ctx)
        table = _table_in(target, name, trees) if target else _array_of(name, file, trees, ctx)
        if table is not None:
            _walk(table, full, scan, trees, ctx, visited)


def _children(
    array: Node,
    prefix: str,
    file: str,
    scan: RouteScan,
    trees: _Trees,
    ctx: ResolveContext,
    visited: set[tuple[str, str]],
) -> None:
    """Элементы `children`: свои объекты и спреды импортированных массивов."""
    for element in array.named_children:
        if element.type == "object":
            _branch(element, prefix, file, scan, trees, ctx, visited)
            continue
        if element.type == "spread_element":
            name = _text(next((c for c in element.named_children), None))
            table = _array_of(name, file, trees, ctx) if name else None
            if table is not None:
                _walk(table, prefix, scan, trees, ctx, visited)


def _component_of(element: Node, file: str, ctx: ResolveContext) -> str:
    """Компонент ветки: `component: X` либо любая из форм `loadComponent`."""
    direct = _pair(element, "component")
    if direct is not None and direct.type == "identifier":
        return _component_fqn(_text(direct), file, ctx)

    lazy = _pair(element, "loadComponent")
    if lazy is None:
        return ""

    target, name = _lazy_target(lazy, file, ctx)
    if not name:
        return ""
    if target is None:
        return _component_fqn(name, file, ctx)

    declared = resolve_exported(target, name, ctx)
    return f"{module_fqn(declared)}.{name}" if declared else name


def build_routes(sources: dict[str, bytes], ctx: ResolveContext) -> RouteScan:
    """Собрать дерево страниц из всех таблиц роутов.

    Корневой считается таблица, на которую **никто не ссылается**: ни спредом,
    ни `loadChildren`. Опознание по `provideRouter`/`RouterModule.forRoot`
    не годится — в боевом модуле их нет вовсе, а дерево собрано.
    """
    trees = _Trees(sources)
    tables: list[RouteTable] = []
    referenced: set[str] = set()

    for path in sorted(sources):
        root = trees.root(path)
        if root is None:
            continue
        tables.extend(_tables_of(root, path))
        referenced.update(_referenced_names(root))

    scan = RouteScan()
    visited: set[tuple[str, str]] = set()
    for table in sorted(tables, key=lambda item: (item.file, item.name)):
        if table.name in referenced:
            continue
        _walk(table, "", scan, trees, ctx, visited)

    scan.entries.sort(key=lambda entry: (entry.path, entry.component))
    return scan
