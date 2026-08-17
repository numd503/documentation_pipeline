"""Список страниц фронта и обоснование каждой.

Вид `page` — единственный, который **не выдаётся правилом**. Правило говорит
«это компонент», а страницей его делает повышение по таблице роутов
(`web/tree.py`), собранной межфайлово. Поэтому ни `scan --stats`, ни `symbols`
на вопрос «почему этот класс — страница» ответить не могут: они показывают
срабатывания правил, а правило здесь сказало `component`.

Отчёт читает **готовый манифест** и ничего не разбирает заново. Свой разбор
отвечал бы на другой вопрос — «что получилось бы сейчас» вместо «что лежит
в файле» — и расходился бы с манифестом на каждой смене конфигурации.

Главное свойство вывода: «API страницы» здесь **вычисляется обходом**
зависимостей, а не читается полем. Вызовы лежат на узле того файла, где они
записаны, то есть на сервисе, — и это правильно, связь строит сервис. Значит
у отчёта обязаны быть видны и глубина обхода, и посредник: иначе список
выглядит как факт манифеста, которым не является.
"""

import csv
import io
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.hashing import stable_json_dumps
from docpipe.model import DocNode, Manifest

PAGE_KIND: Final = "page"
COMPONENT_KIND: Final = "component"
STATE_KIND: Final = "state"
FEATURE_KIND: Final = "feature"

# Глубина обхода по умолчанию. Три шага — это полная цепочка боевого фронта:
# «страница -> стейт (обработчик экшена) -> сервис -> сервис». Замер на АС CF:
# на глубине 2 страницы зовут 42 эндпоинта, на глубине 4 — 49, и почти вся
# разница набирается третьим шагом; четвёртый добавляет единицы.
#
# Больше не значит лучше: обход идёт по парам «узел, член» и через настоящие
# вызовы, но каждый лишний шаг уводит документ страницы в чужую ответственность.
DEFAULT_DEPTH: Final = 3

FORMATS: Final[tuple[str, ...]] = ("text", "json", "csv")

# Наблюдения отчёта. Это **подсказки, а не смена вида**: ни одно из них
# не меняет `kind` в манифесте. Угаданная страница ломает якорь бизнес-документа
# (он пишется на маршрут, которого у неё нет), а угаданная не-страница молча
# выкидывает экран из документации. Решение принимает человек — правилами
# и конфигурацией, а не эвристикой отчёта.
NOTE_UNANCHORABLE: Final = (
    "маршрут не восстановлен ни у одной записи: якорь `page` на неё поставить нельзя, "
    "и в покрытии страниц её не будет"
)
NOTE_EMPTY_ROUTE: Final = "маршрут пустой: обычно это layout с <router-outlet>, а не экран"
NOTE_NO_FEATURES: Final = (
    "признаков функционала не найдено: ни членов, ни внешнего шаблона, ни вызовов"
)
NOTE_NO_CALLS: Final = (
    "страница не ходит за данными сама: ни прямого вызова сервиса, ни диспатча. "
    "Обычно так выглядит экран, которому данные приходят через @Input от родителя "
    "либо из платформенного грида"
)
NOTE_CHAIN_STOPS: Final = (
    "цепочка вызовов обрывается на символах без решения: список эндпоинтов "
    "короче правды. Настройте правила (`web scan --stats`, `symbols --lang ts "
    "--state undecided`) — узлом станет то, про что решение принято"
)
REASON_NO_ROUTE: Final = "маршрута нет: в таблицах роутов этот компонент не встретился"

# Маршрут есть, а вида страницы нет — так выглядит снятие руками через
# `pages.yaml`. Причину снятия знает тот файл, а манифест хранит факт: узел
# стои́т в таблице роутов и страницей при этом не является.
REASON_MANUAL: Final = (
    "снята вручную: маршрут в таблице есть, а вид страницы отменён — см. `pages.yaml`"
)


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PageRoute(_Base):
    """Запись маршрута вместе с тем, откуда она взялась."""

    path: str
    unresolved: bool = False
    source: str = ""
    table: str = ""


class PageDependency(_Base):
    """Узел, до чьих членов страница доходит по графу вызовов.

    `depth` — на каком шаге встретился. Шаг обязан быть виден: «страница зовёт
    этот эндпоинт» и «страница зовёт сервис, который зовёт сервис, который
    зовёт этот эндпоинт» — разные утверждения.

    `members` — какие именно члены зовут. Это ответ на вопрос «почему он тут»:
    участник, попавший в список необъяснимо (на боевом прогоне так выглядел
    `SetApiUrl`), либо назовёт зовомый метод, либо окажется ошибкой резолва.
    """

    node_id: str
    title: str
    kind: str
    depth: int
    doc_path: str
    members: list[str] = Field(default_factory=list)


class PageCall(_Base):
    """HTTP-вызов, до которого страница доходит **по вызовам**.

    `through` — узел и метод, в котором вызов записан (`ModelService.byId`);
    `action` — тип экшена, если до цели дошли диспатчем. Оба поля обязаны быть
    видны: список эндпоинтов страницы не факт манифеста, а вывод по графу,
    и без пути его нельзя проверить.
    """

    http_method: str
    route: str
    discriminator: str = ""
    through: str  # `ModelService.byId` — где вызов записан
    action: str = ""  # `[Inner Debt] Load`, если дошли диспатчем
    depth: int  # 1 — вызов метода, который страница зовёт сама


class Walk(_Base):
    """Итог обхода графа вызовов от одной страницы.

    `stops` — обрывы: ребро есть, а узла на его конце в манифесте нет. Это
    **не** дефект обхода, а след настройки правил: символ, про который решение
    не принято, узлом не становится, и цепочка через него не проходит.
    На АС CF таких символов 1249 из 2152, и без этого числа список эндпоинтов
    страницы выглядит полным, будучи обрезанным.
    """

    calls: list[PageCall] = Field(default_factory=list)
    participants: dict[str, tuple[int, list[str]]] = Field(default_factory=dict)
    stops: list[str] = Field(default_factory=list)


class Page(_Base):
    """Страница и всё, из чего следует, что она страница."""

    node_id: str
    title: str
    module: str
    doc_path: str
    matched_rules: list[str] = Field(default_factory=list)
    routes: list[PageRoute] = Field(default_factory=list)

    # Сколько других страниц лежит под маршрутом этой. Факт из таблицы роутов,
    # а не эвристика по числу членов: `/forecast/{}` имеет семь дочерних
    # и при этом собственные 45 членов и свои вызовы, а `…/structuring` —
    # три дочерние, два члена и ни одного вызова. Числом это разводится,
    # догадкой «мало членов — значит контейнер» — нет.
    children: int = 0
    members: int = 0
    template: bool = False
    dependencies: list[PageDependency] = Field(default_factory=list)
    calls: list[PageCall] = Field(default_factory=list)

    # Сколько эндпоинтов **достижимо** по внедрению — величина до P01–P03.
    # Оставлена рядом намеренно: разница между ней и длиной `calls` и есть
    # мера того, сколько чужого приезжало в документ страницы.
    reachable_calls: int = 0

    # Символы, на которых обход остановился: узла нет, значит решение про них
    # не принято. Прямое следствие ненастроенных правил, и без этого числа
    # список эндпоинтов выглядит полным, будучи обрезанным.
    chain_stops: int = 0
    notes: list[str] = Field(default_factory=list)


class NotAPage(_Base):
    """Компонент, страницей не ставший, и причина.

    Обратная сторона списка. Вопрос «почему этого экрана нет среди страниц»
    задаётся ровно так же часто, как прямой, и без этой половины отчёта
    ответа на него нет нигде.
    """

    node_id: str
    title: str
    module: str
    doc_path: str
    routes: list[str] = Field(default_factory=list)
    reason: str = REASON_NO_ROUTE


class PagesReport(_Base):
    """Артефакт `web pages`. Времени в нём нет — иначе его нельзя сравнить."""

    schema_version: Literal["1.1"] = "1.1"
    depth: int = DEFAULT_DEPTH
    filters: str = ""
    pages: list[Page] = Field(default_factory=list)

    # Разделы — вторая единица документации: их объявляет человек, у них нет
    # маршрута, и в списке страниц им места нет. Но и молчать о них нельзя:
    # это документы, которые прогон создаст.
    features: list[Page] = Field(default_factory=list)
    not_pages: list[NotAPage] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


def _children_of(routes: list[PageRoute], every: list[list[PageRoute]]) -> int:
    """Сколько страниц лежит под маршрутом этой — по сегментам, а не по подстроке.

    Подстрока сделала бы `/forecast` родителем `/forecaster`, и обычный экран
    оказался бы контейнером. Пустой маршрут (`/`) — родитель всему: layout
    приложения и есть корень дерева.

    Это **факт**, а не эвристика, и в этом вся его ценность: `/forecast/{}`
    имеет семь дочерних при собственных 45 членах и своих вызовах,
    а `…/structuring` — три дочерние, два члена и ни одного вызова. Догадка
    «мало членов — значит контейнер» их не разводит, число — разводит.
    """
    parents = [item.path.split("/") if item.path else [] for item in routes if not item.unresolved]
    if not parents:
        return 0

    found = 0
    for other in every:
        if other is routes:
            continue
        for entry in other:
            if entry.unresolved:
                continue
            child = entry.path.split("/") if entry.path else []
            if any(
                len(child) > len(parent) and child[: len(parent)] == parent for parent in parents
            ):
                found += 1
                break
    return found


def state_name(node: DocNode) -> str:
    """Имя стейта из декоратора `@State({ name: 'innerDebt' })`.

    Именно оно, а не имя класса: переименование `DebtState` смысла не меняет,
    а `name` — контракт, по которому стейт селектят и который видит человек.
    """
    if node.symbol is None:
        return ""
    for attribute in node.symbol.attributes:
        if attribute.name == "State" and "name" in attribute.named_args:
            return attribute.named_args["name"]
    return ""


def index_by_fqn(manifest: Manifest) -> dict[str, DocNode]:
    """FQN -> узел. Публичная: этим же индексом ходит по графу шаг 2."""
    return _index_by_fqn(manifest)


def _index_by_fqn(manifest: Manifest) -> dict[str, DocNode]:
    """FQN -> узел. Побеждает лексикографически меньший id, а не порядок в файле."""
    found: dict[str, DocNode] = {}
    for node in sorted(manifest.nodes, key=lambda item: item.id):
        if node.symbol is not None:
            found.setdefault(node.symbol.fqn, node)
    return found


def _reachable(node: DocNode, by_fqn: dict[str, DocNode], depth: int) -> dict[str, int]:
    """id узла -> шаг, на котором он встретился. Обход в ширину, без повторов.

    Ширина, а не глубина: узел, до которого есть и короткий, и длинный путь,
    обязан получить короткий. Иначе «шаг» в выводе зависит от порядка обхода.
    """
    seen: dict[str, int] = {}
    frontier = [node]
    for level in range(1, depth + 1):
        following: list[DocNode] = []
        for current in frontier:
            targets = sorted(
                {dependency.target for dependency in current.dependencies if dependency.target}
            )
            for target in targets:
                reached = by_fqn.get(target)
                if reached is None or reached.id in seen or reached.id == node.id:
                    continue
                seen[reached.id] = level
                following.append(reached)
        frontier = following
    return seen


def called(node: DocNode, by_fqn: dict[str, DocNode], depth: int) -> Walk:
    """Вызовы, до которых страница доходит **по вызовам**, и кто в этом участвовал.

    Обход идёт по парам «узел, член», а не по узлам: ребро `uses` говорит
    не «страница знает сервис», а «член страницы зовёт вот этот член сервиса»,
    и вызовы берутся только из этого члена. Именно замена одного другим убирает
    с боевой страницы 51 эндпоинт вместо семи.

    Возвращает вызовы и участников (FQN -> шаг), потому что оба считаются одним
    обходом: второй такой же разошёлся бы с первым на первой правке.
    """
    calls: dict[tuple[str, str, str, str, str], PageCall] = {}
    participants: dict[str, tuple[int, list[str]]] = {}
    stops: set[str] = set()

    # Собственные вызовы страницы — шаг ноль. Компонент, который зовёт `http`
    # сам, в Angular редкость, но он есть, и потерять его нельзя: до этого
    # эндпоинта страница доходит вообще без посредников.
    for call in node.web_calls:
        key = (
            call.key.http_method,
            call.key.route,
            call.key.discriminator,
            f"{node.title}.{call.member}" if call.member else node.title,
            "",
        )
        calls.setdefault(
            key,
            PageCall(
                http_method=key[0],
                route=key[1],
                discriminator=key[2],
                through=key[3],
                depth=0,
            ),
        )

    # Состояние обхода: (FQN узла, член этого узла, тип экшена на пути сюда).
    frontier: list[tuple[str, str, str]] = []
    for usage in node.uses:
        frontier.append((usage.target, usage.member, usage.action))

    seen: set[tuple[str, str]] = set()
    for level in range(1, depth + 1):
        following: list[tuple[str, str, str]] = []
        for fqn, member, action in sorted(frontier):
            if (fqn, member) in seen:
                continue
            seen.add((fqn, member))

            reached = by_fqn.get(fqn)
            if reached is None:
                # Ребро ведёт в символ, который узлом не стал: решение о нём
                # не принято либо он отсеян. Цепочка обрывается здесь, и молчать
                # об этом нельзя — список эндпоинтов страницы окажется короче
                # правды ровно на то, что лежит за обрывом.
                stops.add(fqn)
                continue
            seen_level, called_members = participants.setdefault(fqn, (level, []))
            if member and member not in called_members:
                called_members.append(member)
            participants[fqn] = (seen_level, sorted(called_members))

            for call in reached.web_calls:
                # Вызов из другого метода того же сервиса к странице отношения
                # не имеет: сервис на тридцать методов зовут ради двух.
                if call.member != member:
                    continue
                key = (
                    call.key.http_method,
                    call.key.route,
                    call.key.discriminator,
                    f"{reached.title}.{member}",
                    action,
                )
                calls.setdefault(
                    key,
                    PageCall(
                        http_method=key[0],
                        route=key[1],
                        discriminator=key[2],
                        through=key[3],
                        action=key[4],
                        depth=level,
                    ),
                )

            for usage in reached.uses:
                if usage.via == member:
                    following.append((usage.target, usage.member, action or usage.action))
        frontier = following

    return Walk(
        calls=[calls[key] for key in sorted(calls)],
        participants=participants,
        stops=sorted(stops),
    )


def _reachable_calls(node: DocNode, by_fqn: dict[str, DocNode], depth: int) -> int:
    """Сколько эндпоинтов **достижимо** по внедрению — величина до P01–P03.

    Считается по тем же зависимостям, что и раньше, и печатается рядом
    с настоящим числом. Разница — не дефект, а мера: на боевом модуле
    у `ForecastComponent` 13 внедрённых сервисов давали 51 эндпоинт.
    """
    reached = _reachable(node, by_fqn, depth)
    by_id = {item.id: item for item in by_fqn.values()}
    keys = {
        (call.key.http_method, call.key.route, call.key.discriminator) for call in node.web_calls
    }
    for node_id in reached:
        target = by_id.get(node_id)
        if target is None:
            continue
        keys |= {
            (call.key.http_method, call.key.route, call.key.discriminator)
            for call in target.web_calls
        }
    return len(keys)


def _has_template(node: DocNode) -> bool:
    """Есть ли у компонента внешний `.html`.

    Шаблон дописывается в `sources` на шаге `web` рядом с `.ts`, поэтому
    признак читается отсюда, а не из атрибутов: `template:` строкой в декораторе
    внешнего файла не даёт.
    """
    if node.symbol is None:
        return False
    return any(source.path.endswith(".html") for source in node.symbol.sources)


def _notes(
    page_routes: list[PageRoute], members: int, template: bool, calls: int, stops: int = 0
) -> list[str]:
    notes: list[str] = []
    if page_routes and all(route.unresolved for route in page_routes):
        notes.append(NOTE_UNANCHORABLE)
    if any(not route.path and not route.unresolved for route in page_routes):
        notes.append(NOTE_EMPTY_ROUTE)

    # Два разных случая, и путать их нельзя. «Ни членов, ни шаблона, ни вызовов»
    # — кандидат в layout. «Члены есть, а вызовов нет» — почти наверняка NGXS:
    # экшен создаётся в теле метода, и обход по зависимостям его не видит.
    # Одна заметка на оба случая заставила бы читать её как «страница пустая».
    if not members and not template and not calls:
        notes.append(NOTE_NO_FEATURES)
    elif not calls:
        notes.append(NOTE_NO_CALLS)
    if stops:
        notes.append(NOTE_CHAIN_STOPS)
    return notes


def _page_of(
    node: DocNode, by_fqn: dict[str, DocNode], by_id: dict[str, DocNode], depth: int
) -> Page:
    walk = called(node, by_fqn, depth)
    calls, participants = walk.calls, walk.participants

    # Участники — те, до чьих членов страница дошла **по вызовам**. Список
    # внедрённого сюда не входит: сервис, который внедрили и не зовут,
    # к содержанию документа страницы отношения не имеет.
    dependencies = sorted(
        (
            PageDependency(
                node_id=by_fqn[fqn].id,
                title=by_fqn[fqn].title,
                kind=by_fqn[fqn].kind,
                depth=level,
                doc_path=by_fqn[fqn].doc_path,
                members=called,
            )
            for fqn, (level, called) in participants.items()
            if fqn in by_fqn
        ),
        key=lambda item: (item.depth, item.title, item.node_id),
    )

    routes = sorted(
        (
            PageRoute(
                path=entry.path,
                unresolved=entry.route_unresolved,
                source=entry.source,
                table=entry.table,
            )
            for entry in node.routes
        ),
        key=lambda item: (item.path, item.unresolved, item.source, item.table),
    )
    members = len(node.symbol.members) if node.symbol else 0
    template = _has_template(node)

    return Page(
        node_id=node.id,
        title=node.title,
        module=node.module,
        doc_path=node.doc_path,
        matched_rules=sorted(node.matched_rules),
        routes=routes,
        members=members,
        template=template,
        dependencies=dependencies,
        calls=calls,
        reachable_calls=_reachable_calls(node, by_fqn, depth),
        chain_stops=len(walk.stops),
        notes=_notes(routes, members, template, len(calls), len(walk.stops)),
    )


def _describe(depth: int, route: str, module: str) -> str:
    parts = [f"глубина {depth}"]
    if route:
        parts.append(f"маршрут содержит «{route}»")
    if module:
        parts.append(f"модуль содержит «{module}»")
    return ", ".join(parts)


def build_report(
    manifest: Manifest,
    depth: int = DEFAULT_DEPTH,
    route: str = "",
    module: str = "",
) -> PagesReport:
    """Собрать отчёт по манифесту шага `web`.

    Счётчики считаются по **всему** дереву, а не по отфильтрованному списку:
    сужение отвечает на вопрос «покажи эти», а не «сколько их всего», и сводка,
    посчитанная по срезу, объявила бы, что страниц три.
    """
    by_fqn = _index_by_fqn(manifest)
    by_id = {node.id: node for node in manifest.nodes}

    pages = [
        _page_of(node, by_fqn, by_id, depth)
        for node in sorted(manifest.nodes, key=lambda item: item.id)
        if node.kind == PAGE_KIND
    ]

    # Дочерние считаются, когда все страницы уже собраны: величина про дерево
    # маршрутов целиком, а не про одну запись.
    every = [page.routes for page in pages]
    pages = [
        page.model_copy(update={"children": _children_of(page.routes, every)}) for page in pages
    ]
    features = [
        _page_of(node, by_fqn, by_id, depth)
        for node in sorted(manifest.nodes, key=lambda item: item.id)
        if node.kind == FEATURE_KIND
    ]
    not_pages = sorted(
        (
            NotAPage(
                node_id=node.id,
                title=node.title,
                module=node.module,
                doc_path=node.doc_path,
                routes=sorted({f"/{entry.path}" for entry in node.routes}),
                # Маршрут у компонента есть, а вида страницы нет — значит вид
                # отменён руками. Другого способа получить такую пару нет.
                reason=REASON_MANUAL if node.routes else REASON_NO_ROUTE,
            )
            for node in manifest.nodes
            if node.kind == COMPONENT_KIND
        ),
        key=lambda item: (item.module, item.title, item.node_id),
    )

    counts = {
        "pages": len(pages),
        "routes": sum(len(page.routes) for page in pages),
        "routes_unresolved": sum(1 for page in pages for item in page.routes if item.unresolved),
        "unanchorable": sum(1 for page in pages if NOTE_UNANCHORABLE in page.notes),
        "without_calls": sum(1 for page in pages if not page.calls),
        # Две величины рядом: сколько эндпоинтов страницы зовут и сколько
        # их было бы, считай мы по внедрению. Разница — мера того, сколько
        # чужого приезжало в документ до графа вызовов.
        "endpoints_called": sum(len(page.calls) for page in pages),
        "endpoints_reachable": sum(page.reachable_calls for page in pages),
        "chain_stops": sum(page.chain_stops for page in pages),
        "without_features": sum(1 for page in pages if NOTE_NO_FEATURES in page.notes),
        "components_without_route": len(not_pages),
        "features": len(features),
        "containers": sum(1 for page in pages if page.children and not page.calls),
        "nodes_total": len(manifest.nodes),
    }

    shown = [
        page
        for page in pages
        if (not route or any(route in item.path for item in page.routes))
        and (not module or module in page.module)
    ]
    shown.sort(key=lambda page: (page.routes[0].path if page.routes else "", page.title))
    filtered_not_pages = [item for item in not_pages if not module or module in item.module]

    return PagesReport(
        depth=depth,
        filters=_describe(depth, route, module),
        pages=shown,
        features=[item for item in features if not module or module in item.module],
        not_pages=filtered_not_pages,
        counts=counts,
    )


# --------------------------------------------------------------------------------------
# Форматы вывода
# --------------------------------------------------------------------------------------

_LABEL = 17


def _line(label: str, value: str) -> str:
    return f"  {label.ljust(_LABEL)}{value}"


def _route_line(item: PageRoute) -> str:
    path = "/" + item.path
    if item.unresolved:
        path += "  (маршрут не восстановлен)"
    origin = f"{item.source} : {item.table}" if item.source else "источник не записан"
    return f"{path}   <- {origin}"


def _call_line(call: PageCall) -> str:
    key = f"{call.http_method} /{call.route}"
    if call.discriminator:
        key += f" [{call.discriminator}]"
    where = "в самой странице" if call.depth == 0 else f"через {call.through}, шаг {call.depth}"
    if call.action:
        where += f", диспатч {call.action}"
    return f"{key}   <- {where}"


def _why(page: Page) -> str:
    rules = ", ".join(page.matched_rules) or "правило не записано"
    return f"{rules} -> component, затем повышение по таблице роутов ({len(page.routes)})"


def _block(page: Page) -> list[str]:
    head = "/" + (page.routes[0].path if page.routes else "")
    lines = [f"{head}   {page.title}   [{page.module}]"]
    lines.append(_line("почему страница", _why(page)))
    for index, item in enumerate(page.routes):
        lines.append(_line("маршруты" if index == 0 else "", _route_line(item)))
    lines.append(_line("документ", page.doc_path))
    lines.append(
        _line(
            "состав",
            f"членов {page.members}, внешний шаблон {'есть' if page.template else 'нет'}"
            + (f", дочерних страниц {page.children}" if page.children else ""),
        )
    )
    for index, dependency in enumerate(page.dependencies):
        lines.append(
            _line(
                "зовёт" if index == 0 else "",
                f"{dependency.title} ({dependency.kind}, шаг {dependency.depth})"
                + (f": {', '.join(dependency.members)}" if dependency.members else ""),
            )
        )
    lines.append(
        _line(
            "эндпоинтов",
            f"{len(page.calls)} зовёт, {page.reachable_calls} достижимо по внедрению "
            "(вторая величина — инвентарь, а не состав страницы)"
            + (f"; обрывов цепочки {page.chain_stops}" if page.chain_stops else ""),
        )
    )
    for call in page.calls:
        lines.append(_line("", _call_line(call)))
    for index, note in enumerate(page.notes):
        lines.append(_line("заметки" if index == 0 else "", note))
    return lines


_SUMMARY: Final[tuple[tuple[str, str], ...]] = (
    ("pages", "страниц"),
    ("unanchorable", "из них якорь поставить нельзя: маршрут не восстановлен"),
    ("without_calls", "без единого вызова: за данными не ходят сами"),
    ("endpoints_called", "эндпоинтов зовут страницы"),
    ("endpoints_reachable", "эндпоинтов было бы, считай мы по внедрению"),
    ("chain_stops", "обрывов цепочки: узла нет, решение о символе не принято"),
    ("without_features", "без признаков функционала: ни членов, ни шаблона, ни вызовов"),
    ("containers", "похожи на контейнер: под ними есть страницы, своих вызовов нет"),
    ("features", "разделов: объявлены человеком, маршрута у них нет"),
    ("components_without_route", "компонентов без маршрута — страницами не стали"),
)


def format_report(report: PagesReport, show_not_pages: bool = False) -> str:
    """Человекочитаемый список. Каждое число названо, каждый вывод обоснован."""
    lines = [
        f"Страниц: {report.counts['pages']} из {report.counts['nodes_total']} узлов манифеста. "
        f"Обход зависимостей: {report.filters}."
    ]
    if len(report.pages) != report.counts["pages"]:
        lines[0] += f" Показано: {len(report.pages)}."

    for page in report.pages:
        lines += ["", *_block(page)]

    for feature in report.features:
        lines += ["", f"РАЗДЕЛ {feature.title}   [{feature.module}]"]
        lines.append(_line("документ", feature.doc_path))
        lines.append(
            _line(
                "эндпоинтов",
                f"{len(feature.calls)} зовёт"
                + (f"; обрывов цепочки {feature.chain_stops}" if feature.chain_stops else ""),
            )
        )
        for call in feature.calls:
            lines.append(_line("", _call_line(call)))

    if show_not_pages:
        lines += ["", f"Компоненты, страницами не ставшие ({len(report.not_pages)}):"]
        lines += [
            f"  {item.title}   [{item.module}]"
            + (f"   {' '.join(item.routes)}" if item.routes else "")
            + f"   {item.reason}"
            for item in report.not_pages
        ]

    lines += ["", "Итого:"]
    lines += [f"  {report.counts[key]:>5}  {title}" for key, title in _SUMMARY]

    if report.counts.get("chain_stops"):
        lines += [
            "",
            "Обрыв цепочки — это ненастроенные правила, а не дефект обхода: "
            "символ, про который решение не принято, узлом не становится, и путь "
            "через него не проходит. Пока обрывы есть, список эндпоинтов у страниц "
            "короче правды.",
        ]

    if report.counts["without_calls"]:
        lines += [
            "",
            "«Без вызовов» здесь означает, что страница не ходит за данными сама: "
            "ни прямого вызова сервиса, ни диспатча экшена. Вызовы из шаблона "
            '(`(click)="save()"`, `service.ready$ | async`) разбор не видит — '
            "грамматика Angular-шаблонов в дерево не входит.",
        ]
    return "\n".join(lines)


def report_json(report: PagesReport) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))


_CSV_HEADER: Final[tuple[str, ...]] = (
    "маршруты",
    "класс",
    "модуль",
    "документ",
    "дочерних",
    "членов",
    "шаблон",
    "правила",
    "зовёт",
    "эндпоинтов",
    "достижимо",
    "обрывов",
    "вызовы",
    "заметки",
)


def report_csv(report: PagesReport) -> str:
    """Тот же список таблицей — им размечают страницы руками.

    Разделитель строк задан явно: по умолчанию `csv` пишет `\\r\\n`, и файл
    перестал бы совпадать сам с собой при сравнении на другой платформе.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_HEADER)
    for page in report.pages:
        writer.writerow(
            [
                " ".join(
                    ("?" if item.unresolved else "") + "/" + item.path for item in page.routes
                ),
                page.title,
                page.module,
                page.doc_path,
                page.children,
                page.members,
                "да" if page.template else "нет",
                " ".join(page.matched_rules),
                " ".join(f"{item.title}:{item.depth}" for item in page.dependencies),
                len(page.calls),
                page.reachable_calls,
                page.chain_stops,
                # Путь идёт в ту же ячейку, что и ключ: разметку страниц ведут
                # по этому файлу, и «через кого» нужнее всего именно там.
                " ".join(
                    f"{call.http_method} /{call.route}"
                    + (f"[{call.discriminator}]" if call.discriminator else "")
                    + f"<-{call.through}#{call.depth}"
                    + (f"[{call.action}]" if call.action else "")
                    for call in page.calls
                ),
                "; ".join(page.notes),
            ]
        )
    return buffer.getvalue()
