"""Загрузка скелетов документов и подстановка значений.

Скелет объявляет **состав секций** и место генерируемого блока — больше ничего.
Всё, что лежит вне маркеров, копируется в документ дословно, поэтому преамбулам
и инструкциям в шаблоне не место: они окажутся в каждом документе дерева.
Инструкция живёт в `templates/README.md`.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from docpipe.documents.zones import (
    DocumentError,
    is_section_empty,
    parse_document,
)

SUBSTITUTION: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

# Скелет, применяемый к виду сущности, для которого своего скелета нет.
# Имя фиксировано, а не задаётся в конфигурации: второй способ назвать одно
# и то же разошёлся бы с первым, а выигрыша нет.
DEFAULT_TEMPLATE: Final[str] = "default"

# Белый список ключей подстановки. Закрыт намеренно: `{{ tema }}` иначе молча
# осталась бы в каждом документе дерева как текст.
ALLOWED_KEYS: Final[frozenset[str]] = frozenset(
    {"title", "fqn", "kind", "module", "domain", "team", "doc_path", "node_id"}
)

_SECTION_MARKER = re.compile(r"docpipe:section:(start|end)")
_SECTION_NAME = re.compile(r"[a-z][a-z0-9_]*")


class Template(BaseModel):
    """Скелет документа одного вида сущности."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    text: str
    sections: list[str] = Field(default_factory=list)


class TemplateError(ValueError):
    """Скелет непригоден. Ошибка настройки, а не данных: код возврата 2."""


def substitute(text: str, values: Mapping[str, str]) -> str:
    """Подставить значения вместо `{{ ключ }}`.

    Не `str.format` и не `string.Template`: одинарные `{}` встречаются
    в маршрутах (`api/v1/Pricing/{id:guid}`) и в примерах JSON внутри подсказок.
    `str.format` на таком упадёт или испортит текст.

    Ключ без значения остаётся как есть — при валидном шаблоне это невозможно,
    а тихая замена на пустую строку прятала бы ошибку вызывающего.
    """
    return SUBSTITUTION.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise TemplateError(message)


def _load_one(path: Path) -> Template:
    # Явный `encoding="utf-8"`: подсказки на русском, а на Windows кодировка
    # по умолчанию не UTF-8, и файл прочитался бы мусором.
    text = path.read_text(encoding="utf-8")
    where = path.name

    try:
        parsed = parse_document(text)
    except DocumentError as exc:
        raise TemplateError(f"{where}: {exc}") from exc

    _check(
        parsed.front_matter is None,
        f"{where}: во front matter шаблона нет нужды — он собирается целиком по манифесту",
    )

    generated = parsed.generated
    _check(generated is not None, f"{where}: нет генерируемого блока")
    assert generated is not None
    _check(
        not generated.body.strip(),
        f"{where}: генерируемый блок обязан быть пустым — шаблон объявляет только его место,"
        " и всё, что там написано, затрётся при первом же прогоне",
    )

    # Маркер с именем не по образцу не распознаётся разбором и молча становится
    # обычным текстом. Сравнение числа маркеров с числом разобранных секций —
    # единственный способ это заметить.
    sections = parsed.section_names
    _check(
        len(_SECTION_MARKER.findall(text)) == 2 * len(sections),
        f"{where}: маркер секции не распознан; имя обязано быть по образцу"
        f" `{_SECTION_NAME.pattern}` и стоять в строке одно",
    )

    _check(bool(sections), f"{where}: в шаблоне нет ни одной секции")
    _check(
        "notes" in sections,
        f"{where}: нет секции `notes`. Она есть везде намеренно: при реклассификации"
        " должна остаться хотя бы одна секция-приёмник",
    )

    for name in sections:
        body = parsed.section(name)
        assert body is not None
        _check(
            is_section_empty(body.body),
            f"{where}: секция `{name}` не пуста. Подсказки в скелете бывают только"
            " HTML-комментариями: обычный текст сделал бы каждый новый документ"
            " «уже написанным», и агент обошёл бы всё дерево впустую",
        )

    unknown = sorted(set(SUBSTITUTION.findall(text)) - ALLOWED_KEYS)
    _check(
        not unknown,
        f"{where}: неизвестные ключи подстановки {unknown};"
        f" известны: {', '.join(sorted(ALLOWED_KEYS))}",
    )

    return Template(name=path.stem, text=text, sections=sections)


def load_templates(directory: Path) -> dict[str, Template]:
    """Загрузить скелеты из каталога.

    Обход **не рекурсивный**: `templates/examples/` содержит заполненные образцы
    для агента, и они скелетами не являются.
    """
    if not directory.is_dir():
        raise TemplateError(f"Каталог шаблонов не найден: {directory}")

    templates = {
        path.stem: _load_one(path)
        for path in sorted(directory.glob("*.md"))
        if path.stem != "README"
    }
    _check(bool(templates), f"{directory}: не найдено ни одного шаблона")
    return templates


def resolve_template(name: str, templates: Mapping[str, Template]) -> str | None:
    """Имя скелета, который будет применён: запрошенный, `default` или ничего.

    Одна функция на весь шаг 2, и это существенно. Разойдись разрешение
    в `plan` и в `build` — документ, созданный по базовому скелету, сверялся бы
    с другим и оставался бы `stale` навсегда, а агент шага 3 переписывал бы
    его на каждом прогоне.

    `None` — не подстановка «ничего», а отказ: базового скелета в каталоге нет.
    Решает вызывающий, и `plan` решает отменить прогон.
    """
    if name in templates:
        return name
    return DEFAULT_TEMPLATE if DEFAULT_TEMPLATE in templates else None
