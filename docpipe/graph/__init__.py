"""Индекс связей: сборка и чтение.

Наружу выходит только наш индекс. Модуль-мост (`engine.py`) — единственный,
кто знает о существовании стороннего разборщика; остальные фичи графа читают
отсюда и о нём не знают ничего (правило Р13).
"""

from docpipe.graph.binding import BindingReport, binds, complete
from docpipe.graph.build import BuildResult, build, language_of, node_key, project
from docpipe.graph.data import DataReport, data_key
from docpipe.graph.entrypoints import EntryPointReport, entry_key
from docpipe.graph.identity import member_key, parameter_types, symbol_member_key, symbol_type_key
from docpipe.graph.match import MatchReport, match
from docpipe.graph.model import (
    EDGE_KINDS,
    NODE_KINDS,
    SCHEMA_VERSION,
    GraphEdge,
    GraphIndex,
    GraphMeta,
    GraphNode,
)
from docpipe.graph.reach import Reachability, compute, path, shared_components
from docpipe.graph.store import (
    IndexVersionError,
    logical_hash,
    read_index,
    read_meta,
    read_reach,
    write_index,
)

__all__ = [
    "EDGE_KINDS",
    "NODE_KINDS",
    "SCHEMA_VERSION",
    "BindingReport",
    "BuildResult",
    "DataReport",
    "Reachability",
    "EntryPointReport",
    "MatchReport",
    "GraphEdge",
    "GraphIndex",
    "GraphMeta",
    "GraphNode",
    "IndexVersionError",
    "binds",
    "build",
    "complete",
    "compute",
    "data_key",
    "entry_key",
    "language_of",
    "match",
    "member_key",
    "parameter_types",
    "symbol_member_key",
    "symbol_type_key",
    "logical_hash",
    "node_key",
    "project",
    "path",
    "read_index",
    "read_meta",
    "read_reach",
    "shared_components",
    "write_index",
]
