"""Загрузка бизнес-каталога.

Каталог — это **множество файлов**, а не сгенерированный манифест: аналитик
работает в IDE, и один файл на процесс с `git add` дешевле любого промежуточного
артефакта, который пришлось бы пересобирать перед каждой правкой.

Документом каталога считается `.md`, у которого во front matter
`docpipe.schema` начинается с `business/`. Проверка именно по схеме, а не по
наличию ключа `docpipe`: формат зон общий с шагом 2, и отбор по ключу перепутал
бы слои в обе стороны.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from docpipe.business.model import (
    ANCHOR_KINDS,
    FOLDER_BY_PREFIX,
    KIND_BY_PREFIX,
    BusinessDoc,
    Capability,
    Catalog,
)
from docpipe.materialize import DocumentError, read_document

SCHEMA_PREFIX = "business/"
ID_RE = re.compile(r"^(bp|be|cap)\.[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+(-[a-z0-9]+)*)+$")
CAPABILITIES_FILE = "capabilities.yaml"


def doc_path_for(doc_id: str, business_root: str) -> str:
    """Путь документа, выведенный из идентификатора.

    Вид документа берётся из префикса, а не из отдельного параметра: два
    источника истины про вид разошлись бы, и документ уехал бы не в тот каталог.

    Идентификатор неизменен, поэтому и путь неизменен — механика автопереноса
    из шага 2 бизнес-слою не нужна.
    """
    prefix, *rest = doc_id.split(".")
    folder = FOLDER_BY_PREFIX[prefix]
    return f"{business_root}/{folder}/{'/'.join(rest)}.md"


def _load_capabilities(path: Path) -> tuple[list[Capability], list[str]]:
    if not path.is_file():
        return [], []

    errors: list[str] = []
    try:
        raw: Any = yaml.safe_load(path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], [f"{path.name}: не разбирается ({exc})"]

    if not isinstance(raw, dict):
        return [], [f"{path.name}: должен быть словарём"]

    capabilities: list[Capability] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("capabilities") or []):
        where = f"{path.name}: возможность #{index}"
        if not isinstance(item, dict):
            errors.append(f"{where}: должна быть словарём")
            continue
        try:
            capability = Capability.model_validate(item)
        except ValidationError as exc:
            errors.append(f"{where}: {exc}")
            continue
        if not capability.id.startswith("cap."):
            errors.append(f"{where}: идентификатор должен начинаться с `cap.`")
            continue
        if capability.id in seen:
            errors.append(f"{where}: повтор идентификатора {capability.id!r}")
            continue
        seen.add(capability.id)
        capabilities.append(capability)

    declared = {capability.id for capability in capabilities}
    for capability in capabilities:
        if capability.parent and capability.parent not in declared:
            errors.append(
                f"{path.name}: {capability.id} ссылается на необъявленного"
                f" родителя {capability.parent!r}"
            )

    return sorted(capabilities, key=lambda c: c.id), errors


def _check_anchors(doc: BusinessDoc, where: str) -> list[str]:
    errors: list[str] = []
    for anchor in doc.anchors:
        if not anchor.ref:
            errors.append(f"{where}: якорь `{anchor.kind}` без `ref`")
        if anchor.kind not in ANCHOR_KINDS:
            known = ", ".join(sorted(ANCHOR_KINDS))
            errors.append(f"{where}: неизвестный вид якоря `{anchor.kind}`; известны: {known}")
    return errors


def _build_doc(
    block: dict[str, Any], rel: str, business_root: str
) -> tuple[BusinessDoc | None, list[str]]:
    where = rel
    try:
        doc = BusinessDoc.model_validate({**block, "doc_path": rel})
    except ValidationError as exc:
        return None, [f"{where}: {exc}"]

    errors: list[str] = []
    if not ID_RE.match(doc.id):
        errors.append(
            f"{where}: идентификатор {doc.id!r} не по образцу"
            " `(bp|be|cap).<домен>.<имя>` строчными латинскими"
        )
        return None, errors

    prefix = doc.id.split(".", 1)[0]
    if KIND_BY_PREFIX[prefix] != doc.kind:
        errors.append(
            f"{where}: префикс {prefix!r} означает вид {KIND_BY_PREFIX[prefix]!r},"
            f" а объявлен {doc.kind!r}"
        )

    expected = doc_path_for(doc.id, business_root)
    if rel != expected:
        errors.append(
            f"{where}: путь не совпадает с выведенным из идентификатора ({expected})."
            " Идентификатор неизменен, поэтому расхождение означает ручное"
            " переименование файла, а не переезд"
        )

    errors += _check_anchors(doc, where)
    return (doc if not errors else None), errors


def load_catalog(root: Path, business_root: str) -> Catalog:
    """Прочитать каталог целиком.

    Обход детерминирован: файлы сортируются, порядок обхода ФС источником
    порядка не является.
    """
    base = root / business_root
    capabilities, errors = _load_capabilities(base / CAPABILITIES_FILE)

    docs: list[BusinessDoc] = []
    seen: dict[str, str] = {}

    for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
        rel = path.relative_to(root).as_posix()

        # Отсев по первым байтам до чтения целиком: в дереве документации
        # встречаются `.md` на мегабайты, и читать их незачем.
        if path.read_bytes()[:4] not in (b"---\n", b"---\r"):
            continue

        try:
            _, parsed = read_document(path)
        except DocumentError as exc:
            errors.append(f"{rel}: {exc}")
            continue

        schema = parsed.schema_id
        if schema is None or not schema.startswith(SCHEMA_PREFIX):
            continue

        block = parsed.docpipe or {}
        doc, doc_errors = _build_doc(block, rel, business_root)
        errors += doc_errors
        if doc is None:
            continue

        if doc.id in seen:
            errors.append(f"{rel}: повтор идентификатора {doc.id!r}, уже объявлен в {seen[doc.id]}")
            continue
        seen[doc.id] = rel
        docs.append(doc)

    declared = {capability.id for capability in capabilities}
    for doc in docs:
        if doc.capability and doc.capability not in declared:
            errors.append(
                f"{doc.doc_path}: возможность {doc.capability!r} не объявлена"
                f" в {business_root}/{CAPABILITIES_FILE}"
            )

    return Catalog(
        docs=sorted(docs, key=lambda d: d.id),
        capabilities=capabilities,
        errors=errors,
    )
