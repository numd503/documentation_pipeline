"""Команда `docs explain`: трассировка одного документа.

Отчёт по дереву группирует по статусу и на три вопроса не отвечает: какой
фильтр обхода отбросил лежащий на диске файл, чем собранный текст отличается
от него и не задевает ли перезапись авторские секции. Здесь проверяются
ответы на все три.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.materialize.explain import format_explain_document, zone_diff

MANIFEST = Path("tests/golden/doc-tree.json")
CONTROLLER = "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"
runner = CliRunner()


def _materialize(root: Path, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["materialize", str(manifest), "--root", str(root)])


def _explain(root: Path, path: str, *extra: str, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["docs", "explain", str(manifest), path, "--root", str(root), *extra])


def _with_domain(tmp_path: Path, domain: str) -> Path:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        node["domain"] = domain
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return changed


# --------------------------------------------------------------------------------------
# Почему именно это действие
# --------------------------------------------------------------------------------------


def test_explains_why_update_and_not_create(tmp_path: Path) -> None:
    _materialize(tmp_path)
    changed = _with_domain(tmp_path, "Другой домен")

    result = _explain(tmp_path, CONTROLLER, manifest=changed)

    assert result.exit_code == 0
    assert "действие с файлом:  update" in result.stdout
    assert "файл есть, и собранный текст от него отличается" in result.stdout
    # Причина названа поимённо, а не «документ изменился».
    assert "docpipe.domain: Sample.Pricing.Api → Другой домен" in result.stdout
    assert "генерируемый блок: пересобран" in result.stdout
    assert "авторские секции:  не тронуты" in result.stdout


def test_explains_create_on_an_empty_tree(tmp_path: Path) -> None:
    result = _explain(tmp_path, CONTROLLER)

    assert result.exit_code == 0
    assert "действие с файлом:  create" in result.stdout
    assert "файл на диске:      отсутствует" in result.stdout


def test_diff_flag_shows_the_full_diff(tmp_path: Path) -> None:
    _materialize(tmp_path)
    changed = _with_domain(tmp_path, "Другой домен")

    result = _explain(tmp_path, CONTROLLER, "--diff", manifest=changed)

    assert "--- на диске" in result.stdout
    assert "+++ будет записано" in result.stdout
    assert "+  domain: Другой домен" in result.stdout


def test_accepts_a_path_relative_to_the_current_directory(tmp_path: Path) -> None:
    """Путь принимается и репо-относительный, и обычный: человек копирует его
    из отчёта или дополняет по Tab, и преобразовывать руками не должен."""
    _materialize(tmp_path)

    result = _explain(tmp_path, str(tmp_path / CONTROLLER))

    assert result.exit_code == 0
    assert result.stdout.startswith(CONTROLLER)


# --------------------------------------------------------------------------------------
# Файл есть, а обход его не вернул
# --------------------------------------------------------------------------------------


def test_names_the_filter_that_dropped_the_file(tmp_path: Path) -> None:
    _materialize(tmp_path)
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace("  schema: materialize/1\n", ""),
        encoding="utf-8",
    )

    result = _explain(tmp_path, CONTROLLER)

    assert result.exit_code == 0
    assert "действие с файлом:  refuse" in result.stdout
    assert "`docpipe.schema` = нет, а нужен префикс `materialize/`" in result.stdout


def test_unknown_path_is_reported_not_guessed(tmp_path: Path) -> None:
    _materialize(tmp_path)

    result = _explain(tmp_path, "docs/modules/controllers/Sample.Common/нет-такого.md")

    assert result.exit_code == 1
    assert "В плане такого документа нет" in result.stdout


# --------------------------------------------------------------------------------------
# Авторский текст
# --------------------------------------------------------------------------------------


def test_zone_diff_separates_generated_from_authored() -> None:
    before = (
        "---\ndocpipe:\n  schema: materialize/1\n---\n"
        "<!-- docpipe:generated:start -->\nстарое\n<!-- docpipe:generated:end -->\n"
        "<!-- docpipe:section:start purpose -->\nавторский текст\n"
        "<!-- docpipe:section:end purpose -->\n"
    )
    only_generated = before.replace("старое", "новое")
    touched = before.replace("авторский текст", "затёрли")

    assert zone_diff(before, only_generated).generated_changed
    assert not zone_diff(before, only_generated).touches_authored
    assert zone_diff(before, touched).touches_authored
    assert zone_diff(before, before).empty


def test_touched_authored_section_is_a_defect_with_exit_code(tmp_path: Path) -> None:
    """Затронутая авторская секция — нарушение главного инварианта шага 2,
    поэтому код возврата: такое ловят проверкой, а не глазами в выводе."""
    from docpipe.materialize.plan import PlannedDoc

    path = tmp_path / "docs" / "x.md"
    path.parent.mkdir(parents=True)
    before = (
        "---\ndocpipe:\n  schema: materialize/1\n---\n"
        "<!-- docpipe:section:start purpose -->\nавторский текст\n"
        "<!-- docpipe:section:end purpose -->\n"
    )
    path.write_text(before, encoding="utf-8")
    doc = PlannedDoc(
        doc_path="docs/x.md",
        node_id="type:x",
        file_action="update",
        status="current",
        agent_action="skip",
        content=before.replace("авторский текст", "затёрли"),
    )

    report = format_explain_document("docs/x.md", doc, tmp_path, [])

    assert "АВТОРСКИЕ СЕКЦИИ:  purpose" in report
    assert "Это дефект" in report
