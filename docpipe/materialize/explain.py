"""Трассировка одного документа: почему с ним сделают именно это.

`docs status` отвечает про дерево и группирует по статусу; здесь ответ про один
файл и по шагам. Вопросов, ради которых модуль заведён, три, и ни на один
из них отчёт по дереву не отвечает:

* документ на диске есть, а прогон считает, что его нет — **какой из фильтров
  обхода его отбросил**;
* почему `update`, а не `create` — и **чем именно** собранный текст отличается
  от файла;
* не задевает ли перезапись авторские секции. Это главный инвариант шага 2,
  и увидеть его нарушение надо строкой в отчёте, а не выловить из `git diff`
  на тысяче документов.

Сравнение идёт по зонам, а не построчно: у зон разная судьба при прогоне
(проекция пересобирается, генерируемый блок пересобирается, авторский текст
не трогается никогда), поэтому и разница в них значит разное.
"""

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from docpipe.discovery import matches_glob
from docpipe.documents.model import ParsedDocument
from docpipe.documents.zones import DocumentError, parse_document, read_document
from docpipe.materialize.plan import (
    SCHEMA_PREFIX,
    PlannedDoc,
    opens_front_matter,
)

# Правило, по которому выбрано действие с файлом. Формулировки здесь, а не
# в отчёте: их читают, чтобы понять поведение, и разойтись с кодом они не должны.
FILE_ACTION_RULES: Final[dict[str, str]] = {
    "create": "обход не нашёл файла на этом пути",
    "update": "файл есть, и собранный текст от него отличается",
    "unchanged": "собранный текст совпадает с файлом, файл не открывается на запись",
    "relocate": "узел сопоставлен с файлом на другом пути",
    "refuse": "файл трогать нельзя",
}


def scan_verdict(path: Path, root: Path, globs: list[str]) -> str | None:
    """Почему обход документов не примет этот файл. `None` — примет.

    Фильтры повторяют `scan_docs` по одному, но каждый со своей формулировкой:
    сам обход про отсев молчит, ему достаточно ответа «да или нет». Порядок
    тот же, что в обходе, иначе причина будет названа не та, что сработала.
    """
    rel = path.relative_to(root).as_posix()

    for glob in globs:
        if matches_glob(rel, glob):
            return f"отброшен шаблоном обхода `{glob}` (docs_scan_exclude либо встроенный)"

    try:
        head = path.read_bytes()[:8]
    except OSError as exc:
        return f"файл не читается: {exc}"

    if not opens_front_matter(head):
        if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return "файл в UTF-16, а не в UTF-8"
        return f"файл не открывается строкой `---`: первые байты {head[:4]!r}"

    try:
        _, parsed = read_document(path)
    except DocumentError as exc:
        return f"структура документа испорчена: {exc}"
    except OSError as exc:
        return f"файл не читается: {exc}"

    schema = parsed.schema_id or ""
    if not schema.startswith(SCHEMA_PREFIX):
        return f"`docpipe.schema` = {schema or 'нет'}, а нужен префикс `{SCHEMA_PREFIX}`"
    if not (parsed.docpipe or {}).get("node_id"):
        return "во front matter нет `docpipe.node_id`"

    return None


# --------------------------------------------------------------------------------------
# Разница по зонам
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneDiff:
    """Что меняется в документе, разложенное по зонам."""

    front_matter: list[str] = field(default_factory=list)
    generated_changed: bool = False
    sections_added: list[str] = field(default_factory=list)
    sections_changed: list[str] = field(default_factory=list)
    sections_removed: list[str] = field(default_factory=list)

    @property
    def touches_authored(self) -> bool:
        """Задевает ли перезапись авторский текст.

        Изменение или пропажа секции означает нарушение главного инварианта
        шага 2. Дописанная секция — нет: скелет новой секции шаблона приходит
        пустым и в конец.
        """
        return bool(self.sections_changed or self.sections_removed)

    @property
    def empty(self) -> bool:
        return not (
            self.front_matter
            or self.generated_changed
            or self.sections_added
            or self.sections_changed
            or self.sections_removed
        )


def _render(value: Any) -> str:
    """Значение front matter одной строкой, обрезанное.

    Списки `sources` и `members` бывают длинными, а в отчёте важно не значение
    целиком, а факт «поменялось».
    """
    text = str(value).replace("\n", " ")
    return text if len(text) <= 60 else text[:57] + "…"


def _flatten(data: dict[str, Any] | None, prefix: str = "") -> dict[str, str]:
    """Front matter в плоский вид `ключ.подключ → значение строкой`."""
    flat: dict[str, str] = {}
    for key, value in (data or {}).items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat |= _flatten(value, f"{name}.")
        else:
            flat[name] = _render(value)
    return flat


def _front_matter_changes(before: ParsedDocument, after: ParsedDocument) -> list[str]:
    old = _flatten(before.front_matter)
    new = _flatten(after.front_matter)
    lines = []
    for key in sorted(set(old) | set(new)):
        if key not in new:
            lines.append(f"{key}: {old[key]} → удалено")
        elif key not in old:
            lines.append(f"{key}: добавлено = {new[key]}")
        elif old[key] != new[key]:
            lines.append(f"{key}: {old[key]} → {new[key]}")
    return lines


def _sections(parsed: ParsedDocument) -> dict[str, str]:
    return {
        segment.name: segment.body
        for segment in parsed.segments
        if segment.kind == "section" and segment.name
    }


def _generated(parsed: ParsedDocument) -> str:
    return "".join(segment.body for segment in parsed.segments if segment.kind == "generated")


def zone_diff(before_text: str, after_text: str) -> ZoneDiff:
    """Сравнить два текста документа по зонам.

    Оба разбираются одним и тем же `parse_document`, поэтому сравниваются
    именно зоны, а не строки, случайно оказавшиеся рядом.
    """
    before, after = parse_document(before_text), parse_document(after_text)
    old, new = _sections(before), _sections(after)

    return ZoneDiff(
        front_matter=_front_matter_changes(before, after),
        generated_changed=_generated(before) != _generated(after),
        sections_added=sorted(set(new) - set(old)),
        sections_removed=sorted(set(old) - set(new)),
        sections_changed=sorted(name for name in set(old) & set(new) if old[name] != new[name]),
    )


def unified(before_text: str, after_text: str) -> str:
    """Обычный unified diff — для `--diff`, когда разбивки по зонам мало."""
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(True),
            after_text.splitlines(True),
            "на диске",
            "будет записано",
            n=1,
        )
    )


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def _zone_lines(diff: ZoneDiff) -> list[str]:
    lines = ["", "Что изменится:"]

    if diff.front_matter:
        lines.append("  front matter:")
        lines += [f"    {line}" for line in diff.front_matter]
    else:
        lines.append("  front matter:      без изменений")

    lines.append(
        f"  генерируемый блок: {'пересобран' if diff.generated_changed else 'без изменений'}"
    )

    if diff.sections_added:
        lines.append(f"  дописаны секции:   {', '.join(diff.sections_added)}")
    if diff.touches_authored:
        # Не «изменение», а нарушение инварианта: авторский текст не затирается
        # никогда, и увидеть это надо здесь, а не в `git diff` постфактум.
        removed = [f"{name} (удалена)" for name in diff.sections_removed]
        changed = ", ".join(diff.sections_changed + removed)
        lines += [
            f"  АВТОРСКИЕ СЕКЦИИ:  {changed}",
            "    Это дефект: авторский текст шаг 2 не трогает. Не применяйте прогон,"
            " сохраните документ и заведите issue.",
        ]
    elif not diff.sections_added:
        lines.append("  авторские секции:  не тронуты")

    return lines


def format_explain_document(
    doc_path: str,
    doc: PlannedDoc | None,
    root: Path,
    globs: list[str],
    show_diff: bool = False,
) -> str:
    """Отчёт по одному документу."""
    path = root / doc_path
    exists = path.is_file()
    lines = [doc_path, ""]

    if doc is None:
        lines.append("В плане такого документа нет: ни узла с таким `doc_path`, ни файла,")
        lines.append("который обход счёл бы документом шага 2.")
        if exists:
            reason = scan_verdict(path, root, globs) or "принят обходом"
            lines += ["", f"файл на диске:      есть, но {reason}"]
        else:
            lines += ["", "файл на диске:      отсутствует"]
        return "\n".join(lines)

    lines.append(f"файл на диске:      {'есть' if exists else 'отсутствует'}")
    if exists:
        verdict = scan_verdict(path, root, globs)
        lines.append(f"обход документов:   {verdict or 'файл принят'}")

    rule = FILE_ACTION_RULES.get(doc.file_action, "")
    lines.append(f"действие с файлом:  {doc.file_action} — {rule}")
    lines.append(f"статус документа:   {doc.status}" + (f" — {doc.reason}" if doc.reason else ""))
    lines.append(f"решение агенту:     {doc.agent_action}")
    if doc.node_id:
        lines.append(f"узел:               {doc.node_id}")
    if doc.team:
        lines.append(f"команда:            {doc.team}")
    if doc.relocate_from:
        lines.append(f"перенос:            {doc.relocate_from} → {doc.doc_path} ({doc.confidence})")
    if doc.error:
        lines.append(f"причина отказа:     {doc.error}")
    if doc.empty_sections:
        lines.append(f"пустые секции:      {', '.join(doc.empty_sections)}")
    if doc.orphan_sections:
        lines.append(f"не из шаблона:      {', '.join(doc.orphan_sections)}")

    if doc.content is None or not exists:
        return "\n".join(lines)

    before = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    lines += _zone_lines(zone_diff(before, doc.content))

    if show_diff:
        lines += ["", unified(before, doc.content).rstrip("\n")]

    return "\n".join(lines)


__all__ = [
    "FILE_ACTION_RULES",
    "ZoneDiff",
    "format_explain_document",
    "scan_verdict",
    "unified",
    "zone_diff",
]
