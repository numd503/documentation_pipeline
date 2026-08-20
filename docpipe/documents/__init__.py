"""Документ как файл: зоны, состояние, запись.

Пакет не принадлежит ни шагу 2, ни бизнес-слою. До выделения разбор зон
и правила приёмки лежали внутри `materialize`, и бизнес-слой зависел
от шага 2 как от модуля — а третий потребитель (детекция дрейфа
спецификаций, G17) добавил бы третью копию тех же правил.

Признак того, что выделение сделано верно, один: обе действующие приёмки
зовут одну функцию.
"""

from docpipe.documents.model import ParsedDocument, Segment, SegmentKind
from docpipe.documents.write import (
    RESERVED_KEYS,
    STATE_KEY,
    accepted_block,
    read_accepted,
    read_review,
    write_atomic,
)
from docpipe.documents.zones import (
    MANAGED_END,
    MANAGED_START,
    DocumentError,
    assemble,
    is_section_empty,
    parse_document,
    read_document,
)

__all__ = [
    "MANAGED_END",
    "MANAGED_START",
    "RESERVED_KEYS",
    "STATE_KEY",
    "DocumentError",
    "ParsedDocument",
    "Segment",
    "SegmentKind",
    "accepted_block",
    "assemble",
    "is_section_empty",
    "parse_document",
    "read_accepted",
    "read_document",
    "read_review",
    "write_atomic",
]
