"""Проверка разбора директив using (T08)."""

from pathlib import Path

from docpipe.dotnet.parser import parse_file, parse_source

# --------------------------------------------------------------------------------------
# Канонические случаи из плана
# --------------------------------------------------------------------------------------


def test_plain_usings(sample_solution: Path) -> None:
    result = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    )
    assert result.usings == [
        "Microsoft.AspNetCore.Mvc",
        "Sample.Common.Web",
        "Sample.Pricing.Api.Services",
    ]
    assert result.global_usings == []


def test_all_four_forms_in_one_file(wild_solution: Path) -> None:
    """`global` ×2, `using static`, алиас — файл существует ровно ради этого."""
    result = parse_file(wild_solution / "src/Wild.Api/GlobalUsings.cs", wild_solution)

    assert result.global_usings == ["System.Text.Json", "Wild.Api.Contracts"]
    assert result.usings == []


# --------------------------------------------------------------------------------------
# Отбрасываемые формы
# --------------------------------------------------------------------------------------


def test_alias_is_ignored() -> None:
    """Алиас именует один тип, а не пространство имён, и резолву не помогает."""
    source = b"using Json = System.Text.Json.JsonSerializer;\nusing System.IO;\nnamespace N;\n"
    result = parse_source(source, "N.cs")

    assert result.usings == ["System.IO"]
    assert result.global_usings == []


def test_using_static_is_ignored() -> None:
    """`using static` импортирует статические члены, а не типы."""
    source = b"using static System.Math;\nusing System.IO;\nnamespace N;\n"
    assert parse_source(source, "N.cs").usings == ["System.IO"]


def test_global_using_static_does_not_become_a_global_using() -> None:
    """Порядок проверок: сначала `static`, потом `global`.

    Реализация, проверяющая `global` первой, положила бы сюда `System.Console`.
    """
    source = b"global using static System.Console;\nglobal using System.IO;\nnamespace N;\n"
    result = parse_source(source, "N.cs")

    assert result.global_usings == ["System.IO"]
    assert result.usings == []


def test_global_alias_is_ignored() -> None:
    source = b"global using Shim = A.B.C;\nnamespace N;\n"
    result = parse_source(source, "N.cs")

    assert result.usings == []
    assert result.global_usings == []


# --------------------------------------------------------------------------------------
# Формы, найденные в реальных репозиториях
# --------------------------------------------------------------------------------------


def test_global_qualifier_is_stripped() -> None:
    """`using global::AutoMapper;` импортирует ровно `AutoMapper` (найдено в ABP).

    Здесь `global` — квалификатор внутри имени, а не признак global using:
    прямым потомком директивы он не является. Реализация, ищущая слово `global`
    в тексте, отнесла бы эту директиву в `global_usings`.
    """
    source = b"using global::AutoMapper;\nnamespace N;\n"
    result = parse_source(source, "N.cs")

    assert result.usings == ["AutoMapper"]
    assert result.global_usings == []


def test_extern_alias_qualifier_is_kept() -> None:
    """Алиас сборки снять нельзя — без его разрешения имя всё равно не резолвится."""
    source = b"using MyLib::Some.Namespace;\nnamespace N;\n"
    assert parse_source(source, "N.cs").usings == ["MyLib::Some.Namespace"]


def test_using_inside_block_namespace_is_found() -> None:
    """Директива может лежать внутри блочного namespace — в ABP таких 8.

    Обход только детей `compilation_unit` их не увидит.
    """
    source = b"""using Top.Level;
namespace N
{
    using Inside.Namespace;
    class C { }
}
"""
    assert parse_source(source, "N.cs").usings == ["Inside.Namespace", "Top.Level"]


def test_using_statements_in_method_bodies_are_not_directives() -> None:
    """`using var s = …` и `using (…) { }` — операторы, а не директивы."""
    source = b"""using System.IO;
namespace N;
class C
{
    void M()
    {
        using var stream = File.OpenRead("x");
        using (var other = File.OpenRead("y")) { }
    }
}
"""
    result = parse_source(source, "N.cs")

    assert result.usings == ["System.IO"]
    assert result.parse_errors == 0


def test_single_segment_namespace() -> None:
    """Односегментное имя — узел `identifier`, а не `qualified_name`."""
    assert parse_source(b"using System;\nnamespace N;\n", "N.cs").usings == ["System"]


# --------------------------------------------------------------------------------------
# Свойства списков
# --------------------------------------------------------------------------------------


def test_usings_are_sorted_and_deduplicated() -> None:
    source = b"using B.Two;\nusing A.One;\nusing B.Two;\nnamespace N;\n"
    assert parse_source(source, "N.cs").usings == ["A.One", "B.Two"]


def test_file_without_usings() -> None:
    result = parse_source(b"namespace N;\npublic class C { }\n", "N.cs")
    assert result.usings == []
    assert result.global_usings == []


def test_usings_are_deterministic(wild_solution: Path) -> None:
    first = parse_file(wild_solution / "src/Wild.Api/GlobalUsings.cs", wild_solution)
    second = parse_file(wild_solution / "src/Wild.Api/GlobalUsings.cs", wild_solution)
    assert (first.usings, first.global_usings) == (second.usings, second.global_usings)
