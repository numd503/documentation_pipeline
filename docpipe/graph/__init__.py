"""Индекс связей: сборка и чтение.

Наружу выходит только наш индекс. Модуль-мост (`engine.py`) — единственный,
кто знает о существовании стороннего разборщика; остальные фичи графа читают
отсюда и о нём не знают ничего (правило Р13).
"""

from docpipe.graph.build import BuildResult, build, language_of, node_key, project
from docpipe.graph.model import (
    EDGE_KINDS,
    NODE_KINDS,
    SCHEMA_VERSION,
    GraphEdge,
    GraphIndex,
    GraphMeta,
    GraphNode,
)
from docpipe.graph.store import (
    IndexVersionError,
    logical_hash,
    read_index,
    read_meta,
    write_index,
)

__all__ = [
    "EDGE_KINDS",
    "NODE_KINDS",
    "SCHEMA_VERSION",
    "BuildResult",
    "GraphEdge",
    "GraphIndex",
    "GraphMeta",
    "GraphNode",
    "IndexVersionError",
    "build",
    "language_of",
    "logical_hash",
    "node_key",
    "project",
    "read_index",
    "read_meta",
    "write_index",
]
