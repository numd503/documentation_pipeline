"""Нормализованный реестр архитектурных элементов (R03).

Контракт между разведкой и графом: слева от него догадки и ручная работа,
справа детерминированная сборка. Пакет не знает ни об одном конкретном
репозитории — что читать и как называются теги, знают адаптеры (R04).
"""

from docpipe.arch.adapters import ADAPTERS, run_adapter
from docpipe.arch.collect import AdapterSpec, Collected, collect
from docpipe.arch.dump import dump_registry
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
    "ADAPTERS",
    "ARCH_VERSION",
    "AdapterSpec",
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
    "Collected",
    "check_document",
    "collect",
    "dump_registry",
    "format_statuses",
    "load_arch_registry",
    "load_optional",
    "read_document",
    "run_adapter",
    "source_statuses",
    "statuses_json",
]
