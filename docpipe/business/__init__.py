"""Бизнес-слой: процессы, сущности и их связь с технической документацией.

Инвариант, из которого следует всё остальное: **техника ссылается на бизнес,
бизнес о технике не знает.** Поэтому рефакторинг физически не может сломать
бизнес-документ — худшее, что случается, это висящий якорь на технической
стороне, и чинит его тот же PR, который его сломал.

Пакет не импортирует `docpipe.dotnet`: система полиязычна, и якоря уровня
контракта одинаковы для .NET, Python и TypeScript.
"""

from docpipe.business.catalog import doc_path_for, load_catalog
from docpipe.business.model import (
    ANCHOR_KINDS,
    Anchor,
    BusinessDoc,
    Capability,
    Catalog,
    Contract,
)

__all__ = [
    "ANCHOR_KINDS",
    "Anchor",
    "BusinessDoc",
    "Capability",
    "Catalog",
    "Contract",
    "doc_path_for",
    "load_catalog",
]
