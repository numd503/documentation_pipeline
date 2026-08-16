"""Сквозной прогон `docpipe web scan` (F11).

Манифест фронта — отдельный файл той же схемы. Проверяется не «команда
отработала», а три свойства, каждое из которых ломается молча: побайтовая
воспроизводимость, независимость от порядка обхода и сброс кэша при апгрейде
грамматики.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cache import ParseCache
from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, WebConfig
from docpipe.model import Manifest, ParserVersions
from docpipe.web.tree import parser_versions
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")


@pytest.fixture
def manifest(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


# --------------------------------------------------------------------------------------
# Что получилось
# --------------------------------------------------------------------------------------


def test_modules_are_typescript_and_keyed_by_boundary(manifest: Manifest) -> None:
    assert [module.id for module in manifest.modules] == [
        "module:nx-app/apps/widget",
        "module:src",
    ]
    assert {module.lang for module in manifest.modules} == {"ts"}


def test_nodes_carry_calls_and_routes(manifest: Manifest) -> None:
    """Вызовы — на узле того файла, где записаны; маршруты — на компоненте."""
    by_title = {node.title: node for node in manifest.nodes}

    assert [call.key.route for call in by_title["AuditService"].web_calls] == [
        "integration/log/auditj"
    ] * 3
    assert [entry.path for entry in by_title["QuizComponent"].routes] == ["models/loader/quiz"]
    assert by_title["ModelService"].routes == []


def test_manifest_has_no_csharp_grammar(manifest: Manifest) -> None:
    """Версия чужой грамматики была бы шумом, а `null` читается однозначно."""
    assert manifest.parser.grammar_typescript
    assert manifest.parser.grammar_c_sharp is None


def test_manifest_validates_as_the_common_schema(web_workspace: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.web.json"
    result = runner.invoke(
        app, ["web", "scan", "--root", str(web_workspace), "--out", str(out), "--no-cache"]
    )
    assert result.exit_code == 0, result.output

    validated = runner.invoke(app, ["validate", str(out)])
    assert validated.exit_code == 0, validated.output


def test_report_names_every_number(web_workspace: Path, tmp_path: Path) -> None:
    """«Связь построена для 340 вызовов» без второго числа не значит ничего."""
    result = runner.invoke(
        app,
        ["web", "scan", "--root", str(web_workspace), "--out", str(tmp_path / "w.json")],
    )
    assert "восстановлено 15" in result.output
    assert "не восстановлено 1" in result.output
    assert "Страниц: 6" in result.output
    assert "маршрут не собран у 1" in result.output


# --------------------------------------------------------------------------------------
# Детерминизм
# --------------------------------------------------------------------------------------


def test_two_runs_give_byte_identical_files(web_workspace: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        result = runner.invoke(
            app, ["web", "scan", "--root", str(web_workspace), "--out", str(out), "--no-cache"]
        )
        assert result.exit_code == 0, result.output

    assert first.read_bytes() == second.read_bytes()


def test_no_run_metadata_leaks_into_the_manifest(web_workspace: Path, tmp_path: Path) -> None:
    """Ни времени, ни хоста: проверка воспроизводимости — побайтовое сравнение."""
    out = tmp_path / "w.json"
    runner.invoke(app, ["web", "scan", "--root", str(web_workspace), "--out", str(out)])

    text = out.read_text(encoding="utf-8")
    assert "generated_at" not in text
    assert "host" not in text

    sidecar = json.loads((tmp_path / "w.run.json").read_text(encoding="utf-8"))
    assert sidecar["generated_at"] and sidecar["host"]


def test_sidecar_carries_the_named_numbers(web_workspace: Path, tmp_path: Path) -> None:
    out = tmp_path / "w.json"
    runner.invoke(app, ["web", "scan", "--root", str(web_workspace), "--out", str(out)])
    stats = json.loads((tmp_path / "w.run.json").read_text(encoding="utf-8"))["stats"]

    assert stats["calls_resolved"] + stats["calls_unresolved"] == 16
    assert stats["routes"] == 6
    assert stats["routes_unresolved"] == 1


def test_result_does_not_depend_on_the_listing_order(
    web_workspace: Path, manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок обхода ФС источником порядка быть не может.

    Подменяется сам `os.walk`: перемешивание списка после сортировки проверило
    бы только сортировку, а не то, что от порядка не зависит ничего дальше.
    """
    import os

    original = os.walk

    def shuffled(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        for dirpath, dirnames, filenames in original(*args, **kwargs):  # type: ignore[arg-type]
            yield dirpath, list(reversed(dirnames)), list(reversed(filenames))

    monkeypatch.setattr(os, "walk", shuffled)
    assert run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest == manifest


# --------------------------------------------------------------------------------------
# Кэш
# --------------------------------------------------------------------------------------


def test_cache_is_reused_between_runs(web_workspace: Path, tmp_path: Path) -> None:
    first = run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web"), cache_dir=tmp_path)
    second = run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web"), cache_dir=tmp_path)

    assert first.manifest == second.manifest
    assert (tmp_path / "parse-web.sqlite").is_file()


def test_grammar_upgrade_invalidates_the_cache(web_workspace: Path, tmp_path: Path) -> None:
    """Иначе прогон отдаст разбор, сделанный старой грамматикой, — без сообщения.

    Хэш содержимого при апгрейде не меняется, поэтому попадание в кэш дало бы
    устаревший результат, и единственный способ это заметить — сверить версии.
    """
    run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web"), cache_dir=tmp_path)

    database = tmp_path / "parse-web.sqlite"
    current = parser_versions()
    with ParseCache(database, current) as cache:
        assert cache.all_paths()

    upgraded = current.model_copy(update={"grammar_typescript": "99.0.0"})
    with ParseCache(database, upgraded) as cache:
        assert cache.all_paths() == []


def test_web_cache_does_not_fight_the_dotnet_cache(web_workspace: Path, tmp_path: Path) -> None:
    """Общий файл кэша два шага инвалидировали бы друг другу на каждом прогоне.

    Версии грамматик у них разные, а запись о версии в базе одна.
    """
    run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web"), cache_dir=tmp_path)
    assert (tmp_path / "parse-web.sqlite").is_file()
    assert not (tmp_path / "parse.sqlite").exists()


# --------------------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------------------


def test_registry_table_reaches_the_keys(web_workspace: Path) -> None:
    """Смена настройки меняет ключи связи, не трогая исходники."""
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
    result = run_web(web_workspace, config, load_ruleset(RULES, "web"))
    items = next(node for node in result.manifest.nodes if node.title == "ItemsService")

    assert sorted(call.key.discriminator for call in items.web_calls) == ["", "", "models", "users"]


def test_url_rewrite_is_applied_per_module(web_workspace: Path) -> None:
    """У семи фронтов репозитория семь разных `pathRewrite`.

    Одно правило на прогон склеило бы их маршруты; правило берётся по модулю,
    в котором лежит файл.
    """
    config = DocpipeConfig(
        web=WebConfig.model_validate({"url_rewrite": [{"module": "widget", "strip_prefix": "/pm"}]})
    )
    result = run_web(web_workspace, config, load_ruleset(RULES, "web"))
    by_title = {node.title: node for node in result.manifest.nodes}

    assert [call.key.route for call in by_title["WidgetService"].web_calls] == [
        "api/limits/getperiods"
    ]
    # Модуль без записи в таблице остаётся как есть.
    assert by_title["ModelService"].web_calls[0].key.route.startswith("api/ml/")


def test_roots_narrow_the_scan(web_workspace: Path) -> None:
    config = DocpipeConfig(web=WebConfig(roots=["nx-app"]))
    result = run_web(web_workspace, config, load_ruleset(RULES, "web"))

    assert {node.module for node in result.manifest.nodes} == {"widget"}


def test_versions_go_into_the_manifest_and_the_sidecar(web_workspace: Path, tmp_path: Path) -> None:
    out = tmp_path / "w.json"
    runner.invoke(app, ["web", "scan", "--root", str(web_workspace), "--out", str(out)])

    parser = json.loads(out.read_text(encoding="utf-8"))["parser"]
    assert (
        parser["grammar_typescript"]
        == ParserVersions(
            tree_sitter=parser["tree_sitter"], grammar_typescript=parser["grammar_typescript"]
        ).grammar_typescript
    )
