"""Наш индекс на диске: SQLite, локальный, не в git.

Три свойства, каждое из которых ломается молча, если о нём не помнить.

**Запись атомарна.** Сборка пишет во временный файл и подменяет индекс
переименованием. Читатель, пришедший во время пересборки, иначе получит
полуиндекс без единого сообщения об ошибке.

**Поколение выводится из содержимого.** Один вход — одно поколение;
времени в индексе нет вовсе, иначе два прогона на одном входе выглядели бы
как разные индексы, и сравнить их было бы нечем.

**Детерминизм логический, а не побайтовый (Р4).** Побайтовое равенство
SQLite обеспечить трудно; вместо него хэш от отсортированного набора узлов
с атрибутами и рёбер, записанный рядом.
"""

import json
import os
import sqlite3
from pathlib import Path

from docpipe.graph.model import SCHEMA_VERSION, GraphEdge, GraphIndex, GraphMeta, GraphNode
from docpipe.graph.reach import Reachability
from docpipe.graph.search import SearchEntry
from docpipe.graph.search import write as write_search
from docpipe.hashing import stable_hash

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE nodes (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    owner TEXT NOT NULL,
    file TEXT NOT NULL,
    lang TEXT NOT NULL,
    source TEXT NOT NULL,
    attributes TEXT NOT NULL
);
CREATE TABLE edges (
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    via TEXT NOT NULL,
    confidence REAL NOT NULL,
    attributes TEXT NOT NULL
);
CREATE TABLE reach (
    node TEXT PRIMARY KEY,
    mask BLOB NOT NULL,
    component INTEGER NOT NULL
);
CREATE INDEX edges_source ON edges (source, kind);
CREATE INDEX edges_target ON edges (target, kind);
CREATE INDEX nodes_kind ON nodes (kind);
"""


class IndexVersionError(RuntimeError):
    """Индекс собран другой версией схемы: читать нельзя, надо пересобрать."""


def logical_hash(index: GraphIndex) -> str:
    """Хэш логического содержимого: узлы с атрибутами и рёбра, отсортированные."""
    return stable_hash(
        {
            "schema": SCHEMA_VERSION,
            "nodes": [node.model_dump(mode="json") for node in sorted_nodes(index.nodes)],
            "edges": [edge.model_dump(mode="json") for edge in sorted_edges(index.edges)],
        }
    )


def sorted_nodes(nodes: tuple[GraphNode, ...]) -> list[GraphNode]:
    return sorted(nodes, key=lambda node: (node.kind, node.key))


def sorted_edges(edges: tuple[GraphEdge, ...]) -> list[GraphEdge]:
    return sorted(edges, key=lambda edge: (edge.kind, edge.source, edge.target, edge.via))


def _unique(nodes: list[GraphNode]) -> tuple[list[GraphNode], int]:
    """Узлы без повторов ключа.

    Последняя защита перед записью, а не замена аккуратности источников:
    источники сводят свои совпадения сами и считают их. Здесь ловится
    столкновение **между** источниками — например, тип из разбора и узел
    фронта в одном файле. Падение с `UNIQUE constraint failed` на боевом
    репозитории объясняет ровно ничего, а число объясняет.
    """
    seen: dict[str, GraphNode] = {}
    dropped = 0
    for node in nodes:
        if node.key in seen:
            dropped += 1
            continue
        seen[node.key] = node
    return list(seen.values()), dropped


def write_index(
    path: Path,
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability | None = None,
    searchable: list[SearchEntry] | None = None,
) -> GraphMeta:
    """Записать индекс атомарно и вернуть паспорт с проставленным поколением.

    Достижимость в хэш логического содержимого **не входит**: она выводится
    из узлов и рёбер, и включать её значило бы хэшировать одно и то же дважды.
    Порядок корней при этом хранится: биты маски назначены именно ему,
    и без него маска — набор чисел без смысла.
    """
    nodes, dropped = _unique(sorted_nodes(index.nodes))
    if dropped:
        index = GraphIndex(nodes=tuple(nodes), edges=index.edges)
        meta = meta.model_copy(
            update={"report": {**meta.report, "узлов с совпавшим ключом отброшено": dropped}}
        )

    generation = logical_hash(index)
    meta = meta.model_copy(update={"generation": generation, "schema_version": SCHEMA_VERSION})

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(_SCHEMA)
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            sorted(_meta_rows(meta)),
        )
        connection.executemany(
            "INSERT INTO nodes (key, kind, name, owner, file, lang, source, attributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    node.key,
                    node.kind,
                    node.name,
                    node.owner,
                    node.file,
                    node.lang,
                    node.source,
                    json.dumps(node.attributes, sort_keys=True, ensure_ascii=False),
                )
                for node in sorted_nodes(index.nodes)
            ],
        )
        connection.executemany(
            "INSERT INTO edges (kind, source, target, via, confidence, attributes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    edge.kind,
                    edge.source,
                    edge.target,
                    edge.via,
                    edge.confidence,
                    json.dumps(edge.attributes, sort_keys=True, ensure_ascii=False),
                )
                for edge in sorted_edges(index.edges)
            ],
        )
        if searchable:
            write_search(connection, searchable)
        if reachability is not None:
            connection.executemany(
                "INSERT INTO reach (node, mask, component) VALUES (?, ?, ?)",
                [
                    (
                        key,
                        mask.to_bytes((mask.bit_length() + 7) // 8 or 1, "big"),
                        reachability.component_size.get(key, 1),
                    )
                    for key, mask in sorted(reachability.masks.items())
                ],
            )
        connection.commit()
    finally:
        connection.close()

    # Подмена переименованием: читатель либо видит прежний индекс целиком,
    # либо новый целиком, и никогда — половину.
    os.replace(temporary, path)
    return meta


def _meta_rows(meta: GraphMeta) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [
        ("schema_version", meta.schema_version),
        ("generation", meta.generation),
        ("engine_version", meta.engine_version),
        ("engine_checksum", meta.engine_checksum),
        ("repo", meta.repo),
        ("counts", json.dumps(meta.counts, sort_keys=True, ensure_ascii=False)),
        ("report", json.dumps(meta.report, sort_keys=True, ensure_ascii=False)),
        ("roots", json.dumps(meta.roots, ensure_ascii=False)),
        ("composition_roots", json.dumps(meta.composition_roots, ensure_ascii=False)),
    ]
    return rows


def read_meta(path: Path) -> GraphMeta:
    """Прочитать паспорт индекса. Несовпадение версии — отказ."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = dict(connection.execute("SELECT key, value FROM meta").fetchall())
    finally:
        connection.close()
    version = rows.get("schema_version", "")
    if version != SCHEMA_VERSION:
        raise IndexVersionError(
            f"индекс собран схемой {version or '(не указана)'}, поддерживается {SCHEMA_VERSION}. "
            "Пересоберите: docpipe graph build --root <репозиторий>"
        )
    return GraphMeta(
        schema_version=version,
        generation=rows.get("generation", ""),
        engine_version=rows.get("engine_version", ""),
        engine_checksum=rows.get("engine_checksum", ""),
        repo=rows.get("repo", ""),
        counts=json.loads(rows.get("counts", "{}")),
        report=json.loads(rows.get("report", "{}")),
        roots=tuple(json.loads(rows.get("roots", "[]"))),
        composition_roots=tuple(json.loads(rows.get("composition_roots", "[]"))),
    )


def read_index(path: Path) -> GraphIndex:
    """Прочитать индекс целиком. Для запросов это не нужно — только для проверок."""
    read_meta(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        nodes = [
            GraphNode(
                key=row[0],
                kind=row[1],
                name=row[2],
                owner=row[3],
                file=row[4],
                lang=row[5],
                source=row[6],
                attributes=json.loads(row[7]),
            )
            for row in connection.execute(
                "SELECT key, kind, name, owner, file, lang, source, attributes FROM nodes"
            )
        ]
        edges = [
            GraphEdge(
                kind=row[0],
                source=row[1],
                target=row[2],
                via=row[3],
                confidence=row[4],
                attributes=json.loads(row[5]),
            )
            for row in connection.execute(
                "SELECT kind, source, target, via, confidence, attributes FROM edges"
            )
        ]
    finally:
        connection.close()
    return GraphIndex(nodes=tuple(nodes), edges=tuple(edges))


def read_reach(path: Path) -> Reachability:
    """Прочитать предвычисленную достижимость.

    Порядок корней берётся из паспорта: биты маски назначены ему, и прочитать
    маску, не зная порядка, нельзя — числа совпадут, а смысл будет чужой.
    """
    meta = read_meta(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT node, mask, component FROM reach").fetchall()
    finally:
        connection.close()
    return Reachability(
        roots=meta.roots,
        masks={row[0]: int.from_bytes(row[1], "big") for row in rows},
        component_size={row[0]: row[2] for row in rows},
    )
