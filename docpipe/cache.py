"""Кэш разобранных файлов: sqlite + zlib.

Кэш существует ради одного свойства: инкрементальный прогон обязан давать
**тот же результат**, что и полный. Поэтому попадание определяется хэшем
содержимого файла, а не временем модификации — `mtime` меняется при checkout
и переносе репозитория, не меняя ни байта в файле.

Кэш — расходный материал. Любое сомнение в его актуальности решается очисткой:
потеря кэша стоит одного лишнего прогона парсера, а работа на устаревших
данных — неверного манифеста.
"""

import sqlite3
import zlib
from pathlib import Path
from types import TracebackType

from docpipe.hashing import stable_json_dumps
from docpipe.model import FileParseResult, ParserVersions

# Версия формата хранения. Менять при любом изменении схемы или способа
# сериализации payload — иначе старый кэш будет прочитан как новый.
CACHE_VERSION = "2"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta  (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                                  payload BLOB NOT NULL);
"""

# Уровень сжатия фиксирован явно: значение по умолчанию у zlib менялось между
# версиями Python, а payload хочется иметь воспроизводимым побайтово.
_COMPRESSION_LEVEL = 6


class ParseCache:
    """Хранилище `FileParseResult` с инвалидацией по версии парсера.

    Записи не коммитятся по одной: на десятках тысяч файлов это десятки тысяч
    fsync. Коммит происходит в `close()`, то есть при выходе из `with`.
    Незакоммиченные записи видны через `get` в том же соединении, поэтому
    на поведение внутри прогона это не влияет.
    """

    def __init__(self, db_path: Path, parser_versions: ParserVersions) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = self._connect()
        self._connection.executescript(_SCHEMA)
        self._invalidate_if_stale(parser_versions)

    # ----------------------------------------------------------------------------------
    # Открытие и инвалидация
    # ----------------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Открыть БД, пересоздав её, если файл повреждён или не является БД.

        Кэш нельзя делать причиной падения всего прогона: оборванная запись,
        обрезанный файл или посторонний файл с тем же именем должны стоить
        одного лишнего разбора, а не отказа команды с советом «удалите каталог».
        """
        try:
            connection = sqlite3.connect(self._path)
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return connection
        except sqlite3.DatabaseError:
            self._path.unlink(missing_ok=True)
            return sqlite3.connect(self._path)

    def _invalidate_if_stale(self, parser_versions: ParserVersions) -> None:
        """Очистить кэш, если он писался другой версией парсера или формата.

        Апгрейд грамматики может законно изменить вывод парсера на том же
        файле. Хэш содержимого при этом не меняется, поэтому попадание в кэш
        дало бы устаревший разбор — единственный способ это заметить —
        сверить версии.
        """
        expected = {
            "cache_version": CACHE_VERSION,
            "parser_versions": stable_json_dumps(parser_versions.model_dump()),
        }
        stored = dict(self._connection.execute("SELECT key, value FROM meta").fetchall())

        if stored == expected:
            return

        self._connection.execute("DELETE FROM files")
        self._connection.execute("DELETE FROM meta")
        self._connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)", sorted(expected.items())
        )
        self._connection.commit()

    # ----------------------------------------------------------------------------------
    # Чтение и запись
    # ----------------------------------------------------------------------------------

    def get(self, path: str, content_hash: str) -> FileParseResult | None:
        """Разбор файла, если он в кэше **и** содержимое не изменилось."""
        row = self._connection.execute(
            "SELECT payload FROM files WHERE path = ? AND content_hash = ?",
            (path, content_hash),
        ).fetchone()
        return None if row is None else _decode(row[0])

    def get_any(self, path: str) -> FileParseResult | None:
        """Разбор файла без сверки хэша.

        Нужен скоуп-режиму: файлы вне скоупа не читаются вовсе, поэтому их
        хэш неизвестен и сверять его не с чем. Свежесть здесь не гарантируется —
        именно поэтому такой манифест помечается `partial`.
        """
        row = self._connection.execute(
            "SELECT payload FROM files WHERE path = ?", (path,)
        ).fetchone()
        return None if row is None else _decode(row[0])

    def put(self, result: FileParseResult) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO files (path, content_hash, payload) VALUES (?, ?, ?)",
            (result.path, result.content_hash, _encode(result)),
        )

    # ----------------------------------------------------------------------------------
    # Обслуживание
    # ----------------------------------------------------------------------------------

    def all_paths(self) -> list[str]:
        return [row[0] for row in self._connection.execute("SELECT path FROM files ORDER BY path")]

    def prune(self, keep_paths: set[str]) -> int:
        """Удалить записи о файлах, которых больше нет. Возвращает число удалённых.

        Разница считается в Python, а не через `NOT IN (…)`: список путей
        измеряется тысячами, а число параметров в запросе sqlite ограничено.
        """
        stale = [path for path in self.all_paths() if path not in keep_paths]
        if stale:
            self._connection.executemany(
                "DELETE FROM files WHERE path = ?", ((path,) for path in stale)
            )
        return len(stale)

    def close(self) -> None:
        self._connection.commit()
        self._connection.close()

    def __enter__(self) -> "ParseCache":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _encode(result: FileParseResult) -> bytes:
    return zlib.compress(result.model_dump_json().encode("utf-8"), _COMPRESSION_LEVEL)


def _decode(payload: bytes) -> FileParseResult:
    return FileParseResult.model_validate_json(zlib.decompress(payload))
