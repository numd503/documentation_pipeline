"""Запись документа и принятое состояние.

Единственный экземпляр принятого состояния живёт в самом документе — это
правило шага 2, бизнес-слоя и всего, что придёт следом. Зеркала не заводятся:
второй источник обязан отстать, а приёмка, взявшая значение оттуда, зафиксирует
устаревшее, и документ навсегда останется `current`, будучи `stale`.

Здесь лежит то, что у всех слоёв одинаково: атомарная запись и **форма** блока
состояния. Содержимое блока — своё у каждого слоя (`signature_hash` у шага 2,
`business_hash` у бизнес-слоя), и сводить его в одно значило бы придумать общий
знаменатель там, где его нет.
"""

import os
from pathlib import Path
from typing import Any, Final

from docpipe.documents.model import ParsedDocument

# Ключ front matter, в котором живёт состояние. Один на все слои: документ
# читается общим разбором зон, и разные ключи означали бы, что слой не видит
# приёмку соседа и молча перезаписывает её.
STATE_KEY: Final[str] = "docpipe_state"

# Ключи front matter, принадлежащие инструменту. Всё остальное — чужое
# и не трогается никогда; список нужен обоим слоям, и записанный дважды
# он разъедется на первом же новом ключе.
RESERVED_KEYS: Final[tuple[str, ...]] = ("docpipe", STATE_KEY)


def accepted_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Блок состояния для записи приёмки.

    Две вещи, которые обязаны быть одинаковыми у всех слоёв и однажды не были:

    - **`review` сбрасывается приёмкой.** Отметка о пересмотре означает
      «человек ещё не смотрел»; после приёмки она ложна. Слой, забывший её
      снять, оставит документ с признаком пересмотра навсегда.
    - **времени в блоке нет.** С ним приёмка перестаёт быть идемпотентной:
      два прогона подряд дают дифф на пустом месте, а история правок и так
      точнее хранится в git.
    """
    return {"accepted": payload, "review": None}


def read_accepted(parsed: ParsedDocument) -> dict[str, Any] | None:
    """Принятое состояние из разобранного документа, если оно есть.

    `None` означает «приёмки не было» — это самостоятельный статус
    (`undeclared`), а не пустое состояние: разница между «сверяли и приняли»
    и «не сверяли» и есть то, ради чего блок хранится.
    """
    state = parsed.state or {}
    value = state.get("accepted")
    return value if isinstance(value, dict) else None


def read_review(parsed: ParsedDocument) -> dict[str, Any] | None:
    """Отметка о пересмотре, если она стои́т."""
    state = parsed.state or {}
    value = state.get("review")
    return value if isinstance(value, dict) else None


def write_atomic(path: Path, content: str) -> None:
    """Запись через временный файл в том же каталоге и `os.replace`.

    Временный файл называется `.{имя}.md.tmp`: точка в начале и суффикс не `.md`,
    поэтому обход сирот не подберёт его, если процесс умер между созданием
    и переименованием.

    `newline="\\n"` — всегда. Сравнение «писать или нет» идёт по нормализованному
    тексту, а запись обязана быть одинаковой на всех платформах.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
