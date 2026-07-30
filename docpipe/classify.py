"""Движок правил классификации: `Symbol` -> вид сущности и шаблон.

Здесь проходит граница пайплайна. Всё, что решается по синтаксису — атрибуты,
базовые типы, конвенции имён, — решается правилами из YAML, детерминированно
и проверяемо. Всё, что требует понимания смысла, уходит агенту шага 3.

Правила — данные, а не код: добавление вида сущности не должно требовать правки
Python. Поэтому набор предикатов фиксирован и мал, а сложность выражается
вложенностью `any`/`all`.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from docpipe.discovery import matches_glob
from docpipe.model import Symbol
from docpipe.ruleset import (
    COMBINATORS,
    PredicateTable,
    evaluate,
    load_rule_items,
    pick_winner,
    validate_condition,
)


@dataclass(frozen=True)
class Classification:
    """Результат классификации.

    `matched_rules` содержит **все** совпавшие правила, а не только победившее:
    без этого настройка набора правил превращается в гадание — не видно,
    что ещё сработало и почему выиграло не оно.
    """

    kind: str
    template: str
    matched_rules: list[str]


@dataclass(frozen=True)
class ExcludeRule:
    """Решение «не документируем» с причиной. Проверяется до правил классификации.

    `reason` обязателен. Без него отчёт снова показывает безымянное число
    «отсеяно 4820», а именно от него и уходим: настройщику нужно видеть, по
    какому решению отсеян символ, чтобы отличить решённое от неразобранного.
    """

    id: str
    reason: str
    priority: int
    when: dict[str, Any]


@dataclass(frozen=True)
class Exclusion:
    """Что не документируется вовсе.

    `require_public` остался полем, а не правилом, по двум причинам. Первая
    техническая: отрицания в движке правил нет намеренно, и «не public» через
    его предикаты не выражается. Вторая содержательная: это не решение о группе
    типов, а глобальный переключатель «документируем ли мы внутреннюю кухню»,
    и держать его на одном уровне с решениями значило бы их запутать.
    """

    require_public: bool = False
    rules: list[ExcludeRule] = field(default_factory=list)


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    template: str
    priority: int
    when: dict[str, Any]


@dataclass(frozen=True)
class Ruleset:
    version: str
    ruleset_version: str
    exclude: Exclusion
    rules: list[Rule]


# --------------------------------------------------------------------------------------
# Сопоставление имён базовых типов
# --------------------------------------------------------------------------------------


def base_type_candidates(text: str) -> set[str]:
    """Имена, под которыми базовый тип может быть записан в правиле.

    Наивное «последний сегмент после точки» работает для
    `Sample.Common.Web.BaseApiController`, но ломается на fluent-базах,
    которые реально встречаются в коде:

        EndpointBaseAsync.WithRequest<AuthenticateRequest>.WithActionResult<AuthenticateResponse>

    Здесь значимо **первое** звено, а последний сегмент даёт бессмысленное
    `WithActionResult`. Различаются эти случаи по признаку «точка после первого `<`»:
    у квалифицированного имени точки идут до дженериков, у fluent-базы — после.

    Условие с `<` обязательно. Без него `Sample.Common.Web.BaseApiController` дал бы
    кандидата `Sample`, и правило `base_type: ["Sample"]` совпало бы со всем подряд.
    """
    head = text.split("<")[0]
    candidates = {text, head, head.split(".")[-1]}
    if "<" in text and "." in text[text.index("<") :]:
        candidates.add(head.split(".")[0])
    return candidates


def _matches_any_type(values: list[str], texts: list[str]) -> bool:
    return any(value in base_type_candidates(text) for text in texts for value in values)


# --------------------------------------------------------------------------------------
# Предикаты
# --------------------------------------------------------------------------------------


def _attribute(symbol: Symbol, values: list[str]) -> bool:
    names = {attribute.name for attribute in symbol.attributes}
    return any(value in names for value in values)


def _base_type(symbol: Symbol, values: list[str]) -> bool:
    return _matches_any_type(values, symbol.base_types + symbol.base_types_raw)


def _inherits(symbol: Symbol, values: list[str]) -> bool:
    return _matches_any_type(values, symbol.base_type_closure)


def _name_regex(symbol: Symbol, values: list[str]) -> bool:
    return any(re.fullmatch(value, symbol.name) for value in values)


def _name_suffix(symbol: Symbol, values: list[str]) -> bool:
    return any(symbol.name.endswith(value) for value in values)


def _namespace_regex(symbol: Symbol, values: list[str]) -> bool:
    return any(re.fullmatch(value, symbol.namespace) for value in values)


def _path_glob(symbol: Symbol, values: list[str]) -> bool:
    # `matches_glob`, а не `fnmatch`: последний не понимает `**` как «ноль или
    # больше сегментов», и `**/obj/**` не поймал бы файл в `obj/` в корне.
    return any(matches_glob(source.path, value) for source in symbol.sources for value in values)


def _type_kind(symbol: Symbol, values: list[str]) -> bool:
    return symbol.type_kind in values


def _modifier(symbol: Symbol, values: list[str]) -> bool:
    return any(value in symbol.modifiers for value in values)


def _has_member_with_attribute(symbol: Symbol, values: list[str]) -> bool:
    names = {attribute.name for member in symbol.members for attribute in member.attributes}
    return any(value in names for value in values)


_PREDICATES: Final[dict[str, Any]] = {
    "attribute": _attribute,
    "base_type": _base_type,
    "inherits": _inherits,
    "name_regex": _name_regex,
    "name_suffix": _name_suffix,
    "namespace_regex": _namespace_regex,
    "path_glob": _path_glob,
    "type_kind": _type_kind,
    "modifier": _modifier,
    "has_member_with_attribute": _has_member_with_attribute,
}


# Регулярки компилируются при загрузке: битая обнаружилась бы иначе посреди
# прогона, на первом символе, который до неё дошёл.
_TABLE: Final[PredicateTable] = PredicateTable(
    predicates=_PREDICATES,
    regex_keys=frozenset({"name_regex", "namespace_regex"}),
)


# --------------------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------------------

# Решение «тип не public — не документируем». Правилом не выражается: отрицания
# в движке нет. `when` пустой и никогда не вычисляется — совпадение проверяется
# кодом в `exclusion_of`.
REQUIRE_PUBLIC = ExcludeRule(
    id="exclude.require_public",
    reason="Тип не public: внутренняя реализация, а не контракт",
    priority=-4,
    when={},
)

# Краткая форма `exclude` — четыре поля без причин — разворачивается в правила
# с зарезервированными идентификаторами. Ради этого она и сохранена: набор,
# написанный до появления причин, продолжает работать и сразу получает разбивку
# по причинам отсева, пусть и с общими формулировками.
#
# Приоритеты отрицательные, поэтому правило, написанное человеком (по умолчанию
# 0), забирает атрибуцию себе: его причина всегда информативнее общей. Порядок
# между собой повторяет порядок проверок прежней реализации — иначе замеры
# «по какой причине отсеяно» из findings перестали бы сходиться.
_LEGACY: Final[dict[str, tuple[str, int, str]]] = {
    "path_glob": (
        "path_glob",
        -1,
        "Путь совпал с шаблоном отсева: сгенерированный код, тесты, миграции",
    ),
    "name_regex": ("name_regex", -2, "Имя типа совпало с шаблоном отсева"),
    "type_kind_deny": ("type_kind", -3, "Этот вид типа не документируется отдельным документом"),
}

_EXCLUDE_KEYS: Final[frozenset[str]] = frozenset({*_LEGACY, "require_public", "rules"})

# Префикс зарезервирован за развёрнутой краткой формой: иначе в таблице причин
# появились бы две строки с одним id, и понять, какая из них сработала, было бы нельзя.
_RESERVED_PREFIX: Final = "exclude."


def condition_values(when: dict[str, Any], predicate: str) -> list[str]:
    """Все значения предиката внутри условия, включая вложенные `any`/`all`.

    Нужна там, где по набору правил задают вопрос «а отсекается ли такой путь»:
    без обхода дерева ответ зависел бы от того, записано условие плоско или
    через комбинатор, то есть от формы записи, а не от смысла.
    """
    key, value = next(iter(when.items()))
    if key in COMBINATORS:
        return [item for child in value for item in condition_values(child, predicate)]
    return list(value) if key == predicate else []


def _load_exclusion(raw: Any, path: Path) -> Exclusion:
    """Разобрать секцию `exclude` в обеих формах: с правилами и краткой."""
    if raw is None:
        return Exclusion()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: секция `exclude` должна быть словарём")
    if unknown := sorted(set(raw) - _EXCLUDE_KEYS):
        raise ValueError(
            f"{path}: неизвестные поля в `exclude`: {', '.join(unknown)}; "
            f"известны: {', '.join(sorted(_EXCLUDE_KEYS))}"
        )

    rules = [
        ExcludeRule(
            id=f"{_RESERVED_PREFIX}{field_name}",
            reason=reason,
            priority=priority,
            when={predicate: list(raw[field_name])},
        )
        for field_name, (predicate, priority, reason) in _LEGACY.items()
        if raw.get(field_name)
    ]

    for item in load_rule_items(raw.get("rules"), f"{path}:exclude", {"id", "reason", "when"}):
        if item["id"].startswith(_RESERVED_PREFIX):
            raise ValueError(
                f"{path}: id правила отсева {item['id']!r} начинается с "
                f"{_RESERVED_PREFIX!r} — префикс зарезервирован за краткой формой"
            )
        rules.append(
            ExcludeRule(
                id=item["id"],
                reason=str(item["reason"]),
                priority=int(item.get("priority", 0)),
                when=item["when"],
            )
        )

    # Проверка условий одна на оба случая: краткая форма получает ту же
    # диагностику битой регулярки при загрузке, что была у неё до правки.
    for rule in rules:
        validate_condition(rule.when, f"{path}:exclude.{rule.id}.when", _TABLE)

    return Exclusion(require_public=bool(raw.get("require_public", False)), rules=rules)


def load_ruleset(path: Path) -> Ruleset:
    """Загрузить набор правил с полной проверкой структуры."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: набор правил должен быть словарём")

    exclusion = _load_exclusion(raw.get("exclude"), path)

    rules: list[Rule] = []
    for item in load_rule_items(
        raw.get("rules"), path, {"id", "kind", "template", "priority", "when"}
    ):
        validate_condition(item["when"], f"{path}:{item['id']}.when", _TABLE)
        rules.append(
            Rule(
                id=item["id"],
                kind=item["kind"],
                template=item["template"],
                priority=int(item["priority"]),
                when=item["when"],
            )
        )

    return Ruleset(
        version=str(raw.get("version", "1")),
        ruleset_version=str(raw["ruleset_version"]),
        exclude=exclusion,
        rules=rules,
    )


# --------------------------------------------------------------------------------------
# Применение
# --------------------------------------------------------------------------------------


def exclusion_of(symbol: Symbol, ruleset: Ruleset) -> ExcludeRule | None:
    """Решение «не документируем» для символа, или `None`, если такого решения нет.

    Возвращается **одно** правило — то, чья причина попадёт в отчёт. При
    нескольких совпавших побеждает больший `priority`, при равном — меньший `id`
    (`pick_winner`). Без этого счётчики по причинам зависели бы от порядка строк
    в YAML: перестановка двух правил меняла бы цифры, которые идут в журнал и в CI.
    """
    matched = [rule for rule in ruleset.exclude.rules if evaluate(rule.when, symbol, _TABLE)]
    if ruleset.exclude.require_public and "public" not in symbol.modifiers:
        matched.append(REQUIRE_PUBLIC)
    return pick_winner(matched) if matched else None


def is_excluded(symbol: Symbol, ruleset: Ruleset) -> bool:
    """Отбрасывается ли символ секцией `exclude`."""
    return exclusion_of(symbol, ruleset) is not None


def classify(symbol: Symbol, ruleset: Ruleset) -> Classification | None:
    """Вид и шаблон символа. `None` — исключён или не подошёл ни под одно правило.

    Вычисляются **все** правила, а не первое совпавшее: победитель определяется
    приоритетом, а остальные попадают в `matched_rules` для аудита.
    """
    if is_excluded(symbol, ruleset):
        return None

    matched = [rule for rule in ruleset.rules if evaluate(rule.when, symbol, _TABLE)]
    if not matched:
        return None

    winner = pick_winner(matched)
    return Classification(
        kind=winner.kind,
        template=winner.template,
        matched_rules=sorted(rule.id for rule in matched),
    )
