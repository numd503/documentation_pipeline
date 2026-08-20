"""Поиск и разрешение имён (G10).

Без этой фичи API бесполезен: попытка найти связь двух сущностей провалится
потому, что одна «не нашлась», и отличить это от «связи нет» будет невозможно.

**Семантику приносит вызывающий.** MCP зовут из оболочки агента — на том конце
языковая модель, которая переформулирует, переведёт термин и попробует ещё раз.
Поэтому здесь нет ни эмбеддингов, ни морфологии, ни синонимов: работа индекса
— быстрый нечёткий матч и честный список кандидатов **с указанием, чем
совпало**. Вызывающий, знающий, что матч был по триграммам в английском имени,
переформулирует осмысленно; вызывающий с голым списком может только гадать.

**Регистр кириллицы сворачивается на нашей стороне.** `LOWER()` и `NOCASE`
в SQLite без ICU сворачивают только ASCII: «пользовательские» не совпадёт
с «Пользовательские» никогда, тесты на английских именах этого не покажут,
и весь русский поиск умрёт молча — вместе с единственным глоссарием
предметных слов. Отсюда нормализация одним правилом и при индексации,
и при разборе запроса (`docpipe/keys.py`).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from docpipe.graph.model import GraphIndex, GraphNode
from docpipe.keys import normalize_text
from docpipe.model import Manifest

TRIGRAM_MINIMUM: Final[float] = 0.25
DEFAULT_LIMIT: Final[int] = 15

# Как совпало. Строка идёт в ответ: без неё вызывающий не знает, стоит ли
# переформулировать запрос и как именно.
EXACT: Final[str] = "точное совпадение"
PREFIX: Final[str] = "совпало начало"
SUBSTRING: Final[str] = "совпала подстрока"
TRIGRAM: Final[str] = "похоже по триграммам"


@dataclass(frozen=True)
class SearchEntry:
    node: str
    field: str
    text: str


@dataclass(frozen=True)
class Match:
    node: str
    kind: str
    name: str
    module: str
    field: str
    how: str
    fragment: str
    score: float


def _trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def similarity(left: str, right: str) -> float:
    """Доля общих триграмм. Мера грубая и намеренно такая."""
    first, second = _trigrams(left), _trigrams(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def entries(index: GraphIndex, manifest: Manifest | None = None) -> list[SearchEntry]:
    """Что вообще ищется. Перечень положительный и объяснимый по каждому полю."""
    found: list[SearchEntry] = []

    def add(node: str, field: str, text: str) -> None:
        value = (text or "").strip()
        if value:
            found.append(SearchEntry(node=node, field=field, text=value))

    for node in index.nodes:
        add(node.key, "имя", node.name)
        add(node.key, "ключ", node.key)
        for name, value in node.attributes.items():
            if name in ("fqn", "route", "registry_key", "ref", "impl"):
                add(node.key, name, value)
            # Русские названия полей списка — единственный источник предметных
            # слов на репозитории, где код английский, а предметная область
            # русская. Формат значения — «вид|человеческое название».
            elif name.startswith("field:"):
                add(node.key, "поле", value.split("|", 1)[-1])
                add(node.key, "поле", name.removeprefix("field:"))

    if manifest is not None:
        keys = {node.key for node in index.nodes}
        for document in manifest.nodes:
            symbol = document.symbol
            if symbol is None:
                continue
            # Документ привязывается к узлу графа тем же способом, что
            # и в сопоставлении: по файлу и имени типа.
            for source in symbol.sources:
                key = f"{source.path}#{symbol.name}"
                if key not in keys:
                    continue
                add(key, "заголовок", document.title)
                add(key, "домен", document.domain)
                if symbol.xml_doc:
                    add(key, "описание", symbol.xml_doc)

    return sorted(found, key=lambda entry: (entry.node, entry.field, entry.text))


def write(connection: sqlite3.Connection, found: list[SearchEntry]) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS search (
            node TEXT NOT NULL,
            field TEXT NOT NULL,
            text TEXT NOT NULL,
            norm TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS search_norm ON search (norm);
        """
    )
    connection.executemany(
        "INSERT INTO search (node, field, text, norm) VALUES (?, ?, ?, ?)",
        [(entry.node, entry.field, entry.text, normalize_text(entry.text)) for entry in found],
    )


def resolve(
    path: Path, query: str, nodes: dict[str, GraphNode], limit: int = DEFAULT_LIMIT
) -> tuple[list[Match], bool]:
    """Разрешить свободный текст в кандидатов.

    Возвращает кандидатов и признак «точного совпадения нет». **Пустой ответ
    невозможен**: если ничего не совпало ни точно, ни подстрокой, отдаются
    ближайшие по триграммам с явной пометкой. Пустота — худший из возможных
    ответов: она неотличима от факта и не даёт зацепки для второй попытки.
    """
    wanted = normalize_text(query)
    if not wanted:
        return [], True

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT node, field, text, norm FROM search").fetchall()
    finally:
        connection.close()

    scored: dict[tuple[str, str], Match] = {}

    def remember(node: str, field: str, text: str, how: str, score: float) -> None:
        target = nodes.get(node)
        if target is None:
            return
        identity = (node, field)
        current = scored.get(identity)
        if current is not None and current.score >= score:
            return
        scored[identity] = Match(
            node=node,
            kind=target.kind,
            name=target.name or node,
            module=target.attributes.get("module", ""),
            field=field,
            how=how,
            fragment=text,
            score=score,
        )

    exact_found = False
    for node, field, text, norm in rows:
        if norm == wanted:
            remember(node, field, text, EXACT, 1.0)
            exact_found = True
        elif norm.startswith(wanted):
            remember(node, field, text, PREFIX, 0.8)
        elif wanted in norm:
            remember(node, field, text, SUBSTRING, 0.6)

    if not scored:
        for node, field, text, norm in rows:
            score = similarity(wanted, norm)
            if score >= TRIGRAM_MINIMUM:
                remember(node, field, text, TRIGRAM, round(score, 3))

    # По узлу оставляется лучшее совпадение: один и тот же тип, найденный
    # и по имени, и по заголовку документа, — это одна находка, а не две.
    # Поле, по которому совпало, при этом остаётся в ответе: без него
    # вызывающий не знает, как переформулировать запрос.
    best: dict[str, Match] = {}
    for match in scored.values():
        current = best.get(match.node)
        if current is None or match.score > current.score:
            best[match.node] = match

    ranked = sorted(best.values(), key=lambda match: (-match.score, match.node))
    return ranked[:limit], not exact_found


def nearest(path: Path, query: str, nodes: dict[str, GraphNode], limit: int = 5) -> list[Match]:
    """Ближайшие кандидаты, когда не нашлось ничего. Используется в ответах,
    где пустота запрещена."""
    found, _ = resolve(path, query, nodes, limit)
    return found
