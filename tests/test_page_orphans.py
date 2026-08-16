"""Сироты после поглощения (P10).

Включение агрегата отбирает у поглощённых узлов их документы. Файлы при этом
остаются — с уже написанным текстом, и на боевом репозитории таких будут сотни.
Инструмент документы **не удаляет**: он называет их и говорит, куда переехало
содержание. Удаление — команда человека.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app

runner = CliRunner()


@pytest.fixture
def documents(web_workspace: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Дерево документов, созданное ДО поглощения.

    Манифест правится руками: `absorbed_by` снимается со всех узлов, документы
    материализуются, и только потом прогон повторяется с настоящим манифестом.
    Так воспроизводится ровно тот переход, который случится на боевом
    репозитории при обновлении инструмента.
    """
    import json

    manifest = tmp_path / "web.json"
    docs = tmp_path / "docs-root"
    docs.mkdir()

    scan = runner.invoke(app, ["web", "scan", "--root", str(web_workspace), "--out", str(manifest)])
    assert scan.exit_code == 0, scan.output

    before = tmp_path / "before.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    for node in raw["nodes"]:
        node["absorbed_by"] = ""
    before.write_text(json.dumps(raw), encoding="utf-8")

    assert runner.invoke(app, ["materialize", str(before), "--root", str(docs)]).exit_code == 0
    return manifest, docs


def test_absorbed_document_becomes_an_orphan_that_names_its_page(
    documents: tuple[Path, Path],
) -> None:
    manifest, docs = documents
    status = runner.invoke(app, ["docs", "status", str(manifest), "--root", str(docs)])

    assert status.exit_code == 0, status.output
    assert "orphan" in status.output
    assert "описывается внутри страницы" in status.output


def test_orphan_file_is_not_touched(documents: tuple[Path, Path]) -> None:
    """Молчаливое удаление запрещено: в файле уже написанный человеком текст."""
    manifest, docs = documents
    path = docs / "docs/modules/api-services/tr-p/items-service.md"
    before = path.read_text(encoding="utf-8")

    assert runner.invoke(app, ["materialize", str(manifest), "--root", str(docs)]).exit_code == 0

    assert path.exists()
    assert path.read_text(encoding="utf-8") == before


def test_second_run_creates_no_new_orphans(documents: tuple[Path, Path]) -> None:
    """Идемпотентность: прогон не должен порождать работу сам себе."""
    manifest, docs = documents

    first = runner.invoke(app, ["materialize", str(manifest), "--root", str(docs)])
    second = runner.invoke(app, ["materialize", str(manifest), "--root", str(docs)])

    assert first.exit_code == 0 and second.exit_code == 0
    assert "orphan           5" in first.output
    assert "orphan           5" in second.output
    assert "создано:      0" in second.output


def test_adopt_refuses_and_says_where_the_text_belongs(documents: tuple[Path, Path]) -> None:
    """Слить авторский текст в раздел страницы автоматически нельзя.

    Место ему выбирает человек, а не совпадение имён, — но сказать, куда
    именно, инструмент обязан: иначе отказ выглядит как «непонятно что».
    """
    manifest, docs = documents

    result = runner.invoke(
        app,
        [
            "docs",
            "adopt",
            str(manifest),
            "--root",
            str(docs),
            "--from",
            "docs/modules/api-services/tr-p/items-service.md",
            "--to",
            "docs/modules/pages/tr-p/quiz-component.md",
        ],
    )

    assert result.exit_code == 1
    assert "описывается внутри страницы QuizComponent" in result.output
    assert "«Состояние»" in result.output
