"""Чтение реестров по их описанию.

Код ничего не знает ни про АС CF, ни про Ignite, ни про Quartz: что читать,
откуда и как называются поля — целиком в `registries.yaml`. Здесь только
механика: найти файлы, разобрать, достать значения, перейти по ссылке.

Устойчивость важнее полноты. Один нечитаемый файл, одна запись без якоря,
одна оборванная ссылка дают строку в `errors`, а не отказ: реестров десятки,
и правились они годами разными командами.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from docpipe.registry.model import (
    ChildSpec,
    FollowSpec,
    RegistryItem,
    RegistryResult,
    RegistrySpec,
)


def _decode(path: Path) -> str:
    """Прочитать файл байтами и снять BOM.

    `read_text(encoding="utf-8")` BOM не снимает, и `ET.fromstring` падает на
    первом же символе. XML в поставке АС CF сохранён редакторами Windows,
    поэтому BOM — норма, а не исключение.
    """
    return path.read_bytes().decode("utf-8-sig")


def _extract_xml(elem: ET.Element, expr: str) -> str | None:
    """Достать значение по выражению: `@attr`, `путь/@attr` или `путь` (текст).

    Отдельная ветка для `путь/@attr` нужна потому, что ElementTree **не умеет
    выбирать атрибуты**: `find("./File/@Path")` возвращает `None`, и описание
    реестра выглядит рабочим, молча отдавая пустые значения.
    """
    if expr.startswith("@"):
        return elem.get(expr[1:])

    if "/@" in expr:
        path, _, attr = expr.rpartition("/@")
        target = elem.find(path)
        return None if target is None else target.get(attr)

    target = elem.find(expr)
    if target is None:
        return None
    return (target.text or "").strip() or None


def _fields_xml(elem: ET.Element, spec: dict[str, str]) -> dict[str, str]:
    """Собрать поля записи. Ненайденные ключи опускаются, а не кладутся как None."""
    found = {name: _extract_xml(elem, expr) for name, expr in spec.items()}
    return {name: value for name, value in found.items() if value is not None}


def _fields_json(data: dict[str, Any], spec: dict[str, str]) -> dict[str, str]:
    """То же для JSON. Значения приводятся к строке: реестр хранит строки."""
    found = {name: data.get(expr) for name, expr in spec.items()}
    return {name: str(value) for name, value in found.items() if value is not None}


def _children_xml(
    elem: ET.Element, specs: list[ChildSpec], registry: str, rel: str
) -> tuple[list[RegistryItem], list[str]]:
    items: list[RegistryItem] = []
    errors: list[str] = []
    for child in specs:
        for node in elem.findall(child.item_xpath):
            fields = _fields_xml(node, child.fields)
            ref = fields.pop("ref", None)
            if ref is None:
                errors.append(f"{rel}: вложенная запись `{child.kind}` без поля `ref`, пропущена")
                continue
            items.append(
                RegistryItem(
                    registry=registry, kind=child.kind, ref=ref, fields=fields, source_path=rel
                )
            )
    return items, errors


def _follow(
    spec: FollowSpec, fields: dict[str, str], root: Path, registry: str, ref_hint: str
) -> tuple[dict[str, str], list[RegistryItem], list[str]]:
    """Перейти по ссылке из записи и прочитать описание.

    Путь внутри реестра записан разделителями Windows и отсчитывается от базы
    развёртывания. Ни то, ни другое не выводится из положения файла реестра.
    """
    raw = fields.get(spec.field)
    if raw is None:
        return {}, [], [f"{ref_hint}: нет поля `{spec.field}` для перехода по ссылке"]

    relative = raw.replace("\\", "/")
    target = root / spec.base / relative
    if not target.is_file():
        return {}, [], [f"{ref_hint}: файл описания не найден: {spec.base}/{relative}"]

    rel = target.relative_to(root).as_posix()
    try:
        data: Any = json.loads(_decode(target))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [], [f"{rel}: описание не разбирается ({exc})"]

    if not isinstance(data, dict):
        return {}, [], [f"{rel}: описание должно быть словарём"]

    followed = _fields_json(data, spec.fields)
    children: list[RegistryItem] = []
    errors: list[str] = []

    if spec.children is not None:
        raw_children = data.get(spec.children.items_key)
        if not isinstance(raw_children, list):
            errors.append(f"{rel}: `{spec.children.items_key}` отсутствует или не массив")
        else:
            for index, entry in enumerate(raw_children):
                if not isinstance(entry, dict):
                    errors.append(f"{rel}: {spec.children.items_key}[{index}] не словарь")
                    continue
                child_fields = _fields_json(entry, spec.children.fields)
                child_ref = child_fields.pop("ref", None)
                if child_ref is None:
                    errors.append(
                        f"{rel}: {spec.children.items_key}[{index}] без поля `ref`, пропущена"
                    )
                    continue
                children.append(
                    RegistryItem(
                        registry=registry,
                        kind=spec.children.kind,
                        ref=child_ref,
                        fields=child_fields,
                        source_path=rel,
                    )
                )

    return followed, children, errors


def _iter_files(spec: RegistrySpec, root: Path) -> tuple[list[Path], list[str]]:
    """Найти файлы реестра. Порядок обхода ФС источником порядка не является."""
    found: set[Path] = set()
    errors: list[str] = []
    for pattern in spec.patterns():
        matched = [path for path in root.glob(pattern) if path.is_file()]
        if not matched:
            errors.append(f"реестр {spec.id!r}: по шаблону {pattern!r} файлов не найдено")
        found.update(matched)
    return sorted(found), errors


def _read_xml(spec: RegistrySpec, path: Path, root: Path) -> tuple[list[RegistryItem], list[str]]:
    rel = path.relative_to(root).as_posix()
    try:
        text = _decode(path)
    except UnicodeDecodeError as exc:
        return [], [f"{rel}: файл не читается как UTF-8 ({exc})"]

    try:
        tree = ET.fromstring(text)
    except ET.ParseError as exc:
        return [], [f"{rel}: XML не разбирается ({exc})"]

    assert spec.item_xpath is not None  # проверено при загрузке описания
    items: list[RegistryItem] = []
    errors: list[str] = []

    for elem in tree.findall(spec.item_xpath):
        fields = _fields_xml(elem, spec.fields)
        children, child_errors = _children_xml(elem, spec.children, spec.id, rel)
        errors.extend(child_errors)

        if spec.follow is not None:
            hint = f"{rel}: запись {fields.get('title') or fields.get('ref') or '?'}"
            followed, followed_children, follow_errors = _follow(
                spec.follow, fields, root, spec.id, hint
            )
            errors.extend(follow_errors)
            fields = {**fields, **followed}
            children.extend(followed_children)

        ref = fields.pop("ref", None)
        if ref is None:
            errors.append(f"{rel}: запись без поля `ref`, пропущена")
            continue

        items.append(
            RegistryItem(
                registry=spec.id,
                kind=spec.kind,
                ref=ref,
                fields=fields,
                source_path=rel,
                children=children,
            )
        )

    return items, errors


def read_registry(spec: RegistrySpec, root: Path) -> RegistryResult:
    """Прочитать реестр целиком.

    Записи верхнего уровня сортируются: они приходят из нескольких файлов, и
    порядок обхода ФС не должен влиять на вывод. Вложенные записи остаются
    в порядке объявления — у шагов workflow порядок является данными
    (цепочка `NextStepId`), а не следствием обхода.
    """
    if spec.format == "inline":
        items = [
            RegistryItem(
                registry=spec.id,
                kind=spec.kind,
                ref=entry["ref"],
                fields={k: v for k, v in entry.items() if k != "ref"},
                source_path="",
            )
            for entry in spec.items
        ]
        return RegistryResult(registry=spec.id, items=sorted(items, key=lambda i: i.ref))

    files, errors = _iter_files(spec, root)
    items = []
    namespaced = False

    for path in files:
        file_items, file_errors = _read_xml(spec, path, root)
        items.extend(file_items)
        errors.extend(file_errors)
        if not file_items:
            namespaced = namespaced or _declares_namespace(path)

    # Реестр, не давший ни одной записи, — самая дорогая из возможных ошибок:
    # он неотличим от «таких точек входа в системе нет». Отдельная подсказка
    # про пространство имён нужна потому, что при объявленном xmlns поиск
    # по голому имени тега не находит ничего и не сообщает об этом.
    if files and not items:
        message = (
            f"реестр {spec.id!r}: по выражению {spec.item_xpath!r} "
            f"не найдено ни одной записи в {len(files)} файлах"
        )
        if namespaced:
            message += "; корневой элемент объявляет пространство имён"
        errors.append(message)

    return RegistryResult(
        registry=spec.id,
        items=sorted(items, key=lambda i: (i.ref, i.source_path)),
        errors=errors,
    )


def _declares_namespace(path: Path) -> bool:
    """Объявляет ли корневой элемент пространство имён — только для подсказки."""
    try:
        return ET.fromstring(_decode(path)).tag.startswith("{")
    except (UnicodeDecodeError, ET.ParseError):
        return False
