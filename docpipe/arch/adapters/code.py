"""Адаптер реестра, записанного исполняемым кодом.

Реестр не обязан быть файлом данных. Регистрация сервисов, таблица обработчиков,
список маршрутов — всё это часто лежит модулем на Python, и по природе своей
это те же записи: имя и класс, который его обслуживает.

**Модуль разбирается статически и никогда не импортируется.** Импорт чужого
файла ради «посмотреть, что зарегистрировалось» — это выполнение произвольного
кода репозитория при разведке, то есть ровно то, чего инструмент разведки
делать не должен. Плата известна и принята: динамическая регистрация (цикл
по каталогу, сборка имени из кусков) сюда не попадёт, и её отсутствие видно
числом, а не догадкой.

Три формы, и все три встречаются в одном файле:

    SERVICES = {"forecast": ForecastHandler}       # словарь
    ROUTES = [("/forecast", ForecastHandler)]      # список пар
    register("forecast", ForecastHandler)          # вызов регистрации
"""

import ast
from typing import Any, Final

from docpipe.arch.adapters.base import (
    AdapterContext,
    AdapterResult,
    FileHashes,
    require,
    unknown_options,
)
from docpipe.arch.model import ArchRecord, EntryPointRecord, Source

ADAPTER = "python_code"

_KNOWN_OPTIONS = {"path", "variable", "call", "entry_kind"}
_MIN_KEY_LENGTH: Final[int] = 2


def _dotted(node: ast.AST) -> str:
    """Имя значения: `Handler`, `handlers.Forecast`, `Forecast()`.

    Вызов разворачивается до вызываемого: `Forecast()` в таблице регистрации
    — это тот же класс, а не другой объект, и терять его из-за скобок
    было бы обидно.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _literal(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _pairs_from_value(node: ast.AST) -> list[tuple[str, str]]:
    """Пары «строка → имя» из словаря или из списка пар."""
    pairs: list[tuple[str, str]] = []
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            name = _literal(key) if key is not None else ""
            if name:
                pairs.append((name, _dotted(value)))
    elif isinstance(node, ast.List | ast.Tuple | ast.Set):
        for element in node.elts:
            if isinstance(element, ast.Tuple | ast.List) and len(element.elts) >= 2:
                name = _literal(element.elts[0])
                if name:
                    pairs.append((name, _dotted(element.elts[1])))
    return pairs


def from_python_code(ctx: AdapterContext, options: dict[str, Any]) -> AdapterResult:
    """Разобрать модуль Python и выдать точки входа по его таблицам регистрации."""
    unknown_options(options, _KNOWN_OPTIONS, ADAPTER)
    relative = str(require(options, "path", ADAPTER))
    variable = str(options.get("variable") or "")
    call = str(options.get("call") or "")
    entry_kind = str(options.get("entry_kind") or "service")

    path = ctx.root / relative
    if not path.is_file():
        return AdapterResult(errors=(f"адаптер {ADAPTER}: файла нет — {relative}",))

    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return AdapterResult(errors=(f"адаптер {ADAPTER}: {relative} не разбирается — {error}",))

    digest = FileHashes(ctx.root).of(relative)
    found: list[tuple[str, str, str]] = []  # ключ, реализация, адрес записи

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if variable and variable not in names:
                continue
            if node.value is None:
                continue
            where = names[0] if names else "?"
            for key, impl in _pairs_from_value(node.value):
                found.append((key, impl, f"{where}[{key!r}]"))
        elif isinstance(node, ast.Call) and call:
            called = _dotted(node.func)
            if called != call and not called.endswith(f".{call}"):
                continue
            if len(node.args) >= 2:
                key = _literal(node.args[0])
                if key:
                    found.append((key, _dotted(node.args[1]), f"{call}({key!r})"))

    records: list[ArchRecord] = []
    seen: set[str] = set()
    for key, impl, where in sorted(found):
        if len(key) < _MIN_KEY_LENGTH or key in seen:
            continue
        seen.add(key)
        records.append(
            EntryPointRecord(
                key=key,
                entry_kind=entry_kind,  # type: ignore[arg-type]
                impl=(impl,) if impl else (),
                source=Source(file=relative, record=where, hash=digest),
                provenance="adapter",
            )
        )

    errors: list[str] = []
    if not records:
        # Ноль записей произносится вслух: пустой результат внешне неотличим
        # от «в файле нет регистраций», а причина обычно другая — искали
        # не ту переменную или регистрация динамическая.
        errors.append(
            f"адаптер {ADAPTER}: в {relative} не нашлось ни одной пары «строка → имя»"
            + (f" (искали в {variable!r})" if variable else "")
            + (f" и вызовов {call!r}" if call else "")
        )
    return AdapterResult(records=tuple(records), errors=tuple(errors))
