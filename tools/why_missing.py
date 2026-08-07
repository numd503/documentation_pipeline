"""Почему документ, который лежит на диске, не виден шагу 2.

`scan_docs` отбрасывает файл пятью разными способами и **ни об одном не сообщает**:
узел просто получает `missing`, а сам файл не попадает в отчёт вообще — ни сиротой,
ни перенесённым. Отличить «документа нет» от «документ есть, но отброшен» по выводу
`docs status` и `worklist` невозможно, и именно это делает диагностику долгой.

Скрипт повторяет фильтры `scan_docs` по одному и печатает причину для каждого узла,
чей документ существует на диске, но не был принят. Фильтры импортируются из самого
`docpipe`, а не переписываются здесь: копия разошлась бы с оригиналом и стала бы врать.

    uv run python tools/why_missing.py МАНИФЕСТ --config docpipe.yaml --root .
"""

import argparse
import sys
from pathlib import Path

from docpipe.config import load_config
from docpipe.discovery import matches_glob
from docpipe.materialize.document import DocumentError, read_document
from docpipe.materialize.plan import DEFAULT_DOCS_SCAN_EXCLUDE, SCHEMA_PREFIX
from docpipe.model import Manifest


def drop_reason(path: Path, root: Path, globs: list[str]) -> str | None:
    """Причина, по которой `scan_docs` не примет файл. `None` — примет."""
    rel = path.relative_to(root).as_posix()

    for glob in globs:
        if matches_glob(rel, glob):
            return f"отброшен фильтром docs_scan_exclude/встроенным: {glob}"

    try:
        head = path.read_bytes()[:4]
    except OSError as exc:
        return f"файл не читается: {exc}"

    if head not in (b"---\n", b"---\r"):
        if head[:3] == b"\xef\xbb\xbf":
            return "UTF-8 BOM в начале файла: пересохраните без BOM"
        if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return "файл в UTF-16: пересохраните в UTF-8"
        return f"первые 4 байта не '---': {head!r} (нет front matter или пустая строка перед ним)"

    try:
        _, parsed = read_document(path)
    except DocumentError as exc:
        return f"структура документа испорчена (станет broken, не missing): {exc}"
    except OSError as exc:
        return f"файл не читается: {exc}"

    schema = parsed.schema_id or ""
    docpipe = parsed.docpipe or {}
    if not schema.startswith(SCHEMA_PREFIX):
        return f"docpipe.schema = {schema!r}, а нужен префикс {SCHEMA_PREFIX!r}"
    if not docpipe.get("node_id"):
        return "во front matter нет docpipe.node_id"

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    settings = load_config(args.config)
    root = args.root.resolve()
    manifest = Manifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    globs = DEFAULT_DOCS_SCAN_EXCLUDE + list(settings.docs_scan_exclude)

    base = root / settings.docs_root
    print(f"root:        {root}")
    print(f"docs_root:   {settings.docs_root}  -> {base}")
    print(f"modules_dir: {settings.modules_dir}  (префикс doc_path: {settings.modules_root})")
    print(f"узлов в манифесте: {len(manifest.nodes)}")

    if not base.is_dir():
        print(f"\nОШИБКА: {base} не существует. scan_docs вернёт пустой список,")
        print("и ВСЕ узлы получат missing. Проверьте пару --root и docs_root.")
        return 1

    prefixes = {node.doc_path.split("/")[0] for node in manifest.nodes}
    print(f"первый сегмент doc_path в манифесте: {', '.join(sorted(prefixes))}")

    absent = 0
    dropped: list[tuple[str, str]] = []
    accepted = 0

    for node in sorted(manifest.nodes, key=lambda item: item.doc_path):
        path = root / node.doc_path
        if not path.is_file():
            absent += 1
            continue
        reason = drop_reason(path, root, globs)
        if reason is None:
            accepted += 1
        else:
            dropped.append((node.doc_path, reason))

    print(f"\nфайл есть и принят:      {accepted}")
    print(f"файла на doc_path нет:   {absent}  (это честный missing либо перенос)")
    print(f"файл есть, но отброшен:  {len(dropped)}  <- вот они и выглядят как missing")

    for doc_path, reason in dropped:
        print(f"\n  {doc_path}\n    {reason}")

    return 1 if dropped else 0


if __name__ == "__main__":
    sys.exit(main())
