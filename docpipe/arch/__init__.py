"""Нормализованный реестр архитектурных элементов (R03).

Контракт между разведкой и графом: слева от него догадки и ручная работа,
справа детерминированная сборка. Пакет не знает ни об одном конкретном
репозитории — что читать и как называются теги, знают адаптеры (R04).
"""

from docpipe.arch.load import (
    ArchProblem,
    check_document,
    load_arch_registry,
    load_optional,
    read_document,
)
from docpipe.arch.model import (
    ARCH_VERSION,
    ArchRecord,
    ArchRegistry,
    DataField,
    DataRecord,
    EntryPointRecord,
    LayerRecord,
    SeamRecord,
    Source,
)
from docpipe.arch.status import SourceStatus, format_statuses, source_statuses, statuses_json

__all__ = [
    "ARCH_VERSION",
    "ArchProblem",
    "ArchRecord",
    "ArchRegistry",
    "DataField",
    "DataRecord",
    "EntryPointRecord",
    "LayerRecord",
    "SeamRecord",
    "Source",
    "SourceStatus",
    "check_document",
    "format_statuses",
    "load_arch_registry",
    "load_optional",
    "read_document",
    "source_statuses",
    "statuses_json",
]
