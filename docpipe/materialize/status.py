"""Отчёт `docs status`: вход агента шага 3.

Команда информационная и **ничего не пишет**. Соблазн «заодно починить front
matter» превращает её в изменяющую, и её перестают гонять в CI.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from docpipe.hashing import stable_json_dumps
from docpipe.materialize.plan import MaterializePlan, PlannedDoc

AGENT_ACTIONS: Final[frozenset[str]] = frozenset({"write", "review", "skip"})
STATUSES: Final[frozenset[str]] = frozenset(
    {
        "missing",
        "empty",
        "undeclared",
        "stale",
        "drifted",
        "relocated",
        "current",
        "broken",
        "orphan",
    }
)


def filter_documents(
    documents: list[PlannedDoc], paths: list[Path], actions: list[str]
) -> list[PlannedDoc]:
    """Сузить выборку. Принимаются и файлы, и каталоги."""
    selected = documents
    if paths:
        prefixes = [path.as_posix().rstrip("/") for path in paths]
        selected = [
            doc
            for doc in selected
            if any(
                doc.doc_path == prefix or doc.doc_path.startswith(prefix + "/")
                for prefix in prefixes
            )
        ]
    if actions:
        selected = [doc for doc in selected if doc.agent_action in set(actions)]
    return selected


def _document_json(doc: PlannedDoc) -> dict[str, Any]:
    return {
        "action": doc.agent_action,
        "reason": doc.reason,
        "changes": asdict(doc.changes),
        "doc_path": doc.doc_path,
        "node_id": doc.node_id,
        "status": doc.status,
        "kind": doc.kind,
        "template_ref": doc.template_ref,
        "example_ref": doc.example_ref,
        "team": doc.team,
        "empty_sections": doc.empty_sections,
        "orphan_sections": doc.orphan_sections,
        "sources": [source.model_dump(mode="json") for source in doc.sources],
        "broken_links": doc.broken_links,
    }


def format_status_json(plan: MaterializePlan, documents: list[PlannedDoc]) -> str:
    """Машинный вывод. Через `stable_json_dumps`, поэтому два вызова совпадают
    байт в байт — иначе агент увидит изменение там, где его нет."""
    from collections import Counter

    counted = Counter(doc.status for doc in documents)
    return stable_json_dumps(
        {
            "counts": dict(sorted(counted.items())),
            "documents": [
                _document_json(doc) for doc in sorted(documents, key=lambda item: item.doc_path)
            ],
            "errors": plan.errors,
            "manifest_partial": plan.manifest_partial,
            "total": len(documents),
        }
    ).rstrip("\n")


def format_status(plan: MaterializePlan, documents: list[PlannedDoc]) -> str:
    """Текстовый вывод. `current` попадает только в счётчик.

    На АС CF подробный список по всем документам — тысячи строк, и в них
    теряется то немногое, ради чего команду и звали.
    """
    if plan.errors:
        return "\n".join(["Блокирующие ошибки:", *(f"  {error}" for error in plan.errors)])

    counts = MaterializePlan(documents=documents).counts()
    lines = ["Состояние документов:"]
    lines += [f"  {status:<12} {count:>5}" for status, count in counts.items()]
    lines.append(f"  {'всего':<12} {len(documents):>5}")

    detailed = [doc for doc in documents if doc.status != "current"]
    if detailed:
        lines.append("")
        for doc in detailed:
            team = f"  [{doc.team}]" if doc.team else ""
            lines.append(f"{doc.agent_action:<7} {doc.status:<11} {doc.doc_path}{team}")
            if doc.reason:
                lines.append(f"        {doc.reason}")
            if doc.empty_sections:
                lines.append(f"        пустые секции: {', '.join(doc.empty_sections)}")
            if doc.orphan_sections:
                lines.append(f"        не из шаблона: {', '.join(doc.orphan_sections)}")
            if doc.broken_links:
                lines.append(f"        битые ссылки: {', '.join(doc.broken_links)}")
            if doc.error:
                lines.append(f"        {doc.error}")

    if plan.notes:
        lines += ["", "Замечания по переносам:"] + [f"  {note}" for note in plan.notes]

    if plan.manifest_partial:
        lines += [
            "",
            "Внимание: манифест частичный (--scope). Статусы вне скоупа недостоверны:"
            " узлы там взяты из предыдущего прогона.",
        ]

    return "\n".join(lines)
