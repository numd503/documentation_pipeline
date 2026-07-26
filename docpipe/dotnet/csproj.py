"""Разбор `.csproj` в модель модуля.

Полноценного вычисления свойств MSBuild здесь нет и не планируется: условия,
подстановки `$(…)` и цепочки `Import` не разворачиваются. Задача — вытащить
структурные факты (имя, зависимости, целевые платформы), а не воспроизвести
поведение сборки.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from docpipe.model import Module

# Файлы уровня каталога, из которых проекты наследуют свойства.
# Directory.Packages.props формально предназначен для версий пакетов, но
# на практике в него кладут и TargetFramework (так сделано в eShopOnWeb).
_PROPS_FILES = ("Directory.Build.props", "Directory.Packages.props")


def _local_name(tag: str) -> str:
    """Имя тега без XML-namespace.

    Проекты в формате SDK идут без namespace, но legacy-формат объявляет
    `xmlns="http://schemas.microsoft.com/developer/msbuild/2003"`, и тогда
    теги выглядят как `{http://…}PropertyGroup`. Сравнение по полному тегу
    молча перестало бы находить что-либо в старых проектах.
    """
    return tag.rpartition("}")[2]


def _parse_xml(path: Path) -> ET.Element:
    """Разобрать XML, устойчиво к BOM.

    Реальные `.csproj` часто сохранены в UTF-8 с BOM (все 10 в eShopOnWeb).
    Чтение через `read_text(encoding="utf-8")` оставило бы BOM первым символом,
    и разбор упал бы с `not well-formed`. Байты `ElementTree` обрабатывает сам.
    """
    return ET.fromstring(path.read_bytes())


def _iter_elements(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _split_frameworks(text: str) -> list[str]:
    return [part.strip() for part in text.split(";") if part.strip()]


def _target_frameworks(root: ET.Element) -> list[str]:
    """Целевые платформы из `<TargetFramework>` или `<TargetFrameworks>`."""
    found: set[str] = set()
    for name in ("TargetFramework", "TargetFrameworks"):
        for element in _iter_elements(root, name):
            if element.text:
                found.update(_split_frameworks(element.text))
    return sorted(found)


def _inherited_frameworks(csproj: Path, repo_root: Path) -> list[str]:
    """Целевые платформы из ближайшего props-файла вверх по дереву.

    Большинство реальных проектов **не объявляют** `TargetFramework` у себя:
    в eShopOnWeb это 0 из 10, значение приходит из `Directory.Packages.props`
    уровня решения. Без этого обхода `target_frameworks` был бы пуст у всех
    модулей сразу.

    Ближайший файл выигрывает, как и в MSBuild. Условия не вычисляются.
    """
    current = csproj.parent.resolve()
    stop = repo_root.resolve()

    while True:
        for name in _PROPS_FILES:
            candidate = current / name
            if candidate.is_file():
                frameworks = _target_frameworks(_parse_xml(candidate))
                if frameworks:
                    return frameworks
        if current == stop or current.parent == current:
            return []
        current = current.parent


def _includes(root: ET.Element, name: str) -> list[str]:
    """Значения атрибута Include у элементов с заданным именем."""
    values = []
    for element in _iter_elements(root, name):
        include = element.get("Include")
        if include:
            values.append(include.strip())
    return values


def parse_csproj(path: Path, repo_root: Path) -> Module:
    """Разобрать файл проекта в `Module`.

    `domain` и `enrolled` заполняются заглушками: их проставляет `tree.py`,
    когда становится известна конфигурация.
    """
    root = _parse_xml(path)
    name = path.stem

    frameworks = _target_frameworks(root) or _inherited_frameworks(path, repo_root)

    project_references = sorted(
        {Path(value.replace("\\", "/")).stem for value in _includes(root, "ProjectReference")}
    )
    package_references = sorted(set(_includes(root, "PackageReference")))

    return Module(
        id=f"module:{name}",
        name=name,
        csproj=path.resolve().relative_to(repo_root.resolve()).as_posix(),
        target_frameworks=frameworks,
        project_references=project_references,
        package_references=package_references,
        domain="",
        enrolled=True,
    )
