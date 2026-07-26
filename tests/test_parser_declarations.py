"""Проверка разбора объявлений типов (T06)."""

from pathlib import Path

import pytest

from docpipe.dotnet.parser import normalize_type_text, parse_file, parse_source
from docpipe.model import Attribute

SAMPLE_CS = [
    "src/Sample.Common/Abstractions/IPricingProvider.cs",
    "src/Sample.Common/Web/BaseApiController.cs",
    "src/Sample.Pricing.Api/Controllers/PricingController.cs",
    "src/Sample.Pricing.Api/Grid/RiskComputeService.cs",
    "src/Sample.Pricing.Api/Models/PriceDto.cs",
    "src/Sample.Pricing.Api/Program.cs",
    "src/Sample.Pricing.Api/Providers/CurveProvider.cs",
    "src/Sample.Pricing.Api/Services/IPricingService.cs",
    "src/Sample.Pricing.Api/Services/PricingService.Calculations.cs",
    "src/Sample.Pricing.Api/Services/PricingService.cs",
    "src/Sample.Pricing.Api/Workflows/ValuationWorkflow.cs",
]


# --------------------------------------------------------------------------------------
# Канонический случай из плана
# --------------------------------------------------------------------------------------


def test_controller_declaration(sample_solution: Path) -> None:
    result = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    )
    assert result.path == "src/Sample.Pricing.Api/Controllers/PricingController.cs"
    assert result.parse_errors == 0
    assert result.content_hash.startswith("sha256:")
    assert len(result.declarations) == 1

    declaration = result.declarations[0]
    assert declaration.name == "PricingController"
    assert declaration.type_kind == "class"
    assert declaration.namespace == "Sample.Pricing.Api.Controllers"
    assert declaration.containing_type is None
    assert declaration.modifiers == ["public", "sealed"]
    assert declaration.base_types == ["BaseApiController"]
    assert declaration.attributes == [
        Attribute(name="Route", args=["api/v1/[controller]"], named_args={})
    ]
    assert declaration.xml_doc == "Handles pricing requests."


def test_span_covers_attributes(sample_solution: Path) -> None:
    """Атрибуты — потомки объявления, поэтому span начинается с них, а не с `public`.

    Это желаемое поведение: ссылка в документации должна вести на начало
    объявления целиком.
    """
    result = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    )
    span = result.declarations[0].span
    assert span.path == "src/Sample.Pricing.Api/Controllers/PricingController.cs"
    assert span.start == 8  # строка с [Route(...)]
    assert span.end == 26


def test_generic_base_type_kept_whole(sample_solution: Path) -> None:
    result = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Providers/CurveProvider.cs", sample_solution
    )
    assert result.declarations[0].base_types == ["IPricingProvider<string>"]


def test_interface_and_type_parameters(sample_solution: Path) -> None:
    result = parse_file(
        sample_solution / "src/Sample.Common/Abstractions/IPricingProvider.cs", sample_solution
    )
    declaration = result.declarations[0]
    assert declaration.type_kind == "interface"
    assert declaration.type_parameters == ["T"]


def test_record_kind(sample_solution: Path) -> None:
    result = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Models/PriceDto.cs", sample_solution
    )
    assert result.declarations[0].type_kind == "record"


@pytest.mark.parametrize("relative", SAMPLE_CS)
def test_sample_solution_parses_without_errors(sample_solution: Path, relative: str) -> None:
    assert parse_file(sample_solution / relative, sample_solution).parse_errors == 0


# --------------------------------------------------------------------------------------
# Случаи из реальных репозиториев (WildSolution)
# --------------------------------------------------------------------------------------


def test_multiline_base_type_is_normalized(wild_solution: Path) -> None:
    """Главная находка прогона на eShopOnWeb.

    Без нормализации в base_types попал бы текст с \\n и отступами, и
    signature_hash начал бы зависеть от форматирования файла.
    """
    result = parse_file(
        wild_solution / "src/Wild.Api/Endpoints/AuthenticateEndpoint.cs", wild_solution
    )
    assert result.declarations[0].base_types == [
        "EndpointBaseAsync.WithRequest<AuthenticateRequest>.WithActionResult<AuthenticateResponse>"
    ]


def test_block_form_namespace(wild_solution: Path) -> None:
    result = parse_file(wild_solution / "src/Wild.Api/Legacy/BlockNamespace.cs", wild_solution)
    assert result.declarations[0].namespace == "Wild.Api.Legacy"


def test_global_namespace_is_empty_string(wild_solution: Path) -> None:
    result = parse_file(wild_solution / "src/Wild.Api/Legacy/NoNamespace.cs", wild_solution)
    declaration = result.declarations[0]
    assert declaration.name == "GlobalNamespaceService"
    assert declaration.namespace == ""


def test_nested_type_records_its_container(wild_solution: Path) -> None:
    result = parse_file(wild_solution / "src/Wild.Api/Pages/Login.cshtml.cs", wild_solution)
    names = {(d.name, d.containing_type) for d in result.declarations}
    assert names == {("LoginModel", None), ("InputModel", "LoginModel")}


def test_file_without_type_declarations(wild_solution: Path) -> None:
    """Program.cs на top-level statements не объявляет типов вовсе."""
    result = parse_file(wild_solution / "src/Wild.Api/Program.cs", wild_solution)
    assert result.declarations == []
    assert result.parse_errors == 0


# --------------------------------------------------------------------------------------
# Конструкции, разобранные из строк
# --------------------------------------------------------------------------------------


def test_record_struct_is_distinguished() -> None:
    """Отдельного узла record_struct в грамматике нет — различаем по потомку `struct`."""
    source = b"namespace N;\npublic readonly record struct Point(int X, int Y);\n"
    declaration = parse_source(source, "N.cs").declarations[0]
    assert declaration.type_kind == "record_struct"

    plain = parse_source(b"namespace N;\npublic record Money(decimal V);\n", "N.cs").declarations[0]
    assert plain.type_kind == "record"


def test_deeply_nested_type_keeps_full_chain() -> None:
    """При двойной вложенности одного имени контейнера не хватит для FQN."""
    source = b"""namespace N;
public class Outer { public class Middle { public class Inner { } } }
"""
    chains = {d.name: d.containing_type for d in parse_source(source, "N.cs").declarations}
    assert chains == {"Outer": None, "Middle": "Outer", "Inner": "Outer.Middle"}


def test_nested_block_namespaces_are_joined() -> None:
    source = b"namespace A.B { namespace C { public class X { } } }\n"
    assert parse_source(source, "N.cs").declarations[0].namespace == "A.B.C"


def test_attribute_arguments() -> None:
    source = b"""namespace N;
[Route(@"api\\v1"), Obsolete("old", DiagnosticId = "X", UrlFormat = "u")]
[System.ComponentModel.Description("qualified")]
[HttpPost]
public class X { }
"""
    attributes = parse_source(source, "N.cs").declarations[0].attributes
    by_name = {a.name: a for a in attributes}

    # Verbatim-строка: единый лист, кавычки и @ снимаются вручную.
    assert by_name["Route"].args == ["api\\v1"]
    # Именованные аргументы отсортированы по ключу.
    assert by_name["Obsolete"].args == ["old"]
    assert list(by_name["Obsolete"].named_args) == ["DiagnosticId", "UrlFormat"]
    # Квалифицированное имя приводится к простому.
    assert "Description" in by_name
    # Атрибут без аргументов.
    assert by_name["HttpPost"].args == []


def test_attribute_suffix_is_stripped() -> None:
    source = b'namespace N;\n[RouteAttribute("r")]\npublic class X { }\n'
    assert parse_source(source, "N.cs").declarations[0].attributes[0].name == "Route"


def test_xml_doc_spanning_several_lines() -> None:
    source = b"""namespace N;

/// <summary>
/// Computes prices
/// for instruments.
/// </summary>
/// <remarks>Ignored.</remarks>
public class X { }
"""
    assert (
        parse_source(source, "N.cs").declarations[0].xml_doc == "Computes prices for instruments."
    )


def test_xml_doc_absent_or_without_summary() -> None:
    without_doc = parse_source(b"namespace N;\npublic class X { }\n", "N.cs")
    assert without_doc.declarations[0].xml_doc is None

    plain_comment = parse_source(b"namespace N;\n// not xml doc\npublic class X { }\n", "N.cs")
    assert plain_comment.declarations[0].xml_doc is None

    no_summary = parse_source(
        b"namespace N;\n/// <remarks>only</remarks>\npublic class X { }\n", "N.cs"
    )
    assert no_summary.declarations[0].xml_doc is None


def test_xml_doc_inner_tags_are_stripped() -> None:
    source = (
        b"namespace N;\n"
        b'/// <summary>See <see cref="Y"/> for details.</summary>\n'
        b"public class X { }\n"
    )
    assert parse_source(source, "N.cs").declarations[0].xml_doc == "See for details."


# --------------------------------------------------------------------------------------
# Препроцессор — находки прогона на ABP, см. docs/findings-abp.md
# --------------------------------------------------------------------------------------


def test_types_inside_preprocessor_are_found() -> None:
    """`#if` вокруг объявления типа разбору не мешает: обе ветки попадают в дерево."""
    source = b"""namespace N;
#if DEBUG
public class OnlyDebug { }
#else
public class OnlyRelease { }
#endif
public class Always { }
"""
    result = parse_source(source, "N.cs")
    assert result.parse_errors == 0
    assert [d.name for d in result.declarations] == ["OnlyDebug", "OnlyRelease", "Always"]


def test_preprocessor_inside_attribute_loses_the_declaration(wild_solution: Path) -> None:
    """Известное ограничение tree-sitter: `#if` внутри списка аргументов атрибута.

    Директива рвёт выражение. Атрибут — потомок объявления, поэтому ломается
    и само объявление: остаток файла переразбирается как top-level statements,
    и тип исчезает из вывода целиком. Так в ABP теряется `CmsKitWebUnifiedModule`
    (8 ошибок, 0 объявлений) — фикстура воспроизводит его один в один.

    Восстановится ли грамматика, зависит от того, что идёт за атрибутом:
    на упрощённом классе она справляется, на настоящем — нет. Опираться на это
    нельзя, поэтому единственный надёжный признак беды — `parse_errors > 0`
    при пустом списке объявлений, и T20 обязан ловить именно эту комбинацию.
    """
    result = parse_file(wild_solution / "src/Wild.Api/Modules/ConditionalModule.cs", wild_solution)
    assert result.parse_errors > 0
    assert result.declarations == []


def test_preprocessor_around_usings_alone_is_harmless() -> None:
    """Контроль к предыдущему тесту: дело именно в аргументах атрибута."""
    source = b"""#if EF
using A.Ef;
#elif Mongo
using A.Mongo;
#endif
namespace N;
[DependsOn(typeof(CoreModule))]
public class AppModule { }
"""
    result = parse_source(source, "N.cs")
    assert result.parse_errors == 0
    assert [d.name for d in result.declarations] == ["AppModule"]


def test_generic_arity_overloads_share_name_and_namespace(wild_solution: Path) -> None:
    """Три разных типа с одинаковыми именем и FQN, различимые только арностью.

    В ABP таких групп 112. Ключ, построенный по одному FQN, склеил бы их,
    и суффикс `-{sha256(fqn)[:8]}` не помог бы: FQN у них совпадает.
    """
    result = parse_file(wild_solution / "src/Wild.Api/Services/CrudAppService.cs", wild_solution)

    assert {d.name for d in result.declarations} == {"ICrudAppService"}
    assert {d.namespace for d in result.declarations} == {"Wild.Api.Services"}
    assert sorted(len(d.type_parameters) for d in result.declarations) == [1, 2, 3]


def test_declarations_are_sorted_by_position() -> None:
    source = b"namespace N;\npublic class B { }\npublic class A { }\n"
    assert [d.name for d in parse_source(source, "N.cs").declarations] == ["B", "A"]


def test_broken_source_is_counted_not_raised() -> None:
    """Сломанный файл не должен ронять прогон: tree-sitter отдаёт дерево с ERROR."""
    result = parse_source(b"namespace N;\npublic class X { void ( ; }\n", "N.cs")
    assert result.parse_errors > 0
    assert result.declarations  # объявление всё равно найдено


def test_parsing_is_deterministic(sample_solution: Path) -> None:
    first = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs", sample_solution
    )
    second = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs", sample_solution
    )
    assert first == second


# --------------------------------------------------------------------------------------
# normalize_type_text
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ControllerBase", "ControllerBase"),
        ("IPricingProvider<string>", "IPricingProvider<string>"),
        ("A\n    .B<C>\n    .D<E>", "A.B<C>.D<E>"),
        ("  Sample . Common . Web . Base  ", "Sample.Common.Web.Base"),
        ("IEndpoint<IResult,\n  ListRequest>", "IEndpoint<IResult, ListRequest>"),
    ],
)
def test_normalize_type_text(raw: str, expected: str) -> None:
    assert normalize_type_text(raw) == expected
