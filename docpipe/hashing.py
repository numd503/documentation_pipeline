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


def camel_words(name: str) -> list[str]:
    """Слова имени типа по границам CamelCase, без type parameters и разделителей.

    Одна реализация на имя файла (`slugify`) и на срез «последнее слово»
    в отчёте настройки: два разбиения одного имени разошлись бы на первом же
    `HTTPClient`, и срез называл бы слово, которого нет в пути документа.

    Три формы, на которых разбиение легко прочитать неверно. Цифры прилипают
    к слову: `Migration20240101` — одно слово, граница режет только перед
    заглавной. Имя TypeScript начинается со строчной: `authInterceptor` —
    `auth`, `Interceptor`. Префикс интерфейса отделяется: `IPricingProvider` —
    `I`, `Pricing`, `Provider`. Буквы вне ASCII в слово не входят — как
    и в имя файла.
    """
    # Type parameters в слова не попадают: `Repository<T>` — одно слово.
    bracket = name.find("<")
    if bracket != -1:
        name = name[:bracket]

    return [word for part in _NON_ALNUM.split(name) for word in _CAMEL_BOUNDARY.split(part) if word]


def slugify(name: str) -> str:
    """Имя типа -> kebab-case ASCII, пригодный для имени файла.

    Type parameters отбрасываются: `Repository<T>` и `Repository` дают один slug.
    Развести их — задача разрешения коллизий в `tree.py`, а не этой функции.
    """
    return "-".join(camel_words(name)).lower() or "unnamed"
