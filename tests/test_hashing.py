"""Проверка примитивов детерминизма (T02)."""

import pytest

from docpipe.hashing import content_hash, slugify, stable_hash, stable_json_dumps

# Таблица из плана — фиксирует контракт slugify.
SLUG_CASES = [
    ("PricingController", "pricing-controller"),
    ("HTTPClientFactory", "http-client-factory"),
    ("IPricingProvider<T>", "i-pricing-provider"),
    ("Repository2", "repository2"),
    ("_weird__Name_", "weird-name"),
    ("ABC", "abc"),
    ("<>", "unnamed"),
]


@pytest.mark.parametrize(("source", "expected"), SLUG_CASES)
def test_slugify_table(source: str, expected: str) -> None:
    assert slugify(source) == expected


def test_slugify_drops_type_parameters() -> None:
    """Generic и не-generic версии дают один slug; развести их — работа tree.py."""
    assert slugify("Repository<TEntity, TKey>") == slugify("Repository")


def test_slugify_never_returns_empty() -> None:
    """Пустой slug сделал бы невалидное имя файла."""
    for source in ["", "<T>", "___", "---", "!!!"]:
        assert slugify(source) == "unnamed"


def test_slugify_is_idempotent() -> None:
    """Повторное применение к результату ничего не меняет."""
    for source, expected in SLUG_CASES:
        assert slugify(expected) == expected, source


def test_content_hash_format() -> None:
    result = content_hash(b"")
    assert result.startswith("sha256:")
    # sha256 в hex — всегда 64 символа.
    assert len(result) == len("sha256:") + 64
    assert content_hash(b"a") != content_hash(b"b")


def test_stable_json_dumps_sorts_keys() -> None:
    assert stable_json_dumps({"b": 1, "a": 2}) == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_stable_json_dumps_ends_with_newline() -> None:
    assert stable_json_dumps({}).endswith("\n")
    assert stable_json_dumps([]).endswith("\n")


def test_stable_json_dumps_keeps_non_ascii_readable() -> None:
    """ensure_ascii=False: кириллица в манифесте должна читаться, а не быть \\uXXXX."""
    assert "Сервис" in stable_json_dumps({"title": "Сервис"})


def test_stable_hash_ignores_key_order() -> None:
    """Главное свойство: порядок вставки в словарь не влияет на хэш."""
    first = {"a": 1, "b": {"x": 1, "y": 2}}
    second = {"b": {"y": 2, "x": 1}, "a": 1}
    assert stable_hash(first) == stable_hash(second)


def test_stable_hash_detects_real_changes() -> None:
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})
    assert stable_hash({"a": 1}) != stable_hash({"b": 1})
    # Список — порядок значим, в отличие от ключей словаря.
    assert stable_hash([1, 2]) != stable_hash([2, 1])
