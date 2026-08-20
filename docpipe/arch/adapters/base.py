"""Общее для адаптеров реестров (R04).

Адаптер читает исходный реестр при каждой сборке и выдаёт записи формата R03.
Он подключается **по имени в конфигурации**, а не ветвлением в ядре: ядро
не знает ни одного конкретного реестра ни одного конкретного репозитория,
и это проверяется тестом, а не обещанием (Р11).

Второй путь записи — это вторая реализация формата, и она разойдётся
с первой. Поэтому адаптер не строит модели в обход валидации: он собирает
те же `ArchRecord`, которые проверяет загрузчик, и его вывод обязан
проходить ту же проверку.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docpipe.arch.model import ArchRecord
from docpipe.hashing import content_hash


@dataclass(frozen=True)
class AdapterContext:
    """Что адаптеру нужно снаружи.

    Две разные базы отсчёта, и путать их нельзя. `root` — корень
    документируемого репозитория: от него отсчитываются файлы данных.
    `resolve` — разрешение **входа инструмента** (две ступени: текущий каталог,
    затем каталог `docpipe.yaml`): по нему находят описание реестров, которое
    лежит рядом с конфигурацией, а не в дереве исходников.
    """

    root: Path
    resolve: Callable[[str], Path]


@dataclass(frozen=True)
class AdapterResult:
    """Записи и находки.

    `errors` непуст и при успешном чтении: один битый файл или одна запись
    без якоря не должны ронять прогон — реестров десятки, и правились они
    годами разными командами. Правило перенесено из `registry/reader.py`.
    """

    records: tuple[ArchRecord, ...] = ()
    errors: tuple[str, ...] = ()


Adapter = Callable[[AdapterContext, dict[str, Any]], AdapterResult]


class FileHashes:
    """Хэши источников с кэшем на прогон.

    Адаптер проставляет `source.hash` сам, и это не забота о будущем: снимок,
    снятый с адаптера (`docpipe arch snapshot`), обязан сразу уметь стареть.
    Снимок без хэшей — снимок, устаревание которого не ловится ничем,
    и человеку пришлось бы дописывать сорок строк руками.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, str] = {}

    def of(self, relative: str) -> str:
        if relative not in self._cache:
            path = self._root / relative
            self._cache[relative] = content_hash(path.read_bytes()) if path.is_file() else ""
        return self._cache[relative]


def require(options: dict[str, Any], key: str, adapter: str) -> Any:
    """Обязательный параметр адаптера.

    Проверяется при запуске, а не при первом применении: адаптер без пути
    к источнику даёт ноль записей, а ноль записей внешне неотличим
    от «в системе нет таких точек входа».
    """
    if key not in options or options[key] in (None, ""):
        raise ValueError(f"адаптер {adapter}: нет обязательного параметра `{key}`")
    return options[key]


def unknown_options(options: dict[str, Any], known: set[str], adapter: str) -> None:
    """Опечатка в имени параметра — отказ, а не молчание.

    Параметр, которого адаптер не знает, ничего не делает; реестр при этом
    читается «почти правильно», и разбираться в результате будет уже не тот,
    кто опечатался.
    """
    extra = sorted(set(options) - known)
    if extra:
        raise ValueError(
            f"адаптер {adapter}: неизвестные параметры {extra}; известны: {sorted(known)}"
        )
