"""Каталог бизнес-документов: формат, загрузка, проверки (B04).

Каталог — множество файлов, а не сгенерированный манифест: аналитик работает
в IDE. Отсюда два требования, которые здесь и проверяются: инвариант
обратимости шага 2 держится и на бизнес-документах, а чужие файлы в дереве
каталогом не считаются.
"""

from pathlib import Path

import pytest

from docpipe.business import ANCHOR_KINDS, Catalog, doc_path_for, load_catalog
from docpipe.materialize import assemble, is_section_empty, parse_document, read_document

ROOT = Path("tests/fixtures")
BUSINESS = "business"


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog(ROOT, BUSINESS)


# --------------------------------------------------------------------------------------
# Загрузка фикстурного каталога
# --------------------------------------------------------------------------------------


def test_catalog_loads_without_errors(catalog: Catalog) -> None:
    assert catalog.errors == []
    assert [doc.id for doc in catalog.docs] == [
        "be.valuation.user-task",
        "bp.valuation.limits-load",
        "bp.valuation.twinml-scoring",
    ]
    assert [c.id for c in catalog.capabilities] == ["cap.valuation", "cap.valuation.eod"]


def test_anchors_are_read(catalog: Catalog) -> None:
    doc = catalog.by_id()["bp.valuation.twinml-scoring"]

    assert [(a.kind, a.display) for a in doc.entry] == [
        ("list_event", "UserTasks/ItemAdded"),
        ("workflow", "SampleWorkflow@2"),
    ]
    assert doc.contracts[0].direction == "state"
    assert doc.owner_team == "ML"


def test_upstream_is_declared_but_not_verified(catalog: Catalog) -> None:
    """Граница зоны ответственности: процесс начинается у другой команды.

    Требовать доказательства чужого триггера — значит получить вечно красный
    линт и его отключение.
    """
    doc = catalog.by_id()["bp.valuation.twinml-scoring"]
    kafka = doc.upstream[0]

    assert kafka.verify is False
    assert kafka.owner == "команда интеграции"
    assert all(anchor.verify for anchor in doc.entry)


def test_external_ref_allows_catalog_before_migration(catalog: Catalog) -> None:
    """Каталог заводится целиком до того, как проза перенесена из Confluence,
    и миграция становится измеримой величиной."""
    doc = catalog.by_id()["bp.valuation.limits-load"]

    assert doc.external_ref is not None
    assert doc.status == "draft"


def test_job_anchor_with_colon_survives_yaml(catalog: Catalog) -> None:
    """`JOBTITLE` содержит двоеточие и пробелы — как раз то, из-за чего якорь
    хранится объектом, а не склеенной строкой."""
    doc = catalog.by_id()["bp.valuation.limits-load"]

    assert doc.entry[0].ref == "PM: Load limits"


# --------------------------------------------------------------------------------------
# Границы: что каталогом не считается
# --------------------------------------------------------------------------------------


def test_foreign_markdown_is_ignored(catalog: Catalog) -> None:
    """README без front matter не должен становиться битым документом."""
    assert all("README" not in doc.doc_path for doc in catalog.docs)
    assert not any("README" in error for error in catalog.errors)


def test_step_two_document_is_ignored(catalog: Catalog) -> None:
    """Отбор по `schema`, а не по наличию ключа `docpipe`.

    Формат зон у слоёв общий, и отбор по ключу перепутал бы их в обе стороны:
    бизнес-каталог стал бы сиротами шага 2, а документы шага 2 — битыми
    бизнес-документами.
    """
    assert all("not-ours" not in doc.doc_path for doc in catalog.docs)
    assert not any("not-ours" in error for error in catalog.errors)


# --------------------------------------------------------------------------------------
# Инвариант обратимости держится и здесь
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "processes/valuation/twinml-scoring.md",
        "processes/valuation/limits-load.md",
        "entities/valuation/user-task.md",
    ],
)
def test_round_trip(name: str) -> None:
    text, doc = read_document(ROOT / BUSINESS / name)

    assert assemble(doc) == text


def test_draft_sections_are_empty() -> None:
    """Подсказки — только HTML-комментарии. Обычный текст сделал бы каждый
    новый документ «уже написанным»."""
    _, doc = read_document(ROOT / BUSINESS / "processes/valuation/limits-load.md")

    assert doc.section_names == ["purpose", "trigger", "steps"]
    assert all(is_section_empty(doc.section(name).body) for name in doc.section_names)  # type: ignore[union-attr]


def test_filled_sections_are_not_empty() -> None:
    _, doc = read_document(ROOT / BUSINESS / "processes/valuation/twinml-scoring.md")
    filled = [n for n in doc.section_names if n != "notes"]

    assert filled
    assert not any(is_section_empty(doc.section(name).body) for name in filled)  # type: ignore[union-attr]


# --------------------------------------------------------------------------------------
# Путь выводится из идентификатора
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc_id", "expected"),
    [
        ("bp.valuation.twinml-scoring", "business/processes/valuation/twinml-scoring.md"),
        ("be.valuation.user-task", "business/entities/valuation/user-task.md"),
        ("cap.valuation", "business/capabilities/valuation.md"),
        ("bp.risk.stress.daily", "business/processes/risk/stress/daily.md"),
    ],
)
def test_doc_path_for(doc_id: str, expected: str) -> None:
    assert doc_path_for(doc_id, BUSINESS) == expected


def test_every_document_lies_where_its_id_says(catalog: Catalog) -> None:
    for doc in catalog.docs:
        assert doc.doc_path == doc_path_for(doc.id, BUSINESS)


# --------------------------------------------------------------------------------------
# Условия отказа
# --------------------------------------------------------------------------------------


def _write(tmp_path: Path, rel: str, front_matter: str) -> Path:
    path = tmp_path / BUSINESS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front_matter}---\n\n# Заголовок\n", encoding="utf-8")
    return path


GOOD = """docpipe:
  schema: business/1
  id: bp.a.b
  kind: process
  title: Тест
"""


def test_valid_minimal_document(tmp_path: Path) -> None:
    _write(tmp_path, "processes/a/b.md", GOOD)
    result = load_catalog(tmp_path, BUSINESS)

    assert result.errors == []
    assert [doc.id for doc in result.docs] == ["bp.a.b"]


@pytest.mark.parametrize(
    ("rel", "front_matter", "expected"),
    [
        (
            "processes/a/b.md",
            GOOD.replace("id: bp.a.b", "id: bp.A.B"),
            "не по образцу",
        ),
        (
            "processes/a/b.md",
            GOOD.replace("kind: process", "kind: entity"),
            "означает вид",
        ),
        (
            "processes/a/other.md",
            GOOD,
            "не совпадает с выведенным",
        ),
        (
            "processes/a/b.md",
            GOOD + "  entry:\n  - kind: telepathy\n    ref: x\n",
            "неизвестный вид якоря",
        ),
        (
            "processes/a/b.md",
            GOOD.replace("kind: process", "kind: неведомое"),
            "kind",
        ),
        (
            "processes/a/b.md",
            GOOD + "  лишний: ключ\n",
            "Extra inputs",
        ),
    ],
)
def test_rejects(tmp_path: Path, rel: str, front_matter: str, expected: str) -> None:
    _write(tmp_path, rel, front_matter)
    result = load_catalog(tmp_path, BUSINESS)

    assert result.docs == []
    assert any(expected in error for error in result.errors), result.errors


def test_dangling_capability_is_reported_but_document_survives(tmp_path: Path) -> None:
    """Документ структурно исправен, не разрешается только ссылка.

    Выбросить его значило бы спрятать целый процесс из всех отчётов из-за
    опечатки в одном поле — а чинить будут то, что видно.
    """
    _write(tmp_path, "processes/a/b.md", GOOD + "  capability: cap.nope\n")
    result = load_catalog(tmp_path, BUSINESS)

    assert [doc.id for doc in result.docs] == ["bp.a.b"]
    assert any("не объявлена" in error for error in result.errors)


def test_duplicate_id(tmp_path: Path) -> None:
    _write(tmp_path, "processes/a/b.md", GOOD)
    path = tmp_path / BUSINESS / "processes/a/b2.md"
    path.write_text(f"---\n{GOOD}---\n", encoding="utf-8")

    result = load_catalog(tmp_path, BUSINESS)

    assert any("повтор идентификатора" in e or "не совпадает" in e for e in result.errors)


def test_broken_document_is_reported_not_fatal(tmp_path: Path) -> None:
    _write(tmp_path, "processes/a/b.md", GOOD)
    broken = tmp_path / BUSINESS / "processes/a/broken.md"
    broken.write_text("---\ndocpipe:\n  schema: business/1\n", encoding="utf-8")

    result = load_catalog(tmp_path, BUSINESS)

    assert [doc.id for doc in result.docs] == ["bp.a.b"]
    assert any("не закрыт" in error for error in result.errors)


def test_missing_catalog_is_empty_not_broken(tmp_path: Path) -> None:
    result = load_catalog(tmp_path, BUSINESS)

    assert result == Catalog()


def test_capability_with_unknown_parent(tmp_path: Path) -> None:
    path = tmp_path / BUSINESS / "capabilities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "capabilities:\n  - id: cap.a\n    title: A\n    parent: cap.nope\n", encoding="utf-8"
    )

    result = load_catalog(tmp_path, BUSINESS)

    assert any("необъявленного родителя" in error for error in result.errors)


def test_anchor_kinds_are_closed() -> None:
    """Перечень видов якорей закрыт намеренно: опечатка иначе означает якорь,
    который просто никогда не разрешится."""
    assert "list_event" in ANCHOR_KINDS
    assert "list" not in ANCHOR_KINDS


def test_catalog_is_deterministic() -> None:
    first = load_catalog(ROOT, BUSINESS)
    second = load_catalog(ROOT, BUSINESS)

    assert first == second


def test_business_document_parses_as_step_two_zones() -> None:
    """Формат зон общий: разбор один на оба слоя, дублировать его нельзя."""
    text = (ROOT / BUSINESS / "processes/valuation/twinml-scoring.md").read_text(encoding="utf-8")
    doc = parse_document(text)

    assert doc.generated is not None
    assert "purpose" in doc.section_names
