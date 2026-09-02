"""Факты из тел: создание объекта и вызов с литералом.

Это **не** разбор тел. Тела не строятся в граф и не хранятся; извлекаются два
конкретных факта, каждый под названный вопрос:

- **где создают объект запроса** — без этого диспетчеризация по типу запроса
  не существует как ребро: обработчик известен из объявления, а место отправки
  живёт в теле, и сторонний разбор его не записывает (измерено на 0.6.0);
- **где имя таблицы записано литералом** — `ToTable("FOO")` в конфигурации
  модели и `CreateTable("FOO")` в миграции. Это единственное место, где имя
  таблицы **прочитано**, а не выведено по соглашению.

Приём тот же, что у `di.py`: один запрос по дереву и разбор смысла в Python.
Предикаты запроса тут не помогают — нужен разбор аргументов.
"""

import re
from typing import Final

from tree_sitter import Node

from docpipe.model import Construction, LiteralCall

# Члены, внутри которых факт может лежать. Совпадает с перечнем видов членов
# разбора: факт вне члена (в инициализаторе поля модуля, в top-level
# statements) тоже законен — тогда имя члена пустое, и это состояние,
# а не неизвестность.
_MEMBER_NODES: Final[frozenset[str]] = frozenset(
    {
        "method_declaration",
        "property_declaration",
        "field_declaration",
        "constructor_declaration",
        "event_declaration",
        "local_function_statement",
    }
)

_STRING = re.compile(r'^@?"(?P<value>.*)"$', re.DOTALL)

# Методы, у которых строковый аргумент — имя таблицы. Константа модуля,
# а не конфигурация: называются они так у одного ORM, и настраивать тут
# нечего. Список положительный: метод, которого здесь нет, литералом
# таблицы не считается — иначе именем таблицы станет любая строка в коде.
TABLE_METHODS: Final[frozenset[str]] = frozenset(
    {"ToTable", "CreateTable", "DropTable", "RenameTable", "ToView", "ToSqlQuery"}
)

# Методы, у которых строковый аргумент — это SQL. Тот же приём и тот же
# положительный список: строка, переданная в `LogInformation`, запросом
# не является, и отличить её можно только по тому, куда она передана.
SQL_METHODS: Final[frozenset[str]] = frozenset(
    {
        "ExecuteSqlRaw",
        "ExecuteSqlRawAsync",
        "ExecuteSqlInterpolated",
        "FromSqlRaw",
        "FromSqlInterpolated",
        "ExecuteScalar",
        "ExecuteReader",
        "ExecuteNonQuery",
        "Query",
        "QueryAsync",
        "QueryFirstOrDefault",
        "QuerySingleOrDefault",
        "Execute",
        "ExecuteAsync",
    }
)


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _member_node(node: Node) -> Node | None:
    """Узел члена, в теле которого записан факт."""
    current = node.parent
    while current is not None:
        if current.type in _MEMBER_NODES:
            return current
        current = current.parent
    return None


def _member_of(node: Node) -> str:
    """Член, в теле которого записан факт.

    Обходом вверх: факт под `#if` прямым потомком тела не является — та же
    асимметрия, из-за которой члены ищутся запросом, а не перебором детей.
    """
    current = node.parent
    while current is not None:
        if current.type in _MEMBER_NODES:
            name = current.child_by_field_name("name")
            if name is not None:
                return _text(name)
            # У поля имя лежит глубже: `field_declaration` содержит
            # `variable_declaration` со списком объявителей.
            declarator = next(
                (child for child in current.named_children if child.type == "variable_declaration"),
                None,
            )
            if declarator is not None:
                first = next(
                    (
                        child.child_by_field_name("name")
                        for child in declarator.named_children
                        if child.type == "variable_declarator"
                    ),
                    None,
                )
                return _text(first)
            return ""
        current = current.parent
    return ""


def _bare_type(text: str) -> str:
    """Имя типа без дженерика и без квалификатора: `Ns.Query<T>` → `Query`."""
    bracket = text.find("<")
    if bracket != -1:
        text = text[:bracket]
    return text.strip().rsplit(".", 1)[-1]


def extract_constructions(nodes: list[Node]) -> list[Construction]:
    """`new XQuery(...)` → тип и член, в котором это записано."""
    found: dict[tuple[str, str, int], Construction] = {}
    for node in nodes:
        type_node = node.child_by_field_name("type")
        name = _bare_type(_text(type_node))
        if not name:
            continue
        line = node.start_point[0] + 1
        member = _member_of(node)
        found[(name, member, line)] = Construction(type_name=name, member=member, line=line)
    return [found[key] for key in sorted(found)]


def _string_arguments(call: Node) -> list[str]:
    """Строковые аргументы вызова, как написаны, без кавычек.

    Нестроковый аргумент пропускается молча: он не «неизвестное имя таблицы»,
    а другое значение — схема из константы, флаг, лямбда.
    """
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    values: list[str] = []
    for child in arguments.named_children:
        matched = _STRING.match(_text(child).strip())
        if matched:
            values.append(matched.group("value"))
    return values


def _entity_of_member(call: Node) -> str:
    """Сущность из параметра метода: `Configure(EntityTypeBuilder<CatalogItem> b)`.

    Вторая форма записи конфигурации, и на реальном коде она **основная**:
    `builder.ToTable("Catalog")` внутри `IEntityTypeConfiguration<CatalogItem>`.
    Проверено на открытом репозитории — форму `Entity<X>().ToTable(…)` там
    не встретилось ни разу, а эту нашлось у каждой конфигурации.
    """
    member = _member_node(call)
    if member is None:
        return ""
    parameters = member.child_by_field_name("parameters")
    if parameters is None:
        return ""
    for parameter in parameters.named_children:
        type_node = parameter.child_by_field_name("type")
        if type_node is None or type_node.type != "generic_name":
            continue
        arguments = next(
            (child for child in type_node.children if child.type == "type_argument_list"), None
        )
        if arguments is None:
            continue
        first = next((child for child in arguments.named_children), None)
        found = _bare_type(_text(first))
        if found:
            return found
    return ""


def _generic_of_receiver(call: Node) -> str:
    """Аргумент дженерика у получателя: `b.Entity<Contract>().ToTable("FOO")`.

    Без него литерал имени таблицы известен, а **чьё** это имя — нет,
    и связать прочитанное имя с сущностью нечем. Ищется на один шаг влево
    по цепочке: получатель вызова — это тот самый `Entity<Contract>()`.
    """
    function = call.child_by_field_name("function")
    if function is None:
        return ""
    receiver = function.child_by_field_name("expression")
    if receiver is None or receiver.type != "invocation_expression":
        return ""
    inner = receiver.child_by_field_name("function")
    if inner is None:
        return ""
    name = inner.child_by_field_name("name") or inner
    if name.type != "generic_name":
        return ""
    arguments = next((c for c in name.children if c.type == "type_argument_list"), None)
    if arguments is None:
        return ""
    first = next((c for c in arguments.named_children), None)
    return _bare_type(_text(first))


def _called_name(call: Node) -> str:
    function = call.child_by_field_name("function")
    if function is None:
        return ""
    name = function.child_by_field_name("name")
    if name is None:
        return _text(function).rsplit(".", 1)[-1]
    if name.type == "generic_name":
        return _text(name.children[0])
    return _text(name)


def extract_literal_calls(calls: list[Node], wanted: frozenset[str]) -> list[LiteralCall]:
    """Вызовы названных методов, у которых есть строковые аргументы.

    Перечень методов — константа модуля, а не конфигурация: `ToTable`
    и `CreateTable` называются так у одного ORM, и настраивать тут нечего.
    Метод из этого списка без строкового аргумента попадает сюда с пустым
    списком аргументов: это не мусор, а неразрешённое имя таблицы, и считать
    такие обязан отчёт.
    """
    found: dict[tuple[str, tuple[str, ...], str, int], LiteralCall] = {}
    for call in calls:
        method = _called_name(call)
        if method not in wanted:
            continue
        # Вызов без строкового аргумента тоже записывается — с пустым
        # списком. Это не мусор, а **неразрешённое имя таблицы**: имя пришло
        # из константы или переменной, и посчитать такие обязан отчёт.
        # Выбросив их здесь, мы получили бы «неразрешённых нет» вместо числа.
        arguments = _string_arguments(call)
        line = call.start_point[0] + 1
        member = _member_of(call)
        key = (method, tuple(arguments), member, line)
        found[key] = LiteralCall(
            method=method,
            arguments=list(arguments),
            member=member,
            line=line,
            # Две формы записи, и обе встречаются: сущность в получателе
            # (`Entity<X>().ToTable(…)`) и сущность в параметре метода
            # (`Configure(EntityTypeBuilder<X> b)`). Вторая на реальном коде
            # оказалась основной — проверено на открытом репозитории.
            entity=_generic_of_receiver(call) or _entity_of_member(call),
        )
    return [found[key] for key in sorted(found)]
