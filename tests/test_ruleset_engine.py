"""Общий движок правил: комбинаторы, диагностика опечаток, выбор победителя (M01).

Проверяется на **искусственной** таблице из двух предикатов, а не на правилах
классификации: движок про предметную область ничего не знает, и тест, завязанный
на `Symbol`, проверял бы заодно классификацию — то есть не проверял бы движок.
"""

from dataclasses import dataclass

import pytest

from docpipe.ruleset import (
    COMBINATORS,
    PredicateTable,
    evaluate,
    load_rule_items,
    pick_winner,
    validate_condition,
)


@dataclass(frozen=True)
class _Subject:
    name: str
    tags: list[str]


def _name_is(subject: _Subject, values: list[str]) -> bool:
    return subject.name in values


def _tag(subject: _Subject, values: list[str]) -> bool:
    return any(value in subject.tags for value in values)


def _name_regex(subject: _Subject, values: list[str]) -> bool:
    import re

    return any(re.fullmatch(value, subject.name) for value in values)


TABLE = PredicateTable(
    predicates={"name_is": _name_is, "tag": _tag, "name_regex": _name_regex},
    regex_keys=frozenset({"name_regex"}),
)

SUBJECT = _Subject(name="Alpha", tags=["red", "small"])


# --------------------------------------------------------------------------------------
# Вычисление
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"name_is": ["Alpha"]}, True),
        ({"name_is": ["Beta"]}, False),
        ({"tag": ["red"]}, True),
        ({"tag": ["blue", "small"]}, True),
        ({"any": [{"name_is": ["Beta"]}, {"tag": ["red"]}]}, True),
        ({"any": [{"name_is": ["Beta"]}, {"tag": ["blue"]}]}, False),
        ({"all": [{"name_is": ["Alpha"]}, {"tag": ["red"]}]}, True),
        ({"all": [{"name_is": ["Alpha"]}, {"tag": ["blue"]}]}, False),
        (
            {"all": [{"tag": ["red"]}, {"any": [{"name_is": ["Beta"]}, {"tag": ["small"]}]}]},
            True,
        ),
    ],
)
def test_evaluate(condition: dict[str, object], expected: bool) -> None:
    assert evaluate(condition, SUBJECT, TABLE) is expected


def test_combinators_nest_both_ways() -> None:
    """`any` внутри `all` и наоборот — иначе наборы правил разойдутся
    в выразительности, а разница обнаружится только у настройщика."""
    deep = {"any": [{"all": [{"name_is": ["Alpha"]}, {"tag": ["red"]}]}, {"tag": ["blue"]}]}

    assert evaluate(deep, SUBJECT, TABLE) is True
    assert sorted(COMBINATORS) == ["all", "any"]


# --------------------------------------------------------------------------------------
# Диагностика: опечатка обязана быть понятной ошибкой,
# а не молча не срабатывающим правилом
# --------------------------------------------------------------------------------------


def test_unknown_predicate_lists_the_known_ones() -> None:
    with pytest.raises(ValueError, match="неизвестный предикат `nmae_is`") as exc:
        validate_condition({"nmae_is": ["Alpha"]}, "r.when", TABLE)

    assert "name_is, name_regex, tag" in str(exc.value)


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"any": []}, "`any` требует непустой список условий"),
        ({"all": []}, "`all` требует непустой список условий"),
        ({"any": "не список"}, "`any` требует непустой список условий"),
        ({"name_is": "строка"}, "ожидается список строк"),
        ({"name_is": [1, 2]}, "ожидается список строк"),
        ({"name_regex": ["("]}, "неверное регулярное выражение"),
        ({}, "словарём с одним ключом"),
        ({"a": [], "b": []}, "словарём с одним ключом"),
        ("строка", "словарём с одним ключом"),
    ],
)
def test_validate_rejects(condition: object, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        validate_condition(condition, "r.when", TABLE)


def test_error_points_at_the_nested_node() -> None:
    """Без пути до узла в сообщении опечатку в глубоком условии искать негде."""
    condition = {"all": [{"name_is": ["Alpha"]}, {"any": [{"tga": ["red"]}]}]}

    with pytest.raises(ValueError, match=r"r\.when\.all\[1\]\.any\[0\]"):
        validate_condition(condition, "r.when", TABLE)


def test_regex_is_checked_only_where_declared() -> None:
    """Значение не-regex предиката регуляркой не считается: `(` там законен."""
    validate_condition({"name_is": ["("]}, "r.when", TABLE)


# --------------------------------------------------------------------------------------
# Выбор победителя
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    id: str
    priority: int


def test_highest_priority_wins() -> None:
    rules = [_Rule("low", 10), _Rule("high", 100), _Rule("mid", 50)]

    assert pick_winner(rules).id == "high"


def test_tie_goes_to_lexicographically_smaller_id() -> None:
    """Порядок правил в файле источником решения быть не может: иначе
    перестановка строк в YAML меняла бы вывод."""
    rules = [_Rule("zeta", 50), _Rule("alpha", 50), _Rule("mid", 50)]

    assert pick_winner(rules).id == "alpha"
    assert pick_winner(list(reversed(rules))).id == "alpha"


# --------------------------------------------------------------------------------------
# Загрузка списка правил
# --------------------------------------------------------------------------------------


def test_load_rule_items() -> None:
    raw = [{"id": "a", "team": "x"}, {"id": "b", "team": "y"}]

    assert [item["id"] for item in load_rule_items(raw, "f.yaml", {"id", "team"})] == ["a", "b"]


def test_load_rule_items_accepts_missing_section() -> None:
    """Секция, у которой закомментированы все записи, разбирается YAML как `None`."""
    assert load_rule_items(None, "f.yaml", {"id"}) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([{"id": "a"}], "правило #0 без полей \\['team'\\]"),
        ([{"id": "a", "team": "x"}, {"id": "a", "team": "y"}], "повтор id правила 'a'"),
        (["строка"], "правило #0 должно быть словарём"),
        ("не список", "`rules` должен быть списком"),
    ],
)
def test_load_rule_items_rejects(raw: object, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        load_rule_items(raw, "f.yaml", {"id", "team"})
