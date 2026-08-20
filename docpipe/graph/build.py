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

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from docpipe.graph.engine import Engine, EngineGraph, EngineNode, EngineRun
from docpipe.graph.model import GraphEdge, GraphIndex, GraphMeta, GraphNode

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
) -> BuildResult:
    """Полный цикл: проверка бинаря, индексация, чтение, проекция."""
    version = engine.check()
    run = engine.index(root)
    graph = engine.read(run.project, is_excluded=is_excluded)  # type: ignore[arg-type]
    index, report = project(graph, version)
    meta = GraphMeta(
        generation="",
        engine_version=version,
        engine_checksum=engine.expected_sha256,
        repo=root.resolve().name,
        counts={
            "nodes": len(index.nodes),
            "edges": len(index.edges),
            "engine_nodes": run.nodes,
            "engine_edges": run.edges,
        },
        report=report,
    )
    return BuildResult(index=index, meta=meta, run=run)
