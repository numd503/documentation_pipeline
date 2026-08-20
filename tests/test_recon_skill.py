"""Скилл разведки (R02).

Скилл — это инструкция, и тестировать в нём можно ровно то, что проверяемо:
что он существует, что не зовёт человека делать запрещённое и что пример
в нём — не выдумка, а файл, который действительно проходит валидацию.

Последнее и есть главный тест. Пример в инструкции, который не грузится,
хуже отсутствия примера: агент воспроизведёт его буквально и получит отказ
на первом же прогоне, а решит, что сломан инструмент.
"""

import re
from pathlib import Path

import yaml

from docpipe.arch import check_document

SKILL = Path(".claude/skills/recon/SKILL.md")


def frontmatter_and_body() -> tuple[dict, str]:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "у скилла нет front matter"
    _, header, body = text.split("---\n", 2)
    return yaml.safe_load(header), body


def test_skill_exists_with_name_and_description() -> None:
    header, _ = frontmatter_and_body()
    assert header["name"] == "recon"
    # Описание — то, по чему скилл выбирают. Без условий применения его
    # не позовут там, где он нужен, и позовут там, где не нужен.
    assert len(header["description"]) > 200
    assert "реестр" in header["description"]


def test_skill_forbids_writing_into_the_registry() -> None:
    """Р10 в инструкции: скилл не пишет в `arch-registry.yaml`.

    Проверка текстовая и потому слабая — но её отсутствие означало бы, что
    границу держит только память. Настоящая проверка живёт в загрузчике
    (`skill_proposed` в реестре отвергается), и она рядом, в тестах формата.
    """
    _, body = frontmatter_and_body()
    assert "не пишешь в `arch-registry.yaml`" in body
    assert "arch-registry.draft.yaml" in body
    assert "--draft" in body


def test_skill_requires_evidence_and_forbids_empty_answers() -> None:
    _, body = frontmatter_and_body()
    assert "skill_proposed" in body
    assert "note" in body
    # Отсутствие находки произносится вслух и со списком того, что смотрели.
    assert "успешный исход" in body


def test_skill_example_draft_actually_validates() -> None:
    """Пример черновика из инструкции грузится в режиме черновика и
    отвергается в боевом режиме.

    Оба утверждения проверяются на одном и том же тексте: он копируется
    агентом буквально, и если он не грузится — виноват не агент.
    """
    _, body = frontmatter_and_body()
    blocks = re.findall(r"```yaml\n(.*?)```", body, re.DOTALL)
    assert blocks, "в скилле нет примера черновика"
    document = yaml.safe_load(blocks[0])

    draft, draft_problems = check_document(document, draft=True)
    assert draft is not None, draft_problems
    assert draft.records[0].provenance == "skill_proposed"
    assert draft.records[0].note.strip(), "пример без обоснования учит плохому"

    registry, problems = check_document(document)
    assert registry is None
    assert any("skill_proposed" in problem.message for problem in problems)


def test_skill_points_at_the_recon_script_and_the_format_reference() -> None:
    """Скилл зовёт скрипты R01 и ссылается на справочник формата.

    Инструкция, повторяющая формат своими словами, разойдётся со справочником
    на первой же правке — и разойдётся молча.
    """
    _, body = frontmatter_and_body()
    assert "tools/recon.py" in body
    assert "docs/arch-registry.md" in body
    assert Path("tools/recon.py").is_file()
    assert Path("docs/arch-registry.md").is_file()
