"""Разбор файла C# в `FileParseResult`.

Чистый синтаксис одного файла: никакой межфайловой логики, никакого резолва имён.
Всё, что требует знания о других файлах, живёт в `resolve.py`.

Парсер устойчив к синтаксически некорректному коду — tree-sitter возвращает дерево
с узлами `ERROR`, а не бросает исключение. Их количество попадает в `parse_errors`,
чтобы деградацию было видно, а не приходилось догадываться о ней по пустому выводу.
"""

import re
from collections import defaultdict
from functools import cache
from pathlib import Path

import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from docpipe.dotnet.di import extract_registrations
from docpipe.hashing import content_hash
from docpipe.model import (
    Attribute,
    FileParseResult,
    Member,
    MemberKind,
    RawDeclaration,
    SourceSpan,
    TypeKind,
)

_LANGUAGE = Language(tscs.language())
_PARSER = Parser(_LANGUAGE)
_QUERIES_DIR = Path(__file__).parent / "queries"

# Узел объявления -> вид типа. `record_declaration` покрывает и `record`,
# и `record struct`; различаются по наличию потомка `struct`.
_TYPE_KIND_BY_NODE: dict[str, TypeKind] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "struct_declaration": "struct",
    "record_declaration": "record",
    "enum_declaration": "enum",
}

# Узел члена -> вид. `event_field_declaration` — обычная форма события
# (`public event EventHandler Changed;`), `event_declaration` — форма с add/remove.
_MEMBER_KIND_BY_NODE: dict[str, MemberKind] = {
    "method_declaration": "method",
    "property_declaration": "property",
    "field_declaration": "field",
    "constructor_declaration": "constructor",
    "event_field_declaration": "event",
    "event_declaration": "event",
}

# Узлы, на которых заканчивается сигнатура: тело, стрелка, блок аксессоров
# или просто `;` у абстрактного члена. Всё, что до них, — часть сигнатуры,
# включая ограничения `where T : class`.
_SIGNATURE_TERMINATORS = frozenset({"block", "arrow_expression_clause", "accessor_list", ";"})

_WHITESPACE = re.compile(r"\s+")
_SPACE_AROUND_DOT = re.compile(r"\s*\.\s*")
_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL | re.IGNORECASE)
_XML_TAG = re.compile(r"<[^>]*>")


@cache
def _query(name: str) -> Query:
    return Query(_LANGUAGE, (_QUERIES_DIR / name).read_text(encoding="utf-8"))


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def normalize_type_text(raw: str) -> str:
    """Привести текст типа к канонической однострочной форме.

    Базовый список может занимать несколько строк:

        class AuthenticateEndpoint : EndpointBaseAsync
            .WithRequest<AuthenticateRequest>
            .WithActionResult<AuthenticateResponse>

    Без нормализации переносы строк и отступы попали бы в `signature_hash`,
    и переформатирование файла заставляло бы агента на шаге 3 перегенерировать
    документ впустую. Второй шаг (пробелы вокруг точки) обязателен: без него
    остаётся `EndpointBaseAsync .WithRequest<…>`, что не является каноническим C#.
    """
    collapsed = _WHITESPACE.sub(" ", raw).strip()
    return _SPACE_AROUND_DOT.sub(".", collapsed)


def _literal_text(node: Node) -> str:
    """Значение литерала без кавычек.

    `string_literal` хранит содержимое в потомке `string_literal_content`,
    а `verbatim_string_literal` — единый лист вместе с `@` и кавычками.
    """
    raw = _text(node)
    if node.type == "string_literal":
        parts = [_text(c) for c in node.children if c.type == "string_literal_content"]
        return "".join(parts) if parts else raw.strip('"')
    if node.type == "verbatim_string_literal":
        return raw.removeprefix("@").strip('"').replace('""', '"')
    if node.type == "raw_string_literal":
        return raw.strip('"')
    return raw


def _attributes(declaration: Node) -> list[Attribute]:
    """Атрибуты объявления.

    Имя приводится к простому: `[System.ComponentModel.Description]` -> `Description`,
    суффикс `Attribute` отбрасывается (`[RouteAttribute]` и `[Route]` неразличимы
    в C#, и правила классификации пишутся по простому имени).
    """
    found: list[Attribute] = []
    for attribute_list in (c for c in declaration.children if c.type == "attribute_list"):
        for attribute in (c for c in attribute_list.named_children if c.type == "attribute"):
            qualified = normalize_type_text(_text(attribute.child_by_field_name("name")))
            simple = qualified.rpartition(".")[2]
            name = simple.removesuffix("Attribute") or simple
            if not name:
                continue

            args: list[str] = []
            named: dict[str, str] = {}
            argument_list = next(
                (c for c in attribute.children if c.type == "attribute_argument_list"), None
            )
            if argument_list is not None:
                for argument in (
                    c for c in argument_list.named_children if c.type == "attribute_argument"
                ):
                    if not argument.named_children:
                        continue
                    value = argument.named_children[-1]
                    key = argument.child_by_field_name("name")
                    if key is not None and len(argument.named_children) > 1:
                        named[_text(key)] = _literal_text(value)
                    else:
                        args.append(_literal_text(value))

            found.append(Attribute(name=name, args=args, named_args=dict(sorted(named.items()))))
    return found


def _xml_doc(declaration: Node) -> str | None:
    """Содержимое `<summary>` из идущих подряд комментариев `///`.

    Комментарии — предшествующие **соседи** объявления, а не его потомки
    (атрибуты, наоборот, потомки, и поэтому не мешают).
    """
    lines: list[str] = []
    sibling = declaration.prev_sibling
    while sibling is not None and sibling.type == "comment":
        stripped = _text(sibling).lstrip()
        if not stripped.startswith("///"):
            break
        lines.append(stripped.removeprefix("///"))
        sibling = sibling.prev_sibling

    if not lines:
        return None

    match = _SUMMARY.search(" ".join(reversed(lines)))
    if match is None:
        return None
    return _WHITESPACE.sub(" ", _XML_TAG.sub(" ", match.group(1))).strip() or None


def _namespace_of(declaration: Node) -> str:
    """Полное имя namespace, охватывающего объявление.

    Две формы обрабатываются по-разному. Блочная (`namespace N { }`) —
    настоящий предок, её можно найти обходом вверх. Файловая (`namespace N;`)
    контейнером **не является**: объявления идут её соседями в `compilation_unit`,
    и обход предков её не найдёт. Смешивать формы в одном файле C# запрещает,
    поэтому проверяем блочную, а при её отсутствии — файловую.
    """
    parts: list[str] = []
    current = declaration.parent
    while current is not None:
        if current.type == "namespace_declaration":
            parts.append(_text(current.child_by_field_name("name")))
        current = current.parent
    if parts:
        return ".".join(reversed([p for p in parts if p]))

    unit = declaration
    while unit.parent is not None:
        unit = unit.parent
    for child in unit.children:
        if child.type == "file_scoped_namespace_declaration":
            return _text(child.child_by_field_name("name"))
    return ""


def _containing_type(declaration: Node) -> str | None:
    """Цепочка охватывающих типов, например `Outer.Middle` для `Inner`.

    Именно цепочка, а не ближайший предок: при двойной вложенности одного имени
    не хватит, чтобы собрать корректный FQN в `resolve.py`.
    """
    names: list[str] = []
    current = declaration.parent
    while current is not None:
        if current.type in _TYPE_KIND_BY_NODE:
            names.append(_text(current.child_by_field_name("name")))
        current = current.parent
    return ".".join(reversed([n for n in names if n])) or None


def _type_kind(declaration: Node) -> TypeKind:
    if declaration.type == "record_declaration" and any(
        child.type == "struct" for child in declaration.children
    ):
        return "record_struct"
    return _TYPE_KIND_BY_NODE[declaration.type]


def _type_parameters(declaration: Node) -> list[str]:
    parameter_list = next(
        (c for c in declaration.children if c.type == "type_parameter_list"), None
    )
    if parameter_list is None:
        return []
    return [
        _text(c.child_by_field_name("name"))
        for c in parameter_list.named_children
        if c.type == "type_parameter"
    ]


def _base_types(declaration: Node) -> list[str]:
    base_list = next((c for c in declaration.children if c.type == "base_list"), None)
    if base_list is None:
        return []
    return [normalize_type_text(_text(child)) for child in base_list.named_children]


def _owning_type(member: Node) -> Node | None:
    """Ближайший предок-объявление типа, то есть тип, которому принадлежит член.

    Обходом вверх, а не перебором детей тела: члены под `#if` прямыми детьми
    `declaration_list` не являются (см. `queries/members.scm`). Ближайшего
    предка достаточно — члены вложенного типа принадлежат ему, а не внешнему.
    """
    current = member.parent
    while current is not None:
        if current.type in _TYPE_KIND_BY_NODE:
            return current
        current = current.parent
    return None


def _signature(member: Node) -> str:
    """Сигнатура: текст объявления до тела, стрелки, аксессоров или `;`.

    Атрибуты отбрасываются, хотя они и являются потомками узла: в сигнатуре
    они шумят, а для правил классификации есть отдельное поле `attributes`.
    """
    parts: list[Node] = []
    for child in member.children:
        if child.type == "attribute_list":
            continue
        if child.type in _SIGNATURE_TERMINATORS:
            break
        parts.append(child)

    if not parts:
        return ""

    # Срез по байтам относительно самого узла: так не нужно тащить сюда
    # исходник целиком, а `member.text` — ровно его фрагмент.
    raw = member.text or b""
    start = parts[0].start_byte - member.start_byte
    end = parts[-1].end_byte - member.start_byte
    return _WHITESPACE.sub(" ", raw[start:end].decode("utf-8", errors="replace")).strip()


def _member_names(member: Node) -> list[str]:
    """Имена, объявленные одним узлом.

    У поля и события-поля имя лежит не в поле `name`, а в `variable_declarator`,
    и объявителей может быть несколько: `private int _a = 1, _b = 2;`. Возвращаем
    все — иначе `_b` пропал бы из документации молча. На ABP таких полей 0 из 1652,
    но цена корректности здесь нулевая.
    """
    if member.type in ("field_declaration", "event_field_declaration"):
        declaration = next(
            (c for c in member.named_children if c.type == "variable_declaration"), None
        )
        if declaration is None:
            return []
        # Пустые имена отбрасываются: при восстановлении после ошибки грамматика
        # выдаёт `field_declaration` без имени (обломок выражения из-под `#if`
        # или конструкция новее самой грамматики). Безымянный член в манифесте
        # превратился бы в безымянный раздел документации.
        names = [
            _text(c.child_by_field_name("name"))
            for c in declaration.named_children
            if c.type == "variable_declarator"
        ]
        return [name for name in names if name]

    name = _text(member.child_by_field_name("name"))
    return [name] if name else []


def _build_members(nodes: list[Node]) -> list[Member]:
    """Члены одного типа, отсортированные по `(line, name)`.

    Обе ветки `#if/#else` дают члены с одинаковым именем — это ожидаемо
    и не дедуплицируется: препроцессор не выполняется, документируются
    оба варианта.
    """
    found: list[Member] = []
    for node in nodes:
        kind = _MEMBER_KIND_BY_NODE[node.type]
        signature = _signature(node)
        modifiers = sorted(_text(c) for c in node.children if c.type == "modifier")
        attributes = _attributes(node)
        xml_doc = _xml_doc(node)
        for name in _member_names(node):
            found.append(
                Member(
                    name=name,
                    kind=kind,
                    signature=signature,
                    modifiers=modifiers,
                    attributes=attributes,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    xml_doc=xml_doc,
                )
            )

    found.sort(key=lambda m: (m.line, m.name))
    return found


def _using_name(directive: Node) -> str:
    """Импортируемое пространство имён из `using_directive`.

    Имя — последний именованный потомок: у алиаса перед ним стоит ещё
    `identifier` самого алиаса, у остальных форм оно единственное.
    Ключевые слова `global` и `static` — узлы анонимные и сюда не попадают.

    `global::` — квалификатор глобального пространства имён, а не часть имени:
    `using global::AutoMapper;` импортирует ровно `AutoMapper`. Без снятия
    префикса резолв на T10 не нашёл бы такой namespace (в ABP таких два).
    Прочие extern-алиасы (`MyAlias::Foo`) оставляем как есть — без разрешения
    алиасов сборок они всё равно не резолвятся, а врать в манифесте хуже.
    """
    if not directive.named_children:
        return ""
    return _text(directive.named_children[-1]).removeprefix("global::")


def _usings(tree_root: Node) -> tuple[list[str], list[str]]:
    """`(usings, global_usings)` — отсортированные и дедуплицированные.

    Отбрасываются две формы:

    - **алиас** (`using X = A.B.C;`) — импортирует не пространство имён, а имя
      для одного типа, и резолву по namespace не помогает;
    - **`using static`** — импортирует статические члены, а не типы.

    Порядок проверок важен: `global using static System.Console;` — это
    `static`, и в `global_usings` ему не место.
    """
    plain: set[str] = set()
    globals_: set[str] = set()

    for directive in QueryCursor(_query("usings.scm")).captures(tree_root).get("using", []):
        kinds = {child.type for child in directive.children}
        if "=" in kinds or "static" in kinds:
            continue

        name = _using_name(directive)
        if not name:
            continue
        (globals_ if "global" in kinds else plain).add(name)

    return sorted(plain), sorted(globals_)


def _count_errors(node: Node) -> int:
    total = 1 if node.type == "ERROR" or node.is_missing else 0
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "ERROR" or current.is_missing:
            total += 1
        stack.extend(current.children)
    return total


def _build_declaration(declaration: Node, path: str, members: list[Member]) -> RawDeclaration:
    return RawDeclaration(
        name=_text(declaration.child_by_field_name("name")),
        type_kind=_type_kind(declaration),
        namespace=_namespace_of(declaration),
        containing_type=_containing_type(declaration),
        type_parameters=_type_parameters(declaration),
        modifiers=sorted(_text(c) for c in declaration.children if c.type == "modifier"),
        base_types=_base_types(declaration),
        attributes=_attributes(declaration),
        members=members,
        span=SourceSpan(
            path=path,
            start=declaration.start_point[0] + 1,
            end=declaration.end_point[0] + 1,
        ),
        xml_doc=_xml_doc(declaration),
    )


def parse_source(source: bytes, path: str) -> FileParseResult:
    """Разобрать содержимое файла. `path` используется только как метка."""
    tree = _PARSER.parse(source)
    captures = QueryCursor(_query("declarations.scm")).captures(tree.root_node)
    member_captures = QueryCursor(_query("members.scm")).captures(tree.root_node)

    # Члены группируются по id узла-владельца: узлы дерева нехэшируемы,
    # а `id` уникален в пределах одного разбора.
    members_by_owner: defaultdict[int, list[Node]] = defaultdict(list)
    for node in member_captures.get("member", []):
        owner = _owning_type(node)
        if owner is not None:
            members_by_owner[owner.id].append(node)

    declarations = [
        _build_declaration(node, path, _build_members(members_by_owner.get(node.id, [])))
        for node in captures.get("declaration", [])
    ]
    declarations.sort(key=lambda d: (d.span.start, d.span.end, d.name))
    usings, global_usings = _usings(tree.root_node)

    di_calls = QueryCursor(_query("di.scm")).captures(tree.root_node).get("call", [])

    return FileParseResult(
        path=path,
        content_hash=content_hash(source),
        usings=usings,
        global_usings=global_usings,
        declarations=declarations,
        di_registrations=extract_registrations(di_calls, path),
        parse_errors=_count_errors(tree.root_node),
    )


def parse_file(path: Path, repo_root: Path) -> FileParseResult:
    """Разобрать файл. Путь в результате — репо-относительный POSIX."""
    relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    return parse_source(path.read_bytes(), relative)
