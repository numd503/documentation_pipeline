"""Сведение двух манифестов: кто зовёт какой эндпоинт и чего не хватает.

Ключ связи — пара `(HTTP-метод, маршрут)`, а не ссылка на узел: идентификатор
узла содержит путь проекта и FQN, и переименование контроллера рвало бы связь
при неизменном HTTP-контракте.

**Различитель в сопоставление не входит.** `api/items/query` с `listInnerName`
в теле — один и тот же эндпоинт платформы для всех имён списков; различитель
отвечает на вопрос «зачем звали», а не «куда». Поэтому связь строится на каждый
вызов отдельно: два обращения к одному маршруту с разными именами списков дают
две связи, а не одну.

Пять категорий, и только последняя — дефект. Остальные четыре печатаются всегда
и кода возврата не меняют, пока не названы в `--fail-on`: линт, красный
с первого дня, выключат на второй, и вместе с ним пропадут работающие проверки.

**Эндпоинт с методом `*`** — действие с `[Route]` без глагола — принимает
любой метод, и сопоставляется с вызовом любого метода: и точно, и «почти».
"""

from dataclasses import replace
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.model import DocNode, Manifest
from docpipe.route import RouteKey, almost_equal, route_key

MatchKind = Literal["exact", "almost"]

# Вид узла бэкенда, у которого маршрут обязан собираться из атрибутов.
# Значение из `rules/dotnet.yaml`; в наборе с другим именем категория
# «конвенциональная маршрутизация» просто останется пустой.
CONTROLLER_KIND: Final = "controller"

# Метод эндпоинта, принимающего любой глагол (`dotnet.endpoints.ANY_METHOD`).
# Литерал, а не импорт: `docpipe/web/**` не импортирует `docpipe/dotnet/**`.
ANY_METHOD: Final = "*"

# Атрибут, задающий шаблон маршрута типа. Глагол без шаблона (`[HttpGet]`)
# его не задаёт: действие остаётся конвенциональным с ограничением по методу.
_ROUTE_ATTRIBUTE: Final = "Route"

CATEGORIES: Final[tuple[str, ...]] = (
    "linked",
    "almost",
    "calls_without_endpoint",
    "endpoints_without_caller",
    "duplicate_endpoints",
)


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Link(_Base):
    """Вызов фронта и эндпоинты бэкенда, в которые он попадает."""

    http_method: str
    route: str
    discriminator: str = ""
    caller: str  # id узла фронта
    file: str
    line: int
    endpoints: list[str] = Field(default_factory=list)  # id узлов бэкенда
    match: MatchKind


class CallRef(_Base):
    """Вызов, которому эндпоинт не нашёлся."""

    http_method: str
    route: str
    discriminator: str = ""
    caller: str
    file: str
    line: int


class EndpointRef(_Base):
    """Эндпоинт, которого никто не зовёт из этого фронта."""

    http_method: str
    route: str
    node: str
    member: str


class DuplicateEndpoint(_Base):
    """Один ключ, объявленный несколькими узлами бэкенда. Это дефект."""

    http_method: str
    route: str
    nodes: list[str]


class UnresolvedEndpoint(_Base):
    """Эндпоинт, маршрут которого не собрался: аргумент — выражение без литерала.

    В индекс ключей он не идёт (пустой маршрут склеил бы их все в один),
    и без отдельного списка пропал бы из отчёта молча: до S17 такой контроллер
    хотя бы выглядел «конвенциональным», теперь у него есть атрибуты маршрута.
    """

    http_method: str
    node: str
    member: str
    reason: str


class LinkReport(_Base):
    """Артефакт `web-link.json`.

    Времени в файле нет намеренно, как и в очереди шага 3: иначе он меняется
    на каждом прогоне и его нельзя сравнить.
    """

    # 1.1 — `unresolved_endpoints` и `counts.endpoints_unresolved` (S17).
    schema_version: Literal["1.1"] = "1.1"
    links: list[Link] = Field(default_factory=list)
    calls_without_endpoint: list[CallRef] = Field(default_factory=list)
    endpoints_without_caller: list[EndpointRef] = Field(default_factory=list)
    duplicate_endpoints: list[DuplicateEndpoint] = Field(default_factory=list)

    # Не категории связи, а состояние настройки и знания о бэкенде.
    unconfigured_modules: list[str] = Field(default_factory=list)
    conventional_controllers: list[str] = Field(default_factory=list)
    unresolved_endpoints: list[UnresolvedEndpoint] = Field(default_factory=list)

    counts: dict[str, int] = Field(default_factory=dict)


def _backend_keys(manifest: Manifest) -> dict[RouteKey, list[EndpointRef]]:
    """Ключ -> эндпоинты бэкенда, его объявляющие."""
    found: dict[RouteKey, list[EndpointRef]] = {}
    for node in manifest.nodes:
        for endpoint in node.endpoints:
            if not endpoint.route:
                # Маршрут не собрался из атрибутов: такой контроллер попадает
                # в отдельную категорию, а не в индекс ключей — иначе все они
                # склеились бы в один пустой ключ.
                continue
            key = route_key(endpoint.http_method, endpoint.route)
            found.setdefault(key, []).append(
                EndpointRef(
                    http_method=key.http_method,
                    route=key.route,
                    node=node.id,
                    member=endpoint.member,
                )
            )
    return found


def _unresolved(manifest: Manifest) -> list[UnresolvedEndpoint]:
    """Эндпоинты с невосстановленным маршрутом — с узлом, членом и причиной."""
    return sorted(
        (
            UnresolvedEndpoint(
                http_method=endpoint.http_method,
                node=node.id,
                member=endpoint.member,
                reason=endpoint.unresolved,
            )
            for node in manifest.nodes
            for endpoint in node.endpoints
            if endpoint.unresolved
        ),
        key=lambda item: (item.node, item.member, item.http_method, item.reason),
    )


def _is_conventional(node: DocNode) -> bool:
    """Настоящий ли конвенциональный контроллер: URL из атрибутов не собирается.

    Прежнее условие («эндпоинтов нет либо есть с пустым маршрутом») на двух
    открытых репозиториях дало 10 записей, настоящих из них 2: в список шли
    абстрактные базы (`ApiController` — своих действий нет, маршрут она отдаёт
    наследникам), контроллеры с `[Route]` без глагола и `AcceptVerbs`.

    Голый `[HttpGet]` шаблона не задаёт, и действие под ним в ASP.NET остаётся
    конвенциональным с ограничением по методу. Поэтому смотрим не на наличие
    атрибутов на членах, а на то, собрался ли из них маршрут: свой `[Route]`
    типа, непустой маршрут хоть одного эндпоинта (в том числе унаследованный
    от базы) или невосстановленный — всё это атрибутная маршрутизация.
    """
    symbol = node.symbol
    if symbol is None or "abstract" in symbol.modifiers:
        return False
    if not any(
        member.kind == "method"
        and "public" in member.modifiers
        and "static" not in member.modifiers
        for member in symbol.members
    ):
        return False
    if any(attribute.name == _ROUTE_ATTRIBUTE for attribute in symbol.attributes):
        return False
    return all(not endpoint.route and not endpoint.unresolved for endpoint in node.endpoints)


def _conventional(manifest: Manifest) -> list[str]:
    """Контроллеры, маршрут которых из атрибутов не собирается.

    Их вызовы попали бы в «эндпоинт не найден» и выглядели бы дефектом фронта,
    хотя дефекта нет: URL задан в `MapControllerRoute`, то есть в конфигурации
    приложения, а не на типе.
    """
    return sorted(
        node.id
        for node in manifest.nodes
        if node.kind == CONTROLLER_KIND and _is_conventional(node)
    )


def _exact(keys: dict[RouteKey, list[EndpointRef]], lookup: RouteKey) -> list[RouteKey]:
    """Ключи бэкенда, совпавшие с вызовом точно: свой метод и `*`."""
    candidates = [lookup, replace(lookup, http_method=ANY_METHOD)]
    return [key for key in candidates if key in keys]


def _almost(key: RouteKey, lookup: RouteKey) -> bool:
    """«Почти» с учётом `*`: у эндпоинта на любой метод глагол не сравнивается."""
    if key.http_method == ANY_METHOD:
        return almost_equal(key, replace(lookup, http_method=ANY_METHOD))
    return almost_equal(key, lookup)


def _unconfigured(web: Manifest, configured: set[str]) -> list[str]:
    """Модули фронта, у которых есть вызовы, но нет записи в `url_rewrite`.

    Пустые поля правила и отсутствие правила — разные вещи. Первое значит
    «проверено, преобразования нет», второе — «настройку забыли», и именно
    забытая настройка выглядит как исправная связь, расходясь ровно на префиксе.
    """
    return sorted(
        {node.module for node in web.nodes if node.web_calls and node.module not in configured}
    )


def build_report(
    backend: Manifest,
    web: Manifest,
    configured_modules: set[str] | None = None,
) -> LinkReport:
    """Свести два манифеста в отчёт связи."""
    keys = _backend_keys(backend)

    links: list[Link] = []
    orphans: list[CallRef] = []
    used: set[RouteKey] = set()

    for node in web.nodes:
        for call in node.web_calls:
            lookup = RouteKey(http_method=call.key.http_method, route=call.key.route)
            exact = _exact(keys, lookup)
            if exact:
                used.update(exact)
                links.append(
                    Link(
                        http_method=lookup.http_method,
                        route=lookup.route,
                        discriminator=call.key.discriminator,
                        caller=node.id,
                        file=call.file,
                        line=call.line,
                        endpoints=sorted({item.node for key in exact for item in keys[key]}),
                        match="exact",
                    )
                )
                continue

            near = sorted(
                (key for key in keys if _almost(key, lookup)),
                key=lambda key: (key.route, key.http_method),
            )
            if near:
                used.update(near)
                links.append(
                    Link(
                        http_method=lookup.http_method,
                        route=lookup.route,
                        discriminator=call.key.discriminator,
                        caller=node.id,
                        file=call.file,
                        line=call.line,
                        endpoints=sorted({item.node for key in near for item in keys[key]}),
                        match="almost",
                    )
                )
                continue

            orphans.append(
                CallRef(
                    http_method=lookup.http_method,
                    route=lookup.route,
                    discriminator=call.key.discriminator,
                    caller=node.id,
                    file=call.file,
                    line=call.line,
                )
            )

    uncalled = sorted(
        (item for key, items in keys.items() if key not in used for item in items),
        key=lambda item: (item.route, item.http_method, item.node),
    )
    duplicates = sorted(
        (
            DuplicateEndpoint(
                http_method=key.http_method,
                route=key.route,
                nodes=sorted(item.node for item in items),
            )
            for key, items in keys.items()
            if len({item.node for item in items}) > 1
        ),
        key=lambda item: (item.route, item.http_method),
    )

    links.sort(key=lambda link: (link.route, link.http_method, link.discriminator, link.caller))
    orphans.sort(key=lambda item: (item.route, item.http_method, item.caller))
    unresolved = _unresolved(backend)

    return LinkReport(
        links=links,
        calls_without_endpoint=orphans,
        endpoints_without_caller=uncalled,
        duplicate_endpoints=duplicates,
        unconfigured_modules=_unconfigured(web, configured_modules or set()),
        conventional_controllers=_conventional(backend),
        unresolved_endpoints=unresolved,
        counts={
            "linked": sum(1 for link in links if link.match == "exact"),
            "almost": sum(1 for link in links if link.match == "almost"),
            "calls_without_endpoint": len(orphans),
            "endpoints_without_caller": len(uncalled),
            "duplicate_endpoints": len(duplicates),
            "calls_total": sum(len(node.web_calls) for node in web.nodes),
            "endpoints_total": sum(len(items) for items in keys.values()),
            "endpoints_unresolved": len(unresolved),
        },
    )


_TITLES: Final[dict[str, str]] = {
    "linked": "связь найдена точно",
    "almost": "почти совпало: ключи различаются только числом {}",
    "calls_without_endpoint": "вызов без эндпоинта",
    "endpoints_without_caller": "эндпоинт без вызывающего",
    "duplicate_endpoints": "ОДИН КЛЮЧ У ДВУХ УЗЛОВ БЭКЕНДА",
}


def format_report(report: LinkReport, top: int = 10) -> str:
    """Человекочитаемый отчёт. Каждое число названо и сопровождено вторым."""
    lines = [
        f"Вызовов фронта: {report.counts['calls_total']}, "
        f"эндпоинтов бэкенда: {report.counts['endpoints_total']}.",
        "",
    ]
    for category in CATEGORIES:
        lines.append(f"  {report.counts[category]:>5}  {_TITLES[category]}")

    if report.duplicate_endpoints:
        lines += ["", "Коллизии маршрутов (это дефект):"]
        lines += [
            f"  {item.http_method} {item.route}: {', '.join(item.nodes)}"
            for item in report.duplicate_endpoints[:top]
        ]

    if report.calls_without_endpoint:
        lines += ["", "Вызовы, которым эндпоинт не нашёлся:"]
        lines += [
            f"  {item.http_method} {item.route}  <- {item.file}:{item.line}"
            for item in report.calls_without_endpoint[:top]
        ]

    if report.conventional_controllers:
        lines += [
            "",
            f"Контроллеров с конвенциональной маршрутизацией: "
            f"{len(report.conventional_controllers)}. Их маршрут задан в конфигурации "
            "приложения, а не атрибутами, — вызовы к ним попадают в «вызов без эндпоинта».",
        ]

    if report.unresolved_endpoints:
        lines += [
            "",
            f"Эндпоинтов с невосстановленным маршрутом: {len(report.unresolved_endpoints)}. "
            "В ключи связи они не входят — вызовы к ним попадают в «вызов без эндпоинта»:",
        ]
        lines += [
            f"  {item.http_method} {item.node}.{item.member}: {item.reason}"
            for item in report.unresolved_endpoints[:top]
        ]

    if report.unconfigured_modules:
        lines += [
            "",
            "Модули фронта без записи в `web.url_rewrite`: "
            + ", ".join(report.unconfigured_modules),
            "  Пустое правило и отсутствие правила — разные вещи: первое значит "
            "«проверено, преобразования нет».",
        ]

    return "\n".join(lines)
