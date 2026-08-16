"""Сведение двух манифестов (F13).

Пять категорий, и только последняя — дефект. Проверяется в первую очередь то,
что находка **не** роняет прогон: линт, красный с первого дня, выключат
на второй, и вместе с ним пропадут работающие проверки.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.emit import run as run_dotnet
from docpipe.model import Manifest
from docpipe.web.link import LinkReport, build_report, format_report
from docpipe.web.tree import run as run_web

runner = CliRunner()


@pytest.fixture
def backend(wild_solution: Path) -> Manifest:
    return run_dotnet(wild_solution, DocpipeConfig()).manifest


@pytest.fixture
def frontend(web_workspace: Path) -> Manifest:
    return run_web(
        web_workspace, DocpipeConfig(), load_ruleset(Path("rules/rules.yaml"), "web")
    ).manifest


@pytest.fixture
def report(backend: Manifest, frontend: Manifest) -> LinkReport:
    return build_report(backend, frontend, configured_modules={"tr-p"})


def _links(report: LinkReport, route: str) -> list[str]:
    return sorted(link.discriminator for link in report.links if link.route == route)


# --------------------------------------------------------------------------------------
# Связь найдена
# --------------------------------------------------------------------------------------


def test_link_by_a_literal(report: LinkReport) -> None:
    link = next(link for link in report.links if link.route == "api/ml/innerdebts/insert")
    assert link.match == "exact"
    assert link.endpoints and all("InnerDebtsController" in node for node in link.endpoints)


def test_link_by_a_template(report: LinkReport) -> None:
    """`` `api/ml/innerdebts/state/byclient/${clientId}` `` против маршрута без параметра."""
    link = next(
        link for link in report.links if link.route == "api/ml/innerdebts/state/byclient/{}"
    )
    assert link.match == "almost"


def test_link_by_a_resolved_constant(report: LinkReport) -> None:
    """Через `auditUrl` идут три вызова; без разрешения константы связи не было бы.

    Эндпоинта у них нет — это внешняя интеграция, — но вызовы обязаны быть
    видны с файлом и строкой, а не пропасть.
    """
    orphans = [
        item for item in report.calls_without_endpoint if item.route == "integration/log/auditj"
    ]
    assert len(orphans) == 3
    assert all(item.file.endswith("audit.service.ts") and item.line > 0 for item in orphans)


def test_almost_match_catches_the_named_pair(report: LinkReport) -> None:
    """`…/getforupdate` ↔ `…/getforupdate/{}`: инвентарь, а не дефект."""
    link = next(link for link in report.links if link.route == "api/ml/structure/getforupdate/{}")
    assert link.match == "almost"
    assert link.endpoints


def test_exact_match_wins_over_almost(report: LinkReport) -> None:
    """Иначе точные связи уедут в корзину «почти» и число настоящих занизится."""
    link = next(link for link in report.links if link.route == "api/ml/structure")
    assert link.match == "exact"


# --------------------------------------------------------------------------------------
# Обращения к реестру
# --------------------------------------------------------------------------------------


def test_registry_calls_do_not_merge(web_workspace: Path, backend: Manifest) -> None:
    """Два вызова одного маршрута с разными именами списков — две связи.

    Ключ «метод + маршрут» склеил бы обращения к пользователям и к моделям
    в одну точку и потерял ровно тот смысл, ради которого связь строится.
    """
    from docpipe.config import WebConfig

    config = DocpipeConfig(
        web=WebConfig.model_validate(
            {
                "registry_calls": [
                    {
                        "route": "api/items/query",
                        "discriminator": {"in": "body", "name": "listInnerName"},
                        "kind": "list",
                    }
                ]
            }
        )
    )
    frontend = run_web(
        web_workspace, config, load_ruleset(Path("rules/rules.yaml"), "web")
    ).manifest
    report = build_report(backend, frontend)

    assert _links(report, "api/items/query") == ["models", "users"]
    # Эндпоинт при этом один и тот же: различитель говорит «зачем», а не «куда».
    assert (
        len({tuple(link.endpoints) for link in report.links if link.route == "api/items/query"})
        == 1
    )


# --------------------------------------------------------------------------------------
# Категории, которые дефектом не являются
# --------------------------------------------------------------------------------------


def test_endpoint_without_a_caller_is_not_a_defect(report: LinkReport) -> None:
    """Его может звать другая система, мобильный клиент или интеграция."""
    routes = {item.route for item in report.endpoints_without_caller}
    assert "api/ml/structure/purge" in routes


def test_conventional_controllers_are_a_separate_category(report: LinkReport) -> None:
    """Иначе их вызовы попадут в «эндпоинт не найден» и будут выглядеть дефектом фронта."""
    assert any("HomeController" in node for node in report.conventional_controllers)


def test_unconfigured_module_is_named(report: LinkReport) -> None:
    """Пустое правило и отсутствие правила — разные вещи.

    Забытая настройка выглядит как исправная связь, а расходиться будет ровно
    на префиксе.
    """
    assert report.unconfigured_modules == ["widget"]


def test_configured_module_is_not_named(backend: Manifest, frontend: Manifest) -> None:
    report = build_report(backend, frontend, configured_modules={"tr-p", "widget"})
    assert report.unconfigured_modules == []


# --------------------------------------------------------------------------------------
# Единственный дефект
# --------------------------------------------------------------------------------------


def test_one_key_declared_twice_is_a_defect(report: LinkReport) -> None:
    """Коллизия маршрутов ломает приложение: ASP.NET не выберет из двух действий."""
    assert len(report.duplicate_endpoints) == 1

    duplicate = report.duplicate_endpoints[0]
    assert (duplicate.http_method, duplicate.route) == ("GET", "api/ml/structure")
    assert len(duplicate.nodes) == 2


# --------------------------------------------------------------------------------------
# Числа и детерминизм
# --------------------------------------------------------------------------------------


def test_every_call_lands_in_exactly_one_category(report: LinkReport) -> None:
    """Сумма обязана сходиться: иначе «связей 6» не значит ничего."""
    assert (
        report.counts["linked"] + report.counts["almost"] + report.counts["calls_without_endpoint"]
        == report.counts["calls_total"]
    )


def test_report_is_deterministic_under_shuffled_input(
    backend: Manifest, frontend: Manifest, report: LinkReport
) -> None:
    shuffled_backend = backend.model_copy(update={"nodes": list(reversed(backend.nodes))})
    shuffled_web = frontend.model_copy(update={"nodes": list(reversed(frontend.nodes))})

    assert build_report(shuffled_backend, shuffled_web, {"tr-p"}) == report


def test_no_run_metadata_in_the_artifact(report: LinkReport) -> None:
    """Времени в файле нет: иначе он меняется на каждом прогоне и его не сравнить."""
    dumped = report.model_dump(mode="json")
    assert "generated_at" not in dumped and "host" not in dumped


def test_text_report_names_both_numbers(report: LinkReport) -> None:
    text = format_report(report)
    assert "Вызовов фронта: 15" in text
    assert "эндпоинтов бэкенда: 10" in text


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


@pytest.fixture
def manifests(wild_solution: Path, web_workspace: Path, tmp_path: Path) -> tuple[Path, Path]:
    backend_path, web_path = tmp_path / "net.json", tmp_path / "web.json"
    runner.invoke(app, ["scan", "--root", str(wild_solution), "--out", str(backend_path)])
    runner.invoke(
        app, ["web", "scan", "--root", str(web_workspace), "--out", str(web_path), "--no-cache"]
    )
    return backend_path, web_path


def test_command_writes_the_artifact(manifests: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "web-link.json"
    result = runner.invoke(
        app, ["web", "link", str(manifests[0]), str(manifests[1]), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["counts"]["duplicate_endpoints"] == 1


def test_findings_do_not_change_the_exit_code(manifests: tuple[Path, Path], tmp_path: Path) -> None:
    """Даже коллизия: пока категория не названа в `--fail-on`, прогон зелёный."""
    result = runner.invoke(
        app,
        ["web", "link", str(manifests[0]), str(manifests[1]), "--out", str(tmp_path / "l.json")],
    )
    assert result.exit_code == 0


def test_fail_on_names_the_category(manifests: tuple[Path, Path], tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "web",
            "link",
            str(manifests[0]),
            str(manifests[1]),
            "--out",
            str(tmp_path / "l.json"),
            "--fail-on",
            "duplicate_endpoints",
        ],
    )
    assert result.exit_code == 1
    assert "duplicate_endpoints" in result.output


def test_unknown_fail_on_category_is_refused(manifests: tuple[Path, Path], tmp_path: Path) -> None:
    """Опечатка в имени категории значила бы, что проверка молча выключена."""
    result = runner.invoke(
        app,
        [
            "web",
            "link",
            str(manifests[0]),
            str(manifests[1]),
            "--out",
            str(tmp_path / "l.json"),
            "--fail-on",
            "duplicates",
        ],
    )
    assert result.exit_code == 2


def test_json_format_is_stable(manifests: tuple[Path, Path], tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        runner.invoke(
            app,
            [
                "web",
                "link",
                str(manifests[0]),
                str(manifests[1]),
                "--out",
                str(out),
                "--format",
                "json",
            ],
        )
    assert first.read_bytes() == second.read_bytes()
