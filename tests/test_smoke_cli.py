"""Прогон всех команд CLI и кодов возврата (T21).

Тесты каждой команды по отдельности лежат рядом; здесь проверяется, что все они
живы одновременно, что коды возврата означают то, что обещано, и что пример
конфигурации из репозитория действительно работает.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe import __version__
from docpipe.cli import app
from docpipe.config import load_config

runner = CliRunner()
EXAMPLE_CONFIG = Path("docpipe.example.yaml")


@pytest.fixture
def manifest(sample_solution: Path, tmp_path: Path) -> Path:
    out = tmp_path / "doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    return out


# --------------------------------------------------------------------------------------
# Все команды на месте и отвечают
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["version", "schema", "scan", "diff", "validate", "stats"])
def test_command_has_help(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert command in result.output


def test_root_help_lists_every_command() -> None:
    result = runner.invoke(app, ["--help"])
    for command in ("version", "schema", "scan", "diff", "validate", "stats"):
        assert command in result.output


def test_version(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_schema(tmp_path: Path) -> None:
    out = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", "--out", str(out)])

    assert result.exit_code == 0
    assert '"$defs"' in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Сквозной сценарий
# --------------------------------------------------------------------------------------


def test_full_pipeline(sample_solution: Path, tmp_path: Path) -> None:
    """Прогон, проверка, статистика, дифф, скоуп — в том порядке, в каком их зовут."""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert (
        runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(first)]).exit_code
        == 0
    )
    assert runner.invoke(app, ["validate", str(first)]).exit_code == 0
    assert runner.invoke(app, ["stats", str(first)]).exit_code == 0

    assert (
        runner.invoke(
            app,
            [
                "scan",
                "--root",
                str(sample_solution),
                "--out",
                str(second),
                "--scope",
                "src/Sample.Pricing.Api",
                "--from-manifest",
                str(first),
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["diff", str(first), str(second)])
    assert result.exit_code == 0
    assert "Изменений нет" in result.output


def test_example_config_works(sample_solution: Path, tmp_path: Path) -> None:
    """Пример конфигурации из репозитория обязан загружаться и работать.

    Проверяется именно файл, а не его копия: пример, который не запускается,
    хуже отсутствующего — он выглядит рабочим.
    """
    settings = load_config(EXAMPLE_CONFIG)
    assert settings.enrolled == ["src/**"]
    assert settings.domains == {}  # все записи закомментированы, и это не ошибка

    out = tmp_path / "doc-tree.json"
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--config",
            str(EXAMPLE_CONFIG),
            "--out",
            str(out),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()


# --------------------------------------------------------------------------------------
# Коды возврата означают то, что обещано
# --------------------------------------------------------------------------------------


def test_exit_codes(manifest: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"

    # 0 — всё в порядке.
    assert runner.invoke(app, ["validate", str(manifest)]).exit_code == 0
    assert runner.invoke(app, ["diff", str(manifest), str(manifest)]).exit_code == 0

    # 1 — манифест не прошёл проверку.
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    assert runner.invoke(app, ["validate", str(broken)]).exit_code == 1

    # 2 — ошибка вызова: не найден файл или недопустимый аргумент.
    assert runner.invoke(app, ["stats", str(missing)]).exit_code == 2
    assert runner.invoke(app, ["diff", str(missing), str(missing)]).exit_code == 2
    assert (
        runner.invoke(
            app, ["scan", "--root", str(tmp_path / "nowhere"), "--out", str(tmp_path / "o.json")]
        ).exit_code
        == 2
    )
    assert (
        runner.invoke(
            app, ["scan", "--root", ".", "--out", str(tmp_path / "o.json"), "--scope", "src"]
        ).exit_code
        == 2
    )


def test_module_entry_point_works() -> None:
    """`python -m docpipe` — запасной путь для окружения без установленного проекта.

    Проверяется реальным запуском подпроцесса: импорт `docpipe.__main__` в этом
    же процессе не выполнил бы ветку `if __name__ == "__main__"` и не поймал бы
    опечатку в имени объекта приложения.
    """
    result = subprocess.run(
        [sys.executable, "-m", "docpipe", "version"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == __version__


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "scan" in result.output


def test_unknown_command() -> None:
    assert runner.invoke(app, ["nonesuch"]).exit_code != 0
