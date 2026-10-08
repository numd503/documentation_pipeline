"""Ревью настройки: новое и прежние решения (`setup review`, S25).

Вход «ревью» скилла настройки (Р-4): после изменений в репозитории ответить,
что появилось и **как прежние решения обошлись с новым кодом**. База —
последний коммит файлов настройки (П-1): коммит и есть принятие, отдельного
файла с принятым состоянием нет. Новый код — то, что git видит изменённым
после базы: коммиты, рабочее дерево и неотслеживаемые файлы.

Ревью сравнивает **код, а не числа**. Число отсеянных растёт и тогда, когда
правило работает как задумано (новые `*Dto` под правилом «dto не
документируем»), поэтому человеку нужен не коэффициент, а список: какие
решения применились к коду, которого он ещё не видел, — его он и проверяет.

Разделы отчёта:

- `applied` — решения с охватом в новых файлах: разбивка «файл → сколько
  решено» того же подсчёта, что у `setup status` (`status_detail`);
  записи «не берём» — первыми;
- `new_findings` — находки `setup status`, у которых есть места в новых
  файлах, с этими местами;
- `dead_decisions` — решения без охвата во всём репозитории: `url_rewrite`
  модуля, которого нет, обёртка без вызовов, правило владения без побед;
  у каждого — адрес: удалить запись или держать с причиной (`keep_key`);
- `inapplicable` и `kept_unused` — записи без охвата, которые ревью
  не красят (S35): нечего решать по построению (фронта нет, файлы отсёк
  `exclude`, переключатель `require_public`) и правила отсева, которые
  человек держит с причиной `unused_reason`;
- `config_dirty` — файлы настройки изменены и не закоммичены: ревью
  сравнивает код, а несохранённые решения ещё не решения.

Git зовётся подпроцессом (`git -C <root> …`), как у разведки: только
чтение, сеть не нужна. `GIT_OPTIONAL_LOCKS=0` — чтобы `git status` и
`git diff` не обновляли индекс: команда настройки ничего не пишет (Р-1).
"""

import os
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.classify import REQUIRE_PUBLIC, RESERVED_PREFIX, ExcludeRule, condition_values
from docpipe.discovery import file_field, in_scope, matches_glob, normalize_scope
from docpipe.hashing import stable_json_dumps
from docpipe.setup.candidates import DEFAULT_LIMIT
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.status import (
    CoverageDetail,
    SetupStatus,
    build_status,
    format_status,
    status_detail,
)
from docpipe.stats import plural

# 1.1 — `inapplicable`, `kept_unused`, `DeadDecision.keep_key` (S35).
SCHEMA_VERSION: Final = "1.1"

# Ключи охвата, которых нет в `dead_decisions`: правило классификации без побед
# не решение, переставшее работать, а запас набора. Нейтральный набор несёт
# правила на виды, которых в репозитории может не быть вовсе (`web.guard`,
# `ignite.compute`): на фикстуре шва без побед 13 правил классификации из 18,
# и с ними `--fail-on-changes` был бы красным на любом репозитории с первого
# дня. Символ, который правило могло бы взять, без него не теряется — он
# `undecided`, и об этом говорит находка. Правило отсева без охвата остаётся:
# «не документируем» — решение человека с причиной.
NOT_DEAD_KEYS: Final = frozenset({"dotnet.rules", "web.rules"})

# Правила отсева набора: ключ охвата → секция файла правил и адрес причины
# держать правило без охвата (`unused_reason`).
EXCLUSION_KEYS: Final[dict[str, Literal["dotnet", "web"]]] = {
    "dotnet.exclude": "dotnet",
    "web.exclude": "web",
}
KEEP_KEYS: Final[dict[str, str]] = {key: f"{key}.rules[].unused_reason" for key in EXCLUSION_KEYS}

# Ключи охвата записей «не берём», кроме секции `link` (`is_refusal`): отсев
# файлов и символов, модули вне области, «это не обёртка», `remove` страниц.
# В `applied` они идут первыми: правило отсева, молча забравшее новый код, —
# то, ради чего ревью и нужно (S25), а по одному числу оно уходило за корни
# и правила владения, у которых новых файлов больше всех. Прогон S31 на
# squidex после S33: `data.contracts` с новым DTO — седьмой из 11, и страница
# `applied` (`offset`, бюджет ответа сервера) срезала бы его первым.
REFUSAL_KEYS: Final = frozenset(
    {"exclude", "not_enrolled", "dotnet.exclude", "web.exclude", "web.not_wrappers", "remove"}
)

# Записи, которые решают о вызовах и символах фронта: без фронта им нечего
# решать (`no_front`). Кроме `web.*` — две записи секции `link` о вызовах:
# `link.external_callers` решает об эндпоинтах .NET и сюда не входит.
FRONT_LINK_KEYS: Final = frozenset({"link.unresolvable", "link.external_targets"})

# Коды `why` у `inapplicable`.
WHY_NO_FRONT: Final = "no_front"
WHY_UNDER_EXCLUDE: Final = "under_exclude"
WHY_SWITCH: Final = "switch"

# Коды пометок отчёта. Стабильные: по ним скилл (S29) выбирает, что сказать.
NOTE_UNCOMMITTED: Final = "config.uncommitted"
NOTE_OUTSIDE: Final = "config.outside_repository"
NOTE_SUBMODULES: Final = "git.submodules"
NOTE_DEFECTS: Final = "status.defects"

# Сколько знаков хэша базы в тексте. В JSON — хэш целиком.
SHORT_HASH: Final = 12


class HistoryError(InputError):
    """Ревью не на чем построить: не git, ревизия не найдена, база за границей клона.

    Подкласс `InputError`: CLI и сервер отвечают на него кодом 2 с сообщением,
    как на любой негодный вход, но с другой подписью — это не ошибка
    конфигурации.
    """


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Note(_Base):
    """Пометка отчёта: код (`NOTE_*`) и что он значит."""

    code: str
    message: str


def is_refusal(key: str) -> bool:
    """Ключ охвата записи «не берём»: `REFUSAL_KEYS` и вся секция `link`.

    Каждая запись секции `link` — решение человека с его причиной (протокол
    интервью, «Кто решает»), в том числе `external_callers` с `document: true`.
    """
    return key in REFUSAL_KEYS or key.startswith("link.")


class AppliedDecision(_Base):
    """Решение, применившееся к новому коду.

    `count` — единиц кода, решённых в новых файлах (сумма разбивки по этим
    файлам: символ из двух новых файлов — в обоих), `total` — охват во всём
    репозитории, как в `setup status`. `files` — новые файлы, до `--limit`,
    `files_total` — сколько их всего. В `Review.applied` первыми идут записи
    «не берём» (`is_refusal`), внутри обеих групп — по `count`, затем по `id`.
    """

    id: str
    file: str
    key: str
    value: str
    reason: str
    count: int
    total: int
    files: list[str]
    files_total: int


class NewFinding(_Base):
    """Находка `setup status` с местами в новых файлах.

    `count` — число находки во всём репозитории (как в `setup status`),
    `places` — её мест в новых файлах, `examples` — эти места до `--limit`.
    Единица `count` — та, что у находки (у `link.module_without_rewrite` —
    модули), а `places` — всегда места.
    """

    code: str
    category: Literal["decision", "defect"]
    title: str
    decision_home: str
    count: int
    places: int
    examples: list[str]


class _Decision(_Base):
    """Запись настройки без охвата: где лежит и почему её писали."""

    id: str
    file: str
    key: str
    value: str
    reason: str


class DeadDecision(_Decision):
    """Решение без охвата во всём репозитории: оно больше не решает ничего.

    Адрес решения: удалить запись (`file` → `key`, `value`) — по слову
    человека — или держать с причиной в `keep_key`. `keep_key` есть только
    у правил отсева набора (`dotnet.exclude.rules[].unused_reason`,
    `web.exclude.rules[].unused_reason`); у записей `docpipe.yaml`,
    `pages.yaml`, `ownership.yaml` и у краткой формы `exclude` — `None`:
    обёртку без вызовов и модуль без кода правят или удаляют, держать их
    незачем.
    """

    keep_key: str | None


class InapplicableDecision(_Decision):
    """Запись без охвата, которой нечего решать по построению: не мёртвая и не красит ревью.

    `why`: `no_front` — фронта нет (`web.roots` не задан или `[]`, шаг `web`
    без модулей), а запись решает о фронте; `under_exclude` — файлы, которые
    правило отсева решило бы, отсёк `exclude`, а среди прочитанных шагом
    его файлов нет (`detail` — шаблоны `exclude`, накрывшие эти файлы);
    `switch` — `exclude.require_public`, переключатель секции, а не запись
    о группе.
    """

    why: Literal["no_front", "under_exclude", "switch"]
    detail: list[str]


class KeptDecision(_Decision):
    """Правило отсева без охвата, которое человек держит с причиной (`unused_reason`)."""

    unused_reason: str


class Review(_Base):
    """Ответ `setup review`.

    `base` — коммит, от которого считается новый код; `None` — базы нет
    (файлы настройки не коммитились или лежат вне репозитория): тогда
    `status` — отчёт `setup status` целиком, а разделы ревью пусты.
    `since` — ревизия, как её назвали (`--since`); `None` — база взята
    по последнему коммиту файлов настройки (`config_files`).

    `new_files` — изменённые после базы файлы под `--root` (без файлов
    настройки и кэша разбора), до `--limit`; `new_files_total` — сколько их.
    `outside_area` — те из них, что вне `roots` и `web.roots`: обход их
    не читает, но решения о них есть (`exclude`, фронт вне корней).
    `unexplained` и `defects` — те же суммы, что у `setup status`.

    Записи без охвата — тремя списками (S35): `dead_decisions` — перестали
    решать то, что решали, и у каждой адрес; `inapplicable` — нечего решать
    по построению; `kept_unused` — человек держит их с причиной. Красит
    ревью (`has_changes`) только первый.
    """

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    base: str | None
    since: str | None
    config_files: list[str]
    config_dirty: bool
    config_changes: list[str]
    new_files: list[str]
    new_files_total: int
    outside_area: list[str]
    outside_area_total: int
    applied: list[AppliedDecision]
    new_findings: list[NewFinding]
    dead_decisions: list[DeadDecision]
    inapplicable: list[InapplicableDecision]
    kept_unused: list[KeptDecision]
    unexplained: int
    defects: int
    notes: list[Note]
    status: SetupStatus | None


# --------------------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Позвать git в `root`; не нашёлся сам git — `HistoryError`.

    `core.quotePath=false` — как у разведки: кириллица в путях приходит как
    есть. `GIT_OPTIONAL_LOCKS=0` — `status` и `diff` не берут блокировку
    индекса и не переписывают его: команда ревью только читает.
    """
    try:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except OSError as exc:
        raise HistoryError(f"ревью опирается на git, а он не запускается: {exc}") from exc


def _entries(output: str) -> list[str]:
    """Записи вывода с `-z`: разделитель — NUL, пустых нет."""
    return [item for item in output.split("\0") if item]


def _error(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stderr.strip() or f"код {proc.returncode}"


@dataclass(frozen=True)
class _History:
    """Что ревью узнало от git, до любого прогона."""

    base: str | None
    config_files: list[tuple[str, Path]]
    config_changes: list[str]
    new_files: list[str]
    submodules: list[str]


def _toplevel(root: Path) -> Path:
    """Корень рабочего дерева git, в котором лежит `--root`. Не git — `HistoryError`."""
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        raise HistoryError(
            f"ревью опирается на git, а {root} не под git ({_error(proc)}); "
            "без истории — `setup status`"
        )
    return Path(proc.stdout.strip()).resolve()


def _config_files(ctx: SetupContext, top: Path) -> list[tuple[str, Path]]:
    """Файлы настройки внутри рабочего дерева: подпись (как `file` у охвата) и путь.

    Берутся файлы, из которых прогон читает решения: `docpipe.yaml`, наборы
    правил, `pages.yaml`, правила владения — те же, что называет охват.
    **Каталог настройки целиком не берётся:** у фикстуры шва и у любого
    репозитория, где `docpipe.yaml` лежит в корне, каталог настройки — весь
    репозиторий, и база съезжала бы на каждый коммит кода, а `config_dirty`
    вспыхивал бы от любой правки исходника. Файл вне рабочего дерева
    (набор правил, взятый с машины) в историю этого репозитория не входит.
    """
    named: list[tuple[str, Path | None]] = []
    if ctx.config is not None:
        named.append((ctx.config_label, ctx.config))
    inputs: tuple[Callable[[], Path | None], ...] = (
        lambda: ctx.rules_file,
        lambda: ctx.web_rules_file,
        lambda: ctx.pages_file,
        lambda: ctx.ownership_file,
    )
    for get in inputs:
        try:
            path = get()
        except InputError:
            # `web.pages` назван, а не читается — об этом скажет `load.errors`.
            continue
        if path is not None:
            named.append((path.as_posix(), path))

    found: dict[Path, str] = {}
    for label, path in named:
        if path is None or not path.is_file():
            continue
        resolved = path.resolve()
        if resolved.is_relative_to(top):
            found.setdefault(resolved, label)
    return sorted(((label, path) for path, label in found.items()), key=lambda pair: pair[0])


def _shallow_commits(root: Path) -> set[str]:
    """Граница неглубокого клона: коммиты, родителей которых в клоне нет."""
    probe = _git(root, "rev-parse", "--is-shallow-repository")
    if probe.stdout.strip() != "true":
        return set()
    where = _git(root, "rev-parse", "--git-path", "shallow").stdout.strip()
    path = Path(where) if Path(where).is_absolute() else root / where
    try:
        return set(path.read_text(encoding="utf-8").split())
    except OSError:
        return set()


def _base(root: Path, since: str | None, files: list[tuple[str, Path]]) -> str | None:
    """Коммит базы: `--since` или последний коммит, затронувший файлы настройки.

    `None` — файлы настройки не коммитились (или коммитов нет вовсе).

    **Неглубокий клон врёт молча.** Коммит на границе клона для git —
    корневой: он «добавляет» все файлы, и `git log -1 -- <настройка>`
    вернёт его, когда настройка менялась намного раньше. База съехала бы
    вперёд, и новый код оказался бы старым. Поэтому база на границе — отказ
    с подсказкой `--since`, а явная ревизия проверяется только на наличие.
    """
    if since is not None:
        # `--end-of-options`: ревизия вида `-x` — не флаг, а имя, которого нет.
        revision = since + "^{commit}"
        proc = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", revision)
        if proc.returncode != 0 or not proc.stdout.strip():
            raise HistoryError(
                f"ревизия --since {since!r} не найдена: опечатка, или в неглубоком клоне "
                "её нет (git fetch --unshallow или --deepen)"
            )
        return proc.stdout.strip()
    if not files:
        return None
    if _git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        return None
    proc = _git(root, "log", "-1", "--format=%H", "--", *(str(path) for _, path in files))
    if proc.returncode != 0:
        raise HistoryError(f"история настройки не читается: {_error(proc)}")
    base = proc.stdout.strip() or None
    if base is not None and base in _shallow_commits(root):
        raise HistoryError(
            f"база ревью не найдена: последний коммит настройки {base[:SHORT_HASH]} — граница "
            "неглубокого клона, и настройка могла меняться раньше, чего git здесь не видит. "
            "Назови базу явно: --since REV (или git fetch --unshallow)"
        )
    return base


def _dirty(root: Path, files: list[tuple[str, Path]]) -> list[str]:
    """Подписи файлов настройки, изменённых и не закоммиченных (неотслеживаемые — тоже)."""
    changed: list[str] = []
    for label, path in files:
        proc = _git(root, "status", "--porcelain", "--untracked-files=all", "--", str(path))
        if proc.stdout.strip():
            changed.append(label)
    return changed


def _changed(root: Path, base: str) -> list[str]:
    """Изменённые после базы файлы под `--root`: коммиты, рабочее дерево, неотслеживаемые.

    `--relative` — пути от `--root` и только под ним (корень может быть
    подкаталогом репозитория). `--no-renames` и `--diff-filter=d`:
    переименование — это новый файл (путь входит в предикаты, и решение
    могло смениться вместе с ним), удалённого файла в новом коде нет.
    """
    diff = _git(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "--relative",
        "--diff-filter=d",
        "-z",
        base,
        "--",
    )
    if diff.returncode != 0:
        raise HistoryError(f"git diff от базы {base[:SHORT_HASH]} не выполнился: {_error(diff)}")
    others = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if others.returncode != 0:
        raise HistoryError(f"неотслеживаемые файлы не читаются: {_error(others)}")
    return sorted(set(_entries(diff.stdout)) | set(_entries(others.stdout)))


def _submodules(root: Path) -> list[str]:
    """Подмодули git под `--root`: запись индекса с режимом 160000."""
    proc = _git(root, "ls-files", "--stage", "-z")
    found: list[str] = []
    for entry in _entries(proc.stdout):
        meta, _, path = entry.partition("\t")
        if meta.startswith("160000 "):
            found.append(path)
    return sorted(found)


def _relative(path: Path, root: Path) -> str | None:
    """Путь от `--root` в POSIX; вне корня — `None`."""
    resolved, base = path.resolve(), root.resolve()
    return resolved.relative_to(base).as_posix() if resolved.is_relative_to(base) else None


def _history(ctx: SetupContext, since: str | None) -> _History:
    """База, новый код и состояние файлов настройки — всё, что нужно от git.

    До прогонов: опечатка в `--since` или репозиторий без git не должны
    стоить разбора.
    """
    root = ctx.root
    top = _toplevel(root)
    files = _config_files(ctx, top)
    base = _base(root, since, files)
    changes = _dirty(root, files)
    submodules = _submodules(root)
    if base is None:
        return _History(None, files, changes, [], submodules)

    # Файлы настройки — не код: их правка видна `config_dirty`. Кэш разбора
    # под `--root` пишут сами команды `setup`, и без `.gitignore` на него
    # тысяча его файлов встала бы «новым кодом».
    skipped = {rel for _, path in files if (rel := _relative(path, root)) is not None}
    cache = ctx.settings.cache_dir.strip("/")
    new_files = [
        path
        for path in _changed(root, base)
        if path not in skipped
        and path not in submodules
        and not (cache and (path == cache or path.startswith(cache + "/")))
    ]
    return _History(base, files, changes, new_files, submodules)


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def _scopes(ctx: SetupContext) -> list[list[tuple[str, ...]] | None]:
    return [normalize_scope(ctx.settings.roots), normalize_scope(ctx.settings.web.root_paths)]


def _in_area(path: str, scopes: list[list[tuple[str, ...]] | None]) -> bool:
    return any(in_scope(path, scope) for scope in scopes)


def _touches_area(directory: str, scopes: list[list[tuple[str, ...]] | None]) -> bool:
    """Подмодуль в области: внутри корня или корень внутри него."""
    segments = tuple(directory.split("/"))
    for scope in scopes:
        if in_scope(directory, scope):
            return True
        if scope is not None and any(prefix[: len(segments)] == segments for prefix in scope):
            return True
    return False


def _page(items: list[str], limit: int) -> list[str]:
    return items if limit == 0 else items[:limit]


def _notes(ctx: SetupContext, history: _History, defects: int) -> list[Note]:
    """Пометки в порядке важности: базы нет, подмодули в области, дефекты прогона."""
    notes: list[Note] = []
    if history.base is None:
        if not history.config_files:
            notes.append(
                Note(
                    code=NOTE_OUTSIDE,
                    message="ни одного файла настройки нет в этом репозитории: базы ревью нет, "
                    "назови её --since REV; ниже — отчёт setup status целиком",
                )
            )
        else:
            notes.append(
                Note(
                    code=NOTE_UNCOMMITTED,
                    message="настройка ещё не закоммичена: базы ревью нет, ниже — отчёт "
                    "setup status целиком. Коммит настройки — это принятие решений, "
                    "и следующее ревью считает новый код от него",
                )
            )
    scopes = _scopes(ctx)
    inside = [path for path in history.submodules if _touches_area(path, scopes)]
    if inside:
        notes.append(
            Note(
                code=NOTE_SUBMODULES,
                message="область лежит в подмодуле git, а git diff родителя видит у подмодуля "
                "только смену ссылки: новый код внутри ревью не видит — " + ", ".join(inside),
            )
        )
    if defects and history.base is not None:
        notes.append(
            Note(
                code=NOTE_DEFECTS,
                message=f"в области {plural(defects, 'дефект', 'дефекта', 'дефектов')}: прогон "
                "с дефектом даёт неполный охват и неполные находки, и ревью по нему неполно — "
                "подробности в setup status",
            )
        )
    return notes


# --------------------------------------------------------------------------------------
# Записи без охвата: мёртвые, неприменимые, удержанные
# --------------------------------------------------------------------------------------


class _Idle:
    """Разбор записей без охвата на три списка ревью.

    Прогоны и наборы правил — из контекста: `status_detail` их уже собрал,
    и `cached_property` отдаёт готовое. Запись шага, который не собрался,
    в охват не попала вовсе (`status._coverage`), поэтому здесь каждое
    обращение к прогону — к прогону, который есть.
    """

    def __init__(self, ctx: SetupContext) -> None:
        self._ctx = ctx
        self._no_front: bool | None = None

    def no_front(self) -> bool:
        """Фронта нет: шаг `web` без модулей **и** `web.roots` не перечисляет каталогов.

        «Фронта нет» и «фронт исчез» выглядят одинаково — пустой шаг `web`.
        Различает их только `web.roots`: явный непустой список без модулей —
        решение, которое перестало работать, и его записи остаются мёртвыми.
        Ключ не задан (умолчание `["."]`) или задан `[]` — каталога фронта
        человек не называл, и записям фронта нечего решать.
        """
        if self._no_front is None:
            web = self._ctx.settings.web
            named = "roots" in web.model_fields_set and bool(web.root_paths)
            try:
                self._no_front = not named and not self._ctx.web.manifest.modules
            except InputError:
                # Шаг `web` не собрался — о нём `load.errors`, а «фронта нет»
                # неизвестно: записи остаются мёртвыми, ревью не зеленеет молча.
                self._no_front = False
        return self._no_front

    def rule(self, key: str, value: str) -> ExcludeRule | None:
        """Правило отсева набора по ключу охвата и `id`; не правило отсева — `None`."""
        section = EXCLUSION_KEYS.get(key)
        if section is None:
            return None
        ruleset = self._ctx.ruleset if section == "dotnet" else self._ctx.web_ruleset
        return next((rule for rule in ruleset.exclude.rules if rule.id == value), None)

    def under_exclude(self, key: str, rule: ExcludeRule) -> list[str]:
        """Шаблоны `exclude`, отсёкшие все файлы, которые правило отсева решило бы.

        Решают файлы, которые есть, а не сравнение глобов: «шаблон `exclude`
        поглощает глоб правила» в общем виде не решается (`**` проходит
        через `/`, `matches_glob`). Правило — под `exclude`, когда его
        `path_glob` совпал хотя бы с одним отсечённым файлом и ни с одним
        файлом, который шаг читает. Оба множества — файлы языка шага под его
        корнями: `ctx.discovered` обходит весь `--root` без `roots`, а файл
        вне корней шаг не прочёл бы и без `exclude`. Пусто — не под `exclude`.
        """
        globs = condition_values(rule.when, "path_glob")
        if not globs:
            return []
        ctx, found = self._ctx, self._ctx.discovered
        dotnet = EXCLUSION_KEYS[key] == "dotnet"
        field = "cs_files" if dotnet else "ts_files"
        scope = normalize_scope(ctx.settings.roots if dotnet else ctx.settings.web.root_paths)

        def decided(path: str) -> bool:
            return in_scope(path, scope) and any(matches_glob(path, glob) for glob in globs)

        read: list[str] = found.cs_files if dotnet else found.ts_files
        if any(decided(path) for path in read):
            return []
        return sorted(
            {
                pattern
                for path, patterns in (found.excluded or {}).items()
                if file_field(path.rsplit("/", 1)[-1]) == field and decided(path)
                for pattern in patterns
            }
        )


def _idle(
    ctx: SetupContext, coverage: list[CoverageDetail]
) -> tuple[list[DeadDecision], list[InapplicableDecision], list[KeptDecision]]:
    """Записи без охвата — на мёртвые, неприменимые по построению и удержанные с причиной.

    Порядок проверок: причина человека (`unused_reason`) — первой, это его
    слово; затем переключатель, «фронта нет», «под `exclude`»; что осталось —
    мёртвое, с адресом. Правила классификации (`NOT_DEAD_KEYS`) не идут
    никуда: это запас набора.
    """
    idle = _Idle(ctx)
    dead: list[DeadDecision] = []
    inapplicable: list[InapplicableDecision] = []
    kept: list[KeptDecision] = []
    for item in coverage:
        if item.count or item.key in NOT_DEAD_KEYS:
            continue
        fields: dict[str, Any] = {
            "id": item.id,
            "file": item.file,
            "key": item.key,
            "value": item.value,
            "reason": item.reason,
        }
        rule = idle.rule(item.key, item.value)
        if rule is not None and rule.unused_reason:
            kept.append(KeptDecision(**fields, unused_reason=rule.unused_reason))
        elif item.key in EXCLUSION_KEYS and item.value == REQUIRE_PUBLIC.id:
            inapplicable.append(InapplicableDecision(**fields, why=WHY_SWITCH, detail=[]))
        elif (item.key.startswith("web.") or item.key in FRONT_LINK_KEYS) and idle.no_front():
            inapplicable.append(InapplicableDecision(**fields, why=WHY_NO_FRONT, detail=[]))
        elif rule is not None and (cut := idle.under_exclude(item.key, rule)):
            inapplicable.append(InapplicableDecision(**fields, why=WHY_UNDER_EXCLUDE, detail=cut))
        else:
            # Краткая форма `exclude` (`exclude.path_glob`…) места для причины
            # не имеет: её запись правят или удаляют.
            short = item.value.startswith(RESERVED_PREFIX)
            keep = KEEP_KEYS.get(item.key) if rule is not None and not short else None
            dead.append(DeadDecision(**fields, keep_key=keep))
    return (
        sorted(dead, key=lambda entry: entry.id),
        sorted(inapplicable, key=lambda entry: entry.id),
        sorted(kept, key=lambda entry: entry.id),
    )


def build_review(
    ctx: SetupContext, *, since: str | None = None, limit: int = DEFAULT_LIMIT
) -> Review:
    """Что появилось после коммита настройки и как прежние решения обошлись с этим кодом.

    `since` — ревизия базы; `None` — последний коммит файлов настройки.
    `limit` — новых файлов, файлов у решения и примеров у находки; `0` — все.
    Не git, ревизии нет, база на границе неглубокого клона — `HistoryError`.
    Без базы (настройка не коммитилась) — отчёт `setup status` в поле
    `status` с пометкой, разделы ревью пусты.
    """
    if limit < 0:
        raise InputError("limit не бывает отрицательным")
    history = _history(ctx, since)
    labels = [label for label, _ in history.config_files]

    if history.base is None:
        status = build_status(ctx, limit=limit)
        return Review(
            base=None,
            since=since,
            config_files=labels,
            config_dirty=bool(history.config_changes),
            config_changes=history.config_changes,
            new_files=[],
            new_files_total=0,
            outside_area=[],
            outside_area_total=0,
            applied=[],
            new_findings=[],
            dead_decisions=[],
            inapplicable=[],
            kept_unused=[],
            unexplained=status.unexplained,
            defects=status.defects,
            notes=_notes(ctx, history, status.defects),
            status=status,
        )

    detail = status_detail(ctx, limit=limit)
    new = set(history.new_files)
    scopes = _scopes(ctx)
    outside = [path for path in history.new_files if not _in_area(path, scopes)]

    applied: list[AppliedDecision] = []
    for item in detail.coverage:
        hits = {path: count for path, count in item.files.items() if path in new}
        if not hits:
            continue
        files = sorted(hits)
        applied.append(
            AppliedDecision(
                id=item.id,
                file=item.file,
                key=item.key,
                value=item.value,
                reason=item.reason,
                count=sum(hits.values()),
                total=item.count,
                files=_page(files, limit),
                files_total=len(files),
            )
        )
    # Сверху — записи «не берём», забравшие новый код, затем — по числу нового кода.
    applied.sort(key=lambda item: (not is_refusal(item.key), -item.count, item.id))

    new_findings: list[NewFinding] = []
    for finding in detail.status.findings:
        places = [
            place.example
            for place in detail.places.get(finding.code, ())
            if new.intersection(place.files)
        ]
        if places:
            new_findings.append(
                NewFinding(
                    code=finding.code,
                    category=finding.category,
                    title=finding.title,
                    decision_home=finding.decision_home,
                    count=finding.count,
                    places=len(places),
                    examples=_page(places, limit),
                )
            )

    dead, inapplicable, kept = _idle(ctx, detail.coverage)
    status = detail.status
    return Review(
        base=history.base,
        since=since,
        config_files=labels,
        config_dirty=bool(history.config_changes),
        config_changes=history.config_changes,
        new_files=_page(history.new_files, limit),
        new_files_total=len(history.new_files),
        outside_area=_page(outside, limit),
        outside_area_total=len(outside),
        applied=applied,
        new_findings=new_findings,
        dead_decisions=dead,
        inapplicable=inapplicable,
        kept_unused=kept,
        unexplained=status.unexplained,
        defects=status.defects,
        notes=_notes(ctx, history, status.defects),
        status=None,
    )


def has_changes(review: Review) -> bool:
    """Есть ли что проверить человеку — код 1 у `--fail-on-changes`.

    Новые находки, решения без охвата и дефекты: ревью по прогону, который
    не собрался, неполно, и «зелёный» ответ по нему был бы молчанием.
    Без базы — любая находка или дефект `setup status`: не принято ничего.
    `inapplicable` и `kept_unused` не смотрит (S35): записи, которым нечего
    решать по построению, и записи, которые человек держит с причиной, —
    не работа, и с ними ревью нейтрального набора было бы красным всегда.
    """
    if review.status is not None:
        return bool(review.status.unexplained or review.status.defects)
    return bool(review.new_findings or review.dead_decisions or review.defects)


def review_json(review: Review) -> str:
    return stable_json_dumps(review.model_dump(mode="json"))


# --------------------------------------------------------------------------------------
# Печать
# --------------------------------------------------------------------------------------


def _more(shown: int, total: int) -> str:
    return f" … и ещё {total - shown}" if total > shown else ""


def _sentence(message: str) -> str:
    """Пометка строкой текста: с прописной и точкой. В JSON — как есть."""
    return message[:1].upper() + message[1:] + "."


def format_review(review: Review) -> str:
    """Текст для человека: база, состояние настройки, затем три раздела ревью.

    Без базы — пометка и отчёт `setup status` его же печатью.
    """
    lines: list[str] = []
    if review.status is not None:
        lines += [_sentence(note.message) for note in review.notes]
        if review.config_dirty:
            lines.append(f"Не закоммичено: {', '.join(review.config_changes)}.")
        return "\n".join(lines) + "\n\n" + format_status(review.status)
    base = review.base or ""

    origin = f"--since {review.since}" if review.since is not None else "последний коммит настройки"
    lines.append(
        f"Ревью от {base[:SHORT_HASH]} ({origin}): новых файлов {review.new_files_total}, "
        f"из них вне roots/web.roots {review.outside_area_total}; применилось решений "
        f"{len(review.applied)}, новых находок {len(review.new_findings)}, решений без охвата "
        f"{len(review.dead_decisions)}."
    )
    if review.config_files:
        lines.append(f"Файлы настройки: {', '.join(review.config_files)}.")
    if review.config_dirty:
        lines.append(
            f"Настройка изменена и не закоммичена: {', '.join(review.config_changes)} — "
            "ревью сравнивает код, а несохранённые решения ещё не решения."
        )
    lines += [_sentence(note.message) for note in review.notes]

    if review.applied:
        lines += ["", "Решения, применившиеся к новому коду:"]
        for item in review.applied:
            files = ", ".join(item.files) + _more(len(item.files), item.files_total)
            lines.append(f"  {item.id} — {item.count} (всего {item.total}): {files}")
            if item.reason:
                lines.append(f"    причина: {item.reason}")
    if review.new_findings:
        lines += ["", "Новые находки (места в новых файлах):"]
        for finding in review.new_findings:
            mark = "ДЕФЕКТ: " if finding.category == "defect" else ""
            lines.append(
                f"  {mark}{finding.title} ({finding.code}): {finding.places} "
                f"(всего у находки {finding.count})"
            )
            if finding.decision_home != "—":
                lines.append(f"    решение: {finding.decision_home}")
            lines += [f"    {example}" for example in finding.examples]
            more = _more(len(finding.examples), finding.places)
            if more:
                lines.append(f"   {more}")
    if review.dead_decisions:
        lines += ["", "Решения без охвата во всём репозитории (удалить или держать с причиной):"]
        for dead in review.dead_decisions:
            lines.append(f"  {dead.id}" + (f" — {dead.reason}" if dead.reason else ""))
            address = f"    удалить запись: {dead.file} → {dead.key}, {dead.value}"
            if dead.keep_key is not None:
                address += f"; или держать с причиной: {dead.keep_key}"
            lines.append(address)
    # Не работа, а сведения: по строке с числом, без вопросов (S35, п. 6).
    idle: list[str] = []
    if review.inapplicable:
        why = Counter(item.why for item in review.inapplicable)
        idle.append(
            f"Неприменимо по построению: {len(review.inapplicable)} (фронта нет — "
            f"{why[WHY_NO_FRONT]}, под `exclude` — {why[WHY_UNDER_EXCLUDE]}, "
            f"переключатель — {why[WHY_SWITCH]})."
        )
    if review.kept_unused:
        idle.append(f"Держатся без охвата с причиной (`unused_reason`): {len(review.kept_unused)}.")
    if idle:
        lines += ["", *idle]

    shown = review.new_files
    if shown:
        lines += ["", f"Новые файлы ({review.new_files_total}):"]
        lines += [f"  {path}" for path in shown]
        if review.new_files_total > len(shown):
            lines.append(f"  … и ещё {review.new_files_total - len(shown)} (--limit 0)")
    else:
        lines += ["", "Нового кода после базы нет."]
    if review.outside_area:
        lines.append("Из них вне roots/web.roots (обход их не читает, решения о них есть):")
        lines += [f"  {path}" for path in review.outside_area]
    return "\n".join(lines) + "\n"


__all__ = [
    "EXCLUSION_KEYS",
    "FRONT_LINK_KEYS",
    "KEEP_KEYS",
    "NOTE_DEFECTS",
    "NOTE_OUTSIDE",
    "NOTE_SUBMODULES",
    "NOTE_UNCOMMITTED",
    "NOT_DEAD_KEYS",
    "WHY_NO_FRONT",
    "WHY_SWITCH",
    "WHY_UNDER_EXCLUDE",
    "AppliedDecision",
    "DeadDecision",
    "HistoryError",
    "InapplicableDecision",
    "KeptDecision",
    "NewFinding",
    "Note",
    "Review",
    "build_review",
    "format_review",
    "has_changes",
    "review_json",
]
