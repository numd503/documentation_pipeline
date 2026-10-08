"""Каталог вопросов интервью (`docs/setup-interview.md`) не отстаёт от кода (S28).

Протокол читает агент контура, а не человек: вопрос, который указывает на ключ,
которого нет, проходит любое ревью глазами и ломается на первой же правке —
агент впишет в файл настройки то, что загрузчик отвергнет (S02), или то, что
никто не читает. Поэтому всё, что в каталоге названо именем кода — коды
находок, ключи, команды, тесты-примеры, — сверяется с кодом здесь.

Разметка, на которой держится разбор, описана в самом документе («Как читать
каталог»): раздел `### <код>`, блоки `**Имя.**`, варианты — нумерованный
список с подписью жирным, адреса — строки `- правка: `файл` → `ключ`, …`,
`- команда: `docpipe …``, `- вне настройки: …`.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest
import typer

from docpipe.cli import app
from docpipe.setup.candidates import KINDS as CANDIDATE_KINDS
from docpipe.setup.status import CODES, FINDING_CODES
from tests.setup_map_support import (
    DOCPIPE,
    OWNERSHIP,
    PAGES,
    REGISTRIES,
    RULES,
    map_keys,
)

DOC: Final = Path("docs/setup-interview.md")
CATALOG: Final = "## Каталог"

# Вид файла в строке правки → раздел карты `setup-map.md`.
FILES: Final[dict[str, str]] = {
    "docpipe.yaml": DOCPIPE,
    "rules.yaml": RULES,
    "pages.yaml": PAGES,
    "ownership.yaml": OWNERSHIP,
    "registries.yaml": REGISTRIES,
}

# Ключи, которые вводит параллельная задача: вариант уже указывает на них,
# а карты ещё нет. Пропуск действует, только пока в карте нет **ни одного**
# ключа с этим началом: как только задача влита, ключи сверяются с картой
# как все остальные — и опечатка в имени поля станет красным тестом, а не
# вечным пропуском.
PENDING: Final[dict[tuple[str, str], str]] = {}

# Записи «не берём»: их правка — решение человека с его причиной (`purpose.md`,
# граница детерминизма). Агент не пишет их сам ни в «Без вопроса», ни в «Починке».
NEGATIVE: Final[dict[str, tuple[str, ...]]] = {
    DOCPIPE: ("not_enrolled", "exclude", "link", "web.not_wrappers"),
    RULES: ("dotnet.exclude", "web.exclude"),
    PAGES: ("remove",),
}

REASONS: Final = ("слова человека", "доказательство агента", "не нужна")
DECISION_BLOCKS: Final = ("Материал", "Без вопроса", "Вопрос", "Варианты", "Пример")
DEFECT_BLOCKS: Final = ("Материал", "Починка", "Пример")
HEADER_LIMIT: Final = 12

_SECTION = re.compile(r"^### `?([a-z_]+\.[a-z_]+)`?\s*$")
_BLOCK = re.compile(r"^\*\*([^*]+?)\.\*\*")
_VARIANT = re.compile(r"^\d+\. \*\*([^*]+)\*\*")
_EDIT = re.compile(r"^\s*- правка: `([^`]+)` → (.+)$")
_KEYS = re.compile(r"`[^`]+`(?:, `[^`]+`)*")
_COMMAND_LINE = re.compile(r"^\s*- команда: `(docpipe [^`]+)`")
_OUTSIDE = re.compile(r"^\s*- вне настройки: \S")
_REASON = re.compile(r"^\s*- причина: (.+)$")
_HEADER = re.compile(r"Заголовок «([^»]+)»")
_COMMAND = re.compile(r"`(docpipe [^`]+)`")
_TEST_PATH = re.compile(r"`(tests/[^`:\s]+)(?:::(\w+))?(?::\d+)?`")
_NAME = re.compile(r"[a-z][a-z-]*")


@dataclass(frozen=True)
class Edit:
    """Строка правки: вид файла, раздел карты и ключи."""

    file: str
    section: str
    keys: tuple[str, ...]


@dataclass
class Variant:
    label: str
    edits: list[Edit] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    outside: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def addresses(self) -> int:
        return len(self.edits) + len(self.commands) + self.outside


@dataclass
class Section:
    code: str
    blocks: dict[str, list[str]] = field(default_factory=dict)
    variants: list[Variant] = field(default_factory=list)
    # Правки и команды раздела по блокам: «Без вопроса», «Починка», «Варианты».
    edits: list[tuple[str, Edit]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def block(self, name: str) -> str:
        """Текст блока одной строкой: шаблон вопроса переносится как угодно."""
        return " ".join(line.strip() for line in self.blocks.get(name, []))


def _edit(line: str) -> Edit | None:
    match = _EDIT.match(line)
    if match is None:
        return None
    file, rest = match.groups()
    # Строгая форма: после стрелки — только ключи в кавычках через запятую.
    # Значение живёт строкой «значение», иначе его приняли бы за ключ.
    assert _KEYS.fullmatch(rest.strip()), f"{DOC}: строка правки не по форме: {line!r}"
    assert file in FILES, f"{DOC}: неизвестный вид файла {file!r} в {line!r}"
    keys = tuple(re.findall(r"`([^`]+)`", rest))
    return Edit(file=file, section=FILES[file], keys=keys)


def _catalog_lines(text: str) -> list[str]:
    """Строки раздела «Каталог» до следующего заголовка второго уровня."""
    lines = text.splitlines()
    assert CATALOG in lines, f"{DOC}: нет раздела {CATALOG!r}"
    body = lines[lines.index(CATALOG) + 1 :]
    end = next((index for index, line in enumerate(body) if line.startswith("## ")), len(body))
    return body[:end]


def parse_catalog(text: str) -> list[Section]:
    sections: list[Section] = []
    current: Section | None = None
    block = ""
    for line in _catalog_lines(text):
        heading = _SECTION.match(line)
        if heading:
            current = Section(code=heading.group(1))
            sections.append(current)
            block = ""
            continue
        if current is None:
            continue
        opened = _BLOCK.match(line)
        if opened:
            block = opened.group(1).split(" (")[0]
        current.blocks.setdefault(block, []).append(line)

        if block == "Варианты" and (variant := _VARIANT.match(line)):
            current.variants.append(Variant(label=variant.group(1).strip()))
            continue
        target = current.variants[-1] if block == "Варианты" and current.variants else None
        if (edit := _edit(line)) is not None:
            current.edits.append((block, edit))
            if target is not None:
                target.edits.append(edit)
        elif command := _COMMAND_LINE.match(line):
            current.commands.append(command.group(1))
            if target is not None:
                target.commands.append(command.group(1))
        elif _OUTSIDE.match(line) and target is not None:
            target.outside += 1
        elif (reason := _REASON.match(line)) and target is not None:
            target.reasons.append(reason.group(1).strip())
    return sections


TEXT: Final = DOC.read_text(encoding="utf-8")
SECTIONS: Final = parse_catalog(TEXT)
BY_CODE: Final = {section.code: section for section in SECTIONS}
MAPPED: Final = map_keys()


def _all_edits(text: str) -> Iterator[tuple[int, Edit]]:
    """Все строки правки документа — каталог и вопросы по кандидатам."""
    for number, line in enumerate(text.splitlines(), start=1):
        edit = _edit(line)
        if edit is not None:
            yield number, edit


def _known(section: str, key: str) -> bool:
    """Ключ есть в карте или ждёт своей задачи (`PENDING`), а карта о нём ещё не знает."""
    keys = MAPPED[section]
    if key in keys:
        return True
    for (pending_section, prefix), _ in PENDING.items():
        if section == pending_section and _starts(key, prefix):
            return not any(_starts(mapped, prefix) for mapped in keys)
    return False


def _starts(key: str, prefix: str) -> bool:
    """Начало ключа по границе сегмента: `exclude` — у `exclude[].glob`, но не у `excluded`."""
    return key == prefix or key.startswith((f"{prefix}.", f"{prefix}["))


def _negative(edit: Edit) -> bool:
    return any(
        _starts(key, prefix) for key in edit.keys for prefix in NEGATIVE.get(edit.section, ())
    )


def _writes_reason(edit: Edit) -> bool:
    """Вписывает ли правка причину в запись «не берём» — поле `reason` этой записи."""
    return any(
        key.endswith(".reason") and _starts(key, prefix)
        for key in edit.keys
        for prefix in NEGATIVE.get(edit.section, ())
    )


def _resolves(command: str) -> bool:
    """`docpipe docs adopt МАНИФЕСТ …` — путь до зарегистрированной команды приложения.

    Слова после команды (аргументы, флаги, `…`) не проверяются; остановка
    на группе (`docpipe setup`) — не команда.
    """
    current: object = typer.main.get_command(app)
    for word in command.split()[1:]:
        commands = getattr(current, "commands", None)
        if commands is None:
            break
        if not _NAME.fullmatch(word) or word not in commands:
            return False
        current = commands[word]
    return getattr(current, "commands", None) is None


# --------------------------------------------------------------------------------------
# Разделы: по одному на код, в порядке кодов
# --------------------------------------------------------------------------------------


def test_every_finding_code_has_a_section_in_code_order() -> None:
    """Критерий приёмки: раздел на каждый код `FINDING_CODES`, лишних нет.

    Порядок — тот же, что у кодов: по нему агент находит раздел находки,
    которую только что вернул `setup status`, и по нему идут фазы.
    """
    assert [section.code for section in SECTIONS] == [item.code for item in FINDING_CODES]


def test_each_code_has_one_section() -> None:
    assert len(BY_CODE) == len(SECTIONS)


@pytest.mark.parametrize("code", sorted(CODES))
def test_section_has_the_blocks_of_its_category(code: str) -> None:
    """Решение — вопрос с вариантами; дефект — починка без вопроса (Р-6)."""
    section = BY_CODE[code]
    if CODES[code].category == "decision":
        missing = [name for name in DECISION_BLOCKS if name not in section.blocks]
        assert not missing, f"{code}: нет блоков {missing}"
    else:
        missing = [name for name in DEFECT_BLOCKS if name not in section.blocks]
        assert not missing, f"{code}: нет блоков {missing}"
        # Дефект решением «не беру» не закрывается: вопроса к человеку у него нет.
        assert "Вопрос" not in section.blocks and not section.variants, code
        assert any(block == "Починка" for block, _ in section.edits) or any(
            _OUTSIDE.match(line) for line in section.blocks["Починка"]
        ), f"{code}: у починки нет адреса"


# --------------------------------------------------------------------------------------
# Вопрос и варианты
# --------------------------------------------------------------------------------------

DECISIONS: Final = sorted(code for code, item in CODES.items() if item.category == "decision")


@pytest.mark.parametrize("code", DECISIONS)
def test_question_has_a_short_header_and_says_what_depends_on_it(code: str) -> None:
    """Форма вопроса (S28, п. 2): заголовок до 12 символов, «от ответа зависит»."""
    question = BY_CODE[code].block("Вопрос")
    headers = _HEADER.findall(question)
    assert len(headers) == 1, f"{code}: заголовок вопроса — «Заголовок «…»», один"
    assert len(headers[0]) <= HEADER_LIMIT, f"{code}: заголовок {headers[0]!r} длиннее 12"
    assert "От ответа зависит" in question, code


@pytest.mark.parametrize("code", DECISIONS)
def test_every_variant_has_an_address_and_a_reason_rule(code: str) -> None:
    """2–4 варианта; у каждого адрес ответа и одно правило причины.

    Вариант без адреса — анкета: ответ никуда не ляжет, и следующая сессия
    задаст тот же вопрос.
    """
    variants = BY_CODE[code].variants
    assert 2 <= len(variants) <= 4, f"{code}: вариантов {len(variants)}"
    for variant in variants:
        assert variant.addresses, f"{code}, «{variant.label}»: нет адреса"
        assert len(variant.reasons) == 1, f"{code}, «{variant.label}»: строка причины — одна"
        assert variant.reasons[0] in REASONS, f"{code}, «{variant.label}»: {variant.reasons}"


@pytest.mark.parametrize("code", sorted(CODES))
def test_negative_decisions_are_asked_and_their_reason_is_the_humans(code: str) -> None:
    """Ловушка S28: агент, дописывающий причину отсева сам, ломает границу детерминизма.

    Запись «не берём» не правится в «Без вопроса» и «Починке», а вариант,
    вписывающий в неё причину, несёт слова человека, а не доказательство агента.
    """
    section = BY_CODE[code]
    alone = [edit for block, edit in section.edits if block != "Варианты" and _negative(edit)]
    assert not alone, f"{code}: запись «не берём» без вопроса — {alone}"
    for variant in section.variants:
        if any(_writes_reason(edit) for edit in variant.edits):
            assert variant.reasons == ["слова человека"], f"{code}, «{variant.label}»"


@pytest.mark.parametrize("code", DECISIONS)
def test_edits_cover_where_the_decision_lives(code: str) -> None:
    """Ключи из `decision_home` кода — среди правок его раздела.

    Иначе каталог и `setup status` назвали бы агенту разные места решения:
    отчёт отправил бы в `link.external_targets`, а вопрос — мимо него.
    """
    section = BY_CODE[code]
    keys = [key for _, edit in section.edits for key in edit.keys]
    for token in re.findall(r"`([^`]+)`", CODES[code].decision_home):
        if token.startswith("docpipe "):
            assert any(command.startswith(token) for command in section.commands), (code, token)
        else:
            assert any(_starts(key, token) for key in keys), (code, token)


# --------------------------------------------------------------------------------------
# Ключи, команды и примеры существуют
# --------------------------------------------------------------------------------------


def test_every_edited_key_is_in_the_settings_map() -> None:
    """Критерий приёмки: каждый ключ, на который указывает правка, есть в `setup-map.md`.

    Список ключей — из помощника теста карты (S09): та же разбивка по видам
    файлов, что у карты, поэтому `rules[].id` владения и `dotnet.rules[].id`
    правил не перепутаются.
    """
    unknown = sorted(
        (number, edit.file, key)
        for number, edit in _all_edits(TEXT)
        for key in edit.keys
        if not _known(edit.section, key)
    )
    assert not unknown, f"{DOC}: ключей нет в карте — {unknown}"


def test_pending_keys_are_used_and_named() -> None:
    """Пропуск `PENDING` не живёт сам по себе: его ключ стоит в варианте и назван в тексте."""
    for (section, prefix), task in PENDING.items():
        used = [
            key
            for _, edit in _all_edits(TEXT)
            if edit.section == section
            for key in edit.keys
            if _starts(key, prefix)
        ]
        assert used, f"PENDING {prefix}: ни одна правка на него не указывает"
        assert task in TEXT, f"{DOC}: не сказано, что {prefix} вводит {task}"


def test_every_command_is_registered() -> None:
    """Команда `docpipe …` в тексте — зарегистрированная команда приложения.

    Ловушка S29: инструкция со ссылкой на несуществующую команду проходит
    любое ревью глазами и ломается у агента на первом шаге.
    """
    commands = sorted(set(_COMMAND.findall(TEXT)))
    assert commands
    broken = [command for command in commands if not _resolves(command)]
    assert not broken


def test_every_candidate_kind_is_known() -> None:
    """`setup candidates <вид>` — вид из `KINDS`: опечатка в нём — код 2 у агента."""
    kinds = set(re.findall(r"`docpipe setup candidates ([a-z-]+)", TEXT))
    assert kinds
    assert kinds <= set(CANDIDATE_KINDS), sorted(kinds - set(CANDIDATE_KINDS))


@pytest.mark.parametrize("code", sorted(CODES))
def test_example_points_at_an_existing_fixture_or_test(code: str) -> None:
    """Пример — на фикстуре или в тесте, которые есть: переименование без правки — красное."""
    example = BY_CODE[code].block("Пример")
    found = _TEST_PATH.findall(example)
    assert found, f"{code}: пример без фикстуры или теста"
    for path, test in found:
        assert Path(path).exists(), f"{code}: нет {path}"
        if test:
            source = Path(path).read_text(encoding="utf-8")
            assert f"def {test}(" in source, f"{code}: нет теста {path}::{test}"


# --------------------------------------------------------------------------------------
# Разбор разметки: на чём держатся проверки выше
# --------------------------------------------------------------------------------------

_SAMPLE: Final = """\
## Каталог

### scope.module_undecided

**Материал.** `docpipe setup status`.

**Без вопроса.** Нет.

**Вопрос.** Заголовок «Область». «От ответа зависит, …».

**Варианты.**

1. **Берём** — в области.
   - правка: `docpipe.yaml` → `enrolled`, `enrolled[].glob`
   - значение: `src/**`
   - причина: не нужна
2. **Не берём** — чужой код.
   - правка: `docpipe.yaml` → `not_enrolled[].glob`, `not_enrolled[].reason`
   - причина: слова человека

**Пример.** `tests/fixtures/SampleSolution`.

## Дальше
"""


def test_parser_reads_variants_edits_and_reasons() -> None:
    [section] = parse_catalog(_SAMPLE)
    assert section.code == "scope.module_undecided"
    assert [variant.label for variant in section.variants] == ["Берём", "Не берём"]
    first, second = section.variants
    assert first.edits == [Edit("docpipe.yaml", DOCPIPE, ("enrolled", "enrolled[].glob"))]
    assert (first.reasons, second.reasons) == (["не нужна"], ["слова человека"])
    assert _writes_reason(second.edits[0]) and not _negative(first.edits[0])
    assert "Заголовок «Область»" in section.block("Вопрос")


def test_edit_line_with_a_value_after_the_arrow_is_refused() -> None:
    """Значение после стрелки разбор принял бы за ключ — форма строгая."""
    with pytest.raises(AssertionError, match="не по форме"):
        _edit("   - правка: `docpipe.yaml` → `enrolled[].glob`: `src/**`")


def test_negative_prefix_is_a_segment_not_a_substring() -> None:
    assert _negative(Edit("docpipe.yaml", DOCPIPE, ("exclude[].glob",)))
    assert not _negative(Edit("docpipe.yaml", DOCPIPE, ("docs_scan_exclude",)))
    assert _negative(Edit("rules.yaml", RULES, ("dotnet.exclude.rules[].when",)))
    assert not _negative(Edit("rules.yaml", RULES, ("dotnet.rules[].when",)))


def test_pending_key_passes_only_while_the_map_has_none_of_it() -> None:
    assert _known(DOCPIPE, "web.http_wrappers[].receiver")
    assert not _known(DOCPIPE, "web.http_wrapper[].receiver")
    if not any(_starts(key, "web.not_wrappers") for key in MAPPED[DOCPIPE]):
        # Задача ещё не влита: любой ключ с этим началом ждёт её.
        assert _known(DOCPIPE, "web.not_wrappers[].anything")
    else:
        # Влита: пропуска больше нет, ключ сверяется с картой как все.
        assert not _known(DOCPIPE, "web.not_wrappers[].no_such_field")


def test_command_resolution_follows_groups() -> None:
    assert _resolves("docpipe docs adopt МАНИФЕСТ --from a --to b")
    assert _resolves("docpipe scan --stats")
    assert not _resolves("docpipe setup")  # группа, а не команда
    assert not _resolves("docpipe setup statuz")
    assert not _resolves("docpipe …")
