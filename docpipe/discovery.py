"""Обход файловой системы: поиск исходников с учётом исключений и scope.

Порядок обхода файловой системы нигде не используется как источник порядка —
все результаты сортируются явно. Без этого манифест переставал бы быть
воспроизводимым при переносе репозитория на другую машину или ФС.
"""

import os
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

# Расширения, которые ищем. Ключ — поле результата.
#
# `.slnx` — новый XML-формат решения (VS 17.10+). Игнорировать его нельзя:
# ABP мигрировал целиком, в его дереве 30 файлов `.slnx` и **ноль** `.sln`,
# то есть поиск только по `.sln` нашёл бы там ровно ничего.
_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "cs_files": (".cs",),
    "csproj_files": (".csproj",),
    "sln_files": (".sln", ".slnx"),
    "ts_files": (".ts",),
    "html_files": (".html",),
}

# Файлы, объявляющие модуль фронта. Отбираются по ПОЛНОМУ имени, а не по
# расширению: `.json` в репозитории тысячи, и в `_EXTENSIONS` такой отбор
# не помещается.
#
# Сравнение именно целого имени, а не суффикса: glob `*nx.json` ловит любой
# файл, чьё имя кончается на `nx.json`, — на разведке первой строкой раздела
# встал `DuplicatedRecordsBugEvanx.json` из тестовых данных .NET.
_WEB_PROJECT_FILE_NAMES: frozenset[str] = frozenset(
    {"angular.json", "nx.json", "project.json", "package.json"}
)


@dataclass(frozen=True)
class Discovered:
    """Найденные файлы. Пути репо-относительные POSIX, каждый список отсортирован."""

    cs_files: list[str]
    csproj_files: list[str]
    sln_files: list[str]  # и `.sln`, и `.slnx`
    ts_files: list[str]  # включая `*.spec.ts` и `*.d.ts` — они нужны резолву
    html_files: list[str]
    web_project_files: list[str]  # angular.json, nx.json, project.json, package.json


def matches_glob(path: str, glob: str) -> bool:
    """Совпадает ли POSIX-путь с glob-шаблоном.

    `fnmatch` не понимает `**` как «ноль или больше сегментов»: он транслирует
    `*` в `.*` без учёта разделителей, поэтому `**/obj/**` не ловит `obj/x.cs`
    в корне репозитория, а `**/*.g.cs` не ловит `Foo.g.cs` там же. Из-за
    обязательного `/` в шаблоне такие файлы молча просачивались бы в документацию.

    Лечится вторым прогоном с отброшенным префиксом `**/`.
    """
    if fnmatch(path, glob):
        return True
    if glob.startswith("**/"):
        return fnmatch(path, glob[3:])
    return False


def is_excluded(path: str, exclude_globs: list[str]) -> bool:
    """Отбрасывается ли путь хотя бы одним из шаблонов исключения."""
    return any(matches_glob(path, glob) for glob in exclude_globs)


def _directory_globs(exclude_globs: list[str]) -> list[str]:
    """Шаблоны для отсечения каталогов целиком.

    Из `**/obj/**` получается `**/obj`: если каталог совпал, то и любой файл
    под ним совпал бы с исходным шаблоном, поэтому в такой каталог можно не
    заходить вовсе. На большом репозитории это разница между обходом всего
    `node_modules` и мгновенным пропуском.

    Отсечение — чистая оптимизация: на результат оно повлиять не может,
    что проверяется тестом `test_pruning_does_not_change_result`.
    """
    return [glob[:-3] for glob in exclude_globs if glob.endswith("/**")]


def normalize_scope(scope: list[str] | None) -> list[tuple[str, ...]] | None:
    """Привести элементы scope к кортежам сегментов пути.

    Сравнение по сегментам, а не по подстроке: иначе scope `src/Sample.Common`
    захватил бы и `src/Sample.CommonExtras/`.
    """
    if scope is None:
        return None
    normalized = []
    for item in scope:
        cleaned = item.replace("\\", "/").strip("/")
        if cleaned in ("", "."):
            # Пустой scope означает «весь репозиторий» — ограничивать нечем.
            return None
        normalized.append(tuple(cleaned.split("/")))
    return sorted(set(normalized))


def in_scope(path: str, scope: list[tuple[str, ...]] | None) -> bool:
    """Лежит ли путь внутри одной из директорий scope."""
    if scope is None:
        return True
    segments = tuple(path.split("/"))
    return any(segments[: len(prefix)] == prefix for prefix in scope)


def discover(
    root: Path,
    exclude_globs: list[str],
    scope: list[str] | None = None,
    roots: list[str] | None = None,
) -> Discovered:
    """Найти исходники под `root` — и .NET, и фронта, одним обходом.

    Обход общий намеренно: два прохода по дереву репозитория пришлось бы держать
    согласованными по исключениям, scope и roots, и разошлись бы они молча.
    Шаг `web` читает `ts_files`/`html_files`/`web_project_files`, шаг 1 — остальные.

    Символические ссылки не разыменовываются: цикл через симлинк подвесил бы
    обход, а копия дерева по ссылке породила бы дубли символов.

    `roots` и `scope` сужают одинаково и **складываются**: первый постоянен
    и приходит из конфигурации, второй задаётся флагом на один прогон. Оба
    отсеивают файлы, а не каталоги: каталог может быть предком нужного
    и обязан быть пройден, даже если сам ничего не даёт.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"Корень обхода не является директорией: {root}")

    dir_globs = _directory_globs(exclude_globs)
    normalized_scope = normalize_scope(scope)
    normalized_roots = normalize_scope(roots)
    found: dict[str, list[str]] = {field: [] for field in _EXTENSIONS}
    found["web_project_files"] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        relative_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if relative_dir == "." else f"{relative_dir}/"

        # Отсечение каталогов правим на месте — os.walk читает dirnames после yield.
        # Сортировка здесь только ради предсказуемости обхода при отладке:
        # итоговый порядок всё равно задаётся sorted() ниже.
        dirnames[:] = sorted(
            name for name in dirnames if not is_excluded(f"{prefix}{name}", dir_globs)
        )

        for filename in sorted(filenames):
            suffix = Path(filename).suffix
            field = next((f for f, exts in _EXTENSIONS.items() if suffix in exts), None)
            if field is None and filename in _WEB_PROJECT_FILE_NAMES:
                field = "web_project_files"
            if field is None:
                continue

            relative_path = f"{prefix}{filename}"
            if is_excluded(relative_path, exclude_globs):
                continue
            if not in_scope(relative_path, normalized_scope):
                continue
            if not in_scope(relative_path, normalized_roots):
                continue

            found[field].append(relative_path)

    return Discovered(
        cs_files=sorted(found["cs_files"]),
        csproj_files=sorted(found["csproj_files"]),
        sln_files=sorted(found["sln_files"]),
        ts_files=sorted(found["ts_files"]),
        html_files=sorted(found["html_files"]),
        web_project_files=sorted(found["web_project_files"]),
    )
