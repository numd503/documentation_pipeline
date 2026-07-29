"""Инвентаризация точек входа: резолв в узлы манифеста и команда (B03).

Манифест собирается здесь синтетически, а не берётся из фикстур C#: связь
«реестр → узел» не зависит от языка, и тянуть ради неё разбор .NET значило бы
привязать бизнес-слой к нему через тесты.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.model import DocNode, Manifest, ParserVersions, Relation, Symbol
from docpipe.registry import load_registries, read_registry, resolve_anchors
from docpipe.registry.anchors import ENTRY_KINDS, ResolvedAnchor

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
        "list_event": 3,
        "list": 2,
    }


def test_list_is_not_an_entry_point(anchors: list[ResolvedAnchor]) -> None:
    """Список — сущность, а вход — объявленный на нём обработчик события."""
    entries = [a for a in anchors if a.kind in ENTRY_KINDS]

    assert len(entries) == 10
    assert all(a.kind != "list" for a in entries)


def test_event_receivers_are_lifted_to_top_level(anchors: list[ResolvedAnchor]) -> None:
    """Обработчик — вход, поэтому он отдельный якорь со ссылкой на свой список."""
    events = [a for a in anchors if a.kind == "list_event"]

    assert {a.display for a in events} == {
        "UserTasks/ItemAdding",
        "UserTasks/ItemUpdating",
        "UserTasks/ItemAdded",
    }
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
    added = next(a for a in anchors if a.display == "UserTasks/ItemAdded")

    assert added.targets[0].via == "direct"
    assert added.targets[0].doc_path is not None


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
    assert "Точек входа: 10" in result.stdout
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
