"""Обход файловой системы: поиск исходников с учётом исключений и scope.

Порядок обхода файловой системы нигде не используется как источник порядка —
все результаты сортируются явно. Без этого манифест переставал бы быть
воспроизводимым при переносе репозитория на другую машину или ФС.
"""

import os
from collections import Counter
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
    # Исходники SQL: процедуры, функции, представления. Нужны там, где часть
    # работы с данными живёт в базе, и пустой список — законный ответ,
    # а не пробел разбора.
    "sql_files": (".sql",),
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


# Поля `Discovered`, которые читает шаг `web`; остальные — шаг 1. Нужно тому,
# кто спрашивает «чей это файл» мимо обхода (`setup explain`): своя копия
# списка расширений разошлась бы с обходом на первом новом расширении.
WEB_FIELDS: frozenset[str] = frozenset({"ts_files", "html_files", "web_project_files"})


def file_field(filename: str) -> str | None:
    """Поле `Discovered`, в которое попадает файл с этим именем; `None` — обходу не нужен.

    Одна функция на обход и на тех, кто считает файлы мимо него: ответ
    «исходник ли это» в инструменте обязан быть один.
    """
    suffix = Path(filename).suffix
    field = next((f for f, exts in _EXTENSIONS.items() if suffix in exts), None)
    if field is None and filename in _WEB_PROJECT_FILE_NAMES:
        field = "web_project_files"
    return field


@dataclass(frozen=True)
class Discovered:
    """Найденные файлы. Пути репо-относительные POSIX, каждый список отсортирован.

    `excluded` — исходники, которые отсекли шаблоны исключения: путь → все
    совпавшие с ним шаблоны в порядке `exclude_globs`. Заполняется только
    при `discover(..., count_excluded=True)`, иначе `None`: отсечённые каталоги
    обход не открывает, и счёт без них был бы меньше настоящего — у `**/obj/**`
    на свежем клоне ноль при сотне файлов под `obj/`. `None`, а не пустой
    словарь, — чтобы «не считали» не читалось как «не отсекли ничего».
    """

    cs_files: list[str]
    csproj_files: list[str]
    sln_files: list[str]  # и `.sln`, и `.slnx`
    ts_files: list[str]  # включая `*.spec.ts` и `*.d.ts` — они нужны резолву
    html_files: list[str]
    sql_files: list[str]
    web_project_files: list[str]  # angular.json, nx.json, project.json, package.json
    excluded: dict[str, tuple[str, ...]] | None = None

    @property
    def excluded_by(self) -> dict[str, int] | None:
        """Шаблон → сколько исходников он отсёк; файл под двумя шаблонами — у обоих.

        Охват шаблона `exclude` (S24): правка одного из двух совпавших файл
        не вернёт, поэтому счёт у каждого свой, а не «первому совпавшему».
        Шаблона, не отсёкшего ничего, в словаре нет.
        """
        if self.excluded is None:
            return None
        counts = Counter(glob for globs in self.excluded.values() for glob in globs)
        return dict(sorted(counts.items()))


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


def _matched(path: str, exclude_globs: list[str]) -> tuple[str, ...]:
    """Все шаблоны исключения, совпавшие с путём, в порядке списка."""
    return tuple(glob for glob in exclude_globs if matches_glob(path, glob))


def _record_pruned(
    root: Path, directory: str, exclude_globs: list[str], excluded: dict[str, tuple[str, ...]]
) -> None:
    """Исходники под отсечённым каталогом — в `excluded`, без сбора в результат.

    Отдельный проход внутрь каталога, в который основной обход не заходит:
    иначе охват шаблона, отсёкшего каталог целиком, был бы нулём. Тот же
    отбор исходников (`file_field`), те же шаблоны и тот же отказ разыменовывать
    ссылки, что у основного обхода, — счёт не зависит от того, отсечён каталог
    или пройден (`test_pruned_and_walked_count_the_same`).

    Каталог-ссылку не открывает: `os.walk` разыменовывает сам корень прохода,
    а основной обход в ссылку не зашёл бы.
    """
    if (root / directory).is_symlink():
        return
    for dirpath, _, filenames in os.walk(root / directory, followlinks=False):
        relative_dir = Path(dirpath).relative_to(root).as_posix()
        for filename in filenames:
            if file_field(filename) is None:
                continue
            relative_path = f"{relative_dir}/{filename}"
            matched = _matched(relative_path, exclude_globs)
            if matched:
                excluded[relative_path] = matched


def discover(
    root: Path,
    exclude_globs: list[str],
    scope: list[str] | None = None,
    roots: list[str] | None = None,
    *,
    count_excluded: bool = False,
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

    `count_excluded` — записать отсечённые исходники в `Discovered.excluded`
    (охват шаблонов `exclude`, S24). Отсечённые каталоги тогда открываются
    отдельным проходом только ради счёта: в `node_modules` на сотню тысяч
    файлов это заметно, поэтому `scan` и `web scan` флаг не ставят. Отсев
    проверяется раньше `scope` и `roots` — как и при сборе: шаблон отсёк
    и файл вне корней.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"Корень обхода не является директорией: {root}")

    dir_globs = _directory_globs(exclude_globs)
    normalized_scope = normalize_scope(scope)
    normalized_roots = normalize_scope(roots)
    found: dict[str, list[str]] = {field: [] for field in _EXTENSIONS}
    found["web_project_files"] = []
    excluded: dict[str, tuple[str, ...]] | None = {} if count_excluded else None

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        relative_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if relative_dir == "." else f"{relative_dir}/"

        # Отсечение каталогов правим на месте — os.walk читает dirnames после yield.
        # Сортировка здесь только ради предсказуемости обхода при отладке:
        # итоговый порядок всё равно задаётся sorted() ниже.
        walked: list[str] = []
        for name in sorted(dirnames):
            if not is_excluded(f"{prefix}{name}", dir_globs):
                walked.append(name)
            elif excluded is not None:
                _record_pruned(root, f"{prefix}{name}", exclude_globs, excluded)
        dirnames[:] = walked

        for filename in sorted(filenames):
            field = file_field(filename)
            if field is None:
                continue

            relative_path = f"{prefix}{filename}"
            if excluded is not None:
                matched = _matched(relative_path, exclude_globs)
                if matched:
                    excluded[relative_path] = matched
                    continue
            elif is_excluded(relative_path, exclude_globs):
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
        sql_files=sorted(found["sql_files"]),
        web_project_files=sorted(found["web_project_files"]),
        excluded=dict(sorted(excluded.items())) if excluded is not None else None,
    )


def map_files_to_modules(cs_files: list[str], csproj_files: list[str]) -> dict[str, str]:
    """Файл -> `.csproj` ближайшего вверх по дереву проекта.

    Файлы, не попавшие ни в один проект, в результат не входят: документировать
    код вне проектов некуда. Такое встречается — общий код, подключённый через
    `<Compile Include>`, физически лежит вне каталогов проектов (см. T05b).
    """
    directories = {str(Path(c).parent): c for c in csproj_files}

    mapping: dict[str, str] = {}
    for relative in cs_files:
        current = Path(relative).parent
        while True:
            if str(current) in directories:
                mapping[relative] = directories[str(current)]
                break
            if current == Path("."):
                break
            current = current.parent
    return mapping
