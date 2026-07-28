#!/usr/bin/env python3
"""Урезать манифест до enrolled-модулей: и `modules`, и `nodes`.

Локальный вспомогательный скрипт для просмотра глазами, не часть пайплайна.
Только стандартная библиотека — запускается любым python3.12+, окружение
поставки для этого не нужно.

    python3 trim-manifest.py artifacts/doc-tree.json -o /tmp/trimmed.json
    python3 trim-manifest.py artifacts/doc-tree.json | less

Узлы отбираются по `parent` (это `id` модуля), а не по `module`: имена
проектов в больших репозиториях не уникальны, и отбор по имени утащил бы
узлы одноимённого модуля, который в enrolled не входит.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def trim(manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Оставить только enrolled-модули и относящиеся к ним узлы."""
    modules: list[dict[str, Any]] = manifest.get("modules", [])
    nodes: list[dict[str, Any]] = manifest.get("nodes", [])

    kept_modules = [m for m in modules if m.get("enrolled")]
    kept_ids = {m["id"] for m in kept_modules}
    kept_nodes = [n for n in nodes if n.get("parent") in kept_ids]

    # Ссылки на выброшенные модули не чистятся: это факты о проекте, а не
    # артефакт фильтрации. Но знать, сколько их повисло, полезно — иначе
    # урезанный манифест выглядит самодостаточным, не будучи им.
    dangling = sum(
        1
        for module in kept_modules
        for reference in module.get("project_references", [])
        if reference not in kept_ids
    )

    counters = {
        "modules_before": len(modules),
        "modules_after": len(kept_modules),
        "nodes_before": len(nodes),
        "nodes_after": len(kept_nodes),
        "dangling_project_references": dangling,
    }
    return {**manifest, "modules": kept_modules, "nodes": kept_nodes}, counters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path, help="исходный doc-tree.json")
    parser.add_argument("-o", "--out", type=Path, help="куда писать; по умолчанию stdout")
    parser.add_argument(
        "--compact", action="store_true", help="без отступов, одной строкой"
    )
    args = parser.parse_args()

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Не удалось прочитать манифест: {exc}", file=sys.stderr)
        return 2

    trimmed, counters = trim(manifest)

    text = json.dumps(
        trimmed,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)

    # Счётчики — в stderr, чтобы вывод можно было направить в файл или в jq.
    width = max(len(name) for name in counters)
    for name, value in counters.items():
        print(f"{name:<{width}}  {value}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
