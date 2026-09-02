"""Шаг 2: материализация документов по манифесту.

Пакет намеренно не импортирует `docpipe.dotnet`: разбор зон, шаблоны, план и
запись от языка исходников не зависят. На этом же стоит бизнес-слой — он
переиспользует разбор документа целиком.
"""

from docpipe.documents.model import ParsedDocument, Segment
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
    "DocumentError",
    "ParsedDocument",
    "Segment",
    "assemble",
    "is_section_empty",
    "parse_document",
    "read_document",
]
