"""Загрузка `arch-registry.yaml` с полной проверкой структуры.

Проверять надо при загрузке, а не при первом применении. Опечатка в имени
поля или в виде записи иначе означает реестр, который просто ничего
не находит, а пустой реестр внешне неотличим от «в системе нет таких точек
входа» — и обнаружится это в лучшем случае через месяц. Правило перенесено
из `registry/config.py` без изменений: там оно уже окупилось.

Проверки собираются **все**, а не до первой: файл заполняет человек, и второй
заход ради второй ошибки — способ отучить его заполнять файл.
"""

from pathlib import Path
from typing import Any, NamedTuple

import yaml
from pydantic import TypeAdapter, ValidationError
from pydantic_core import ErrorDetails

from docpipe.arch.model import ARCH_VERSION, ArchRecord, ArchRegistry

_KNOWN_TOP = frozenset({"version", "records"})
_KINDS = frozenset({"entry_point", "data", "seam", "layer"})

_RECORD_ADAPTER: TypeAdapter[ArchRecord] = TypeAdapter(ArchRecord)


class ArchProblem(NamedTuple):
    """Одна находка проверки. `where` — адрес, чтобы идти чинить было куда."""

    where: str
    message: str


def _describe(error: ErrorDetails) -> str:
    location = ".".join(str(part) for part in error.get("loc", ()) if part != "function-after")
    message = error.get("msg", "")
    return f"{location}: {message}" if location else str(message)


def check_document(
    raw: Any, *, draft: bool = False
) -> tuple[ArchRegistry | None, list[ArchProblem]]:
    """Проверить разобранный YAML и собрать реестр.

    `draft=True` — режим черновика, который готовит скилл разведки (R02).
    В нём разрешён провенанс `skill_proposed`; в боевом реестре он запрещён,
    и это структурная реализация Р10: предложение модели становится входом
    графа, только пройдя через человека, и «пройдя» проверяется файлом,
    а не памятью.
    """
    problems: list[ArchProblem] = []

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return None, [ArchProblem("файл", "верхний уровень обязан быть словарём")]

    unknown = sorted(set(raw) - _KNOWN_TOP)
    if unknown:
        problems.append(
            ArchProblem("файл", f"неизвестные ключи {unknown}; известны: {sorted(_KNOWN_TOP)}")
        )

    version = str(raw.get("version", ""))
    if not version:
        problems.append(ArchProblem("файл", "нет ключа `version`; ожидается " + ARCH_VERSION))
    elif version != ARCH_VERSION:
        problems.append(
            ArchProblem(
                "файл",
                f"версия формата {version}, поддерживается {ARCH_VERSION}. "
                "Обновлять файл руками по `docs/arch-registry.md`; "
                "молча прочитать его нельзя — записи будут не те, что писал автор",
            )
        )

    entries = raw.get("records") or []
    if not isinstance(entries, list):
        return None, [*problems, ArchProblem("records", "`records` обязан быть списком")]

    records: list[ArchRecord] = []
    for index, entry in enumerate(entries):
        where = f"records[{index}]"
        if not isinstance(entry, dict):
            problems.append(ArchProblem(where, "запись обязана быть словарём"))
            continue
        key = entry.get("key")
        if isinstance(key, str) and key:
            where = f"{where} ({key})"
        kind = entry.get("kind")
        if kind not in _KINDS:
            problems.append(
                ArchProblem(where, f"вид {kind!r} неизвестен; известны: {sorted(_KINDS)}")
            )
            continue
        try:
            record = _RECORD_ADAPTER.validate_python(entry)
        except ValidationError as error:
            for item in error.errors():
                problems.append(ArchProblem(where, _describe(item)))
            continue
        if record.provenance == "skill_proposed":
            if not draft:
                problems.append(
                    ArchProblem(
                        where,
                        "провенанс `skill_proposed` в реестре запрещён: предложение скилла "
                        "переносит в реестр человек и меняет провенанс на `skill_confirmed` "
                        "(Р10). Черновик проверяется командой `arch validate --draft`",
                    )
                )
            elif not record.note.strip():
                # Предложение без обоснования в черновик не попадает — критерий
                # приёмки R02 п. 1. Проверка здесь, а не в инструкции скилла:
                # инструкцию можно не выполнить, а загрузчик не уговоришь.
                # Обоснование — единственное, что отличает предложение модели
                # от выдумки, и стоит оно одной строки.
                problems.append(
                    ArchProblem(
                        where,
                        "предложение без обоснования: заполните `note` — какой файл, "
                        "какая запись и какое пересечение литералов его породило",
                    )
                )
        records.append(record)

    seen: dict[tuple[str, str], str] = {}
    for record in records:
        identity = (record.kind, record.normalized_key)
        if identity in seen:
            problems.append(
                ArchProblem(
                    f"{record.kind} ({record.key})",
                    f"ключ совпадает с записью {seen[identity]!r} после нормализации "
                    f"({record.normalized_key!r}); один ключ — одна запись",
                )
            )
            continue
        seen[identity] = record.key

    if problems:
        return None, problems
    return ArchRegistry(version=version or ARCH_VERSION, records=tuple(records)), []


def read_document(path: Path) -> Any:
    """Прочитать YAML байтами и снять BOM.

    `read_text(encoding="utf-8")` BOM не снимает, а редакторы Windows его
    ставят — на реестрах целевого репозитория это норма, а не исключение.
    """
    return yaml.safe_load(path.read_bytes().decode("utf-8-sig"))


def load_arch_registry(path: Path, *, draft: bool = False) -> ArchRegistry:
    """Загрузить реестр или отказаться, перечислив все находки."""
    if not path.exists():
        raise ValueError(f"реестр не найден: {path}")
    registry, problems = check_document(read_document(path), draft=draft)
    if registry is None:
        listed = "\n".join(f"  {problem.where}: {problem.message}" for problem in problems)
        raise ValueError(f"{path}: реестр не прошёл проверку\n{listed}")
    return registry


def load_optional(path: Path | None) -> ArchRegistry:
    """Реестр, если он есть, и пустой реестр, если его нет.

    Отсутствие реестра — не ошибка: на репозитории, где точки входа объявлены
    в коде, граф строится без него. Ошибкой было бы молча считать пустым
    файл, который есть, но не читается, — за это отвечает `load_arch_registry`.
    """
    if path is None or not path.exists():
        return ArchRegistry()
    return load_arch_registry(path)
