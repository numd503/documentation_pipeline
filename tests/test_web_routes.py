"""Дерево роутов и якорь страницы (F08).

Якорь страницы — маршрут, а не имя компонента: `/models/loader/quiz` знает
пользователь, `LoaderQuizComponent` не знает никто снаружи.

Главное здесь межфайловое. В боевом модуле `loadChildren` — ноль,
`RouterModule.forRoot/forChild` — ноль, а 26 записей `path:` собраны спредом
импортированного массива. Разбор одного `app.routes.ts` даёт два пути
из двадцати шести, и молча.
"""

from pathlib import Path

import pytest

from docpipe.config import DocpipeConfig
from docpipe.discovery import discover
from docpipe.emit import exclude_globs
from docpipe.model import RouteEntry
from docpipe.web.parser import parse_file, parse_source
from docpipe.web.resolve import ResolveContext, build_context, load_tsconfig
from docpipe.web.routes import RouteScan, build_routes


@pytest.fixture
def scan(web_workspace: Path) -> RouteScan:
    found = discover(web_workspace, exclude_globs(DocpipeConfig()))
    results = [parse_file(web_workspace / relative, web_workspace) for relative in found.ts_files]
    context = build_context(results, load_tsconfig(web_workspace, "tsconfig.json"))
    sources = {relative: (web_workspace / relative).read_bytes() for relative in found.ts_files}
    return build_routes(sources, context)


def _paths(scan: RouteScan) -> list[str]:
    return [entry.path for entry in scan.entries if not entry.route_unresolved]


def _entry(scan: RouteScan, path: str) -> RouteEntry:
    found = [entry for entry in scan.entries if entry.path == path and not entry.route_unresolved]
    assert len(found) == 1, f"{path}: найдено {len(found)}"
    return found[0]


def _scan_of(sources: dict[str, str]) -> RouteScan:
    """Собрать дерево из исходников, заданных прямо в тесте."""
    encoded = {path: text.encode("utf-8") for path, text in sources.items()}
    results = [parse_source(source, path) for path, source in encoded.items()]
    context: ResolveContext = build_context(results, load_tsconfig(Path("/nonexistent"), "x.json"))
    return build_routes(encoded, context)


# --------------------------------------------------------------------------------------
# Обе межфайловые формы
# --------------------------------------------------------------------------------------


def test_paths_are_assembled_through_a_spread(scan: RouteScan) -> None:
    """Основная форма АС CF: `children: [...modelsPath]` из соседнего файла."""
    assert "models" in _paths(scan)
    assert "models/loader/quiz" in _paths(scan)


def test_paths_are_assembled_through_load_children(scan: RouteScan) -> None:
    """Вторая межфайловая форма: `() => import('./x').then(m => m.lazyRoutes)`."""
    assert "forecast/daily" in _paths(scan)


def test_child_table_is_not_a_root_of_its_own(scan: RouteScan) -> None:
    """Иначе `daily` появится и как `/forecast/daily`, и как `/daily`.

    Корнем считается таблица, на которую никто не ссылается: опознание
    по `provideRouter`/`RouterModule.forRoot` не годится — в боевом модуле
    их нет вовсе, а дерево собрано.
    """
    assert "daily" not in _paths(scan)
    assert "loader/quiz" not in _paths(scan)


def test_lazy_arrow_gives_the_inner_name_not_then() -> None:
    """`() => import('./x').then(m => m.ROUTES)` — здесь две стрелки.

    Реализация, берущая первое попавшееся свойство, подставит `then`
    и в имя таблицы, и в имя компонента: правдоподобно и целиком неверно.
    """
    scan = _scan_of(
        {
            "src/app.routes.ts": (
                "import { Routes } from '@angular/router';\n"
                "export const appRoutes: Routes = [\n"
                "  { path: 'a',\n"
                "    loadChildren: () => import('./inner').then((m) => m.innerRoutes) },\n"
                "];\n"
            ),
            "src/inner.ts": (
                "import { Routes } from '@angular/router';\n"
                "import { InnerComponent } from './inner.component';\n"
                "export const innerRoutes: Routes = [{ path: 'b', component: InnerComponent }];\n"
            ),
            "src/inner.component.ts": "export class InnerComponent {}\n",
        }
    )
    assert _paths(scan) == ["a/b"]
    assert _entry(scan, "a/b").component == "src/inner.component.InnerComponent"


# --------------------------------------------------------------------------------------
# Формы объявления компонента
# --------------------------------------------------------------------------------------


def test_component_is_resolved_to_its_declaration(scan: RouteScan) -> None:
    """В якорь идёт маршрут, но узел документации ищется по компоненту."""
    assert _entry(scan, "models").component == (
        "src/app/routes/models/list/list.component.ListComponent"
    )


def test_load_component_with_import(scan: RouteScan) -> None:
    assert _entry(scan, "models/loader/quiz").component == (
        "src/app/routes/models/quiz/quiz.component.QuizComponent"
    )


def test_load_component_short_form(scan: RouteScan) -> None:
    """`loadComponent: () => ForecastComponent` — без `import()` вовсе."""
    assert _entry(scan, "forecast/daily").component == (
        "src/app/routes/forecast/forecast.component.ForecastComponent"
    )


# --------------------------------------------------------------------------------------
# Что страницей не является
# --------------------------------------------------------------------------------------


def test_wildcard_and_redirect_are_not_pages(scan: RouteScan) -> None:
    """`**` — заглушка, редирект ведёт на страницу, уже посчитанную по маршруту."""
    assert not any(entry.path.endswith("**") for entry in scan.entries)
    assert "models/old" not in _paths(scan)


def test_component_outside_the_table_is_not_a_page(scan: RouteScan) -> None:
    """Переиспользуемый компонент страницей не становится."""
    components = {entry.component for entry in scan.entries}
    assert not any("legacy" in component for component in components)


# --------------------------------------------------------------------------------------
# Параметр маршрута
# --------------------------------------------------------------------------------------


def test_route_parameter_becomes_a_substitution(scan: RouteScan) -> None:
    """`:id` -> `{}` через `docpipe/route.py`, а не своей копией.

    Копия разошлась бы с нормализацией стороны .NET на первом же частном
    случае, и разошлась бы молча.
    """
    assert "models/{}" in _paths(scan)


def test_parameter_normalization_matches_the_backend_side() -> None:
    """Тот же ключ, что даёт шаблон контроллера `{id:guid}`."""
    from docpipe.route import normalize_route

    assert normalize_route("models/:id") == normalize_route("models/{id:guid}")


# --------------------------------------------------------------------------------------
# Невосстановленный маршрут — состояние, а не отсутствие
# --------------------------------------------------------------------------------------


def test_expression_segment_gives_an_unresolved_entry(scan: RouteScan) -> None:
    """Отсутствие узла неотличимо от «страницы нет».

    Покрытие посчиталось бы по заниженному знаменателю: отчёт показал бы
    успех там, где его нет.
    """
    unresolved = [entry for entry in scan.entries if entry.route_unresolved]

    assert scan.unresolved == 1
    assert unresolved[0].component.endswith("ListComponent")
    # Путь до ветки известен, дальше — нет.
    assert unresolved[0].path == "models"


# --------------------------------------------------------------------------------------
# Устойчивость
# --------------------------------------------------------------------------------------


def test_cycle_between_tables_does_not_hang() -> None:
    scan = _scan_of(
        {
            "src/a.ts": (
                "import { Routes } from '@angular/router';\n"
                "import { b } from './b';\n"
                "export const a: Routes = [{ path: 'a', children: [...b] }];\n"
            ),
            "src/b.ts": (
                "import { Routes } from '@angular/router';\n"
                "import { a } from './a';\n"
                "export const b: Routes = [{ path: 'b', children: [...a] }];\n"
            ),
        }
    )
    assert scan.entries == []


def test_array_without_the_routes_annotation_is_not_a_table() -> None:
    """«Массив объектов с ключом `path`» поймал бы любой конфиг меню."""
    scan = _scan_of(
        {
            "src/menu.ts": (
                "export const menu = [{ path: 'x', component: Something, title: 'X' }];\n"
            )
        }
    )
    assert scan.entries == []


def test_build_routes_is_deterministic(web_workspace: Path, scan: RouteScan) -> None:
    found = discover(web_workspace, exclude_globs(DocpipeConfig()))
    results = [parse_file(web_workspace / relative, web_workspace) for relative in found.ts_files]
    context = build_context(results, load_tsconfig(web_workspace, "tsconfig.json"))
    sources = {
        relative: (web_workspace / relative).read_bytes() for relative in reversed(found.ts_files)
    }

    assert build_routes(sources, context).entries == scan.entries
