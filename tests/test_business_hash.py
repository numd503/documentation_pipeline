"""`business_hash`: что создаёт работу, а что нет (B05).

Ядро всей затеи проверяется парами «должно и не должно». Требование постановки
формулируется одной строкой — **рефакторинг без смены бизнес-смысла не должен
давать `review`** — и здесь оно превращается в проверяемое свойство: для каждой
пары одно изменение хэш меняет, а внешне похожее на него — нет.

Правки вносятся в копию дерева реестров и перечитываются целиком: проверяется
вся цепочка «файл → читатель → якорь → факты → хэш», а не одна функция.
"""

from pathlib import Path

import pytest

from docpipe.business import business_hash, hashed_anchors, load_catalog, resolve_all
from docpipe.business.model import Anchor
from docpipe.business.resolve import ResolveContext
from docpipe.model import DocNode
from tests.business_support import (
    BUSINESS_ROOT,
    context,
    edit,
    manifest,
    member,
    node,
    nodes,
    registries_copy,
)

WORKFLOW = Anchor(kind="workflow", ref="SampleWorkflow", version="2")
JOB = Anchor(kind="job", ref="PM: Load limits")
GRID = Anchor(kind="grid_service", ref="ICalcResult")
EVENT = Anchor(kind="list_event", ref="ItemAdded", scope="UserTasks")
TABLE = Anchor(kind="table", ref="UserTasks")


def digest(anchors: list[Anchor], ctx: ResolveContext) -> str:
    return business_hash(resolve_all(anchors, ctx))


@pytest.fixture
def ctx() -> ResolveContext:
    return context()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return registries_copy(tmp_path)


# --------------------------------------------------------------------------------------
# Рефакторинг без смены смысла: хэш не меняется
# --------------------------------------------------------------------------------------


def test_refactoring_does_not_change_business_hash(tree: Path, ctx: ResolveContext) -> None:
    """Переименование класса реализации, класса шага, перенос типа в другой
    namespace и смена `ruleset_version` не меняют `business_hash` ни в одном
    из видов якорей.

    Каждое из этих изменений — обычная работа разработчика, о которой аналитик
    не узнаёт и узнавать не должен. Если хоть одно даёт `review`, отчёт
    становится шумом и его перестают читать.
    """
    before = digest([WORKFLOW, JOB, GRID, EVENT, TABLE], ctx)

    # 1. Переименован класс реализации grid-сервиса — и в реестре, и в коде.
    edit(
        tree / "services.config",
        "Sbt.Cashflow.Grid.Services.CalcResult.CalcResult",
        "Sbt.Cashflow.Grid.Services.CalcResult.CalcResultV2",
    )
    # 2. Переименован класс шага workflow.
    edit(
        tree / "deployment/Data/Items/Workflows/Sample.v2.json",
        "Sbt.Sample.Steps.ScoreStep",
        "Sbt.Sample.Steps.ScoringStep",
    )
    # 3. Переименован класс обработчика события внутри той же сборки.
    edit(
        tree / "Structure.xml",
        "UserTasksAddedTriggerSampleWorkflowEventReceiver",
        "UserTasksAddedStartsScoringEventReceiver",
    )

    renamed: list[DocNode] = [
        item
        for item in nodes()
        if "CalcResult.CalcResult" not in (item.symbol.fqn if item.symbol else "")
    ]
    renamed.append(
        node(
            "Sbt.Cashflow.Grid.Services.CalcResult.CalcResultV2",
            members=[member("Calc"), member("Recalc"), member("Prepare", public=False)],
        )
    )
    # 4. Тип перенесён в другой namespace, 5. сменилась версия набора правил.
    renamed = [item for item in renamed if item.title != "SampleAggregate"]
    renamed.append(node("Sbt.Sample.Domain.Models.SampleAggregate"))

    after = digest(
        [WORKFLOW, JOB, GRID, EVENT, TABLE],
        context(tree, manifest(renamed, ruleset_version="2026-08-01.1")),
    )

    assert after == before


def test_reclassification_does_not_change_business_hash(ctx: ResolveContext) -> None:
    """`doc_path` меняется от правки `rules/dotnet.yaml`, когда код не трогали.

    Ровно поэтому идентичность документа в хэш и не входит: иначе смена вида
    сущности в наборе правил порождала бы работу по всему каталогу.
    """
    before = digest([WORKFLOW, GRID], ctx)

    reclassified = [
        item.model_copy(
            update={
                "kind": "provider",
                "template": "provider",
                "doc_path": item.doc_path.replace("/services/", "/providers/"),
            }
        )
        for item in nodes()
    ]
    after = digest([WORKFLOW, GRID], context(resolved=manifest(reclassified, "2026-08-01.1")))

    assert after == before


def test_field_order_does_not_change_business_hash(tree: Path, ctx: ResolveContext) -> None:
    """Перестановка полей списка — форматирование XML, а не изменение состава."""
    before = digest([TABLE], ctx)

    # Текст поля повторяет фикстуру дословно, включая выравнивание переноса:
    # ищется он в файле, а не сравнивается по смыслу.
    experimental = (
        '<FieldSomethingNew InnerName="UserTasksExperimental" ColumnName="EXPERIMENTAL"\n'
        + " " * 35
        + 'DisplayName="Поле неизвестного вида" Required="false" />'
    )
    edit(tree / "Structure.xml", experimental + "\n            </Fields>", "</Fields>")
    edit(tree / "Structure.xml", "<Fields>", "<Fields>\n                " + experimental)

    assert digest([TABLE], context(tree)) == before


def test_anchor_order_does_not_change_business_hash(ctx: ResolveContext) -> None:
    """Перевёрнутый порядок якорей во front matter даёт тот же хэш: документ
    правят руками постоянно, и перестановка двух строк не является событием."""
    assert digest([WORKFLOW, JOB, GRID], ctx) == digest([GRID, JOB, WORKFLOW], ctx)


# --------------------------------------------------------------------------------------
# Смена контракта: хэш меняется
# --------------------------------------------------------------------------------------


def test_contract_change_does_change_business_hash(tree: Path, ctx: ResolveContext) -> None:
    """Добавление шага, изменение `NextStepId`, изменение `Interval`,
    добавление поля списка и переименование метода grid-сервиса — меняют.

    Каждое из пяти проверяется отдельно, а не пачкой: пачка прошла бы и в том
    случае, когда работает только одно из пяти.
    """
    # Добавление шага.
    added_step = registries_copy(tree.parent / "added-step")
    edit(
        added_step / "deployment/Data/Items/Workflows/Sample.v2.json",
        '{\n      "Id": "ScoreStep",',
        '{\n      "Id": "NotifyStep",\n'
        '      "StepType": "Sbt.Sample.Steps.NotifyStep, Sbt.Sample"\n'
        '    },\n    {\n      "Id": "ScoreStep",',
    )
    assert digest([WORKFLOW], context(added_step)) != digest([WORKFLOW], ctx)

    # Изменение перехода: порядок шагов бизнес-видим.
    reordered = registries_copy(tree.parent / "reordered")
    edit(
        reordered / "deployment/Data/Items/Workflows/Sample.v2.json",
        '"NextStepId": "ThresholdStep"',
        '"NextStepId": "ScoreStep"',
    )
    assert digest([WORKFLOW], context(reordered)) != digest([WORKFLOW], ctx)

    # Изменение расписания джоба.
    rescheduled = registries_copy(tree.parent / "rescheduled")
    edit(
        rescheduled / "deployment/Data/Items/Items_Basic.xml",
        "Interval=&quot;86400",
        "Interval=&quot;3600",
    )
    assert digest([JOB], context(rescheduled)) != digest([JOB], ctx)

    # Добавление поля списка.
    extended = registries_copy(tree.parent / "extended")
    edit(
        extended / "Structure.xml",
        "</Fields>",
        '    <FieldText InnerName="UserTasksComment" ColumnName="COMMENT"\n'
        '                           DisplayName="Комментарий" Required="false" />\n'
        "            </Fields>",
    )
    assert digest([TABLE], context(extended)) != digest([TABLE], ctx)

    # Переименование метода grid-сервиса: методы вызывают по имени через прокси.
    renamed = [item for item in nodes() if item.title != "CalcResult"]
    renamed.append(
        node(
            "Sbt.Cashflow.Grid.Services.CalcResult.CalcResult",
            members=[member("Calculate"), member("Recalc")],
        )
    )
    assert digest([GRID], context(resolved=manifest(renamed))) != digest([GRID], ctx)


def test_new_handler_assembly_changes_hash_but_rename_inside_it_does_not(
    tree: Path, ctx: ResolveContext
) -> None:
    """Участники события — сборки, а не классы.

    Появление обработчика из новой сборки означает, что в процесс вошла другая
    команда; переименование класса внутри той же сборки не означает ничего.
    """
    before = digest([EVENT], ctx)

    added = registries_copy(tree.parent / "added-handler")
    edit(
        added / "Structure.xml",
        "</EventReceivers>",
        '    <EventReceiver Class="Sbt.Cashflow.Audit.EventReceivers.AuditEventReceiver"\n'
        '                               Assembly="Sbt.Cashflow.Audit.EventReceivers"\n'
        '                               EventType="ItemAdded" />\n'
        "            </EventReceivers>",
    )
    assert digest([EVENT], context(added)) != before

    renamed = registries_copy(tree.parent / "renamed-handler")
    edit(
        renamed / "Structure.xml",
        "UserTasksAddedTriggerSampleWorkflowEventReceiver",
        "UserTasksAddedStartsScoringEventReceiver",
    )
    assert digest([EVENT], context(renamed)) == before


def test_disappeared_entry_point_changes_hash(tree: Path, ctx: ResolveContext) -> None:
    """Точка входа перестала находиться — это событие, а не тишина.

    Неразрешённый якорь входит в хэш своей идентичностью с пустыми фактами,
    поэтому пропажа записи из реестра даёт `review`, а не молчаливое `skip`.
    """
    before = digest([JOB], ctx)
    edit(
        tree / "deployment/Data/Items/Items_Basic.xml",
        'KeyValue="PM: Load limits"',
        'KeyValue="PM: Load caps"',
    )

    assert digest([JOB], context(tree)) != before


# --------------------------------------------------------------------------------------
# Что в хэш не входит вовсе
# --------------------------------------------------------------------------------------


def test_unverified_anchor_is_absent_from_hash(ctx: ResolveContext) -> None:
    """Якорь с `verify: false` в хэш не входит: разрешать в нём нечего,
    и изменения на чужой стороне нашей работой не являются."""
    kafka = Anchor(kind="kafka", ref="pricing.eod.requested", verify=False)
    other = Anchor(kind="kafka", ref="совсем другой топик", verify=False)

    assert digest([WORKFLOW, kafka], ctx) == digest([WORKFLOW], ctx)
    assert digest([WORKFLOW, kafka], ctx) == digest([WORKFLOW, other], ctx)


def test_upstream_never_enters_the_hash() -> None:
    """`upstream` — чужая зона: он объявлен, показан в документе и не хэшируется."""

    catalog = load_catalog(BUSINESS_ROOT, "business")
    doc = catalog.by_id()["bp.valuation.twinml-scoring"]

    assert doc.upstream
    assert all(anchor not in hashed_anchors(doc) for anchor in doc.upstream)


def test_hash_is_stable_across_calls(ctx: ResolveContext) -> None:
    assert digest([WORKFLOW, JOB, GRID, EVENT, TABLE], ctx) == digest(
        [WORKFLOW, JOB, GRID, EVENT, TABLE], ctx
    )


def test_hash_of_whole_document(ctx: ResolveContext) -> None:
    """Сквозная проверка: хэш считается по документу каталога целиком."""
    catalog = load_catalog(BUSINESS_ROOT, "business")
    doc = catalog.by_id()["bp.valuation.twinml-scoring"]
    value = business_hash(resolve_all(hashed_anchors(doc), ctx))

    assert value.startswith("sha256:")
    assert value == business_hash(resolve_all(hashed_anchors(doc), ctx))
