"""Отчёт `docs status`: вход агента шага 3.

Команда информационная и **ничего не пишет**. Соблазн «заодно починить front
matter» превращает её в изменяющую, и её перестают гонять в CI.
"""

from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.hashing import stable_json_dumps
from docpipe.materialize.plan import MaterializePlan, PlannedDoc

# Версия конверта `docs status --format json`. Своя, не связана ни с очередью
# (`worklist`), ни с манифестом: у них разные потребители и разные поводы
# менять формат.
SCHEMA_VERSION: Final[Literal["1.0"]] = "1.0"

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


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubstitutedTemplate(_Base):
    """Вид, документированный базовым скелетом за неимением своего, и сколько узлов."""

    template: str
    count: int


class DocumentProblem(_Base):
    """Почему файл трогать нельзя (`PlannedDoc.error`), по одному документу."""

    doc_path: str
    error: str


class StatusReport(_Base):
    """Конверт `docs status --format json`.

    Записи документов — словари `document_json`, а не модель: их форму держит
    `WorklistEntry` (`extra="forbid"`), и вторая модель той же записи здесь
    разошлась бы с ней на первом поле. Всё новое — только в конверте: новое
    поле в записи уронило бы `worklist`, который собирает очередь из неё же.

    `notes`, `substituted` и `document_errors` текст печатал всегда, а JSON
    терял: агент, читавший JSON, не видел ни замечаний по переносам, ни
    подстановки скелета, ни причины отказа — только статус `broken`.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    counts: dict[str, int]
    documents: list[dict[str, Any]]
    errors: list[str]
    manifest_partial: bool
    total: int
    notes: list[str]
    substituted: list[SubstitutedTemplate]
    document_errors: list[DocumentProblem]


def substituted_templates(plan: MaterializePlan) -> list[SubstitutedTemplate]:
    """Подстановки скелета списком: чаще — выше, при равенстве по имени.

    Общая для `docs status` и `materialize`: порядок задан ключом здесь,
    а не порядком словаря в плане.
    """
    return [
        SubstitutedTemplate(template=name, count=count)
        for name, count in sorted(plan.substituted.items(), key=lambda item: (-item[1], item[0]))
    ]


def status_report(plan: MaterializePlan, documents: list[PlannedDoc]) -> StatusReport:
    """Отчёт по выборке. Счётчики и записи — по ней, `notes` и подстановки — по плану.

    `notes` и `substituted` описывают прогон, а не документ, и сузить их
    фильтром нечем: замечание о переносе не привязано к одному пути.
    """
    ordered = sorted(documents, key=lambda item: item.doc_path)
    counted = Counter(doc.status for doc in documents)
    return StatusReport(
        counts=dict(sorted(counted.items())),
        documents=[document_json(doc) for doc in ordered],
        errors=list(plan.errors),
        manifest_partial=plan.manifest_partial,
        total=len(documents),
        notes=list(plan.notes),
        substituted=substituted_templates(plan),
        document_errors=[
            DocumentProblem(doc_path=doc.doc_path, error=doc.error) for doc in ordered if doc.error
        ],
    )


def format_status_json(plan: MaterializePlan, documents: list[PlannedDoc]) -> str:
    """Машинный вывод. Через `stable_json_dumps`, поэтому два вызова совпадают
    байт в байт — иначе агент увидит изменение там, где его нет."""
    return stable_json_dumps(status_report(plan, documents).model_dump(mode="json")).rstrip("\n")


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
