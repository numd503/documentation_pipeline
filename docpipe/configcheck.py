"""Проверка настройки: во что разрешается каждый путь `docpipe.yaml` и что из этого есть.

Пути конфигурации отсчитываются от трёх разных баз, и по имени ключа базу
не угадать: входы ищутся от текущего каталога, затем от каталога
`docpipe.yaml`; цели записи — только от текущего; корни обхода, каталоги
документов и кэша — от `--root`. Пока проверить это было нечем, половина
настроечных проблем выглядела как «инструмент не видит документы»
и разбиралась на полном прогоне.

Логика живёт здесь, а не в `cli.py`, потому что у отчёта два потребителя:
команда `docpipe config check` и инструмент сервера настройки (S27 плана
настройки). Копия проверки в CLI и в сервере разошлась бы на первом же
новом ключе — ровно так однажды разошлись три копии входа шага 2.

Отчёт — ответ агенту, а не артефакт: в нём абсолютные пути, разрешённые
от `cwd` и `--root` этой машины, и сравнивать его между машинами незачем.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.config import DocpipeConfig, candidate_inputs

# 1.1 — код проблемы `placeholder-left` (S08 плана настройки).
# 1.2 — код `input-shadowed` и поле `shadowed` у входов (S36).
SCHEMA_VERSION: Final = "1.2"

ProblemCode = Literal[
    "placeholder-left",
    "input-missing",
    "input-shadowed",
    "root-missing",
    "adapter-input-missing",
    "engine-missing",
]
AdapterOption = Literal["spec", "path"]
AdapterBase = Literal["config", "root"]

# Ключи и их база отсчёта. Список положительный и жёсткий: ключ, забытый здесь,
# сделает отчёт неполным, а неполный отчёт хуже отсутствующего — по нему решат,
# что настройка проверена. Порядок задан константой, а не данными, и в отчёте
# держится он же: так ключи читаются группами, как в `docpipe.yaml`.
INPUT_KEYS: Final[tuple[str, ...]] = (
    "rules",
    "templates",
    "ownership",
    "registries",
    "arch",
    "web.rules",
    "web.pages",
)
# Входы, которые читаются как файл, а не как любой существующий путь.
# `pages.yaml` ищет `web/overrides.configured_pages` предикатом `is_file()`:
# каталог `pages.yaml/` в текущем каталоге чтение пропускает и берёт файл
# рядом с конфигурацией. Отчёт, проверяющий `exists()`, назвал бы найденным
# не тот кандидат, который прочтёт прогон. Остальные входы читаются через
# `resolve_input` — предикатом `exists()`, и `templates` из них — каталог.
FILE_INPUTS: Final[frozenset[str]] = frozenset({"web.pages"})
TARGET_KEYS: Final[tuple[str, ...]] = (
    "out",
    "worklist",
    "web.out",
    "web.link_out",
    "graph.out",
    "graph.cache_dir",
)
# `modules_dir` и `web.modules_dir` сюда не входят: они не пути, а сегменты
# внутри `docs_root`, и показанные склейкой с корнем читались бы как каталоги,
# которых нет. Собранные из них префиксы идут отдельными полями отчёта.
ROOT_KEYS: Final[tuple[str, ...]] = (
    "docs_root",
    "business_root",
    "cache_dir",
)
# Какой параметр адаптера называет файл и от чего он отсчитывается. Базы две,
# и по имени параметра их не угадать — ради этого поле `base` и есть в отчёте:
# описание реестров (`spec`) лежит рядом с настройкой и ищется двумя ступенями
# `resolve_input`, как его читает `arch/adapters/declared.py`; модуль Python
# (`path`) лежит в дереве исходников и отсчитывается от `--root`
# (`arch/adapters/code.py`). Новый адаптер обязан появиться здесь — это сверяет
# тест с `ADAPTERS`, иначе его вход останется без проверки молча.
ADAPTER_INPUTS: Final[dict[str, tuple[AdapterOption, AdapterBase]]] = {
    "registries": ("spec", "config"),
    "python_code": ("path", "root"),
}

# Плейсхолдер установщика: `@CONFIG_DIR@`, `@CACHE_DIR@`, `@ENGINE@`. Имя —
# прописными, как у подстановок autoconf и `install.sh`: пара `@` вокруг
# строчного слова в пути или маршруте законна, а прописное имя в такой
# рамке — только недоделанная подстановка.
PLACEHOLDER: Final = re.compile(r"@[A-Z][A-Z0-9_]*@")

# Порядок проблем — порядок разделов отчёта, чтобы сводка читалась сверху вниз
# так же, как сам отчёт. Плейсхолдер — первым: он причина, а «не найден»
# и «нет движка» у того же ключа — её следствия.
_PROBLEM_ORDER: Final[tuple[ProblemCode, ...]] = (
    "placeholder-left",
    "input-missing",
    "input-shadowed",
    "root-missing",
    "adapter-input-missing",
    "engine-missing",
)

_STEP_TEXT: Final[dict[str, str]] = {
    "cwd": "от текущего каталога",
    "config": "рядом с конфигурацией",
}
_BASE_TEXT: Final[dict[str, str]] = {
    "config": "от текущего каталога, затем от каталога конфигурации",
    "root": "от корня репозитория",
}
_SHADOWED_TEXT: Final = "рядом с конфигурацией лежит другой, его прогон не прочтёт"
_INPUT_HINT: Final = (
    "Путь входа пишут относительно каталога конфигурации — тогда он "
    "не зависит от того, откуда зовут команду."
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputCheck(_Frozen):
    """Вход инструмента: файл, который команда читает.

    `candidates` и `found` — пути так, как их увидит команда, то есть
    относительно `cwd` отчёта: `found` — всегда один из кандидатов, и отказ
    называет первого, потому что его человек и написал. Пустой `value` —
    ключ не задан, кандидатов нет.

    `shadowed` — второй кандидат, когда есть оба и это разные пути: прогон
    возьмёт первый (`step: cwd`), а одноимённый вход рядом с конфигурацией
    останется непрочитанным. Записан так же, как `found`.
    """

    key: str
    value: str
    candidates: list[str]
    found: str | None
    step: Literal["cwd", "config"] | None
    shadowed: str | None = None


class TargetCheck(_Frozen):
    """Цель записи. Второй ступени у неё нет: угадывать, куда писать, нельзя.

    Отсутствие каталога — не проблема: каталоги создают все писатели,
    а умолчание `graph.cache_dir` даёт «каталога нет» на любом свежем
    репозитории.
    """

    key: str
    value: str
    resolved: str
    parent_exists: bool


class RootKeyCheck(_Frozen):
    """Путь от `--root`: каталог документов, бизнес-документов, кэша.

    Отсутствие — тоже не проблема: каталоги заводят те, кто в них пишет.
    """

    key: str
    value: str
    resolved: str
    exists: bool


class RootsCheck(_Frozen):
    """Корень обхода: каталог от `--root`, в котором ищут исходники.

    Здесь отсутствие — проблема: корень, которого нет, молча даёт ноль файлов,
    и «модулей ноль» неотличимо от репозитория без кода.
    """

    key: Literal["roots", "web.roots"]
    entry: str
    resolved: str
    exists: bool


class AdapterInput(_Frozen):
    """Файл, который читает адаптер реестра, и база, от которой он отсчитан.

    Пустой `value` — обязательный параметр не задан: адаптер откажет на сборке.
    `shadowed` — как у `InputCheck`: у базы `config` два кандидата, и второй
    есть, но прочитан будет первый. У базы `root` второй ступени нет.
    """

    adapter_id: str
    option: AdapterOption
    base: AdapterBase
    value: str
    resolved: str
    exists: bool
    shadowed: str | None = None


class EngineCheck(_Frozen):
    """Движок разбора. Не задан — законно для шагов 1, 2 и бизнес-слоя."""

    configured: bool
    path: str
    exists: bool


class ConfigProblem(_Frozen):
    """То, из-за чего прогон не сработает или сработает с правдоподобным нулём."""

    code: ProblemCode
    key: str
    message: str


class ConfigReport(_Frozen):
    schema_version: Literal["1.2"] = SCHEMA_VERSION
    config: str | None
    cwd: str
    root: str
    inputs: list[InputCheck]
    targets: list[TargetCheck]
    root_keys: list[RootKeyCheck]
    roots: list[RootsCheck]
    adapter_inputs: list[AdapterInput]
    engine: EngineCheck
    modules_root: str
    web_modules_root: str | None
    problems: list[ConfigProblem]


def _key_value(settings: DocpipeConfig, key: str) -> object:
    """Значение ключа с одной точкой (`web.rules`).

    Глубже не умеет намеренно: `arch_adapters[]` — список, и его обходят
    отдельно, а не строкой ключа.
    """
    section, _, name = key.partition(".")
    holder: object = settings if not name else getattr(settings, section)
    return getattr(holder, name or section)


def _string_values(value: object, key: str) -> Iterator[tuple[str, str]]:
    """Все строковые значения настройки с путём ключа: `(ключ, значение)`.

    Путь ключа — тот же, каким его называют остальные проблемы: элемент
    списка строк — ключом списка (`docs_scan_exclude`), запись адаптера —
    по `id` (`arch_adapters[regs].options.spec`), прочие записи списков —
    `[]` (`web.url_rewrite[].strip_prefix`). Ключи словарей значениями
    не считаются: в `domains` это глобы, а не пути из подстановки.
    """
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _string_values(item, f"{key}.{name}" if key else str(name))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                ident = item.get("id")
                yield from _string_values(item, f"{key}[{ident if isinstance(ident, str) else ''}]")
            else:
                yield from _string_values(item, key)


def _placeholders(settings: DocpipeConfig) -> list[ConfigProblem]:
    """Незаменённые плейсхолдеры установщика — в любом ключе, не только в путях.

    `@CONFIG_DIR@/artifacts/doc-tree.json` — законное значение цели записи:
    прогон создаст каталог `@CONFIG_DIR@` в текущем и напишет туда, а человек
    будет искать манифест там, где его нет. `@ENGINE@` до этой проверки
    ловился только как «движка нет», и причина — файл поставки позвали
    напрямую, мимо установщика, — не называлась.
    """
    found: list[ConfigProblem] = []
    raw = settings.model_dump(mode="json", by_alias=True)
    for key, value in _string_values(raw, ""):
        names = sorted(set(PLACEHOLDER.findall(value)))
        if not names:
            continue
        found.append(
            ConfigProblem(
                code="placeholder-left",
                key=key,
                message=(
                    f"{key}: {value!r} — незаменённый плейсхолдер {', '.join(names)}. "
                    "Его подставляет установщик; файл из поставки читают только "
                    "после установки"
                ),
            )
        )
    return found


def _present(path: Path, cwd: Path, file_only: bool) -> bool:
    """Есть ли кандидат — тем предикатом, каким его ищет читатель входа.

    Существование — от `cwd` отчёта, а не от текущего каталога процесса:
    сервер настройки зовёт проверку за каталог, из которого будут звать
    команду, и ответ обязан быть тем, что увидит она.
    """
    target = cwd / path
    return target.is_file() if file_only else target.exists()


def _first_existing(candidates: list[Path], cwd: Path, file_only: bool = False) -> Path | None:
    """Кандидат, который выберет читатель входа, запущенный из `cwd`."""
    return next((path for path in candidates if _present(path, cwd, file_only)), None)


def _shadowed(candidates: list[Path], cwd: Path, file_only: bool = False) -> Path | None:
    """Второй кандидат, если есть оба и это разные пути; иначе `None`.

    Порядок ступеней `resolve_input` не меняется (его docstring, `CLAUDE.md`):
    конфигурации в корне писались от текущего каталога. Лечится не порядок,
    а молчание: короткое имя `templates` на abp нашлось собственным каталогом
    продукта, и отчёт ответил `step: cwd` без единой проблемы.

    Одинаковый `resolve()` — один и тот же путь, увиденный дважды: конфигурация
    в корне, названная абсолютным путём, или ссылка на каталог настройки.
    Выбор тут ни на что не влияет, и проблемы нет.
    """
    if len(candidates) < 2:
        return None
    first, second = candidates[0], candidates[1]
    if not (_present(first, cwd, file_only) and _present(second, cwd, file_only)):
        return None
    if (cwd / first).resolve() == (cwd / second).resolve():
        return None
    return second


def _check_input(key: str, value: str, config: Path | None, cwd: Path) -> InputCheck:
    if not value:
        return InputCheck(key=key, value="", candidates=[], found=None, step=None)
    file_only = key in FILE_INPUTS
    candidates = candidate_inputs(value, config)
    found = _first_existing(candidates, cwd, file_only)
    step: Literal["cwd", "config"] | None = None
    if found is not None:
        step = "cwd" if found == candidates[0] else "config"
    shadowed = _shadowed(candidates, cwd, file_only)
    return InputCheck(
        key=key,
        value=value,
        candidates=[str(path) for path in candidates],
        found=None if found is None else str(found),
        step=step,
        shadowed=None if shadowed is None else str(shadowed),
    )


def _from_cwd(path: Path, cwd: Path) -> str:
    """Значение, по которому вход найдёт первая ступень: путь от `cwd`, если он внутри.

    Второй кандидат относителен, когда относителен путь к `docpipe.yaml`
    (`--config docs/docpipe/docpipe.yaml`), и тогда он и есть готовое значение.
    Абсолютный путь к конфигурации даёт абсолютного кандидата; внутри `cwd`
    он переводится в путь от него — абсолютный в настройке привязал бы её
    к этой машине.
    """
    if not path.is_absolute():
        return path.as_posix()
    for base, target in ((cwd, path), (cwd.resolve(), path.resolve())):
        try:
            return target.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _shadow_message(key: str, value: str, shadowed: str, cwd: Path) -> str:
    """Текст `input-shadowed`: что выбрал прогон, что осталось и чего стоит починка.

    Переносимой записи, которая обошла бы первую ступень, нет: короткое имя
    находит от текущего каталога, путь от корня продукта — тоже. Путь от корня
    не переезжает вместе с каталогом настройки — это цена, и в тексте она
    не прячется.
    """
    fix = _from_cwd(Path(shadowed), cwd)
    return (
        f"{key}: короткое имя нашлось от текущего каталога (`{value}` — в каталоге "
        f"продукта), а рядом с docpipe.yaml лежит одноимённый `{shadowed}`; прогон "
        "возьмёт первый. Если нужен второй — напишите путь от корня продукта "
        f"(`{fix}`): так его найдёт первая ступень, но вместе с каталогом настройки "
        "он уже не переедет"
    )


def _check_adapter(
    adapter_id: str,
    adapter: str,
    options: dict[str, object],
    config: Path | None,
    cwd: Path,
    root: Path,
) -> AdapterInput | None:
    """Вход адаптера; `None` — у адаптера нет файла на входе или имя неизвестно.

    Неизвестное имя здесь не проверяется: путь его не касается, а сборка
    отказывает по нему сама, с перечнем известных адаптеров.
    """
    known = ADAPTER_INPUTS.get(adapter)
    if known is None:
        return None
    option, base = known
    raw = options.get(option)
    value = "" if raw in (None, "") else str(raw)
    if not value:
        return AdapterInput(
            adapter_id=adapter_id,
            option=option,
            base=base,
            value="",
            resolved="",
            exists=False,
        )
    shadowed: Path | None = None
    if base == "config":
        # Тем же предикатом, что у `resolve_input`, через который файл читает
        # адаптер (`arch/collect.collect_configured`): `exists()`.
        candidates = candidate_inputs(value, config)
        chosen = _first_existing(candidates, cwd) or candidates[0]
        resolved = (cwd / chosen).resolve()
        shadowed = _shadowed(candidates, cwd)
    else:
        resolved = (root / value).resolve()
    return AdapterInput(
        adapter_id=adapter_id,
        option=option,
        base=base,
        value=value,
        resolved=str(resolved),
        exists=resolved.exists(),
        shadowed=None if shadowed is None else str(shadowed),
    )


def check_config(
    settings: DocpipeConfig, config: Path | None, root: Path, cwd: Path
) -> ConfigReport:
    """Разрешить каждый путь настройки так, как его разрешит команда из `cwd` с `--root`.

    Проблема — только то, что действительно ломает прогон: незаменённый
    плейсхолдер установщика, ненайденный вход, вход, нашедшийся от текущего
    каталога при одноимённом рядом с конфигурацией, корень обхода без каталога,
    вход адаптера без файла, названный, но отсутствующий движок. Каталог цели
    записи, которого ещё нет, проблемой не считается: его создаст первый прогон.

    Значение с плейсхолдером даёт одну проблему — `placeholder-left`, а не
    ещё и «не найден» или «движка нет»: то следствия, и чинится всё одной
    подстановкой. Разрешение такого пути в разделах отчёта остаётся.
    """
    absolute_root = (cwd / root).resolve()
    problems: list[ConfigProblem] = _placeholders(settings)

    def placeholder(value: str) -> bool:
        return PLACEHOLDER.search(value) is not None

    inputs = [
        _check_input(key, str(_key_value(settings, key) or ""), config, cwd) for key in INPUT_KEYS
    ]
    for item in inputs:
        if item.value and item.found is None and not placeholder(item.value):
            problems.append(
                ConfigProblem(
                    code="input-missing",
                    key=item.key,
                    message=(
                        f"{item.key}: {item.value!r} не найден; искали: "
                        + ", ".join(item.candidates)
                    ),
                )
            )
        if item.shadowed is not None and not placeholder(item.value):
            problems.append(
                ConfigProblem(
                    code="input-shadowed",
                    key=item.key,
                    message=_shadow_message(item.key, item.value, item.shadowed, cwd),
                )
            )

    targets = []
    for key in TARGET_KEYS:
        value = str(_key_value(settings, key))
        resolved = (cwd / value).resolve()
        targets.append(
            TargetCheck(
                key=key,
                value=value,
                resolved=str(resolved),
                parent_exists=resolved.parent.is_dir(),
            )
        )

    root_keys = []
    for key in ROOT_KEYS:
        value = str(_key_value(settings, key))
        # Абсолютное значение выигрывает склейку — и для `cache_dir` это
        # законно и намеренно. Показать надо результат, а не слагаемые.
        resolved = (absolute_root / value).resolve()
        root_keys.append(
            RootKeyCheck(key=key, value=value, resolved=str(resolved), exists=resolved.is_dir())
        )

    roots = []
    sources: tuple[tuple[Literal["roots", "web.roots"], list[str]], ...] = (
        ("roots", settings.roots),
        ("web.roots", settings.web.root_paths),
    )
    for roots_key, entries in sources:
        for entry in sorted(entries):
            # `.` нормализуется в пустую строку; показать её пустой значило бы
            # заставить гадать, что имелось в виду.
            shown = entry or "."
            resolved = (absolute_root / shown).resolve()
            check = RootsCheck(
                key=roots_key, entry=shown, resolved=str(resolved), exists=resolved.is_dir()
            )
            roots.append(check)
            if not check.exists and not placeholder(shown):
                problems.append(
                    ConfigProblem(
                        code="root-missing",
                        key=roots_key,
                        message=(
                            f"{roots_key}: каталога {shown!r} нет ({resolved}); "
                            "обход не найдёт в нём ни одного файла и не скажет об этом"
                        ),
                    )
                )

    adapter_inputs = []
    for adapter in sorted(settings.arch_adapters, key=lambda item: item.id):
        checked = _check_adapter(
            adapter.id, adapter.adapter, dict(adapter.options), config, cwd, absolute_root
        )
        if checked is None:
            continue
        adapter_inputs.append(checked)
        if placeholder(checked.value):
            continue
        key = f"arch_adapters[{checked.adapter_id}].options.{checked.option}"
        if checked.shadowed is not None:
            problems.append(
                ConfigProblem(
                    code="input-shadowed",
                    key=key,
                    message=_shadow_message(key, checked.value, checked.shadowed, cwd),
                )
            )
        if checked.exists:
            continue
        if not checked.value:
            message = f"{key}: обязательный параметр не задан; адаптер откажет на сборке"
        else:
            message = (
                f"{key}: {checked.value!r} не найден ({_BASE_TEXT[checked.base]}): "
                f"{checked.resolved}"
            )
        problems.append(ConfigProblem(code="adapter-input-missing", key=key, message=message))

    engine = _check_engine(settings, cwd)
    if engine.configured and not engine.exists and not placeholder(settings.graph.engine_path):
        problems.append(
            ConfigProblem(
                code="engine-missing",
                key="graph.engine_path",
                message=(
                    f"graph.engine_path: файла {engine.path} нет; команды `graph *` "
                    "откажутся работать"
                ),
            )
        )

    return ConfigReport(
        config=None if config is None else str(config),
        cwd=str(cwd),
        root=str(absolute_root),
        inputs=inputs,
        targets=targets,
        root_keys=root_keys,
        roots=roots,
        adapter_inputs=adapter_inputs,
        engine=engine,
        modules_root=settings.modules_root,
        web_modules_root=settings.web_modules_root if settings.web.modules_dir else None,
        problems=sorted(
            problems, key=lambda item: (_PROBLEM_ORDER.index(item.code), item.key, item.message)
        ),
    )


def _check_engine(settings: DocpipeConfig, cwd: Path) -> EngineCheck:
    if not settings.graph.engine_path:
        return EngineCheck(configured=False, path="", exists=False)
    # Путь показывается как написан, без разворота ссылок: мост запускает
    # и сверяет чек-сумму именно по нему, и человек искать будет его же.
    engine = cwd / Path(settings.graph.engine_path).expanduser()
    return EngineCheck(configured=True, path=str(engine), exists=engine.is_file())


# --------------------------------------------------------------------------------------
# Текст
# --------------------------------------------------------------------------------------


def format_report(report: ConfigReport) -> str:
    """Отчёт для человека; сводка проблем — отдельно, `format_problems`.

    Формулировки держатся прежние: на них опираются тесты и установщик,
    который советует звать проверку первой.
    """
    lines = [
        f"Конфигурация: {report.config or 'не задана — действуют умолчания'}",
        f"Текущий каталог: {report.cwd}",
        f"Корень (--root): {report.root}",
        "",
        "Входы — от текущего каталога, затем от каталога конфигурации:",
    ]
    for item in report.inputs:
        if not item.value:
            lines.append(f"  {item.key:16} не задан")
        elif item.found is None or item.step is None:
            lines.append(f"  {item.key:16} {item.value}  → НЕ НАЙДЕН: {item.candidates[0]}")
        else:
            line = f"  {item.key:16} {item.value}  → {item.found} ({_STEP_TEXT[item.step]})"
            if item.shadowed is not None:
                line += f"; {_SHADOWED_TEXT}: {item.shadowed}"
            lines.append(line)

    lines += ["", "Цели записи — только от текущего каталога:"]
    for target in report.targets:
        state = "каталог есть" if target.parent_exists else "каталога пока нет, создаст прогон"
        lines.append(f"  {target.key:16} {target.value}  → {target.resolved} ({state})")

    lines += ["", "От корня репозитория:"]
    lines += [f"  {item.key:16} {item.value}  → {item.resolved}" for item in report.root_keys]

    lines += ["", "Корни обхода — каталоги от корня репозитория:"]
    for root in report.roots:
        state = "есть" if root.exists else "КАТАЛОГА НЕТ"
        lines.append(f"  {root.key:16} {root.entry}  → {root.resolved} ({state})")

    if report.adapter_inputs:
        lines += ["", "Входы адаптеров (`arch_adapters`):"]
        for adapter in report.adapter_inputs:
            name = f"{adapter.adapter_id}.{adapter.option}"
            if not adapter.value:
                lines.append(f"  {name:16} не задан")
                continue
            state = "есть" if adapter.exists else "НЕ НАЙДЕН"
            line = (
                f"  {name:16} {adapter.value}  → {adapter.resolved}"
                f" ({_BASE_TEXT[adapter.base]}; {state})"
            )
            if adapter.shadowed is not None:
                line += f"; {_SHADOWED_TEXT}: {adapter.shadowed}"
            lines.append(line)

    lines.append("")
    if report.engine.configured:
        state = "есть" if report.engine.exists else "НЕ НАЙДЕН"
        lines.append(f"Движок разбора: {report.engine.path} ({state})")
    else:
        lines.append(
            "Движок разбора не задан (`graph.engine_path`): команды `graph *` "
            "откажутся работать. Для шагов 1, 2 и бизнес-слоя это законно."
        )

    lines += ["", f"Префикс doc_path технических документов: {report.modules_root}"]
    if report.web_modules_root is not None:
        lines.append(f"Префикс doc_path документов фронта:    {report.web_modules_root}")

    if not report.problems:
        lines += ["", "Всё, что настроено, на месте."]
    return "\n".join(lines)


def format_problems(report: ConfigReport) -> str:
    """Сводка проблем для stderr; пустая строка — проблем нет."""
    lines: list[str] = []
    missing = [item.key for item in report.problems if item.code == "input-missing"]
    if missing:
        lines += ["Не найдено: " + ", ".join(missing) + ".", _INPUT_HINT]
    # Вход здесь найден, и «сломано» о нём было бы неправдой: прогон прочтёт
    # файл — возможно, не тот.
    shadowed = [item for item in report.problems if item.code == "input-shadowed"]
    if shadowed:
        lines.append("Короткое имя нашлось в каталоге продукта, а не рядом с конфигурацией:")
        lines += [f"  {item.message}" for item in shadowed]
    others = [
        item for item in report.problems if item.code not in ("input-missing", "input-shadowed")
    ]
    if others:
        lines.append("Сломано в настройке:")
        lines += [f"  {item.message}" for item in others]
    return "\n".join(lines)
