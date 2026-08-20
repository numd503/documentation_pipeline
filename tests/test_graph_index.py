"""Наш индекс графа: проекция, ключи, хэш, запись (G01).

Тесты этого файла движок не зовут: всё, что здесь проверяется, — наши
правила, и они обязаны быть проверяемы без стороннего бинаря. Контрактные
тесты моста лежат отдельно, в `test_graph_bridge.py`, и пропускаются,
если бинаря нет.
"""

import re
import sqlite3
from pathlib import Path

import pytest

from docpipe.graph import (
    SCHEMA_VERSION,
    GraphEdge,
    GraphIndex,
    GraphMeta,
    GraphNode,
    IndexVersionError,
    language_of,
    logical_hash,
    node_key,
    project,
    read_index,
    read_meta,
    write_index,
)
from docpipe.graph.engine import EngineEdge, EngineGraph, EngineNode

BRIDGE = Path("docpipe/graph/engine.py")


def engine_node(qualified: str, file: str, label: str = "Method") -> EngineNode:
    return EngineNode(label=label, qualified_name=qualified, file=file)


# ──────────────────────────────────────────────────────────────────────────────
# Р13: словарь движка не выходит за пределы моста
# ──────────────────────────────────────────────────────────────────────────────


def test_engine_vocabulary_is_confined_to_the_bridge() -> None:
    """`grep` по `docpipe/**` за пределами моста не находит ни имени движка,
    ни его видов рёбер, ни слова Cypher (G01 п. 6, правило Р13).

    Проверка тестом, а не соглашением: цена замены источника равна цене
    переписывания одного модуля ровно до тех пор, пока это правило держится,
    и нарушают его по одной строчке за раз.
    """
    forbidden = (
        "codebase-memory",
        "cbm_cache_dir",
        "cypher",
        "match (",
        "query_graph",
        "index_repository",
        "get_graph_schema",
        "trace_path",
    )
    for path in Path("docpipe").rglob("*.py"):
        if path == BRIDGE:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for marker in forbidden:
            assert marker not in text, f"{path}: словарь движка вышел за мост — «{marker}»"


def test_engine_edge_kinds_are_translated_at_the_border() -> None:
    """Виды рёбер движка за мостом не встречаются: наружу выходят наши имена.

    Проверка по границам слова, а не по подстроке: у нас есть собственная
    константа `NOTE_NO_CALLS`, и подстрочный поиск объявил бы её нарушением.
    Тест, который ловит не то, что называет, вреднее отсутствующего.
    """
    pattern = re.compile(r"\b(CALLS|USAGE|INHERITS|IMPLEMENTS|DEFINES_METHOD)\b")
    for path in Path("docpipe").rglob("*.py"):
        if path == BRIDGE:
            continue
        found = pattern.search(path.read_text(encoding="utf-8"))
        assert found is None, f"{path}: вид ребра движка «{found.group()}» вышел за мост"


# ──────────────────────────────────────────────────────────────────────────────
# Ключи
# ──────────────────────────────────────────────────────────────────────────────


def test_node_key_is_file_plus_type_and_member() -> None:
    """Полное имя движка начинается с пути файла; ключ берёт хвост после него."""
    node = engine_node(
        "src.Web.Controllers.ManageController.ManageController.AddErrors",
        "src/Web/Controllers/ManageController.cs",
    )
    assert node_key(node) == "src/Web/Controllers/ManageController.cs#ManageController.AddErrors"


def test_node_key_does_not_depend_on_checkout_location() -> None:
    """Имя проекта движок выводит из абсолютного пути — в ключ оно не входит.

    Иначе два чекаута одного репозитория дали бы два разных индекса,
    а сравнить их было бы нечем.
    """
    node = engine_node("src.a.Klass.Do", "src/a.cs")
    assert "home" not in node_key(node)
    assert node_key(node) == "src/a.cs#Klass.Do"


def test_language_is_read_from_the_suffix() -> None:
    assert language_of("src/a.cs") == "csharp"
    assert language_of("web/app.component.ts") == "typescript"
    assert language_of("srv/main.py") == "python"
    assert language_of("readme.md") == ""


# ──────────────────────────────────────────────────────────────────────────────
# Проекция: источник рёбер задан по языкам
# ──────────────────────────────────────────────────────────────────────────────


def test_frontend_nodes_and_edges_are_not_projected() -> None:
    """Рёбра фронта в индекс не идут: их источник — наш разбор.

    Иначе каждый вызов фронта окажется в графе дважды, а там, где движок
    точнее нашего, его ребро вытеснит наше — и оба раза это будет молча.
    """
    graph = EngineGraph(
        nodes=(
            engine_node("src.a.Klass.Do", "src/a.cs"),
            engine_node("web.app.Comp.load", "web/app.ts"),
        ),
        edges=(EngineEdge(kind="calls", source="web.app.Comp.load", target="src.a.Klass.Do"),),
    )
    index, report = project(graph, "0.0.0")
    assert [node.key for node in index.nodes] == ["src/a.cs#Klass.Do"]
    assert index.edges == ()
    assert report["узлов фронта: источник не разбор, а наш web"] == 1
    assert report["рёбер фронта и отсеянных файлов: источник не разбор"] == 1


def test_edge_carries_provenance_and_engine_version() -> None:
    """У ребра, взятого у движка, `via` называет вид ребра движка и версию.

    Без этого нельзя ответить на вопрос «чьё ребро соврало», а он возникает
    при первом же неверном ответе.
    """
    graph = EngineGraph(
        nodes=(
            engine_node("src.a.A.One", "src/a.cs"),
            engine_node("src.b.B.Two", "src/b.cs"),
        ),
        edges=(EngineEdge(kind="calls", source="src.a.A.One", target="src.b.B.Two"),),
    )
    index, _ = project(graph, "0.6.0")
    assert len(index.edges) == 1
    assert index.edges[0].via == "engine:CALLS@0.6.0"
    assert index.edges[0].confidence == 1.0


def test_overloads_collide_and_are_counted_not_guessed() -> None:
    """Перегрузки у движка носят одно полное имя. Ключи совпадают, и это
    не разрешается догадкой: число идёт в отчёт, разведение — задача G02."""
    graph = EngineGraph(
        nodes=(
            engine_node("src.a.A.Do", "src/a.cs"),
            engine_node("src.a.A.Do", "src/a.cs"),
        )
    )
    index, report = project(graph, "0.0.0")
    assert len(index.nodes) == 1
    assert report["узлов с совпавшим ключом (перегрузки)"] == 1


def test_edges_into_filtered_files_are_dropped_with_a_number() -> None:
    """Ребро в отсеянный файл — ребро в никуда: его цель ничем не подтверждена."""
    graph = EngineGraph(
        nodes=(engine_node("src.a.A.One", "src/a.cs"),),
        edges=(EngineEdge(kind="calls", source="src.a.A.One", target="obj.g.G.Gen"),),
    )
    index, report = project(graph, "0.0.0")
    assert index.edges == ()
    assert report["рёбер фронта и отсеянных файлов: источник не разбор"] == 1


def test_structural_edges_of_the_engine_are_explained_not_lost() -> None:
    """«У разборщика 83 ребра, у нас 5» без объяснения читается как потеря
    семидесяти восьми. Число видов вне нашей модели печатается."""
    graph = EngineGraph(
        nodes=(engine_node("src.a.A.One", "src/a.cs"),),
        declared_edges={"calls": 0},
        read_edges={"calls": 0},
        declared_all={"CALLS": 0, "DEFINES": 40, "CONTAINS_FILE": 12},
    )
    _, report = project(graph, "0.0.0")
    assert report["рёбер иных видов у разбора (структурные, вне нашей модели)"] == 52


# ──────────────────────────────────────────────────────────────────────────────
# Хэш логического содержимого и запись
# ──────────────────────────────────────────────────────────────────────────────


def sample_index() -> GraphIndex:
    return GraphIndex(
        nodes=(
            GraphNode(key="a.cs#A.One", kind="member", name="One", owner="A", file="a.cs"),
            GraphNode(key="b.cs#B.Two", kind="member", name="Two", owner="B", file="b.cs"),
        ),
        edges=(
            GraphEdge(kind="calls", source="a.cs#A.One", target="b.cs#B.Two", via="engine:CALLS@1"),
        ),
    )


def test_logical_hash_ignores_order() -> None:
    """Порядок на входе на хэш не влияет: сортировка идёт по нашим ключам (Р4)."""
    first = sample_index()
    second = GraphIndex(nodes=tuple(reversed(first.nodes)), edges=first.edges)
    assert logical_hash(first) == logical_hash(second)


def test_logical_hash_covers_node_attributes() -> None:
    """Хэш только по рёбрам молча пропустил бы сменившийся провенанс или имя
    узла — прямое требование G01 п. 2."""
    first = sample_index()
    changed = GraphIndex(
        nodes=(first.nodes[0].model_copy(update={"name": "Другое"}), first.nodes[1]),
        edges=first.edges,
    )
    assert logical_hash(first) != logical_hash(changed)


def test_generation_is_derived_from_content(tmp_path: Path) -> None:
    """Один вход — одно поколение. Времени в индексе нет вовсе: иначе два
    прогона на одном входе выглядели бы как разные индексы."""
    path = tmp_path / "graph.db"
    first = write_index(path, sample_index(), GraphMeta(generation=""))
    second = write_index(path, sample_index(), GraphMeta(generation=""))
    assert first.generation == second.generation == logical_hash(sample_index())


def test_write_is_atomic_and_leaves_no_temporary(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    write_index(path, sample_index(), GraphMeta(generation=""))
    assert path.is_file()
    assert not list(tmp_path.glob("*.tmp"))


def test_round_trip_preserves_nodes_and_edges(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    write_index(path, sample_index(), GraphMeta(generation=""))
    loaded = read_index(path)
    assert sorted(node.key for node in loaded.nodes) == ["a.cs#A.One", "b.cs#B.Two"]
    assert loaded.edges[0].via == "engine:CALLS@1"


def test_version_mismatch_refuses_with_a_command(tmp_path: Path) -> None:
    """Несовпадение версии схемы — отказ с командой пересборки, а не
    смешанное чтение: индекс другой версии отвечает по-другому, и понять
    это по ответу нельзя."""
    path = tmp_path / "graph.db"
    write_index(path, sample_index(), GraphMeta(generation=""))
    connection = sqlite3.connect(path)
    connection.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(IndexVersionError, match="graph build"):
        read_meta(path)


def test_meta_carries_engine_version_and_checksum(tmp_path: Path) -> None:
    path = tmp_path / "graph.db"
    meta = write_index(
        path,
        sample_index(),
        GraphMeta(generation="", engine_version="0.6.0", engine_checksum="sha256:x", repo="демо"),
    )
    stored = read_meta(path)
    assert stored.engine_version == "0.6.0"
    assert stored.engine_checksum == "sha256:x"
    assert stored.repo == "демо"
    assert stored.schema_version == SCHEMA_VERSION == meta.schema_version


def test_graph_does_not_touch_the_manifest() -> None:
    """`doc-tree.json` не меняется ни на байт: манифест о существовании
    графа не знает (G01 п. 11, правило Р3).

    Проверка структурная — по импортам: сборка манифеста и сборка индекса
    не имеют общего кода записи, и появление такого импорта означало бы,
    что граф начал писать в манифест.
    """
    for path in Path("docpipe/graph").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from docpipe.emit" not in text, f"{path}: индекс не пишет манифест"
        assert "from docpipe.tree" not in text, f"{path}: индекс не собирает манифест"
        assert "write_manifest" not in text, f"{path}: индекс не пишет манифест"


def test_composition_roots_survive_the_round_trip(tmp_path: Path) -> None:
    """Файлы сборки контейнера — часть паспорта, а не пометка на узлах.

    У самого частого композиционного корня — `Program.cs` на top-level
    statements — узлов нет вовсе, помечать было бы нечего.
    """
    index = GraphIndex(nodes=(GraphNode(key="a.cs#A", kind="type", name="A", file="a.cs"),))
    meta = GraphMeta(generation="", composition_roots=("Program.cs", "Startup.cs"))
    target = tmp_path / "index.db"
    write_index(target, index, meta)
    assert read_meta(target).composition_roots == ("Program.cs", "Startup.cs")
