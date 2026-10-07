"""Отчёт `--stats` структурой и срез «последнее слово» (S05 плана настройки).

Вопрос интервью «120 типов в `*.Migrations` — документировать?» строится
из числа, которое агент получает структурой, а не разбором русского текста.
Поэтому здесь проверяется не только форма JSON, но и то, что он говорит
то же, что и текст: те же числа, те же срезы, та же обрезка `--top`.
"""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from docpipe.classify import Ruleset, load_ruleset
from docpipe.cli import app
from docpipe.emit import run
from docpipe.hashing import camel_words, slugify
from docpipe.model import Attribute, Symbol
from docpipe.stats import (
    BREAKDOWN_KEYS,
    DECISION_KEYS,
    Stats,
    StatsReport,
    build_stats_report,
    collect_stats,
    format_breakdown,
    format_report,
    last_word,
)
from tests.conftest import sectioned

runner = CliRunner()

PRICING = "src/Sample.Pricing.Api"
STALE_PAGES = (
    'version: "1"\npages:\n  add:\n    - route: "/x"\n'
    '      component: "src/app/gone.Missing"\n      reason: "протухло"\n'
)


def _empty_ruleset(tmp_path: Path) -> Ruleset:
    """Набор, по которому про каждый символ решения нет: всё идёт в срезы."""
    rules = tmp_path / "rules.yaml"
    rules.write_text(sectioned("ruleset_version: t\nrules: []\n"), encoding="utf-8")
    return load_ruleset(rules, "dotnet")


def _symbol(name: str, **extra: object) -> Symbol:
    fields: dict[str, object] = {
        "fqn": f"App.{name}",
        "name": name,
        "type_kind": "class",
        "namespace": "App",
        "module": "src/App/App.csproj",
    }
    fields.update(extra)
    return Symbol.model_validate(fields)


def _stats_of(names: list[str], tmp_path: Path) -> Stats:
    symbols = [_symbol(name) for name in names]
    return collect_stats({symbol.fqn: symbol for symbol in symbols}, [], _empty_ruleset(tmp_path))


def _json(arguments: list[str]) -> dict[str, object]:
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    payload: dict[str, object] = json.loads(result.stdout)
    return payload


# --------------------------------------------------------------------------------------
# Блок решений: числа критериев приёмки
# --------------------------------------------------------------------------------------


def test_sample_solution_decisions(sample_solution: Path) -> None:
    """6/1/1/2, и сумма по всем шести ключам равна числу символов."""
    report = build_stats_report(run(sample_solution).stats, lang="cs")

    assert report.total == 10
    assert report.decisions == {
        "documented": 6,
        "not_documented": 1,
        "undecided": 1,
        "interface_covered": 2,
        "page_covered": 0,
        "not_enrolled": 0,
    }
    assert sum(report.decisions.values()) == report.total


def test_documented_is_the_sum_of_kinds(sample_solution: Path) -> None:
    """В `Stats.counts` виды и особые состояния лежат одним словарём; в отчёте
    они разведены, и ни один особый ключ не просачивается в таблицу видов."""
    report = build_stats_report(run(sample_solution).stats, lang="cs")

    assert report.kinds == [
        ("controller", 2),
        ("ignite_service", 1),
        ("provider", 1),
        ("service", 1),
        ("workflow", 1),
    ]
    assert report.decisions["documented"] == sum(count for _, count in report.kinds)
    assert not {kind for kind, _ in report.kinds} & set(DECISION_KEYS)


def test_skipped_rules_keep_reason_and_order(sample_solution: Path) -> None:
    report = build_stats_report(run(sample_solution).stats, lang="cs")

    assert [(rule.rule_id, rule.count) for rule in report.skipped] == [("data.contracts", 1)]
    assert report.skipped[0].reason.startswith("Контракт передачи данных")


def test_cli_scan_json_matches_the_criteria(sample_solution: Path) -> None:
    payload = _json(
        ["scan", "--root", str(sample_solution), "--stats", "--format", "json", "--no-cache"]
    )

    assert payload["schema_version"] == "1.0"
    assert payload["lang"] == "cs"
    assert payload["total"] == 10
    decisions = payload["decisions"]
    assert isinstance(decisions, dict)
    assert [decisions[key] for key in ("documented", "not_documented", "undecided")] == [6, 1, 1]
    assert decisions["interface_covered"] == 2
    assert payload["stale_overrides"] == []
    assert payload["scope"] == {"partial": False, "restored_from_cache": 0, "missing_from_cache": 0}


def test_cli_web_scan_json_matches_the_criteria(web_workspace: Path) -> None:
    payload = _json(
        ["web", "scan", "--root", str(web_workspace), "--stats", "--format", "json", "--no-cache"]
    )
    decisions = payload["decisions"]
    assert isinstance(decisions, dict)

    assert payload["lang"] == "ts"
    assert [decisions[key] for key in ("documented", "not_documented", "undecided")] == [25, 4, 0]
    assert decisions["page_covered"] == 5
    assert sum(decisions.values()) == payload["total"]
    # Скоупа у шага `web` нет: `None`, а не «не частичный».
    assert payload["scope"] is None


# --------------------------------------------------------------------------------------
# Протухшие правила, скоуп, ошибки разбора
# --------------------------------------------------------------------------------------


def test_stale_overrides_are_in_the_report(web_workspace: Path, tmp_path: Path) -> None:
    """Правило `pages.yaml`, не легшее ни на что, видно и в JSON, а не только в stderr."""
    pages = tmp_path / "pages.yaml"
    pages.write_text(STALE_PAGES, encoding="utf-8")
    result = runner.invoke(
        app,
        ["web", "scan", "--root", str(web_workspace), "--pages", str(pages)]
        + ["--stats", "--format", "json", "--no-cache"],
    )

    assert result.exit_code == 0, result.output
    # Предупреждение по-прежнему в stderr, и stdout от него остаётся чистым JSON.
    assert "не легло" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["stale_overrides"] == [
        {"kind": "add-missed", "key": "src/app/gone.Missing", "reason": "протухло"}
    ]


def test_stale_overrides_still_fail_when_asked(web_workspace: Path, tmp_path: Path) -> None:
    pages = tmp_path / "pages.yaml"
    pages.write_text(STALE_PAGES, encoding="utf-8")
    result = runner.invoke(
        app,
        ["web", "scan", "--root", str(web_workspace), "--pages", str(pages)]
        + ["--stats", "--format", "json", "--no-cache", "--fail-on-stale-overrides"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stale_overrides"]


def test_scoped_run_is_marked_partial(sample_solution: Path, tmp_path: Path) -> None:
    """Числа скоуп-прогона без пометки выглядят как числа всего репозитория."""
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root, ignore=shutil.ignore_patterns(".docpipe"))
    full = tmp_path / "full.json"
    written = runner.invoke(app, ["scan", "--root", str(root), "--out", str(full), "--no-cache"])
    assert written.exit_code == 0, written.output

    payload = _json(
        ["scan", "--root", str(root), "--scope", PRICING, "--from-manifest", str(full)]
        + ["--stats", "--format", "json"]
    )
    scope = payload["scope"]

    assert isinstance(scope, dict)
    assert scope["partial"] is True
    assert scope["missing_from_cache"] > 0  # кэш холодный: копия без `.docpipe/`


def test_parse_error_files_are_in_the_report(wild_solution: Path) -> None:
    """Тип, уничтоженный `#if` внутри выражения, не виден ни в одном счётчике."""
    payload = _json(
        ["scan", "--root", str(wild_solution), "--stats", "--format", "json", "--no-cache"]
    )
    files = payload["parse_error_files"]

    assert isinstance(files, list)
    assert any(path.endswith("ConditionalModule.cs") for path in files)
    assert files == sorted(files)


# --------------------------------------------------------------------------------------
# Срезы и `--top`
# --------------------------------------------------------------------------------------


def test_all_six_slices_are_always_present(sample_solution: Path) -> None:
    """Пустой срез — `total: 0`, а не пропавший ключ: «не посчитано» и «ноль» —
    разные ответы."""
    report = build_stats_report(run(sample_solution).stats, lang="cs")

    assert set(report.breakdown) == set(BREAKDOWN_KEYS.values())
    assert report.breakdown["attributes"].total == 0
    assert report.breakdown["modules"].items == [("Sample.Pricing.Api", 1)]


def test_every_slice_title_has_a_latin_key(tmp_path: Path) -> None:
    """Срез, забытый в таблице соответствия, выпал бы из JSON молча."""
    symbol = _symbol(
        "OrderService",
        base_type_closure=["IService"],
        attributes=[Attribute(name="Serializable")],
    )
    stats = collect_stats({symbol.fqn: symbol}, [], _empty_ruleset(tmp_path))

    assert set(stats.breakdown) == set(BREAKDOWN_KEYS)


def test_unknown_slice_title_is_refused() -> None:
    stats = Stats(counts={}, total=0, breakdown={"новый срез": [("x", 1)]})

    with pytest.raises(ValueError, match="новый срез"):
        build_stats_report(stats, lang="cs")


def test_top_cuts_items_but_not_total(tmp_path: Path) -> None:
    """`total` — до усечения, иначе усечённый срез неотличим от полного."""
    stats = _stats_of(["AlphaRq", "BetaRq", "GammaDm", "DeltaGuard"], tmp_path)
    report = build_stats_report(stats, lang="cs", top=1)

    assert report.breakdown["last_words"].total == 3
    assert report.breakdown["last_words"].items == [("Rq", 2)]
    assert build_stats_report(stats, lang="cs", top=0).breakdown["last_words"].items == []


def test_cli_top_applies_to_json(wild_solution: Path) -> None:
    payload = _json(
        ["scan", "--root", str(wild_solution), "--stats", "--format", "json", "--top", "1"]
        + ["--no-cache"]
    )
    breakdown = payload["breakdown"]
    assert isinstance(breakdown, dict)
    namespaces = breakdown["namespaces"]

    assert len(namespaces["items"]) == 1
    assert namespaces["total"] > 1


def test_negative_top_is_refused() -> None:
    with pytest.raises(ValueError, match="отрицательным"):
        build_stats_report(Stats(counts={}, total=0), lang="cs", top=-1)


# --------------------------------------------------------------------------------------
# Срез «последнее слово»: по тесту на каждую ловушку разбиения
# --------------------------------------------------------------------------------------


def test_digits_stick_to_the_word() -> None:
    """Граница режет только перед заглавной: дата миграции — часть слова."""
    assert camel_words("Migration20240101") == ["Migration20240101"]
    assert camel_words("Migration20240101Initial") == ["Migration20240101", "Initial"]
    assert last_word("Migration20240101") == "Migration20240101"


def test_typescript_name_starts_lowercase() -> None:
    assert camel_words("authInterceptor") == ["auth", "Interceptor"]
    assert last_word("authInterceptor") == "Interceptor"


def test_interface_prefix_is_split_off() -> None:
    assert camel_words("IPricingProvider") == ["I", "Pricing", "Provider"]
    assert last_word("IPricingProvider") == "Provider"
    # Серия заглавных перед словом — одно слово, как в имени файла.
    assert camel_words("HTTPClientFactory") == ["HTTP", "Client", "Factory"]
    assert camel_words("IOStream") == ["IO", "Stream"]


def test_type_parameters_and_separators_are_not_words() -> None:
    assert camel_words("Repository<TEntity, TKey>") == ["Repository"]
    assert camel_words("_weird__Name_") == ["weird", "Name"]
    assert camel_words("<T>") == []


def test_lone_i_is_not_a_word_of_the_slice(tmp_path: Path) -> None:
    """Строка «I» ни на какое правило не указывает и в срез не идёт."""
    assert last_word("I") == ""
    assert last_word("ServiceI") == ""
    assert last_word("") == ""

    stats = _stats_of(["I", "ServiceI", "IOrderService"], tmp_path)
    rows = dict(stats.breakdown["последнее слово"])

    assert "I" not in rows
    assert rows == {"Service": 1}


def test_last_words_catch_project_conventions(tmp_path: Path) -> None:
    """Ради чего срез: конвенции проекта и фронта, которых нет в словаре окончаний."""
    names = ["CreateOrderRq", "UpdateOrderRq", "OrderDm", "authGuard", "AppState", "Banner"]
    stats = _stats_of(names, tmp_path)

    assert stats.breakdown["последнее слово"] == [
        ("Rq", 2),
        ("Banner", 1),
        ("Dm", 1),
        ("Guard", 1),
        ("State", 1),
    ]
    # Словарь окончаний .NET отправляет все шесть в «(прочее)».
    assert stats.breakdown["окончания имён"] == [("(прочее)", 6)]


def test_slugify_still_splits_the_same_way() -> None:
    """`slugify` перешёл на `camel_words`; путь документа меняться не должен."""
    cases = {
        "PricingController": "pricing-controller",
        "HTTPClientFactory": "http-client-factory",
        "IPricingProvider<T>": "i-pricing-provider",
        "Migration20240101Initial": "migration20240101-initial",
        "authInterceptor": "auth-interceptor",
        "Foo_Bar-Baz": "foo-bar-baz",
        "<>": "unnamed",
    }
    assert {name: slugify(name) for name in cases} == cases


# --------------------------------------------------------------------------------------
# Текст и форма вывода
# --------------------------------------------------------------------------------------


def test_text_report_gains_one_slice_in_place(wild_solution: Path) -> None:
    """Текст прежний плюс новый срез — между окончаниями и базовыми типами."""
    report = format_report(run(wild_solution).stats)
    positions = [
        report.index("  модули:"),
        report.index("  окончания имён:"),
        report.index("  последнее слово:"),
        report.index("  базовые типы:"),
        report.index("  namespace:"),
    ]

    assert positions == sorted(positions)


def test_text_slice_marks_what_it_cut_off(tmp_path: Path) -> None:
    stats = _stats_of(["AlphaRq", "BetaDm"], tmp_path)
    text = format_breakdown(stats, top=1)

    assert "последнее слово:" in text
    assert "и ещё 1 строка" in text


def test_json_output_is_stable_and_has_no_trailing_blank_line(sample_solution: Path) -> None:
    arguments = ["scan", "--root", str(sample_solution), "--stats", "--format", "json"]
    arguments.append("--no-cache")
    first = runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    assert first.exit_code == 0, first.output
    assert first.stdout == second.stdout
    assert first.stdout.endswith("}\n")
    assert not first.stdout.endswith("\n\n")
    assert list(json.loads(first.stdout)) == sorted(json.loads(first.stdout))


def test_unknown_format_is_refused(sample_solution: Path) -> None:
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--stats", "--format", "jsno", "--no-cache"]
    )

    assert result.exit_code == 2
    assert "допустимы: text, json" in result.output


@pytest.mark.parametrize("command", [["scan"], ["web", "scan"]])
def test_json_without_stats_is_refused(command: list[str], tmp_path: Path) -> None:
    """Иначе `--format json` молча напечатал бы строку о записи манифеста."""
    out = tmp_path / "m.json"
    result = runner.invoke(
        app, [*command, "--root", str(tmp_path), "--out", str(out), "--format", "json"]
    )

    assert result.exit_code == 2
    assert "--stats" in result.output
    assert not out.exists()


def test_negative_top_is_refused_by_cli(web_workspace: Path) -> None:
    result = runner.invoke(
        app, ["web", "scan", "--root", str(web_workspace), "--stats", "--top", "-1", "--no-cache"]
    )

    assert result.exit_code == 2


def test_report_model_is_frozen_and_strict(sample_solution: Path) -> None:
    report = build_stats_report(run(sample_solution).stats, lang="cs")
    payload = report.model_dump(mode="json")

    with pytest.raises(ValidationError):
        StatsReport.model_validate({**payload, "extra": 1})
    assert StatsReport.model_validate(payload) == report
