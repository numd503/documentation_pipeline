"""Адаптер декларативных реестров: `registries.yaml` → записи R03.

Это перебазирование `docpipe/registry/` на общий формат, а не его переписывание.
Модуль уже был адаптером — просто зашитым в ядро и говорящим на своём словаре:
он умеет читать XML и JSON по описанию, ходить по ссылкам и не ронять прогон
на битом файле. Не хватало ему формата под ним, общего с остальными
репозиториями; здесь он и появляется.

Что специфичного для конкретного репозитория есть в этом файле: **ничего**.
Пути, теги, имена атрибутов живут в `registries.yaml`; здесь — только
отображение «вид записи реестра → вид записи графа», и оно тоже
переопределяется параметром.
"""

from typing import Any, Final

from docpipe.arch.adapters.base import (
    AdapterContext,
    AdapterResult,
    FileHashes,
    require,
    unknown_options,
)
from docpipe.arch.model import (
    ArchRecord,
    DataField,
    DataRecord,
    EntryPointRecord,
    LayerRecord,
    SeamRecord,
    Source,
)
from docpipe.registry import load_registries, read_registry
from docpipe.registry.model import RegistryItem, RegistrySpec

ADAPTER = "registries"

# Вид записи реестра → вид записи графа. Словарь, а не ветвление: новый вид
# реестра — это строка здесь или в конфигурации, а не правка кода.
#
# Имена слева — словарь `registries.yaml`, то есть наш собственный, а не чужой
# платформенный: `grid_service`, `job`, `workflow` осмысленны на любом
# репозитории, где такие сущности есть. Специфичное имя вида на новом
# репозитории добавляется параметром `kinds`, без единой правки здесь.
DEFAULT_KINDS: Final[dict[str, str]] = {
    "grid_service": "entry_point:grid_service",
    "job": "entry_point:job",
    "workflow": "entry_point:workflow",
    "workflow_step": "entry_point:workflow_step",
    "service": "entry_point:service",
    "page": "entry_point:page",
    "http_endpoint": "entry_point:http_endpoint",
    "list_event": "entry_point:event_handler",
    "event_receiver": "entry_point:event_handler",
    "list": "data:table",
    "table": "data:table",
    "view": "data:view",
    "procedure": "data:procedure",
    "list_field": "field",
    "kafka_topic": "seam:topic",
    "queue": "seam:queue",
    "http_route": "seam:http_route",
    "module": "layer:library",
}

# Поля записи реестра, у которых есть место в схеме R03. Всё остальное
# уезжает в `attributes` целиком: терять `assembly`, `team` или расписание
# нельзя — это то, ради чего реестр читают.
_NAME_FIELDS: Final[tuple[str, ...]] = ("display_name", "title", "name")
_IMPL_FIELDS: Final[tuple[str, ...]] = ("impl_fqn", "contract_fqn", "class", "handler")
_TABLE_FIELDS: Final[tuple[str, ...]] = ("table", "table_name")
_FIELD_KIND_FIELDS: Final[tuple[str, ...]] = ("field_type", "type", "kind")
_FIELD_REF_FIELDS: Final[tuple[str, ...]] = ("lookup", "list_source", "references")

_KNOWN_OPTIONS = {"spec", "kinds"}


def _first(fields: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = fields.get(name, "").strip()
        if value:
            return value
    return ""


def _attributes(fields: dict[str, str], consumed: set[str]) -> dict[str, str]:
    return {key: value for key, value in sorted(fields.items()) if key not in consumed and value}


def _source(item: RegistryItem, spec: RegistrySpec, hashes: FileHashes, fallback: str) -> Source:
    """Источник записи. У встроенного реестра источник — само его описание.

    Реестр формата `inline` держит записи прямо в `registries.yaml`: так
    заводят перечень, у которого источника пока не нашлось. Пути к файлу
    данных у такой записи нет, и оставить источник пустым нельзя — запись
    без источника не принимается на уровне загрузки, и правильно делает.
    Источником в этом случае является файл описания: там факт и объявлен,
    и его правка обязана делать снимок устаревшим.
    """
    file = item.source_path or fallback
    return Source(file=file, record=f"{spec.id}[{item.ref}]", hash=hashes.of(file))


def _data_fields(item: RegistryItem, kinds: dict[str, str]) -> tuple[DataField, ...]:
    """Поля списка — атрибуты узла данных, а не отдельные записи.

    Вид поля читается как есть, без белого списка: перечень видов у платформы
    заведомо неполон, и потерять поле дороже, чем принять незнакомое имя.
    """
    fields: list[DataField] = []
    for child in item.children:
        if kinds.get(child.kind) != "field":
            continue
        fields.append(
            DataField(
                name=child.ref,
                kind=_first(child.fields, _FIELD_KIND_FIELDS),
                display_name=_first(child.fields, _NAME_FIELDS),
                references=_first(child.fields, _FIELD_REF_FIELDS),
            )
        )
    return tuple(fields)


def record_key(item: RegistryItem, parent_key: str = "") -> str:
    """Ключ записи: якорь, который знает вызывающий.

    Две добавки к `ref`, и обе — не украшение.

    **Версия входит в ключ, если она есть.** Один и тот же workflow живёт
    в репозитории в нескольких версиях одновременно, и `Id` у них общий.
    Ключ без версии склеил бы их в одну запись, а вторая молча пропала бы
    как дубль — то есть инструмент уверенно отвечал бы про не ту версию
    процесса. Форма `Id@Version` совпадает с якорем бизнес-слоя не случайно:
    это одно и то же утверждение с разных сторон.

    **Родитель входит в ключ вложенной записи.** «Список + EventType»
    и «workflow + шаг» — якоря уровня контракта: одно и то же `ItemAdded`
    есть у десятка списков, и само по себе оно не адрес.
    """
    version = item.fields.get("version", "").strip()
    base = f"{item.ref}@{version}" if version else item.ref
    return f"{parent_key}:{base}" if parent_key else base


def _build(
    item: RegistryItem,
    spec: RegistrySpec,
    target: str,
    kinds: dict[str, str],
    hashes: FileHashes,
    parent: RegistryItem | None = None,
    parent_key: str = "",
    fallback: str = "",
) -> ArchRecord:
    kind, _, subkind = target.partition(":")
    source = _source(item, spec, hashes, fallback)
    name = _first(item.fields, _NAME_FIELDS)
    key = record_key(item, parent_key)

    if kind == "entry_point":
        consumed = set(_NAME_FIELDS) | set(_IMPL_FIELDS)
        return EntryPointRecord(
            key=key,
            name=name,
            entry_kind=subkind,  # type: ignore[arg-type]
            impl=(_first(item.fields, _IMPL_FIELDS),) if _first(item.fields, _IMPL_FIELDS) else (),
            # Обработчик события сидит на таблице своего списка, и связь эта
            # известна из декларации — проходить за ней через код не нужно.
            touches=(parent.ref,)
            if parent is not None and parent.kind in ("list", "table")
            else (),
            source=source,
            provenance="adapter",
            attributes=_attributes(item.fields, consumed),
        )
    if kind == "data":
        consumed = set(_NAME_FIELDS) | set(_TABLE_FIELDS)
        fields = _data_fields(item, kinds)
        return DataRecord(
            key=key,
            name=name,
            data_kind=subkind or "table",  # type: ignore[arg-type]
            table=_first(item.fields, _TABLE_FIELDS),
            fields=fields,
            references=tuple(sorted({field.references for field in fields if field.references})),
            source=source,
            provenance="adapter",
            attributes=_attributes(item.fields, consumed),
        )
    if kind == "seam":
        consumed = set(_NAME_FIELDS)
        return SeamRecord(
            key=key,
            name=name,
            seam_kind=subkind or "other",  # type: ignore[arg-type]
            literal=item.fields.get("literal", "") or key,
            source=source,
            provenance="adapter",
            attributes=_attributes(item.fields, consumed),
        )
    return LayerRecord(
        key=key,
        name=name,
        role=subkind or "other",  # type: ignore[arg-type]
        path=item.fields.get("path", ""),
        source=source,
        provenance="adapter",
        attributes=_attributes(item.fields, set(_NAME_FIELDS)),
    )


def _merge_implementations(records: list[ArchRecord]) -> list[ArchRecord]:
    """Свести точки входа с одним ключом в одну запись с несколькими реализациями.

    Случай из реального реестра: на паре «список + EventType» сидят два
    обработчика — аудит и запуск процесса. Это один якорь и два корня кода,
    а не две точки входа: вызывающий знает пару, а не имена классов.

    Атрибуты сохраняются только те, что совпали у всех слитых записей.
    Расходящиеся выбрасываются: записать одно из двух значений `assembly`
    значит соврать про вторую реализацию, а соврать хуже, чем не сказать.
    """
    merged: dict[tuple[str, str], ArchRecord] = {}
    order: list[tuple[str, str]] = []
    result: list[ArchRecord] = []
    for record in records:
        if not isinstance(record, EntryPointRecord):
            result.append(record)
            continue
        identity = (record.kind, record.normalized_key)
        first = merged.get(identity)
        if first is None:
            merged[identity] = record
            order.append(identity)
            continue
        assert isinstance(first, EntryPointRecord)
        agreed = {
            key: value
            for key, value in first.attributes.items()
            if record.attributes.get(key) == value
        }
        merged[identity] = first.model_copy(
            update={
                "impl": tuple(sorted(set(first.impl) | set(record.impl))),
                "attributes": agreed,
            }
        )
    result.extend(merged[identity] for identity in order)
    return result


def from_registries(ctx: AdapterContext, options: dict[str, Any]) -> AdapterResult:
    """Прочитать реестры по их описанию и выдать записи R03."""
    unknown_options(options, _KNOWN_OPTIONS, ADAPTER)
    spec_path = ctx.resolve(str(require(options, "spec", ADAPTER)))
    overrides = options.get("kinds") or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"адаптер {ADAPTER}: `kinds` обязан быть словарём")
    kinds = {**DEFAULT_KINDS, **{str(k): str(v) for k, v in overrides.items()}}

    hashes = FileHashes(ctx.root)
    # Путь описания реестров репо-относительный, если он внутри репозитория,
    # и как есть — если снаружи (конфигурация инструмента часто лежит рядом
    # с ней, а не в дереве исходников).
    try:
        fallback = spec_path.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        fallback = spec_path.as_posix()

    records: list[ArchRecord] = []
    errors: list[str] = []
    unmapped: set[str] = set()

    for spec in load_registries(spec_path):
        result = read_registry(spec, ctx.root)
        errors.extend(f"{spec.id}: {error}" for error in result.errors)
        for item in result.items:
            target = kinds.get(item.kind)
            if target is None:
                unmapped.add(item.kind)
                continue
            records.append(_build(item, spec, target, kinds, hashes, fallback=fallback))
            parent_key = record_key(item)
            for child in item.children:
                child_target = kinds.get(child.kind)
                # `field` — не запись, а часть узла данных: он уже собран выше.
                if child_target is None:
                    unmapped.add(child.kind)
                    continue
                if child_target == "field":
                    continue
                records.append(_build(child, spec, child_target, kinds, hashes, item, parent_key))

    records = _merge_implementations(records)

    for kind in sorted(unmapped):
        errors.append(
            f"вид записи {kind!r} не отображён в вид записи графа: записи пропущены. "
            f"Добавьте пару в `kinds` адаптера {ADAPTER}"
        )
    return AdapterResult(records=tuple(records), errors=tuple(errors))
