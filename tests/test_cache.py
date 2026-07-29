"""Проверка кэша разобранных файлов (T09)."""

import sqlite3
from pathlib import Path

from docpipe.cache import CACHE_VERSION, ParseCache
from docpipe.dotnet.parser import parse_file, parse_source
from docpipe.model import FileParseResult, ParserVersions

VERSIONS = ParserVersions(tree_sitter="0.26.0", grammar_c_sharp="0.23.5")
OTHER_VERSIONS = ParserVersions(tree_sitter="0.27.0", grammar_c_sharp="0.23.5")


def _result(path: str, source: bytes = b"namespace N;\npublic class C { }\n") -> FileParseResult:
    return parse_source(source, path)


def _db(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "parse.sqlite"


# --------------------------------------------------------------------------------------
# Базовый цикл
# --------------------------------------------------------------------------------------


def test_put_then_get_returns_equal_object(tmp_path: Path) -> None:
    result = _result("a.cs")
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(result)
        assert cache.get("a.cs", result.content_hash) == result


def test_get_with_other_hash_is_a_miss(tmp_path: Path) -> None:
    """Попадание определяется содержимым, а не именем файла."""
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(_result("a.cs"))
        assert cache.get("a.cs", "sha256:0000") is None


def test_get_unknown_path_is_a_miss(tmp_path: Path) -> None:
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        assert cache.get("nowhere.cs", "sha256:0000") is None


def test_put_replaces_previous_entry(tmp_path: Path) -> None:
    """Файл изменился — в кэше должна остаться одна запись, новая."""
    old = _result("a.cs", b"namespace N;\npublic class Old { }\n")
    new = _result("a.cs", b"namespace N;\npublic class New { }\n")

    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(old)
        cache.put(new)

        assert cache.all_paths() == ["a.cs"]
        assert cache.get("a.cs", old.content_hash) is None
        assert cache.get("a.cs", new.content_hash) == new


def test_survives_reopen(tmp_path: Path) -> None:
    result = _result("a.cs")
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(result)

    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        assert cache.get("a.cs", result.content_hash) == result


def test_round_trip_keeps_full_structure(sample_solution: Path, tmp_path: Path) -> None:
    """Через кэш проходит весь разбор целиком, а не только объявления."""
    result = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    )
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(result)
        restored = cache.get(result.path, result.content_hash)

    assert restored == result
    assert restored is not None
    assert restored.usings and restored.declarations[0].members


# --------------------------------------------------------------------------------------
# Инвалидация
# --------------------------------------------------------------------------------------


def test_other_parser_versions_wipe_the_cache(tmp_path: Path) -> None:
    """Апгрейд грамматики может изменить вывод на том же файле.

    Хэш содержимого при этом не меняется, поэтому попадание в кэш вернуло бы
    устаревший разбор. Сверка версий — единственный способ это заметить.
    """
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(_result("a.cs"))

    with ParseCache(_db(tmp_path), OTHER_VERSIONS) as cache:
        assert cache.all_paths() == []


def test_other_cache_version_wipes_the_cache(tmp_path: Path) -> None:
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(_result("a.cs"))

    connection = sqlite3.connect(_db(tmp_path))
    connection.execute("UPDATE meta SET value = ? WHERE key = 'cache_version'", ("0",))
    connection.commit()
    connection.close()

    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        assert cache.all_paths() == []


def test_same_versions_keep_the_cache(tmp_path: Path) -> None:
    """Контроль к двум предыдущим тестам: без изменения версий кэш не трогается."""
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(_result("a.cs"))

    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        assert cache.all_paths() == ["a.cs"]


def test_meta_is_written_on_first_open(tmp_path: Path) -> None:
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        stored = dict(cache._connection.execute("SELECT key, value FROM meta").fetchall())

    assert stored["cache_version"] == CACHE_VERSION
    assert "0.26.0" in stored["parser_versions"]


def test_corrupt_database_is_recreated(tmp_path: Path) -> None:
    """Битый кэш стоит одного лишнего разбора, а не отказа всей команды.

    Без этого прогон падал бы с `DatabaseError`, и пользователю пришлось бы
    догадаться удалить каталог кэша.
    """
    path = _db(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a database, not even close")

    with ParseCache(path, VERSIONS) as cache:
        cache.put(_result("a.cs"))
        assert cache.all_paths() == ["a.cs"]


def test_empty_file_is_a_valid_empty_database(tmp_path: Path) -> None:
    path = _db(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()

    with ParseCache(path, VERSIONS) as cache:
        assert cache.all_paths() == []


# --------------------------------------------------------------------------------------
# Обслуживание
# --------------------------------------------------------------------------------------


def test_prune_removes_paths_that_are_gone(tmp_path: Path) -> None:
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        for name in ("a.cs", "b.cs", "c.cs"):
            cache.put(_result(name))

        assert cache.prune({"a.cs"}) == 2
        assert cache.all_paths() == ["a.cs"]


def test_prune_on_nothing_to_remove(tmp_path: Path) -> None:
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(_result("a.cs"))
        assert cache.prune({"a.cs", "b.cs"}) == 0
        assert cache.all_paths() == ["a.cs"]


def test_all_paths_is_sorted(tmp_path: Path) -> None:
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        for name in ("c.cs", "a.cs", "b.cs"):
            cache.put(_result(name))
        assert cache.all_paths() == ["a.cs", "b.cs", "c.cs"]


def test_get_any_ignores_hash(tmp_path: Path) -> None:
    """Скоуп-режим не читает файлы вне скоупа, поэтому сверять хэш не с чем."""
    result = _result("a.cs")
    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(result)

        assert cache.get_any("a.cs") == result
        assert cache.get_any("missing.cs") is None


def test_creates_missing_cache_directory(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "parse.sqlite"
    with ParseCache(path, VERSIONS):
        pass
    assert path.is_file()


def test_cache_version_bump_invalidates_old_entries(tmp_path: Path) -> None:
    """Старый кэш не должен отдавать записи без `decl_hash` (M02).

    Хэш содержимого файла при смене формата разбора не меняется, поэтому
    попадание в кэш вернуло бы запись, собранную прошлой версией моделей.
    Единственный способ это заметить — сверить версию формата.
    """
    import docpipe.cache as cache_module

    parsed = _result("S.cs", b"namespace N;\npublic class S { public void M() { } }\n")
    original = cache_module.CACHE_VERSION
    try:
        cache_module.CACHE_VERSION = "old"
        with ParseCache(_db(tmp_path), VERSIONS) as stale:
            stale.put(parsed)
            assert stale.get(parsed.path, parsed.content_hash) is not None

        cache_module.CACHE_VERSION = original
        with ParseCache(_db(tmp_path), VERSIONS) as fresh:
            assert fresh.get(parsed.path, parsed.content_hash) is None
    finally:
        cache_module.CACHE_VERSION = original


def test_declarations_carry_decl_hash_through_cache(tmp_path: Path) -> None:
    parsed = _result("S.cs", b"namespace N;\npublic class S { public void M() { } }\n")

    with ParseCache(_db(tmp_path), VERSIONS) as cache:
        cache.put(parsed)
        restored = cache.get(parsed.path, parsed.content_hash)

    assert restored is not None
    assert restored.declarations[0].decl_hash == parsed.declarations[0].decl_hash
    assert restored.declarations[0].decl_hash.startswith("sha256:")
