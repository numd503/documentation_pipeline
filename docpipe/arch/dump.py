"""Запись реестра в YAML: снимок, снятый с адаптера.

Нужна ровно для одного: превратить живое чтение в снимок, который можно
прочитать глазами, закоммитить и потом заметить, что он отстал. Это второй
законный способ получить записи, и выбор между ним и адаптером — решение,
а не деталь.

Порядок полей фиксирован, пустые значения не пишутся. Причина та же, что
у стабильной сериализации манифеста: файл читают в диффе, и перестановка
ключей превращает правку одной записи в правку всего файла.
"""

from typing import Any

import yaml

from docpipe.arch.model import (
    ArchRecord,
    ArchRegistry,
    DataRecord,
    EntryPointRecord,
    LayerRecord,
    SeamRecord,
)


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in ("", (), [], {}, None)}


def record_dict(record: ArchRecord) -> dict[str, Any]:
    """Запись в вид, пригодный для YAML. Порядок ключей — как в справочнике."""
    body: dict[str, Any] = {"kind": record.kind, "key": record.key, "name": record.name}

    if isinstance(record, EntryPointRecord):
        body["entry_kind"] = record.entry_kind
        # Одна реализация пишется строкой, несколько — списком: файл читают
        # глазами, и `impl: [Foo]` вместо `impl: Foo` — шум на каждой записи.
        body["impl"] = record.impl[0] if len(record.impl) == 1 else list(record.impl)
        body["route"] = record.route
        body["http_method"] = record.http_method
        body["touches"] = list(record.touches)
    elif isinstance(record, DataRecord):
        body["data_kind"] = record.data_kind
        body["table"] = record.table
        body["fields"] = [
            _clean(
                {
                    "name": field.name,
                    "kind": field.kind,
                    "display_name": field.display_name,
                    "references": field.references,
                }
            )
            for field in record.fields
        ]
        body["references"] = list(record.references)
    elif isinstance(record, SeamRecord):
        body["seam_kind"] = record.seam_kind
        body["literal"] = record.literal
        body["http_method"] = record.http_method
        body["sides"] = list(record.sides)
    elif isinstance(record, LayerRecord):
        body["role"] = record.role
        body["path"] = record.path
        body["language"] = record.language

    body["source"] = _clean(
        {
            "file": record.source.file,
            "record": record.source.record,
            "hash": record.source.hash,
        }
    )
    body["provenance"] = record.provenance
    body["attributes"] = dict(record.attributes)
    body["note"] = record.note
    return _clean(body)


def dump_registry(registry: ArchRegistry, header: str = "") -> str:
    """Реестр в текст YAML.

    Заголовок — комментарий сверху: снимок обязан сам сказать, чем он снят,
    иначе через месяц его правят руками и удивляются, что правка исчезла
    после следующего прогона.
    """
    document = {
        "version": registry.version,
        "records": [record_dict(record) for record in registry.records],
    }
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    return f"{header}\n{body}" if header else body
