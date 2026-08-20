"""Полный цикл сборки индекса: движок → мост → наш индекс.

Здесь заканчивается всё, что знает о движке, и начинается то, что читают
остальные фичи. Правило проекции одно и следует из модели графа плана:
**источник рёбер задан по языкам**.

- .NET и Python — движок (плюс довершение G04);
- TypeScript — наш разбор (`docpipe/web`): цепочку NGXS, спред импортированных
  массивов роутов и различитель маршрутов движок не знает. Его рёбра по TS
  в индекс **не проецируются** — иначе каждый вызов фронта окажется в графе
  дважды, а где движок точнее нашего, его ребро вытеснит наше.

Отсеянное не пропадает молча: числа по категориям идут в паспорт индекса
и дальше в отчёт о неполноте.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from docpipe.arch.model import ArchRegistry
from docpipe.graph.binding import BindingReport, binds, complete, dispatch
from docpipe.graph.data import DataReport
from docpipe.graph.data import collect as collect_data
from docpipe.graph.data import from_registry as data_from_registry
from docpipe.graph.data import from_sql as data_from_sql
from docpipe.graph.data import merge as merge_data
from docpipe.graph.engine import Engine, EngineGraph, EngineNode, EngineRun
from docpipe.graph.entrypoints import EntryPointReport, from_manifest, from_registry, link
from docpipe.graph.grid import GridReport
from docpipe.graph.grid import seams as grid_seams
from docpipe.graph.match import MatchReport, apply, match
from docpipe.graph.model import GraphEdge, GraphIndex, GraphMeta, GraphNode
from docpipe.graph.reach import Reachability, compute
from docpipe.graph.search import SearchEntry
from docpipe.graph.search import entries as search_entries
from docpipe.graph.web import WebReport
from docpipe.graph.web import collect as collect_web
from docpipe.model import Manifest

# Расширение → язык. Служит одному решению: чьи рёбра брать. Список короткий
# намеренно — язык, которого здесь нет, попадает в «прочее» и виден в отчёте.
LANG_BY_SUFFIX: Final[dict[str, str]] = {
    ".cs": "csharp",
    ".fs": "fsharp",
    ".vb": "vbnet",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".vue": "typescript",
    ".java": "java",
    ".go": "go",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
}

# Языки, чьи узлы и рёбра берутся не у движка. Перечень положительный:
# «всё, кроме» однажды молча включит язык, разбора которого у нас нет.
OUR_LANGUAGES: Final[frozenset[str]] = frozenset({"typescript", "javascript"})


@dataclass(frozen=True)
class BuildResult:
    index: GraphIndex
    meta: GraphMeta
    run: EngineRun
    # Отчёт сопоставления с манифестом. `None` — манифест не давали,
    # и это законно: индекс собирается и без него, просто у узлов не будет
    # ни имени документа, ни модуля.
    match: MatchReport | None = None
    # Отчёт о корнях. `None` — ни реестра, ни манифеста не давали.
    entry_points: EntryPointReport | None = None
    binding: BindingReport | None = None
    data: DataReport | None = None
    grid: GridReport | None = None
    reachability: Reachability | None = None
    searchable: list[SearchEntry] = field(default_factory=list)
    web: WebReport | None = None


def language_of(file: str) -> str:
    suffix = Path(file).suffix.lower()
    return LANG_BY_SUFFIX.get(suffix, "")


def node_key(node: EngineNode) -> str:
    """Наш ключ узла: файл и хвост полного имени.

    Полное имя движка собрано как «путь файла точками + тип + член», то есть
    начинается с пути. Хвост после пути — это «тип.член», и он не зависит
    ни от номеров строк, ни от того, где лежит чекаут.

    Ключ **не** включает сигнатуру параметров: перегрузки разводит G02
    по манифесту, где параметры и так разобраны. Здесь совпадение ключей
    считается и попадает в отчёт, а не разрешается догадкой.
    """
    tail = node.qualified_name
    if node.file:
        prefix = Path(node.file).with_suffix("").as_posix().replace("/", ".")
        if tail.startswith(prefix + "."):
            tail = tail[len(prefix) + 1 :]
    return f"{node.file}#{tail}" if node.file else tail


def _owner_and_name(key_tail: str) -> tuple[str, str]:
    parts = key_tail.rsplit(".", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", key_tail)


def project(graph: EngineGraph, engine_version: str) -> tuple[GraphIndex, dict[str, int]]:
    """Перевести граф движка в наш индекс. Единственное место такого перевода."""
    report: dict[str, int] = {}

    def count(category: str, delta: int = 1) -> None:
        report[category] = report.get(category, 0) + delta

    nodes: dict[str, GraphNode] = {}
    for item in graph.nodes:
        lang = language_of(item.file)
        if lang in OUR_LANGUAGES:
            count("узлов фронта: источник не разбор, а наш web")
            continue
        key = node_key(item)
        tail = key.split("#", 1)[-1]
        owner, name = _owner_and_name(tail)
        if key in nodes:
            # Перегрузки: у движка полное имя одно на все, и развести их
            # здесь нечем. Число идёт в отчёт, разрешение — задача G02.
            count("узлов с совпавшим ключом (перегрузки)")
            continue
        nodes[key] = GraphNode(
            key=key,
            kind="member" if item.label in ("Method", "Function") else "type",
            name=name,
            owner=owner,
            file=item.file,
            lang=lang or "прочее",
            source="engine",
            attributes={"engine_label": item.label},
        )

    by_qualified: dict[str, str] = {}
    for item in graph.nodes:
        if language_of(item.file) in OUR_LANGUAGES:
            continue
        by_qualified.setdefault(item.qualified_name, node_key(item))

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in graph.edges:
        source = by_qualified.get(edge.source)
        target = by_qualified.get(edge.target)
        if source is None or target is None:
            count("рёбер фронта и отсеянных файлов: источник не разбор")
            continue
        if source not in nodes or target not in nodes:
            count("рёбер с концом вне проекции")
            continue
        identity = (edge.kind, source, target)
        if identity in seen:
            count("рёбер-дублей после сведения ключей")
            continue
        seen.add(identity)
        edges.append(
            GraphEdge(
                kind=edge.kind,
                source=source,
                target=target,
                # Провенанс называет вид ребра движка и версию его индекса:
                # без этого нельзя ответить, чьё ребро соврало.
                via=f"engine:{edge.kind.upper()}@{engine_version}",
            )
        )

    for kind, declared in graph.declared_edges.items():
        read = graph.read_edges.get(kind, 0)
        if declared > read:
            count(f"рёбер {kind} вне наших меток", declared - read)
    if graph.http_noise:
        # Мусорные рёбра разбора: литералы регулярок из минифицированного JS,
        # принятые за маршруты. Правило отсева записано в мосте, срабатывания
        # считаются здесь.
        count("рёбер разбора отсеяно как мусор (литералы регулярок)", graph.http_noise)

    for reason, number in graph.filtered_nodes.items():
        count(f"узлов разбора отсеяно: {reason}", number)

    # Виды рёбер и узлов, которых нет в нашей модели. Не потеря и не мусор:
    # структурные рёбра (файл содержит файл, тип объявляет член) выражены
    # у нас иначе. Но число обязано быть видно, иначе «у разборщика 83 ребра,
    # у нас 5» читается как потеря семидесяти восьми.
    ours = {kind.upper() for kind in graph.declared_edges}
    outside = sum(number for name, number in graph.declared_all.items() if name.upper() not in ours)
    if outside:
        count("рёбер иных видов у разбора (структурные, вне нашей модели)", outside)

    return GraphIndex(nodes=tuple(nodes.values()), edges=tuple(edges)), report


def build(
    engine: Engine,
    root: Path,
    *,
    is_excluded: object = None,
    manifest: Manifest | None = None,
    arch: ArchRegistry | None = None,
    web: Manifest | None = None,
    progress: Callable[[str], None] | None = None,
) -> BuildResult:
    """Полный цикл: проверка бинаря, индексация, чтение, проекция, сопоставление.

    `progress` вызывается на границах этапов. Это не украшение: пользователь,
    ждущий минуты без единого сообщения, решит, что инструмент завис,
    и прервёт прогон — а прерванный прогон выглядит как сломанный инструмент.
    """
    say = progress or (lambda _message: None)

    say("проверяю разборщик")
    version = engine.check()
    say("индексирую репозиторий")
    run = engine.index(root)
    say(f"читаю граф: узлов {run.nodes}, рёбер {run.edges}")
    graph = engine.read(run.project, is_excluded=is_excluded)  # type: ignore[arg-type]
    say("перевожу в наши термины")
    index, report = project(graph, version)
    # Полное имя разбора → узел: обращения приходят в его именах, а индекс
    # адресуется нашими ключами, и перевод обязан быть один.
    _by_name = {item.qualified_name: item for item in graph.nodes}

    match_report: MatchReport | None = None
    if manifest is not None:
        say("сопоставляю с манифестом")
        attributes, match_report = match(index.nodes, manifest)
        index = GraphIndex(nodes=apply(index.nodes, attributes), edges=index.edges)
        report.update(match_report.as_counts())

    # Связывание идёт ПОСЛЕ сопоставления и ДО корней: довершённые рёбра
    # вызова участвуют в достижимости так же, как чужие, а корни связываются
    # уже с полным набором узлов.
    # Диспетчеризация по типу запроса: обе половины приходят из манифеста —
    # объявление из базового типа обработчика, место отправки из тела
    # (создание объекта запроса). Сторонний разбор здесь не участвует:
    # создание объекта он обращением не считает, и первая редакция,
    # опиравшаяся на его обращения, давала только петли.
    if manifest is not None and manifest.dispatch_handlers:
        say("собираю диспетчеризацию по типу запроса")
        dispatch_edges, dispatch_report = dispatch(
            index.nodes, tuple(manifest.dispatch_sends), list(manifest.dispatch_handlers)
        )
        index = GraphIndex(nodes=index.nodes, edges=index.edges + tuple(dispatch_edges))
        report.update(dispatch_report)

    binding_report: BindingReport | None = None
    if manifest is not None and manifest.di_registrations:
        say("связываю вызовы по регистрациям контейнера")
        bind_edges, binding_report = binds(list(manifest.di_registrations), index.nodes, manifest)
        completed, binding_report = complete(
            index.nodes, index.edges, bind_edges, binding_report, manifest
        )
        index = GraphIndex(
            nodes=index.nodes, edges=index.edges + tuple(bind_edges) + tuple(completed)
        )
        report.update(binding_report.as_counts())

    # Корни собираются ПОСЛЕ сопоставления: связывание с кодом опирается
    # на полное имя типа, а оно приходит из манифеста.
    entry_report: EntryPointReport | None = None
    if arch is not None or manifest is not None:
        say("собираю точки входа")
        entries: list[GraphNode] = []
        if arch is not None:
            entries.extend(from_registry(arch))
        if manifest is not None:
            entries.extend(from_manifest(manifest))
        dispatches, entry_report = link(entries, index.nodes, manifest)
        index = GraphIndex(
            nodes=index.nodes + tuple(entries), edges=index.edges + tuple(dispatches)
        )
        report.update(entry_report.as_counts())

    # Швы через кластер: вызов доходит до члена интерфейса, а связь
    # «интерфейс → зарегистрированная реализация» объявлена реестром.
    grid_report: GridReport | None = None
    if arch is not None:
        say("свожу швы через кластер")
        grid_edges, grid_report = grid_seams(index.nodes, index.edges)
        index = GraphIndex(nodes=index.nodes, edges=index.edges + tuple(grid_edges))
        report.update(grid_report.as_counts())

    # Фронт: узлы, цепочка и шов с бэкендом. Идёт ПОСЛЕ точек входа —
    # шов сводится с уже существующими эндпоинтами.
    web_report: WebReport | None = None
    if web is not None:
        say("свожу фронт с бэкендом")
        endpoints = {
            node.key
            for node in index.nodes
            if node.kind == "entry_point" and node.attributes.get("entry_kind") == "http_endpoint"
        }
        web_nodes, web_edges, web_report = collect_web(web, endpoints)
        index = GraphIndex(
            nodes=index.nodes + tuple(web_nodes), edges=index.edges + tuple(web_edges)
        )
        report.update(web_report.as_counts())

        # Перекрёстная проверка: тот же счёт вызовов, сделанный сторонним
        # разбором. Расходятся формы, которые одна из сторон не видит, —
        # это дешёвый детектор дыр в обоих, и он живёт в отчёте, а не
        # в графе: рёбра разбора по TypeScript в индекс не проецируются.
        ours = {
            (call.key.http_method.upper(), call.key.route)
            for node in web.nodes
            for call in node.web_calls
        }
        theirs = {(method, route.strip("/").lower()) for method, route in graph.http_routes}
        normalized = {(method, route.strip("/").lower()) for method, route in ours}
        report["маршрутов фронта: у нас"] = len(ours)
        report["маршрутов фронта: у разбора"] = len(theirs)
        report["маршрутов фронта: только у нас"] = len(normalized - theirs)
        report["маршрутов фронта: только у разбора"] = len(theirs - normalized)

    # Узлы данных: из объявлений кода и из реестра. Сливаются по ключу —
    # таблица, объявленная в реестре и использованная через контекст,
    # это один узел с двумя источниками.
    data_report: DataReport | None = None
    if manifest is not None:
        say("собираю узлы данных")
        data_nodes, data_edges, data_report = collect_data(manifest, index.nodes)
        index = GraphIndex(
            nodes=index.nodes + tuple(data_nodes), edges=index.edges + tuple(data_edges)
        )
        report.update(data_report.as_counts())
    if manifest is not None and (manifest.sql_objects or manifest.sql_usages):
        say("читаю SQL: процедуры и обращения к таблицам")
        sql_nodes, sql_edges, sql_report = data_from_sql(manifest, index.nodes)
        merged_sql, joined_sql = merge_data(index.nodes, sql_nodes)
        index = GraphIndex(nodes=tuple(merged_sql), edges=index.edges + tuple(sql_edges))
        report.update(sql_report)
        if joined_sql:
            report["узлов данных сведено с SQL"] = joined_sql

    if arch is not None:
        roots = tuple(node for node in index.nodes if node.kind == "entry_point")
        registry_nodes, registry_edges, registry_report = data_from_registry(arch, roots)
        merged, joined = merge_data(index.nodes, registry_nodes)
        index = GraphIndex(nodes=tuple(merged), edges=index.edges + tuple(registry_edges))
        report.update(registry_report)
        if joined:
            report["узлов данных сведено из двух источников"] = joined

    # Достижимость считается последней: к этому моменту в индексе есть все
    # рёбра — чужие вызовы, наши довершения, диспетчеризация и обращения
    # к данным. Посчитать её раньше значит посчитать по половине графа.
    say(f"считаю достижимость: узлов {len(index.nodes)}, рёбер {len(index.edges)}")
    reachability = compute(index)
    say("собираю пространство поиска")
    searchable = search_entries(index, manifest)

    meta = GraphMeta(
        generation="",
        roots=reachability.roots,
        engine_version=version,
        engine_checksum=engine.expected_sha256,
        repo=root.resolve().name,
        counts={
            "nodes": len(index.nodes),
            "edges": len(index.edges),
            "roots": len(reachability.roots),
            "engine_nodes": run.nodes,
            "engine_edges": run.edges,
        },
        report=report,
    )
    return BuildResult(
        index=index,
        meta=meta,
        run=run,
        match=match_report,
        entry_points=entry_report,
        binding=binding_report,
        data=data_report,
        grid=grid_report,
        reachability=reachability,
        searchable=searchable,
        web=web_report,
    )
