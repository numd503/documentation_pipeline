"""Модуль фронта: две формы объявления и границы (F06).

Единицей модуля не может быть ни «каталог с `angular.json`», ни «проект nx»
по отдельности: в боевом репозитории есть и то, и другое. Проверяются обе формы
и то, что ключ модуля не склеивает разные модули в один.
"""

import json
from pathlib import Path

from docpipe.config import DocpipeConfig
from docpipe.discovery import Discovered, discover
from docpipe.emit import exclude_globs
from docpipe.web.modules import (
    WebModule,
    discover_modules,
    load_aliases,
    map_files_to_modules,
    module_of,
)


def _modules(root: Path) -> list[WebModule]:
    return discover_modules(root, discover(root, exclude_globs(DocpipeConfig())))


def _write(root: Path, relative: str, payload: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------------------
# Фикстура: обе формы сразу
# --------------------------------------------------------------------------------------


def test_both_forms_give_modules(web_workspace: Path) -> None:
    modules = _modules(web_workspace)

    assert [module.module.name for module in modules] == ["widget", "tr-p"]
    assert [module.module.project_file for module in modules] == [
        "nx-app/apps/widget/project.json",
        "angular.json",
    ]


def test_module_is_marked_as_typescript(web_workspace: Path) -> None:
    assert {module.module.lang for module in _modules(web_workspace)} == {"ts"}


def test_stack_versions_and_store_are_in_the_module(web_workspace: Path) -> None:
    """Версия Angular и наличие стора — ответ на вопрос «подо что собран модуль».

    Тот же смысл, что `net8.0` у .NET, поэтому и поле то же.
    """
    workspace = next(module for module in _modules(web_workspace) if module.key == "src")

    assert workspace.module.target_frameworks == ["@angular/core@17.3.12", "typescript@5.4.5"]
    assert "@ngxs/store" in workspace.module.package_references
    assert "@ngrx/store" not in workspace.module.package_references


def test_proxy_conf_is_found(web_workspace: Path) -> None:
    """Файл нужен человеку, заполняющему `url_rewrite`: вывести это из кода нельзя."""
    workspace = next(module for module in _modules(web_workspace) if module.key == "src")
    assert workspace.proxy_conf == "proxy.conf.js"


def test_aliases_are_loaded_for_the_module(web_workspace: Path) -> None:
    workspace = next(module for module in _modules(web_workspace) if module.key == "src")
    assert "@shared/*" in load_aliases(web_workspace, workspace).paths


def test_every_source_file_belongs_to_a_module(web_workspace: Path) -> None:
    found = discover(web_workspace, exclude_globs(DocpipeConfig()))
    mapping = map_files_to_modules(found.ts_files, _modules(web_workspace))

    assert set(mapping) == set(found.ts_files)
    assert mapping["src/main.ts"] == "src"
    assert mapping["nx-app/apps/widget/src/app/widget.service.ts"] == "nx-app/apps/widget"


# --------------------------------------------------------------------------------------
# Ключ модуля: файл объявления уникальным не бывает
# --------------------------------------------------------------------------------------


def test_multi_project_angular_json_gives_one_module_per_project(tmp_path: Path) -> None:
    """Один файл объявляет несколько модулей — ключ по файлу склеил бы их в один."""
    _write(
        tmp_path,
        "angular.json",
        {
            "projects": {
                "app": {"root": "projects/app", "sourceRoot": "projects/app/src"},
                "admin": {"root": "projects/admin", "sourceRoot": "projects/admin/src"},
                "shell": {"root": "projects/shell"},
            }
        },
    )
    _write(tmp_path, "package.json", {"dependencies": {"@angular/core": "^17.0.0"}})

    modules = _modules(tmp_path)
    assert [module.module.name for module in modules] == ["admin", "app", "shell"]
    assert len({module.module.id for module in modules}) == 3


def test_same_project_name_in_different_directories_differs(tmp_path: Path) -> None:
    """Имена проектов не уникальны — на .NET это стоило отдельного разбора."""
    for directory in ("front-a", "front-b"):
        _write(
            tmp_path,
            f"{directory}/angular.json",
            {"projects": {"app": {"root": "", "sourceRoot": "src"}}},
        )
        _write(tmp_path, f"{directory}/package.json", {"dependencies": {}})

    modules = _modules(tmp_path)
    assert [module.module.name for module in modules] == ["app", "app"]
    assert [module.module.id for module in modules] == [
        "module:front-a/src",
        "module:front-b/src",
    ]


def test_source_root_wins_over_root(tmp_path: Path) -> None:
    """В ML `root: ""`: по нему модулем оказался бы весь репозиторий целиком.

    А в нём лежат ещё шесть фронтов и весь бэкенд.
    """
    _write(tmp_path, "angular.json", {"projects": {"app": {"root": "", "sourceRoot": "src"}}})
    _write(tmp_path, "package.json", {"dependencies": {}})

    assert [module.key for module in _modules(tmp_path)] == ["src"]


# --------------------------------------------------------------------------------------
# nx
# --------------------------------------------------------------------------------------


def test_project_json_without_nx_json_is_not_a_module(tmp_path: Path) -> None:
    """`project.json` без `nx.json` рядом — не обязательно nx-проект."""
    _write(tmp_path, "apps/widget/project.json", {"name": "widget"})
    _write(tmp_path, "package.json", {"dependencies": {}})

    assert [module.module.name for module in _modules(tmp_path)] == ["."]


def test_nx_project_boundary_is_its_own_directory(tmp_path: Path) -> None:
    """`sourceRoot` в nx задан относительно корня workspace, а не проекта.

    Склейка с каталогом проекта дала бы `apps/widget/apps/widget/src` —
    путь правдоподобный и несуществующий, а файлы модуля остались бы
    ничьими и выпали бы из манифеста.
    """
    _write(tmp_path, "nx.json", {"npmScope": "demo"})
    _write(
        tmp_path, "apps/widget/project.json", {"name": "widget", "sourceRoot": "apps/widget/src"}
    )
    _write(tmp_path, "package.json", {"dependencies": {}})
    _write(tmp_path, "apps/widget/src/main.ts", "export class A {}")

    modules = _modules(tmp_path)
    assert [module.key for module in modules] == ["apps/widget"]
    assert map_files_to_modules(["apps/widget/src/main.ts"], modules) == {
        "apps/widget/src/main.ts": "apps/widget"
    }


# --------------------------------------------------------------------------------------
# Запасные правила
# --------------------------------------------------------------------------------------


def test_package_json_outside_root_becomes_a_module(tmp_path: Path) -> None:
    _write(tmp_path, "packages/ui/package.json", {"name": "ui", "dependencies": {}})

    assert [module.key for module in _modules(tmp_path)] == ["packages/ui"]


def test_bare_repository_is_one_module(tmp_path: Path) -> None:
    """Иначе символы окажутся ничьими и в манифест не попадут вовсе."""
    _write(tmp_path, "package.json", {"name": "app", "dependencies": {}})

    modules = _modules(tmp_path)
    assert [module.key for module in modules] == ["."]
    assert map_files_to_modules(["src/a.ts"], modules) == {"src/a.ts": "."}


def test_no_declaration_files_gives_no_modules(tmp_path: Path) -> None:
    assert _modules(tmp_path) == []


# --------------------------------------------------------------------------------------
# Границы
# --------------------------------------------------------------------------------------


def test_nearest_boundary_wins(tmp_path: Path) -> None:
    """Файл вложенного проекта подходит обоим модулям и принадлежит ближнему."""
    _write(tmp_path, "angular.json", {"projects": {"outer": {"root": "", "sourceRoot": "src"}}})
    _write(tmp_path, "nx.json", {"npmScope": "demo"})
    _write(tmp_path, "src/inner/project.json", {"name": "inner"})
    _write(tmp_path, "package.json", {"dependencies": {}})

    modules = _modules(tmp_path)
    assert module_of("src/inner/a.ts", modules) is not None
    assert module_of("src/inner/a.ts", modules).key == "src/inner"  # type: ignore[union-attr]
    assert module_of("src/other/a.ts", modules).key == "src"  # type: ignore[union-attr]


def test_boundary_is_compared_by_segments(tmp_path: Path) -> None:
    """`src` не должен захватывать `srcs`: сравнение по подстроке захватило бы."""
    _write(tmp_path, "angular.json", {"projects": {"app": {"root": "", "sourceRoot": "src"}}})
    _write(tmp_path, "package.json", {"dependencies": {}})

    assert module_of("srcs/a.ts", _modules(tmp_path)) is None


def test_broken_declaration_file_does_not_stop_the_run(tmp_path: Path) -> None:
    """Отказ ронял бы весь прогон ради одного модуля."""
    _write(tmp_path, "angular.json", "{ not json at all")
    _write(tmp_path, "packages/ui/package.json", {"name": "ui"})

    assert [module.key for module in _modules(tmp_path)] == ["packages/ui"]


def test_discover_modules_is_deterministic(web_workspace: Path) -> None:
    found = discover(web_workspace, exclude_globs(DocpipeConfig()))
    shuffled = Discovered(
        cs_files=[],
        csproj_files=[],
        sln_files=[],
        ts_files=list(reversed(found.ts_files)),
        html_files=[],
        sql_files=[],
        web_project_files=list(reversed(found.web_project_files)),
    )

    assert [module.key for module in discover_modules(web_workspace, found)] == [
        module.key for module in discover_modules(web_workspace, shuffled)
    ]
