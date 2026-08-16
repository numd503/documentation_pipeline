"""Выборка символов по состоянию решения — рабочий инструмент настройки правил.

Отчёт `scan --stats` отвечает на вопрос «сколько», но не на вопрос «что именно».
Пока непокрытого тысячи, «сколько» и есть главное. Как только настройка доходит
до одного проекта и остатка в единицы символов, нужно ровно обратное: увидеть эти
символы и то, по чему для них пишется предикат.

Поэтому здесь показываются не все поля символа, а решающие: замыкание
наследования, атрибуты, публичные члены и путь. Остальное — шум, который
заставляет листать вывод вместо того, чтобы принять решение.
"""

from dataclasses import dataclass

from docpipe.classify import Ruleset
from docpipe.discovery import matches_glob
from docpipe.hashing import stable_json_dumps
from docpipe.model import DocNode, Symbol
from docpipe.stats import (
    STATE_TITLES,
    Decision,
    absorbed_pages,
    decide,
    documented_base_types,
    plural,
)

ANY = "any"

# Сколько публичных членов показывать. Имена методов grid-сервиса — это контракт
# (их вызывают по имени через прокси), поэтому они и попадают в вывод; но полный
# список членов крупного типа вытесняет с экрана всё остальное.
_MEMBERS = 8


@dataclass(frozen=True)
class Row:
    """Символ вместе с принятым про него решением."""

    symbol: Symbol
    decision: Decision


@dataclass(frozen=True)
class Selection:
    """Результат выборки. `total` — сколько нашлось до применения `limit`."""

    rows: list[Row]
    total: int
    description: str


def _matches_module(symbol: Symbol, pattern: str) -> bool:
    """Глоб по пути `.csproj`, если в шаблоне есть `*`, иначе подстрока.

    Глоб — чтобы значение можно было скопировать из `enrolled` в `docpipe.yaml`
    и получить ту же выборку. Подстрока — чтобы в обычном случае хватало имени
    проекта, без шаблона вокруг него.
    """
    if "*" in pattern:
        return matches_glob(symbol.module, pattern)
    return pattern in symbol.module


def select(
    index: dict[str, Symbol],
    nodes: list[DocNode],
    ruleset: Ruleset,
    enrolled: set[str] | None = None,
    *,
    state: str = "undecided",
    module: str = "",
    namespace: str = "",
    rule: str = "",
    kind: str = "",
    limit: int = 0,
) -> Selection:
    """Отобрать символы по состоянию решения и фильтрам.

    Решение считается той же `decide`, что и в отчёте, — иначе `--stats` и эта
    выборка расходились бы в числах, и доверять было бы нельзя ни одному.
    """
    documented_bases = documented_base_types(nodes)
    absorbed = absorbed_pages(nodes)

    rows: list[Row] = []
    for symbol in index.values():
        decision = decide(symbol, ruleset, enrolled, documented_bases, absorbed)
        if state != ANY and decision.state != state:
            continue
        if module and not _matches_module(symbol, module):
            continue
        if namespace and not symbol.namespace.startswith(namespace):
            continue
        if kind and decision.kind != kind:
            continue
        if rule and rule not in _rules_of(decision):
            continue
        rows.append(Row(symbol=symbol, decision=decision))

    rows.sort(key=lambda row: row.symbol.fqn)
    return Selection(
        rows=rows[:limit] if limit else rows,
        total=len(rows),
        description=_description(state, module, namespace, rule, kind),
    )


def _rules_of(decision: Decision) -> list[str]:
    """Идентификаторы всех правил, причастных к решению.

    И отсев, и классификация: `--rule` спрашивают, чтобы проверить только что
    написанное правило, и помнить при этом, в какой оно секции, человек не должен.
    """
    ids = list(decision.matched_rules)
    if decision.exclusion is not None:
        ids.append(decision.exclusion.id)
    return ids


def _description(state: str, module: str, namespace: str, rule: str, kind: str) -> str:
    """Строка фильтров для заголовка: вывод должен объяснять сам себя."""
    parts = [STATE_TITLES.get(state, state) if state != ANY else "все состояния"]
    parts += [
        f"{label} ~ {value}"
        for label, value in (
            ("модуль", module),
            ("namespace", namespace),
            ("правило", rule),
            ("вид", kind),
        )
        if value
    ]
    return "  ·  ".join(parts)


# --------------------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------------------


def _location(symbol: Symbol) -> str:
    """Первый источник со строкой объявления, плюс счётчик остальных.

    `partial class` живёт в нескольких файлах, и умолчать об этом нельзя:
    `path_glob` истинен, если совпал хотя бы один источник.
    """
    if not symbol.sources:
        return "—"
    first = symbol.sources[0]
    rest = len(symbol.sources) - 1
    return f"{first.path}:{first.start}" + (f"  (+{rest} файл(ов))" if rest else "")


def _public_members(symbol: Symbol) -> str:
    names = [member.name for member in symbol.members if "public" in member.modifiers]
    if not names:
        return "—"
    shown = ", ".join(names[:_MEMBERS])
    return shown + (f", и ещё {len(names) - _MEMBERS}" if len(names) > _MEMBERS else "")


def _decision_line(decision: Decision) -> str:
    if decision.page:
        return f"{STATE_TITLES[decision.state]}: {decision.page} ({decision.kind})"
    if decision.exclusion is not None:
        return (
            f"{STATE_TITLES[decision.state]}: {decision.exclusion.id} — {decision.exclusion.reason}"
        )
    if decision.kind is not None:
        return (
            f"{STATE_TITLES[decision.state]}: {decision.kind} ({', '.join(decision.matched_rules)})"
        )
    return STATE_TITLES[decision.state]


def format_selection(selection: Selection) -> str:
    """Список символов: по одному блоку на символ, поля — те, по которым пишут правила."""
    header = (
        f"{plural(selection.total, 'символ', 'символа', 'символов')}  ·  {selection.description}"
    )
    if not selection.rows:
        return header + "\n\nНичего не найдено."

    blocks = [header]
    if selection.total > len(selection.rows):
        blocks[0] += f"  ·  показано {len(selection.rows)}"

    for row in selection.rows:
        symbol = row.symbol
        modifiers = " ".join(symbol.modifiers) or "—"
        blocks.append(
            "\n".join(
                [
                    "",
                    symbol.fqn,
                    f"  {symbol.type_kind}, {modifiers}",
                    f"  где             {_location(symbol)}",
                    f"  прямые базы     {', '.join(symbol.base_types_raw) or '—'}",
                    f"  замыкание       {', '.join(symbol.base_type_closure) or '—'}",
                    f"  атрибуты        {', '.join(a.name for a in symbol.attributes) or '—'}",
                    f"  public члены    {_public_members(symbol)}",
                    f"  решение         {_decision_line(row.decision)}",
                ]
            )
        )
    return "\n".join(blocks)


def selection_json(selection: Selection) -> str:
    """То же машинно: чтобы выборку можно было прогнать через jq или скрипт."""
    return stable_json_dumps(
        {
            "total": selection.total,
            "shown": len(selection.rows),
            "filters": selection.description,
            "symbols": [
                {
                    "fqn": row.symbol.fqn,
                    "name": row.symbol.name,
                    "type_kind": row.symbol.type_kind,
                    "namespace": row.symbol.namespace,
                    "module": row.symbol.module,
                    "modifiers": row.symbol.modifiers,
                    "base_types_raw": row.symbol.base_types_raw,
                    "base_type_closure": row.symbol.base_type_closure,
                    "attributes": [a.name for a in row.symbol.attributes],
                    "public_members": [
                        m.name for m in row.symbol.members if "public" in m.modifiers
                    ],
                    "sources": [s.path for s in row.symbol.sources],
                    "state": row.decision.state,
                    "kind": row.decision.kind,
                    "rules": _rules_of(row.decision),
                }
                for row in selection.rows
            ],
        }
    )
