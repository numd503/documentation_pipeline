"""Очередь документов для внешнего исполнителя шага 3 (M13).

`docs status` отвечает человеку и CI, `worklist` — чужому процессу. Отсюда две
вещи, которых у `docs status` нет и быть не может без слома её потребителей:

* **конверт с версией формата** — иначе внешний модуль не отличит формат
  следующей версии от текущего и не проверит, что читает файл от того самого
  манифеста;
* **сводка по всему дереву рядом с суженным списком** — у `docs status` фильтр
  сужает и счётчики, поэтому «что с деревом» и «что отдать агенту» в один её
  вывод не помещаются.

Времени в файле нет намеренно: с ним файл менялся бы на каждом прогоне, его
нельзя было бы ни сравнить между прогонами, ни закоммитить без шума, а тест
на детерминизм превратился бы в сравнение с исключением полей. Ровно поэтому
недетерминированное шага 1 живёт в `doc-tree.run.json`, а не в манифесте.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.materialize.plan import STATUS_ORDER, MaterializePlan, PlannedDoc
from docpipe.materialize.status import document_json
from docpipe.model import SourceSpan

# 1.1 — добавлено поле `file_action` у записи. Добавление поля версию всё
# равно двигает: конверт затем и заведён, чтобы читающий не гадал, тот ли
# это формат, а строгий читатель ломается и на новом ключе.
SCHEMA_VERSION: Final[Literal["1.1"]] = "1.1"

# Что попадает в очередь без явного `--action`. `skip` не попадает: очередь —
# это список работы, а не срез дерева.
DEFAULT_ACTIONS: Final[tuple[str, ...]] = ("write", "review")

_PRIORITY: Final[dict[str, int]] = {status: index for index, status in enumerate(STATUS_ORDER)}


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntryChanges(_Base):
    """Что изменилось — чтобы исполнитель не перечитывал манифест."""

    members_added: list[str] = Field(default_factory=list)
    members_removed: list[str] = Field(default_factory=list)
    kind_changed: str | None = None
    relocated_from: str | None = None


class WorklistEntry(_Base):
    """Одна запись очереди.

    Собирается **только** через `document_json`, поэтому совпадает с записью
    `docs status --format json` поле в поле. `extra="forbid"` держит это
    свойство: новое поле там уронит валидацию здесь, и о расхождении станет
    известно на тестах, а не у потребителя.
    """

    action: str
    # Что прогон сделает с файлом; `action` — что делать агенту.
    # Исполнитель шага 3 по нему видит, materialize уже отработал или ещё нет.
    file_action: str
    reason: str
    status: str
    doc_path: str
    node_id: str | None
    kind: str
    template_ref: str
    example_ref: str | None
    team: str | None
    sources: list[SourceSpan] = Field(default_factory=list)
    empty_sections: list[str] = Field(default_factory=list)
    orphan_sections: list[str] = Field(default_factory=list)
    broken_links: list[str] = Field(default_factory=list)
    changes: EntryChanges = Field(default_factory=EntryChanges)


class WorklistTotals(_Base):
    """Без этой тройки внешний модуль не отличит «работы нет» от «работу отфильтровали»."""

    documents: int
    selected: int
    truncated: bool


class Worklist(_Base):
    """Файл очереди целиком."""

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    docs_root: str
    modules_root: str
    ruleset_version: str
    manifest_sha256: str
    manifest_partial: bool
    # «В очереди есть документы, которых на диске ещё нет». Без этого флага
    # исполнитель откроет `doc_path` со статусом `missing`, не найдёт файла
    # и решит, что очередь битая, — вместо того чтобы позвать `materialize`.
    needs_materialize: bool
    counts: dict[str, int]
    totals: WorklistTotals
    documents: list[WorklistEntry] = Field(default_factory=list)


def select_documents(
    documents: list[PlannedDoc],
    actions: tuple[str, ...] = DEFAULT_ACTIONS,
    teams: tuple[str, ...] = (),
    limit: int | None = None,
) -> tuple[list[PlannedDoc], bool]:
    """Отобрать и упорядочить очередь. Возвращает `(записи, обрезано ли)`.

    Порядок — по приоритету статуса, при равенстве по `doc_path`. Он отличается
    от `docs status`, где сортировка только по пути, и отличается намеренно:
    без приоритета `--limit` резал бы очередь по алфавиту и отдавал исполнителю
    `Api/…` вместо сломанного документа.
    """
    selected = [doc for doc in documents if doc.agent_action in set(actions)]
    if teams:
        selected = [doc for doc in selected if doc.team in set(teams)]

    ordered = sorted(selected, key=lambda doc: (_PRIORITY[doc.status], doc.doc_path))
    if limit is not None and len(ordered) > limit:
        return ordered[:limit], True
    return ordered, False


def build_worklist(
    plan: MaterializePlan,
    selected: list[PlannedDoc],
    *,
    docs_root: str,
    modules_root: str,
    ruleset_version: str,
    manifest_sha256: str,
    truncated: bool,
) -> Worklist:
    """Собрать очередь. Ничего не пишет.

    `counts` и `totals.documents` считаются по **всему** плану: ни `--action`,
    ни `--team`, ни `--limit` на них не влияют. Иначе прогон с `--team` объявил
    бы, что в дереве двадцать документов, и эта цифра ушла бы в отчёт.
    """
    return Worklist(
        docs_root=docs_root,
        modules_root=modules_root,
        ruleset_version=ruleset_version,
        manifest_sha256=manifest_sha256,
        manifest_partial=plan.manifest_partial,
        needs_materialize=any(doc.status == "missing" for doc in selected),
        counts=plan.counts(),
        totals=WorklistTotals(
            documents=len(plan.documents), selected=len(selected), truncated=truncated
        ),
        documents=[WorklistEntry.model_validate(document_json(doc)) for doc in selected],
    )


__all__ = [
    "DEFAULT_ACTIONS",
    "SCHEMA_VERSION",
    "EntryChanges",
    "Worklist",
    "WorklistEntry",
    "WorklistTotals",
    "build_worklist",
    "select_documents",
]
