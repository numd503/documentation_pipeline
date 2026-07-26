"""Разбор файла C# в `FileParseResult`.

Чистый синтаксис одного файла: никакой межфайловой логики, никакого резолва имён.
Всё, что требует знания о других файлах, живёт в `resolve.py`.

Парсер устойчив к синтаксически некорректному коду — tree-sitter возвращает дерево
с узлами `ERROR`, а не бросает исключение. Их количество попадает в `parse_errors`,
чтобы деградацию было видно, а не приходилось догадываться о ней по пустому выводу.
"""

import re
from functools import cache
from pathlib import Path

import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from docpipe.hashing import content_hash
from docpipe.model import Attribute, FileParseResult, RawDeclaration, SourceSpan, TypeKind

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


def _count_errors(node: Node) -> int:
    total = 1 if node.type == "ERROR" or node.is_missing else 0
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "ERROR" or current.is_missing:
            total += 1
        stack.extend(current.children)
    return total


def _build_declaration(declaration: Node, path: str) -> RawDeclaration:
    return RawDeclaration(
        name=_text(declaration.child_by_field_name("name")),
        type_kind=_type_kind(declaration),
        namespace=_namespace_of(declaration),
        containing_type=_containing_type(declaration),
        type_parameters=_type_parameters(declaration),
        modifiers=sorted(_text(c) for c in declaration.children if c.type == "modifier"),
        base_types=_base_types(declaration),
        attributes=_attributes(declaration),
        members=[],
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

    declarations = [_build_declaration(node, path) for node in captures.get("declaration", [])]
    declarations.sort(key=lambda d: (d.span.start, d.span.end, d.name))

    return FileParseResult(
        path=path,
        content_hash=content_hash(source),
        declarations=declarations,
        parse_errors=_count_errors(tree.root_node),
    )


def parse_file(path: Path, repo_root: Path) -> FileParseResult:
    """Разобрать файл. Путь в результате — репо-относительный POSIX."""
    relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    return parse_source(path.read_bytes(), relative)
