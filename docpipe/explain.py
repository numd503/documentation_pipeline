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
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.classify import Ruleset
from docpipe.discovery import matches_glob
from docpipe.hashing import stable_json_dumps
from docpipe.model import DocNode, Symbol
from docpipe.stats import (
    PAGE_COVERED,
    STATE_TITLES,
    Decision,
    absorbed_page_refs,
    decide,
    documented_base_types,
    plural,
)

ANY = "any"

# Сколько публичных членов показывать. Имена методов grid-сервиса — это контракт
# (их вызывают по имени через прокси), поэтому они и попадают в вывод; но полный
# список членов крупного типа вытесняет с экрана всё остальное.
_MEMBERS = 8

# Символы, по которым значение `--path` считается глобом, — те же, что
# у `fnmatch`. Без них значение — файл или каталог.
_GLOB_CHARS: Final = "*?["


@dataclass(frozen=True)
class Row:
    """Символ вместе с принятым про него решением."""

    symbol: Symbol
    decision: Decision

    # `(id, заголовок)` страницы, внутри документа которой описан символ.
    # Только у `page_covered`. `Decision.page` хранит один заголовок, а по
    # заголовку узел в манифесте не найти: он не уникален.
    page: tuple[str, str] | None = None


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


def _matches_path(symbol: Symbol, path: str) -> bool:
    """Совпал ли с `--path` хотя бы один источник символа.

    «Хотя бы один» — как у предиката `path_glob`: `partial class` из двух
    файлов попадает в выборку каталога, где лежит любая его часть, и одной
    строкой, а не двумя.

    Без символов глоба значение — файл или каталог. Префикс берётся вместе
    с разделителем: голая строка дала бы `src/App` → `src/AppTests/…`.
    С символами глоба — `matches_glob`, тот же, что у `path_glob`, со всеми
    его свойствами (`*` проходит через `/`): ответ на «попадает ли файл
    под глоб» в инструменте один.
    """
    return any(path_matches(source.path, path) for source in symbol.sources)


def is_glob(value: str) -> bool:
    """Глоб ли значение `--path`: есть символы `fnmatch`. Иначе — файл или каталог."""
    return any(char in value for char in _GLOB_CHARS)


def path_matches(path: str, target: str) -> bool:
    """Попадает ли репо-относительный путь под `target` — файл, каталог или глоб.

    Один ответ для `symbols --path` и `setup explain`: агент проверяет
    каталог одной командой и переносит его в другую, и разные правила
    совпадения дали бы ему два разных набора файлов.
    """
    if is_glob(target):
        return matches_glob(path, target)
    # Хвостовой `/` — обычная запись каталога. Без нормализации `Services/`
    # молча не совпал бы ни с чем: префикс превратился бы в `Services//`.
    directory = target.rstrip("/") or target
    return path == directory or path.startswith(directory + "/")


def select(
    index: dict[str, Symbol],
    nodes: list[DocNode],
    ruleset: Ruleset,
    enrolled: set[str] | None = None,
    *,
    state: str = "undecided",
    module: str = "",
    namespace: str = "",
    path: str = "",
    rule: str = "",
    kind: str = "",
    limit: int = 0,
    offset: int = 0,
) -> Selection:
    """Отобрать символы по состоянию решения и фильтрам.

    Решение считается той же `decide`, что и в отчёте, — иначе `--stats` и эта
    выборка расходились бы в числах, и доверять было бы нельзя ни одному.

    `offset` и `limit` — страница выборки по FQN (`limit` 0 — до конца);
    `total` — сколько нашлось до страницы. Страница режется здесь, а не
    у вызывающего: у CLI и сервера настройки (S27) она обязана быть одной.
    """
    documented_bases = documented_base_types(nodes)
    pages = absorbed_page_refs(nodes)
    absorbed = {fqn: title for fqn, (_, title) in pages.items()}

    rows: list[Row] = []
    for symbol in index.values():
        decision = decide(symbol, ruleset, enrolled, documented_bases, absorbed)
        if state != ANY and decision.state != state:
            continue
        if module and not _matches_module(symbol, module):
            continue
        if namespace and not symbol.namespace.startswith(namespace):
            continue
        if path and not _matches_path(symbol, path):
            continue
        if kind and decision.kind != kind:
            continue
        if rule and rule not in _rules_of(decision):
            continue
        page = pages.get(symbol.fqn) if decision.state == PAGE_COVERED else None
        rows.append(Row(symbol=symbol, decision=decision, page=page))

    rows.sort(key=lambda row: row.symbol.fqn)
    window = rows[offset:]
    return Selection(
        rows=window[:limit] if limit else window,
        total=len(rows),
        description=_description(state, module, namespace, path, rule, kind),
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


def _description(state: str, module: str, namespace: str, path: str, rule: str, kind: str) -> str:
    """Строка фильтров для заголовка: вывод должен объяснять сам себя."""
    parts = [STATE_TITLES.get(state, state) if state != ANY else "все состояния"]
    parts += [
        f"{label} ~ {value}"
        for label, value in (
            ("модуль", module),
            ("namespace", namespace),
            ("путь", path),
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
        # Победитель — отдельно от остальных совпавших: по одному списку
        # не видно, какое правило дало вид, а вопрос «почему выиграло не то»
        # задают именно здесь.
        others = [rule for rule in decision.matched_rules if rule != decision.winner_rule]
        tail = f" (совпали также: {', '.join(others)})" if others else ""
        return f"{STATE_TITLES[decision.state]}: {decision.kind} по {decision.winner_rule}{tail}"
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


# --------------------------------------------------------------------------------------
# Отчёт структурой: `symbols --format json`
# --------------------------------------------------------------------------------------

SYMBOLS_SCHEMA_VERSION: Final = "1.0"


class _Report(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExclusionRef(_Report):
    """Решение «не документируем», отсеявшее символ: какое правило и почему."""

    id: str
    reason: str


class PageRef(_Report):
    """Страница, внутри документа которой описан символ. `id` — узел манифеста."""

    id: str
    title: str


class SymbolRow(_Report):
    """Символ и решение о нём — строка `symbols --format json`.

    `rules` — все причастные правила (совпавшие классификации и отсев);
    `winner_rule` — то, что дало вид. Охват правила считается по победам:
    по `rules` правило, совпадающее со всем и нигде не выигрывающее,
    выглядело бы самым нагруженным.
    """

    fqn: str
    name: str
    type_kind: str
    namespace: str
    module: str
    modifiers: list[str]
    base_types_raw: list[str]
    base_type_closure: list[str]
    attributes: list[str]
    public_members: list[str]
    sources: list[str]
    state: str
    kind: str | None
    rules: list[str]
    winner_rule: str | None
    exclusion: ExclusionRef | None
    page: PageRef | None


class SymbolsReport(_Report):
    """Выборка символов структурой. `total` — сколько нашлось до `limit`, `shown` — после."""

    schema_version: Literal["1.0"] = SYMBOLS_SCHEMA_VERSION
    total: int
    shown: int
    filters: str
    symbols: list[SymbolRow]


def _symbol_row(row: Row) -> SymbolRow:
    symbol, decision, exclusion = row.symbol, row.decision, row.decision.exclusion
    return SymbolRow(
        fqn=symbol.fqn,
        name=symbol.name,
        type_kind=symbol.type_kind,
        namespace=symbol.namespace,
        module=symbol.module,
        modifiers=symbol.modifiers,
        base_types_raw=symbol.base_types_raw,
        base_type_closure=symbol.base_type_closure,
        attributes=[attribute.name for attribute in symbol.attributes],
        public_members=[member.name for member in symbol.members if "public" in member.modifiers],
        sources=[source.path for source in symbol.sources],
        state=decision.state,
        kind=decision.kind,
        rules=_rules_of(decision),
        winner_rule=decision.winner_rule,
        exclusion=(ExclusionRef(id=exclusion.id, reason=exclusion.reason) if exclusion else None),
        page=PageRef(id=row.page[0], title=row.page[1]) if row.page else None,
    )


def build_symbols_report(selection: Selection) -> SymbolsReport:
    """Выборка моделью. Её же позовёт инструмент сервера настройки (S27).

    Строки уже упорядочены `select` по FQN, и порядок здесь не меняется.
    """
    return SymbolsReport(
        total=selection.total,
        shown=len(selection.rows),
        filters=selection.description,
        symbols=[_symbol_row(row) for row in selection.rows],
    )


def selection_json(selection: Selection) -> str:
    """То же машинно: чтобы выборку можно было прогнать через jq или скрипт."""
    return stable_json_dumps(build_symbols_report(selection).model_dump(mode="json"))
