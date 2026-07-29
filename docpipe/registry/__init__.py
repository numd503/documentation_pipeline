"""Реестры платформы: файлы, объявляющие точки входа.

В АС CF джобы, workflow и обработчики событий объявлены строками в XML и
исполняются универсальными раннерами, получающими имя типа из данных. Литерала
конкретного workflow в C# не существует **структурно**, поэтому граф триггеров
не восстанавливается статическим анализом — его читают отсюда.
"""

from docpipe.registry.anchors import AnchorTarget, ResolvedAnchor, resolve_anchors
from docpipe.registry.config import load_registries
from docpipe.registry.model import (
    ChildSpec,
    FollowChildSpec,
    FollowSpec,
    RegistryItem,
    RegistryResult,
    RegistrySpec,
)
from docpipe.registry.reader import read_registry

__all__ = [
    "AnchorTarget",
    "ChildSpec",
    "FollowChildSpec",
    "FollowSpec",
    "RegistryItem",
    "RegistryResult",
    "RegistrySpec",
    "ResolvedAnchor",
    "load_registries",
    "read_registry",
    "resolve_anchors",
]
