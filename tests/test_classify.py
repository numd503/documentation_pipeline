"""Проверка движка правил классификации (T14)."""

from pathlib import Path

import pytest
import yaml

from docpipe.classify import (
    Classification,
    base_type_candidates,
    classify,
    condition_values,
    exclusion_of,
    is_excluded,
    load_ruleset,
)
from docpipe.dotnet.parser import parse_file, parse_source
from docpipe.dotnet.resolve import build_symbol_index, compute_closures
from docpipe.model import Symbol
from tests.conftest import by_fqn, index_of, sectioned

RULES = Path("rules/rules.yaml")


@pytest.fixture
def ruleset():  # type: ignore[no-untyped-def]
    return load_ruleset(RULES, "dotnet")


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
    """Не исключён, но и ни под одно правило не подходит — решение не принято."""
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
    ruleset = load_ruleset(RULES, "dotnet")

    assert "EndpointBaseAsync" in base_type_candidates(symbol.base_types_raw[0])
    assert classify(symbol, ruleset) is None  # в наборе по умолчанию правила REPR нет


# --------------------------------------------------------------------------------------
# Предикаты
# --------------------------------------------------------------------------------------


def _ruleset_with(when: dict, tmp_path: Path, exclude: dict | None = None):  # type: ignore[no-untyped-def]
    path = tmp_path / "r.yaml"
    path.write_text(
        sectioned(
            {
                "version": "1",
                "ruleset_version": "test",
                "exclude": exclude or {},
                "rules": [{"id": "r", "kind": "k", "template": "t", "priority": 1, "when": when}],
            },
        ),
        encoding="utf-8",
    )
    return load_ruleset(path, "dotnet")


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
# Решения «не документируем»: причина, атрибуция, совместимость форм
# --------------------------------------------------------------------------------------


def _exclude_rule(**overrides: object) -> dict[str, object]:
    return {"id": "r", "reason": "потому что", "when": {"type_kind": ["class"]}} | overrides


def test_exclusion_names_the_decision_and_its_reason(tmp_path: Path) -> None:
    """Отсев обязан быть объясним: без причины это снова безымянное число."""
    symbol = _symbol(b"namespace N;\npublic class C { }\n")
    ruleset = _ruleset_with(
        {"type_kind": ["interface"]}, tmp_path, exclude={"rules": [_exclude_rule(id="generated")]}
    )
    decision = exclusion_of(symbol, ruleset)

    assert decision is not None
    assert (decision.id, decision.reason) == ("generated", "потому что")


def test_attribute_can_exclude_what_no_path_pattern_catches(tmp_path: Path) -> None:
    """Главная причина, по которой краткой формы было мало.

    Генерат в АС CF опознаётся атрибутом `[GenerateCode]`, а не каталогом:
    1478 типов, которые в краткой форме нельзя было ни классифицировать,
    ни отсеять, и потому они оставались «неклассифицированными» навсегда.
    """
    symbol = _symbol(b"namespace N;\n[GenerateCode]\npublic class Srv2020Ver005 { }\n")
    ruleset = _ruleset_with(
        {"type_kind": ["class"]},
        tmp_path,
        exclude={"rules": [_exclude_rule(id="generated", when={"attribute": ["GenerateCode"]})]},
    )

    assert is_excluded(symbol, ruleset)


def test_short_form_is_equivalent_to_rules(
    sample_symbols: dict[str, Symbol], tmp_path: Path
) -> None:
    """Набор, написанный до появления причин, обязан работать без правки.

    Иначе правка требует одновременной миграции развёрнутых конфигураций —
    в том числе той, что уже лежит на боевом репозитории АС CF.
    """
    short_dir, spelled_dir = tmp_path / "short", tmp_path / "spelled"
    short_dir.mkdir()
    spelled_dir.mkdir()

    short = _ruleset_with(
        {"type_kind": ["class"]},
        short_dir,
        exclude={
            "path_glob": ["**/obj/**"],
            "name_regex": ["^.*Dto$"],
            "type_kind_deny": ["enum"],
            "require_public": True,
        },
    )
    spelled = _ruleset_with(
        {"type_kind": ["class"]},
        spelled_dir,
        exclude={
            "require_public": True,
            "rules": [
                _exclude_rule(id="generated", when={"path_glob": ["**/obj/**"]}),
                _exclude_rule(id="contracts", when={"name_regex": ["^.*Dto$"]}),
                _exclude_rule(id="enums", when={"type_kind": ["enum"]}),
            ],
        },
    )

    assert {name for name, s in sample_symbols.items() if is_excluded(s, short)} == {
        name for name, s in sample_symbols.items() if is_excluded(s, spelled)
    }


_REFERENCE_SET_BEFORE_MIGRATION = """
ruleset_version: before
exclude:
  path_glob:
    - "**/obj/**"
    - "**/bin/**"
    - "**/*.g.cs"
    - "**/*.Designer.cs"
    - "**/*.generated.cs"
    - "**/Migrations/**"
    - "**/test/**"
    - "**/tests/**"
    - "**/*Test/**"
    - "**/*Tests/**"
  name_regex:
    - "^.*Dto$"
    - "^.*Request$"
    - "^.*Response$"
    - "^.*Options$"
    - "^.*Settings$"
    - "^.*Tests?$"
  type_kind_deny: ["enum"]
  require_public: true
rules: []
"""


def test_reference_set_migration_changed_nothing(
    sample_solution: Path, wild_solution: Path, tmp_path: Path
) -> None:
    """Перевод эталонного набора в форму с причинами не изменил состав отсева.

    Здесь проверяется сама миграция, а не механизм: слева — секция `exclude`
    ровно в том виде, в каком она была до правки, справа — четыре решения
    с причинами. Отличаться они обязаны только тем, что теперь видно, почему
    символ отсеян; какие символы отсеяны — тем же.
    """
    before_path = tmp_path / "before.yaml"
    before_path.write_text(sectioned(_REFERENCE_SET_BEFORE_MIGRATION), encoding="utf-8")
    before, after = load_ruleset(before_path, "dotnet"), load_ruleset(RULES, "dotnet")

    for root in (sample_solution, wild_solution):
        symbols = compute_closures(index_of(root)).values()
        assert {s.fqn for s in symbols if is_excluded(s, before)} == {
            s.fqn for s in symbols if is_excluded(s, after)
        }, root


def test_short_form_reports_which_of_its_four_causes_fired(tmp_path: Path) -> None:
    """Разбивка по причинам появляется и у краткой формы, с общими формулировками.

    До этого она добывалась ручными замерами: в README есть цифры «на ABP
    name_regex — 508 из 996», которых инструмент не показывал.
    """
    ruleset = _ruleset_with(
        {"type_kind": ["class"]},
        tmp_path,
        exclude={"name_regex": ["^.*Dto$"], "require_public": True},
    )

    public_dto = exclusion_of(_symbol(b"namespace N;\npublic class PriceDto { }\n"), ruleset)
    internal = exclusion_of(_symbol(b"namespace N;\ninternal class Helper { }\n"), ruleset)

    assert public_dto is not None and public_dto.id == "exclude.name_regex"
    assert internal is not None and internal.id == "exclude.require_public"
    assert internal.reason  # причина есть даже у переключателя


def test_written_rule_takes_attribution_from_the_short_form(tmp_path: Path) -> None:
    """При совпадении обоих в отчёт идёт причина человека: она информативнее."""
    symbol = _symbol(b"namespace N;\npublic class PriceDto { }\n")
    ruleset = _ruleset_with(
        {"type_kind": ["class"]},
        tmp_path,
        exclude={
            "name_regex": ["^.*Dto$"],
            "rules": [_exclude_rule(id="contracts", when={"name_regex": ["^Price.*$"]})],
        },
    )
    decision = exclusion_of(symbol, ruleset)

    assert decision is not None and decision.id == "contracts"


def test_attribution_does_not_depend_on_order_in_the_file(tmp_path: Path) -> None:
    """Иначе перестановка двух строк в YAML меняла бы цифры в отчёте и в CI."""
    symbol = _symbol(b"namespace N;\npublic class C { }\n")
    first = _exclude_rule(id="aaa", when={"type_kind": ["class"]})
    second = _exclude_rule(id="zzz", when={"name_regex": ["^C$"]})

    for order in ([first, second], [second, first]):
        ruleset = _ruleset_with({"type_kind": ["interface"]}, tmp_path, exclude={"rules": order})
        decision = exclusion_of(symbol, ruleset)
        assert decision is not None and decision.id == "aaa"


def test_exclude_rule_without_reason_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="без полей"):
        _ruleset_with(
            {"type_kind": ["class"]}, tmp_path, exclude={"rules": [{"id": "r", "when": {}}]}
        )


def test_empty_exclude_condition_fails_at_load(tmp_path: Path) -> None:
    """Правило-заглушка обнулила бы нерешённое, ничего не решив."""
    with pytest.raises(ValueError, match="одним ключом"):
        _ruleset_with(
            {"type_kind": ["class"]}, tmp_path, exclude={"rules": [_exclude_rule(when={})]}
        )


def test_reserved_id_prefix_fails_at_load(tmp_path: Path) -> None:
    """Иначе в таблице причин появились бы две строки с одним id."""
    with pytest.raises(ValueError, match="зарезервирован"):
        _ruleset_with(
            {"type_kind": ["class"]},
            tmp_path,
            exclude={"rules": [_exclude_rule(id="exclude.name_regex")]},
        )


def test_unknown_exclude_field_fails_at_load(tmp_path: Path) -> None:
    """Опечатка в имени поля иначе означала бы молча не работающий отсев."""
    with pytest.raises(ValueError, match="неизвестные поля"):
        _ruleset_with({"type_kind": ["class"]}, tmp_path, exclude={"name_regexp": ["^X$"]})


def test_bad_regex_in_exclude_fails_at_load(tmp_path: Path) -> None:
    """Битая регулярка обнаруживалась бы иначе посреди прогона, и не всегда."""
    with pytest.raises(ValueError, match="неверное регулярное выражение"):
        _ruleset_with({"type_kind": ["class"]}, tmp_path, exclude={"name_regex": ["["]})


def test_condition_values_walks_combinators() -> None:
    """Вопрос «отсекается ли такой путь» не должен зависеть от формы записи."""
    flat = {"path_glob": ["a/**"]}
    nested = {"any": [{"path_glob": ["a/**"]}, {"all": [{"path_glob": ["b/**"]}]}]}

    assert condition_values(flat, "path_glob") == ["a/**"]
    assert condition_values(nested, "path_glob") == ["a/**", "b/**"]
    assert condition_values(nested, "name_regex") == []


# --------------------------------------------------------------------------------------
# Загрузка и проверка набора
# --------------------------------------------------------------------------------------


def test_default_ruleset_loads() -> None:
    ruleset = load_ruleset(RULES, "dotnet")
    assert ruleset.ruleset_version == "2026-07-30.1"
    assert {rule.id for rule in ruleset.exclude.rules} == {
        "generated.code",
        "tests",
        "data.contracts",
        "enums",
    }
    assert all(rule.reason for rule in ruleset.exclude.rules)
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

    Понять это можно было бы только по счётчику «решение не принято» — то есть никак.
    """
    path = tmp_path / "r.yaml"
    path.write_text(
        sectioned(
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
        load_ruleset(path, "dotnet")


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
        sectioned({"ruleset_version": "t", "rules": [rule, dict(rule)]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="повтор id"):
        load_ruleset(path, "dotnet")


def test_bad_regex_fails_at_load(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(
        sectioned(
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
        load_ruleset(path, "dotnet")


def test_missing_rule_field_fails_at_load(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(
        sectioned({"ruleset_version": "t", "rules": [{"id": "r", "kind": "k"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="без полей"):
        load_ruleset(path, "dotnet")


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
        path.write_text(sectioned({"ruleset_version": "t", "rules": order}), encoding="utf-8")
        results.append(classify(symbol, load_ruleset(path, "dotnet")))

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


# --------------------------------------------------------------------------------------
# `unless`: вырез внутри решения об отсеве
# --------------------------------------------------------------------------------------
#
# Самый частый случай настройки: в каталоге лежит один вид документируемых типов
# и много вспомогательных. Описать вспомогательные положительно удаётся не всегда,
# а общий `not` в движке отсутствует намеренно — он превращает набор в систему
# уравнений, которую нельзя прочитать построчно. `unless` читается построчно:
# «исключаем каталог, кроме наследников такого-то».

GRID = b"""
namespace App.Grid;
public abstract class GridService { }
public class CalcService : GridService { }
public class CalcOptions { }
"""


def _grid_symbols() -> dict[str, Symbol]:
    result = parse_source(GRID, "src/Grid/Services.cs")
    index = compute_closures(
        build_symbol_index([result], {"src/Grid/Services.cs": "src/Grid/Grid.csproj"})
    )
    return {symbol.name: symbol for symbol in index.values()}


def _with_unless(tmp_path: Path, unless: dict | None):  # type: ignore[no-untyped-def]
    rule: dict = {
        "id": "grid.support",
        "reason": "Вспомогательные типы каталога сервисов",
        "priority": 50,
        "when": {"path_glob": ["src/Grid/**"]},
    }
    if unless is not None:
        rule["unless"] = unless
    path = tmp_path / "r.yaml"
    path.write_text(
        sectioned(
            {
                "version": "1",
                "ruleset_version": "test",
                "exclude": {"rules": [rule]},
                "rules": [
                    {
                        "id": "grid.services",
                        "kind": "ignite_service",
                        "template": "ignite-service",
                        "priority": 50,
                        "when": {"inherits": ["GridService"]},
                    }
                ],
            },
        ),
        encoding="utf-8",
    )
    return load_ruleset(path, "dotnet")


def test_unless_carves_the_wanted_types_out_of_the_exclusion(tmp_path: Path) -> None:
    symbols = _grid_symbols()
    ruleset = _with_unless(tmp_path, {"inherits": ["GridService"]})

    assert not is_excluded(symbols["CalcService"], ruleset)
    assert classify(symbols["CalcService"], ruleset) is not None
    assert is_excluded(symbols["CalcOptions"], ruleset)
    assert is_excluded(symbols["GridService"], ruleset)


def test_without_unless_the_whole_directory_is_gone(tmp_path: Path) -> None:
    """Приоритет правила классификации этого не меняет и не может:
    отсев — стадия ДО правил, а не участник одного с ними соревнования."""
    symbols = _grid_symbols()
    ruleset = _with_unless(tmp_path, None)

    assert is_excluded(symbols["CalcService"], ruleset)
    assert classify(symbols["CalcService"], ruleset) is None


def test_unless_keeps_the_reason_of_the_rule_it_belongs_to(tmp_path: Path) -> None:
    """Вырез не заводит второго решения: отсеянные соседи по-прежнему
    объясняются одной причиной, и отчёт `--stats` не дробится."""
    symbols = _grid_symbols()
    ruleset = _with_unless(tmp_path, {"inherits": ["GridService"]})
    decision = exclusion_of(symbols["CalcOptions"], ruleset)

    assert decision is not None
    assert decision.id == "grid.support"
    assert exclusion_of(symbols["CalcService"], ruleset) is None


def test_unless_is_checked_only_when_when_matched(tmp_path: Path) -> None:
    """Правило описывает группу, исключение — вырез внутри неё. Обратный
    порядок сделал бы `unless` вторым условием отсева, а не выключателем."""
    outside = _symbol(b"namespace N;\npublic class Other { }\n", path="src/Other/X.cs")
    ruleset = _with_unless(tmp_path, {"inherits": ["GridService"]})

    assert not is_excluded(outside, ruleset)


def test_empty_unless_is_rejected(tmp_path: Path) -> None:
    """Пустой `unless` — недописанное правило, а не «исключений нет»:
    молча оно вело бы себя как обычный отсев."""
    with pytest.raises(ValueError, match="пустой `unless`"):
        _with_unless(tmp_path, {})


def test_broken_predicate_in_unless_is_rejected_at_load(tmp_path: Path) -> None:
    """Та же диагностика, что у `when`: опечатка иначе означала бы вырез,
    который никогда не срабатывает, и типы молча исчезли бы из документации."""
    with pytest.raises(ValueError, match="unless"):
        _with_unless(tmp_path, {"inherit": ["GridService"]})


def test_unless_accepts_combinators(tmp_path: Path) -> None:
    symbols = _grid_symbols()
    ruleset = _with_unless(
        tmp_path, {"any": [{"inherits": ["GridService"]}, {"name_suffix": ["Options"]}]}
    )

    assert not is_excluded(symbols["CalcService"], ruleset)
    assert not is_excluded(symbols["CalcOptions"], ruleset)


# --------------------------------------------------------------------------------------
# Секционный формат файла правил
# --------------------------------------------------------------------------------------
#
# Один файл на проект, по секции на шаг. Секцию называет вызывающий: угадывать
# её загрузчик не имеет права, потому что цена ошибки — .NET-правила,
# применённые к TypeScript, то есть пустое дерево без единого сообщения.


def test_both_sections_live_in_one_file(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "dotnet": {"ruleset_version": "net.1", "rules": []},
                "web": {"ruleset_version": "web.1", "rules": []},
            }
        ),
        encoding="utf-8",
    )

    assert load_ruleset(path, "dotnet").ruleset_version == "net.1"
    assert load_ruleset(path, "web").ruleset_version == "web.1"
    # `version` — свойство формата файла, а не набора: два разных значения
    # в одном файле означали бы два разных разбора одного файла.
    assert load_ruleset(path, "web").version == "1"


def test_missing_section_is_refused_and_names_what_is_there(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump({"version": "1", "dotnet": {"ruleset_version": "net.1", "rules": []}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="нет секции `web:`.*dotnet"):
        load_ruleset(path, "web")


def test_flat_file_is_refused_with_the_migration_command(tmp_path: Path) -> None:
    """Старый плоский файл — отказ с командой переноса, а не молчаливый разбор.

    Прочитанный шагом `web`, он дал бы .NET-правила на TypeScript:
    `require_public: true` отсеял бы весь фронт, и дерево вышло бы пустым.
    """
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"ruleset_version": "old", "rules": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="старом плоском формате") as failure:
        load_ruleset(path, "dotnet")
    assert "migrate_rules.py" in str(failure.value)


def test_section_without_version_is_refused(tmp_path: Path) -> None:
    """`ruleset_version` уходит в манифест и в `business_hash`: умолчание здесь
    дало бы двум разным наборам одну версию в отчётах."""
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"web": {"rules": []}}), encoding="utf-8")

    with pytest.raises(ValueError, match="нет `ruleset_version`"):
        load_ruleset(path, "web")


def test_errors_name_the_section(tmp_path: Path) -> None:
    """Одного пути в сообщении мало: в файле две секции, и надо знать, в какой."""
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump({"web": {"ruleset_version": "t", "rules": [{"id": "r", "kind": "k"}]}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"rules\.yaml:web"):
        load_ruleset(path, "web")


def test_web_section_does_not_inherit_require_public_from_dotnet(tmp_path: Path) -> None:
    """G13 п. 3. `require_public` из `dotnet` не доезжает до `web`.

    У TypeScript модификатора `public` на уровне объявления нет вовсе:
    видимость задаётся словом `export`. Значение, унаследованное из соседней
    секции, отсеяло бы **весь** фронт — и без единого сообщения об ошибке.
    """
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "dotnet": {
                    "ruleset_version": "net.1",
                    "exclude": {"require_public": True},
                    "rules": [],
                },
                "web": {"ruleset_version": "web.1", "rules": []},
            }
        ),
        encoding="utf-8",
    )

    assert load_ruleset(path, "dotnet").exclude is not None
    web = load_ruleset(path, "web")
    assert web.exclude is None or not web.exclude.require_public
