"""Разбор Angular-шаблонов: что вызывает экран (G14).

Известная дыра, закрываемая здесь: `(click)="save()"` и `service.ready$ | async`
живут в `.html`, и разбор `.ts` их не видит. Для рёбер «страница → сервис»
это существенно, а страница — та самая граничная сущность, о которой
спрашивает пользователь.

**Грамматики шаблонов у нас нет, и притворяться, что есть, не нужно.**
Выражения достаются регулярными выражениями из мест, где они по синтаксису
Angular и лежат: интерполяции `{{ … }}` и значения биндингов `(click)=`,
`[value]=`, `[(ngModel)]=`, `*ngIf=`. Ложные срабатывания снимаются не
хитростью разбора, а **сверкой с объявленными членами**: имя, которого нет
ни среди членов компонента, ни среди его зависимостей, ребром не становится.
Это то же правило, что и в разборе `.ts`: разрешается только однозначное.

**Ошибка чтения шаблона не роняет разбор компонента.** Шаблон может быть
удалён, переименован или лежать вне репозитория; компонент от этого никуда
не девается, и терять его целиком из-за отсутствующего `.html` нельзя.
"""

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from docpipe.model import Symbol

# Место, где в шаблоне лежит выражение. Три формы, и все три обязательны:
# интерполяция, биндинг в квадратных или круглых скобках и структурная
# директива со звёздочкой.
_INTERPOLATION: Final[re.Pattern[str]] = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)
_BINDING: Final[re.Pattern[str]] = re.compile(
    r"""[\[(*][^\s"'=<>]*[\])]?\s*=\s*(?P<quote>["'])(?P<expression>.*?)(?P=quote)""",
    re.DOTALL,
)

_CALL: Final[re.Pattern[str]] = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_ACCESS: Final[re.Pattern[str]] = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)")
_NAME: Final[re.Pattern[str]] = re.compile(r"\b([A-Za-z_$][\w$]*)\b")

# Метка источника вызова. Вызов из шаблона не принадлежит ни одному члену:
# его пишет разметка, а не метод. Приписать его конструктору или первому
# методу значило бы соврать про то, кто зовёт.
TEMPLATE_MEMBER: Final[str] = "<шаблон>"


@dataclass(frozen=True)
class TemplateSource:
    """Откуда взят шаблон и что в нём написано."""

    where: str
    text: str
    inline: bool


@dataclass(frozen=True)
class TemplateCall:
    """Обращение из шаблона: к своему члену или к члену зависимости."""

    receiver: str
    member: str
    own: bool


def template_of(root: Path, symbol: Symbol) -> TemplateSource | None:
    """Шаблон компонента: внешний файл или строка в декораторе.

    Обе формы обрабатываются одинаково — разница только в том, откуда взят
    текст. Компонент с инлайновым шаблоном ничем не хуже: на боевом коде
    такие встречаются у мелких экранов и у обёрток.
    """
    for attribute in symbol.attributes:
        inline = attribute.named_args.get("template")
        if inline:
            return TemplateSource(where="<инлайн>", text=inline, inline=True)

        relative = attribute.named_args.get("templateUrl")
        if not relative:
            continue
        source = symbol.sources[0].path if symbol.sources else ""
        joined = posixpath.normpath(
            posixpath.join(posixpath.dirname(source), relative) if source else relative
        )
        path = root / joined
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            # Файла нет или он не читается: компонент остаётся, шаблона нет.
            return None
        return TemplateSource(where=joined, text=text, inline=False)
    return None


def expressions(text: str) -> list[str]:
    """Выражения шаблона: интерполяции и значения биндингов."""
    found = [match.group(1) for match in _INTERPOLATION.finditer(text)]
    found.extend(match.group("expression") for match in _BINDING.finditer(text))
    return [item.strip() for item in found if item.strip()]


def calls(text: str, members: set[str], receivers: set[str]) -> list[TemplateCall]:
    """Обращения шаблона, сверенные с объявленными именами.

    Сверка — единственная защита от ложных рёбер: в выражении шаблона
    встречаются и локальные переменные `*ngFor`, и имена директив, и поля
    чужих объектов. Имя, которого нет ни среди членов компонента, ни среди
    его зависимостей, ребром не становится.
    """
    found: dict[tuple[str, str], TemplateCall] = {}
    for expression in expressions(text):
        for receiver, member in _ACCESS.findall(expression):
            if receiver in receivers:
                found[(receiver, member)] = TemplateCall(
                    receiver=receiver, member=member, own=False
                )
            elif receiver == "this" and member in members:
                found[("", member)] = TemplateCall(receiver="", member=member, own=True)

        for name in _CALL.findall(expression):
            if name in members:
                found[("", name)] = TemplateCall(receiver="", member=name, own=True)

        for name in _NAME.findall(expression):
            # Голое имя без скобок — это обращение к полю: `service.ready$ | async`
            # и `{{ items$ | async }}` для страницы значат одно и то же.
            if name in members:
                found.setdefault(("", name), TemplateCall(receiver="", member=name, own=True))
    return [found[key] for key in sorted(found)]
