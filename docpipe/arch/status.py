"""Состояние снимков: не отстал ли реестр от источника.

Снимок стареет по определению — это цена за то, что он работает для формата,
который мы видим впервые. Единственный способ узнать, что зеркало отстало, —
хранить хэш того, что зеркалится, и сверять его регулярно.

«Регулярно» здесь не дисциплина, а строка отчёта: те же расхождения обязан
печатать `graph health` (G08). Проверка, которую делают «когда дойдут руки»,
не делается никогда (риск Р-12).
"""

from pathlib import Path
from typing import Any, Final, NamedTuple

from docpipe.arch.model import ArchRegistry
from docpipe.hashing import content_hash

# Порядок состояний несёт смысл: сначала то, что сломано, потом то, что
# в работе, потом то, что в порядке.
STATES: Final[tuple[str, ...]] = ("stale", "source_missing", "no_hash", "live", "current")

_EXPLAIN: Final[dict[str, str]] = {
    "stale": "источник изменился после снимка — запись устарела",
    "source_missing": "файла источника нет: переименован, удалён или перенесён",
    "no_hash": "хэш источника не записан — устаревание такой записи не ловится",
    "live": "адаптер читает источник при каждой сборке, снимка нет",
    "current": "снимок совпадает с источником",
}


class SourceStatus(NamedTuple):
    kind: str
    key: str
    file: str
    state: str
    # Хэш источника **сейчас**. Пусто, если файла нет или снимок не нужен.
    # Он в отчёте не для красоты: без него человек, увидевший «снимок отстал»,
    # идёт считать sha256 руками — а это ровно та мелкая работа, из-за которой
    # проверку перестают делать (риск Р-11).
    current: str = ""


def source_statuses(registry: ArchRegistry, root: Path) -> list[SourceStatus]:
    """Состояние каждой записи. Порядок — явная сортировка, не порядок файла."""
    statuses: list[SourceStatus] = []
    cache: dict[str, str | None] = {}
    for record in registry.records:
        source = record.source
        if source.file not in cache:
            path = root / source.file
            if path.is_file():
                cache[source.file] = content_hash(path.read_bytes())
            elif path.exists():
                # Каталог: источником он быть может (слой — это каталог),
                # а хэша у него нет. Пустая строка — «есть, но хэшировать
                # нечего», и это не то же самое, что «нет вовсе».
                cache[source.file] = ""
            else:
                cache[source.file] = None
        current = cache[source.file]

        # Существование проверяется ДО хэша и независимо от него. Иначе
        # запись, чей источник переименовали или удалили, остаётся тихой:
        # хэш ловит только «файл менялся», а не «файла больше нет» —
        # ровно та дыра, что записана в плане риском Р-12.
        if current is None:
            state = "source_missing"
        elif record.provenance == "adapter":
            state = "live"
        elif not source.hash or not current:
            state = "no_hash"
        else:
            state = "current" if current == source.hash else "stale"
        statuses.append(
            SourceStatus(record.kind, record.normalized_key, source.file, state, current or "")
        )
    return sorted(statuses, key=lambda item: (STATES.index(item.state), item.kind, item.key))


def statuses_json(statuses: list[SourceStatus]) -> dict[str, Any]:
    counts = {state: sum(1 for item in statuses if item.state == state) for state in STATES}
    return {
        "total": len(statuses),
        "by_state": counts,
        "records": [
            {"kind": item.kind, "key": item.key, "file": item.file, "state": item.state}
            for item in statuses
        ],
    }


def format_statuses(statuses: list[SourceStatus], verbose: bool = False) -> str:
    """Отчёт для человека.

    Пустой реестр называется вслух: «записей нет» и «проверка не отработала»
    в отчёте обязаны различаться (Р7).
    """
    if not statuses:
        return "Реестр пуст: записей нет. Это валидное состояние — граф строится и без реестра.\n"

    lines: list[str] = []
    for state in STATES:
        selected = [item for item in statuses if item.state == state]
        if not selected:
            continue
        lines.append(f"{state}: {len(selected)} — {_EXPLAIN[state]}")
        shown = selected if verbose or state in ("stale", "source_missing") else selected[:5]
        for item in shown:
            lines.append(f"    {item.kind:<12} {item.key:<40} {item.file}")
        if state in ("stale", "no_hash"):
            # Одна строка на файл, а не на запись: у реестра из сорока записей
            # источник один, и сорок одинаковых хэшей — это не подсказка.
            for file, digest in sorted({item.file: item.current for item in selected}.items()):
                if digest:
                    lines.append(f"    вписать в `hash:` для {file} → {digest}")
        if len(shown) < len(selected):
            lines.append(f"    … ещё {len(selected) - len(shown)}; полный список — с `--verbose`")
    lines.append("")
    lines.append(f"Всего записей: {len(statuses)}")
    return "\n".join(lines) + "\n"
