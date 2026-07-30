"""Скелеты, `business new` и `business build` (B07).

Запись, сравнение и атомарность взяты из шага 2 без изменений, поэтому здесь
проверяется не они, а то, что бизнес-слой их действительно переиспользует:
инвариант обратимости держится, авторский текст переживает пересборку,
а прогон без изменений не открывает файл на запись.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.business import load_catalog
from docpipe.cli import app
from docpipe.materialize import assemble, is_section_empty, parse_document
from tests.business_support import combined_tree, manifest

runner = CliRunner()
BUSINESS = "business"
TEMPLATES = Path("templates/business")
KINDS = ("process", "entity", "capability")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = combined_tree(tmp_path)
    (root / "doc-tree.json").write_text(manifest().model_dump_json(), encoding="utf-8")
    return root


def build(tree: Path, *args: str) -> object:
    return runner.invoke(
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
            BUSINESS,
            *args,
        ],
    )


# --------------------------------------------------------------------------------------
# Скелеты
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_skeleton_sections_are_all_empty(kind: str) -> None:
    """Подсказки — только HTML-комментарии.

    Обычный текст-подсказка сделал бы каждый новый документ «уже написанным»,
    и `business status` перестал бы отличать написанное от созданного.
    """
    parsed = parse_document((TEMPLATES / f"{kind}.md").read_text(encoding="utf-8"))

    assert parsed.section_names
    for name in parsed.section_names:
        segment = parsed.section(name)
        assert segment is not None
        assert is_section_empty(segment.body), f"{kind}: секция {name} не пуста"


@pytest.mark.parametrize("kind", KINDS)
def test_skeleton_is_reversible(kind: str) -> None:
    text = (TEMPLATES / f"{kind}.md").read_text(encoding="utf-8")

    assert assemble(parse_document(text)) == text


@pytest.mark.parametrize("kind", KINDS)
def test_skeleton_has_exactly_one_generated_block(kind: str) -> None:
    parsed = parse_document((TEMPLATES / f"{kind}.md").read_text(encoding="utf-8"))

    assert sum(segment.kind == "generated" for segment in parsed.segments) == 1


def test_skeletons_have_no_handwritten_cross_links() -> None:
    """Все кросс-ссылки живут только в генерируемом блоке: поставленная руками
    не починится при переносе технического документа."""
    for kind in KINDS:
        text = (TEMPLATES / f"{kind}.md").read_text(encoding="utf-8")
        assert "](" not in text, f"{kind}: в скелете есть ссылка"


def test_example_is_parsed_and_every_section_is_filled() -> None:
    """Образец показывает агенту глубину и стиль. Пустая секция в нём означала бы
    «так тоже можно»."""
    text = (TEMPLATES / "examples" / "process.md").read_text(encoding="utf-8")
    parsed = parse_document(text)

    assert assemble(parsed) == text
    assert parsed.schema_id == "business/1"
    for name in parsed.section_names:
        segment = parsed.section(name)
        assert segment is not None
        assert not is_section_empty(segment.body), f"секция {name} пуста"


def test_examples_are_not_skeletons() -> None:
    """Обход каталога скелетов не рекурсивный: `examples/` — заполненные
    образцы, и вид документа `examples` не существует."""
    assert sorted(path.stem for path in TEMPLATES.glob("*.md") if path.stem != "README") == sorted(
        KINDS
    )


# --------------------------------------------------------------------------------------
# `business new`
# --------------------------------------------------------------------------------------


def test_new_creates_document_at_derived_path(tree: Path) -> None:
    """Вид берётся из префикса идентификатора и параметром не задаётся:
    второй источник истины про вид увёл бы документ не в тот каталог."""
    result = runner.invoke(
        app,
        [
            "business",
            "new",
            "bp.pricing.eod-revaluation",
            "--title",
            "Переоценка",
            "--root",
            str(tree),
            "--business-root",
            BUSINESS,
        ],
    )
    path = tree / BUSINESS / "processes" / "pricing" / "eod-revaluation.md"

    assert result.exit_code == 0
    assert path.is_file()
    assert "# Переоценка" in path.read_text(encoding="utf-8")


def test_new_document_is_loadable_by_the_catalog(tree: Path) -> None:
    """Созданный документ обязан читаться каталогом сразу: скелет, который
    не проходит собственную загрузку, — самый дорогой вид опечатки."""
    runner.invoke(
        app,
        [
            "business",
            "new",
            "be.pricing.position",
            "--title",
            "Позиция",
            "--root",
            str(tree),
            "--business-root",
            BUSINESS,
        ],
    )
    catalog = load_catalog(tree, BUSINESS)

    assert "be.pricing.position" in catalog.by_id()
    assert catalog.errors == []


def test_new_refuses_to_overwrite(tree: Path) -> None:
    """Повторный вызов — отказ: документ уже могли наполнить, и «создать
    заново» здесь означало бы стереть чужую работу."""
    args = [
        "business",
        "new",
        "bp.pricing.eod-revaluation",
        "--title",
        "Переоценка",
        "--root",
        str(tree),
        "--business-root",
        BUSINESS,
    ]
    runner.invoke(app, args)
    path = tree / BUSINESS / "processes" / "pricing" / "eod-revaluation.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nАвторский текст.\n", encoding="utf-8")

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "Авторский текст." in path.read_text(encoding="utf-8")


def test_new_rejects_a_bad_identifier(tree: Path) -> None:
    result = runner.invoke(
        app,
        [
            "business",
            "new",
            "BP.Pricing.Eod",
            "--title",
            "Переоценка",
            "--root",
            str(tree),
            "--business-root",
            BUSINESS,
        ],
    )

    assert result.exit_code == 2
    assert "не по образцу" in result.output


# --------------------------------------------------------------------------------------
# `business build`
# --------------------------------------------------------------------------------------


def test_build_is_idempotent(tree: Path) -> None:
    """Второй прогон подряд не меняет ни байта и не открывает файл на запись.

    Иначе на репозитории с `core.autocrlf=true` каждый документ оказывался бы
    изменённым при каждом прогоне, и дифф перестал бы что-либо означать.
    """
    assert build(tree).exit_code == 0

    path = tree / BUSINESS / "processes" / "valuation" / "twinml-scoring.md"
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns

    result = build(tree)

    assert result.exit_code == 0
    assert "без изменений: 3" in result.stdout
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp


def test_build_preserves_authored_text(tree: Path) -> None:
    """Текст, дописанный в секцию, переживает пересборку после правки реестра."""
    build(tree)
    path = tree / BUSINESS / "processes" / "valuation" / "twinml-scoring.md"
    text = path.read_text(encoding="utf-8").replace(
        "Сбор идентификаторов", "Сбор идентификаторов, затем добавленный вручную шаг"
    )
    path.write_text(text, encoding="utf-8")

    definition = tree / "deployment" / "Data" / "Items" / "Workflows" / "Sample.v2.json"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace(
            '"NextStepId": "ScoreStep"', '"NextStepId": "CollectStep"'
        ),
        encoding="utf-8",
    )
    build(tree)

    assert "затем добавленный вручную шаг" in path.read_text(encoding="utf-8")


def test_build_preserves_foreign_front_matter_keys(tree: Path) -> None:
    """Чужие ключи front matter принадлежат человеку и не затираются."""
    path = tree / BUSINESS / "processes" / "valuation" / "limits-load.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "docpipe_state:", "confluence: PAGE-42\nreviewers:\n- ivanov\ndocpipe_state:", 1
        ),
        encoding="utf-8",
    )
    build(tree)
    text = path.read_text(encoding="utf-8")

    assert "confluence: PAGE-42" in text
    assert "ivanov" in text


def test_build_keeps_the_document_reversible(tree: Path) -> None:
    """Инвариант шага 2 обязан держаться и на бизнес-документах."""
    build(tree)

    for path in sorted((tree / BUSINESS).rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            assert assemble(parse_document(text)) == text, path


def test_build_does_not_create_documents(tree: Path) -> None:
    """Бизнес-документы создаются людьми, а не выводятся из кода: `build`
    порождать файлы не должен, иначе каталог начал бы расти сам."""
    before = {path.relative_to(tree) for path in (tree / BUSINESS).rglob("*.md")}
    build(tree)
    after = {path.relative_to(tree) for path in (tree / BUSINESS).rglob("*.md")}

    assert before == after


def test_build_dry_run_writes_nothing(tree: Path) -> None:
    path = tree / BUSINESS / "processes" / "valuation" / "twinml-scoring.md"
    before = path.read_bytes()
    result = build(tree, "--dry-run")

    assert result.exit_code == 0
    assert "Было бы обновлено: 3" in result.stdout
    assert path.read_bytes() == before


def test_new_finds_skeletons_through_the_templates_key(tree: Path, tmp_path: Path) -> None:
    """Каталог скелетов выводится из ключа `templates`, а не задан константой.

    В установке инструмент лежит не в корне сканируемого репозитория, и
    `templates/business` относительно текущего каталога указывало бы в никуда:
    ровно та ловушка, из-за которой путь к шаблонам шага 2 обязателен
    в конфигурации.
    """
    shutil.copytree(TEMPLATES, tree / "tools" / "skeletons" / "business")
    (tree / "docpipe.yaml").write_text(
        f"templates: {tree / 'tools' / 'skeletons'}\nbusiness_root: {BUSINESS}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "business",
            "new",
            "bp.pricing.eod",
            "--title",
            "Переоценка",
            "--root",
            str(tree),
            "--config",
            str(tree / "docpipe.yaml"),
        ],
    )

    assert result.exit_code == 0
    assert (tree / BUSINESS / "processes" / "pricing" / "eod.md").is_file()


def test_new_names_the_templates_key_when_the_skeleton_is_missing(tree: Path) -> None:
    """Сообщение обязано называть причину: «файл не найден» без неё выглядит
    как дефект инструмента, хотя виновата конфигурация или поставка."""
    result = runner.invoke(
        app,
        [
            "business",
            "new",
            "bp.pricing.eod",
            "--title",
            "Переоценка",
            "--root",
            str(tree),
            "--templates",
            str(tree / "нет-такого"),
        ],
    )

    assert result.exit_code == 2
    assert "--templates" in result.output
