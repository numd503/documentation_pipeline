"""Разрешение путей, названных ключами `docpipe.yaml`.

Конфигурация инструмента лежит не в корне репозитория (на АС CF —
`docs/ml/docspipe/…`), а пути внутри неё пишут относительно неё же: это
единственный вид пути, который автор конфигурации может проверить глазами,
не помня, из какого каталога зовут команду. До второй ступени `business new`
искал скелет только от текущего каталога и падал со «Скелет не найден» на
конфигурации, которая для `scan` выглядела рабочей.

Разделение, которое проверяется здесь: **входы** ищутся по двум ступеням,
**цели записи** — по одной. Первый существующий кандидат выигрывает, порядок
ступеней обратным быть не может.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import candidate_inputs, resolve_input
from tests.business_support import combined_tree, manifest

runner = CliRunner()


@pytest.fixture
def nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Раскладка боевого репозитория: конфигурация, правила и шаблоны лежат
    вместе внутри `docs/`, а команды зовутся из корня."""
    tools = tmp_path / "docs" / "ml" / "cashflow-docpipe"
    tools.mkdir(parents=True)
    shutil.copytree(Path("templates"), tools / "templates")
    shutil.copy(Path("rules/rules.yaml"), tools / "rules.yaml")
    (tools / "docpipe.yaml").write_text(
        'templates: "templates"\n'
        'rules: "rules.yaml"\n'
        "web:\n"
        '  rules: "rules.yaml"\n'
        'business_root: "docs/ml/cashflow-docpipe/business"\n',
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
    другой набор правил или шаблонов, ничего не сказав.
    """
    (tmp_path / "here").mkdir()
    (tmp_path / "beside" / "here").mkdir(parents=True)
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


def test_config_in_the_current_directory_gives_one_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обе ступени совпали — кандидат один. Дубль в списке попал бы
    в сообщение об отказе и выглядел бы как ошибка инструмента."""
    monkeypatch.chdir(tmp_path)

    assert candidate_inputs("templates", Path("docpipe.yaml")) == [Path("templates")]


# --------------------------------------------------------------------------------------
# Правила: `scan` и `web scan`
# --------------------------------------------------------------------------------------


def test_scan_finds_rules_beside_the_config(nested: Path, sample_solution: Path) -> None:
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--out",
            str(nested / "dt.json"),
            "--config",
            "docs/ml/cashflow-docpipe/docpipe.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (nested / "dt.json").is_file()


def test_web_scan_finds_rules_beside_the_config(nested: Path, web_workspace: Path) -> None:
    """Ключ `web.rules` — отдельное поле в отдельной секции, и ступень ему
    нужна ровно так же: секция `web` появилась позже, чем ключ `rules`."""
    result = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(web_workspace),
            "--out",
            str(nested / "dt.web.json"),
            "--config",
            "docs/ml/cashflow-docpipe/docpipe.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (nested / "dt.web.json").is_file()


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


def test_new_with_an_explicit_flag_does_not_gain_a_second_stage(
    nested: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--templates` задан руками из текущего каталога — вторая ступень
    к нему не применяется: флаг важнее конфигурации во всём, включая то,
    относительно чего он считается."""
    result = runner.invoke(
        app,
        [
            "business",
            "new",
            "bp.a.b",
            "--title",
            "Т",
            "--templates",
            "templates/business",
            "--config",
            "docs/ml/cashflow-docpipe/docpipe.yaml",
        ],
    )

    assert result.exit_code == 2
    assert "templates/business/process.md" in result.output
    assert "docs/ml/cashflow-docpipe" not in result.output


# --------------------------------------------------------------------------------------
# Цели записи ступени не получают
# --------------------------------------------------------------------------------------


def test_out_is_relative_to_the_current_directory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_solution: Path
) -> None:
    """`out` — цель записи, а не поиска: «первый существующий» для неё
    не значит ничего, а угадывать, куда писать, инструмент не должен.

    Проверяется тем, что рядом с конфигурацией лежит файл с тем же именем,
    и прогон его НЕ трогает.
    """
    tools = tmp_path / "docs" / "tools"
    tools.mkdir(parents=True)
    rules = Path("rules/rules.yaml").resolve()
    (tools / "docpipe.yaml").write_text(f'out: "dt.json"\nrules: "{rules}"\n', encoding="utf-8")
    (tools / "dt.json").write_text("не трогать", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["scan", "--root", str(sample_solution), "--config", "docs/tools/docpipe.yaml"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "dt.json").is_file()
    assert (tools / "dt.json").read_text(encoding="utf-8") == "не трогать"


def test_business_build_names_the_web_manifest_it_did_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`web.out` второй ступени не получает — но и молчать не может.

    «Страниц ноль» — законный исход, и по нему не отличить репозиторий без
    фронта от прогона из другого каталога. Признак различим ровно один:
    манифест лежит рядом с конфигурацией.
    """
    tree = combined_tree(tmp_path)
    (tree / "doc-tree.json").write_text(manifest().model_dump_json(), encoding="utf-8")

    tools = tree / "docs" / "tools"
    tools.mkdir(parents=True)
    (tools / "docpipe.yaml").write_text(
        'web:\n  out: "dt.web.json"\n',
        encoding="utf-8",
    )
    (tools / "dt.web.json").write_text(manifest().model_dump_json(), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "business",
            "build",
            str(tree / "doc-tree.json"),
            "--registries",
            str(tree / "registries.yaml"),
            "--root",
            str(tree),
            "--business-root",
            "business",
            "--config",
            str(tools / "docpipe.yaml"),
        ],
    )

    assert "Манифест фронта не прочитан" in result.output
    assert "dt.web.json" in result.output


def test_business_build_is_silent_when_there_is_no_frontend_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Репозиторий без фронта — обычный случай, и предупреждать не о чем:
    подсказка, печатающаяся всегда, перестаёт что-либо значить."""
    tree = combined_tree(tmp_path)
    (tree / "doc-tree.json").write_text(manifest().model_dump_json(), encoding="utf-8")

    tools = tree / "docs" / "tools"
    tools.mkdir(parents=True)
    (tools / "docpipe.yaml").write_text('web:\n  out: "dt.web.json"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "business",
            "build",
            str(tree / "doc-tree.json"),
            "--registries",
            str(tree / "registries.yaml"),
            "--root",
            str(tree),
            "--business-root",
            "business",
            "--config",
            str(tools / "docpipe.yaml"),
        ],
    )

    assert "Манифест фронта" not in result.output


# --------------------------------------------------------------------------------------
# `docpipe config check`
# --------------------------------------------------------------------------------------


def test_config_check_names_the_step_that_found_each_input(nested: Path) -> None:
    """Ради этого команда и заводилась: базу отсчёта по имени ключа не угадать.

    Пока проверить это было нечем, половина настроечных проблем выглядела как
    «инструмент не видит документы» и разбиралась на полном прогоне.
    """
    result = runner.invoke(
        app,
        ["config", "check", "--config", "docs/ml/cashflow-docpipe/docpipe.yaml", "--root", "."],
    )

    assert result.exit_code == 0, result.output
    assert "рядом с конфигурацией" in result.output
    assert "docs/ml/cashflow-docpipe/rules.yaml" in result.output
    assert "docs/ml/cashflow-docpipe/templates" in result.output


def test_config_check_fails_when_an_input_is_missing(nested: Path) -> None:
    """Ненайденный вход — код возврата, а не строка в выводе.

    Иначе проверку нельзя поставить в установщик и в CI, а сама она превращается
    в текст, который читают по диагонали.
    """
    (nested / "docs/ml/cashflow-docpipe/rules.yaml").unlink()

    result = runner.invoke(
        app,
        ["config", "check", "--config", "docs/ml/cashflow-docpipe/docpipe.yaml", "--root", "."],
    )

    assert result.exit_code == 1
    assert "НЕ НАЙДЕН" in result.output
    assert "rules" in result.output


def test_config_check_names_what_the_author_wrote_not_the_last_candidate(
    nested: Path,
) -> None:
    """Отказ обязан называть ПЕРВЫЙ кандидат — то, что человек написал
    в конфигурации, а не последнее из перебранного инструментом."""
    (nested / "docs/ml/cashflow-docpipe/rules.yaml").unlink()

    result = runner.invoke(
        app,
        ["config", "check", "--config", "docs/ml/cashflow-docpipe/docpipe.yaml", "--root", "."],
    )

    assert "НЕ НАЙДЕН: rules.yaml" in result.output


def test_config_check_separates_write_targets_from_inputs(nested: Path) -> None:
    """Цели записи второй ступени не получают, и отчёт обязан это показывать:
    «первый существующий» для них не значит ничего."""
    result = runner.invoke(
        app,
        ["config", "check", "--config", "docs/ml/cashflow-docpipe/docpipe.yaml", "--root", "."],
    )

    assert "Цели записи — только от текущего каталога" in result.output
    assert "out" in result.output


def test_config_check_reports_a_missing_engine(nested: Path) -> None:
    """Движок задаётся явно и не ищется в PATH; «не найден» обязано быть видно
    до сборки графа, а не на ней."""
    config = nested / "docs/ml/cashflow-docpipe/docpipe.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + 'graph:\n  engine_path: "/нет/такого"\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["config", "check", "--config", str(config), "--root", "."],
    )

    assert "НЕ НАЙДЕН" in result.output
    assert "/нет/такого" in result.output


def test_config_check_refuses_an_unreadable_configuration(tmp_path: Path) -> None:
    """Конфигурация, которая не читается, хуже отсутствующей: она выглядит
    настроенной. Код 2 — отказ инструмента, а не находка проверки."""
    broken = tmp_path / "docpipe.yaml"
    broken.write_text("rootz: [1]\n", encoding="utf-8")

    result = runner.invoke(app, ["config", "check", "--config", str(broken)])

    assert result.exit_code == 2
    assert "не читается" in result.output
