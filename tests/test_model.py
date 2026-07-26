"""Проверка моделей данных и генерации JSON Schema (T03)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docpipe.hashing import stable_json_dumps
from docpipe.model import (
    Attribute,
    DocNode,
    FileParseResult,
    Manifest,
    Member,
    Module,
    ParserVersions,
    RawDeclaration,
    SourceSpan,
    Symbol,
)

PARSER_VERSIONS = ParserVersions(tree_sitter="0.26.0", grammar_c_sharp="0.23.5")


def _minimal_symbol() -> Symbol:
    return Symbol(
        fqn="Sample.Pricing.Api.Controllers.PricingController",
        name="PricingController",
        type_kind="class",
        namespace="Sample.Pricing.Api.Controllers",
        module="Sample.Pricing.Api",
        modifiers=["public", "sealed"],
        base_types=["Sample.Common.Web.BaseApiController"],
        base_types_raw=["BaseApiController"],
        base_type_closure=["ControllerBase", "Sample.Common.Web.BaseApiController"],
        attributes=[Attribute(name="Route", args=["api/v1/[controller]"])],
        sources=[SourceSpan(path="src/A/PricingController.cs", start=9, end=26)],
        xml_doc="Handles pricing requests.",
    )


def _minimal_manifest() -> Manifest:
    return Manifest(
        ruleset_version="2026-07-26.1",
        parser=PARSER_VERSIONS,
        modules=[
            Module(
                id="module:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
                name="Sample.Pricing.Api",
                csproj="src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
                target_frameworks=["net8.0", "net9.0"],
                project_references=["src/Sample.Common/Sample.Common.csproj"],
                domain="pricing",
                enrolled=True,
            )
        ],
        nodes=[
            DocNode(
                id="type:Sample.Pricing.Api.Controllers.PricingController",
                kind="controller",
                template="controller",
                title="PricingController",
                doc_path="docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md",
                parent="module:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
                module="Sample.Pricing.Api",
                domain="pricing",
                symbol=_minimal_symbol(),
                matched_rules=["controller.aspnet"],
                signature_hash="sha256:deadbeef",
            )
        ],
    )


def test_manifest_round_trip() -> None:
    """Сериализация и разбор обратно дают эквивалентный объект."""
    original = _minimal_manifest()
    restored = Manifest.model_validate(original.model_dump())
    assert restored == original


def test_manifest_round_trip_through_json() -> None:
    """Тот же круг, но через JSON — так манифест реально путешествует между шагами."""
    original = _minimal_manifest()
    restored = Manifest.model_validate_json(original.model_dump_json())
    assert restored == original


def test_schema_version_defaults_and_is_pinned() -> None:
    """Чужая версия схемы должна отвергаться явно, а не разбираться молча."""
    assert _minimal_manifest().schema_version == "1.0"
    payload = _minimal_manifest().model_dump()
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        Manifest.model_validate(payload)


def test_models_are_frozen() -> None:
    """Мутация собранной структуры почти всегда означает ошибку в пайплайне."""
    symbol = _minimal_symbol()
    with pytest.raises(ValidationError):
        symbol.fqn = "other"  # type: ignore[misc]


def test_unknown_field_is_rejected() -> None:
    """extra='forbid' ловит опечатки при загрузке чужого манифеста."""
    payload = _minimal_manifest().model_dump()
    payload["nodes"][0]["unexpected_field"] = 1
    with pytest.raises(ValidationError):
        Manifest.model_validate(payload)


def test_literal_fields_reject_typos() -> None:
    """type_kind — Literal, а не str: опечатка должна падать при разборе."""
    span = SourceSpan(path="a.cs", start=1, end=2)

    valid = RawDeclaration(name="X", type_kind="class", namespace="N", span=span)
    assert valid.type_kind == "class"

    with pytest.raises(ValidationError):
        RawDeclaration(
            name="X",
            type_kind="klass",  # type: ignore[arg-type]
            namespace="N",
            span=span,
        )


def test_partial_class_yields_several_sources() -> None:
    """Ключевое свойство модели: sources — список, а не одно значение."""
    symbol = _minimal_symbol().model_copy(
        update={
            "sources": [
                SourceSpan(path="src/A/PricingService.cs", start=6, end=19),
                SourceSpan(path="src/A/PricingService.Calculations.cs", start=3, end=6),
            ]
        }
    )
    assert len(symbol.sources) == 2


def test_collection_defaults_are_independent() -> None:
    """Классическая ловушка изменяемых значений по умолчанию."""
    first = FileParseResult(path="a.cs", content_hash="sha256:0")
    second = FileParseResult(path="b.cs", content_hash="sha256:1")
    assert first.usings == [] and second.usings == []
    assert first.usings is not second.usings


def test_member_and_attribute_defaults() -> None:
    member = Member(name="GetAsync", kind="method", signature="void GetAsync()", line=1, end_line=2)
    assert member.attributes == []
    assert member.xml_doc is None
    assert Attribute(name="HttpPost").args == []


def test_schema_generation_is_deterministic() -> None:
    """Схема должна быть байт-в-байт воспроизводимой: она коммитится в репозиторий."""
    first = stable_json_dumps(Manifest.model_json_schema())
    second = stable_json_dumps(Manifest.model_json_schema())
    assert first == second


def test_schema_is_valid_json_and_describes_manifest() -> None:
    schema = json.loads(stable_json_dumps(Manifest.model_json_schema()))
    assert schema["title"] == "Manifest"
    assert "nodes" in schema["properties"]
    assert "modules" in schema["properties"]
    # Все модели должны попасть в $defs — иначе схема неполна.
    for name in ["DocNode", "Symbol", "Module", "Attribute", "SourceSpan", "Endpoint"]:
        assert name in schema["$defs"], name


def test_schema_command_writes_stable_file(tmp_path: Path) -> None:
    """Повторный вызов команды не должен менять файл."""
    from typer.testing import CliRunner

    from docpipe.cli import app

    runner = CliRunner()
    out = tmp_path / "nested" / "doc-tree.schema.json"

    first = runner.invoke(app, ["schema", "--out", str(out)])
    assert first.exit_code == 0, first.output
    content_first = out.read_bytes()

    second = runner.invoke(app, ["schema", "--out", str(out)])
    assert second.exit_code == 0, second.output
    assert out.read_bytes() == content_first
