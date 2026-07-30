"""Движок правил: условия, вычисление, выбор победителя.

Один синтаксис условий и **одна диагностика** на все наборы правил проекта:
классификацию типов (`rules/dotnet.yaml`) и владение (`ownership.yaml`).

Ради чего вынесено. Настройщик правил классификации и настройщик границ команд —
часто один человек. Два движка означают два набора сообщений об ошибках, два
поведения при опечатке и неизбежный дрейф: «в `rules` `any` можно вкладывать,
а в `ownership` почему-то нет».

Решающий довод — именно диагностика. `validate_condition` превращает опечатку
в понятную ошибку вместо молча не срабатывающего правила, а такое правило
неотличимо от «набор не покрывает этот случай»: видно только по счётчику
«решение не принято», и то не сразу.

Чего здесь **нет**: предикатов. Они знают про предметную область (`Symbol`,
`DocNode`) и живут у своего набора правил. Сюда приходит готовая таблица.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

# Предикат получает предмет проверки и список значений из правила.
# Тип предмета намеренно `Any`: движок про него ничего не знает.
Predicate = Callable[[Any, list[str]], bool]

COMBINATORS: Final[frozenset[str]] = frozenset({"any", "all"})


@dataclass(frozen=True)
class PredicateTable:
    """Набор предикатов одного вида правил.

    `regex_keys` перечисляет предикаты, значения которых компилируются как
    регулярные выражения при загрузке. Проверять их на месте применения поздно:
    битая регулярка обнаружилась бы на первом же символе, который до неё дошёл,
    то есть посреди прогона и не всегда.
    """

    predicates: Mapping[str, Predicate]
    regex_keys: frozenset[str] = field(default_factory=frozenset)


class Prioritised(Protocol):
    """Правило, у которого есть приоритет и идентификатор."""

    @property
    def id(self) -> str: ...

    @property
    def priority(self) -> int: ...


def validate_condition(node: Any, where: str, table: PredicateTable) -> None:
    """Проверить узел `when` при загрузке, а не при первом применении.

    Опечатка в имени предиката иначе означала бы правило, которое просто
    никогда не срабатывает: набор молча теряет вид сущности, и понять это
    можно только по счётчику «решение не принято».
    """
    if not isinstance(node, dict) or len(node) != 1:
        raise ValueError(
            f"{where}: узел условия должен быть словарём с одним ключом, получено {node!r}"
        )

    key, value = next(iter(node.items()))
    if key in COMBINATORS:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{where}: `{key}` требует непустой список условий")
        for index, child in enumerate(value):
            validate_condition(child, f"{where}.{key}[{index}]", table)
        return

    if key not in table.predicates:
        known = ", ".join(sorted(table.predicates))
        raise ValueError(f"{where}: неизвестный предикат `{key}`; известны: {known}")

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}.{key}: ожидается список строк, получено {value!r}")

    if key in table.regex_keys:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"{where}.{key}: неверное регулярное выражение {pattern!r}: {exc}"
                ) from exc


def evaluate(node: dict[str, Any], subject: Any, table: PredicateTable) -> bool:
    """Вычислить условие. Структура уже проверена `validate_condition`."""
    key, value = next(iter(node.items()))
    if key == "any":
        return any(evaluate(child, subject, table) for child in value)
    if key == "all":
        return all(evaluate(child, subject, table) for child in value)
    return bool(table.predicates[key](subject, value))


def pick_winner[R: Prioritised](matched: list[R]) -> R:
    """Победитель среди совпавших правил.

    При равном приоритете побеждает лексикографически меньший `id`: порядок
    правил в файле источником решения быть не может — иначе перестановка строк
    в YAML меняла бы вывод.
    """
    return min(matched, key=lambda rule: (-rule.priority, rule.id))


def load_rule_items(raw: Any, path: Any, required: set[str]) -> list[dict[str, Any]]:
    """Проверить список правил: обязательные поля и повторы `id`.

    Возвращает сырые словари: собирать из них свою модель — дело набора правил,
    у каждого она своя (`kind`/`template` у классификации, `team` у владения).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: `rules` должен быть списком")

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: правило #{index} должно быть словарём")

        missing = required - set(item)
        if missing:
            raise ValueError(f"{path}: правило #{index} без полей {sorted(missing)}")
        if item["id"] in seen:
            raise ValueError(f"{path}: повтор id правила {item['id']!r}")

        seen.add(item["id"])
        items.append(item)

    return items
