"""`symbols`: причина, страница, правило-победитель, `--path` (S07).

На вопросы «почему этот символ так решён» и «что решено о символах этого
каталога» отвечает одна команда, и отвечает структурой: агент настройки
читает JSON, а не текст, и по нему решает, какое правило править.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docpipe.classify import Classification, _path_glob, classify, load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.emit import run
from docpipe.explain import Selection, build_symbols_report, select, selection_json
from docpipe.model import DocNode, Symbol
from docpipe.stats import PAGE_COVERED, absorbed_page_refs, absorbed_pages, decide
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")
SERVICES = "src/Sample.Pricing.Api/Services"


def _select(root: Path, **kwargs: Any) -> Selection:
    result = run(root)
    enrolled = {module.project_file for module in result.manifest.modules if module.enrolled}
    return select(
        result.index, result.manifest.nodes, load_ruleset(RULES, "dotnet"), enrolled, **kwargs
    )


def _names(selection: Selection) -> set[str]:
    return {row.symbol.name for row in selection.rows}


def _symbols(root: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(
        app, ["symbols", "--root", str(root), "--no-cache", "--format", "json", *args]
    )
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    return payload


def _by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in payload["symbols"]}


# --------------------------------------------------------------------------------------
# `--path`
# --------------------------------------------------------------------------------------


def test_path_selects_only_the_directory(sample_solution: Path) -> None:
    """Критерий приёмки: сервис и его интерфейс, частичный класс — одной строкой."""
    payload = _symbols(sample_solution, "--path", SERVICES, "--state", "any")

    assert [row["name"] for row in payload["symbols"]] == ["IPricingService", "PricingService"]
    assert payload["total"] == 2
    assert _by_name(payload)["PricingService"]["sources"] == [
        f"{SERVICES}/PricingService.Calculations.cs",
        f"{SERVICES}/PricingService.cs",
    ]
    assert "путь ~ " + SERVICES in payload["filters"]


def test_path_prefix_is_a_directory_not_a_string(sample_solution: Path) -> None:
    """`Serv` — не каталог: строковый префикс дал бы `src/App` → `src/AppTests/…`."""
    assert _select(sample_solution, state="any", path="src/Sample.Pricing.Api/Serv").total == 0


def test_trailing_slash_names_the_same_directory(sample_solution: Path) -> None:
    """`Services/` — обычная запись каталога, и молча не совпасть ни с чем она не должна."""
    plain = _select(sample_solution, state="any", path=SERVICES)
    slashed = _select(sample_solution, state="any", path=SERVICES + "/")

    assert _names(slashed) == _names(plain) == {"IPricingService", "PricingService"}


def test_path_to_one_part_of_a_partial_class(sample_solution: Path) -> None:
    """Хотя бы один источник — как у `path_glob`: вторая часть типа в выборку тоже приводит."""
    selection = _select(
        sample_solution, state="any", path=f"{SERVICES}/PricingService.Calculations.cs"
    )
    assert _names(selection) == {"PricingService"}


@pytest.mark.parametrize(
    "glob",
    [
        "**/Grid/*.cs",
        "src/*Service.cs",  # `*` проходит через `/` — наследуется у `path_glob` намеренно
        "**/Services/I*.cs",
        "src/Sample.Common/**",
        "**/*.Calculations.cs",
    ],
)
def test_glob_agrees_with_the_path_glob_predicate(sample_solution: Path, glob: str) -> None:
    """Один ответ на «попадает ли файл под глоб» во всём инструменте.

    `--path` с глобом обязан отбирать ровно то, что отобрало бы правило
    `path_glob` с тем же значением: иначе агент проверит шаблон выборкой,
    перенесёт его в правило, и правило поймает другое.
    """
    result = run(sample_solution)
    expected = {symbol.name for symbol in result.index.values() if _path_glob(symbol, [glob])}

    selection = _select(sample_solution, state="any", path=glob)

    assert expected
    assert _names(selection) == expected


def test_star_crosses_directories(sample_solution: Path) -> None:
    """Ловушка из плана, записанная числом: `src/*Service.cs` ловит файлы на любой глубине."""
    selection = _select(sample_solution, state="any", path="src/*Service.cs")
    assert _names(selection) == {"IPricingService", "PricingService", "RiskComputeService"}


def test_path_combines_with_the_other_filters(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="documented", path=SERVICES)
    assert _names(selection) == {"PricingService"}


def test_path_works_on_the_web_step(web_workspace: Path) -> None:
    """Пути символов фронта — те же репо-относительные пути, и фильтр тот же."""
    payload = _symbols(
        web_workspace,
        "--lang",
        "ts",
        "--rules",
        str(RULES),
        "--state",
        "any",
        "--path",
        "src/app/routes/models",
    )

    assert payload["total"]
    assert all(
        any(source.startswith("src/app/routes/models/") for source in row["sources"])
        for row in payload["symbols"]
    )


# --------------------------------------------------------------------------------------
# Причина, правило-победитель, страница
# --------------------------------------------------------------------------------------


def test_excluded_symbol_carries_its_reason(sample_solution: Path) -> None:
    payload = _symbols(sample_solution, "--state", "not_documented")
    row = _by_name(payload)["PriceDto"]

    assert row["exclusion"]["id"] == "data.contracts"
    assert row["exclusion"]["reason"].strip()
    # У отсеянного вида нет — значит, нет и победителя классификации.
    assert row["winner_rule"] is None
    assert row["page"] is None


def test_classified_symbol_names_the_winner(sample_solution: Path) -> None:
    """Совпали два правила, вид дало одно — и это видно без знания приоритетов."""
    payload = _symbols(sample_solution, "--state", "documented")
    rows = _by_name(payload)

    assert rows["RiskComputeService"]["winner_rule"] == "ignite.service"
    assert rows["RiskComputeService"]["rules"] == ["ignite.service", "service"]
    assert rows["PricingService"]["winner_rule"] == "service"
    assert all(row["exclusion"] is None for row in payload["symbols"])
    assert all(row["winner_rule"] for row in payload["symbols"])


def test_undecided_symbol_has_no_decision_fields(sample_solution: Path) -> None:
    row = _by_name(_symbols(sample_solution))["Program"]

    assert (row["exclusion"], row["winner_rule"], row["page"]) == (None, None, None)


def test_page_covered_symbol_names_its_page_node(web_workspace: Path) -> None:
    """`page.id` — узел страницы в манифесте фронта: по заголовку его не найти."""
    ruleset = load_ruleset(RULES, "web")
    scan = run_web(web_workspace, DocpipeConfig(), ruleset)
    absorbed_by = {
        node.symbol.fqn: node.absorbed_by
        for node in scan.manifest.nodes
        if node.symbol and node.absorbed_by
    }
    pages = {node.id: node for node in scan.manifest.nodes if node.kind == "page"}

    payload = _symbols(
        web_workspace, "--lang", "ts", "--rules", str(RULES), "--state", "page_covered"
    )

    assert payload["total"] == 5
    for row in payload["symbols"]:
        assert row["page"]["id"] == absorbed_by[row["fqn"]]
        assert row["page"]["title"] == pages[row["page"]["id"]].title
        assert row["winner_rule"]
    assert {row["page"]["title"] for row in payload["symbols"]} == {
        "DetailComponent",
        "ListComponent",
        "QuizComponent",
    }


def test_page_is_set_only_for_page_covered(web_workspace: Path) -> None:
    payload = _symbols(web_workspace, "--lang", "ts", "--rules", str(RULES), "--state", "any")

    for row in payload["symbols"]:
        assert (row["page"] is not None) == (row["state"] == PAGE_COVERED), row["fqn"]


# --------------------------------------------------------------------------------------
# Форма отчёта
# --------------------------------------------------------------------------------------


def test_report_keeps_the_old_keys_and_adds_the_new(sample_solution: Path) -> None:
    """Скрипты, читавшие JSON до S07, обязаны продолжить работать: поля добавлены, не заменены."""
    payload = _symbols(sample_solution, "--state", "any")

    assert payload["schema_version"] == "1.0"
    assert set(payload) == {"schema_version", "total", "shown", "filters", "symbols"}
    assert set(payload["symbols"][0]) == {
        "fqn",
        "name",
        "type_kind",
        "namespace",
        "module",
        "modifiers",
        "base_types_raw",
        "base_type_closure",
        "attributes",
        "public_members",
        "sources",
        "state",
        "kind",
        "rules",
        "exclusion",
        "page",
        "winner_rule",
    }


def test_json_output_ends_with_one_newline(sample_solution: Path) -> None:
    """Общее правило 3: `stable_json_dumps` уже кончается переводом строки."""
    result = runner.invoke(
        app, ["symbols", "--root", str(sample_solution), "--no-cache", "--format", "json"]
    )

    assert result.output.endswith("}\n")
    assert not result.output.endswith("\n\n")


def test_function_and_command_give_the_same_json(sample_solution: Path) -> None:
    """CLI тонкий: печатает то, что вернула функция, и ничего не добавляет."""
    selection = _select(sample_solution, state="any", path=SERVICES)
    payload = _symbols(sample_solution, "--state", "any", "--path", SERVICES)

    assert json.loads(selection_json(selection)) == payload
    assert build_symbols_report(selection).model_dump(mode="json") == payload


def test_text_names_the_winner_apart_from_the_rest(sample_solution: Path) -> None:
    result = runner.invoke(
        app,
        ["symbols", "--root", str(sample_solution), "--no-cache", "--state", "documented"],
    )

    assert result.exit_code == 0, result.output
    assert "ignite_service по ignite.service (совпали также: service)" in result.output
    assert "service по service\n" in result.output


# --------------------------------------------------------------------------------------
# Победитель в классификации и пара страницы
# --------------------------------------------------------------------------------------


def test_classification_records_the_winner(sample_solution: Path) -> None:
    ruleset = load_ruleset(RULES, "dotnet")
    symbols = {symbol.name: symbol for symbol in run(sample_solution).index.values()}

    result = classify(symbols["RiskComputeService"], ruleset)

    assert result is not None
    assert result.winner == "ignite.service"
    assert decide(symbols["RiskComputeService"], ruleset).winner_rule == "ignite.service"
    # Отсеянный не классифицируется вовсе, и победителя у его решения нет.
    assert decide(symbols["PriceDto"], ruleset).winner_rule is None


def test_winner_does_not_take_part_in_equality() -> None:
    """Победитель выводится из совпавших и приоритетов; сравнение от него не зависит."""
    left = Classification(kind="k", template="t", matched_rules=["a", "b"], winner="a")
    right = Classification(kind="k", template="t", matched_rules=["a", "b"])

    assert left == right


def _node(node_id: str, title: str, **extra: Any) -> DocNode:
    return DocNode(
        id=node_id,
        kind=extra.pop("kind", "page"),
        template="page",
        title=title,
        doc_path=f"docs/{title}.md",
        module="src",
        domain="src",
        signature_hash="sha256:0",
        **extra,
    )


def _absorbed(fqn: str, page_id: str) -> DocNode:
    return _node(
        f"type:src#{fqn}",
        fqn,
        kind="api-service",
        absorbed_by=page_id,
        symbol=Symbol(fqn=fqn, name=fqn, namespace="src", module="src", type_kind="class"),
    )


def test_absorbed_page_refs_give_id_and_title() -> None:
    nodes = [_node("type:src#page", "DetailComponent"), _absorbed("src.A", "type:src#page")]

    assert absorbed_page_refs(nodes) == {"src.A": ("type:src#page", "DetailComponent")}
    # Старая форма осталась и называет ту же страницу.
    assert absorbed_pages(nodes) == {"src.A": "DetailComponent"}


def test_missing_page_node_is_named_by_its_id() -> None:
    """Пустой заголовок читался бы как «страницы нет»; подписываем `id`."""
    nodes = [_absorbed("src.A", "type:src#gone")]

    assert absorbed_page_refs(nodes) == {"src.A": ("type:src#gone", "type:src#gone")}
    assert absorbed_pages(nodes) == {"src.A": "type:src#gone"}
