"""Идентичность узла: один узел — один ключ (G02).

Ключ наш и только наш. Идентификатор узла стороннего разбора ключом быть
не может: он не документирован, не обещан стабильным между версиями и умрёт
вместе с источником при переключении на запасной вариант. Идентификатор
источника — поле сопоставления, живущее один прогон.

Правило ключа продолжает то, что уже действует в манифесте: символ —
это `модуль + FQN + арность дженерика`, потому что FQN сам по себе типы
не различает (проверено на ABP: 255 коллизий на 9075 объявлений). Член
добавляет к ключу типа своё имя, арность и **отпечаток списка параметров**:
перегрузки одного имени обязаны различаться, а порядковый номер перегрузки
ключом быть не может — перегрузка, добавленная выше существующей, сменила бы
ключи существующих, и маски достижимости пересчитались бы там, где смысл
не менялся.

Ключ не зависит ни от номеров строк, ни от путей документов: переформатирование
файла ключ не меняет. Позиция участвует только в **сопоставлении**.
"""

import re
from typing import Final

from docpipe.hashing import content_hash
from docpipe.model import Member, Symbol
from docpipe.symbols import symbol_key

# Модификаторы параметров: на тип они не влияют, а в тексте сигнатуры стоят
# перед ним. `ref int` и `int` — один и тот же тип параметра для целей
# различения перегрузок... кроме `ref`/`out`/`in`, которые в C# как раз
# перегрузку и различают. Поэтому они остаются в отпечатке, а `params`,
# `this` и `scoped` — нет: они не создают перегрузку.
_DROPPED_MODIFIERS: Final[frozenset[str]] = frozenset({"params", "this", "scoped", "readonly"})

_WHITESPACE = re.compile(r"\s+")
_FINGERPRINT_LENGTH: Final[int] = 8


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """Разбить по разделителю верхнего уровня.

    Глубина считается по трём видам скобок сразу: `Dictionary<string, int> map`
    — один параметр, а не два, и `(int, int) pair` — тоже один. Наивный
    `split(",")` даёт здесь два и три параметра соответственно, то есть
    отпечаток, который не совпадёт ни с чем.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth = max(0, depth - 1)
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _last_balanced_group(text: str) -> str:
    """Последняя сбалансированная группа `(…)` в тексте — список параметров.

    Последняя, а не первая: у метода с кортежным возвращаемым значением
    (`public (int, int) GetPair(int x)`) первая группа принадлежит типу
    результата, и разбор по первой дал бы параметры `int, int` вместо `int x`.
    """
    end = text.rfind(")")
    if end == -1:
        return ""
    depth = 0
    for index in range(end, -1, -1):
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                return text[index + 1 : end]
    return ""


def parameter_types(signature: str) -> tuple[str, ...]:
    """Типы параметров из текста сигнатуры.

    Имя параметра отбрасывается: переименование параметра перегрузки
    не создаёт, а ключ менять не должно. Значение по умолчанию отбрасывается
    по той же причине.
    """
    inside = _last_balanced_group(signature)
    if not inside.strip():
        return ()
    types: list[str] = []
    for parameter in _split_top_level(inside):
        text = _split_top_level(parameter, "=")[0].strip()
        words = text.split()
        words = [word for word in words if word.lower() not in _DROPPED_MODIFIERS]
        if len(words) > 1:
            # Последнее слово — имя параметра; всё до него — тип с модификаторами.
            words = words[:-1]
        types.append(_WHITESPACE.sub(" ", " ".join(words)).strip())
    return tuple(types)


def generic_arity(signature: str, name: str) -> int:
    """Число параметров-дженериков метода: `Do<T, K>(…)` → 2."""
    position = signature.find(name + "<")
    if position == -1:
        return 0
    start = position + len(name)
    inside_end = signature.find(">", start)
    if inside_end == -1:
        return 0
    return len(_split_top_level(signature[start + 1 : inside_end]))


def member_fingerprint(kind: str, types: tuple[str, ...]) -> str:
    """Отпечаток члена: вид плюс список типов параметров.

    Вид входит намеренно: поле и метод без параметров с одним именем
    не должны спорить за ключ. Отпечаток короткий — ключи идут в отчёты
    и в базу, и полный sha256 сделал бы их нечитаемыми.
    """
    digest = content_hash("|".join([kind, *types]).encode("utf-8"))
    return digest.split(":", 1)[1][:_FINGERPRINT_LENGTH]


def type_key(module: str, fqn: str, arity: int) -> str:
    """Ключ типа — тот же, что у манифеста: модуль, FQN, арность."""
    return symbol_key(module, fqn, arity)


def member_key(owner: str, name: str, arity: int, kind: str, types: tuple[str, ...]) -> str:
    """Ключ члена: ключ типа, имя, арность дженерика и отпечаток параметров."""
    return f"{owner}::{name}`{arity}~{member_fingerprint(kind, types)}"


def symbol_type_key(symbol: Symbol) -> str:
    return type_key(symbol.module, symbol.fqn, len(symbol.type_parameters))


def symbol_member_key(symbol: Symbol, member: Member) -> str:
    return member_key(
        symbol_type_key(symbol),
        member.name,
        generic_arity(member.signature, member.name),
        member.kind,
        parameter_types(member.signature),
    )
