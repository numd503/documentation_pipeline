"""Разбор TypeScript в `FileParseResult` (F04).

Ошибка в этом модуле снаружи не видна: файл разберётся, символы появятся, но
частью, и отчёт будет выглядеть исправным. Поэтому здесь проверяется не «разбор
прошёл», а каждая асимметрия грамматики по отдельности.
"""

import ast
from pathlib import Path

import pytest

from docpipe.model import FileParseResult, RawDeclaration
from docpipe.web.parser import parse_file, parse_source


@pytest.fixture
def parsed(web_workspace: Path) -> dict[str, FileParseResult]:
    """Вся фикстура, разобранная один раз: путь → результат."""
    return {
        path.relative_to(web_workspace).as_posix(): parse_file(path, web_workspace)
        for path in sorted(web_workspace.rglob("*.ts"))
        if "node_modules" not in path.parts
    }


def _declaration(parsed: dict[str, FileParseResult], name: str) -> RawDeclaration:
    found = [
        declaration
        for result in parsed.values()
        for declaration in result.declarations
        if declaration.name == name
    ]
    assert len(found) == 1, f"{name}: найдено {len(found)}"
    return found[0]


# --------------------------------------------------------------------------------------
# Декоратор: сосед объявления, а не потомок, и это инверсия правила C#
# --------------------------------------------------------------------------------------


def test_decorator_is_found_on_both_levels(parsed: dict[str, FileParseResult]) -> None:
    """Утверждаются **оба** класса, иначе ловушка не проверена.

    У экспортируемого класса декоратор лежит в `export_statement` рядом
    с `class_declaration`, у неэкспортируемого — внутри него. Реализация,
    собирающая декораторы с одного уровня, найдёт ровно половину — и не даст
    ни одной ошибки разбора.
    """
    exported = _declaration(parsed, "LegacyModule")
    inner = _declaration(parsed, "LegacyBannerComponent")

    assert [a.name for a in exported.attributes] == ["NgModule"]
    assert [a.name for a in inner.attributes] == ["Component"]
    assert "export" in exported.modifiers
    assert "export" not in inner.modifiers


def test_two_decorators_on_one_class(parsed: dict[str, FileParseResult]) -> None:
    """`@State<…>({…})` и `@Injectable()` — собрать обязательно оба."""
    state = _declaration(parsed, "DebtState")
    assert [a.name for a in state.attributes] == ["State", "Injectable"]


def test_decorator_name_is_taken_before_the_generic(parsed: dict[str, FileParseResult]) -> None:
    """Декоратор стейта всегда написан с дженериком.

    Поиск имени по тексту до скобки дал бы `State<DebtStateModel>` и не совпал
    бы ни с одним правилом — при том, что стейт в выводе присутствует.
    """
    state = _declaration(parsed, "DebtState")
    assert state.attributes[0].name == "State"
    assert state.attributes[0].named_args["name"] == "innerDebt"


def test_member_decorators_are_preceding_siblings(parsed: dict[str, FileParseResult]) -> None:
    """У члена класса декоратор — сосед внутри `class_body`, а не потомок.

    Третья форма подряд, отличная от двух предыдущих: цепочка NGXS собирается
    именно по `@Action`, и без неё у страницы не будет связи с эндпоинтом.
    """
    state = _declaration(parsed, "DebtState")
    by_name = {member.name: member for member in state.members}

    assert [a.name for a in by_name["load"].attributes] == ["Action"]
    assert by_name["load"].attributes[0].args == ["LoadInnerDebts"]
    assert [a.name for a in by_name["items"].attributes] == ["Selector"]
    assert by_name["constructor"].attributes == []


def test_decorator_object_fields_are_taken_only_as_literals(
    parsed: dict[str, FileParseResult],
) -> None:
    """`templateUrl` нужен шагу 2, `standalone` и `selector` — правилам.

    Значение-выражение не берётся вовсе: «поле есть, но неизвестно» и «поля нет»
    обязаны различаться.
    """
    component = _declaration(parsed, "ListComponent")
    named = component.attributes[0].named_args

    assert named["templateUrl"] == "./list.component.html"
    assert named["standalone"] == "true"
    assert named["selector"] == "app-models-list"
    # `imports: [CommonModule, ReactiveFormsModule]` — массив, а не литерал.
    assert "imports" not in named


def test_decorator_without_parentheses(parsed: dict[str, FileParseResult]) -> None:
    """`@Injectable` без скобок — тоже декоратор, и имя у него то же."""
    result = parse_source(b"@Sealed\nclass X {}\n", "src/x.ts")
    assert [a.name for a in result.declarations[0].attributes] == ["Sealed"]


# --------------------------------------------------------------------------------------
# Что вообще считается объявлением
# --------------------------------------------------------------------------------------


def test_functional_interceptor_is_a_symbol(parsed: dict[str, FileParseResult]) -> None:
    """`export const X: HttpInterceptorFn = …` — класса нет, декоратора нет.

    В АС CF так объявлены интерцепторы и guard'ы. Реализация, ищущая только
    классы, пропустит их молча.
    """
    interceptor = _declaration(parsed, "authInterceptor")
    assert interceptor.type_kind == "const"
    assert "export" in interceptor.modifiers


def test_classes_interfaces_and_enums_are_taken_regardless_of_export(
    parsed: dict[str, FileParseResult],
) -> None:
    kinds = {
        declaration.name: declaration.type_kind
        for result in parsed.values()
        for declaration in result.declarations
    }
    assert kinds["Model"] == "interface"
    assert kinds["ModelKind"] == "enum"
    assert kinds["LegacyBannerComponent"] == "class"
    assert kinds["modelsPath"] == "const"


def test_local_const_is_not_a_declaration() -> None:
    """Иначе каждая локальная переменная каждого метода стала бы символом."""
    source = b"""
export class A {
  run(): void {
    const local = 1;
  }
}
const moduleLevelButNotExported = 2;
"""
    names = {declaration.name for declaration in parse_source(source, "src/a.ts").declarations}
    assert names == {"A"}


def test_several_names_in_one_const_statement() -> None:
    """`export const a = 1, b = 2;` — пропавшее имя исчезло бы молча."""
    result = parse_source(b"export const a = 1, b = 2;\n", "src/a.ts")
    assert [d.name for d in result.declarations] == ["a", "b"]


def test_destructuring_declares_no_symbol() -> None:
    """Имени у такого объявления нет, и выдумывать его не из чего."""
    result = parse_source(b"export const { a, b } = obj;\n", "src/a.ts")
    assert result.declarations == []


def test_exported_function_is_a_symbol() -> None:
    result = parse_source(b"export function makeUrl(p: string): string { return p; }\n", "src/a.ts")
    assert [(d.name, d.type_kind) for d in result.declarations] == [("makeUrl", "function")]


# --------------------------------------------------------------------------------------
# Наследование: три клаузы, устроенные по-разному
# --------------------------------------------------------------------------------------


def test_base_types_cover_extends_and_implements(parsed: dict[str, FileParseResult]) -> None:
    assert _declaration(parsed, "FixUrlInterceptor").base_types == ["HttpInterceptor"]
    assert _declaration(parsed, "BaseApiService").base_types == ["CoreService"]
    assert _declaration(parsed, "InnerDebtService").base_types == ["BaseApiService"]


def test_generic_argument_survives_in_every_clause() -> None:
    """У `extends` класса дженерик лежит отдельным полем, у остальных — внутри типа.

    Обход, одинаковый для всех трёх, отдал бы `Base` вместо `Base<T>` только
    у `extends`: правило по базовому типу с дженериком сработало бы
    на `implements` и не сработало на `extends`.
    """
    source = b"""
export class Box<T, U> extends Base<T> implements Holder<U>, Other {}
export interface Pair<A> extends First<A>, Second {}
"""
    box, pair = parse_source(source, "src/a.ts").declarations
    assert box.type_parameters == ["T", "U"]
    assert box.base_types == ["Base<T>", "Holder<U>", "Other"]
    assert pair.base_types == ["First<A>", "Second"]


# --------------------------------------------------------------------------------------
# Члены
# --------------------------------------------------------------------------------------


def test_member_kinds_and_modifiers(parsed: dict[str, FileParseResult]) -> None:
    component = _declaration(parsed, "ListComponent")
    by_name = {member.name: member for member in component.members}

    assert by_name["constructor"].kind == "constructor"
    assert by_name["load"].kind == "method"
    assert by_name["cache"].kind == "field"
    assert by_name["cache"].modifiers == ["private", "readonly"]
    # Интерфейс объявляет свойства, а не поля.
    assert {m.kind for m in _declaration(parsed, "Model").members} == {"property"}


def test_constructor_signature_carries_its_parameters(parsed: dict[str, FileParseResult]) -> None:
    """Параметры конструктора — это зависимости: в Angular DI конструкторный."""
    signature = next(
        member.signature
        for member in _declaration(parsed, "InnerDebtService").members
        if member.kind == "constructor"
    )
    assert signature == "constructor(http: HttpClient)"


def test_field_initializer_is_not_part_of_the_signature(parsed: dict[str, FileParseResult]) -> None:
    """`readonly form = new FormGroup({…})` в сигнатуре занял бы пол-экрана."""
    form = next(m for m in _declaration(parsed, "ListComponent").members if m.name == "form")
    assert form.signature == "readonly form"


def test_accessor_is_a_property() -> None:
    """Пара «метод чтения / метод записи» дала бы два раздела об одном поле."""
    source = b"export class A {\n  get title(): string { return ''; }\n}\n"
    member = parse_source(source, "src/a.ts").declarations[0].members[0]
    assert (member.name, member.kind) == ("title", "property")


# --------------------------------------------------------------------------------------
# JSDoc
# --------------------------------------------------------------------------------------


def test_jsdoc_is_found_through_export_and_decorator() -> None:
    """Комментарий — сосед `export_statement`, а не объявления.

    У компонента между комментарием и словом `class` стои́т ещё `@Component({…})`
    на десяток строк.
    """
    source = b"""
/**
 * Screen with the list of models.
 * @deprecated use the new one
 */
@Component({ selector: 'a' })
export class A {}
"""
    declaration = parse_source(source, "src/a.ts").declarations[0]
    assert declaration.xml_doc == "Screen with the list of models."


def test_plain_comment_is_not_jsdoc() -> None:
    source = b"// just a note\nexport class A {}\n"
    assert parse_source(source, "src/a.ts").declarations[0].xml_doc is None


# --------------------------------------------------------------------------------------
# Устойчивость и детерминизм
# --------------------------------------------------------------------------------------


def test_broken_file_reports_errors_and_does_not_crash() -> None:
    source = b"export class A { constructor( { \nexport const x =\n"
    result = parse_source(source, "src/broken.ts")
    assert result.parse_errors > 0


def test_whole_fixture_parses_without_errors(parsed: dict[str, FileParseResult]) -> None:
    """Ноль ошибок на всей фикстуре: иначе тесты выше проверяют обломки."""
    assert {
        path: result.parse_errors for path, result in parsed.items() if result.parse_errors
    } == {}


def test_parsing_twice_gives_the_same_result(web_workspace: Path) -> None:
    path = web_workspace / "src/app/inner-debt/state/debt.state.ts"
    assert parse_file(path, web_workspace) == parse_file(path, web_workspace)


def test_declaration_hash_ignores_indentation_and_line_breaks() -> None:
    """Иначе прогон `prettier` по репозиторию пометил бы устаревшими все документы."""
    flat = b"export class A { run(): void {} }\n"
    indented = b"export class A {\n\n      run(): void {}\n\n}\n"

    assert (
        parse_source(flat, "src/a.ts").declarations[0].decl_hash
        == parse_source(indented, "src/b.ts").declarations[0].decl_hash
    )


def test_declaration_hash_does_not_depend_on_the_path() -> None:
    source = b"export class A {}\n"
    assert (
        parse_source(source, "src/a.ts").declarations[0].decl_hash
        == parse_source(source, "other/deep/b.ts").declarations[0].decl_hash
    )


def test_namespace_is_the_directory_of_the_file(parsed: dict[str, FileParseResult]) -> None:
    """В TypeScript пространств имён нет: единица изоляции — файл."""
    assert _declaration(parsed, "ListComponent").namespace == "src/app/routes/models/list"
    assert parse_source(b"export class A {}\n", "a.ts").declarations[0].namespace == ""


def test_declarations_are_ordered_by_position(parsed: dict[str, FileParseResult]) -> None:
    """Порядок объявлений — из текста файла, а не из порядка захватов запроса."""
    for result in parsed.values():
        starts = [declaration.span.start for declaration in result.declarations]
        assert starts == sorted(starts), result.path


# --------------------------------------------------------------------------------------
# Граница пакетов
# --------------------------------------------------------------------------------------


def _imports_of(package: str, forbidden: str) -> list[str]:
    """Кто в пакете импортирует запрещённое. Обходом AST, а не импортом:
    импорт проверил бы только то, что модуль загружается, а не то, что
    зависимости нет."""
    offenders: list[str] = []
    for path in sorted(Path(package).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(forbidden):
                offenders.append(f"{path.name}: from {node.module}")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith(forbidden)
                ]
    return offenders


def test_web_does_not_import_dotnet() -> None:
    """Общее у языков — модели, маршрут, обход и всё, что после манифеста."""
    assert _imports_of("docpipe/web", "docpipe.dotnet") == []


def test_dotnet_does_not_import_web() -> None:
    assert _imports_of("docpipe/dotnet", "docpipe.web") == []
