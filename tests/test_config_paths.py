"""Разрешение путей, названных ключами `docpipe.yaml`.

Конфигурация инструмента лежит не в корне репозитория (на АС CF —
`docs/ml/docspipe/…`), а пути внутри неё пишут относительно неё же: это
единственный вид пути, который автор конфигурации может проверить глазами,
не помня, из какого каталога зовут команду. До этой ступени `business new`
искал скелет только от текущего каталога и падал со «Скелет не найден» на
конфигурации, которая для `scan` и `materialize` выглядела рабочей.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import candidate_inputs, resolve_input

runner = CliRunner()


@pytest.fixture
def nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Раскладка боевого репозитория: конфигурация и шаблоны лежат вместе
    внутри `docs/`, а команды зовутся из корня."""
    tools = tmp_path / "docs" / "ml" / "cashflow-docpipe"
    tools.mkdir(parents=True)
    shutil.copytree(Path("templates"), tools / "templates")
    (tools / "docpipe.yaml").write_text(
        'templates: "templates"\nbusiness_root: "docs/ml/cashflow-docpipe/business"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------------------
# `resolve_input`
# --------------------------------------------------------------------------------------


def test_current_directory_wins_over_the_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок ступеней именно такой, а не обратный.

    Конфигурации в корне репозитория писались с путями от текущего каталога.
    Отдай приоритет каталогу конфигурации — и прогон не упал бы, а взял
    другой набор шаблонов, ничего не сказав.
    """
    (tmp_path / "here").mkdir()
    (tmp_path / "beside").mkdir()
    (tmp_path / "beside" / "here").mkdir()
    monkeypatch.chdir(tmp_path)

    assert resolve_input("here", tmp_path / "beside" / "docpipe.yaml") == Path("here")


def test_falls_back_to_the_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "beside" / "templates").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "beside" / "docpipe.yaml"

    assert resolve_input("templates", config) == config.parent / "templates"


def test_missing_everywhere_names_what_the_author_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сообщение об ошибке обязано называть значение из конфигурации, а не
    последнее из перебранного: иначе человек ищет опечатку не там."""
    monkeypatch.chdir(tmp_path)

    assert resolve_input("templates", tmp_path / "beside" / "docpipe.yaml") == Path("templates")


def test_absolute_path_has_a_single_candidate(tmp_path: Path) -> None:
    """Абсолютный путь достраивать нечем, и склейка дала бы путь, который
    выглядит настоящим и указывает в никуда."""
    assert candidate_inputs(tmp_path / "templates", tmp_path / "docpipe.yaml") == [
        tmp_path / "templates"
    ]


def test_without_a_config_there_is_nothing_to_be_relative_to(tmp_path: Path) -> None:
    assert candidate_inputs("templates", None) == [Path("templates")]


# --------------------------------------------------------------------------------------
# `business new`
# --------------------------------------------------------------------------------------


def test_new_finds_templates_beside_the_config(nested: Path) -> None:
    """Ровно случай, из-за которого ступень появилась."""
    result = runner.invoke(
        app,
        [
            "business",
            "new",
            "bp.pricing.eod",
            "--title",
            "Переоценка",
            "--config",
            "docs/ml/cashflow-docpipe/docpipe.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (nested / "docs/ml/cashflow-docpipe/business/processes/pricing/eod.md").is_file()


def test_new_lists_every_candidate_when_the_skeleton_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Не найден» с одним путём заставляет гадать, искался ли второй, —
    а виновата здесь поставка, а не команда."""
    tools = tmp_path / "docs" / "tools"
    tools.mkdir(parents=True)
    (tools / "docpipe.yaml").write_text('templates: "templates"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["business", "new", "bp.a.b", "--title", "Т", "--config", "docs/tools/docpipe.yaml"],
    )

    assert result.exit_code == 2
    assert "templates/business/process.md" in result.output
    assert "docs/tools/templates/business/process.md" in result.output
