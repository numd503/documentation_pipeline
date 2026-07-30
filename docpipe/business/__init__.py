"""Бизнес-слой: процессы, сущности и их связь с технической документацией.

Инвариант, из которого следует всё остальное: **техника ссылается на бизнес,
бизнес о технике не знает.** Поэтому рефакторинг физически не может сломать
бизнес-документ — худшее, что случается, это висящий якорь на технической
стороне, и чинит его тот же PR, который его сломал.

Пакет не импортирует `docpipe.dotnet`: система полиязычна, и якоря уровня
контракта одинаковы для .NET, Python и TypeScript.
"""

from docpipe.business.catalog import doc_path_for, load_catalog
from docpipe.business.fingerprint import business_hash, hashed_anchors
from docpipe.business.model import (
    ANCHOR_KINDS,
    Anchor,
    BusinessDoc,
    Capability,
    Catalog,
    Contract,
)
from docpipe.business.resolve import (
    DATA_DISPATCHED,
    REGISTRY_KIND,
    Resolution,
    ResolveContext,
    build_context,
    resolve,
    resolve_all,
)

__all__ = [
    "ANCHOR_KINDS",
    "DATA_DISPATCHED",
    "REGISTRY_KIND",
    "Anchor",
    "BusinessDoc",
    "Capability",
    "Catalog",
    "Contract",
    "ResolveContext",
    "Resolution",
    "build_context",
    "business_hash",
    "doc_path_for",
    "hashed_anchors",
    "load_catalog",
    "resolve",
    "resolve_all",
]
