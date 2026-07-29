"""Загрузка `registries.yaml` с полной проверкой структуры.

Проверять надо при загрузке, а не при первом применении: опечатка в имени поля
или в XPath иначе означает реестр, который просто ничего не находит. Пустой
реестр внешне неотличим от «в системе нет таких точек входа», и обнаружится
это в лучшем случае через месяц.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from docpipe.registry.model import RegistrySpec

_KNOWN_TOP = frozenset({"version", "registries_version", "registries"})
_REQUIRED = frozenset({"id", "kind", "format"})


def _check_keys(item: dict[str, Any], where: str) -> None:
    known = set(RegistrySpec.model_fields)
    unknown = set(item) - known
    if unknown:
        raise ValueError(f"{where}: неизвестные ключи {sorted(unknown)}; известны: {sorted(known)}")


def _check_shape(item: dict[str, Any], where: str) -> None:
    """Проверки, зависящие от формата: их не выражает схема модели."""
    if item["format"] == "inline":
        entries = item.get("items")
        if not isinstance(entries, list):
            raise ValueError(f"{where}: формат `inline` требует список `items`")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or "ref" not in entry:
                raise ValueError(f"{where}: items[{index}] должен быть словарём с ключом `ref`")
        return

    if not item.get("path") and not item.get("paths"):
        raise ValueError(f"{where}: нужен `path` или `paths`")
    if not item.get("item_xpath"):
        raise ValueError(f"{where}: нужен `item_xpath`")

    # `ref` может прийти как из самой записи, так и из файла, на который ведёт
    # `follow`: у workflow заголовок лежит в XML, а идентификатор — в JSON.
    fields = item.get("fields") or {}
    follow_fields = (item.get("follow") or {}).get("fields") or {}
    if "ref" not in fields and "ref" not in follow_fields:
        raise ValueError(f"{where}: среди `fields` нет `ref` — нечего использовать как якорь")


def load_registries(path: Path) -> list[RegistrySpec]:
    """Загрузить описания реестров.

    Порядок в файле сохраняется: это порядок чтения, а не порядок вывода —
    результат каждого реестра сортируется отдельно.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Файл реестров не найден: {path}")

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: описание реестров должно быть словарём")

    unknown = set(raw) - _KNOWN_TOP
    if unknown:
        raise ValueError(
            f"{path}: неизвестные ключи верхнего уровня {sorted(unknown)};"
            f" известны: {sorted(_KNOWN_TOP)}"
        )

    entries = raw.get("registries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: `registries` должен быть непустым списком")

    specs: list[RegistrySpec] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        where = f"{path}: реестр #{index}"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: должен быть словарём, получено {type(item).__name__}")

        missing = _REQUIRED - set(item)
        if missing:
            raise ValueError(f"{where}: без полей {sorted(missing)}")

        where = f"{path}: реестр {item['id']!r}"
        if item["id"] in seen:
            raise ValueError(f"{where}: повтор id")
        seen.add(item["id"])

        _check_keys(item, where)
        _check_shape(item, where)

        try:
            specs.append(RegistrySpec.model_validate(item))
        except ValidationError as exc:
            raise ValueError(f"{where}: {exc}") from exc

    return specs
