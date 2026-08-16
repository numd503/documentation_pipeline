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

# Глубина обхода зависимостей по умолчанию. Два шага — это «страница ->
# сервис» и «страница -> сервис -> сервис»: столько и нужно, чтобы увидеть
# API экрана. Больше — и в список приезжает половина приложения, а вопрос
# «что зовёт эта страница» снова остаётся без ответа.
DEFAULT_DEPTH: Final = 2

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
    "вызовов не найдено, но функционал есть: поход за данными через "
    "dispatch(new Action()) -> стейт -> сервис по конструкторным зависимостям не виден"
)
REASON_NO_ROUTE: Final = "маршрута нет: в таблицах роутов этот компонент не встретился"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PageRoute(_Base):
    """Запись маршрута вместе с тем, откуда она взялась."""

    path: str
    unresolved: bool = False
    source: str = ""
    table: str = ""


class PageDependency(_Base):
    """Узел, до которого страница дотягивается по конструкторным зависимостям.

    `depth` — на каком шаге встретился. Шаг обязан быть виден: «страница зовёт
    этот эндпоинт» и «страница зовёт сервис, который зовёт сервис, который
    зовёт этот эндпоинт» — разные утверждения.
    """

    node_id: str
    title: str
    kind: str
    depth: int
    doc_path: str


class PageCall(_Base):
    """HTTP-вызов, найденный у страницы или у того, что она тянет."""

    http_method: str
    route: str
    discriminator: str = ""
    through: str  # заголовок узла, на котором записан вызов
    depth: int  # 0 — вызов записан в самом компоненте страницы


class Page(_Base):
    """Страница и всё, из чего следует, что она страница."""

    node_id: str
    title: str
    module: str
    doc_path: str
    matched_rules: list[str] = Field(default_factory=list)
    routes: list[PageRoute] = Field(default_factory=list)
    members: int = 0
    template: bool = False
    dependencies: list[PageDependency] = Field(default_factory=list)
    calls: list[PageCall] = Field(default_factory=list)
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
    reason: str = REASON_NO_ROUTE


class PagesReport(_Base):
    """Артефакт `web pages`. Времени в нём нет — иначе его нельзя сравнить."""

    schema_version: Literal["1.0"] = "1.0"
    depth: int = DEFAULT_DEPTH
    filters: str = ""
    pages: list[Page] = Field(default_factory=list)
    not_pages: list[NotAPage] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


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


def _calls_of(node: DocNode, through: str, depth: int) -> list[PageCall]:
    return [
        PageCall(
            http_method=call.key.http_method,
            route=call.key.route,
            discriminator=call.key.discriminator,
            through=through,
            depth=depth,
        )
        for call in node.web_calls
    ]


def _has_template(node: DocNode) -> bool:
    """Есть ли у компонента внешний `.html`.

    Шаблон дописывается в `sources` на шаге `web` рядом с `.ts`, поэтому
    признак читается отсюда, а не из атрибутов: `template:` строкой в декораторе
    внешнего файла не даёт.
    """
    if node.symbol is None:
        return False
    return any(source.path.endswith(".html") for source in node.symbol.sources)


def _notes(page_routes: list[PageRoute], members: int, template: bool, calls: int) -> list[str]:
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
    return notes


def _page_of(
    node: DocNode, by_fqn: dict[str, DocNode], by_id: dict[str, DocNode], depth: int
) -> Page:
    reached = _reachable(node, by_fqn, depth)

    dependencies = sorted(
        (
            PageDependency(
                node_id=node_id,
                title=by_id[node_id].title,
                kind=by_id[node_id].kind,
                depth=level,
                doc_path=by_id[node_id].doc_path,
            )
            for node_id, level in reached.items()
        ),
        key=lambda item: (item.depth, item.title, item.node_id),
    )

    calls = _calls_of(node, node.title, 0)
    for dependency in dependencies:
        calls += _calls_of(by_id[dependency.node_id], dependency.title, dependency.depth)

    # Один и тот же вызов может быть записан дважды (перегрузки одного метода
    # сервиса), а в отчёте это шум: ключ вызова здесь и есть единица смысла.
    unique = {
        (call.http_method, call.route, call.discriminator, call.through, call.depth): call
        for call in calls
    }
    calls = [unique[key] for key in sorted(unique)]

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
        notes=_notes(routes, members, template, len(calls)),
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
    not_pages = sorted(
        (
            NotAPage(node_id=node.id, title=node.title, module=node.module, doc_path=node.doc_path)
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
        "without_features": sum(1 for page in pages if NOTE_NO_FEATURES in page.notes),
        "components_without_route": len(not_pages),
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
    where = "в самом компоненте" if call.depth == 0 else f"через {call.through}, шаг {call.depth}"
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
            f"членов {page.members}, внешний шаблон {'есть' if page.template else 'нет'}",
        )
    )
    for index, dependency in enumerate(page.dependencies):
        lines.append(
            _line(
                "тянет" if index == 0 else "",
                f"{dependency.title} ({dependency.kind}, шаг {dependency.depth})",
            )
        )
    for index, call in enumerate(page.calls):
        lines.append(_line("вызовы" if index == 0 else "", _call_line(call)))
    for index, note in enumerate(page.notes):
        lines.append(_line("заметки" if index == 0 else "", note))
    return lines


_SUMMARY: Final[tuple[tuple[str, str], ...]] = (
    ("pages", "страниц"),
    ("unanchorable", "из них якорь поставить нельзя: маршрут не восстановлен"),
    ("without_calls", "без единого вызова на этой глубине"),
    ("without_features", "без признаков функционала: ни членов, ни шаблона, ни вызовов"),
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

    if show_not_pages:
        lines += ["", f"Компоненты, страницами не ставшие ({len(report.not_pages)}):"]
        lines += [f"  {item.title}   [{item.module}]   {item.reason}" for item in report.not_pages]

    lines += ["", "Итого:"]
    lines += [f"  {report.counts[key]:>5}  {title}" for key, title in _SUMMARY]

    if report.counts["without_calls"]:
        lines += [
            "",
            "«Без вызовов» не означает «без API»: экшены NGXS создаются в теле метода, "
            "а не внедряются конструктором, поэтому путь dispatch -> стейт -> сервис "
            "обходом по зависимостям не виден.",
        ]
    return "\n".join(lines)


def report_json(report: PagesReport) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))


_CSV_HEADER: Final[tuple[str, ...]] = (
    "маршруты",
    "класс",
    "модуль",
    "документ",
    "членов",
    "шаблон",
    "правила",
    "тянет",
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
                page.members,
                "да" if page.template else "нет",
                " ".join(page.matched_rules),
                " ".join(f"{item.title}:{item.depth}" for item in page.dependencies),
                " ".join(
                    f"{call.http_method} /{call.route}"
                    + (f"[{call.discriminator}]" if call.discriminator else "")
                    for call in page.calls
                ),
                "; ".join(page.notes),
            ]
        )
    return buffer.getvalue()
