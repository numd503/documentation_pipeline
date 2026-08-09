"""Проверка целостности фикстуры WildSolution (T01b).

Каждый файл здесь существует ради конкретной конструкции, найденной в реальном
репозитории (eShopOnWeb) и не покрытой SampleSolution. Если конструкция исчезнет
из файла, соответствующий тест в T06/T08/T10/T13 продолжит проходить, ничего
не проверяя — поэтому наличие конструкций фиксируется здесь явно.

Разбор ведётся сырым tree-sitter: на момент T01b парсера ещё нет, а проверять
фикстуру чем-то надо.
"""

from pathlib import Path

import pytest
import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Node, Parser

_LANGUAGE = Language(tscs.language())
_PARSER = Parser(_LANGUAGE)

EXPECTED_CS = [
    "src/Wild.Api/Constants.cs",
    "src/Wild.Api/Controllers/InnerDebtsController.cs",
    "src/Wild.Api/Duplicates/Constants.cs",
    "src/Wild.Api/Endpoints/AuthenticateEndpoint.cs",
    "src/Wild.Api/Endpoints/CatalogListEndpoint.cs",
    "src/Wild.Api/GlobalUsings.cs",
    "src/Wild.Api/Legacy/BlockNamespace.cs",
    "src/Wild.Api/Legacy/NoNamespace.cs",
    "src/Wild.Api/Modules/ConditionalModule.cs",
    "src/Wild.Api/Pages/Login.cshtml.cs",
    "src/Wild.Api/Pages/Register.cshtml.cs",
    "src/Wild.Api/Program.cs",
    "src/Wild.Api/Services/CrudAppService.cs",
    "tests/Wild.Tests/CatalogListEndpointTests.cs",
]

# Единственный файл фикстуры, который **обязан** разбираться с ошибками:
# он воспроизводит конструкцию, на которой ломается сам tree-sitter.
EXPECTED_BROKEN = "src/Wild.Api/Modules/ConditionalModule.cs"


def _root(solution: Path, relative: str) -> Node:
    return _PARSER.parse((solution / relative).read_bytes()).root_node


def _count_errors(node: Node) -> int:
    total = 1 if node.type == "ERROR" or node.is_missing else 0
    for child in node.children:
        total += _count_errors(child)
    return total


def _descendants(node: Node) -> list[Node]:
    found = []
    stack = [node]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(current.children)
    return found


# --------------------------------------------------------------------------------------
# Состав фикстуры
# --------------------------------------------------------------------------------------


def test_file_inventory(wild_solution: Path) -> None:
    found = sorted(p.relative_to(wild_solution).as_posix() for p in wild_solution.rglob("*.cs"))
    assert found == EXPECTED_CS


def test_two_projects_one_of_them_tests(wild_solution: Path) -> None:
    """Тестовый проект нужен для проверки enrolled в T15: парсится, но не документируется."""
    projects = sorted(
        p.relative_to(wild_solution).as_posix() for p in wild_solution.rglob("*.csproj")
    )
    assert projects == ["src/Wild.Api/Wild.Api.csproj", "tests/Wild.Tests/Wild.Tests.csproj"]


@pytest.mark.parametrize("relative", [p for p in EXPECTED_CS if p != EXPECTED_BROKEN])
def test_every_file_parses_without_errors(wild_solution: Path, relative: str) -> None:
    tree = _PARSER.parse((wild_solution / relative).read_bytes())
    assert _count_errors(tree.root_node) == 0
    assert tree.root_node.has_error is False


# --------------------------------------------------------------------------------------
# Конструкции, ради которых фикстура существует
# --------------------------------------------------------------------------------------


def test_multiline_base_list(wild_solution: Path) -> None:
    """Ломает signature_hash, если не схлопывать пробелы (T06).

    Переносы строк попали бы в хэш, и переформатирование файла заставляло бы
    агента на шаге 3 перегенерировать документ впустую.
    """
    root = _root(wild_solution, "src/Wild.Api/Endpoints/AuthenticateEndpoint.cs")
    declaration = next(c for c in root.children if c.type == "class_declaration")
    base_list = next(c for c in declaration.children if c.type == "base_list")
    raw = base_list.named_children[0].text.decode()

    assert "\n" in raw, "фикстура потеряла многострочность — тест T06 станет бессмысленным"
    assert raw.startswith("EndpointBaseAsync")
    assert "WithActionResult" in raw


def test_top_level_statements_hold_di_registrations(wild_solution: Path) -> None:
    """Program.cs без класса и метода (T13).

    Реализация, ищущая вызовы внутри method_declaration, не найдёт здесь ничего.
    """
    root = _root(wild_solution, "src/Wild.Api/Program.cs")
    child_types = [c.type for c in root.children]

    assert "global_statement" in child_types
    assert "class_declaration" not in child_types
    assert "method_declaration" not in child_types

    invocations = [n for n in _descendants(root) if n.type == "invocation_expression"]
    registrations = [n for n in invocations if "Add" in n.text.decode().split("(")[0]]
    assert len(registrations) == 4  # Scoped, Singleton, TryAddTransient, HostedService


def test_type_without_namespace(wild_solution: Path) -> None:
    """FQN такого типа равен голому имени (T10)."""
    root = _root(wild_solution, "src/Wild.Api/Legacy/NoNamespace.cs")
    child_types = {c.type for c in root.children}

    assert "namespace_declaration" not in child_types
    assert "file_scoped_namespace_declaration" not in child_types
    assert "class_declaration" in child_types


def test_block_form_namespace(wild_solution: Path) -> None:
    """Редкая форма: 1 файл на 239 в eShopOnWeb. Именно на ней всё и ломается."""
    root = _root(wild_solution, "src/Wild.Api/Legacy/BlockNamespace.cs")
    namespace = next(c for c in root.children if c.type == "namespace_declaration")
    nested = [n for n in _descendants(namespace) if n.type == "class_declaration"]
    assert len(nested) == 1


def test_using_directive_variants(wild_solution: Path) -> None:
    """global / static / алиас — три случая, которые T08 обязан различать."""
    root = _root(wild_solution, "src/Wild.Api/GlobalUsings.cs")
    directives = [c for c in root.children if c.type == "using_directive"]
    marks = [{x.type for x in d.children} for d in directives]

    assert sum("global" in m for m in marks) == 2
    assert sum("static" in m for m in marks) == 1
    assert sum("=" in m for m in marks) == 1


def test_nested_types_share_simple_name(wild_solution: Path) -> None:
    """InputModel объявлен дважды в разных родителях.

    Без containing_type в FQN они схлопнутся в один узел (T10).
    """
    names = []
    for relative in ["src/Wild.Api/Pages/Login.cshtml.cs", "src/Wild.Api/Pages/Register.cshtml.cs"]:
        root = _root(wild_solution, relative)
        outer = next(c for c in root.children if c.type == "class_declaration")
        outer_name = next(c.text.decode() for c in outer.children if c.type == "identifier")
        body = next(c for c in outer.children if c.type == "declaration_list")
        for inner in (c for c in body.children if c.type == "class_declaration"):
            inner_name = next(c.text.decode() for c in inner.children if c.type == "identifier")
            names.append(f"{outer_name}.{inner_name}")

    assert names == ["LoginModel.InputModel", "RegisterModel.InputModel"]


def test_preprocessor_inside_attribute_arguments(wild_solution: Path) -> None:
    """Файл, на котором ломается сама грамматика (находка ABP).

    `#if` внутри списка аргументов атрибута рвёт выражение, а вместе с ним и
    объявление, потому что атрибут — потомок объявления. Класс перестаёт быть
    `class_declaration` и переразбирается как top-level statement.

    Фиксируем и наличие конструкции, и её последствие: если файл «починят»,
    тесты T06 и T20 продолжат проходить, ничего не проверяя.
    """
    source = (wild_solution / EXPECTED_BROKEN).read_text(encoding="utf-8")
    attribute = source[source.index("[DependsOn(") : source.index(")]")]
    assert "#if" in attribute, "фикстура потеряла директиву внутри аргументов атрибута"

    root = _root(wild_solution, EXPECTED_BROKEN)
    assert _count_errors(root) > 0
    assert "class_declaration" not in {c.type for c in root.children}
    assert "global_statement" in {c.type for c in root.children}


def test_generic_arity_overloads(wild_solution: Path) -> None:
    """Три типа с одним именем и FQN, различимые только числом параметров (T10, T15)."""
    root = _root(wild_solution, "src/Wild.Api/Services/CrudAppService.cs")
    declarations = [c for c in root.children if c.type == "interface_declaration"]

    names = {
        next(c.text.decode() for c in d.children if c.type == "identifier") for d in declarations
    }
    arities = sorted(
        len(next(c for c in d.children if c.type == "type_parameter_list").named_children)
        for d in declarations
    )

    assert names == {"ICrudAppService"}
    assert arities == [1, 2, 3]


def test_slug_collision_within_one_module(wild_solution: Path) -> None:
    """Два Constants в одном модуле, разные namespace.

    Единственный случай, когда модульная раскладка не разводит дубли сама,
    и включается хэш-суффикс (T15).
    """
    from docpipe.hashing import slugify

    namespaces = []
    for relative in ["src/Wild.Api/Constants.cs", "src/Wild.Api/Duplicates/Constants.cs"]:
        root = _root(wild_solution, relative)
        scoped = next(c for c in root.children if c.type == "file_scoped_namespace_declaration")
        namespaces.append(next(c.text.decode() for c in scoped.named_children))

    assert namespaces == ["Wild.Api", "Wild.Api.Duplicates"]
    assert slugify("Constants") == "constants"  # один и тот же slug при разных FQN
