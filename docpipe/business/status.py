"""Состояние бизнес-документа и приёмка.

**Статус** описывает документ, **решение** — что делать агенту. Вычисляются
по порядку, первое сработавшее выигрывает; порядок несёт смысл и менять его
нельзя, не переписав таблицу в плане.

Приёмка фиксирует не текст, а **разрешённые факты**: что именно было сверено
с реализацией на момент, когда человек сказал «да, описано верно». Поэтому
`drifted` умеет назвать причину словами — «добавлен шаг NotifyStep», — а не
показать два хэша, между которыми читателю предлагается догадываться.
"""

from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.business.fingerprint import business_hash, hashed_anchors
from docpipe.business.model import BusinessDoc, Catalog
from docpipe.business.resolve import Resolution, ResolveContext, resolve_all
from docpipe.materialize.document import DocumentError, is_section_empty, read_document

Status = Literal["broken", "empty", "undeclared", "drifted", "current"]
Action = Literal["write", "review", "skip"]

STATUSES: Final[tuple[str, ...]] = ("broken", "empty", "undeclared", "drifted", "current")
ACTIONS: Final[tuple[str, ...]] = ("write", "review", "skip")

# Статуса `missing` у бизнес-слоя нет, хотя он и назван в плане. Документ здесь
# **и есть файл**: каталог собирается обходом `.md`, и «файла нет» означает
# «документа нет», а не «документ потерян». Сообщать о ненаписанном обязан
# отчёт линта о непокрытых точках входа — он считается по реестрам, то есть
# по единственному источнику, который знает, чего ещё не хватает. Второй
# источник истины про то, какие документы должны существовать, — ровно то,
# от чего отказались решением «каталог — это множество файлов».
#
# Статуса `orphan` нет по симметричной причине: лишний документ — нормальное
# состояние, а не дефект.


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DocumentStatus(_Base):
    doc_id: str
    doc_path: str
    title: str
    kind: str
    team: str | None = None
    status: Status
    action: Action
    reason: str = ""
    empty_sections: list[str] = Field(default_factory=list)


def accepted_state(doc: BusinessDoc, ctx: ResolveContext) -> dict[str, Any]:
    """Состояние, которое пишет приёмка.

    Хэш и факты берутся из **свежего разрешения**, а не из документа: front
    matter — зеркало и мог отстать. Взяв значение оттуда, приёмка зафиксировала
    бы устаревшее, и документ навсегда остался бы `current`, будучи `drifted`.

    Времени в состоянии нет: с ним `accept` перестал бы быть идемпотентным,
    и каждый его вызов давал бы дифф на пустом месте.
    """
    resolutions = resolve_all(hashed_anchors(doc), ctx)
    return {
        "business_hash": business_hash(resolutions),
        "entry": [_snapshot(item) for item in resolutions if item.anchor.verify],
        "resolved_at": None,
    }


def _snapshot(item: Resolution) -> dict[str, Any]:
    return {
        "kind": item.anchor.kind,
        "scope": item.anchor.scope or "",
        "ref": item.anchor.ref,
        "version": item.anchor.version or "",
        "facts": item.facts,
    }


def _key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("kind", "")),
        str(entry.get("scope", "")),
        str(entry.get("ref", "")),
        str(entry.get("version", "")),
    )


def changes(accepted: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Чем текущее состояние отличается от принятого, словами.

    Два хэша ничего не сообщают человеку, который открыл отчёт: между ними
    предлагается догадываться. Разница по фактам называет то, что произошло,
    и по ней сразу видно, надо ли вообще править текст.
    """
    was = {_key(entry): entry for entry in accepted.get("entry", [])}
    now = {_key(entry): entry for entry in current.get("entry", [])}

    lines: list[str] = []
    for key in sorted(now.keys() - was.keys()):
        lines.append(f"добавлена точка входа: {key[0]} {key[2] or '—'}")
    for key in sorted(was.keys() - now.keys()):
        lines.append(f"убрана точка входа: {key[0]} {key[2] or '—'}")

    for key in sorted(was.keys() & now.keys()):
        lines += _fact_changes(key[0], was[key].get("facts", {}), now[key].get("facts", {}))
    return lines


def _fact_changes(kind: str, was: dict[str, Any], now: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for field in sorted(set(was) | set(now)):
        before, after = was.get(field), now.get(field)
        if before == after:
            continue

        if field == "steps":
            lines += _named_changes("steps", _step_ids(before), _step_ids(after))
            lines += _step_link_changes(before, after)
        elif isinstance(before, list) or isinstance(after, list):
            # Отсутствие поля равно пустому списку. Без этого появление первой
            # записи в категории печаталось бы дампом списка вместо «добавлено
            # поле F» — и ровно в тот момент, когда сообщение нужнее всего.
            lines += _named_changes(field, before or [], after or [])
        else:
            lines.append(f"{kind}: {field} было {before!r}, стало {after!r}")
    return lines


# Формы согласуются с родом существительного явной таблицей, а не склейкой
# окончаний: «добавлен поле» в отчёте выглядит как дефект инструмента,
# а не как описание изменения.
_ITEM_NAMES: Final[dict[str, tuple[str, str]]] = {
    "steps": ("добавлен шаг", "убран шаг"),
    "methods": ("добавлен метод", "убран метод"),
    "assemblies": ("добавлена сборка", "убрана сборка"),
    "fields": ("добавлено поле", "убрано поле"),
}


def _step_ids(value: Any) -> list[Any]:
    return [str(step.get("id", "")) for step in value or [] if isinstance(step, dict)]


def _step_link_changes(before: Any, after: Any) -> list[str]:
    """Изменившийся переход между шагами. Порядок шагов бизнес-видим,
    и «поменялся местами» — это другое поведение процесса, а не косметика."""
    was = {
        str(s.get("id", "")): str(s.get("next", "")) for s in before or [] if isinstance(s, dict)
    }
    now = {str(s.get("id", "")): str(s.get("next", "")) for s in after or [] if isinstance(s, dict)}
    return [
        f"шаг {step}: переход был {was[step]!r}, стал {now[step]!r}"
        for step in sorted(was.keys() & now.keys())
        if was[step] != now[step]
    ]


def _named_changes(field: str, before: list[Any], after: list[Any]) -> list[str]:
    added_form, removed_form = _ITEM_NAMES.get(
        field, (f"добавлено в {field}", f"убрано из {field}")
    )
    lines = [f"{added_form} {_name(item)}" for item in after if item not in before]
    lines += [f"{removed_form} {_name(item)}" for item in before if item not in after]
    return lines


def _name(item: Any) -> str:
    return str(item[0]) if isinstance(item, list) and item else str(item)


# --------------------------------------------------------------------------------------
# Решение
# --------------------------------------------------------------------------------------


def decide(doc: BusinessDoc, root: Path, ctx: ResolveContext) -> DocumentStatus:
    """Что делать с документом. Порядок условий фиксирован."""

    def verdict(
        status: Status, action: Action, reason: str = "", empty: list[str] | None = None
    ) -> DocumentStatus:
        return DocumentStatus(
            doc_id=doc.id,
            doc_path=doc.doc_path,
            title=doc.title,
            kind=doc.kind,
            team=doc.owner_team,
            status=status,
            action=action,
            reason=reason,
            empty_sections=empty or [],
        )

    try:
        _, parsed = read_document(root / doc.doc_path)
    except (OSError, DocumentError) as exc:
        return verdict("broken", "review", str(exc))

    empty = [
        name
        for name in parsed.section_names
        if (segment := parsed.section(name)) is not None and is_section_empty(segment.body)
    ]
    if empty:
        return verdict("empty", "write", "документ не наполнен: " + ", ".join(empty), empty)

    resolutions = resolve_all(hashed_anchors(doc), ctx)
    unresolved = [item.anchor for item in resolutions if item.anchor.verify and not item.resolved]
    if unresolved:
        listed = ", ".join(f"{a.kind} {a.display}" for a in unresolved)
        return verdict("broken", "review", f"точка входа не найдена: {listed}")

    accepted = (parsed.state or {}).get("accepted")
    if not isinstance(accepted, dict):
        return verdict("undeclared", "review", "приёмки не было: текст не сверялся с реализацией")

    current = accepted_state(doc, ctx)
    if accepted.get("business_hash") != current["business_hash"]:
        what = "; ".join(changes(accepted, current)) or "состав разрешённых фактов изменился"
        return verdict("drifted", "review", f"состав изменился: {what}")

    return verdict("current", "skip")


def statuses(catalog: Catalog, root: Path, ctx: ResolveContext) -> list[DocumentStatus]:
    """Состояние всего каталога, отсортированное по пути документа."""
    return sorted((decide(doc, root, ctx) for doc in catalog.docs), key=lambda item: item.doc_path)


def format_statuses(items: list[DocumentStatus]) -> str:
    """Текстовый отчёт: счётчики, затем перечень."""
    counted = {status: sum(item.status == status for item in items) for status in STATUSES}
    lines = ["Документов: " + str(len(items))]
    lines += [f"  {status:<11} {count:>5}" for status, count in counted.items() if count]

    if items:
        lines.append("")
    for item in items:
        team = f"  [{item.team}]" if item.team else ""
        lines.append(f"{item.action:<7} {item.status:<11} {item.doc_path}{team}")
        if item.reason:
            lines.append(f"        {item.reason}")
    return "\n".join(lines)
