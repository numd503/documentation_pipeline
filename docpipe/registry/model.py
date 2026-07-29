"""Модели реестров платформы.

Реестр — файл в репозитории, который **уже** объявляет точки входа и читается
исполняющей системой: список сервисов кластера, таблица джобов, определения
workflow. Его нельзя испортить незаметно — неверный реестр ломает работу,
поэтому доверие к нему выше, чем к любому комментарию в коде.

Два набора моделей: описание реестра (`RegistrySpec`, приходит из YAML) и
результат чтения (`RegistryItem`, `RegistryResult`).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Форматы верхнего уровня. `json` сюда не входит намеренно: способ найти записи
# в JSON-файле нигде не задан, а гадать про него — значит выдумать формат,
# которым никто не пользуется. JSON читается только как цель `follow`,
# где структура известна.
RegistryFormat = Literal["xml", "inline"]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class ChildSpec(_Base):
    """Вложенные записи внутри записи реестра: поля списка, обработчики событий.

    `item_xpath` вычисляется относительно родительского элемента.
    """

    kind: str
    item_xpath: str
    fields: dict[str, str]


class FollowChildSpec(_Base):
    """Вложенные записи внутри файла, на который ведёт `follow`.

    Ключ в YAML — `list`: это имя массива в JSON, а не путь. Псевдоним нужен,
    чтобы имя поля в коде не совпадало со встроенным `list`.
    """

    kind: str
    items_key: str = Field(alias="list")
    fields: dict[str, str]


class FollowSpec(_Base):
    """Переход по ссылке: значение поля записи — путь к файлу с описанием.

    `base` задаётся явно и не выводится из положения файла реестра: путь внутри
    реестра отсчитывается от корня развёртывания, а не от каталога, где лежит
    сам реестр. Склейка с каталогом реестра даёт путь, который выглядит
    настоящим и указывает в никуда.
    """

    field: str
    base: str
    format: Literal["json"] = "json"
    fields: dict[str, str]
    children: FollowChildSpec | None = None


class RegistrySpec(_Base):
    """Описание одного реестра. Данные, а не код: приходит из `registries.yaml`.

    `fields` — отображение «имя поля результата → выражение». Для XML выражение
    бывает трёх видов: `@attr`, `путь/@attr` и `путь` (текст элемента).
    """

    id: str
    kind: str
    format: RegistryFormat
    path: str | None = None
    paths: list[str] = Field(default_factory=list)
    item_xpath: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    children: list[ChildSpec] = Field(default_factory=list)
    follow: FollowSpec | None = None
    items: list[dict[str, str]] = Field(default_factory=list)

    def patterns(self) -> list[str]:
        """Шаблоны путей: `path` и `paths` складываются, а не замещают друг друга."""
        return ([self.path] if self.path else []) + list(self.paths)


class RegistryItem(_Base):
    """Одна запись реестра: сервис, джоб, workflow, список.

    `ref` — якорь: строка, которую обязан знать вызывающий. Все значения полей
    строковые, в том числе пришедшие из JSON числа: реестр по природе своей
    хранит строки, и единообразие дешевле, чем разбор типов на этом уровне.
    """

    registry: str
    kind: str
    ref: str
    fields: dict[str, str] = Field(default_factory=dict)
    source_path: str
    children: list["RegistryItem"] = Field(default_factory=list)


class RegistryResult(_Base):
    """Результат чтения одного реестра.

    `errors` непуст и при успешном чтении: один битый файл или одна запись без
    якоря не должны ронять прогон — реестров десятки, правились они годами
    разными командами.
    """

    registry: str
    items: list[RegistryItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


RegistryItem.model_rebuild()
