"""Чтение SQL: какие таблицы трогает запрос и какие процедуры зовёт (G05c).

Разбор здесь **намеренно грубый**: регулярные выражения по ключевым словам,
а не грамматика диалекта. Причина не в лени, а в границе задачи. Нужно
ответить на «какие имена участвуют», а не «что этот запрос делает»; полная
грамматика T-SQL или PL/pgSQL стоит отдельного проекта и ломается на первом же
расширении диалекта, а имена лежат сразу за ключевыми словами и в любом
диалекте одинаково.

Что это правило означает на практике: динамический SQL (строка собирается
и исполняется) **не разрешается** и уходит отдельной категорией. Это не то же
самое, что неразобранный литерал: там имя есть и мы его не поняли, здесь
имени нет вовсе до момента исполнения.

**Временные таблицы узлами данных не становятся.** Иначе граф зарастёт
`#tmp` и `@rows`, а веерность потеряет смысл: у временной таблицы в одной
процедуре нет ничего общего с одноимённой в другой.
"""

import re
from dataclasses import dataclass, field
from typing import Final

# Ключевые слова, за которыми идёт имя таблицы. Разделены по смыслу: чтение
# и запись — разные рёбра, и складывать их в одно значило бы отвечать
# «трогает» там, где спрашивают «портит ли».
_READ: Final[re.Pattern[str]] = re.compile(
    r"\b(?:FROM|JOIN|APPLY)\s+(?P<name>[\[\]\w.\"`#@]+)", re.IGNORECASE
)
_WRITE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE\s+TABLE)\s+"
    r"(?P<name>[\[\]\w.\"`#@]+)",
    re.IGNORECASE,
)
_EXEC: Final[re.Pattern[str]] = re.compile(
    r"\b(?:EXEC|EXECUTE|CALL)\s+(?P<name>[\[\]\w.\"`#@]+)", re.IGNORECASE
)
_DEFINE: Final[re.Pattern[str]] = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?(?P<kind>PROCEDURE|PROC|FUNCTION|VIEW)\s+"
    r"(?P<name>[\[\]\w.\"`#@]+)",
    re.IGNORECASE,
)
# Динамика: исполняется не имя, а выражение — `EXEC (@sql)`, `sp_executesql`.
_DYNAMIC: Final[re.Pattern[str]] = re.compile(
    r"\b(?:EXEC|EXECUTE)\s*\(|\bsp_executesql\b", re.IGNORECASE
)

_COMMENT_BLOCK: Final[re.Pattern[str]] = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE: Final[re.Pattern[str]] = re.compile(r"--[^\n]*")

# Слова, которые синтаксически стоят на месте имени, но именем не являются.
_NOISE: Final[frozenset[str]] = frozenset(
    {"select", "values", "set", "where", "as", "on", "inner", "left", "right", "full", "cross"}
)


@dataclass(frozen=True)
class SqlFacts:
    """Что видно в тексте запроса или процедуры."""

    defines: tuple[tuple[str, str], ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    dynamic: bool = False
    # Временные таблицы, отброшенные по правилу. Число, а не молчание:
    # «временных не было» и «мы их не считали» обязаны различаться.
    temporary: int = 0
    fields: dict[str, int] = field(default_factory=dict)


def normalize_name(raw: str) -> str:
    """Имя объекта без скобок и кавычек, регистр свёрнут.

    Нормализация та же, что у узлов данных: отдельное правило разошлось бы
    с ним, и процедура, названная в C# и объявленная в `.sql`, дала бы
    два узла вместо одного.
    """
    from docpipe.keys import normalize_data_name

    return normalize_data_name(raw)


def _is_temporary(name: str) -> bool:
    """`#tmp`, `##global`, `@rows` — временные, узлами данных не становятся."""
    bare = name.strip('[]"`').split(".")[-1]
    return bare.startswith("#") or bare.startswith("@")


def read(text: str) -> SqlFacts:
    """Извлечь имена из текста SQL."""
    stripped = _COMMENT_LINE.sub(" ", _COMMENT_BLOCK.sub(" ", text))

    def names(pattern: re.Pattern[str]) -> tuple[list[str], int]:
        found: list[str] = []
        temporary = 0
        for match in pattern.finditer(stripped):
            raw = match.group("name")
            if raw.lower() in _NOISE:
                continue
            if _is_temporary(raw):
                temporary += 1
                continue
            name = normalize_name(raw)
            if name and name not in found:
                found.append(name)
        return found, temporary

    reads, temporary_reads = names(_READ)
    writes, temporary_writes = names(_WRITE)
    calls, _ = names(_EXEC)
    defines = [
        (normalize_name(match.group("name")), match.group("kind").lower())
        for match in _DEFINE.finditer(stripped)
    ]
    # Имя, за которым идёт `(`, исполняется как выражение: это динамика,
    # а не вызов процедуры с таким именем.
    dynamic = bool(_DYNAMIC.search(stripped))
    if dynamic:
        calls = [name for name in calls if name]

    return SqlFacts(
        defines=tuple(defines),
        reads=tuple(sorted(reads)),
        writes=tuple(sorted(writes)),
        calls=tuple(sorted(calls)),
        dynamic=dynamic,
        temporary=temporary_reads + temporary_writes,
    )
