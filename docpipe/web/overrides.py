"""Ручной состав страниц: `pages.yaml`.

Состав документации — решение человека. Обход находит страницы по таблице
роутов, но таблица знает не всё: экран, маршрут которого собирается в рантайме,
в неё не попадает, а layout с `<router-outlet>` попадает и экраном не является.

**Ручная страница — это запись маршрута из другого источника, а не второй
механизм.** Добавление порождает `RouteEntry` с `source: pages.yaml`, и дальше
работает всё существующее: повышение вида, якорь бизнес-слоя, отчёт, покрытие.
Отдельная ветка кода для «ручных» страниц разошлась бы с автоматической
на первом же различии.

Снятие не удаляет узел, а отменяет повышение: класс остаётся компонентом
и документируется как компонент.

Главное свойство файла — он **обязан устаревать громко**. Компонент
переименовали, правило перестало совпадать, страница тихо исчезла
из документации: ни ошибки, ни предупреждения, в отчёте просто на строку
меньше. Поэтому каждое неприменившееся правило печатается и считается.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from docpipe.route import normalize_route

# Источник, которым помечается синтетическая запись маршрута. Он же печатается
# в обосновании страницы: «почему это страница» обязано отвечать «так решил
# человек», когда решил человек.
MANUAL_SOURCE = "pages.yaml"
MANUAL_TABLE = "manual"

StaleKind = Literal["add-missed", "add-redundant", "remove-missed"]

STALE_TITLES: dict[str, str] = {
    "add-missed": "правило `add` ни на что не легло: компонент не найден",
    "add-redundant": "правило `add` больше не нужно: маршрут находится сам",
    "remove-missed": "правило `remove` ни на что не легло",
}


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AddPage(_Base):
    """Страница, объявленная руками.

    `component` — FQN узла (`src/app/x/y.component.YPage`). Он зависит от пути
    файла и ломается при переносе; это осознанная цена — другого стабильного
    способа указать на класс у инструмента нет. Именно поэтому неприменившееся
    правило обязано быть громким: оно будет случаться.
    """

    route: str
    component: str
    reason: str

    @model_validator(mode="after")
    def _check(self) -> "AddPage":
        if not self.reason.strip():
            raise ValueError("правило `add` без `reason` запрещено")
        if not self.route.strip() or not self.component.strip():
            raise ValueError("правило `add` требует и `route`, и `component`")
        return self

    @property
    def normalized(self) -> str:
        return normalize_route(self.route)


class RemovePage(_Base):
    """Страница, снятая руками. Ключ — маршрут **либо** компонент."""

    route: str = ""
    component: str = ""
    reason: str

    @model_validator(mode="after")
    def _check(self) -> "RemovePage":
        if not self.reason.strip():
            raise ValueError("правило `remove` без `reason` запрещено")
        if bool(self.route.strip()) == bool(self.component.strip()):
            raise ValueError("правило `remove` требует ровно одно из `route` и `component`")
        return self

    @property
    def normalized(self) -> str:
        return normalize_route(self.route) if self.route else ""


class Overrides(_Base):
    """Содержимое `pages.yaml`."""

    version: str = "1"
    add: list[AddPage] = Field(default_factory=list)
    remove: list[RemovePage] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.add and not self.remove


class StaleRule(_Base):
    """Правило, которое ни на что не легло, — находка, а не пустяк."""

    kind: StaleKind
    key: str
    reason: str

    def describe(self) -> str:
        return f"{STALE_TITLES[self.kind]}: {self.key} ({self.reason})"


class OverrideReport(_Base):
    """Что сделали правила и что из них протухло."""

    added: list[str] = Field(default_factory=list)  # FQN компонентов
    removed: list[str] = Field(default_factory=list)
    stale: list[StaleRule] = Field(default_factory=list)


def load_overrides(path: Path) -> Overrides:
    """Прочитать файл ручного состава страниц.

    Пустой файл — законное состояние (правила все удалили), отсутствующий файл
    вызывающий обязан не звать вовсе: «файла нет» и «файл пуст» различаются
    только на стороне вызывающего, и путать их нельзя.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ожидался словарь верхнего уровня")

    body = raw.get("pages", raw)
    if not isinstance(body, dict):
        raise ValueError(f"{path}: секция `pages` должна быть словарём")

    return Overrides(
        version=str(raw.get("version", "1")),
        add=[AddPage.model_validate(item) for item in body.get("add", [])],
        remove=[RemovePage.model_validate(item) for item in body.get("remove", [])],
    )
