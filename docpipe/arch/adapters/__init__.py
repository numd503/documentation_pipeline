"""Адаптеры реестров (R04): подключаются по имени, а не ветвлением в ядре.

Ядро не знает ни одного конкретного реестра. Что читать, откуда и как
называются поля, задаёт конфигурация; какой код это делает — имя адаптера
в ней же. Отсутствие адаптера ничего не блокирует: снимок остаётся рабочим
способом, и на репозитории, который мы видим впервые, он единственный.
"""

from pathlib import Path
from typing import Any, Final

from docpipe.arch.adapters.base import Adapter, AdapterContext, AdapterResult
from docpipe.arch.adapters.code import from_python_code
from docpipe.arch.adapters.declared import DEFAULT_KINDS, from_registries

ADAPTERS: Final[dict[str, Adapter]] = {
    "registries": from_registries,
    "python_code": from_python_code,
}


def run_adapter(
    name: str, options: dict[str, Any], root: Path, resolve: Any = None
) -> AdapterResult:
    """Запустить адаптер по имени.

    Неизвестное имя — отказ со списком известных. Тихо пропустить адаптер
    нельзя: пропущенный адаптер даёт реестр без части записей, а реестр
    без части записей внешне неотличим от репозитория, где этих точек входа
    нет вовсе.
    """
    adapter = ADAPTERS.get(name)
    if adapter is None:
        known = ", ".join(sorted(ADAPTERS))
        raise ValueError(f"адаптер {name!r} неизвестен; известны: {known}")
    context = AdapterContext(root=root, resolve=resolve or (lambda value: Path(value)))
    return adapter(context, options)


__all__ = [
    "ADAPTERS",
    "DEFAULT_KINDS",
    "Adapter",
    "AdapterContext",
    "AdapterResult",
    "run_adapter",
]
