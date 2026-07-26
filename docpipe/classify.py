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
class Exclusion:
    """Что не документируется вовсе. Проверяется до правил."""

    path_glob: list[str] = field(default_factory=list)
    name_regex: list[str] = field(default_factory=list)
    type_kind_deny: list[str] = field(default_factory=list)
    require_public: bool = False


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

_COMBINATORS: Final[frozenset[str]] = frozenset({"any", "all"})


# --------------------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------------------


def _validate_condition(node: Any, where: str) -> None:
    """Проверить узел `when` при загрузке, а не при первом применении.

    Опечатка в имени предиката иначе означала бы правило, которое просто
    никогда не срабатывает: набор молча теряет вид сущности, и понять это
    можно только по счётчику `unclassified`.
    """
    if not isinstance(node, dict) or len(node) != 1:
        raise ValueError(
            f"{where}: узел условия должен быть словарём с одним ключом, получено {node!r}"
        )

    key, value = next(iter(node.items()))
    if key in _COMBINATORS:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{where}: `{key}` требует непустой список условий")
        for index, child in enumerate(value):
            _validate_condition(child, f"{where}.{key}[{index}]")
        return

    if key not in _PREDICATES:
        known = ", ".join(sorted(_PREDICATES))
        raise ValueError(f"{where}: неизвестный предикат `{key}`; известны: {known}")

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}.{key}: ожидается список строк, получено {value!r}")

    if key in ("name_regex", "namespace_regex"):
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"{where}.{key}: неверное регулярное выражение {pattern!r}: {exc}"
                ) from exc


def load_ruleset(path: Path) -> Ruleset:
    """Загрузить набор правил с полной проверкой структуры."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: набор правил должен быть словарём")

    exclusion = Exclusion(**(raw.get("exclude") or {}))
    for pattern in exclusion.name_regex:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{path}: exclude.name_regex {pattern!r}: {exc}") from exc

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("rules") or []):
        missing = {"id", "kind", "template", "priority", "when"} - set(item)
        if missing:
            raise ValueError(f"{path}: правило #{index} без полей {sorted(missing)}")
        if item["id"] in seen:
            raise ValueError(f"{path}: повтор id правила {item['id']!r}")
        seen.add(item["id"])

        _validate_condition(item["when"], f"{path}:{item['id']}.when")
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


def _evaluate(node: dict[str, Any], symbol: Symbol) -> bool:
    key, value = next(iter(node.items()))
    if key == "any":
        return any(_evaluate(child, symbol) for child in value)
    if key == "all":
        return all(_evaluate(child, symbol) for child in value)
    return bool(_PREDICATES[key](symbol, value))


def is_excluded(symbol: Symbol, ruleset: Ruleset) -> bool:
    """Отбрасывается ли символ секцией `exclude`."""
    exclusion = ruleset.exclude
    if any(
        matches_glob(source.path, glob) for source in symbol.sources for glob in exclusion.path_glob
    ):
        return True
    if any(re.fullmatch(pattern, symbol.name) for pattern in exclusion.name_regex):
        return True
    if symbol.type_kind in exclusion.type_kind_deny:
        return True
    return exclusion.require_public and "public" not in symbol.modifiers


def classify(symbol: Symbol, ruleset: Ruleset) -> Classification | None:
    """Вид и шаблон символа. `None` — исключён или не подошёл ни под одно правило.

    Вычисляются **все** правила, а не первое совпавшее: победитель определяется
    приоритетом, а остальные попадают в `matched_rules` для аудита.
    """
    if is_excluded(symbol, ruleset):
        return None

    matched = [rule for rule in ruleset.rules if _evaluate(rule.when, symbol)]
    if not matched:
        return None

    # При равном приоритете побеждает лексикографически меньший id: порядок
    # правил в файле источником решения быть не может — иначе перестановка
    # строк в YAML меняла бы манифест.
    winner = min(matched, key=lambda rule: (-rule.priority, rule.id))
    return Classification(
        kind=winner.kind,
        template=winner.template,
        matched_rules=sorted(rule.id for rule in matched),
    )
