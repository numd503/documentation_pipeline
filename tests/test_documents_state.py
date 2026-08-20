"""Общий слой документа: зоны, состояние, запись (G00, предмет G17 п. 5).

До выделения разбор зон и правила приёмки лежали внутри `materialize`,
и бизнес-слой зависел от шага 2 как от модуля. Третий потребитель — детекция
дрейфа спецификаций — добавил бы третью копию тех же правил.

Признак того, что выделение сделано верно, один: обе действующие приёмки
зовут одну функцию. Здесь это проверяется, а не декларируется.
"""

from pathlib import Path

from docpipe.documents import (
    STATE_KEY,
    ParsedDocument,
    accepted_block,
    assemble,
    parse_document,
    read_accepted,
    read_review,
    write_atomic,
)

OWNER = Path("docpipe/documents")


def test_state_key_lives_in_one_place() -> None:
    """Ключ front matter с состоянием не повторяется по слоям.

    Разные ключи означали бы, что слой не видит приёмку соседа и молча
    её перезаписывает; одинаковые, но записанные в трёх местах, разъедутся
    на первом переименовании.
    """
    offenders = []
    for path in Path("docpipe").rglob("*.py"):
        if OWNER in path.parents:
            continue
        if f'"{STATE_KEY}"' in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == [], f"ключ состояния записан мимо общего слоя: {offenders}"


def test_both_acceptances_call_one_function() -> None:
    """Форма блока состояния собирается одной функцией, а не литералом.

    Литерал `{"accepted": …, "review": None}` в слое — это вторая реализация
    правила «приёмка снимает отметку о пересмотре», и разойдётся она молча.
    """
    offenders = []
    for path in Path("docpipe").rglob("*.py"):
        if OWNER in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if '"review": None' in text or "'review': None" in text:
            offenders.append(str(path))
    assert offenders == [], f"форма блока приёмки собрана мимо `accepted_block`: {offenders}"


def test_acceptance_clears_the_review_mark() -> None:
    """Отметка о пересмотре означает «человек ещё не смотрел».

    После приёмки она ложна; слой, забывший её снять, оставит документ
    с признаком пересмотра навсегда.
    """
    block = accepted_block({"business_hash": "sha256:x"})
    assert block["review"] is None
    assert block["accepted"] == {"business_hash": "sha256:x"}


def test_acceptance_is_idempotent() -> None:
    """Времени в блоке нет: с ним два прогона подряд давали бы дифф
    на пустом месте, а история правок точнее хранится в git."""
    payload = {"signature_hash": "sha256:y"}
    assert accepted_block(payload) == accepted_block(payload)
    assert set(accepted_block(payload)) == {"accepted", "review"}


def test_absent_acceptance_is_not_an_empty_one() -> None:
    """`None` — это статус «не сверяли», а не пустое состояние.

    Разница между «сверяли и приняли» и «не сверяли» и есть то, ради чего
    блок хранится вообще.
    """
    assert read_accepted(ParsedDocument()) is None
    assert read_accepted(ParsedDocument(front_matter={STATE_KEY: {"accepted": None}})) is None
    assert read_accepted(ParsedDocument(front_matter={STATE_KEY: {"accepted": {}}})) == {}


def test_review_mark_is_read_by_the_same_layer() -> None:
    parsed = ParsedDocument(front_matter={STATE_KEY: {"review": {"reason": "relocated"}}})
    assert read_review(parsed) == {"reason": "relocated"}
    assert read_review(ParsedDocument()) is None


def test_zones_survive_the_move(tmp_path: Path) -> None:
    """Инвариант обратимости от переезда не зависит: `assemble(parse(t)) == t`."""
    text = (
        "---\n"
        "docpipe:\n"
        "  schema: business/1\n"
        f"{STATE_KEY}:\n"
        "  accepted:\n"
        "    business_hash: sha256:z\n"
        "---\n"
        "\n"
        "<!-- docpipe:section:start summary -->\n"
        "Текст автора.\n"
        "<!-- docpipe:section:end summary -->\n"
    )
    parsed = parse_document(text)
    assert assemble(parsed) == text
    assert read_accepted(parsed) == {"business_hash": "sha256:z"}

    target = tmp_path / "doc.md"
    write_atomic(target, text)
    assert target.read_text(encoding="utf-8") == text
    assert not list(tmp_path.glob(".*.tmp"))
