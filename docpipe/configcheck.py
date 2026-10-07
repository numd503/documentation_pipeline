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

from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.config import DocpipeConfig, candidate_inputs

SCHEMA_VERSION: Final = "1.0"

ProblemCode = Literal["input-missing", "root-missing", "adapter-input-missing", "engine-missing"]
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

# Порядок проблем — порядок разделов отчёта, чтобы сводка читалась сверху вниз
# так же, как сам отчёт.
_PROBLEM_ORDER: Final[tuple[ProblemCode, ...]] = (
    "input-missing",
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
    """

    key: str
    value: str
    candidates: list[str]
    found: str | None
    step: Literal["cwd", "config"] | None


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
    """

    adapter_id: str
    option: AdapterOption
    base: AdapterBase
    value: str
    resolved: str
    exists: bool


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
    schema_version: Literal["1.0"] = SCHEMA_VERSION
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


def _first_existing(candidates: list[Path], cwd: Path) -> Path | None:
    """Кандидат, который выберет `resolve_input`, запущенный из `cwd`.

    Существование — от `cwd` отчёта, а не от текущего каталога процесса:
    сервер настройки зовёт проверку за каталог, из которого будут звать
    команду, и ответ обязан быть тем, что увидит она.
    """
    return next((path for path in candidates if (cwd / path).exists()), None)


def _check_input(key: str, value: str, config: Path | None, cwd: Path) -> InputCheck:
    if not value:
        return InputCheck(key=key, value="", candidates=[], found=None, step=None)
    candidates = candidate_inputs(value, config)
    found = _first_existing(candidates, cwd)
    step: Literal["cwd", "config"] | None = None
    if found is not None:
        step = "cwd" if found == candidates[0] else "config"
    return InputCheck(
        key=key,
        value=value,
        candidates=[str(path) for path in candidates],
        found=None if found is None else str(found),
        step=step,
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
    if base == "config":
        candidates = candidate_inputs(value, config)
        chosen = _first_existing(candidates, cwd) or candidates[0]
        resolved = (cwd / chosen).resolve()
    else:
        resolved = (root / value).resolve()
    return AdapterInput(
        adapter_id=adapter_id,
        option=option,
        base=base,
        value=value,
        resolved=str(resolved),
        exists=resolved.exists(),
    )


def check_config(
    settings: DocpipeConfig, config: Path | None, root: Path, cwd: Path
) -> ConfigReport:
    """Разрешить каждый путь настройки так, как его разрешит команда из `cwd` с `--root`.

    Проблема — только то, что действительно ломает прогон: ненайденный вход,
    корень обхода без каталога, вход адаптера без файла, названный, но
    отсутствующий движок. Каталог цели записи, которого ещё нет, проблемой
    не считается: его создаст первый прогон.
    """
    absolute_root = (cwd / root).resolve()
    problems: list[ConfigProblem] = []

    inputs = [
        _check_input(key, str(_key_value(settings, key) or ""), config, cwd) for key in INPUT_KEYS
    ]
    for item in inputs:
        if item.value and item.found is None:
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
        ("web.roots", settings.web.roots),
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
            if not check.exists:
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
        if checked.exists:
            continue
        key = f"arch_adapters[{checked.adapter_id}].options.{checked.option}"
        if not checked.value:
            message = f"{key}: обязательный параметр не задан; адаптер откажет на сборке"
        else:
            message = (
                f"{key}: {checked.value!r} не найден ({_BASE_TEXT[checked.base]}): "
                f"{checked.resolved}"
            )
        problems.append(ConfigProblem(code="adapter-input-missing", key=key, message=message))

    engine = _check_engine(settings, cwd)
    if engine.configured and not engine.exists:
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
        problems=sorted(problems, key=lambda item: (_PROBLEM_ORDER.index(item.code), item.key)),
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
            lines.append(f"  {item.key:16} {item.value}  → {item.found} ({_STEP_TEXT[item.step]})")

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
            lines.append(
                f"  {name:16} {adapter.value}  → {adapter.resolved}"
                f" ({_BASE_TEXT[adapter.base]}; {state})"
            )

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
    others = [item for item in report.problems if item.code != "input-missing"]
    if others:
        lines.append("Сломано в настройке:")
        lines += [f"  {item.message}" for item in others]
    return "\n".join(lines)
