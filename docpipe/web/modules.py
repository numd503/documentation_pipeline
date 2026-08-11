"""Модуль фронта: чем он объявлен, где его границы и что в нём за стек.

Единицей модуля не может быть ни «каталог с `angular.json`», ни «проект nx»
по отдельности: в боевом репозитории есть и то, и другое — шесть фронтов
с собственным `angular.json` (один из них многопроектный) и один на nx
с шестью `apps/` и шестью `libs/`. Нужны обе формы.

Ключ модуля — **каталог-граница**, а не файл объявления: один многопроектный
`angular.json` объявляет несколько модулей, и ключ по файлу склеил бы их
в один. У .NET такой проблемы нет — там файл проекта и модуль всегда один
к одному, — поэтому и правило другое.
"""

import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docpipe.discovery import Discovered
from docpipe.model import Module
from docpipe.web.resolve import TsConfig, load_tsconfig, parse_jsonc

# Версии, которые кладутся в `target_frameworks`. Это ответ на вопрос «подо что
# собран модуль», то есть ровно то же, что `net8.0` у .NET.
_VERSIONED_PACKAGES = ("@angular/core", "typescript")

# Имена файлов прокси, если `angular.json` не назвал его сам.
_PROXY_NAMES = ("proxy.conf.js", "proxy.conf.json", "proxy.conf.mjs", "proxy.conf.cjs")

_TSCONFIG_NAME = "tsconfig.json"
_PACKAGE_NAME = "package.json"


@dataclass(frozen=True)
class WebModule:
    """Модуль фронта вместе с тем, что нужно для разбора, но не идёт в манифест.

    `key` — то же, чем на .NET служит путь `.csproj`: значение, по которому файл
    привязывается к модулю и которое входит в ключ символа. Это каталог-граница,
    потому что файл объявления модуля уникальным не бывает.
    """

    module: Module
    key: str
    source_root: str
    tsconfig_path: str | None = None
    proxy_conf: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    """Прочитать JSONC-файл. Битый или отсутствующий — пустой словарь.

    Отказ здесь роняет весь прогон ради одного модуля, а `angular.json`
    с комментарием — норма, а не поломка.
    """
    if not path.is_file():
        return {}
    try:
        data = parse_jsonc(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _join(*parts: str) -> str:
    joined = posixpath.join(*[part for part in parts if part])
    normalized = posixpath.normpath(joined) if joined else ""
    return "" if normalized == "." else normalized


def _nearest_upwards(root: Path, directory: str, name: str) -> str | None:
    """Ближайший файл с таким именем в каталоге или выше. Репо-относительный путь.

    Проверяются конкретные пути, а не обход: порядок обхода ФС источником
    порядка быть не может, а проверка существования детерминирована.
    """
    current = directory
    while True:
        candidate = _join(current, name)
        if (root / candidate).is_file():
            return candidate
        if not current:
            return None
        current = posixpath.dirname(current)


def _package_info(root: Path, directory: str) -> tuple[list[str], list[str]]:
    """`(target_frameworks, package_references)` из ближайшего `package.json`.

    Ближайший вверх, как это делает и сам npm: у nx-проекта внутри workspace
    своего `package.json` обычно нет, и стек он наследует от корня.
    """
    relative = _nearest_upwards(root, directory, _PACKAGE_NAME)
    if relative is None:
        return [], []

    data = _read_json(root / relative)
    dependencies: dict[str, Any] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        value = data.get(section)
        if isinstance(value, dict):
            dependencies.update(value)

    frameworks = [
        f"{name}@{str(dependencies[name]).lstrip('^~')}"
        for name in _VERSIONED_PACKAGES
        if isinstance(dependencies.get(name), str)
    ]
    return sorted(frameworks), sorted(dependencies)


def _proxy_conf(root: Path, directory: str, declared: str | None) -> str | None:
    """Путь `proxy.conf*`. Сначала тот, что назвал `angular.json`, потом поиск по имени.

    Файл нужен человеку, который заполняет `web.url_rewrite`: там записано,
    срезается ли префикс приложения по дороге к бэкенду. Вывести это из кода
    нельзя, а не заполнить — значит развести ключи фронта и бэка молча.
    """
    if declared:
        candidate = _join(directory, declared)
        if (root / candidate).is_file():
            return candidate

    for name in _PROXY_NAMES:
        candidate = _join(directory, name)
        if (root / candidate).is_file():
            return candidate
    return None


def _build(
    root: Path,
    *,
    name: str,
    project_file: str,
    source_root: str,
    declared_proxy: str | None = None,
) -> WebModule:
    directory = posixpath.dirname(project_file)
    frameworks, packages = _package_info(root, directory)
    key = source_root or "."

    return WebModule(
        module=Module(
            id=f"module:{key}",
            name=name,
            project_file=project_file,
            lang="ts",
            target_frameworks=frameworks,
            package_references=packages,
            domain="",
            enrolled=True,
        ),
        key=key,
        source_root=source_root,
        tsconfig_path=_nearest_upwards(root, source_root or directory, _TSCONFIG_NAME),
        proxy_conf=_proxy_conf(root, directory, declared_proxy),
    )


def _angular_modules(root: Path, relative: str) -> list[WebModule]:
    """Модули из `angular.json`: по одному на элемент `projects`.

    `root` и `sourceRoot` проекта заданы относительно каталога самого
    `angular.json`. Граница — `sourceRoot`, если он объявлен: в ML `root: ""`,
    то есть по `root` модулем оказался бы весь репозиторий вместе с соседними
    фронтами.
    """
    data = _read_json(root / relative)
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return []

    directory = posixpath.dirname(relative)
    found: list[WebModule] = []
    for name, project in sorted(projects.items()):
        if not isinstance(project, dict):
            continue
        declared_root = project.get("root")
        declared_source = project.get("sourceRoot")
        boundary = declared_source if isinstance(declared_source, str) else ""
        if not boundary:
            boundary = declared_root if isinstance(declared_root, str) else ""
        found.append(
            _build(
                root,
                name=name,
                project_file=relative,
                source_root=_join(directory, boundary),
                declared_proxy=_declared_proxy(project),
            )
        )
    return found


def _declared_proxy(project: dict[str, Any]) -> str | None:
    """`architect.serve.options.proxyConfig` — путь относительно каталога workspace."""
    architect = project.get("architect") or project.get("targets")
    if not isinstance(architect, dict):
        return None
    serve = architect.get("serve")
    if not isinstance(serve, dict):
        return None
    options = serve.get("options")
    if not isinstance(options, dict):
        return None
    value = options.get("proxyConfig")
    return value if isinstance(value, str) else None


def _nx_module(root: Path, relative: str) -> WebModule:
    """Модуль из `project.json` рядом с `nx.json`.

    Границей берётся **каталог самого `project.json`**, а не его `sourceRoot`:
    в nx `sourceRoot` задан относительно корня workspace, а не относительно
    проекта, и склейка с каталогом проекта дала бы путь вида
    `nx-app/apps/widget/apps/widget/src` — правдоподобный и несуществующий.
    """
    data = _read_json(root / relative)
    directory = posixpath.dirname(relative)
    name = data.get("name") if isinstance(data.get("name"), str) else posixpath.basename(directory)

    return _build(
        root,
        name=name or directory,
        project_file=relative,
        source_root=directory,
        declared_proxy=_declared_proxy(data),
    )


def discover_modules(root: Path, discovered: Discovered) -> list[WebModule]:
    """Модули фронта, отсортированные по ключу.

    Порядок правил: `angular.json`, затем nx, затем `package.json` вне корня,
    затем — весь репозиторий одним модулем. Первое сработавшее выигрывает,
    но **не глобально, а по месту**: `angular.json` в корне не отменяет
    nx-проекта в соседнем каталоге, иначе один многопроектный репозиторий
    схлопнулся бы в один модуль.
    """
    angular = [path for path in discovered.web_project_files if _is(path, "angular.json")]
    nx_roots = {
        posixpath.dirname(path) for path in discovered.web_project_files if _is(path, "nx.json")
    }
    project_files = [path for path in discovered.web_project_files if _is(path, "project.json")]
    packages = [path for path in discovered.web_project_files if _is(path, _PACKAGE_NAME)]

    found: list[WebModule] = []
    for relative in angular:
        found.extend(_angular_modules(root, relative))

    for relative in project_files:
        if any(_within(relative, workspace) for workspace in nx_roots):
            found.append(_nx_module(root, relative))

    if not found:
        for relative in packages:
            directory = posixpath.dirname(relative)
            if directory:
                found.append(
                    _build(root, name=directory, project_file=relative, source_root=directory)
                )

    if not found and packages:
        found.append(_build(root, name=".", project_file=packages[0], source_root=""))

    return sorted(found, key=lambda item: item.key)


def _is(path: str, name: str) -> bool:
    """Совпадает ли имя файла целиком.

    Сравнение по суффиксу поймало бы `DuplicatedRecordsBugEvanx.json` — на нём
    разведка однажды и построила целый раздел про nx.
    """
    return posixpath.basename(path) == name


def _within(path: str, directory: str) -> bool:
    """Лежит ли путь внутри каталога. Сравнение посегментное, а не по подстроке."""
    if not directory:
        return True
    return path.split("/")[: len(directory.split("/"))] == directory.split("/")


def module_of(path: str, modules: list[WebModule]) -> WebModule | None:
    """Модуль файла: побеждает самая длинная граница.

    Длинная, а не первая: у workspace с `sourceRoot: "src"` и вложенным
    nx-проектом файл nx-проекта подходит обоим, и принадлежит он ближнему.
    """
    best: WebModule | None = None
    for module in modules:
        if not _within(path, module.source_root):
            continue
        if best is None or len(module.source_root) > len(best.source_root):
            best = module
    return best


def map_files_to_modules(paths: list[str], modules: list[WebModule]) -> dict[str, str]:
    """Файл -> ключ модуля. Файлы вне всех границ пропускаются."""
    mapping: dict[str, str] = {}
    for path in paths:
        module = module_of(path, modules)
        if module is not None:
            mapping[path] = module.key
    return mapping


def load_aliases(root: Path, module: WebModule) -> TsConfig:
    """Алиасы `tsconfig` модуля. Без файла — пустая таблица, а не отказ."""
    if module.tsconfig_path is None:
        return TsConfig()
    return load_tsconfig(root, module.tsconfig_path)
