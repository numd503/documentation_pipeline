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
from docpipe.config import DocpipeConfig, candidate_inputs, resolve_input


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


def adapter_specs(settings: DocpipeConfig) -> list[AdapterSpec]:
    """Подключения адаптеров из конфигурации — в том порядке, в каком записаны.

    Порядок здесь законен: при совпадении ключей выигрывает первая запись,
    и это решение человека, записанное расположением строк.
    """
    return [
        AdapterSpec(id=item.id, adapter=item.adapter, options=dict(item.options))
        for item in settings.arch_adapters
    ]


def collect_configured(
    settings: DocpipeConfig, config: Path | None, root: Path, arch: Path | None = None
) -> Collected:
    """Снимок плюс адаптеры по конфигурации. `arch` — путь к снимку вместо ключа."""
    path = arch
    if path is None and settings.arch:
        path = resolve_input(settings.arch, config)
    return collect(
        path, adapter_specs(settings), root, resolve=lambda value: resolve_input(value, config)
    )


def registry_for_build(settings: DocpipeConfig, config: Path | None, root: Path) -> ArchRegistry:
    """Реестр для сборки графа: всё, что названо в конфигурации, или отказ.

    `collect` складывает исключения адаптеров в `errors`, а `load_optional`
    считает отсутствующий файл пустым реестром. Обоим это законно: `arch records`
    показывает находки человеку, а реестра на репозитории может не быть вовсе.
    Сборке графа — нет: она брала только `.registry`, и опечатка в пути снимка
    или упавший адаптер давали индекс без точек входа из реестра, у которого
    в паспорте нет ни строки об этом, — то есть ответ «таких точек входа нет»
    при верной настройке.

    Не задан `arch` и нет адаптеров — пустой реестр: это состояние, а не ошибка.
    Названный ключом файл, которого нет, — ошибка: его назвал человек. Все
    находки собираются в одно сообщение, чтобы чинить их за один прогон.
    """
    problems: list[str] = []
    if settings.arch:
        candidates = candidate_inputs(settings.arch, config)
        if not any(path.exists() for path in candidates):
            tried = ", ".join(str(path) for path in candidates)
            problems.append(f"`arch`: файл {settings.arch!r} не найден; искали: {tried}")

    collected = collect_configured(settings, config, root)
    problems.extend(collected.errors)
    if problems:
        listing = "\n".join(f"  {line}" for line in problems)
        raise ValueError(
            f"реестр для графа собран с ошибками ({len(problems)}):\n{listing}\n"
            "Починить их или убрать источник из конфигурации; находки по источникам"
            " показывает `docpipe arch records`"
        )
    return collected.registry
