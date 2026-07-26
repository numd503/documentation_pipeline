"""Проверка движка правил классификации (T14)."""

from pathlib import Path

import pytest
import yaml

from docpipe.classify import (
    Classification,
    base_type_candidates,
    classify,
    is_excluded,
    load_ruleset,
)
from docpipe.dotnet.parser import parse_file, parse_source
from docpipe.dotnet.resolve import build_symbol_index, compute_closures
from docpipe.model import Symbol
from tests.conftest import by_fqn, index_of

RULES = Path("rules/dotnet.yaml")


@pytest.fixture
def ruleset():  # type: ignore[no-untyped-def]
    return load_ruleset(RULES)


@pytest.fixture
def sample_symbols(sample_solution: Path) -> dict[str, Symbol]:
    index = compute_closures(index_of(sample_solution))
    return {symbol.name: symbol for symbol in index.values()}


def _symbol(source: bytes, path: str = "src/App/X.cs") -> Symbol:
    result = parse_source(source, path)
    index = compute_closures(build_symbol_index([result], {path: "src/App/App.csproj"}))
    return next(iter(index.values()))


# --------------------------------------------------------------------------------------
# Критерии приёмки на фикстуре
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind", "matched"),
    [
        ("PricingController", "controller", ["controller.aspnet"]),
        ("BaseApiController", "controller", ["controller.aspnet"]),
        ("RiskComputeService", "ignite_service", ["ignite.service", "service"]),
        ("ValuationWorkflow", "workflow", ["workflow"]),
        ("CurveProvider", "provider", ["provider"]),
        ("PricingService", "service", ["service"]),
    ],
)
def test_classified_symbols(
    sample_symbols: dict[str, Symbol], ruleset, name: str, kind: str, matched: list[str]
) -> None:
    result = classify(sample_symbols[name], ruleset)
    assert result is not None
    assert result.kind == kind
    assert result.matched_rules == matched


def test_priority_decides_and_audit_keeps_both(sample_symbols: dict[str, Symbol], ruleset) -> None:
    """Главный тест приоритетов: совпали два правила, победило Ignite.

    Если бы `matched_rules` содержал только победителя, было бы не видно,
    что `service` тоже сработал, — и настройка правил превратилась бы в гадание.
    """
    result = classify(sample_symbols["RiskComputeService"], ruleset)
    assert result == Classification(
        kind="ignite_service",
        template="ignite-service",
        matched_rules=["ignite.service", "service"],
    )


def test_interface_is_not_classified(sample_symbols: dict[str, Symbol], ruleset) -> None:
    """`IPricingService` не проходит `type_kind: [class, record]` правила `service`."""
    assert classify(sample_symbols["IPricingService"], ruleset) is None
    assert classify(sample_symbols["IPricingProvider"], ruleset) is None


def test_dto_is_excluded_by_name(sample_symbols: dict[str, Symbol], ruleset) -> None:
    assert is_excluded(sample_symbols["PriceDto"], ruleset)
    assert classify(sample_symbols["PriceDto"], ruleset) is None


def test_program_matches_nothing(sample_symbols: dict[str, Symbol], ruleset) -> None:
    """Не исключён, но и ни под одно правило не подходит — это `unclassified`."""
    assert not is_excluded(sample_symbols["Program"], ruleset)
    assert classify(sample_symbols["Program"], ruleset) is None


def test_generated_file_is_excluded_by_path(sample_solution: Path, ruleset) -> None:
    """`obj/**` отбрасывается ещё на обходе ФС, поэтому символ строится напрямую.

    Правило всё равно нужно: `exclude.path_glob` работает и тогда, когда файл
    попал в разбор из кэша или из scope-режима.
    """
    relative = "src/Sample.Pricing.Api/obj/Debug/net8.0/Sample.Generated.g.cs"
    result = parse_file(sample_solution / relative, sample_solution)
    index = build_symbol_index(
        [result], {relative: "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"}
    )
    symbol = next(iter(index.values()))

    assert symbol.name == "GeneratedService"
    assert is_excluded(symbol, ruleset)
    assert classify(symbol, ruleset) is None


def test_exactly_six_symbols_are_classified(sample_symbols: dict[str, Symbol], ruleset) -> None:
    classified = {n: classify(s, ruleset) for n, s in sample_symbols.items()}
    assert sum(1 for c in classified.values() if c) == 6
    assert sum(1 for n, s in sample_symbols.items() if is_excluded(s, ruleset)) == 1


# --------------------------------------------------------------------------------------
# base_type_candidates
# --------------------------------------------------------------------------------------


def test_qualified_name_gives_last_segment() -> None:
    candidates = base_type_candidates("Sample.Common.Web.BaseApiController")
    assert "BaseApiController" in candidates
    assert "Sample.Common.Web.BaseApiController" in candidates


def test_qualified_name_does_not_give_first_segment() -> None:
    """Иначе правило `base_type: ["Sample"]` совпало бы со всем подряд."""
    assert "Sample" not in base_type_candidates("Sample.Common.Web.BaseApiController")


def test_fluent_base_gives_first_link() -> None:
    """Реальный случай из eShopOnWeb: значимо первое звено, а не последнее."""
    text = (
        "EndpointBaseAsync.WithRequest<AuthenticateRequest>.WithActionResult<AuthenticateResponse>"
    )
    candidates = base_type_candidates(text)

    assert "EndpointBaseAsync" in candidates
    assert text in candidates


def test_generic_base_gives_bare_name() -> None:
    candidates = base_type_candidates("IPricingProvider<string>")
    assert candidates == {"IPricingProvider", "IPricingProvider<string>"}


def test_nested_generic_arguments_do_not_add_noise() -> None:
    text = "IEndpoint<IResult, ListRequest, IRepository<CatalogItem>>"
    assert base_type_candidates(text) == {"IEndpoint", text}


def test_fluent_base_matches_rule(wild_solution: Path) -> None:
    """Правило по первому звену обязано срабатывать на настоящем файле фикстуры."""
    symbol = by_fqn(compute_closures(index_of(wild_solution)))[
        "Wild.Api.Endpoints.AuthenticateEndpoint"
    ]
    ruleset = load_ruleset(RULES)

    assert "EndpointBaseAsync" in base_type_candidates(symbol.base_types_raw[0])
    assert classify(symbol, ruleset) is None  # в наборе по умолчанию правила REPR нет


# --------------------------------------------------------------------------------------
# Предикаты
# --------------------------------------------------------------------------------------


def _ruleset_with(when: dict, tmp_path: Path, exclude: dict | None = None):  # type: ignore[no-untyped-def]
    path = tmp_path / "r.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "ruleset_version": "test",
                "exclude": exclude or {},
                "rules": [{"id": "r", "kind": "k", "template": "t", "priority": 1, "when": when}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return load_ruleset(path)


def test_attribute_predicate(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace N;\n[ApiController]\npublic class C { }\n")
    assert classify(symbol, _ruleset_with({"attribute": ["ApiController"]}, tmp_path))
    assert classify(symbol, _ruleset_with({"attribute": ["Route"]}, tmp_path)) is None


def test_modifier_and_type_kind_predicates(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace N;\npublic sealed class C { }\n")
    assert classify(symbol, _ruleset_with({"modifier": ["sealed"]}, tmp_path))
    assert classify(symbol, _ruleset_with({"type_kind": ["class"]}, tmp_path))
    assert classify(symbol, _ruleset_with({"type_kind": ["interface"]}, tmp_path)) is None


def test_namespace_and_name_regex_predicates(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace Sample.Api;\npublic class Handler { }\n")
    assert classify(symbol, _ruleset_with({"namespace_regex": [r"Sample\..*"]}, tmp_path))
    assert classify(symbol, _ruleset_with({"name_regex": ["^Hand.*r$"]}, tmp_path))
    assert classify(symbol, _ruleset_with({"name_regex": ["Hand"]}, tmp_path)) is None  # fullmatch


def test_path_glob_predicate_understands_double_star(tmp_path: Path) -> None:
    """`fnmatch` не поймал бы файл в корне: `**/` требует хотя бы один сегмент."""
    symbol = _symbol(b"namespace N;\npublic class C { }\n", path="Handlers/C.cs")
    assert classify(symbol, _ruleset_with({"path_glob": ["**/Handlers/**"]}, tmp_path))


def test_has_member_with_attribute_predicate(tmp_path: Path) -> None:
    symbol = _symbol(b'namespace N;\npublic class C { [HttpGet("x")] public void M() { } }\n')
    assert classify(symbol, _ruleset_with({"has_member_with_attribute": ["HttpGet"]}, tmp_path))
    assert (
        classify(symbol, _ruleset_with({"has_member_with_attribute": ["HttpPost"]}, tmp_path))
        is None
    )


def test_nested_any_inside_all(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace N;\npublic class ValuationWorkflow { }\n")
    when = {
        "all": [
            {"type_kind": ["class", "record"]},
            {"any": [{"name_suffix": ["Workflow"]}, {"inherits": ["IWorkflow"]}]},
        ]
    }
    assert classify(symbol, _ruleset_with(when, tmp_path))


# --------------------------------------------------------------------------------------
# Исключения
# --------------------------------------------------------------------------------------


def test_require_public_drops_internal_types(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace N;\ninternal class Handler { }\n")
    ruleset = _ruleset_with({"type_kind": ["class"]}, tmp_path, exclude={"require_public": True})
    assert is_excluded(symbol, ruleset)


def test_type_kind_deny(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace N;\npublic enum Kind { A }\n")
    ruleset = _ruleset_with({"type_kind": ["enum"]}, tmp_path, exclude={"type_kind_deny": ["enum"]})
    assert is_excluded(symbol, ruleset)


def test_exclusion_wins_over_matching_rule(tmp_path: Path) -> None:
    symbol = _symbol(b"namespace N;\npublic class PriceDto { }\n")
    ruleset = _ruleset_with({"type_kind": ["class"]}, tmp_path, exclude={"name_regex": ["^.*Dto$"]})
    assert classify(symbol, ruleset) is None


# --------------------------------------------------------------------------------------
# Загрузка и проверка набора
# --------------------------------------------------------------------------------------


def test_default_ruleset_loads() -> None:
    ruleset = load_ruleset(RULES)
    assert ruleset.ruleset_version == "2026-07-26.1"
    assert {rule.id for rule in ruleset.rules} == {
        "controller.aspnet",
        "ignite.service",
        "ignite.compute",
        "workflow",
        "provider",
        "repository",
        "service",
    }


def test_unknown_predicate_fails_at_load(tmp_path: Path) -> None:
    """Опечатка в предикате иначе дала бы правило, которое никогда не срабатывает.

    Понять это можно было бы только по счётчику `unclassified` — то есть никак.
    """
    path = tmp_path / "r.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "ruleset_version": "test",
                "rules": [
                    {
                        "id": "r",
                        "kind": "k",
                        "template": "t",
                        "priority": 1,
                        "when": {"attribut": ["ApiController"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="неизвестный предикат"):
        load_ruleset(path)


def test_duplicate_rule_id_fails_at_load(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    rule = {
        "id": "r",
        "kind": "k",
        "template": "t",
        "priority": 1,
        "when": {"type_kind": ["class"]},
    }
    path.write_text(
        yaml.safe_dump({"ruleset_version": "t", "rules": [rule, dict(rule)]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="повтор id"):
        load_ruleset(path)


def test_bad_regex_fails_at_load(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "ruleset_version": "t",
                "rules": [
                    {
                        "id": "r",
                        "kind": "k",
                        "template": "t",
                        "priority": 1,
                        "when": {"name_regex": ["["]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="регулярное выражение"):
        load_ruleset(path)


def test_missing_rule_field_fails_at_load(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(
        yaml.safe_dump({"ruleset_version": "t", "rules": [{"id": "r", "kind": "k"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="без полей"):
        load_ruleset(path)


# --------------------------------------------------------------------------------------
# Свойства
# --------------------------------------------------------------------------------------


def test_rule_order_in_file_does_not_matter(tmp_path: Path) -> None:
    """При равном приоритете побеждает меньший id, а не порядок строк в YAML."""
    symbol = _symbol(b"namespace N;\npublic class ThingService { }\n")
    rules = [
        {
            "id": "b.rule",
            "kind": "second",
            "template": "t",
            "priority": 10,
            "when": {"name_suffix": ["Service"]},
        },
        {
            "id": "a.rule",
            "kind": "first",
            "template": "t",
            "priority": 10,
            "when": {"type_kind": ["class"]},
        },
    ]
    results = []
    for order in (rules, list(reversed(rules))):
        path = tmp_path / f"r{len(results)}.yaml"
        path.write_text(yaml.safe_dump({"ruleset_version": "t", "rules": order}), encoding="utf-8")
        results.append(classify(symbol, load_ruleset(path)))

    assert results[0] == results[1]
    assert results[0] is not None
    assert results[0].kind == "first"
    assert results[0].matched_rules == ["a.rule", "b.rule"]


def test_classification_is_deterministic(sample_symbols: dict[str, Symbol], ruleset) -> None:
    symbol = sample_symbols["RiskComputeService"]
    assert classify(symbol, ruleset) == classify(symbol, ruleset)


# --------------------------------------------------------------------------------------
# Тесты не документируются
# --------------------------------------------------------------------------------------


def test_type_named_tests_is_excluded(ruleset) -> None:
    """`*Tests` встречается на порядок чаще `*Test`: в ABP 777 против 54."""
    assert is_excluded(_symbol(b"namespace N;\npublic class PricingServiceTests { }\n"), ruleset)
    assert is_excluded(_symbol(b"namespace N;\npublic class PricingServiceTest { }\n"), ruleset)


def test_word_containing_test_is_not_excluded(ruleset) -> None:
    """Правило по окончанию имени, а не по вхождению: `Contest` — не тест."""
    assert not is_excluded(_symbol(b"namespace N;\npublic class Contest { }\n"), ruleset)
    assert not is_excluded(_symbol(b"namespace N;\npublic class TestingHelper { }\n"), ruleset)


def test_type_in_test_directory_is_excluded(ruleset) -> None:
    """Одного правила по имени мало.

    В тестовых проектах полно вспомогательных типов, названных как продуктовые
    (`AuditTestController`, `AuthorizationTestPermissionDefinitionProvider`),
    и обычные правила их классифицируют. На ABP таких было 104 узла из 828.
    """
    symbol = _symbol(
        b"namespace N;\npublic class AuditTestController { }\n",
        path="framework/test/Volo.Abp.Tests/AuditTestController.cs",
    )
    assert is_excluded(symbol, ruleset)


def test_product_directory_with_test_in_the_name_is_kept(ruleset) -> None:
    """`**/*Tests/**` не должен цеплять `Contest/` или `Testing/`."""
    for path in ("src/App/Contest/Winner.cs", "src/Testing/Helper.cs"):
        symbol = _symbol(b"namespace N;\npublic class WinnerService { }\n", path=path)
        assert not is_excluded(symbol, ruleset), path


def test_test_project_produces_no_nodes(wild_solution: Path) -> None:
    """Сквозная проверка: тестовый проект фикстуры не даёт ни одного документа."""
    from tests.conftest import build_tree

    _, nodes = build_tree(wild_solution)
    assert {node.module for node in nodes} == {"Wild.Api"}
