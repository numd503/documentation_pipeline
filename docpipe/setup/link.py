"""Сводка шва кластерами (`setup link`, S21): несвязанное — группами, с подсказкой правила.

Отчёт связи (`web link`) перечисляет концы шва поштучно: на squidex до
настройки это 262 эндпоинта без вызывающего и 79 невосстановленных вызовов.
Агент контура обрезает такой вывод, и решение по одному месту за раз ему
не принять. Сводка складывает ту же категорию в кластеры по ключу (модуль,
два первых сегмента маршрута, файл, узел, причина, хост, решение) — с числом
мест и до трёх примеров, страницей с продолжением (правило 5 плана).

Источник — `SetupContext.link`, тот же `report_for_settings`, что у `web link`:
вторая сборка отчёта разошлась бы с первой на первом же ключе настройки.
Прогоны — в памяти, по текущей настройке: агент правит `docpipe.yaml` и сразу
зовёт команду ещё раз.

**Место — `(file, line)`, а не пара «вызов × узел».** Вызов, у которого нет
узла с подходящим диапазоном строк, приписан каждому узлу файла (S16, п. 6),
и счёт по записям отчёта посчитал бы его дважды. Эндпоинт — запись
`(узел, член, метод, маршрут)`: у `AcceptVerbs("GET", "POST")` одна строка
даёт два эндпоинта, и это два конца шва, а не повтор.

**Подсказка правила префикса.** Для вызовов без эндпоинта по модулю —
пара `strip_prefix`/`add_prefix`, при которой связывается точно больше всего
вызовов кластера; это то же правило `web.url_rewrite`, которое агент иначе
подбирал бы прогонами. Проверка «свяжется ли» — та же `exact_keys` по тем же
`backend_keys`, что у сведения, а маршрут переписывает та же
`route.normalize_route`. Ключ вызова в манифесте уже нормализован, и
преобразование префикса поверх него даёт то же, что поверх сырого адреса, —
пока у модуля нет своего правила: тогда ключ собран **после** него, и пара
поверх ключа была бы композицией двух правил. Такому модулю подсказки нет,
и сводка говорит почему.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.config import DocpipeConfig
from docpipe.hashing import stable_json_dumps
from docpipe.model import UnresolvedCall
from docpipe.route import RewriteRule, RouteKey, normalize_route
from docpipe.setup.context import InputError, SetupContext
from docpipe.web.link import (
    CallRef,
    DeclaredUnresolvable,
    EndpointRef,
    ExternalCall,
    ExternalEndpoint,
    LinkReport,
    backend_keys,
    exact_keys,
)

# Длина страницы по умолчанию — как у `setup candidates` (правило 5 плана).
DEFAULT_LIMIT: Final = 20

# Примеров в кластере: место и маршрут или выражение. Больше — и сводка
# снова становится поштучным списком.
EXAMPLES: Final = 3

# Ключ `prefix` — столько первых сегментов маршрута. Один сегмент (`api`)
# склеил бы весь бэк; три — разнёс бы `api/apps/{}` и `api/apps/search`.
PREFIX_SEGMENTS: Final = 2

# Сколько первых сегментов адреса вызова пробует `strip_prefix` подсказки.
# Прокси срезает голову пути (`^/pm`, `^/api/v1`) — глубже трёх не бывало.
STRIP_SEGMENTS: Final = 2

CATEGORIES: Final[tuple[str, ...]] = (
    "calls_without_endpoint",
    "calls_unresolved",
    "endpoints_without_caller",
    "almost",
    "external_targets",
    "external_callers",
    "declared_unresolvable",
)

KEYS: Final[tuple[str, ...]] = (
    "module",
    "prefix",
    "file",
    "controller",
    "reason",
    "host",
    "decision",
)

# Какие ключи у какой категории есть. Первый — умолчание `--by`. Ключа нет
# там, где у записи нет поля: у невосстановленного вызова нет ни маршрута,
# ни узла (S18 — «связи нет, узла нет»), у эндпоинта и у «почти» — хоста.
# `controller` — узел: у эндпоинта его контроллер, у вызова — узел фронта,
# который зовёт, у «почти» — контроллеры совпавших эндпоинтов. `reason` —
# причина, по которой адрес не восстановлен; `decision` — условие записи
# секции `link`, решившей конец шва, как написано.
BY_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "calls_without_endpoint": ("module", "prefix", "file", "controller", "host"),
    "calls_unresolved": ("reason", "module", "file"),
    "endpoints_without_caller": ("controller", "module", "prefix", "file"),
    "almost": ("controller", "module", "prefix", "file"),
    "external_targets": ("decision", "module", "prefix", "file", "controller", "host"),
    "external_callers": ("decision", "module", "prefix", "file", "controller"),
    "declared_unresolvable": ("decision", "reason", "module", "file"),
}

DEFAULT_CATEGORY: Final = "calls_without_endpoint"

# Почему у кластера модуля нет подсказки. Формулировки стабильные: по ним
# скилл отличает «правило уже есть» от «дело не в префиксе».
NOTE_MODULE_HAS_RULE: Final = (
    "у модуля есть запись web.url_rewrite с преобразованием: ключи вызовов собраны после неё, "
    "и пара поверх них сложила бы два правила; подсказка — для модуля без записи или с пустой"
)
NOTE_NO_GAIN: Final = (
    "ни одна пара strip_prefix/add_prefix не связывает точно больше вызовов, чем развязывает: "
    "дело не в префиксе"
)


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClusterExample(_Base):
    """Место в коде и что там: `GET api/apps`, `GET this.fileSource`, `GET api/x (List)`."""

    file: str
    line: int
    text: str


class SuggestedRewrite(_Base):
    """Запись `web.url_rewrite`, которая свяжет вызовы кластера.

    `would_link` — сколько мест кластера свяжутся точно, `would_unlink` —
    сколько мест модуля, связанных точно сейчас, это правило развяжет:
    правило одно на модуль и переписывает **все** его адреса.
    """

    module: str
    strip_prefix: str
    add_prefix: str
    would_link: int
    would_unlink: int


class LinkCluster(_Base):
    """Мест категории с одним значением ключа.

    `suggested_rewrite` — только у вызовов без эндпоинта по модулю; когда
    его нет, `rewrite_note` говорит почему (`NOTE_MODULE_HAS_RULE`,
    `NOTE_NO_GAIN`). У остальных кластеров оба пусты.
    """

    key: str
    count: int
    examples: list[ClusterExample] = Field(default_factory=list)
    suggested_rewrite: SuggestedRewrite | None = None
    rewrite_note: str = ""


class LinkClusters(_Base):
    """Сводка одной категории шва кластерами.

    `places` — мест в категории, `total` — кластеров (страница — `clusters`
    с `offset`). Сумма `count` по кластерам равна `places`, кроме ключа
    `controller` у вызова, приписанного нескольким узлам файла: там место
    стоит в кластере каждого узла. `linked` и `categories` — мест в каждой
    категории шва целиком: по ним видно, куда смотреть дальше, не зовя
    команду семь раз.
    """

    schema_version: Literal["1.0"] = "1.0"
    category: str
    by: str
    places: int
    total: int
    offset: int
    clusters: list[LinkCluster] = Field(default_factory=list)
    linked: int
    categories: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Item:
    """Запись категории, сведённая к месту, примеру и значениям ключей."""

    place: tuple[str | int, ...]
    file: str
    line: int
    text: str
    keys: Mapping[str, str]


def check_query(category: str, by: str | None) -> tuple[str, str]:
    """Категория и ключ запроса; неизвестные — `InputError` с перечнем допустимых.

    Проверка до прогонов: опечатка в ключе не должна стоить разбора репозитория.
    Её зовут и CLI (для своих кодов возврата), и `link_clusters` — сервер
    настройки (S27) зовёт функцию, а не команду.
    """
    if category not in BY_KEYS:
        raise InputError(f"категория {category!r} неизвестна; известны: {', '.join(CATEGORIES)}")
    allowed = BY_KEYS[category]
    chosen = by if by is not None else allowed[0]
    if chosen not in allowed:
        raise InputError(
            f"ключ {chosen!r} у категории {category} не определён; допустимы: {', '.join(allowed)}"
        )
    return category, chosen


def _prefix(route: str) -> str:
    return "/".join(route.split("/")[:PREFIX_SEGMENTS])


def _route_text(http_method: str, route: str) -> str:
    return f"{http_method} {route or "''"}".strip()


def _call_item(item: CallRef) -> _Item:
    keys = {
        "module": item.module,
        "prefix": _prefix(item.route),
        "file": item.file,
        "controller": item.caller,
        "host": item.host,
    }
    if isinstance(item, ExternalCall):
        keys["decision"] = item.decision.rule
    text = _route_text(item.http_method, item.route)
    return _Item((item.file, item.line), item.file, item.line, text, keys)


def _endpoint_item(item: EndpointRef) -> _Item:
    keys = {
        "module": item.module,
        "prefix": _prefix(item.route),
        "file": item.file,
        "controller": item.node,
    }
    if isinstance(item, ExternalEndpoint):
        keys["decision"] = item.decision.rule
    text = f"{_route_text(item.http_method, item.route)} ({item.member})"
    return _Item(
        (item.node, item.member, item.http_method, item.route), item.file, item.line, text, keys
    )


def _unresolved_item(item: UnresolvedCall) -> _Item:
    keys = {"module": item.module, "file": item.file, "reason": item.reason}
    if isinstance(item, DeclaredUnresolvable):
        keys["decision"] = item.decision.rule
    text = f"{item.http_method} {item.expression}".strip() or "''"
    return _Item((item.file, item.line), item.file, item.line, text, keys)


def _items(report: LinkReport, category: str) -> list[_Item]:
    """Записи категории в общем виде. Порядок — отсортированный, не порядок отчёта."""
    found: Iterable[_Item]
    if category == "calls_without_endpoint":
        found = map(_call_item, report.calls_without_endpoint)
    elif category == "external_targets":
        found = map(_call_item, report.external_targets)
    elif category == "almost":
        found = (
            _Item(
                (link.file, link.line),
                link.file,
                link.line,
                _route_text(link.http_method, link.route),
                {
                    "module": link.module,
                    "prefix": _prefix(link.route),
                    "file": link.file,
                    "controller": ", ".join(link.endpoints),
                },
            )
            for link in report.links
            if link.match == "almost"
        )
    elif category == "endpoints_without_caller":
        found = map(_endpoint_item, report.endpoints_without_caller)
    elif category == "external_callers":
        found = map(_endpoint_item, report.external_callers)
    elif category == "calls_unresolved":
        found = map(_unresolved_item, report.calls_unresolved)
    else:
        found = map(_unresolved_item, report.declared_unresolvable)
    # Повтор места (вызов у двух узлов файла) отличается только узлом:
    # при равных месте и тексте первым встаёт меньший ключ `controller`.
    return sorted(
        found,
        key=lambda item: (item.file, item.line, item.text, item.place, sorted(item.keys.items())),
    )


def _places(items: list[_Item]) -> int:
    return len({item.place for item in items})


def places_of(report: LinkReport, category: str) -> list[ClusterExample]:
    """Все места категории по одному, в порядке `(file, line, text)` — для ревью (S25).

    Сводка отдаёт три примера на кластер; ревью нужно каждое место, чтобы
    узнать, лежит ли оно в новом файле. Место — то же, что у счёта `places`:
    вызов, приписанный двум узлам файла, — одно место, поэтому число записей
    равно `LinkClusters.places`.
    """
    check_query(category, None)
    first: dict[tuple[str | int, ...], _Item] = {}
    for item in _items(report, category):
        first.setdefault(item.place, item)
    return [
        ClusterExample(file=item.file, line=item.line, text=item.text) for item in first.values()
    ]


def _page[T](items: list[T], limit: int, offset: int) -> list[T]:
    """Страница списка. `limit = 0` — до конца, как у `setup candidates`."""
    return items[offset:] if limit == 0 else items[offset : offset + limit]


# --------------------------------------------------------------------------------------
# Подсказка правила префикса
# --------------------------------------------------------------------------------------


def _heads(route: str) -> list[str]:
    """Первые один–`STRIP_SEGMENTS` сегментов маршрута без подстановок.

    Подстановка `{}` в голове пути — база, которую прокси не срезает: у сырого
    адреса там `${this.base}`, и срез по сегменту `{}` не совпал бы никогда.
    """
    segments = route.split("/") if route else []
    heads: list[str] = []
    for size in range(1, min(STRIP_SEGMENTS, len(segments)) + 1):
        if "{}" in segments[:size]:
            break
        heads.append("/".join(segments[:size]))
    return heads


def _has_fixed_segment(route: str) -> bool:
    return any(segment and segment != "{}" for segment in route.split("/"))


def _links(
    keys: Mapping[RouteKey, object], http_method: str, route: str, rule: RewriteRule
) -> bool:
    """Свяжется ли вызов точно, если модулю записать это правило."""
    lookup = RouteKey(http_method=http_method, route=normalize_route(route, rewrite=rule))
    return bool(exact_keys(keys, lookup))


def suggest_rewrite(
    module: str,
    calls: Iterable[CallRef],
    report: LinkReport,
    keys: Mapping[RouteKey, object],
    settings: DocpipeConfig,
) -> tuple[SuggestedRewrite | None, str]:
    """Пара `strip_prefix`/`add_prefix` для вызовов модуля без эндпоинта.

    Перебор: `strip_prefix` — пусто и головы адресов кластера (`_heads`),
    `add_prefix` — пусто и первые сегменты маршрутов эндпоинтов. Выигрывает
    пара, при которой мест кластера связывается точно больше, чем развязывается
    мест модуля, связанных сейчас: правило одно на модуль и переписывает все
    его адреса, и пара, которая связывает один вызов ценой сотни, — не
    подсказка. При равенстве — более короткий `strip_prefix`, затем меньший
    `add_prefix`, затем меньший `strip_prefix`: выбор не зависит от порядка
    перебора. Пара, ничего не дающая, — не подсказка: `(None, NOTE_NO_GAIN)`.

    Выбор не засчитывает вызов, у маршрута которого нет ни одного сегмента
    без подстановки (`''`, `{}/{}`): `add_prefix: api` делает из `GET ''`
    корень API `GET api`, и связь эта — совпадение, а не правило (на squidex
    так «связывалось» поле `@Input() fileSource = ''`). `would_link` при
    этом считается по всем местам кластера — ровно столько свяжет прогон.
    """
    current = settings.web.rewrite_for(module)
    if current is not None and (current.strip_prefix or current.add_prefix):
        return None, NOTE_MODULE_HAS_RULE

    # Места, а не записи: вызов у двух узлов файла посчитан был бы дважды.
    places: dict[tuple[str, int], CallRef] = {}
    for call in sorted(calls, key=lambda item: (item.file, item.line, item.caller)):
        places.setdefault((call.file, call.line), call)
    linked: dict[tuple[str, int], tuple[str, str]] = {}
    for link in sorted(report.links, key=lambda item: (item.file, item.line, item.caller)):
        if link.module == module and link.match == "exact":
            linked.setdefault((link.file, link.line), (link.http_method, link.route))

    wanted = Counter((call.http_method, call.route) for call in places.values())
    meaningful = {pair for pair in wanted if _has_fixed_segment(pair[1])}
    held = Counter(linked.values())
    strips = {""} | {head for _, route in wanted for head in _heads(route)}
    adds = {""}
    for key in keys:
        first = key.route.split("/", 1)[0]
        if first and first != "{}":
            adds.add(first)

    best: tuple[tuple[int, int, str, str], SuggestedRewrite] | None = None
    for strip in sorted(strips):
        for add in sorted(adds):
            rule = RewriteRule(module=module, strip_prefix=strip, add_prefix=add)
            joined = {pair for pair in wanted if _links(keys, *pair, rule)}
            scored = sum(wanted[pair] for pair in joined & meaningful)
            lost = sum(n for (m, r), n in held.items() if not _links(keys, m, r, rule))
            if scored - lost <= 0:
                continue
            gained = sum(wanted[pair] for pair in joined)
            rank = (lost - scored, len(strip), add, strip)
            if best is None or rank < best[0]:
                best = (
                    rank,
                    SuggestedRewrite(
                        module=module,
                        strip_prefix=strip,
                        add_prefix=add,
                        would_link=gained,
                        would_unlink=lost,
                    ),
                )
    if best is None:
        return None, NOTE_NO_GAIN
    return best[1], ""


# --------------------------------------------------------------------------------------
# Сводка
# --------------------------------------------------------------------------------------


def clusters_of(
    report: LinkReport,
    settings: DocpipeConfig,
    keys: Mapping[RouteKey, object],
    *,
    category: str = DEFAULT_CATEGORY,
    by: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> LinkClusters:
    """Сводка категории отчёта связи кластерами. Чистая функция отчёта и настройки.

    `keys` — ключи эндпоинтов бэкенда (`web.link.backend_keys`): по ним
    подсказка правила префикса проверяет, свяжется ли вызов. Порядок
    кластеров — `(-count, key)`; примеры — первые места по `(file, line)`.
    """
    category, chosen = check_query(category, by)
    if limit < 0 or offset < 0:
        raise InputError("--limit и --offset не бывают отрицательными")

    items = _items(report, category)
    groups: dict[str, dict[tuple[str | int, ...], _Item]] = {}
    for item in items:
        groups.setdefault(item.keys[chosen], {}).setdefault(item.place, item)
    ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))

    suggest = category == "calls_without_endpoint" and chosen == "module"
    clusters: list[LinkCluster] = []
    for key, group in _page(ordered, limit, offset):
        shown = sorted(group.values(), key=lambda item: (item.file, item.line, item.text))
        rewrite: SuggestedRewrite | None = None
        note = ""
        if suggest:
            calls = [call for call in report.calls_without_endpoint if call.module == key]
            rewrite, note = suggest_rewrite(key, calls, report, keys, settings)
        clusters.append(
            LinkCluster(
                key=key,
                count=len(group),
                examples=[
                    ClusterExample(file=item.file, line=item.line, text=item.text)
                    for item in shown[:EXAMPLES]
                ],
                suggested_rewrite=rewrite,
                rewrite_note=note,
            )
        )

    return LinkClusters(
        category=category,
        by=chosen,
        places=_places(items),
        total=len(ordered),
        offset=offset,
        clusters=clusters,
        linked=len({(link.file, link.line) for link in report.links if link.match == "exact"}),
        categories={name: _places(_items(report, name)) for name in CATEGORIES},
    )


def link_clusters(
    ctx: SetupContext,
    *,
    category: str = DEFAULT_CATEGORY,
    by: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> LinkClusters:
    """Сводка шва кластерами по прогонам контекста: `ctx.link` и ключи `ctx.scan`.

    Запрос проверяется до прогонов. Отчёт — `SetupContext.link`: тот же
    `report_for_settings`, что у `web link`; ключи эндпоинтов — по тому же
    манифесту шага 1, из которого он собран.
    """
    check_query(category, by)
    if limit < 0 or offset < 0:
        raise InputError("--limit и --offset не бывают отрицательными")
    report = ctx.link
    return clusters_of(
        report,
        ctx.settings,
        backend_keys(ctx.scan.manifest),
        category=category,
        by=by,
        limit=limit,
        offset=offset,
    )


# --------------------------------------------------------------------------------------
# Печать
# --------------------------------------------------------------------------------------

_TITLES: Final[dict[str, str]] = {
    "calls_without_endpoint": "вызовы без эндпоинта",
    "calls_unresolved": "невосстановленные вызовы без решения",
    "endpoints_without_caller": "эндпоинты без вызывающего",
    "almost": "почти совпавшие: ключи различаются только числом {}",
    "external_targets": "вызовы во внешний адрес (link.external_targets)",
    "external_callers": "эндпоинты, которые зовут извне (link.external_callers)",
    "declared_unresolvable": "невосстановимые вызовы (link.unresolvable)",
}


def link_clusters_json(report: LinkClusters) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))


def _page_line(total: int, offset: int, shown: int) -> str:
    """Строка продолжения: без неё обрезанный список читается как полный."""
    if total == 0:
        return "Кластеров нет."
    if shown == 0:
        return f"Показано 0 из {total}: --offset {offset} за концом списка."
    line = f"Показаны {offset + 1}–{offset + shown} из {total}."
    if offset + shown < total:
        line += f" Дальше: --offset {offset + shown}."
    return line


def format_link_clusters(report: LinkClusters) -> str:
    """Человекочитаемая сводка: категория, кластеры с примерами и подсказкой."""
    overview = ", ".join(f"{name} {count}" for name, count in report.categories.items())
    lines = [
        f"Шов, {_TITLES[report.category]} ({report.category}): мест {report.places}, "
        f"кластеров по {report.by} — {report.total}.",
        f"Мест по категориям: связано точно {report.linked}; {overview}.",
    ]
    for cluster in report.clusters:
        lines += ["", f"{cluster.key or '(пусто)'} — {cluster.count}"]
        lines += [f"  {item.file}:{item.line}  {item.text}" for item in cluster.examples]
        if cluster.count > len(cluster.examples):
            lines.append(f"  … и ещё {cluster.count - len(cluster.examples)}")
        rewrite = cluster.suggested_rewrite
        if rewrite is not None:
            lines.append(
                f"  подсказка (свяжет {rewrite.would_link}, развяжет {rewrite.would_unlink}): "
                f"web.url_rewrite: [{{module: {rewrite.module}, "
                f"strip_prefix: {rewrite.strip_prefix!r}, add_prefix: {rewrite.add_prefix!r}}}]"
            )
        elif cluster.rewrite_note:
            lines.append(f"  подсказки нет: {cluster.rewrite_note}")
    lines += ["", _page_line(report.total, report.offset, len(report.clusters))]
    return "\n".join(lines) + "\n"


__all__ = [
    "BY_KEYS",
    "CATEGORIES",
    "DEFAULT_CATEGORY",
    "KEYS",
    "ClusterExample",
    "LinkCluster",
    "LinkClusters",
    "SuggestedRewrite",
    "check_query",
    "clusters_of",
    "format_link_clusters",
    "link_clusters",
    "link_clusters_json",
    "places_of",
    "suggest_rewrite",
]
