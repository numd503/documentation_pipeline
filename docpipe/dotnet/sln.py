"""Разбор файла решения: классический `.sln` и новый XML-формат `.slnx`.

Решение нужно не как источник истины о составе (проекты находит обход ФС),
а чтобы отличать проекты, входящие в решение, от случайно лежащих рядом.

Оба формата приводятся к одному результату — списку репо-относительных путей
`.csproj`, поэтому вызывающему коду формат знать не нужно.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

# Project("{тип}") = "имя", "путь", "{guid}"
_PROJECT_LINE = re.compile(r'^Project\("\{[^}]*\}"\)\s*=\s*"[^"]*",\s*"([^"]+)"', re.MULTILINE)


def _to_repo_relative(raw: str, solution_dir: Path, repo_root: Path) -> str | None:
    """Путь из файла решения -> репо-относительный POSIX. `None`, если не .csproj или вне корня."""
    relative = raw.replace("\\", "/").strip()
    if not relative.lower().endswith(".csproj"):
        return None

    absolute = (solution_dir / relative).resolve()
    try:
        return absolute.relative_to(repo_root).as_posix()
    except ValueError:
        # Проект вне корня обхода: в манифест ему всё равно не попасть.
        return None


def _projects_from_sln(path: Path, solution_dir: Path, repo_root: Path) -> set[str]:
    """Записи `Project(...)` классического формата.

    Описывают не только проекты C#:

    - папки решения (тип `{2150E333-…}`) кладут в поле пути собственное имя,
      то есть `"src", "src"` — не файл;
    - проекты других типов (`.dcproj`, `.vcxproj`, `.esproj`) — не наша забота.

    Отбираем только `.csproj`; всё остальное молча пропускаем.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    found = {_to_repo_relative(raw, solution_dir, repo_root) for raw in _PROJECT_LINE.findall(text)}
    return {p for p in found if p is not None}


def _projects_from_slnx(path: Path, solution_dir: Path, repo_root: Path) -> set[str]:
    """Записи `<Project Path="…"/>` формата `.slnx`.

    Проекты лежат либо прямо под `<Solution>`, либо внутри произвольно
    вложенных `<Folder>`, поэтому обходим всё дерево, а не только детей корня.
    Читаем байтами по той же причине, что и `.csproj`: возможен BOM.
    """
    root = ET.fromstring(path.read_bytes())
    found = set()
    for element in root.iter():
        if element.tag.rpartition("}")[2] != "Project":
            continue
        raw = element.get("Path")
        if not raw:
            continue
        resolved = _to_repo_relative(raw, solution_dir, repo_root)
        if resolved is not None:
            found.add(resolved)
    return found


def parse_sln(path: Path, repo_root: Path) -> list[str]:
    """Репо-относительные пути `.csproj`, перечисленные в решении."""
    solution_dir = path.parent.resolve()
    root = repo_root.resolve()

    if path.suffix.lower() == ".slnx":
        return sorted(_projects_from_slnx(path, solution_dir, root))
    return sorted(_projects_from_sln(path, solution_dir, root))
