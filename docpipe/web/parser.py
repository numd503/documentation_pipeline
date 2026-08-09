"""Разбор файла TypeScript в `FileParseResult`.

Аналог `dotnet/parser.py`: чистый синтаксис одного файла, никакой межфайловой
логики. Всё, что требует знания о других файлах — алиасы `tsconfig`, бочки,
замыкание наследования, — живёт в `web/resolve.py`.

Ошибка здесь не видна снаружи: файл разберётся, символы появятся, но частью,
и отчёт будет выглядеть исправным. Поэтому каждая асимметрия грамматики,
на которой такое возможно, описана в комментарии рядом с обходом.
"""

import re
from collections import defaultdict
from functools import cache
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from docpipe.hashing import content_hash
from docpipe.model import (
    Attribute,
    FileParseResult,
    ImportedName,
    Member,
    MemberKind,
    ModuleImport,
    RawDeclaration,
    SourceSpan,
    TypeKind,
)

_LANGUAGE = Language(tsts.language_typescript())
_PARSER = Parser(_LANGUAGE)
_QUERIES_DIR = Path(__file__).parent / "queries"

# Узел объявления -> вид. Вид **синтаксический**: `component`, `service`
# и прочее — решения классификации, их принимает `rules/web.yaml`.
_TYPE_KIND_BY_NODE: dict[str, TypeKind] = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "function_declaration": "function",
    "lexical_declaration": "const",
}

_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration"})

# Узлы, на которых заканчивается сигнатура члена: тело, значение поля, `;`.
_SIGNATURE_TERMINATORS = frozenset({"statement_block", ";"})

# Ключевые слова, отделяющие декораторы от самого объявления в `export_statement`.
_EXPORT_KEYWORDS = frozenset({"export", "default"})

# Модификаторы объявления. `declare` попадает сюда же: файл `.d.ts` отсеивается
# правилом с причиной, но знать о модификаторе всё равно полезно.
_DECLARATION_MODIFIERS = frozenset({"abstract", "declare", "async"})

# Модификаторы члена. `accessibility_modifier` — узел-обёртка (`private`,
# `protected`, `public`), остальные — голые ключевые слова.
_MEMBER_MODIFIER_NODES = frozenset(
    {"accessibility_modifier", "readonly", "static", "abstract", "async", "override", "get", "set"}
)

_WHITESPACE = re.compile(r"\s+")
_SPACE_AROUND_DOT = re.compile(r"\s*\.\s*")
# Строка JSDoc: ведущие пробелы и звёздочка. Начало блока `/**` и конец `*/`
# снимаются отдельно.
_JSDOC_LINE = re.compile(r"^\s*\*+ ?")
_JSDOC_TAG = re.compile(r"^\s*@\w+")


@cache
def _query(name: str) -> Query:
    return Query(_LANGUAGE, (_QUERIES_DIR / name).read_text(encoding="utf-8"))


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def normalize_type_text(raw: str) -> str:
    """Привести текст типа к канонической однострочной форме.

    Тот же приём, что на .NET: тип может быть записан в несколько строк
    (`extends BaseComponent<\\n  Model\\n>`), и без нормализации переносы попали бы
    в `decl_hash`, а переформатирование файла заставляло бы агента шага 3
    перегенерировать документ впустую.
    """
    collapsed = _WHITESPACE.sub(" ", raw).strip()
    return _SPACE_AROUND_DOT.sub(".", collapsed)


def _string_value(node: Node) -> str:
    """Значение строкового литерала без кавычек.

    `string` хранит содержимое в потомке `string_fragment`; у пустой строки
    потомка нет вовсе. Шаблонная строка без подстановок тоже считается
    литералом — в Angular так пишут пути и селекторы.
    """
    if node.type in ("string", "template_string"):
        parts = [_text(c) for c in node.children if c.type == "string_fragment"]
        return "".join(parts)
    return _text(node)


def _is_literal(node: Node) -> bool:
    """Литерал ли узел. Шаблон с подстановкой литералом не является."""
    if node.type in ("string", "number", "true", "false", "null", "undefined"):
        return True
    if node.type == "template_string":
        return not any(c.type == "template_substitution" for c in node.children)
    return False


def _anchor(declaration: Node) -> Node:
    """Узел, соседи которого относятся к объявлению.

    У экспортируемого объявления это `export_statement`: и декораторы, и JSDoc
    висят на нём, а не на самом `class_declaration`. Без этой подмены
    у экспортируемого класса теряется комментарий, а у неэкспортируемого — нет,
    и разница выглядит как случайность в данных.
    """
    parent = declaration.parent
    if parent is not None and parent.type == "export_statement":
        return parent
    return declaration


def _decorators(declaration: Node) -> list[Node]:
    """Декораторы объявления. Собираются с ДВУХ уровней, и это обязательно.

    Инверсия правила C#, где атрибут всегда потомок объявления:

    - у неэкспортируемого класса `decorator` лежит ВНУТРИ `class_declaration`;
    - у экспортируемого — РЯДОМ с ним, в `export_statement`;
    - у члена класса — рядом с ним, в `class_body`.

    В Angular экспортируется всё, что имеет декоратор, поэтому реализация,
    перенесённая с .NET буквально, не классифицирует **ни одного** компонента
    и сервиса — при нулевом числе ошибок разбора.
    """
    found = [child for child in declaration.children if child.type == "decorator"]

    anchor = _anchor(declaration)
    if anchor is not declaration:
        found.extend(child for child in anchor.children if child.type == "decorator")
    else:
        # Член класса: декораторы — предшествующие соседи внутри `class_body`.
        sibling = declaration.prev_sibling
        while sibling is not None and sibling.type == "decorator":
            found.append(sibling)
            sibling = sibling.prev_sibling

    return sorted(found, key=lambda node: node.start_byte)


def _decorator_attribute(decorator: Node) -> Attribute | None:
    """Декоратор -> `Attribute`: имя, позиционные аргументы, поля объекта.

    Поля объекта попадают в `named_args` только литеральными значениями:
    `templateUrl` нужен шагу 2 (хэш шаблона входит в `impl_hash` компонента),
    `standalone` и `selector` — правилам классификации. Значение-выражение
    не берётся вовсе: «поле есть, но неизвестно» и «поля нет» обязаны
    различаться, а строка `this.x` в роли значения не различает ничего.

    Имя берётся до дженерика: `@State<DebtStateModel>({…})` — это `State`.
    Поиск по подстроке `@State(` даёт ноль при существующем стейте, и молча.
    """
    inner = next((c for c in decorator.named_children), None)
    if inner is None:
        return None

    args: list[str] = []
    named: dict[str, str] = {}

    if inner.type == "call_expression":
        name_node = inner.child_by_field_name("function")
        arguments = inner.child_by_field_name("arguments")
        for argument in arguments.named_children if arguments is not None else []:
            if argument.type == "object":
                for pair in argument.named_children:
                    if pair.type != "pair":
                        continue
                    key = pair.child_by_field_name("key")
                    value = pair.child_by_field_name("value")
                    if key is None or value is None or not _is_literal(value):
                        continue
                    named[_string_value(key)] = _string_value(value)
            elif _is_literal(argument):
                args.append(_string_value(argument))
            else:
                args.append(normalize_type_text(_text(argument)))
    else:
        # Декоратор без скобок: `@Injectable` вместо `@Injectable()`.
        name_node = inner

    name = normalize_type_text(_text(name_node)).rpartition(".")[2]
    if not name:
        return None
    return Attribute(name=name, args=args, named_args=dict(sorted(named.items())))


def _attributes(declaration: Node) -> list[Attribute]:
    found = [_decorator_attribute(node) for node in _decorators(declaration)]
    return [attribute for attribute in found if attribute is not None]


def _jsdoc(declaration: Node) -> str | None:
    """Первый абзац JSDoc-комментария, предшествующего объявлению.

    Комментарий — предшествующий **сосед**, как и XML-doc на .NET, но искать
    его приходится через декораторы и ключевое слово `export`: у компонента
    между комментарием и `class` стоит `@Component({…})` на десяток строк.

    Текст обрывается на первом теге (`@param`, `@returns`): дальше идёт
    справка по сигнатуре, а не описание смысла.
    """
    sibling = _anchor(declaration).prev_sibling
    while sibling is not None and sibling.type == "decorator":
        sibling = sibling.prev_sibling
    if sibling is None or sibling.type != "comment":
        return None

    raw = _text(sibling)
    if not raw.startswith("/**"):
        return None

    lines: list[str] = []
    for line in raw.removeprefix("/**").removesuffix("*/").splitlines():
        cleaned = _JSDOC_LINE.sub("", line).strip()
        if _JSDOC_TAG.match(cleaned):
            break
        lines.append(cleaned)

    return _WHITESPACE.sub(" ", " ".join(lines)).strip() or None


def _namespace_of(path: str) -> str:
    """Пространство имён символа фронта — каталог файла.

    В TypeScript пространств имён нет: единица изоляции — модуль, то есть файл.
    Каталог даёт ту же группировку, что namespace на .NET, и не зависит
    от содержимого файла, поэтому переносится в FQN без разбора.
    """
    directory = path.rpartition("/")[0]
    return directory


def _containing_type(declaration: Node) -> str | None:
    """Цепочка охватывающих классов. В Angular редкость, но стоит копейки."""
    names: list[str] = []
    current = declaration.parent
    while current is not None:
        if current.type in _CLASS_NODES:
            names.append(_text(current.child_by_field_name("name")))
        current = current.parent
    return ".".join(reversed([name for name in names if name])) or None


def _modifiers(declaration: Node) -> list[str]:
    """Модификаторы объявления, включая `export`.

    `export` лежит не в объявлении, а в его родителе, и это тот же перевёрнутый
    случай, что с декоратором. Правила классификации по нему матчатся:
    неэкспортируемый класс с `@Component` — не документ, а деталь модуля.
    """
    found = {child.type for child in declaration.children if child.type in _DECLARATION_MODIFIERS}

    parent = declaration.parent
    if parent is not None and parent.type == "export_statement":
        found.update(child.type for child in parent.children if child.type in _EXPORT_KEYWORDS)

    return sorted(found)


def _type_parameters(declaration: Node) -> list[str]:
    parameters = declaration.child_by_field_name("type_parameters")
    if parameters is None:
        return []
    return [
        _text(child.child_by_field_name("name") or child)
        for child in parameters.named_children
        if child.type == "type_parameter"
    ]


def _base_types(declaration: Node) -> list[str]:
    """Базовые типы: `extends` и `implements`.

    У класса они лежат в `class_heritage`, у интерфейса — в `extends_type_clause`
    прямо в объявлении. Обе формы возвращаются одним списком: правила
    классификации матчатся по имени базового типа и разницы между
    «наследует» и «реализует» не делают — как и на .NET.

    Три клаузы устроены **по-разному**, и это ловушка:

        extends_clause        value: Base, type_arguments: <T>    — двумя полями
        implements_clause     generic_type Holder<T>, type_identifier Other
        extends_type_clause   generic_type First<A>,  type_identifier Second

    То есть у `extends` класса дженерик лежит отдельным узлом, а у остальных —
    внутри типа. Обход, одинаковый для всех трёх, отдал бы `Base` вместо
    `Base<T>` только у `extends`, и правило по базовому типу с дженериком
    сработало бы на `implements` и не сработало на `extends`.
    """
    found: list[str] = []
    containers = [
        child
        for child in declaration.children
        if child.type in ("class_heritage", "extends_type_clause", "implements_clause")
    ]
    for container in containers:
        clauses = (
            list(container.named_children) if container.type == "class_heritage" else [container]
        )
        for clause in clauses:
            value = clause.child_by_field_name("value")
            if value is not None:
                arguments = clause.child_by_field_name("type_arguments")
                found.append(normalize_type_text(_text(value) + _text(arguments)))
                continue
            found.extend(
                normalize_type_text(_text(child))
                for child in clause.named_children
                if child.type != "type_arguments"
            )
    return [name for name in found if name]


def _owning_type(member: Node) -> Node | None:
    """Ближайший предок-объявление типа. Обходом вверх, а не перебором детей."""
    current = member.parent
    while current is not None:
        if current.type in _CLASS_NODES or current.type == "interface_declaration":
            return current
        current = current.parent
    return None


def _member_kind(member: Node) -> MemberKind:
    """Вид члена. Конструктор и аксессоры различаются не типом узла.

    `method_definition` покрывает метод, конструктор и `get`/`set`. Аксессор
    считается свойством: снаружи он им и является, а разделение на пару
    «метод чтения / метод записи» дало бы два раздела документации об одном
    и том же поле.
    """
    if member.type in ("public_field_definition", "property_signature"):
        return "property" if member.type == "property_signature" else "field"
    if member.type in ("method_signature", "abstract_method_signature"):
        return "method"
    if _text(member.child_by_field_name("name")) == "constructor":
        return "constructor"
    if any(child.type in ("get", "set") for child in member.children):
        return "property"
    return "method"


def _signature(member: Node) -> str:
    """Сигнатура члена: текст до тела или до значения поля.

    Декораторы в сигнатуру не входят — они соседи, а не потомки, и в текст
    узла не попадают вовсе. Значение поля отбрасывается: `readonly form =
    new FormGroup({…})` в сигнатуре превратилось бы в пол-экрана кода,
    а меняется оно от правок, к контракту отношения не имеющих.
    """
    parts: list[Node] = []
    for child in member.children:
        if child.type in _SIGNATURE_TERMINATORS:
            break
        # `=` и всё после него — инициализатор поля, а не сигнатура.
        if child.type == "=":
            break
        parts.append(child)

    if not parts:
        return ""

    raw = member.text or b""
    start = parts[0].start_byte - member.start_byte
    end = parts[-1].end_byte - member.start_byte
    return _WHITESPACE.sub(" ", raw[start:end].decode("utf-8", errors="replace")).strip()


def _member_names(member: Node) -> list[str]:
    name = _text(member.child_by_field_name("name"))
    return [name] if name else []


def _build_members(nodes: list[Node]) -> list[Member]:
    found: list[Member] = []
    for node in nodes:
        kind = _member_kind(node)
        signature = _signature(node)
        modifiers = sorted(
            _text(child) for child in node.children if child.type in _MEMBER_MODIFIER_NODES
        )
        attributes = _attributes(node)
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
                    xml_doc=_jsdoc(node),
                )
            )

    found.sort(key=lambda member: (member.line, member.name))
    return found


def _imported_names(clause: Node) -> tuple[list[ImportedName], bool]:
    """Имена из `import_clause` либо `export_clause` и признак «звёздочка».

    Три формы, и все три встречаются в Angular:

        import { A, B as C } from 'x'   named_imports -> import_specifier
        import Default from 'x'         сам identifier прямо в клаузе
        import * as ns from 'x'         namespace_import

    Алиас (`B as C`) хранится обоими именами: в этом файле имя видно как `C`,
    а в модуле-источнике объявлено как `B`.
    """
    found: list[ImportedName] = []
    star = False

    # `export_clause` сам содержит спецификаторы, `import_clause` — держит их
    # в потомке `named_imports`. Уровень вложенности у двух форм разный.
    groups = [clause] if clause.type == "export_clause" else list(clause.named_children)

    for child in groups:
        if child.type == "namespace_import":
            star = True
            identifier = next((c for c in child.named_children if c.type == "identifier"), None)
            if identifier is not None:
                found.append(ImportedName(local=_text(identifier), imported="*"))
        elif child.type == "identifier":
            # Импорт по умолчанию: `import Default from 'x'`. Имя в источнике
            # у него своё собственное — `default`, а не то, под которым он виден.
            found.append(ImportedName(local=_text(child), imported="default"))
        elif child.type in ("named_imports", "export_clause"):
            for specifier in child.named_children:
                if specifier.type not in ("import_specifier", "export_specifier"):
                    continue
                name = _text(specifier.child_by_field_name("name"))
                alias = _text(specifier.child_by_field_name("alias"))
                if name:
                    found.append(ImportedName(local=alias or name, imported=name))

    return found, star


def _imports(root: Node) -> list[ModuleImport]:
    """Импорты и переэкспорты файла, в порядке появления."""
    captures = QueryCursor(_query("imports.scm")).captures(root)
    found: list[ModuleImport] = []

    for key, re_export in (("import", False), ("re_export", True)):
        for statement in captures.get(key, []):
            source = statement.child_by_field_name("source")
            if source is None:
                continue

            names: list[ImportedName] = []
            star = any(child.type == "*" for child in statement.children)
            for child in statement.children:
                if child.type in ("import_clause", "export_clause"):
                    clause_names, clause_star = _imported_names(child)
                    names.extend(clause_names)
                    star = star or clause_star

            found.append(
                ModuleImport(
                    source=_string_value(source),
                    names=names,
                    star=star,
                    re_export=re_export,
                    line=statement.start_point[0] + 1,
                )
            )

    found.sort(key=lambda item: (item.line, item.source))
    return found


def _count_errors(node: Node) -> int:
    total = 1 if node.type == "ERROR" or node.is_missing else 0
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "ERROR" or current.is_missing:
            total += 1
        stack.extend(current.children)
    return total


def _declaration_hash(declaration: Node) -> str:
    """Хэш текста объявления с нормализованными пробелами.

    Ни номеров строк, ни пути: без нормализации прогон `prettier` по репозиторию
    изменил бы хэш у всех символов сразу, и всё дерево документации потребовало
    бы пересмотра.
    """
    raw = (declaration.text or b"").decode("utf-8", errors="replace")
    return content_hash(_WHITESPACE.sub(" ", raw).strip().encode("utf-8"))


def _declarator_nodes(declaration: Node) -> list[tuple[str, Node]]:
    """Пары «имя, узел» одного объявления.

    `lexical_declaration` объявляет несколько имён сразу
    (`export const a = 1, b = 2;`), и вернуть надо все: пропавшее имя исчезло бы
    из документации молча. Деструктуризация (`const { a } = x`) пропускается —
    имени у такого объявления нет, а выдумывать его не из чего.
    """
    if declaration.type != "lexical_declaration":
        return [(_text(declaration.child_by_field_name("name")), declaration)]

    if not any(child.type == "const" for child in declaration.children):
        return []

    found: list[tuple[str, Node]] = []
    for child in declaration.named_children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        found.append((_text(name_node), child))
    return found


def _build_declaration(
    declaration: Node, name: str, hashed: Node, path: str, members: list[Member]
) -> RawDeclaration:
    return RawDeclaration(
        name=name,
        type_kind=_TYPE_KIND_BY_NODE[declaration.type],
        namespace=_namespace_of(path),
        containing_type=_containing_type(declaration),
        type_parameters=_type_parameters(declaration),
        modifiers=_modifiers(declaration),
        base_types=_base_types(declaration),
        attributes=_attributes(declaration),
        members=members,
        span=SourceSpan(
            path=path,
            start=declaration.start_point[0] + 1,
            end=declaration.end_point[0] + 1,
        ),
        xml_doc=_jsdoc(declaration),
        decl_hash=_declaration_hash(hashed),
    )


def parse_source(source: bytes, path: str) -> FileParseResult:
    """Разобрать содержимое файла. `path` используется как метка и как namespace."""
    tree = _PARSER.parse(source)
    captures = QueryCursor(_query("declarations.scm")).captures(tree.root_node)
    member_captures = QueryCursor(_query("members.scm")).captures(tree.root_node)

    # Члены группируются по id узла-владельца. Брать один ключ захватов
    # и спускаться от узла обязательно: списки разных ключей одного запроса
    # между собой НЕ выровнены — на одном запросе `@method` пришёл в порядке
    # [innerDebtsSnapshot, get, post, …], а `@call` в другом, и `zip` дал бы
    # пары, которых в коде нет.
    members_by_owner: defaultdict[int, list[Node]] = defaultdict(list)
    for node in member_captures.get("member", []):
        owner = _owning_type(node)
        if owner is not None:
            members_by_owner[owner.id].append(node)

    declarations = [
        _build_declaration(
            node, name, hashed, path, _build_members(members_by_owner.get(node.id, []))
        )
        for node in captures.get("declaration", [])
        for name, hashed in _declarator_nodes(node)
        if name
    ]
    declarations.sort(key=lambda declaration: (declaration.span.start, declaration.name))

    return FileParseResult(
        path=path,
        content_hash=content_hash(source),
        imports=_imports(tree.root_node),
        declarations=declarations,
        parse_errors=_count_errors(tree.root_node),
    )


def parse_file(path: Path, repo_root: Path) -> FileParseResult:
    """Разобрать файл. Путь в результате — репо-относительный POSIX."""
    relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    return parse_source(path.read_bytes(), relative)
