"""Сбор записей: снимок плюс адаптеры.

Единственное место, где два законных способа получить записи сходятся в один
набор. Правило разрешения спора одно и следует из Р10: **выигрывает
подтверждённое человеком.** Запись, лежащая в файле, человек прочитал
и закоммитил; запись адаптера появилась сама. При совпадении ключей
остаётся первая, а факт совпадения печатается — молча выброшенная запись
через месяц неотличима от потерянной.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docpipe.arch.adapters import run_adapter
from docpipe.arch.load import load_optional
from docpipe.arch.model import ArchRecord, ArchRegistry


@dataclass(frozen=True)
class AdapterSpec:
    """Подключение адаптера: имя в конфигурации и его параметры."""

    id: str
    adapter: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Collected:
    registry: ArchRegistry
    errors: tuple[str, ...] = ()
    from_file: int = 0
    from_adapters: tuple[tuple[str, int], ...] = ()
    # Дубль снимка и дубль внутри адаптеров — разные находки, и общая
    # категория врала бы про обе: первая говорит «человек и машина описали
    # одно и то же», вторая — «источник объявляет одну запись дважды»,
    # и чинят их в разных местах.
    shadowed_by_file: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()


def collect(
    path: Path | None,
    adapters: list[AdapterSpec],
    root: Path,
    resolve: Any = None,
) -> Collected:
    """Собрать записи из файла и адаптеров в один реестр."""
    snapshot = load_optional(path)
    records: list[ArchRecord] = list(snapshot.records)
    from_file = {(record.kind, record.normalized_key) for record in records}
    seen = set(from_file)
    errors: list[str] = []
    shadowed: list[str] = []
    duplicates: list[str] = []
    counts: list[tuple[str, int]] = []

    for spec in adapters:
        try:
            result = run_adapter(spec.adapter, dict(spec.options), root, resolve)
        except (OSError, ValueError) as error:
            errors.append(f"{spec.id}: {error}")
            counts.append((spec.id, 0))
            continue
        errors.extend(f"{spec.id}: {message}" for message in result.errors)
        added = 0
        for record in result.records:
            identity = (record.kind, record.normalized_key)
            if identity in seen:
                where = shadowed if identity in from_file else duplicates
                where.append(f"{spec.id}: {record.kind} {record.normalized_key}")
                continue
            seen.add(identity)
            records.append(record)
            added += 1
        counts.append((spec.id, added))

    return Collected(
        registry=ArchRegistry(version=snapshot.version, records=tuple(records)),
        errors=tuple(errors),
        from_file=len(snapshot.records),
        from_adapters=tuple(counts),
        shadowed_by_file=tuple(shadowed),
        duplicates=tuple(duplicates),
    )
