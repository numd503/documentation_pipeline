"""Нормализованный реестр архитектурных элементов (R03).

Контракт между разведкой и графом: слева от него догадки и ручная работа,
справа детерминированная сборка. Пакет не знает ни об одном конкретном
репозитории — что читать и как называются теги, знают адаптеры (R04).
"""

from docpipe.arch.adapters import ADAPTERS, run_adapter
from docpipe.arch.collect import (
    AdapterSpec,
    Collected,
    adapter_specs,
    collect,
    collect_configured,
    registry_for_build,
)
from docpipe.arch.dump import dump_registry
from docpipe.arch.load import (
    KIND_ORDER,
    ArchProblem,
    ArchValidation,
    ValidationProblem,
    check_document,
    load_arch_registry,
    load_optional,
    read_document,
    validate_document,
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
    "KIND_ORDER",
    "AdapterSpec",
    "ArchProblem",
    "ArchRecord",
    "ArchRegistry",
    "ArchValidation",
    "DataField",
    "DataRecord",
    "EntryPointRecord",
    "LayerRecord",
    "SeamRecord",
    "Source",
    "SourceStatus",
    "ValidationProblem",
    "Collected",
    "adapter_specs",
    "check_document",
    "collect",
    "collect_configured",
    "dump_registry",
    "format_statuses",
    "load_arch_registry",
    "load_optional",
    "read_document",
    "registry_for_build",
    "run_adapter",
    "source_statuses",
    "statuses_json",
    "validate_document",
]
