"""Обратимый разбор markdown-документа на зоны.

Инвариант, на котором стоит всё остальное:
**`assemble(parse_document(t)) == t` байт в байт.**

Из него следуют два правила реализации, каждое из которых легко нарушить
«упрощением»:

1. маркеры не восстанавливаются при сборке — хранится исходный текст сегмента;
2. строка признаётся маркером, только если состоит **из одного маркера целиком**.
   Отступ в четыре пробела — штатный способ показать маркер внутри блока кода,
   не превратив его в маркер.

Модуль не знает ни про .NET, ни про бизнес-слой: формат зон у них общий.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from docpipe.materialize.model import ParsedDocument, Segment

MANAGED_START = "<!-- docpipe:generated:start -->"
MANAGED_END = "<!-- docpipe:generated:end -->"

# Якорей `^…$` нет: выражения применяются к отдельной строке через fullmatch.
# Ведущие пробелы поэтому маркером не считаются — это и есть обходной путь
# «отступ в четыре пробела» для документации про сам docpipe.
_GENERATED_START = re.compile(r"<!--\s*docpipe:generated:start\s*-->\s*")
_GENERATED_END = re.compile(r"<!--\s*docpipe:generated:end\s*-->\s*")
_SECTION_START = re.compile(r"<!--\s*docpipe:section:start\s+([a-z][a-z0-9_]*)\s*-->\s*")
_SECTION_END = re.compile(r"<!--\s*docpipe:section:end\s+([a-z][a-z0-9_]*)\s*-->\s*")

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_LINES = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
_FENCE = "---"


class DocumentError(ValueError):
    """Порча структуры: границы авторского текста установить нельзя.

    Такой документ не чинится молча и не перезаписывается — единственный
    экземпляр принятого состояния живёт в нём самом.
    """


def _split_lines(text: str) -> list[str]:
    """Разбить на строки с сохранением их окончаний.

    Своё выражение вместо `splitlines(keepends=True)`: последний режет ещё и по
    `\\v`, `\\f`, `\\u2028` и подобным, и строка документа могла бы разъехаться
    там, где переводом строки не пахнет.
    """
    return _LINES.findall(text)


def is_section_empty(body: str) -> bool:
    """Пуста ли секция: после удаления HTML-комментариев ничего не осталось.

    Самая дорогая функция шага 2. Ошибка в сторону «не пусто» заставит агента
    счесть всё дерево написанным и обойти его впустую — отсюда правило, что
    подсказки в шаблонах бывают только комментариями.
    """
    return not _COMMENT.sub("", body).strip()


def _parse_front_matter(lines: list[str]) -> tuple[dict[str, Any] | None, int]:
    """Разобрать front matter. Возвращает данные и число занятых строк."""
    if not lines or lines[0].rstrip("\r\n") != _FENCE:
        return None, 0

    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") != _FENCE:
            continue

        raw = "".join(lines[1:index])
        try:
            data: Any = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise DocumentError(f"строка 1: front matter не разбирается как YAML: {exc}") from exc

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise DocumentError(
                f"строка 1: front matter должен быть словарём, получено {type(data).__name__}"
            )
        for key in ("docpipe", "docpipe_state"):
            if key in data and data[key] is not None and not isinstance(data[key], dict):
                raise DocumentError(f"строка 1: `{key}` должен быть словарём")
        return data, index + 1

    raise DocumentError("строка 1: front matter открыт `---`, но не закрыт")


def parse_document(text: str) -> ParsedDocument:
    """Разобрать документ на front matter и сегменты тела."""
    lines = _split_lines(text)
    front_matter, consumed = _parse_front_matter(lines)
    front_matter_text = "".join(lines[:consumed])

    segments: list[Segment] = []
    literal: list[str] = []
    open_kind: str | None = None
    open_name: str | None = None
    open_line = 0
    open_lines: list[str] = []
    seen_sections: set[str] = set()
    seen_generated = False

    def flush_literal() -> None:
        if literal:
            segments.append(Segment(kind="literal", body="".join(literal), text="".join(literal)))
            literal.clear()

    for offset, raw in enumerate(lines[consumed:]):
        number = consumed + offset + 1
        line = raw.rstrip("\r\n")

        if _GENERATED_START.fullmatch(line):
            if open_kind is not None:
                raise DocumentError(
                    f"строка {number}: генерируемый блок не может открываться внутри"
                    f" {'секции' if open_kind == 'section' else 'генерируемого блока'}"
                )
            if seen_generated:
                raise DocumentError(f"строка {number}: генерируемый блок в документе не один")
            seen_generated = True
            flush_literal()
            open_kind, open_name, open_line, open_lines = "generated", None, number, [raw]
            continue

        if _GENERATED_END.fullmatch(line):
            if open_kind != "generated":
                raise DocumentError(f"строка {number}: `generated:end` без `generated:start`")
            body = "".join(open_lines[1:])
            segments.append(Segment(kind="generated", body=body, text="".join(open_lines) + raw))
            open_kind, open_lines = None, []
            continue

        start = _SECTION_START.fullmatch(line)
        if start:
            name = start.group(1)
            if open_kind is not None:
                raise DocumentError(
                    f"строка {number}: секция `{name}` не может открываться внутри"
                    f" {'другой секции' if open_kind == 'section' else 'генерируемого блока'}"
                )
            if name in seen_sections:
                raise DocumentError(f"строка {number}: повтор секции `{name}`")
            seen_sections.add(name)
            flush_literal()
            open_kind, open_name, open_line, open_lines = "section", name, number, [raw]
            continue

        end = _SECTION_END.fullmatch(line)
        if end:
            name = end.group(1)
            if open_kind != "section":
                raise DocumentError(f"строка {number}: `section:end {name}` без открытия")
            if name != open_name:
                raise DocumentError(
                    f"строка {number}: закрывается `{name}`, а открыта была `{open_name}`"
                )
            body = "".join(open_lines[1:])
            segments.append(
                Segment(kind="section", name=name, body=body, text="".join(open_lines) + raw)
            )
            open_kind, open_name, open_lines = None, None, []
            continue

        (open_lines if open_kind else literal).append(raw)

    if open_kind == "generated":
        raise DocumentError(f"строка {open_line}: генерируемый блок не закрыт")
    if open_kind == "section":
        raise DocumentError(f"строка {open_line}: секция `{open_name}` не закрыта")

    flush_literal()
    return ParsedDocument(
        front_matter=front_matter, front_matter_text=front_matter_text, segments=segments
    )


def assemble(doc: ParsedDocument) -> str:
    """Собрать документ обратно."""
    return doc.front_matter_text + "".join(segment.text for segment in doc.segments)


def read_document(path: Path) -> tuple[str, ParsedDocument]:
    """Прочитать документ с диска.

    `utf-8-sig`, потому что `read_text(encoding="utf-8")` BOM не снимает: `---`
    оказывается не в позиции 0, front matter не находится, документ считается
    чужим — и при следующем прогоне превращается в сироту, а рядом появляется
    новый пустой.

    Переводы строк нормализуются **для сравнения**. Записывать надо всегда `\\n`,
    а сравнивать — нормализованное: иначе на репозитории с `core.autocrlf=true`
    каждый документ окажется изменённым при каждом прогоне.
    """
    text = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return text, parse_document(text)
