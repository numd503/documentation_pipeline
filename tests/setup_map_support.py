"""Ключи файлов настройки и ключи карты `docs/setup-map.md`.

Общий помощник двух тестов: `test_setup_map.py` (S09) держит карту полной
в обе стороны, тест каталога вопросов (S28) сверяет с картой ключи, на которые
указывают варианты ответа. Поэтому обе половины — что ключи **есть в коде**
и что ключи **записаны в карте** — живут здесь, а не в самом тесте.

Ключи кода берутся из моделей и констант загрузчиков, а не перечисляются
руками: список руками отстал бы от кода ровно так, как отстали справочники
(17 мест, S08), и тест сверял бы карту с собственной копией карты.
"""

import re
import sys
import types
from pathlib import Path
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel

from docpipe import classify
from docpipe.arch.adapters import ADAPTERS
from docpipe.arch.model import ArchRegistry
from docpipe.config import DocpipeConfig
from docpipe.materialize import ownership
from docpipe.materialize.template import ALLOWED_KEYS
from docpipe.registry import config as registry_config
from docpipe.registry.model import RegistrySpec
from docpipe.ruleset import COMBINATORS
from docpipe.web.overrides import Overrides

MAP = Path("docs/setup-map.md")
INSTALL = Path("deploy/install.sh")

# Заголовки разделов карты: по разделу на вид файла. Порядок — порядок карты.
DOCPIPE = "`docpipe.yaml`"
RULES = "`rules.yaml`"
OWNERSHIP = "`ownership.yaml`"
PAGES = "`pages.yaml`"
REGISTRIES = "`registries.yaml`"
ARCH = "`arch-registry.yaml`"
TEMPLATES = "Скелеты `templates/*.md`"
INSTALLER = "Флаги и плейсхолдеры `install.sh`"
FILES: tuple[str, ...] = (DOCPIPE, RULES, OWNERSHIP, PAGES, REGISTRIES, ARCH, TEMPLATES, INSTALLER)

# Столбцы таблицы ключа. Последний заполняет S24 (`setup status`).
COLUMNS = 8


# --------------------------------------------------------------------------------------
# Ключи кода
# --------------------------------------------------------------------------------------


def _unwrap(annotation: Any) -> Any:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _variants(annotation: Any) -> list[Any]:
    """Варианты объединения без `None`; не объединение — он сам."""
    annotation = _unwrap(annotation)
    if get_origin(annotation) in (Union, types.UnionType):
        return [
            variant
            for argument in get_args(annotation)
            for variant in _variants(argument)
            if variant is not type(None)
        ]
    return [annotation]


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _field_keys(annotation: Any, key: str) -> set[str]:
    """Листовые пути одного поля.

    Модель — её поля через точку; `list[Model]` и `tuple[Model, ...]` —
    `key[].поле`; запись списка «строка или модель» (вторая форма S22) —
    и сам `key` (короткая форма), и поля записи; словарь и скаляр — `key`.
    """
    keys: set[str] = set()
    for variant in _variants(annotation):
        if _is_model(variant):
            keys |= model_keys(variant, f"{key}.")
        elif get_origin(variant) in (list, tuple):
            for item in _variants(get_args(variant)[0]):
                keys |= model_keys(item, f"{key}[].") if _is_model(item) else {key}
        else:
            keys.add(key)
    return keys


def model_keys(model: type[BaseModel], prefix: str = "") -> set[str]:
    """Листовые пути модели настройки, рекурсивно по `model_fields`.

    Имя ключа — псевдоним поля, если он есть: в файле пишут `in`
    (`web.registry_calls[].discriminator.in`) и `list`
    (`follow.children.list`), а не имена полей модели.
    """
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        keys |= _field_keys(field.annotation, prefix + (field.alias or name))
    return keys


def _nested(top: frozenset[str], containers: dict[str, set[str]], prefix: str = "") -> set[str]:
    """Пути по константам допустимых ключей: контейнер — `key[].поле`, остальное — `key`."""
    keys: set[str] = set()
    for key in top:
        if key in containers:
            keys |= {f"{prefix}{key}[].{item}" for item in containers[key]}
        else:
            keys.add(f"{prefix}{key}")
    return keys


def docpipe_keys() -> set[str]:
    """`docpipe.yaml`: модель плюс параметры адаптеров.

    `options` в модели — словарь (у каждого адаптера свои параметры), но базы
    у `spec` и `path` разные, и карта обязана их назвать. Параметры берутся
    из `_KNOWN_OPTIONS` модуля каждого адаптера `ADAPTERS`: новый адаптер без
    такого множества уронит тест, а не выпадет из карты.
    """
    keys = model_keys(DocpipeConfig)
    for adapter in ADAPTERS.values():
        options: set[str] = sys.modules[adapter.__module__]._KNOWN_OPTIONS  # type: ignore[attr-defined]
        keys |= {f"arch_adapters[].options.{option}" for option in options}
    return keys


def _predicates(prefix: str, names: Any) -> set[str]:
    return {f"{prefix}{name}" for name in (*names, *COMBINATORS)}


def rules_keys() -> set[str]:
    """`rules.yaml`: верх файла, каждая секция и предикаты условий.

    Предикаты общие для `when` и `unless` обеих секций, поэтому записаны
    один раз: `when.<предикат>`.
    """
    keys = {key for key in classify._FILE_KEYS if key not in classify.RULE_SECTIONS}
    for section in classify.RULE_SECTIONS:
        for key in classify._SECTION_KEYS:
            if key == "exclude":
                keys |= _nested(
                    classify._EXCLUDE_KEYS,
                    {"rules": set(classify._EXCLUDE_RULE_KEYS)},
                    f"{section}.exclude.",
                )
            elif key == "rules":
                keys |= {f"{section}.rules[].{item}" for item in classify._RULE_KEYS}
            else:
                keys.add(f"{section}.{key}")
    return keys | _predicates("when.", classify._PREDICATES)


def ownership_keys() -> set[str]:
    keys = _nested(
        ownership._OWNERSHIP_KEYS,
        {"teams": set(ownership._TEAM_KEYS), "rules": set(ownership._OWNERSHIP_RULE_KEYS)},
    )
    return keys | _predicates("rules[].when.", ownership.TABLE.predicates)


def pages_keys() -> set[str]:
    """`pages.yaml`: поля модели и тело `pages:` — контейнер второй формы файла."""
    return model_keys(Overrides) | {"pages"}


def registries_keys() -> set[str]:
    top = {key for key in registry_config._KNOWN_TOP if key != "registries"}
    return top | model_keys(RegistrySpec, "registries[].")


def arch_keys() -> set[str]:
    return model_keys(ArchRegistry)


def template_keys() -> set[str]:
    return {f"{{{{ {key} }}}}" for key in ALLOWED_KEYS}


_FLAG = re.compile(r"^\s*(?:-[a-z]\|)?(--[a-z][a-z-]*)\)", re.MULTILINE)
_PLACEHOLDER = re.compile(r'-e "s\|(@[A-Z][A-Z0-9_]*@)\|')


def installer_keys(script: Path = INSTALL) -> set[str]:
    """Флаги из разбора аргументов `install.sh` и плейсхолдеры, которые он подставляет."""
    text = script.read_text(encoding="utf-8")
    return set(_FLAG.findall(text)) | set(_PLACEHOLDER.findall(text))


def code_keys() -> dict[str, set[str]]:
    """Ключи каждого файла настройки так, как их знает код."""
    return {
        DOCPIPE: docpipe_keys(),
        RULES: rules_keys(),
        OWNERSHIP: ownership_keys(),
        PAGES: pages_keys(),
        REGISTRIES: registries_keys(),
        ARCH: arch_keys(),
        TEMPLATES: template_keys(),
        INSTALLER: installer_keys(),
    }


def top_segments(keys: set[str]) -> set[str]:
    """Ключи верхнего уровня: `web.roots[].path` → `web`, `teams[].id` → `teams`."""
    return {re.split(r"[.\[]", key, maxsplit=1)[0] for key in keys}


# --------------------------------------------------------------------------------------
# Ключи карты
# --------------------------------------------------------------------------------------

# Строка таблицы ключа: первая ячейка начинается с ключа в обратных кавычках.
_ROW = re.compile(r"^\|\s*`([^`]+)`")


def map_rows(text: str) -> dict[str, list[tuple[str, int]]]:
    """Строки таблиц карты по разделам файлов: ключ и число ячеек строки.

    Раздел — заголовок второго уровня из `FILES`; остальные (группы
    взаимозависимых ключей, «вне карты») не разбираются. Ячейки считаются
    по `|`, поэтому в тексте ячеек его нет: объединения типов карта пишет
    словом «или».
    """
    rows: dict[str, list[tuple[str, int]]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            current = title if title in FILES else None
            if current is not None:
                rows.setdefault(current, [])
            continue
        match = _ROW.match(line)
        if current is not None and match:
            rows[current].append((match.group(1), line.strip().strip("|").count("|") + 1))
    return rows


def map_keys(path: Path = MAP) -> dict[str, set[str]]:
    """Ключи, записанные в карте, по разделам файлов."""
    rows = map_rows(path.read_text(encoding="utf-8"))
    return {title: {key for key, _ in found} for title, found in rows.items()}


def all_map_keys(path: Path = MAP) -> set[str]:
    """Все ключи карты одним множеством — для сверки ссылок из других справочников."""
    return {key for keys in map_keys(path).values() for key in keys}
