"""Карта цепочек настройки (`docs/setup-map.md`) не отстаёт от кода (S09).

Полнота в обе стороны: каждый ключ модели или константы загрузчика есть
в таблице своего файла, и каждая строка таблицы — ключ, который код знает.
Карта, проверяемая глазами, отстала бы за месяц — так отстали 17 мест
справочников, найденных сверкой S08.
"""

import re
from collections import Counter

import pytest
from pydantic import BaseModel, Field

from docpipe.arch import load as arch_load
from docpipe.config import DocpipeConfig
from docpipe.registry import config as registry_config
from docpipe.registry.model import RegistrySpec
from docpipe.setup.status import CODES
from docpipe.web import overrides
from tests.setup_map_support import (
    COLUMNS,
    DOCPIPE,
    FILES,
    MAP,
    RULES,
    all_map_keys,
    arch_keys,
    code_keys,
    map_findings,
    map_keys,
    map_rows,
    model_keys,
    pages_keys,
    registries_keys,
    top_segments,
)

CODE = code_keys()
MAPPED = map_keys()


def test_map_has_a_section_per_settings_file() -> None:
    assert list(MAPPED) == list(FILES)


@pytest.mark.parametrize("title", FILES)
def test_every_code_key_is_in_map(title: str) -> None:
    missing = sorted(CODE[title] - MAPPED[title])
    assert not missing, f"{MAP}, раздел {title}: нет строк для ключей {missing}"


@pytest.mark.parametrize("title", FILES)
def test_map_has_no_stale_keys(title: str) -> None:
    stale = sorted(MAPPED[title] - CODE[title])
    assert not stale, f"{MAP}, раздел {title}: ключей {stale} в коде нет"


def test_each_key_has_one_row() -> None:
    rows = map_rows(MAP.read_text(encoding="utf-8"))
    repeated = {
        title: sorted(key for key, count in Counter(key for key, _ in found).items() if count > 1)
        for title, found in rows.items()
    }
    assert not {title: keys for title, keys in repeated.items() if keys}


def test_rows_have_every_column() -> None:
    """Восемь ячеек в каждой строке: последнюю, «Находку», заполняет S24.

    Лишний `|` в тексте ячейки сдвинул бы колонки молча — поэтому объединения
    типов карта пишет словом «или».
    """
    rows = map_rows(MAP.read_text(encoding="utf-8"))
    broken = sorted(
        (title, key, cells)
        for title, found in rows.items()
        for key, cells in found
        if cells != COLUMNS
    )
    assert not broken


def test_finding_column_names_only_known_codes() -> None:
    """«Находка» — «—» или коды `FINDING_CODES` через запятую (S24).

    Код, которого `setup status` не выдаёт, отправил бы агента искать в отчёте
    находку, которой там не будет никогда; переименованный код без правки
    карты — то же самое.
    """
    found = map_findings(MAP.read_text(encoding="utf-8"))
    unknown = sorted(
        (title, key, cell)
        for title, rows in found.items()
        for key, cell in rows
        if cell != "—"
        and not all(
            re.fullmatch(r"`([a-z_.]+)`", part) and part.strip("`") in CODES
            for part in cell.split(", ")
        )
    )
    assert not unknown
    # Колонка заполнена: хоть одна находка у ключей `docpipe.yaml` и у правил.
    assert any(cell != "—" for _, cell in found[DOCPIPE])
    assert any(cell != "—" for _, cell in found[RULES])


def test_group_keys_exist_in_map() -> None:
    """Ключи раздела «Группы взаимозависимых ключей» — из таблиц карты."""
    text = MAP.read_text(encoding="utf-8")
    section = text.split("## Группы взаимозависимых ключей", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if re.match(r"^\| \d+\. ", line)]
    assert len(rows) == 19
    known = all_map_keys()
    unknown = sorted(
        {key for line in rows for key in re.findall(r"`([^`]+)`", line.strip("|").split("|")[1])}
        - known
    )
    assert not unknown


# --------------------------------------------------------------------------------------
# Ключи кода: константы загрузчиков согласованы с моделями
# --------------------------------------------------------------------------------------


def test_pages_constants_match_model() -> None:
    """Верх `pages.yaml` — поля `Overrides` плюс тело `pages:`; тело — те же списки."""
    tops = top_segments(pages_keys())
    assert tops == set(overrides._FILE_KEYS)
    assert set(overrides._BODY_KEYS) <= tops


def test_registries_constants_match_model() -> None:
    """Верх — `_KNOWN_TOP`; запись реестра — поля `RegistrySpec`, как их сверяет загрузчик."""
    keys = registries_keys()
    assert top_segments(keys) == set(registry_config._KNOWN_TOP)
    fields = {key.split(".")[1].split("[")[0] for key in keys if key.startswith("registries[].")}
    assert fields == set(RegistrySpec.model_fields)


def test_arch_constants_match_model() -> None:
    assert top_segments(arch_keys()) == set(arch_load._KNOWN_TOP)


# --------------------------------------------------------------------------------------
# Обход моделей
# --------------------------------------------------------------------------------------


class _Entry(BaseModel):
    glob: str
    reason: str = ""


class _Inner(BaseModel):
    where: str = Field(alias="in")
    name: str


class _Section(BaseModel):
    inner: _Inner
    items: list[_Inner] = Field(default_factory=list)


class _Shapes(BaseModel):
    plain: str = ""
    optional: str | None = None
    mixed: list[str | _Entry] = Field(default_factory=list)
    entries: list[_Entry] = Field(default_factory=list)
    mapping: dict[str, str] = Field(default_factory=dict)
    section: _Section | None = None
    pairs: tuple[_Entry, ...] = ()
    names: list[str] = Field(default_factory=list)


def test_model_keys_follow_the_documented_shapes() -> None:
    assert model_keys(_Shapes) == {
        "plain",
        "optional",
        # Короткая форма — сам ключ, вторая — поля записи.
        "mixed",
        "mixed[].glob",
        "mixed[].reason",
        "entries[].glob",
        "entries[].reason",
        "mapping",
        # Псевдоним — то, что пишут в файле.
        "section.inner.in",
        "section.inner.name",
        "section.items[].in",
        "section.items[].name",
        "pairs[].glob",
        "pairs[].reason",
        "names",
    }


def test_second_form_keys_of_docpipe_yaml() -> None:
    keys = model_keys(DocpipeConfig)
    assert {"enrolled", "enrolled[].glob", "enrolled[].reason"} <= keys
    assert {"not_enrolled[].glob", "not_enrolled[].reason"} <= keys
    assert "not_enrolled" not in keys  # короткой формы у ключа нет
    assert "web.registry_calls[].discriminator.in" in keys
    assert "domains" in keys and not any(key.startswith("domains.") for key in keys)


def test_new_config_key_without_row_breaks_the_map() -> None:
    """Критерий приёмки S09 механизмом, а не только ручной правкой модели."""

    class Extended(DocpipeConfig):
        brand_new_key: list[str] = Field(default_factory=list)

    assert model_keys(Extended) - MAPPED[DOCPIPE] == {"brand_new_key"}
