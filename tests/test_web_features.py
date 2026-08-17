"""Раздел: единица документации без маршрута (P16).

`inner-debt`, `leasing`, `invest-portfolio` в АС CF — законченные
функциональные единицы со своим состоянием и сервисами, которые открываются
из меню с нескольких экранов.

Страницей такой раздел быть не может: якорь страницы — маршрут, который знает
пользователь, а URL здесь не меняется. Поглотиться страницей он тоже не может:
его зовут и `loader`, и `structuring`, то есть по правилу «ровно одна страница»
он общий. Поэтому раздел **объявляет человек** — граф про него честно говорит
«общий», и спорить с этим инструменту нечем.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.business.model import Anchor
from docpipe.business.resolve import build_context, resolve
from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.web.overrides import Feature, Overrides, load_overrides
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")

INNER_DEBT = Feature(
    name="inner-debt",
    path="src/app/inner-debt",
    title="Внутренний долг",
    reason="Открывается из меню с любого экрана, собственного URL нет",
)


@pytest.fixture
def scanned(web_workspace: Path) -> WebScanResult:
    return run_web(
        web_workspace,
        DocpipeConfig(),
        load_ruleset(RULES, "web"),
        None,
        Overrides(features=[INNER_DEBT]),
    )


def _feature(manifest: Manifest) -> object:
    return next(node for node in manifest.nodes if node.kind == "feature")


def _inside(manifest: Manifest) -> set[str]:
    node = _feature(manifest)
    return {item.title for item in manifest.nodes if item.absorbed_by == node.id}


# --------------------------------------------------------------------------------------
# Состав
# --------------------------------------------------------------------------------------


def test_feature_absorbs_everything_under_its_directory(scanned: WebScanResult) -> None:
    """Граница — каталог, а не достижимость.

    По графу раздел «общий»: его зовут несколько страниц, и правило «ровно одна
    страница» не поглотило бы его никогда. Человек знает, что это одна вещь,
    и говорит это каталогом.
    """
    assert _inside(scanned.manifest) == {
        "AuditState",
        "AuditStateModel",
        "DebtState",
        "DebtStateModel",
        "InnerDebtService",
        "LoadInnerDebts",
        "ResetInnerDebts",
        "SaveInnerDebt",
    }


def test_declared_beats_derived(scanned: WebScanResult) -> None:
    """Там, где раздел и страница спорят, побеждает объявленное.

    `InnerDebtService` без раздела поглощался страницей `ListComponent`
    (до него дотягивалась одна). Инструмент не переигрывает решение, которое
    человек записал явно.
    """
    node = next(item for item in scanned.manifest.nodes if item.title == "InnerDebtService")

    assert node.absorbed_by == "feature:inner-debt"


def test_feature_node_has_no_symbol_but_has_a_document(scanned: WebScanResult) -> None:
    """Раздел не класс, а решение человека о том, что вот эти классы — одна вещь."""
    node = _feature(scanned.manifest)

    assert node.symbol is None
    assert node.doc_path.endswith("features/tr-p/inner-debt.md")
    assert node.title == "Внутренний долг"


def test_feature_calls_are_walked_through_its_own_members(scanned: WebScanResult) -> None:
    """Вызовы сервиса, к которому внутри раздела никто не обращается, обязаны быть.

    Его зовут снаружи, но своего документа у него больше нет: пропав из раздела
    «Данные», такой эндпоинт не появится нигде.
    """
    from docpipe.web.pages import called, index_by_fqn

    walk = called(_feature(scanned.manifest), index_by_fqn(scanned.manifest), 3)
    routes = {call.route for call in walk.calls}

    assert "api/ml/innerdebts/state/byclient/{}" in routes
    assert "api/ml/innerdebts/insert" in routes


def test_pages_are_never_absorbed_by_a_feature(web_workspace: Path) -> None:
    """Экран, лежащий под каталогом раздела, остаётся страницей: у него свой якорь."""
    result = run_web(
        web_workspace,
        DocpipeConfig(),
        load_ruleset(RULES, "web"),
        None,
        Overrides(features=[Feature(name="all", path="src/app", reason="проверка границы")]),
    )

    assert all(node.absorbed_by == "" for node in result.manifest.nodes if node.kind == "page")


def test_directory_boundary_is_a_segment_not_a_substring(web_workspace: Path) -> None:
    """`…/leasing` иначе накрыл бы `…/leasing-report`."""
    result = run_web(
        web_workspace,
        DocpipeConfig(),
        load_ruleset(RULES, "web"),
        None,
        Overrides(features=[Feature(name="inner", path="src/app/inner", reason="проверка")]),
    )

    assert not [node for node in result.manifest.nodes if node.absorbed_by.startswith("feature:")]


def test_empty_feature_is_a_finding(web_workspace: Path) -> None:
    """Каталог переименовали — документ раздела просто не появится.

    Молчать нельзя: в отчёте будет на строку меньше, и это ровно тот случай,
    ради которого заведены находки о протухших правилах.
    """
    result = run_web(
        web_workspace,
        DocpipeConfig(),
        load_ruleset(RULES, "web"),
        None,
        Overrides(features=[Feature(name="gone", path="src/app/gone", reason="переехал")]),
    )

    assert [item.kind for item in result.overrides.stale] == ["feature-empty"]
    assert not [node for node in result.manifest.nodes if node.kind == "feature"]


# --------------------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------------------


def test_feature_without_a_reason_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pages.yaml"
    path.write_text(
        'version: "1"\nfeatures:\n  - name: "x"\n    path: "src/app/x"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError):
        load_overrides(path)


def test_title_defaults_to_the_name() -> None:
    assert Feature(name="inner-debt", path="src/app/x", reason="р").heading == "inner-debt"


# --------------------------------------------------------------------------------------
# Бизнес-слой
# --------------------------------------------------------------------------------------


def test_anchor_resolves_by_the_declared_name(scanned: WebScanResult) -> None:
    """Ключ — имя, а не каталог и не имя класса.

    Каталог сменится при первом переносе, класса у раздела нет вовсе, а имя
    человек объявил сам и им же называет раздел в меню.
    """
    manifest = scanned.manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)
    context = build_context([], empty, web=manifest)

    resolution = resolve(Anchor(kind="feature", ref="inner-debt"), context)

    assert resolution.resolved
    assert resolution.facts["feature"] == "inner-debt"
    assert resolution.targets[0].doc_path.endswith("inner-debt.md")


def test_anchor_facts_carry_state_and_endpoints(scanned: WebScanResult) -> None:
    manifest = scanned.manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)
    facts = resolve(
        Anchor(kind="feature", ref="inner-debt"), build_context([], empty, web=manifest)
    ).facts

    assert facts["states"] == ["innerDebt", "innerDebtAudit"]
    assert "GET api/ml/innerdebts/state/byclient/{}" in facts["endpoints"]
    assert not any("DebtState" in str(value) for value in facts.values())


def test_unknown_feature_is_unresolved_and_says_what_was_tried(scanned: WebScanResult) -> None:
    manifest = scanned.manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)

    resolution = resolve(
        Anchor(kind="feature", ref="leasing"), build_context([], empty, web=manifest)
    )

    assert not resolution.resolved
    assert resolution.tried == ["реестр", "раздел"]


# --------------------------------------------------------------------------------------
# Документ и отчёт
# --------------------------------------------------------------------------------------


def test_document_names_where_the_feature_is_opened_from(
    web_workspace: Path, tmp_path: Path
) -> None:
    """Маршрута у раздела нет — значит «откуда сюда попадают» надо посчитать."""
    pages = tmp_path / "pages.yaml"
    pages.write_text(
        'version: "1"\nfeatures:\n  - name: "inner-debt"\n'
        '    path: "src/app/inner-debt"\n    title: "Внутренний долг"\n'
        '    reason: "меню"\n',
        encoding="utf-8",
    )
    manifest = tmp_path / "web.json"
    docs = tmp_path / "docs"
    docs.mkdir()

    assert (
        runner.invoke(
            app,
            [
                "web",
                "scan",
                "--root",
                str(web_workspace),
                "--pages",
                str(pages),
                "--out",
                str(manifest),
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["materialize", str(manifest), "--root", str(docs)]).exit_code == 0

    text = (docs / "docs/modules/features/tr-p/inner-debt.md").read_text(encoding="utf-8")

    assert "### Откуда открывается" in text
    assert "list-component.md" in text
    assert "### Состав документа" in text
    assert "`DebtState` (state)" in text
    assert "### Маршрут страницы" not in text


def test_report_lists_features_separately(scanned: WebScanResult) -> None:
    """В списке страниц разделу места нет: у него нет маршрута.

    Но и молчать о нём нельзя — это документ, который прогон создаст.
    """
    from docpipe.web.pages import build_report, format_report

    report = build_report(scanned.manifest)

    assert report.counts["features"] == 1
    assert [item.title for item in report.features] == ["Внутренний долг"]
    assert "РАЗДЕЛ Внутренний долг" in format_report(report)


def test_uncovered_feature_is_counted_and_does_not_fail_the_run(scanned: WebScanResult) -> None:
    """Раздел без бизнес-документа — состояние работы, а не дефект каталога.

    Та же природа, что у страниц: линт, красный с первого дня, выключат
    на второй, и вместе с ним пропадут работающие проверки.
    """
    from docpipe.business.catalog import Catalog
    from docpipe.business.lint import INFORMATIONAL, lint

    manifest = scanned.manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)
    report = lint(Catalog(), [], build_context([], empty, web=manifest), "docs/business")

    assert [item.where for item in report.findings if item.check == "features-uncovered"] == [
        "inner-debt"
    ]
    assert "features-uncovered" in INFORMATIONAL
