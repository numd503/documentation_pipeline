"""Разбор файла решения `.sln`.

Формат текстовый и стабильный между версиями Visual Studio, поэтому разбирается
регулярным выражением. Решение нужно не как источник истины о составе (проекты
находит обход ФС), а чтобы отличать проекты, входящие в решение, от случайно
лежащих рядом.
"""

import re
from pathlib import Path

# Project("{тип}") = "имя", "путь", "{guid}"
_PROJECT_LINE = re.compile(r'^Project\("\{[^}]*\}"\)\s*=\s*"[^"]*",\s*"([^"]+)"', re.MULTILINE)


def parse_sln(path: Path, repo_root: Path) -> list[str]:
    """Репо-относительные пути `.csproj`, перечисленные в решении.

    Записи `Project(...)` описывают не только проекты C#:

    - папки решения (тип `{2150E333-…}`) кладут в поле пути собственное имя,
      то есть `"src", "src"` — не файл;
    - проекты других типов (`.dcproj`, `.vcxproj`, `.esproj`) — не наша забота.

    Отбираем только `.csproj`; всё остальное молча пропускаем.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    solution_dir = path.parent.resolve()
    root = repo_root.resolve()

    found = set()
    for raw in _PROJECT_LINE.findall(text):
        relative = raw.replace("\\", "/").strip()
        if not relative.lower().endswith(".csproj"):
            continue

        absolute = (solution_dir / relative).resolve()
        try:
            found.add(absolute.relative_to(root).as_posix())
        except ValueError:
            # Проект вне корня обхода: в манифест ему всё равно не попасть.
            continue

    return sorted(found)
