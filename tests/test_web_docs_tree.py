"""Вторая ветка дерева документации: `web.modules_dir`.

Дерево фронта живёт по своим правилам: единица документации там страница,
а не класс, и половина узлов вообще не получает файла. Смешанные в одном
каталоге, две ветки читаются как одна и та же — при том, что искать в них
надо разное.

Инвариант «`materialize` пишет туда, где ищет `docs status`» при этом обязан
держаться: обход документов идёт от `docs_root` и накрывает обе ветки разом.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, WebConfig, load_config
from docpipe.materialize.plan import expected_root, is_web
from docpipe.model import Manifest
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")


def _config(tmp_path: Path, **web: str) -> Path:
    path = tmp_path / "docpipe.yaml"
    path.write_text(
        yaml.safe_dump({"docs_root": "docs", "modules_dir": "modules", "web": web}),
        encoding="utf-8",
    )
    return path


def _web_manifest(workspace: Path, config: DocpipeConfig) -> Manifest:
    return run_web(workspace, config, load_ruleset(RULES, "web")).manifest


# --------------------------------------------------------------------------------------
# Раскладка
# --------------------------------------------------------------------------------------


def test_front_documents_go_to_their_own_branch(web_workspace: Path) -> None:
    config = DocpipeConfig(web=WebConfig(modules_dir="front"))
    manifest = _web_manifest(web_workspace, config)

    roots = {node.doc_path.split("/")[1] for node in manifest.nodes}

    assert roots == {"front"}
    assert config.web_modules_root == "docs/front"


def test_empty_key_keeps_the_old_layout(web_workspace: Path) -> None:
    """Конфигурации, написанные до появления ключа, обязаны давать те же пути."""
    manifest = _web_manifest(web_workspace, DocpipeConfig())

    assert all(node.doc_path.startswith("docs/modules/") for node in manifest.nodes)
    assert DocpipeConfig().web_modules_root == "docs/modules"


def test_backend_keeps_its_branch_under_the_same_config(
    sample_solution: Path, tmp_path: Path
) -> None:
    """Разведение веток не должно трогать шаг 1: у него свой ключ."""
    config = _config(tmp_path, modules_dir="front")
    out = tmp_path / "net.json"

    result = runner.invoke(
        app,
        ["scan", "--root", str(sample_solution), "--config", str(config), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output

    manifest = Manifest.model_validate_json(out.read_text(encoding="utf-8"))
    assert all(node.doc_path.startswith("docs/modules/") for node in manifest.nodes)


def test_web_modules_dir_must_be_repo_relative() -> None:
    """То же требование, что и у остальных путей: иначе манифест непереносим."""
    with pytest.raises(ValueError):
        WebConfig(modules_dir="/absolute/front")


# --------------------------------------------------------------------------------------
# Инвариант «пишем туда, где ищем»
# --------------------------------------------------------------------------------------


def test_status_finds_documents_in_the_second_branch(web_workspace: Path, tmp_path: Path) -> None:
    """Ветка внутри `docs_root`, поэтому обход находит её без отдельного ключа.

    Если бы это был путь целиком, документы легли бы туда, где их никто
    не ищет: каждый навсегда `missing` и переписывается на каждом прогоне.
    """
    config = _config(tmp_path, modules_dir="front")
    manifest = tmp_path / "web.json"
    docs = tmp_path / "tree"
    docs.mkdir()

    scan = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(web_workspace),
            "--config",
            str(config),
            "--out",
            str(manifest),
        ],
    )
    assert scan.exit_code == 0, scan.output

    assert (
        runner.invoke(
            app,
            ["materialize", str(manifest), "--root", str(docs), "--config", str(config)],
        ).exit_code
        == 0
    )
    status = runner.invoke(
        app, ["docs", "status", str(manifest), "--root", str(docs), "--config", str(config)]
    )

    assert (docs / "docs/front").is_dir()
    assert "missing" not in status.output


def test_drift_is_refused_and_names_the_key(web_workspace: Path, tmp_path: Path) -> None:
    """Манифест собран с одной раскладкой, конфигурация читается заново.

    Молча это не ломается ничем видимым, поэтому прогон отказывает — и обязан
    назвать тот ключ, который человек правил.
    """
    config = _config(tmp_path, modules_dir="front")
    manifest = tmp_path / "web.json"
    runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(web_workspace),
            "--config",
            str(config),
            "--out",
            str(manifest),
        ],
    )

    (tmp_path / "plain").mkdir()
    plain = tmp_path / "plain" / "docpipe.yaml"
    plain.write_text(
        yaml.safe_dump({"docs_root": "docs", "modules_dir": "modules"}), encoding="utf-8"
    )

    result = runner.invoke(
        app,
        ["materialize", str(manifest), "--root", str(tmp_path / "t2"), "--config", str(plain)],
    )

    assert "раскладка манифеста разошлась" in result.output
    assert "docs/modules/" in result.output


def test_drift_message_names_the_web_key_when_it_is_set(
    web_workspace: Path, tmp_path: Path
) -> None:
    """А если ветки разведены — виноват `web.modules_dir`, и назвать надо его."""
    manifest = tmp_path / "web.json"
    runner.invoke(app, ["web", "scan", "--root", str(web_workspace), "--out", str(manifest)])

    config = _config(tmp_path, modules_dir="front")
    result = runner.invoke(
        app,
        ["materialize", str(manifest), "--root", str(tmp_path / "t3"), "--config", str(config)],
    )

    assert "web.modules_dir" in result.output


# --------------------------------------------------------------------------------------
# Очередь шага 3
# --------------------------------------------------------------------------------------


def test_worklist_carries_the_front_prefix(web_workspace: Path, tmp_path: Path) -> None:
    """Иначе внешнему исполнителю сообщается префикс, которого нет ни у одного
    документа очереди."""
    import json

    config = _config(tmp_path, modules_dir="front")
    manifest = tmp_path / "web.json"
    docs = tmp_path / "tree"
    docs.mkdir()
    queue = tmp_path / "worklist.json"

    runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(web_workspace),
            "--config",
            str(config),
            "--out",
            str(manifest),
        ],
    )
    result = runner.invoke(
        app,
        [
            "worklist",
            str(manifest),
            "--root",
            str(docs),
            "--config",
            str(config),
            "--out",
            str(queue),
        ],
    )
    assert result.exit_code == 0, result.output

    assert json.loads(queue.read_text(encoding="utf-8"))["modules_root"] == "docs/front"


def test_expected_root_chooses_by_the_manifest(web_workspace: Path) -> None:
    """Манифесты двух шагов лежат в разных файлах и не смешиваются."""
    web = _web_manifest(web_workspace, DocpipeConfig())
    backend = Manifest(ruleset_version="x", parser=web.parser)

    assert is_web(web) and not is_web(backend)
    assert expected_root(web, "docs/modules", "docs/front") == "docs/front"
    assert expected_root(backend, "docs/modules", "docs/front") == "docs/modules"


def test_config_file_accepts_the_key(tmp_path: Path) -> None:
    settings = load_config(_config(tmp_path, modules_dir="front", pages="pages.yaml"))

    assert settings.web.modules_dir == "front"
    assert settings.web_modules_root == "docs/front"
