"""Примитивы детерминизма: хэши, стабильная сериализация, slug'и.

От этого модуля зависит воспроизводимость всего пайплайна, поэтому здесь
не должно появляться ничего, что зависит от времени, локали, порядка обхода
файловой системы или порядка вставки в словарь.
"""

import hashlib
import json
import re
from typing import Any

# Границы слов в CamelCase. Первая альтернатива режет "PricingController"
# после строчной буквы или цифры, вторая — "HTTPClientFactory" перед
# последней заглавной в серии, за которой идёт строчная.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_DASH_RUN = re.compile(r"-{2,}")


def content_hash(data: bytes) -> str:
    """sha256 от содержимого в формате `sha256:<hex>`."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def stable_json_dumps(obj: Any) -> str:
    """Сериализация с фиксированным порядком ключей и завершающим переводом строки.

    `sort_keys` убирает зависимость от порядка вставки, `ensure_ascii=False`
    сохраняет кириллицу читаемой, `indent=2` делает дифы обозримыми.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def stable_hash(obj: Any) -> str:
    """Хэш произвольной JSON-совместимой структуры, не зависящий от порядка ключей."""
    return content_hash(stable_json_dumps(obj).encode("utf-8"))


def slugify(name: str) -> str:
    """Имя типа -> kebab-case ASCII, пригодный для имени файла.

    Type parameters отбрасываются: `Repository<T>` и `Repository` дают один slug.
    Развести их — задача разрешения коллизий в `tree.py`, а не этой функции.
    """
    # Type parameters в имя файла не попадают.
    bracket = name.find("<")
    if bracket != -1:
        name = name[:bracket]

    name = _CAMEL_BOUNDARY.sub("-", name)
    name = _NON_ALNUM.sub("-", name)
    name = _DASH_RUN.sub("-", name).strip("-").lower()

    return name or "unnamed"
