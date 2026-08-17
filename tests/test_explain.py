"""Выборка символов по состоянию решения — команда `docpipe symbols` (T24)."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.emit import run
from docpipe.explain import Selection, format_selection, select, selection_json
from docpipe.stats import UNDECIDED, collect_stats

runner = CliRunner()
RULES = Path("rules/rules.yaml")


def _select(root: Path, **kwargs: Any) -> Selection:
    result = run(root)
    enrolled = {module.project_file for module in result.manifest.modules if module.enrolled}
    return select(
        result.index, result.manifest.nodes, load_ruleset(RULES, "dotnet"), enrolled, **kwargs
    )


# --------------------------------------------------------------------------------------
# Согласованность с отчётом
# --------------------------------------------------------------------------------------


def test_selection_agrees_with_the_report(sample_solution: Path) -> None:
    """Главный инвариант: выборка и отчёт считают одно и то же.

    Расхождение означало бы, что `--stats` говорит «шесть без решения», а
    `symbols` показывает пять, и доверять нельзя ни одному из двух. Поэтому
    решение по символу считается одной функцией `decide`, а не дважды.
    """
    result = run(sample_solution)
    enrolled = {module.project_file for module in result.manifest.modules if module.enrolled}
    stats = collect_stats(
        result.index, result.manifest.nodes, load_ruleset(RULES, "dotnet"), enrolled
    )

    for state, expected in (
        ("undecided", stats.counts.get("undecided", 0)),
        ("not_documented", stats.counts.get("not_documented", 0)),
        ("interface_covered", stats.counts.get("interface_covered", 0)),
    ):
        selection = select(
            result.index,
            result.manifest.nodes,
            load_ruleset(RULES, "dotnet"),
            enrolled,
            state=state,
        )
        assert selection.total == expected, state


def test_documented_state_covers_every_node(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="documented")
    assert selection.total == 6


def test_any_state_covers_the_whole_index(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="any")
    assert selection.total == 10


# --------------------------------------------------------------------------------------
# Фильтры
# --------------------------------------------------------------------------------------


def test_module_filter_by_substring(sample_solution: Path) -> None:
    """Обычный случай отладки: имя проекта, без шаблона вокруг него."""
    selection = _select(sample_solution, state="any", module="Sample.Common")
    modules = {row.symbol.module for row in selection.rows}
    assert modules == {"src/Sample.Common/Sample.Common.csproj"}


def test_module_filter_by_glob(sample_solution: Path) -> None:
    """Со звёздочкой значение можно скопировать из `enrolled` и получить ту же выборку."""
    selection = _select(sample_solution, state="any", module="src/Sample.Common/**")
    modules = {row.symbol.module for row in selection.rows}
    assert modules == {"src/Sample.Common/Sample.Common.csproj"}


def test_namespace_filter_is_a_prefix(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="any", namespace="Sample.Pricing.Api.Services")
    assert selection.total
    assert all(
        row.symbol.namespace.startswith("Sample.Pricing.Api.Services") for row in selection.rows
    )


def test_rule_filter_shows_what_a_rule_caught(sample_solution: Path) -> None:
    """Ради этого фильтра он и нужен: проверить только что написанное правило.

    `RiskComputeService` попадает в выборку по `service`, хотя победило
    `ignite.service`: правило совпало, и это видно — иначе аудит `matched_rules`
    приходилось бы делать через jq по манифесту.
    """
    selection = _select(sample_solution, state="documented", rule="service")
    names = {row.symbol.name for row in selection.rows}
    assert names == {"PricingService", "RiskComputeService"}


def test_rule_filter_works_for_exclusions_too(sample_solution: Path) -> None:
    """Из какой секции правило, помнить не нужно: и отсев, и классификация."""
    selection = _select(sample_solution, state="not_documented", rule="data.contracts")
    names = {row.symbol.name for row in selection.rows}
    assert names == {"PriceDto"}


def test_kind_filter(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="documented", kind="controller")
    assert selection.total == 2


def test_limit_caps_rows_but_not_total(sample_solution: Path) -> None:
    """`total` обязан остаться полным: иначе не видно, что список обрезан."""
    selection = _select(sample_solution, state="any", limit=3)
    assert selection.total == 10
    assert len(selection.rows) == 3


def test_rows_are_sorted_by_fqn(sample_solution: Path) -> None:
    """Порядок обхода индекса источником порядка быть не может."""
    selection = _select(sample_solution, state="any")
    fqns = [row.symbol.fqn for row in selection.rows]
    assert fqns == sorted(fqns)


# --------------------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------------------


def test_output_shows_what_predicates_match_on(sample_solution: Path) -> None:
    """В выводе обязано быть то, по чему пишут правило, и путь до файла."""
    selection = _select(sample_solution, state="documented", kind="ignite_service")
    text = format_selection(selection)

    assert "Sample.Pricing.Api.Grid.RiskComputeService" in text
    assert "IService" in text  # замыкание наследования
    assert "Execute" in text  # публичные члены: имена методов grid-сервиса — контракт
    assert "src/Sample.Pricing.Api/Grid/RiskComputeService.cs:6" in text
    assert "ignite.service" in text  # чем решено


def test_partial_type_reports_all_its_files(sample_solution: Path) -> None:
    """`path_glob` истинен, если совпал хотя бы один источник, — умолчать нельзя."""
    selection = _select(sample_solution, state="documented", kind="service")
    assert "(+1" in format_selection(selection)


def test_empty_selection_says_so(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="undecided", module="нет-такого")
    text = format_selection(selection)

    assert "Ничего не найдено" in text
    assert "модуль ~ нет-такого" in text  # фильтры видны: вывод объясняет сам себя


def test_header_names_the_filters(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="undecided", namespace="Sample")
    assert (
        format_selection(selection)
        .splitlines()[0]
        .startswith("1 символ  ·  решение не принято  ·  namespace ~ Sample")
    )


def test_json_output_is_machine_readable(sample_solution: Path) -> None:
    selection = _select(sample_solution, state="not_documented")
    payload = json.loads(selection_json(selection))

    assert payload["total"] == 1
    assert payload["symbols"][0]["name"] == "PriceDto"
    assert payload["symbols"][0]["rules"] == ["data.contracts"]
    assert payload["symbols"][0]["state"] == "not_documented"


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_command_defaults_to_undecided(sample_solution: Path) -> None:
    result = runner.invoke(app, ["symbols", "--root", str(sample_solution), "--no-cache"])

    assert result.exit_code == 0
    assert "решение не принято" in result.output
    assert "Sample.Pricing.Api.Program" in result.output


def test_command_writes_nothing(sample_solution: Path, tmp_path: Path) -> None:
    """Инструмент отладки, его зовут в цикле — писать он не должен ничего."""
    before = sorted(p.name for p in sample_solution.rglob("*"))
    runner.invoke(app, ["symbols", "--root", str(sample_solution), "--no-cache"])
    assert sorted(p.name for p in sample_solution.rglob("*")) == before


def test_command_rejects_unknown_state(sample_solution: Path) -> None:
    """Опечатка в состоянии иначе дала бы пустую выборку, похожую на «всё решено»."""
    result = runner.invoke(
        app, ["symbols", "--root", str(sample_solution), "--state", "undecied", "--no-cache"]
    )

    assert result.exit_code != 0
    assert "неизвестное состояние" in result.output


def test_command_json_format(sample_solution: Path) -> None:
    result = runner.invoke(
        app,
        ["symbols", "--root", str(sample_solution), "--format", "json", "--no-cache"],
    )
    payload = json.loads(result.output)

    assert payload["total"] == 1
    assert payload["symbols"][0]["state"] == UNDECIDED


def test_symbols_of_the_web_step_show_their_page(web_workspace: Path) -> None:
    """`--lang ts` читает секцию `web` и индекс фронта.

    Без него состояние `page_covered` не увидеть вовсе: поглощение бывает
    только на фронте, а `symbols` до сих пор умел один язык.
    """
    result = runner.invoke(
        app,
        [
            "symbols",
            "--root",
            str(web_workspace),
            "--lang",
            "ts",
            "--state",
            "page_covered",
            "--rules",
            "rules/rules.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "5 символов" in result.output
    assert "документируется внутри страницы: DetailComponent" in result.output


def test_unknown_lang_is_refused(web_workspace: Path) -> None:
    result = runner.invoke(app, ["symbols", "--root", str(web_workspace), "--lang", "kotlin"])

    assert result.exit_code == 2
