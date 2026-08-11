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

# Действия с ФАЙЛОМ — не то же, что решения агента, и потому отдельный набор
# и отдельный флаг. Путать их дорого: `--action write` отбирает документы,
# которые агенту предстоит написать, `--file-action update` — те, которые
# прогон перепишет прямо сейчас, и это разные множества.
FILE_ACTIONS: Final[frozenset[str]] = frozenset(
    {"create", "update", "unchanged", "refuse", "relocate"}
)

# Действия, при которых файл открывается на запись. По ним `current` остаётся
# в подробном списке: статус «документ в порядке» и «файл сейчас перепишут» —
# независимые вещи, а раньше вторая молча исчезала из отчёта вместе с первой.
WRITING_ACTIONS: Final[frozenset[str]] = frozenset({"create", "update", "relocate"})

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
    documents: list[PlannedDoc],
    paths: list[Path],
    actions: list[str],
    file_actions: list[str] | None = None,
) -> list[PlannedDoc]:
    """Сузить выборку. Принимаются и файлы, и каталоги.

    Фильтры складываются: `--action write --file-action update` — «агенту писать
    И файл будет переписан», а не объединение.
    """
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
    if file_actions:
        selected = [doc for doc in selected if doc.file_action in set(file_actions)]
    return selected


def document_json(doc: PlannedDoc) -> dict[str, Any]:
    """Запись одного документа. Публичная: её же берёт `docpipe worklist`.

    Копия этой функции разошлась бы с оригиналом на первом новом поле, и одна
    и та же ситуация получила бы в двух отчётах разные причины — а причина
    здесь единственное, по чему внешний исполнитель решает, что делать.
    """
    return {
        "action": doc.agent_action,
        # Что прогон сделает с файлом. Отдельно от `action`: тот про работу
        # агента, этот про запись, и совпадают они далеко не всегда — документ
        # со `skip` переписывается при смене владельца или домена.
        "file_action": doc.file_action,
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
                document_json(doc) for doc in sorted(documents, key=lambda item: item.doc_path)
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

    # `current` попадает в подробности, только если файл при этом переписывается:
    # смена владельца или домена меняет проекцию, документ остаётся в порядке,
    # а файл открывается на запись — и раньше об этом не было ни строки.
    detailed = [
        doc for doc in documents if doc.status != "current" or doc.file_action in WRITING_ACTIONS
    ]
    if detailed:
        lines.append("")
        for doc in detailed:
            team = f"  [{doc.team}]" if doc.team else ""
            lines.append(
                f"{doc.agent_action:<7} {doc.status:<11} {doc.file_action:<10} {doc.doc_path}{team}"
            )
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
