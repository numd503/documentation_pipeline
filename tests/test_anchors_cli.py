"""Инвентаризация точек входа: резолв в узлы манифеста и команда (B03).

Манифест собирается здесь синтетически, а не берётся из фикстур C#: связь
«реестр → узел» не зависит от языка, и тянуть ради неё разбор .NET значило бы
привязать бизнес-слой к нему через тесты.
"""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.business.build import entry_snippet
from docpipe.business.model import Anchor
from docpipe.business.resolve import ANCHOR_KIND_BY_REGISTRY, resolve
from docpipe.cli import app
from docpipe.model import DocNode, Manifest, ParserVersions, Relation, Symbol
from docpipe.registry import load_registries, read_registry, resolve_anchors
from docpipe.registry.anchors import (
    ENTRY_KINDS,
    ResolvedAnchor,
    find_by_implementation,
    similar_names,
)
from tests.business_support import context

ROOT = Path("tests/fixtures/registries")
REGISTRIES = ROOT / "registries.yaml"
runner = CliRunner()


# --------------------------------------------------------------------------------------
# Синтетический манифест
# --------------------------------------------------------------------------------------


def _node(fqn: str, *, implements: str | None = None, params: list[str] | None = None) -> DocNode:
    name = fqn.rsplit(".", 1)[-1]
    namespace = fqn.rsplit(".", 1)[0]
    return DocNode(
        id=f"type:src/App/App.csproj#{fqn}`{len(params or [])}",
        kind="service",
        template="service",
        title=name,
        doc_path=f"docs/modules/App/services/{name.lower()}.md",
        module="App",
        domain="app",
        signature_hash="sha256:0",
        symbol=Symbol(
            fqn=fqn,
            name=name,
            type_kind="class",
            namespace=namespace,
            module="App",
            type_parameters=params or [],
        ),
        related=([Relation(target=implements, relation="implements")] if implements else []),
    )


# Намеренно НЕ покрыты узлами: BlockFilesService (grid-сервис без документации)
# и реализация ILoadLimitsJob (джоб без реализации) — на них проверяется,
# что неразрешённая ссылка остаётся находкой, а не ошибкой.
NODES = [
    _node("Sbt.Cashflow.Grid.Services.CalcResult.CalcResult"),
    _node("Sbt.Cashflow.Grid.Services.CalcService.DXExcelCalcService"),
    _node(
        "Sbt.CMS.Cashflow.Infrastructure.FinancialReportJob",
        implements="Sbt.CMS.Cashflow.Infrastructure.QuartzJobInterfaces.IFinancialReportJob",
    ),
    _node("Sbt.Sample.Steps.CollectStep"),
    _node("Sbt.Sample.Steps.ScoreStep"),
    _node("Sbt.Sample.Steps.MapStep", params=["TIn", "TOut"]),
    _node("Sbt.Sample.Models.SampleAggregate"),
    _node("Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver"),
]


@pytest.fixture
def manifest() -> Manifest:
    return Manifest(
        ruleset_version="test",
        parser=ParserVersions(tree_sitter="0.25.0", grammar_c_sharp="0.23.0"),
        nodes=NODES,
    )


@pytest.fixture
def manifest_file(tmp_path: Path, manifest: Manifest) -> Path:
    path = tmp_path / "doc-tree.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path


@pytest.fixture
def anchors(manifest: Manifest) -> list[ResolvedAnchor]:
    specs = load_registries(REGISTRIES)
    return resolve_anchors([read_registry(spec, ROOT) for spec in specs], manifest)


def _one(anchors: list[ResolvedAnchor], kind: str, ref: str) -> ResolvedAnchor:
    return next(a for a in anchors if a.kind == kind and a.ref == ref)


# --------------------------------------------------------------------------------------
# Состав инвентаризации
# --------------------------------------------------------------------------------------


def test_counts_by_kind(anchors: list[ResolvedAnchor]) -> None:
    counted = {kind: sum(a.kind == kind for a in anchors) for kind in {a.kind for a in anchors}}

    assert counted == {
        "grid_service": 3,
        "job": 2,
        "workflow": 2,
        # Четыре записи на три события: на `UserTasks + ItemAdded` подписаны
        # двое, и инвентаризация считает записи реестра, а не пары.
        "list_event": 4,
        "list": 2,
    }


def test_list_is_not_an_entry_point(anchors: list[ResolvedAnchor]) -> None:
    """Список — сущность, а вход — объявленный на нём обработчик события."""
    entries = [a for a in anchors if a.kind in ENTRY_KINDS]

    assert len(entries) == 11
    assert all(a.kind != "list" for a in entries)


def test_event_receivers_are_lifted_to_top_level(anchors: list[ResolvedAnchor]) -> None:
    """Обработчик — вход, поэтому он отдельный якорь со ссылкой на свой список."""
    events = [a for a in anchors if a.kind == "list_event"]

    assert sorted(a.display for a in events) == [
        "UserTasks/ItemAdded",
        "UserTasks/ItemAdded",
        "UserTasks/ItemAdding",
        "UserTasks/ItemUpdating",
    ]
    assert all(a.scope == "UserTasks" for a in events)


def test_fields_stay_inside_the_list(anchors: list[ResolvedAnchor]) -> None:
    """Поля списка описывают сущность, а не вход: на верхний уровень не всплывают."""
    tasks = _one(anchors, "list", "UserTasks")

    assert all(a.kind != "list_field" for a in anchors)
    assert sum(child.kind == "list_field" for child in tasks.children) == 4


def test_workflow_display_carries_version(anchors: list[ResolvedAnchor]) -> None:
    workflows = [a for a in anchors if a.kind == "workflow"]

    assert sorted(a.display for a in workflows) == ["SampleWorkflow@1", "SampleWorkflow@2"]


def test_anchors_are_sorted(anchors: list[ResolvedAnchor]) -> None:
    keys = [(a.kind, a.scope or "", a.ref, a.version or "") for a in anchors]

    assert keys == sorted(keys)


# --------------------------------------------------------------------------------------
# Резолв в узлы
# --------------------------------------------------------------------------------------


def test_grid_service_resolves_directly(anchors: list[ResolvedAnchor]) -> None:
    anchor = _one(anchors, "grid_service", "ICalcResult")

    assert [t.via for t in anchor.targets] == ["direct"]
    assert anchor.targets[0].doc_path == "docs/modules/App/services/calcresult.md"
    assert anchor.team == "Core"


def test_job_resolves_through_the_interface(anchors: list[ResolvedAnchor]) -> None:
    """JOBCLASS — интерфейс, а интерфейсы по умолчанию не документируются.

    Без второго шага резолва каждый джоб выглядел бы неразрешённым.
    """
    anchor = _one(anchors, "job", "Financial reports job")

    assert [t.via for t in anchor.targets] == ["implementation"]
    assert anchor.targets[0].node_id is not None
    assert "FinancialReportJob" in anchor.targets[0].node_id


def test_unresolved_reference_is_a_finding(anchors: list[ResolvedAnchor]) -> None:
    limits = _one(anchors, "job", "PM: Load limits")
    blocks = _one(anchors, "grid_service", "IBlockFilesService")

    assert [t.via for t in limits.targets] == ["unresolved"]
    assert [t.via for t in blocks.targets] == ["unresolved"]
    assert not limits.resolved and not blocks.resolved


def test_generic_step_resolves_after_arity_is_stripped(anchors: list[ResolvedAnchor]) -> None:
    """`MapStep`2[[…]], Sbt.Sample` обязан найти узел `Sbt.Sample.Steps.MapStep`."""
    workflow = next(a for a in anchors if a.kind == "workflow" and a.version == "2")
    step = next(child for child in workflow.children if child.ref == "ThresholdStep")

    assert [t.via for t in step.targets] == ["direct"]
    assert step.targets[0].fqn == "Sbt.Sample.Steps.MapStep"


def test_workflow_state_type_resolves(anchors: list[ResolvedAnchor]) -> None:
    workflow = next(a for a in anchors if a.kind == "workflow" and a.version == "2")
    data = next(t for t in workflow.targets if t.field == "data_type")

    assert data.fqn == "Sbt.Sample.Models.SampleAggregate"
    assert data.doc_path is not None


def test_event_receiver_resolves(anchors: list[ResolvedAnchor]) -> None:
    """Каждая запись пары разрешается сама по себе.

    Проверяются обе: `next()` брал бы произвольную из двух, и тест был бы
    зелёным независимо от того, разрешается ли обработчик, который в манифесте
    действительно есть.
    """
    added = [a for a in anchors if a.display == "UserTasks/ItemAdded"]
    resolved = [a for a in added if a.targets and a.targets[0].via == "direct"]

    assert len(added) == 2
    assert len(resolved) == 1
    assert resolved[0].targets[0].doc_path is not None
    # Второй обработчик в манифест не попал — и это нормальное состояние,
    # а не ошибка: узлами становятся только enrolled и классифицированные типы.
    assert [a.targets[0].via for a in added if a not in resolved] == ["unresolved"]


def test_steps_keep_declaration_order(anchors: list[ResolvedAnchor]) -> None:
    workflow = next(a for a in anchors if a.kind == "workflow" and a.version == "2")

    assert [child.ref for child in workflow.children] == [
        "CollectStep",
        "ThresholdStep",
        "ScoreStep",
    ]


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def _invoke(manifest_file: Path, *extra: str) -> object:
    return runner.invoke(
        app,
        [
            "anchors",
            "list",
            str(manifest_file),
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ]
        + list(extra),
    )


def test_list_exits_zero_despite_unresolved(manifest_file: Path) -> None:
    result = _invoke(manifest_file)

    assert result.exit_code == 0
    assert "Точек входа: 11" in result.stdout
    assert "не найден среди узлов документации" in result.stdout


def test_kind_filter(manifest_file: Path) -> None:
    result = _invoke(manifest_file, "--kind", "job", "--format", "json")
    payload = json.loads(result.stdout)

    assert payload["counts"] == {"job": 2}
    assert payload["total"] == 2


def test_team_filter(manifest_file: Path) -> None:
    result = _invoke(manifest_file, "--team", "Core", "--format", "json")
    payload = json.loads(result.stdout)

    assert {a["ref"] for a in payload["anchors"]} == {"ICalcResult", "CalcDxExcel"}


def test_filters_combine(manifest_file: Path) -> None:
    result = _invoke(manifest_file, "--team", "TVM", "--kind", "grid_service", "--format", "json")
    payload = json.loads(result.stdout)

    assert payload["total"] == 1
    assert payload["anchors"][0]["ref"] == "IBlockFilesService"


def test_json_is_byte_stable(manifest_file: Path) -> None:
    first = _invoke(manifest_file, "--format", "json")
    second = _invoke(manifest_file, "--format", "json")

    assert first.stdout == second.stdout


def test_registry_errors_are_reported_not_fatal(manifest_file: Path) -> None:
    """Битый файл под глобом джобов не мешает инвентаризации."""
    result = _invoke(manifest_file)

    assert result.exit_code == 0
    assert "Items_Broken.xml" in result.stdout


def test_explain(manifest_file: Path) -> None:
    result = runner.invoke(
        app,
        [
            "anchors",
            "explain",
            str(manifest_file),
            "Financial reports job",
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ],
    )

    assert result.exit_code == 0
    assert "реестр:  jobs" in result.stdout
    assert "Items_Basic.xml" in result.stdout
    assert "через реализацию интерфейса" in result.stdout
    assert "docs/modules/App/services/financialreportjob.md" in result.stdout


def test_explain_accepts_display_string(manifest_file: Path) -> None:
    result = runner.invoke(
        app,
        [
            "anchors",
            "explain",
            str(manifest_file),
            "UserTasks/ItemAdded",
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ],
    )

    assert result.exit_code == 0
    assert "list_event  UserTasks/ItemAdded" in result.stdout


def test_explain_unknown_anchor(manifest_file: Path) -> None:
    result = runner.invoke(
        app,
        [
            "anchors",
            "explain",
            str(manifest_file),
            "нет такого",
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ],
    )

    assert result.exit_code == 1


def test_broken_manifest_is_user_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")

    assert _invoke(bad).exit_code == 2


def test_broken_registries_is_user_error(manifest_file: Path, tmp_path: Path) -> None:
    bad = tmp_path / "registries.yaml"
    bad.write_text("registries: []", encoding="utf-8")

    result = runner.invoke(
        app,
        ["anchors", "list", str(manifest_file), "--registries", str(bad), "--root", str(ROOT)],
    )

    assert result.exit_code == 2


# --------------------------------------------------------------------------------------
# Обратный поиск: от типа к якорю
# --------------------------------------------------------------------------------------
#
# Направление «реестр → код» даёт `anchors list`, но аналитик начинает
# с другого конца: он знает свой класс и не знает, какой строкой его вызывают.


def _which(manifest_file: Path, query: str, *extra: str) -> object:
    return runner.invoke(
        app,
        [
            "anchors",
            "which",
            str(manifest_file),
            query,
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ]
        + list(extra),
    )


def test_which_finds_anchor_by_fqn(anchors: list[ResolvedAnchor]) -> None:
    found = find_by_implementation(
        anchors, "Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver"
    )

    assert [(m.anchor.kind, m.scope, m.anchor.ref) for m in found] == [
        ("list_event", "UserTasks", "ItemAdded")
    ]
    assert found[0].matched_field == "impl_fqn"


def test_which_finds_anchor_by_simple_name(anchors: list[ResolvedAnchor]) -> None:
    """Человек помнит имя класса, а не полное имя с namespace."""
    found = find_by_implementation(anchors, "UserTasksAddedTriggerSampleWorkflowEventReceiver")

    assert [m.anchor.ref for m in found] == ["ItemAdded"]


def test_which_finds_anchor_by_doc_path(anchors: list[ResolvedAnchor]) -> None:
    """Отправной точкой бывает и документ шага 2: человек читает его и хочет
    знать, каким процессом эта штука запускается."""
    found = find_by_implementation(
        anchors, "docs/modules/App/services/usertasksaddedtriggersampleworkfloweventreceiver.md"
    )

    assert [m.anchor.ref for m in found] == ["ItemAdded"]


def test_which_finds_nested_workflow_step(anchors: list[ResolvedAnchor]) -> None:
    """Шаг workflow на верхний уровень не поднимается, а команда чаще владеет
    шагом, чем процессом целиком. Не искать по детям значило бы не отвечать
    на самый частый вопрос."""
    found = find_by_implementation(anchors, "Sbt.Sample.Steps.ScoreStep")

    assert [(m.anchor.kind, m.scope, m.anchor.ref) for m in found] == [
        ("workflow_step", "SampleWorkflow", "ScoreStep"),
        ("workflow_step", "SampleWorkflow", "ScoreStep"),
    ]
    # Версий у workflow две, и обе объявляют этот шаг: показываются обе,
    # потому что выбор версии — решение автора документа, а не инструмента.
    assert {m.anchor.source_path for m in found} == {
        "deployment/Data/Items/Workflows/Sample.v1.json",
        "deployment/Data/Items/Workflows/Sample.v2.json",
    }


def test_which_names_the_other_holders_of_the_same_anchor(anchors: list[ResolvedAnchor]) -> None:
    """Про общий якорь надо сказать в момент выбора, а не когда чужой класс
    уже появился в собранном документе."""
    found = find_by_implementation(
        anchors, "Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver"
    )
    siblings = found[0].siblings

    assert [s.fields["impl_fqn"] for s in siblings] == [
        "Sbt.Cashflow.Reports.EventReceivers.UserTasksAddedAuditEventReceiver"
    ]


def test_which_prints_a_snippet_that_actually_resolves(anchors: list[ResolvedAnchor]) -> None:
    """Печатается не описание того, что надо вставить, а то, что вставляется.

    Проверяется кругом: сниппет разбирается как YAML, превращается в якорь
    и разрешается реестром. Иначе команда учила бы форме, которая не работает.
    """
    found = find_by_implementation(
        anchors, "Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver"
    )
    parsed = yaml.safe_load(entry_snippet(found[0]))

    anchor = Anchor.model_validate(parsed["entry"][0])
    assert anchor == Anchor(kind="list_event", ref="ItemAdded", scope="UserTasks")
    assert resolve(anchor, context()).confidence == "registry"


def test_which_warns_when_the_step_anchor_is_ambiguous(manifest_file: Path) -> None:
    """Шаг объявлен в двух версиях workflow. Сниппет для него неоднозначен:
    `version` у записи шага нет, и вставленный якорь даст `ambiguous-version`.

    Напечатать его молча значило бы выдать заведомо нерабочий кусок
    за готовый к вставке — ровно та ошибка, которую команда должна убирать.
    """
    result = _which(manifest_file, "Sbt.Sample.Steps.ScoreStep")

    assert result.exit_code == 0
    assert "ОСТОРОЖНО" in result.stdout
    assert "ambiguous-version" in result.stdout


def test_which_snippet_uses_the_dictionary_of_the_catalog(anchors: list[ResolvedAnchor]) -> None:
    """Реестр объявляет `list`, аналитик пишет `table`. Сниппет обязан быть
    на языке каталога, иначе вставленное никогда не разрешится."""
    found = find_by_implementation(anchors, "Sbt.Cashflow.Grid.Services.CalcResult.CalcResult")
    parsed = yaml.safe_load(entry_snippet(found[0]))

    assert parsed["entry"][0]["kind"] == "grid_service"
    assert ANCHOR_KIND_BY_REGISTRY["list"] == "table"


def test_which_cli_prints_snippet_and_siblings(manifest_file: Path) -> None:
    result = _which(manifest_file, "UserTasksAddedTriggerSampleWorkflowEventReceiver")

    assert result.exit_code == 0
    assert "list_event  UserTasks/ItemAdded" in result.stdout
    assert "UserTasksAddedAuditEventReceiver" in result.stdout
    assert "  entry:\n  - kind: list_event\n    ref: ItemAdded\n    scope: UserTasks" in (
        result.stdout
    )


def test_which_exits_zero_when_nothing_found(manifest_file: Path) -> None:
    """«Ниоткуда не вызывается» — нормальное состояние почти всего дерева,
    а не то, что надо чинить. Код 1 здесь означал бы обратное."""
    result = _which(manifest_file, "Sbt.Nothing.Calls.This")

    assert result.exit_code == 0
    assert "не найдено" in result.stdout


def test_which_json_carries_the_snippet(manifest_file: Path) -> None:
    result = _which(manifest_file, "Sbt.Sample.Steps.ScoreStep", "--format", "json")
    payload = json.loads(result.stdout)

    assert payload["query"] == "Sbt.Sample.Steps.ScoreStep"
    assert payload["matches"][0]["entry_snippet"].startswith("  entry:")
    again = _which(manifest_file, "Sbt.Sample.Steps.ScoreStep", "--format", "json")
    assert result.stdout == again.stdout


def test_which_rejects_unknown_format(manifest_file: Path) -> None:
    assert _which(manifest_file, "X", "--format", "yaml").exit_code == 2


def test_which_suggests_close_names(anchors: list[ResolvedAnchor]) -> None:
    """Опечатка в одну букву давала «не найдено», и человек шёл проверять
    реестры, которые в порядке. «Не найдено» и «нашлось похожее» — разные
    ответы."""
    names = similar_names(anchors, "UserTasksAddedTriggerSampleWorkflowEventReceive")

    assert any("UserTasksAddedTriggerSampleWorkflowEventReceiver" in name for name in names)


def test_which_cli_prints_the_suggestion(manifest_file: Path) -> None:
    result = _which(manifest_file, "UserTasksAddedTriggerSampleWorkflowEventReceive")

    assert result.exit_code == 0
    assert "Похожие имена" in result.stdout
    assert "UserTasksAddedTriggerSampleWorkflowEventReceiver" in result.stdout


def test_which_says_nothing_extra_when_nothing_is_close(manifest_file: Path) -> None:
    """Подсказка на всё подряд обесценивает саму подсказку."""
    result = _which(manifest_file, "Совершенно.Другое.Имя")

    assert "Похожие имена" not in result.stdout


def test_explain_shows_how_to_narrow_a_shared_anchor(manifest_file: Path) -> None:
    """На паре подписчиков несколько, и `only` пишут по этому выводу.
    Без подсказки его составляют наугад из перечня полей."""
    result = runner.invoke(
        app,
        [
            "anchors",
            "explain",
            str(manifest_file),
            "UserTasks/ItemAdded",
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ],
    )

    assert result.exit_code == 0
    assert "Совпало записей: 2" in result.stdout
    assert "сузить до этой записи:" in result.stdout
    assert "only: {assembly: Sbt.Cashflow.ML.EventReceivers}" in result.stdout


def test_explain_does_not_offer_narrowing_for_a_single_record(manifest_file: Path) -> None:
    """Сужать нечего — подсказка была бы шумом."""
    result = runner.invoke(
        app,
        [
            "anchors",
            "explain",
            str(manifest_file),
            "UserTasks/ItemAdding",
            "--registries",
            str(REGISTRIES),
            "--root",
            str(ROOT),
        ],
    )

    assert result.exit_code == 0
    assert "сузить до этой записи:" not in result.stdout
