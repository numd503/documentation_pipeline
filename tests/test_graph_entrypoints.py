"""Точки входа как объекты графа (G03).

Корень графа существует независимо от того, есть ли у него бизнес-название,
и независимо от того, нашёлся ли под него код. Второе — состояние работы,
а не дефект, и правило это перенесено из бизнес-слоя без изменений.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.arch import ArchRegistry, load_arch_registry, run_adapter
from docpipe.cli import app
from docpipe.graph import GraphNode, entry_key
from docpipe.graph.entrypoints import (
    LINK_EXACT,
    LINK_NONE,
    LINK_SHORT,
    from_manifest,
    from_registry,
    link,
)
from docpipe.model import Manifest

runner = CliRunner()
REGISTRIES = Path("tests/fixtures/registries")
ARCH_FIXTURE = Path("tests/fixtures/arch/sample-arch.yaml")
GOLDEN = Path("tests/golden/doc-tree.json")


def registry_from_fixture() -> ArchRegistry:
    result = run_adapter(
        "registries", {"spec": str(REGISTRIES / "registries.yaml")}, REGISTRIES, lambda v: Path(v)
    )
    return ArchRegistry(version="1", records=tuple(result.records))


def code_node(key: str, name: str, fqn: str = "", kind: str = "type") -> GraphNode:
    return GraphNode(
        key=key,
        kind=kind,
        name=name,
        file=key.split("#")[0],
        attributes={"fqn": fqn} if fqn else {},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Два источника, и смешивать их нельзя
# ──────────────────────────────────────────────────────────────────────────────


def test_registry_gives_the_exact_number_of_roots() -> None:
    """На тестовых реестрах количество корней совпадает с ожидаемым точно
    (G03 п. 4)."""
    entries = from_registry(registry_from_fixture())
    kinds = {}
    for node in entries:
        kind = node.attributes["entry_kind"]
        kinds[kind] = kinds.get(kind, 0) + 1
    assert kinds == {
        "grid_service": 3,
        "job": 2,
        "workflow": 2,
        "workflow_step": 5,
        "event_handler": 3,
    }


def test_entry_point_key_lives_in_its_own_space() -> None:
    """Корень и класс, который его выполняет, — разные узлы: один класс
    обслуживает несколько корней, и наоборот."""
    assert entry_key("job", "Ночная переоценка").startswith("entry:job:")
    assert entry_key("job", "НОЧНАЯ Переоценка") == entry_key("job", "ночная переоценка")


def test_entry_point_exists_without_a_business_name() -> None:
    """Бизнес-название — необязательная метка сверху (G03 п. 3)."""
    registry = load_arch_registry(ARCH_FIXTURE)
    entries = from_registry(registry)
    assert entries
    for node in entries:
        assert node.name


def test_manifest_gives_http_endpoints() -> None:
    manifest = Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
    entries = from_manifest(manifest)
    routes = {node.attributes["route"] for node in entries}
    assert routes == {"api/v1/Pricing", "api/v1/Pricing/{id:guid}"}
    assert all(node.source == "manifest" for node in entries)


def test_sources_are_distinguishable_in_the_index() -> None:
    """Отсутствие реестра обязано быть отличимо от «точек входа нет»."""
    manifest = Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
    only_code = from_manifest(manifest)
    _, report = link(only_code, (), manifest)
    assert report.registry_present is False
    assert report.from_registry == 0
    assert report.from_manifest == 2


# ──────────────────────────────────────────────────────────────────────────────
# Связывание корня с кодом
# ──────────────────────────────────────────────────────────────────────────────


def test_link_by_full_name_is_exact() -> None:
    registry = load_arch_registry(ARCH_FIXTURE)
    entries = from_registry(registry)
    code = (
        code_node(
            "src/Grid/RiskComputeService.cs#RiskComputeService",
            "RiskComputeService",
            "Sample.Pricing.Api.Grid.RiskComputeService",
        ),
    )
    edges, report = link(entries, code, None)
    assert report.linked[LINK_EXACT] == 1
    assert edges[0].kind == "dispatches"
    assert edges[0].confidence == 1.0


def test_link_by_short_name_lowers_confidence() -> None:
    """Несколько типов с одним коротким именем — не повод выбрать первого."""
    registry = load_arch_registry(ARCH_FIXTURE)
    entries = [node for node in from_registry(registry) if "grid_service" in node.key]
    code = (
        code_node("a/RiskComputeService.cs#RiskComputeService", "RiskComputeService"),
        code_node("b/RiskComputeService.cs#RiskComputeService", "RiskComputeService"),
    )
    edges, report = link(entries, code, None)
    assert report.linked[LINK_SHORT] == 2
    assert {edge.confidence for edge in edges} == {0.5}


def test_root_without_code_does_not_fail_the_run() -> None:
    """Корень, объявленный в реестре и не нашедший узла кода, попадает
    в отчёт, но прогон не роняет (G03 п. 2)."""
    registry = load_arch_registry(ARCH_FIXTURE)
    entries = from_registry(registry)
    _, report = link(entries, (), None)
    assert report.linked[LINK_NONE] == 3
    assert report.unlinked_examples


def test_dispatch_edge_is_not_a_call() -> None:
    """Реестр называет класс, универсальный раннер его запускает.
    Вызывающего в коде нет вовсе, и записывать это как `calls` значило бы
    утверждать то, чего нет."""
    registry = load_arch_registry(ARCH_FIXTURE)
    code = (
        code_node(
            "src/W/ValuationWorkflow.cs#ValuationWorkflow",
            "ValuationWorkflow",
            "Sample.Pricing.Api.Workflows.ValuationWorkflow",
        ),
    )
    edges, _ = link(from_registry(registry), code, None)
    assert {edge.kind for edge in edges} == {"dispatches"}


# ──────────────────────────────────────────────────────────────────────────────
# Один источник записей на два потребителя
# ──────────────────────────────────────────────────────────────────────────────


def test_graph_roots_agree_with_the_anchor_inventory() -> None:
    """Корни графа и инвентарь `anchors` собраны из одного источника (G03 п. 5).

    Полного равенства между ними нет и не должно быть: `anchors` держит
    два якоря на паре «список + EventType», когда обработчиков два, а граф
    сводит их в один корень с двумя реализациями; шаги workflow у `anchors`
    остаются детьми, а в графе они корни. Совпадать обязано множество
    **якорей**: если бы источники разошлись, разошлось бы и оно.
    """
    from docpipe.registry import load_registries, read_registry
    from docpipe.registry.anchors import resolve_anchors

    manifest = Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
    results = [
        read_registry(spec, REGISTRIES) for spec in load_registries(REGISTRIES / "registries.yaml")
    ]
    anchors = resolve_anchors(results, manifest)

    entries = from_registry(registry_from_fixture())
    graph_refs = {
        (node.attributes["registry_kind"], node.attributes["ref"])
        for node in entries
        if node.attributes.get("ref")
    }
    anchor_refs = {(anchor.kind, anchor.ref) for anchor in anchors if anchor.kind != "list"}
    assert anchor_refs <= graph_refs, anchor_refs - graph_refs


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def index_with_roots(tmp_path: Path) -> Path:
    """Индекс без движка: узлы и корни кладутся напрямую.

    Контрактные тесты с настоящим разбором лежат отдельно; здесь проверяется
    команда, а не мост.
    """
    from docpipe.graph import GraphIndex, GraphMeta, write_index

    registry = load_arch_registry(ARCH_FIXTURE)
    entries = from_registry(registry)
    code = (
        code_node(
            "src/W/ValuationWorkflow.cs#ValuationWorkflow",
            "ValuationWorkflow",
            "Sample.Pricing.Api.Workflows.ValuationWorkflow",
        ),
    )
    edges, _ = link(entries, code, None)
    path = tmp_path / "graph.db"
    write_index(
        path,
        GraphIndex(nodes=tuple(code) + tuple(entries), edges=tuple(edges)),
        GraphMeta(generation=""),
    )
    return path


def test_cli_lists_roots_with_link_state(index_with_roots: Path) -> None:
    result = runner.invoke(app, ["graph", "entrypoints", str(index_with_roots)])
    assert result.exit_code == 0, result.output
    assert "связан" in result.output
    assert "НЕ СВЯЗАН" in result.output
    assert "Всего корней: 3" in result.output


def test_cli_can_show_only_unlinked(index_with_roots: Path) -> None:
    result = runner.invoke(app, ["graph", "entrypoints", str(index_with_roots), "--unlinked"])
    assert result.exit_code == 0, result.output
    assert "Всего корней: 2" in result.output


def test_cli_says_where_it_looked_when_there_are_no_roots(tmp_path: Path) -> None:
    """Пустой вывод запрещён: «корней нет» говорится словами и со списком
    того, где искали (Р7)."""
    from docpipe.graph import GraphIndex, GraphMeta, write_index

    path = tmp_path / "graph.db"
    write_index(path, GraphIndex(), GraphMeta(generation=""))
    result = runner.invoke(app, ["graph", "entrypoints", str(path)])
    assert result.exit_code == 0, result.output
    assert "Корней не найдено" in result.output
    assert "arch records" in result.output
