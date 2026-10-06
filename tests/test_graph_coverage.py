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
from docpipe.graph.entrypoints import from_manifest
from docpipe.graph.web import collect
from docpipe.model import (
    DocNode,
    Endpoint,
    Manifest,
    ParserVersions,
    RouteEntry,
    SourceSpan,
    Symbol,
)

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


# ------------------------------------------------------------------------------------------
# Спецификации (G17 п. 3)
# ------------------------------------------------------------------------------------------


def test_specification_id_lives_on_the_technical_side() -> None:
    """Идентификатор чужой спецификации — атрибут записи реестра.

    Стрелка та же, что у бизнес-слоя: техника ссылается на чужую систему,
    а не наоборот. Обратная сломала бы спецификацию при переименовании класса.
    """
    with_id = entry("job", "Ночная переоценка")
    nodes = (
        with_id.model_copy(update={"attributes": {**with_id.attributes, "spec": "CF-SPEC-42"}}),
        entry("job", "Дневная сверка"),
    )
    report = coverage(nodes, Catalog(docs=[]))

    assert report.with_spec == 1
    assert report.specs == ("CF-SPEC-42",)
    assert report.without_spec_examples == ("job: Дневная сверка",)


def test_no_specifications_is_a_legal_state_and_says_so() -> None:
    """Ноль спецификаций — состояние репозитория, а не пустая строка отчёта."""
    report = coverage((entry("job", "Ночная переоценка"),), Catalog(docs=[]))
    assert report.with_spec == 0
    assert "Это законно" in format_coverage(report)


# --------------------------------------------------------------------------------------
# Корни, собранные настоящими сборщиками, а не руками
# --------------------------------------------------------------------------------------
#
# Тесты выше строят корни функцией `entry`, и в ней `ref` записан явно. У корней,
# которые строит граф, `ref` есть только у записей реестра: страница и эндпоинт
# его не несут, и идентичность собиралась из имени — «Заголовок (маршрут)»
# и «GET маршрут». Якорь же страницы и эндпоинта — маршрут. Совпасть они
# не могли, а тесты этого не видели, потому что корни были рукодельные.


def _manifest(*nodes: DocNode) -> Manifest:
    return Manifest(
        ruleset_version="тест", parser=ParserVersions(tree_sitter="0.0"), nodes=list(nodes)
    )


def _node(name: str, kind: str, path: str, **fields: object) -> DocNode:
    symbol = Symbol(
        fqn=f"App.{name}",
        name=name,
        type_kind="class",
        namespace="App",
        module="app",
        sources=[SourceSpan(path=path, start=1, end=20)],
    )
    return DocNode(
        id=f"type:app#{symbol.fqn}",
        kind=kind,
        template=kind,
        title=name,
        doc_path=f"docs/{name}.md",
        module="app",
        domain="app",
        symbol=symbol,
        signature_hash="sha256:0",
        **fields,  # type: ignore[arg-type]
    )


def _page_roots() -> tuple[GraphNode, ...]:
    page = _node(
        "QuizComponent",
        "page",
        "src/app/quiz.component.ts",
        routes=[
            RouteEntry(
                component="App.QuizComponent",
                path="models/loader/quiz",
                source="src/app/routes.ts",
                table="routes",
            )
        ],
    )
    nodes, _, _ = collect(_manifest(page), set())
    return tuple(node for node in nodes if node.kind == "entry_point")


def _http_roots() -> tuple[GraphNode, ...]:
    controller = _node(
        "PricingController",
        "controller",
        "src/Api/PricingController.cs",
        endpoints=[
            Endpoint(http_method="GET", route="api/v1/Pricing/{id:guid}", member="Get", line=10),
            Endpoint(http_method="PUT", route="api/v1/Pricing/{id:guid}", member="Put", line=20),
        ],
    )
    return tuple(from_manifest(_manifest(controller)))


def test_page_anchor_covers_the_page_built_by_the_graph() -> None:
    """Якорь страницы — маршрут в той форме, в какой его пишет аналитик."""
    catalog = Catalog(docs=[document("bp.x", [Anchor(kind="page", ref="/models/loader/quiz")])])

    report = coverage(_page_roots(), catalog)

    assert report.covered == 1
    assert report.anchors_without_entry_point == ()


def test_http_anchor_covers_every_method_on_the_route() -> None:
    """Якорь `http` — маршрут без метода (`entry-guide.md`); на одном маршруте
    бывают GET и PUT, и документ о маршруте описывает оба."""
    catalog = Catalog(docs=[document("bp.x", [Anchor(kind="http", ref="api/v1/pricing/{id}")])])

    report = coverage(_http_roots(), catalog)

    assert report.covered == 2
    assert report.anchors_without_entry_point == ()


def test_anchor_of_a_kind_the_graph_has_no_roots_for_is_not_a_finding() -> None:
    """Разделы фронта, типы, таблицы, топики точками входа в графе не бывают:
    таблица там — узел данных, топик — шов. Назвать такой якорь «без точки
    входа» — значит объявить находкой то, что граф проверить не может;
    проверяет их `business lint`. Отчёт называет их отдельной строкой."""
    anchors = [
        Anchor(kind="feature", ref="inner-debt"),
        Anchor(kind="type", ref="App.PricingService"),
        Anchor(kind="table", ref="UserTasks"),
        Anchor(kind="kafka", ref="limits.published"),
    ]
    catalog = Catalog(docs=[document("bp.x", anchors)])

    report = coverage(_page_roots(), catalog)

    assert report.anchors_without_entry_point == ()
    assert report.anchors_unchecked == (
        "bp.x: feature inner-debt",
        "bp.x: kafka limits.published",
        "bp.x: table UserTasks",
        "bp.x: type App.PricingService",
    )
    assert "не проверяет" in format_coverage(report)


def test_wrong_route_on_a_page_anchor_is_still_a_finding() -> None:
    """Вид проверяемый, точки входа нет — это находка, как и раньше."""
    catalog = Catalog(docs=[document("bp.x", [Anchor(kind="page", ref="/models/loader/exam")])])

    report = coverage(_page_roots(), catalog)

    assert report.anchors_without_entry_point == ("bp.x: page /models/loader/exam",)
