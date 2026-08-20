"""Точки входа и бизнес-документы: кто кого покрывает (G17, часть).

Отчёт обязан печатать два числа, и они про разное: **точка входа без
документа** — состояние работы, **якорь без точки входа** — уже находка,
потому что через месяц он неотличим от опечатки.
"""

from pathlib import Path

from typer.testing import CliRunner

from docpipe.business.model import Anchor, BusinessDoc, Catalog
from docpipe.cli import app
from docpipe.graph import GraphIndex, GraphMeta, GraphNode, write_index
from docpipe.graph.coverage import coverage, format_coverage

runner = CliRunner()


def entry(kind: str, ref: str, registry_kind: str = "") -> GraphNode:
    return GraphNode(
        key=f"entry:{kind}:{ref.lower()}",
        kind="entry_point",
        name=ref,
        source="registry",
        attributes={
            "entry_kind": kind,
            "ref": ref,
            **({"registry_kind": registry_kind} if registry_kind else {}),
        },
    )


def document(identity: str, anchors: list[Anchor]) -> BusinessDoc:
    return BusinessDoc(
        schema="docpipe.business/1",
        id=identity,
        kind="process",
        title=identity,
        entry=anchors,
    )


def test_anchor_matches_the_entry_point_through_the_kind_bridge() -> None:
    """Аналитик пишет `table`, реестр объявляет `list`. Пара, которой нет
    в мосте, просто никогда не разрешится — и это будет выглядеть
    как «инструмент не нашёл»."""
    nodes = (entry("data", "UserTasks", registry_kind="list"),)
    catalog = Catalog(docs=[document("bp.x", [Anchor(kind="table", ref="UserTasks")])])
    report = coverage(nodes, catalog)
    assert report.covered == 1
    assert report.anchors_without_entry_point == ()


def test_uncovered_entry_points_are_grouped_by_kind() -> None:
    nodes = (
        entry("workflow", "Valuation"),
        entry("job", "Nightly"),
        entry("job", "Hourly"),
    )
    report = coverage(nodes, Catalog())
    assert report.uncovered_by_kind == {"workflow": 1, "job": 2}
    assert report.covered == 0


def test_anchor_without_an_entry_point_is_a_finding() -> None:
    catalog = Catalog(docs=[document("bp.x", [Anchor(kind="job", ref="Пропавший")])])
    report = coverage((), catalog)
    assert report.anchors_without_entry_point == ("bp.x: job Пропавший",)


def test_unverified_anchor_is_not_a_finding() -> None:
    """`verify: false` — граница зоны ответственности: процесс может
    начинаться в чужой команде, и требовать доказательства чужого триггера
    значит получить вечно красный отчёт и его отключение."""
    catalog = Catalog(docs=[document("bp.x", [Anchor(kind="job", ref="Чужой", verify=False)])])
    report = coverage((), catalog)
    assert report.anchors_without_entry_point == ()


def test_report_says_that_uncovered_is_work_not_a_defect() -> None:
    text = format_coverage(coverage((entry("job", "Nightly"),), Catalog()))
    assert "состояние работы, а не дефект" in text


def test_cli_fails_only_when_a_threshold_is_given(tmp_path: Path) -> None:
    index = GraphIndex(nodes=(entry("job", "Nightly"),))
    path = tmp_path / "graph.db"
    write_index(path, index, GraphMeta(generation=""))
    catalog_root = tmp_path / "business"
    (catalog_root / "processes").mkdir(parents=True)

    config = tmp_path / "docpipe.yaml"
    config.write_text("business_root: business\n", encoding="utf-8")

    quiet = runner.invoke(
        app, ["graph", "coverage", str(path), "--root", str(tmp_path), "--config", str(config)]
    )
    assert quiet.exit_code == 0, quiet.output
    assert "Точек входа: 1" in quiet.output

    strict = runner.invoke(
        app,
        [
            "graph",
            "coverage",
            str(path),
            "--root",
            str(tmp_path),
            "--config",
            str(config),
            "--fail-under",
            "0.5",
        ],
    )
    assert strict.exit_code == 1
