"""Линт бизнес-каталога: восемь проверок и их деление на две группы (B06).

Главное свойство, которое здесь проверяется, — не «все восемь срабатывают»,
а **что именно роняет прогон**. Линт, красный с первого дня, выключают на
второй, поэтому непокрытые точки входа и незалинкованные записи реестров
кода возврата не меняют, пока их не назвали явно.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.business import load_catalog
from docpipe.business.lint import CHECKS, INFORMATIONAL, LintReport, lint
from docpipe.cli import app
from docpipe.materialize.ownership import load_ownership
from tests.business_support import (
    combined_tree,
    context,
    manifest,
    registry_anchors,
    write_doc,
)

runner = CliRunner()
BUSINESS = "business"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return combined_tree(tmp_path)


def report(tree: Path, ownership_yaml: str | None = None) -> LintReport:
    ownership = None
    if ownership_yaml is not None:
        path = tree / "ownership.yaml"
        path.write_text(ownership_yaml, encoding="utf-8")
        ownership = load_ownership(path)

    return lint(
        load_catalog(tree, BUSINESS),
        registry_anchors(tree),
        context(tree),
        BUSINESS,
        ownership,
    )


def checks_of(result: LintReport) -> set[str]:
    return {finding.check for finding in result.findings}


# --------------------------------------------------------------------------------------
# Каждая проверка срабатывает на подготовленном случае
# --------------------------------------------------------------------------------------


def test_unresolved_anchor_is_a_finding(tree: Path) -> None:
    write_doc(
        tree,
        BUSINESS,
        {
            "id": "bp.valuation.ghost",
            "kind": "process",
            "title": "Призрак",
            "entry": [{"kind": "workflow", "ref": "НетТакогоWorkflow", "version": "1"}],
        },
    )
    found = [f for f in report(tree).findings if f.check == "unresolved"]

    assert len(found) == 1
    assert "НетТакогоWorkflow" in found[0].message
    assert "пробовали: реестр" in found[0].message


def test_unverified_anchor_is_not_a_finding(tree: Path) -> None:
    """`verify: false` — объявленная чужая зона. Требовать доказательства
    чужого триггера значит получить вечно красный линт и его отключение."""
    write_doc(
        tree,
        BUSINESS,
        {
            "id": "bp.valuation.foreign",
            "kind": "process",
            "title": "Чужой триггер",
            "entry": [{"kind": "kafka", "ref": "чужой.топик", "verify": False}],
        },
    )

    assert "unresolved" not in checks_of(report(tree))


def test_process_without_entry_is_a_finding(tree: Path) -> None:
    write_doc(
        tree,
        BUSINESS,
        {"id": "bp.valuation.empty", "kind": "process", "title": "Без входа", "entry": []},
    )
    found = [f for f in report(tree).findings if f.check == "no-entry"]

    assert len(found) == 1
    assert found[0].where.endswith("empty.md")


def test_entity_without_entry_is_not_a_finding(tree: Path) -> None:
    """Сущность точкой входа не запускается: `entry` у неё пуст по смыслу."""
    write_doc(
        tree,
        BUSINESS,
        {"id": "be.valuation.thing", "kind": "entity", "title": "Вещь", "entry": []},
    )

    assert "no-entry" not in checks_of(report(tree))


def test_ambiguous_version_lists_candidates(tree: Path) -> None:
    """Кандидаты перечисляются, выбор не делается: состав шагов у версий
    разный, и молчаливый выбор зафиксировал бы в хэше произвольную."""
    write_doc(
        tree,
        BUSINESS,
        {
            "id": "bp.valuation.vague",
            "kind": "process",
            "title": "Без версии",
            "entry": [{"kind": "workflow", "ref": "SampleWorkflow"}],
        },
    )
    found = [f for f in report(tree).findings if f.check == "ambiguous-version"]

    assert len(found) == 1
    assert "SampleWorkflow@1" in found[0].message
    assert "SampleWorkflow@2" in found[0].message


def test_ambiguous_anchor_is_not_also_unresolved(tree: Path) -> None:
    """Одна причина — одна находка: неоднозначность уже названа проверкой 3,
    и вторая строка про тот же якорь чинить не помогает."""
    write_doc(
        tree,
        BUSINESS,
        {
            "id": "bp.valuation.vague",
            "kind": "process",
            "title": "Без версии",
            "entry": [{"kind": "workflow", "ref": "SampleWorkflow"}],
        },
    )

    assert "unresolved" not in checks_of(report(tree))


def test_unknown_capability_is_a_finding(tree: Path) -> None:
    write_doc(
        tree,
        BUSINESS,
        {
            "id": "bp.valuation.orphan-cap",
            "kind": "process",
            "title": "Сирота",
            "capability": "cap.nope",
            "entry": [{"kind": "job", "ref": "PM: Load limits"}],
        },
    )
    result = report(tree)
    found = [f for f in result.findings if f.check == "unknown-capability"]

    assert len(found) == 1
    assert "cap.nope" in found[0].message
    # Тот же текст не должен приехать вторым разом как ошибка каталога.
    assert not [f for f in result.findings if f.check == "catalog" and "cap.nope" in f.message]


def test_team_mismatch_is_a_finding(tree: Path) -> None:
    """`@team` реестра против `ownership.yaml` по реализации: сервис объявлен
    нашим, а код принадлежит другой команде."""
    ownership = """
version: "1"
ownership_version: "test"
teams:
  - id: TVM
    title: ТВМ
rules:
  - id: tvm.all
    team: TVM
    priority: 10
    when:
      namespace_prefix: ["Sbt.Cashflow.Grid.Services.CalcResult"]
"""
    found = [f for f in report(tree, ownership).findings if f.check == "team-mismatch"]

    assert len(found) == 1
    assert "`Core`" in found[0].message
    assert "`TVM`" in found[0].message


def test_broken_identifier_is_a_finding(tree: Path) -> None:
    """Документ с битым идентификатором в каталог не попадает вовсе,
    поэтому увидеть его можно только через ошибки загрузки."""
    path = tree / BUSINESS / "processes" / "valuation" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ndocpipe:\n  schema: business/1\n  id: BP.Valuation.Broken\n"
        "  kind: process\n  title: Битый\n---\n\n# Битый\n",
        encoding="utf-8",
    )
    found = [f for f in report(tree).findings if f.check == "catalog"]

    assert len(found) == 1
    assert "не по образцу" in found[0].message


def test_registry_unlinked_is_a_finding(tree: Path) -> None:
    """Запись реестра ссылается на тип, не найденный среди узлов.

    Диагноз «мёртвая запись» здесь не ставится: узлами становятся только
    enrolled и классифицированные типы, а индекса символов в манифесте нет.
    """
    found = [f for f in report(tree).findings if f.check == "registry-unlinked"]

    assert any("IBlockFilesService" in f.message for f in found)
    assert any("ILoadLimitsJob" in f.message for f in found)
    assert all("не найден среди узлов документации" in f.message for f in found)
    assert all("мёртв" not in f.message for f in found)


def test_uncovered_entry_points_are_counted(tree: Path) -> None:
    result = report(tree)

    # Обе записи пары «UserTasks + ItemAdded» считаются входами и обе покрыты
    # одним документом: он ссылается на пару, а не на класс подписчика.
    assert result.entry_points == 11
    assert result.covered == 5
    assert dict(result.uncovered_by_kind) == {"grid_service": 3, "job": 1, "list_event": 2}


def test_uncovered_report_has_a_team_slice(tree: Path) -> None:
    """Срез по команде отвечает на вопрос «кому это писать», без него
    отчёт «осталось 300» не является задачей ни для кого."""
    result = report(tree)

    assert ("Core", 2) in result.uncovered_by_team
    assert ("TVM", 1) in result.uncovered_by_team


def test_lists_never_enter_the_uncovered_report(tree: Path) -> None:
    """Списков 289, описывать их поштучно никто не будет, и попадание их
    в отчёт сделало бы его вечно красным."""
    result = report(tree)

    assert all("list:" not in finding.message for finding in result.findings)
    assert "list" not in dict(result.uncovered_by_kind)


# --------------------------------------------------------------------------------------
# Чистый каталог
# --------------------------------------------------------------------------------------


def test_clean_catalog_has_no_defects(tree: Path) -> None:
    """На фикстурном каталоге не срабатывает ни одна проверка-дефект:
    остаются только инвентарные факты."""
    result = report(tree)

    assert checks_of(result) <= INFORMATIONAL
    assert result.failing([]) == []


def test_informational_checks_do_not_fail_by_default(tree: Path) -> None:
    result = report(tree)

    assert result.findings
    assert result.failing([]) == []
    assert result.failing(["uncovered"])


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def _run(tree: Path, *args: str) -> object:
    path = tree / "doc-tree.json"
    path.write_text(manifest().model_dump_json(), encoding="utf-8")
    return runner.invoke(
        app,
        [
            "business",
            "lint",
            str(path),
            "--registries",
            str(tree / "registries.yaml"),
            "--root",
            str(tree),
            "--business-root",
            BUSINESS,
            *args,
        ],
    )


def test_cli_is_green_on_clean_catalog(tree: Path) -> None:
    result = _run(tree)

    assert result.exit_code == 0
    assert "Точек входа: 11, описано: 5, осталось: 6" in result.stdout


def test_cli_fails_on_unresolved(tree: Path) -> None:
    write_doc(
        tree,
        BUSINESS,
        {
            "id": "bp.valuation.ghost",
            "kind": "process",
            "title": "Призрак",
            "entry": [{"kind": "workflow", "ref": "НетТакогоWorkflow", "version": "1"}],
        },
    )

    assert _run(tree).exit_code == 1
    assert _run(tree, "--fail-on", "unresolved").exit_code == 1
    # Названа другая проверка — эта находка прогон не роняет.
    assert _run(tree, "--fail-on", "uncovered").exit_code == 1
    assert _run(tree, "--fail-on", "no-entry").exit_code == 0


def test_cli_rejects_a_typo_in_fail_on(tree: Path) -> None:
    """Опечатка в CI иначе дала бы вечно зелёную проверку — та же ловушка,
    что у `docs status --fail-on`."""
    result = _run(tree, "--fail-on", "unresolvd")

    assert result.exit_code == 2
    assert "Неизвестная проверка" in result.output
    for check in CHECKS:
        assert check in result.output


def test_cli_requires_registries(tree: Path) -> None:
    path = tree / "doc-tree.json"
    path.write_text(manifest().model_dump_json(), encoding="utf-8")
    result = runner.invoke(app, ["business", "lint", str(path), "--root", str(tree)])

    assert result.exit_code == 2
    assert "Реестры не заданы" in result.output


def test_cli_reads_paths_from_config(tree: Path) -> None:
    """Пути берутся из `docpipe.yaml`, когда флагов нет: команду зовут часто,
    и повторять четыре пути руками никто не станет."""
    shutil.copy(tree / "registries.yaml", tree / "registries-copy.yaml")
    (tree / "docpipe.yaml").write_text(
        f"registries: {tree / 'registries-copy.yaml'}\nbusiness_root: {BUSINESS}\n",
        encoding="utf-8",
    )
    path = tree / "doc-tree.json"
    path.write_text(manifest().model_dump_json(), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "business",
            "lint",
            str(path),
            "--root",
            str(tree),
            "--config",
            str(tree / "docpipe.yaml"),
        ],
    )

    assert result.exit_code == 0
    assert "Точек входа: 11" in result.stdout
