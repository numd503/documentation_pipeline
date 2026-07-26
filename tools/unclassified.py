"""Что не попало в документацию и почему — подсказка для настройки правил.

Временный инструмент. Когда появится `docpipe scan --stats` (T20), эта функция
переедет туда, а файл уйдёт: настройка правил — часть основного цикла работы,
а не отдельный скрипт.

Нужен он вот зачем: в манифест попадают только **классифицированные** типы,
а чтобы понять, каких правил не хватает, нужно смотреть ровно на обратное —
на то, что правилами не покрыто.

    uv run python tools/unclassified.py /путь/к/репозиторию [rules/dotnet.yaml]
"""

import sys
from collections import Counter
from pathlib import Path

from docpipe.classify import classify, is_excluded, load_ruleset
from docpipe.discovery import discover
from docpipe.dotnet.parser import parse_file
from docpipe.dotnet.resolve import build_symbol_index, compute_closures
from docpipe.emit import DEFAULT_EXCLUDE, map_files_to_modules

# Окончания имён, по которым в .NET обычно и опознают вид сущности.
_SUFFIXES = (
    "Service",
    "Handler",
    "Manager",
    "Factory",
    "Client",
    "Builder",
    "Provider",
    "Repository",
    "Store",
    "Processor",
    "Validator",
    "Job",
    "Worker",
    "Module",
    "Controller",
    "Endpoint",
    "Middleware",
    "Filter",
    "Converter",
    "Extensions",
    "Attribute",
    "Exception",
    "Command",
    "Query",
    "Event",
    "Adapter",
    "Strategy",
)

_TOP = 15


def main(root: Path, rules_path: Path) -> None:
    ruleset = load_ruleset(rules_path)

    found = discover(root, DEFAULT_EXCLUDE)
    results = [parse_file(root / relative, root) for relative in found.cs_files]
    index = compute_closures(
        build_symbol_index(results, map_files_to_modules(found.cs_files, found.csproj_files))
    )

    excluded = 0
    classified = 0
    rest = []
    for symbol in index.values():
        if is_excluded(symbol, ruleset):
            excluded += 1
        elif classify(symbol, ruleset):
            classified += 1
        else:
            rest.append(symbol)

    print(f"символов: {len(index)}")
    print(f"  классифицировано: {classified}")
    print(f"  исключено:        {excluded}")
    print(f"  без правила:      {len(rest)}\n")

    _table("По каким модулям", Counter(Path(s.module).stem for s in rest))
    _table(
        "Окончания имён",
        Counter(
            next((x for x in _SUFFIXES if s.name.endswith(x)), "(нет из списка)") for s in rest
        ),
    )
    _table(
        "Базовые типы (с учётом наследования)",
        Counter(b for s in rest for b in s.base_type_closure),
    )
    _table("Атрибуты", Counter(a.name for s in rest for a in s.attributes))
    _table("Namespace", Counter(s.namespace for s in rest))


def _table(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    for name, count in counter.most_common(_TOP):
        print(f"  {count:6}  {name}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2] if len(sys.argv) > 2 else "rules/dotnet.yaml"))
