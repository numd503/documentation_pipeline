"""Проверка сборки индекса символов: FQN, слияние partial, резолв баз (T10)."""

from pathlib import Path

from docpipe.dotnet.parser import parse_source
from docpipe.dotnet.resolve import (
    build_symbol_index,
    declaration_fqn,
    index_by_fqn,
    strip_generics,
    symbol_key,
)
from docpipe.model import FileParseResult, Symbol
from tests.conftest import by_fqn, index_of

SAMPLE_FQNS = {
    "Sample.Common.Abstractions.IPricingProvider",
    "Sample.Common.Web.BaseApiController",
    "Sample.Pricing.Api.Controllers.PricingController",
    "Sample.Pricing.Api.Grid.RiskComputeService",
    "Sample.Pricing.Api.Models.PriceDto",
    "Sample.Pricing.Api.Program",
    "Sample.Pricing.Api.Providers.CurveProvider",
    "Sample.Pricing.Api.Services.IPricingService",
    "Sample.Pricing.Api.Services.PricingService",
    "Sample.Pricing.Api.Workflows.ValuationWorkflow",
}


def _synthetic(sources: dict[str, bytes], module: str = "m/M.csproj") -> dict[str, Symbol]:
    """Индекс из исходников в памяти. Все файлы принадлежат одному модулю."""
    results = [parse_source(text, path) for path, text in sources.items()]
    return build_symbol_index(results, dict.fromkeys(sources, module))


# --------------------------------------------------------------------------------------
# Критерии приёмки на фикстуре
# --------------------------------------------------------------------------------------


def test_index_contains_exactly_ten_symbols(sample_solution: Path) -> None:
    index = index_of(sample_solution)
    assert {symbol.fqn for symbol in index.values()} == SAMPLE_FQNS
    assert len(index) == 10


def test_partial_class_becomes_one_symbol(sample_solution: Path) -> None:
    """Две половинки `partial class` — один тип, но два источника."""
    symbol = by_fqn(index_of(sample_solution))["Sample.Pricing.Api.Services.PricingService"]

    assert [source.path for source in symbol.sources] == [
        "src/Sample.Pricing.Api/Services/PricingService.Calculations.cs",
        "src/Sample.Pricing.Api/Services/PricingService.cs",
    ]
    assert "Discount" in [member.name for member in symbol.members]
    assert "PriceAsync" in [member.name for member in symbol.members]


def test_base_type_resolved_through_using(sample_solution: Path) -> None:
    symbol = by_fqn(index_of(sample_solution))["Sample.Pricing.Api.Controllers.PricingController"]
    assert symbol.base_types == ["Sample.Common.Web.BaseApiController"]
    assert symbol.base_types_raw == ["BaseApiController"]


def test_external_base_type_stays_raw(sample_solution: Path) -> None:
    """`ControllerBase` живёт в NuGet-пакете, в индекс не попадает и не резолвится.

    Это не недоделка: правила классификации матчатся именно по таким именам.
    """
    symbol = by_fqn(index_of(sample_solution))["Sample.Common.Web.BaseApiController"]
    assert symbol.base_types == ["ControllerBase"]


def test_generic_argument_dropped_when_resolving(sample_solution: Path) -> None:
    symbol = by_fqn(index_of(sample_solution))["Sample.Pricing.Api.Providers.CurveProvider"]
    assert symbol.base_types == ["Sample.Common.Abstractions.IPricingProvider"]
    assert symbol.base_types_raw == ["IPricingProvider<string>"]


def test_nothing_is_ambiguous_in_the_fixture(sample_solution: Path) -> None:
    assert not any(symbol.ambiguous for symbol in index_of(sample_solution).values())


# --------------------------------------------------------------------------------------
# Ключ символа: FQN сам по себе типы не различает (находка ABP)
# --------------------------------------------------------------------------------------


def test_generic_arity_gives_separate_symbols(wild_solution: Path) -> None:
    """Три `ICrudAppService` с арностями 1, 2, 3 — три разных типа с одним FQN.

    Ключ по одному FQN слил бы их в один символ, и два документа из трёх
    исчезли бы. В ABP таких групп 112.
    """
    index = index_of(wild_solution)
    crud = [s for s in index.values() if s.fqn == "Wild.Api.Services.ICrudAppService"]

    assert len(crud) == 3
    assert sorted(len(s.type_parameters) for s in crud) == [1, 2, 3]
    assert len({symbol_key(s.module, s.fqn, len(s.type_parameters)) for s in crud}) == 3


def test_same_fqn_in_two_modules_gives_two_symbols() -> None:
    """Один FQN законно живёт в разных сборках — ключ без модуля склеил бы их.

    В ABP таких пар 155 (шаблоны проектов и параллельные реализации).
    """
    source = b"namespace N;\npublic class C { }\n"
    results = [parse_source(source, "a/C.cs"), parse_source(source, "b/C.cs")]
    index = build_symbol_index(results, {"a/C.cs": "a/A.csproj", "b/C.cs": "b/B.csproj"})

    assert len(index) == 2
    assert {s.module for s in index.values()} == {"a/A.csproj", "b/B.csproj"}
    assert {s.fqn for s in index.values()} == {"N.C"}


def test_symbol_key_format() -> None:
    """Формат совпадает с id узла на T15 — там добавляется только префикс `type:`."""
    assert symbol_key("src/A/A.csproj", "N.C", 2) == "src/A/A.csproj#N.C`2"


def test_index_by_fqn_returns_all_symbols_of_one_fqn(wild_solution: Path) -> None:
    grouped = index_by_fqn(index_of(wild_solution))
    assert len(grouped["Wild.Api.Services.ICrudAppService"]) == 3
    assert grouped["Wild.Api.Services.ICrudAppService"] == sorted(
        grouped["Wild.Api.Services.ICrudAppService"]
    )


# --------------------------------------------------------------------------------------
# Резолв имён
# --------------------------------------------------------------------------------------


def test_nearest_namespace_wins_and_is_not_ambiguous() -> None:
    """`A.B.Target` перекрывает `A.Target`, и это не неоднозначность.

    Правило «собрать все совпадения из namespace и usings разом» пометило бы
    этот случай ambiguous, хотя в C# ближнее имя просто перекрывает дальнее.
    """
    index = _synthetic(
        {
            "outer.cs": b"namespace A;\npublic class Target { }\n",
            "inner.cs": b"namespace A.B;\npublic class Target { }\n",
            "use.cs": b"namespace A.B;\npublic class User : Target { }\n",
        }
    )
    user = by_fqn(index)["A.B.User"]

    assert user.base_types == ["A.B.Target"]
    assert user.ambiguous is False


def test_using_is_consulted_only_when_namespace_fails() -> None:
    index = _synthetic(
        {
            "target.cs": b"namespace Lib;\npublic class Target { }\n",
            "use.cs": b"using Lib;\nnamespace App;\npublic class User : Target { }\n",
        }
    )
    assert by_fqn(index)["App.User"].base_types == ["Lib.Target"]


def test_two_usings_matching_is_ambiguous() -> None:
    """Вот здесь неоднозначность настоящая: имя видно через два using сразу."""
    index = _synthetic(
        {
            "one.cs": b"namespace Left;\npublic class Target { }\n",
            "two.cs": b"namespace Right;\npublic class Target { }\n",
            "use.cs": b"using Left;\nusing Right;\nnamespace App;\n"
            b"public class User : Target { }\n",
        }
    )
    user = by_fqn(index)["App.User"]

    assert user.ambiguous is True
    assert user.base_types == ["Left.Target"]  # лексикографически меньший


def test_fully_qualified_base_type() -> None:
    index = _synthetic(
        {
            "target.cs": b"namespace Lib.Deep;\npublic class Target { }\n",
            "use.cs": b"namespace App;\npublic class User : Lib.Deep.Target { }\n",
        }
    )
    assert by_fqn(index)["App.User"].base_types == ["Lib.Deep.Target"]


def test_global_namespace_type_is_found() -> None:
    """Пустой префикс namespace обязателен: глобальное пространство видно отовсюду."""
    index = _synthetic(
        {
            "target.cs": b"public class Target { }\n",
            "use.cs": b"namespace App;\npublic class User : Target { }\n",
        }
    )
    assert by_fqn(index)["App.User"].base_types == ["Target"]


def test_global_qualifier_is_stripped_from_base_type() -> None:
    index = _synthetic(
        {
            "target.cs": b"namespace Lib;\npublic class Target { }\n",
            "use.cs": b"namespace App;\npublic class User : global::Lib.Target { }\n",
        }
    )
    assert by_fqn(index)["App.User"].base_types == ["Lib.Target"]


def test_global_using_applies_to_the_whole_module() -> None:
    """`global using` действует на проект, а не на файл, где объявлен.

    Обычно все они собраны в одном `GlobalUsings.cs`, поэтому реализация,
    берущая только usings своего файла, не резолвила бы ни один тип,
    видимый исключительно через них.
    """
    index = _synthetic(
        {
            "target.cs": b"namespace Lib;\npublic class Target { }\n",
            "globals.cs": b"global using Lib;\n",
            "use.cs": b"namespace App;\npublic class User : Target { }\n",
        }
    )
    assert by_fqn(index)["App.User"].base_types == ["Lib.Target"]


def test_global_using_does_not_leak_into_another_module() -> None:
    """Проекты изолированы: `global using` одного не виден в другом."""
    results = [
        parse_source(b"namespace Lib;\npublic class Target { }\n", "lib/Target.cs"),
        parse_source(b"global using Lib;\n", "a/Globals.cs"),
        parse_source(b"namespace App;\npublic class User : Target { }\n", "b/User.cs"),
    ]
    index = build_symbol_index(
        results,
        {"lib/Target.cs": "lib/L.csproj", "a/Globals.cs": "a/A.csproj", "b/User.cs": "b/B.csproj"},
    )
    assert by_fqn(index)["App.User"].base_types == ["Target"]


def test_interface_list_is_resolved_per_element() -> None:
    index = _synthetic(
        {
            "i.cs": b"namespace Lib;\npublic interface IOne { }\npublic interface ITwo { }\n",
            "use.cs": b"using Lib;\nnamespace App;\npublic class C : IOne, ITwo { }\n",
        }
    )
    assert by_fqn(index)["App.C"].base_types == ["Lib.IOne", "Lib.ITwo"]


# --------------------------------------------------------------------------------------
# Слияние
# --------------------------------------------------------------------------------------


def test_merge_unions_modifiers_attributes_and_members() -> None:
    index = _synthetic(
        {
            "b.cs": b"namespace N;\n[Second]\npublic partial class C { void Two() { } }\n",
            "a.cs": b"namespace N;\n[First]\ninternal partial class C { void One() { } }\n",
        }
    )
    symbol = by_fqn(index)["N.C"]

    assert symbol.modifiers == ["internal", "partial", "public"]
    assert [a.name for a in symbol.attributes] == ["First", "Second"]
    assert [m.name for m in symbol.members] == ["One", "Two"]  # порядок по (path, line, name)
    assert [s.path for s in symbol.sources] == ["a.cs", "b.cs"]


def test_merge_takes_first_non_empty_xml_doc() -> None:
    index = _synthetic(
        {
            "b.cs": b"namespace N;\n/// <summary>From b.</summary>\npublic partial class C { }\n",
            "a.cs": b"namespace N;\npublic partial class C { }\n",
        }
    )
    # a.cs идёт первым по имени файла, но документации в нём нет.
    assert by_fqn(index)["N.C"].xml_doc == "From b."


def test_duplicate_attributes_are_deduplicated() -> None:
    index = _synthetic(
        {
            "a.cs": b'namespace N;\n[Route("api")]\npublic partial class C { }\n',
            "b.cs": b'namespace N;\n[Route("api")]\npublic partial class C { }\n',
        }
    )
    assert len(by_fqn(index)["N.C"].attributes) == 1


def test_nested_type_and_namespaced_type_can_share_fqn() -> None:
    """`namespace A { class B { class C } }` и `namespace A.B { class C }` дают один FQN.

    Оба остаются в индексе как один символ — разделить их нечем, зато видно
    по двум источникам у не-partial типа.
    """
    index = _synthetic(
        {
            "nested.cs": b"namespace A;\npublic class B { public class C { } }\n",
            "flat.cs": b"namespace A.B;\npublic class C { }\n",
        }
    )
    symbol = by_fqn(index)["A.B.C"]
    assert len(symbol.sources) == 2
    assert "partial" not in symbol.modifiers


# --------------------------------------------------------------------------------------
# Вспомогательные функции и общие свойства
# --------------------------------------------------------------------------------------


def test_strip_generics_removes_balanced_groups() -> None:
    assert strip_generics("IPricingProvider<string>") == "IPricingProvider"
    assert strip_generics("A.B<C<D>>.E") == "A.B.E"
    assert strip_generics("Plain") == "Plain"
    assert strip_generics("global::Lib.Target") == "Lib.Target"
    assert strip_generics("Dictionary<string, List<int>>") == "Dictionary"


def test_declaration_fqn_skips_empty_parts() -> None:
    declaration = parse_source(b"public class C { }\n", "x.cs").declarations[0]
    assert declaration_fqn(declaration) == "C"

    nested = parse_source(b"namespace N;\nclass O { class I { } }\n", "x.cs").declarations
    assert {declaration_fqn(d) for d in nested} == {"N.O", "N.O.I"}


def test_files_without_module_are_skipped() -> None:
    result = parse_source(b"namespace N;\npublic class C { }\n", "loose/C.cs")
    assert build_symbol_index([result], {}) == {}


def test_file_without_declarations_contributes_nothing() -> None:
    result = FileParseResult(path="a.cs", content_hash="sha256:0")
    assert build_symbol_index([result], {"a.cs": "m/M.csproj"}) == {}


def test_index_is_deterministic(sample_solution: Path) -> None:
    assert index_of(sample_solution) == index_of(sample_solution)


def test_index_keys_are_sorted(sample_solution: Path) -> None:
    keys = list(index_of(sample_solution))
    assert keys == sorted(keys)
