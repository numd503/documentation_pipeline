"""Обратимый разбор документа (M03).

Главный тест здесь один: `assemble(parse_document(t)) == t` байт в байт.
Всё остальное — условия отказа и `is_section_empty`, ошибка в которой заставила
бы агента счесть непрописанное дерево написанным.
"""

from pathlib import Path

import pytest

from docpipe.materialize import (
    DocumentError,
    assemble,
    is_section_empty,
    parse_document,
    read_document,
)

FULL = """---
docpipe:
  schema: materialize/1
  node_id: type:src/A/A.csproj#A.B.C`0
docpipe_state:
  accepted: null
---

# Заголовок

<!-- docpipe:generated:start -->
Собрано инструментом.
<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
Человеческий текст.
<!-- docpipe:section:end purpose -->
"""

REVERSIBLE = [
    pytest.param(FULL, id="полный документ"),
    pytest.param("", id="пустой"),
    pytest.param("Просто текст без ничего.\n", id="без front matter"),
    pytest.param("# Заголовок", id="без завершающего перевода строки"),
    pytest.param("# Заголовок\n\n\n", id="три завершающих перевода строки"),
    pytest.param(FULL.replace("\n", "\r\n"), id="CRLF"),
    pytest.param("---\n---\nтело\n", id="пустой front matter"),
    pytest.param("---\nключ: значение\n---\n", id="только front matter"),
    pytest.param(
        "<!-- docpipe:section:start purpose -->\n<!-- docpipe:section:end purpose -->\n",
        id="пустая секция без тела",
    ),
    pytest.param(
        "<!--docpipe:section:start purpose-->\nтекст\n<!--  docpipe:section:end   purpose  -->\n",
        id="маркеры с непривычными пробелами",
    ),
    pytest.param("Кириллица, эмодзи 🙂, табы\t.\n", id="юникод"),
    pytest.param("---\nzeta: 1\nalpha: 2\nчужой: ключ\n---\nтело\n", id="чужие ключи"),
    pytest.param(
        "<!-- docpipe:section:start notes -->\n"
        "```\n"
        "    <!-- docpipe:section:start fake -->\n"
        "```\n"
        "<!-- docpipe:section:end notes -->\n",
        id="блок кода с маркером под отступом",
    ),
    pytest.param(
        "<!-- docpipe:section:start notes -->\n"
        "    <!-- docpipe:generated:start -->\n"
        "<!-- docpipe:section:end notes -->\n",
        id="маркер с отступом в четыре пробела",
    ),
]

INDENTED_IN_FENCE = REVERSIBLE[-2].values[0]
INDENTED_MARKER = REVERSIBLE[-1].values[0]


@pytest.mark.parametrize("text", REVERSIBLE)
def test_round_trip(text: str) -> None:
    assert assemble(parse_document(text)) == text


def test_bare_marker_inside_code_fence_is_still_a_marker() -> None:
    """Осознанное ограничение, а не дефект: разбор построчный и о ```-блоках
    не знает. Документация про сам docpipe обязана давать маркерам отступ —
    иначе документ окажется структурно испорченным.
    """
    text = (
        "<!-- docpipe:section:start notes -->\n"
        "```\n"
        "<!-- docpipe:section:start fake -->\n"
        "```\n"
        "<!-- docpipe:section:end notes -->\n"
    )

    with pytest.raises(DocumentError, match="внутри другой секции"):
        parse_document(text)


def test_indented_marker_is_literal_text() -> None:
    """Обходной путь: отступ в четыре пробела. Работает и внутри блока кода."""
    doc = parse_document(INDENTED_MARKER)
    fenced = parse_document(INDENTED_IN_FENCE)

    assert doc.section_names == ["notes"]
    assert doc.generated is None
    assert fenced.section_names == ["notes"]


# --------------------------------------------------------------------------------------
# Зоны
# --------------------------------------------------------------------------------------


def test_zones_are_separated() -> None:
    doc = parse_document(FULL)
    generated = doc.generated
    purpose = doc.section("purpose")

    assert generated is not None and generated.body == "Собрано инструментом.\n"
    assert purpose is not None and purpose.body == "Человеческий текст.\n"
    assert doc.schema_id == "materialize/1"
    assert doc.docpipe is not None and doc.docpipe["node_id"].endswith("A.B.C`0")
    assert doc.state == {"accepted": None}


def test_document_without_front_matter_is_not_broken() -> None:
    """Чужой markdown — не ошибка: он просто не документ docpipe."""
    doc = parse_document("# Просто файл\n")

    assert doc.front_matter is None
    assert doc.docpipe is None
    assert doc.schema_id is None


def test_schema_separates_layers() -> None:
    """По `schema` слои отличают свои документы от чужих.

    Отбор по одному лишь наличию `docpipe` объявил бы весь бизнес-каталог
    сиротами шага 2 — а обнаружилось бы это, когда каталог уже заведут.
    """
    business = parse_document("---\ndocpipe:\n  schema: business/1\n  id: bp.a.b\n---\n")

    assert business.schema_id == "business/1"


# --------------------------------------------------------------------------------------
# Условия отказа
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---\nключ: 1\nтело\n", "не закрыт"),
        ("---\n\tкривой:\t- yaml\n  - [\n---\n", "не разбирается как YAML"),
        ("---\n- список\n---\n", "должен быть словарём"),
        ("---\ndocpipe_state: строка\n---\n", "`docpipe_state` должен быть словарём"),
        ("---\ndocpipe: строка\n---\n", "`docpipe` должен быть словарём"),
        ("<!-- docpipe:generated:end -->\n", "без `generated:start`"),
        ("<!-- docpipe:generated:start -->\n", "не закрыт"),
        (
            "<!-- docpipe:generated:start -->\n<!-- docpipe:generated:end -->\n"
            "<!-- docpipe:generated:start -->\n<!-- docpipe:generated:end -->\n",
            "не один",
        ),
        ("<!-- docpipe:section:end purpose -->\n", "без открытия"),
        ("<!-- docpipe:section:start purpose -->\n", "не закрыта"),
        (
            "<!-- docpipe:section:start purpose -->\n<!-- docpipe:section:end notes -->\n",
            "открыта была",
        ),
        (
            "<!-- docpipe:section:start a -->\n<!-- docpipe:section:end a -->\n"
            "<!-- docpipe:section:start a -->\n<!-- docpipe:section:end a -->\n",
            "повтор секции",
        ),
        (
            "<!-- docpipe:section:start a -->\n<!-- docpipe:section:start b -->\n",
            "внутри другой секции",
        ),
        (
            "<!-- docpipe:section:start a -->\n<!-- docpipe:generated:start -->\n",
            "внутри секции",
        ),
        (
            "<!-- docpipe:generated:start -->\n<!-- docpipe:section:start a -->\n",
            "внутри генерируемого блока",
        ),
    ],
)
def test_rejects(text: str, expected: str) -> None:
    with pytest.raises(DocumentError, match=expected):
        parse_document(text)


def test_error_carries_line_number() -> None:
    text = "первая\nвторая\n<!-- docpipe:section:end purpose -->\n"

    with pytest.raises(DocumentError, match="строка 3"):
        parse_document(text)


# --------------------------------------------------------------------------------------
# Пустота секции
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "",
        "\n\n",
        "<!-- подсказка -->\n",
        "<!-- одна -->\n\n<!-- вторая -->\n\n<!-- третья -->\n",
        "<!-- многострочная\n     подсказка -->\n",
    ],
)
def test_section_is_empty(body: str) -> None:
    assert is_section_empty(body)


@pytest.mark.parametrize("body", ["<!-- подсказка -->\nслово\n", "текст", "-"])
def test_section_is_not_empty(body: str) -> None:
    assert not is_section_empty(body)


# --------------------------------------------------------------------------------------
# Чтение с диска
# --------------------------------------------------------------------------------------


def test_bom_is_stripped(tmp_path: Path) -> None:
    """Без `utf-8-sig` документ, сохранённый Блокнотом, считается чужим,
    превращается в сироту, а рядом появляется новый пустой."""
    path = tmp_path / "doc.md"
    path.write_bytes(b"\xef\xbb\xbf" + FULL.encode("utf-8"))

    text, doc = read_document(path)

    assert text.startswith("---")
    assert doc.schema_id == "materialize/1"


def test_line_endings_are_normalised_for_comparison(tmp_path: Path) -> None:
    """Сравнение по нормализованному тексту, иначе на репозитории
    с `core.autocrlf=true` каждый документ будет вечно «изменённым»."""
    path = tmp_path / "doc.md"
    path.write_bytes(FULL.replace("\n", "\r\n").encode("utf-8"))

    text, doc = read_document(path)

    assert "\r" not in text
    assert assemble(doc) == text
