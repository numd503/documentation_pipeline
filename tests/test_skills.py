"""Общие свойства всех скиллов агента контура (S29).

Скилл — инструкция для gigacode (форк qwen code), и проверять в нём общего
можно то, без чего агент его не найдёт или найдёт не тот: front matter,
имя, равное каталогу, описание — то, по чему скилл выбирают, — ссылка для
Claude Code и то, что файлы вообще доедут до клона. Содержательные проверки
живут у каждого скилла своим файлом (`test_recon_skill.py`,
`test_setup_skill.py`).

Проверки собраны здесь, а не повторены в каждом файле: второй скилл
скопировал бы их у первого, и правка одной копии не доехала бы до другой.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

# Проектные скиллы gigacode ищет в `.gigacode/skills/<имя>/SKILL.md`; Claude
# Code, которым ведётся разработка, видит те же каталоги через ссылки
# `.claude/skills/<имя>`.
SKILLS_DIR: Final = Path(".gigacode/skills")
CLAUDE_DIR: Final = Path(".claude/skills")
SKILLS: Final = sorted(path.parent for path in SKILLS_DIR.glob("*/SKILL.md"))

# Границы описания. Короче — по нему не понять, когда скилл звать; 1024 —
# предел описания в формате скиллов: описание каждого скилла лежит
# в контексте агента всегда, и длинное его обрежут или отвергнут.
DESCRIPTION_MIN: Final = 200
DESCRIPTION_MAX: Final = 1024


def split_skill(path: Path) -> tuple[dict[str, Any], str]:
    """Front matter и тело `SKILL.md`. Общий помощник тестов скиллов."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: нет front matter"
    _, header, body = text.split("---\n", 2)
    loaded = yaml.safe_load(header)
    assert isinstance(loaded, dict), f"{path}: front matter — не словарь"
    return loaded, body


def test_both_skills_are_found() -> None:
    """Обход нашёл скиллы: пустой список сделал бы все проверки ниже пустыми."""
    assert {skill.name for skill in SKILLS} >= {"recon", "setup"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.name)
def test_front_matter_names_the_directory_and_describes_when_to_call(skill: Path) -> None:
    """`name` равен каталогу, описание — от 200 до 1024 символов.

    Имя, разошедшееся с каталогом, агент покажет одним, а искать будет
    другим; описание без условий применения не даст его позвать там,
    где он нужен.
    """
    header, body = split_skill(skill / "SKILL.md")
    assert header.get("name") == skill.name
    description = header.get("description")
    assert isinstance(description, str)
    assert DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX, len(description)
    assert body.strip(), f"{skill}: пустое тело"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.name)
def test_claude_sees_the_same_skill_through_a_link(skill: Path) -> None:
    """Ссылка, а не копия, и относительная: две копии одной инструкции
    разъедутся молча, а абсолютная ссылка в клоне на контуре повиснет."""
    link = CLAUDE_DIR / skill.name
    assert link.is_symlink(), f"нет ссылки {link}"
    target = os.readlink(link)
    assert target == f"../../{SKILLS_DIR.as_posix()}/{skill.name}", target
    assert (link / "SKILL.md").resolve() == (skill / "SKILL.md").resolve()


def test_every_claude_link_leads_to_a_skill() -> None:
    """Ссылка без скилла — висящая: Claude Code молча её пропустит."""
    links = sorted(path for path in CLAUDE_DIR.iterdir() if path.is_symlink())
    assert links
    for link in links:
        assert (link / "SKILL.md").is_file(), f"{link} ведёт в никуда"
        assert link.name in {skill.name for skill in SKILLS}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.name)
def test_skill_reaches_a_fresh_clone(skill: Path) -> None:
    """Скилл обязан быть в git, а не только на машине разработчика.

    Строка `.claude/` в `.gitignore` закрывала его целиком: R02 числился
    сделанным, а в свежем клоне скилла не было. Проверка идёт по правилам
    игнорирования, а не по индексу, и по **каждому** файлу скилла: фаза,
    закрытая шаблоном, сломала бы скилл на середине сценария.
    """
    if shutil.which("git") is None or not Path(".git").exists():
        pytest.skip("нужен git-клон")
    files = sorted(str(path) for path in skill.rglob("*") if path.is_file())
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *files, str(CLAUDE_DIR / skill.name)],
        capture_output=True,
        text=True,
    )
    assert result.stdout.split() == []
