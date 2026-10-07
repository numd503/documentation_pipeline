"""Кандидаты в разделы без маршрута (S14): вопрос строится из достижимости страниц.

Без раздела общий узел остаётся отдельными документами: страницей он
не поглощается, потому что до него дотягиваются несколько (`web/absorb.py`),
а раздел объявляет только человек (`docs/pages.md`, P16). Вопрос «этот
каталог со своим состоянием и сервисами — раздел?» инструмент строит
из фактов: какие узлы общие, каких они видов и с каких страниц открываются.

Положительный пример — фикстура `WebSections`: в `WebWorkspace` его нет,
а дополнять её нельзя — на её числа завязаны тесты страниц. Крайние случаи
(подъём, корень модуля, длинный список страниц) — на манифесте, собранном
руками: фикстура под каждый из них была бы втрое больше самой находки.
"""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import DocNode, Manifest, ParserVersions, RouteEntry, SourceSpan, Symbol, Usage
from docpipe.setup.candidates import (
    KINDS,
    MAX_LEVELS,
    CandidateInputs,
    FeatureCandidates,
    candidates,
    candidates_json,
    feature_candidates,
    format_candidates,
)
from docpipe.web.absorb import reachable_from
from docpipe.web.overrides import Feature, Overrides
from docpipe.web.pages import index_by_fqn
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()

WEB_SECTIONS = Path(__file__).parent / "fixtures" / "WebSections"
RULES = Path("rules/rules.yaml")

SHARED = "src/app/shared-orders"
STATE = "src/app/shared-orders/state/orders.state.OrdersState"
API = "src/app/shared-orders/api/orders-api.service.OrdersApiService"
HISTORY = "src/app/orders-detail/order-history.service.OrderHistoryService"

ORDERS = Feature(
    name="orders",
    path=SHARED,
    title="Заказы",
    reason="Состояние и сервисы заказов открываются со списка и с карточки",
)


def _scan(root: Path, overrides: Overrides | None = None) -> WebScanResult:
    return run_web(root, DocpipeConfig(), load_ruleset(RULES, "web"), None, overrides)


@pytest.fixture(scope="module")
def sections() -> WebScanResult:
    return _scan(WEB_SECTIONS)


def _node(manifest: Manifest, fqn: str) -> DocNode:
    return next(node for node in manifest.nodes if node.symbol and node.symbol.fqn == fqn)


def _pages(manifest: Manifest) -> list[DocNode]:
    return sorted((node for node in manifest.nodes if node.kind == "page"), key=lambda n: n.id)


# --------------------------------------------------------------------------------------
# Фикстура: конструкции, а не файлы
# --------------------------------------------------------------------------------------


def test_fixture_has_two_routed_pages(sections: WebScanResult) -> None:
    """Обе страницы — из таблицы роутов, вторая с параметром маршрута."""
    routes = {
        page.title: [entry.path for entry in page.routes] for page in _pages(sections.manifest)
    }

    assert routes == {"OrdersDetailComponent": ["orders/{}"], "OrdersListComponent": ["orders"]}


def test_both_pages_reach_the_shared_state_and_api_service(sections: WebScanResult) -> None:
    """Обе страницы дотягиваются и до стейта, и до сервиса раздела.

    Импорт идёт через алиас `@app/*` из `tsconfig.json` с комментариями
    (JSONC): без его разбора рёбер не было бы ни у одной страницы.
    """
    by_fqn = index_by_fqn(sections.manifest)

    for page in _pages(sections.manifest):
        assert {STATE, API} <= reachable_from(page, by_fqn), page.title


def test_pages_reach_the_state_by_dispatch(sections: WebScanResult) -> None:
    """До стейта страницы доходят диспатчем экшена, а не внедрением.

    Тип экшена живёт на ребре: это и есть цепочка `dispatch(new X())` →
    `@Action(X)`, которой у половины экранов боевого фронта нет замены.
    """
    actions = {
        page.title: sorted(usage.action for usage in page.uses if usage.target == STATE)
        for page in _pages(sections.manifest)
    }

    assert "[Orders] Load" in actions["OrdersListComponent"]
    assert "[Orders] Select" in actions["OrdersDetailComponent"]


def test_shared_nodes_are_of_two_kinds_and_stay_their_own_documents(
    sections: WebScanResult,
) -> None:
    """Виды — от правил `web`: стейт по `@State`, сервис повышен по HTTP-вызовам."""
    manifest = sections.manifest

    assert _node(manifest, STATE).kind == "state"
    assert _node(manifest, API).kind == "api-service"
    assert _node(manifest, STATE).absorbed_by == ""
    assert _node(manifest, API).absorbed_by == ""


def test_detail_page_has_a_private_service(sections: WebScanResult) -> None:
    """Приватный сервис достижим одной страницей и описывается внутри неё."""
    manifest = sections.manifest
    detail = next(page for page in _pages(manifest) if page.title == "OrdersDetailComponent")
    by_fqn = index_by_fqn(manifest)

    assert _node(manifest, HISTORY).absorbed_by == detail.id
    assert [page.title for page in _pages(manifest) if HISTORY in reachable_from(page, by_fqn)] == [
        "OrdersDetailComponent"
    ]


# --------------------------------------------------------------------------------------
# Критерии приёмки
# --------------------------------------------------------------------------------------


def test_shared_directory_is_the_candidate(sections: WebScanResult) -> None:
    """Подъём от `state/` и от `api/` останавливается на общем каталоге.

    В каждом подкаталоге общий узел один и одного вида; два вида впервые
    встречаются в `shared-orders`, — он и кандидат, и он один.
    """
    report = feature_candidates(sections.manifest, Overrides())

    assert [item.path for item in report.items] == [SHARED]
    item = report.items[0]
    assert item.pages == ["/orders", "/orders/{}"]
    assert item.page_count == 2
    assert item.nodes == 2
    assert item.kinds == {"api-service": 1, "state": 1}
    assert item.declared is False
    assert item.examples == [
        "src/app/shared-orders/api/orders-api.service.ts:10",
        "src/app/shared-orders/state/orders.state.ts:21",
    ]
    assert (report.pages_total, report.shared_nodes, report.total) == (2, 2, 1)


def test_private_service_is_not_a_candidate(sections: WebScanResult) -> None:
    report = feature_candidates(sections.manifest, Overrides())

    assert not [item for item in report.items if item.path.startswith("src/app/orders-detail")]


def test_declared_feature_is_marked_and_still_found() -> None:
    """После объявления — `declared: true`, и кандидат никуда не делся.

    Объявленный раздел меняет `absorbed_by` у узлов под каталогом, а пустое
    `absorbed_by` и так значит и «две страницы», и «ни одной». Считай
    кандидаты по нему — каталог исчез бы из списка сразу после объявления,
    и отметка «уже объявлен» не встретилась бы никогда.
    """
    scanned = _scan(WEB_SECTIONS, Overrides(features=[ORDERS]))

    assert _node(scanned.manifest, STATE).absorbed_by == "feature:orders"
    report = feature_candidates(scanned.manifest, Overrides(features=[ORDERS]))
    assert [(item.path, item.declared, item.page_count) for item in report.items] == [
        (SHARED, True, 2)
    ]


def test_declared_ancestor_covers_the_candidate_by_segment(sections: WebScanResult) -> None:
    """Накрывает раздел-предок; раздел с тем же началом имени — нет."""

    def declared(path: str) -> bool:
        feature = Feature(name="x", path=path, reason="проверка")
        return (
            feature_candidates(sections.manifest, Overrides(features=[feature])).items[0].declared
        )

    assert declared("src/app")
    assert declared("src/app/shared-orders/")
    assert not declared("src/app/shared")
    assert not declared("src/app/shared-orders/state")


def test_web_workspace_has_no_candidates(web_workspace: Path) -> None:
    """`inner-debt` достижим одной страницей, а общий узел один — `AuditService`.

    Число общих узлов в отчёте отличает этот ноль от нуля без страниц.
    """
    report = feature_candidates(_scan(web_workspace).manifest, Overrides())

    assert report.items == []
    assert (report.pages_total, report.shared_nodes) == (5, 1)


# --------------------------------------------------------------------------------------
# Подъём и его границы: манифест, собранный руками
# --------------------------------------------------------------------------------------


def _symbol_node(fqn: str, kind: str, path: str, module: str, line: int = 1) -> DocNode:
    name = fqn.rsplit(".", 1)[-1]
    symbol = Symbol(
        fqn=fqn,
        name=name,
        type_kind="class",
        namespace="",
        module=module,
        sources=[SourceSpan(path=path, start=line, end=line + 5)],
    )
    return DocNode(
        id=f"type:{module}#{fqn}`0",
        kind=kind,
        template=kind,
        title=name,
        doc_path=f"docs/{name}.md",
        module="app",
        domain="",
        symbol=symbol,
        signature_hash=name,
    )


def _page(name: str, route: str, targets: list[str], unresolved: bool = False) -> DocNode:
    node = _symbol_node(f"src/pages/{name}.{name}", "page", f"src/pages/{name}.ts", "src")
    return node.model_copy(
        update={
            "routes": [RouteEntry(path=route, component=name, route_unresolved=unresolved)],
            "uses": [Usage(target=target, member="run") for target in targets],
        }
    )


def _manifest(*nodes: DocNode) -> Manifest:
    return Manifest(ruleset_version="x", parser=ParserVersions(tree_sitter="0"), nodes=list(nodes))


def _shared(state: str, service: str, module: str = "src", pages: int = 2) -> Manifest:
    """Стейт и сервис в данных файлах, до обоих дотягиваются `pages` страниц."""
    targets = [f"{state}.State", f"{service}.Service"]
    return _manifest(
        _symbol_node(targets[0], "state", f"{state}.ts", module),
        _symbol_node(targets[1], "service", f"{service}.ts", module),
        *(_page(f"p{index:02}", f"route{index:02}", targets) for index in range(pages)),
    )


def _paths(manifest: Manifest) -> list[str]:
    return [item.path for item in feature_candidates(manifest, Overrides()).items]


def test_climb_stops_after_three_levels() -> None:
    """Общий предок ровно на третьем уровне — кандидат, на четвёртом — нет.

    Выше трёх уровней кандидатом становился бы каталог всего приложения.
    """
    assert MAX_LEVELS == 3
    assert _paths(_shared("src/a/b/c/d/s", "src/a/x/y/z/svc")) == ["src/a"]
    assert _paths(_shared("src/a/b/c/d/e/s", "src/a/x/y/z/w/svc")) == []


def test_climb_stops_at_the_module_root() -> None:
    """Выше корня модуля лежит соседний модуль: раздел оттуда склеил бы два приложения."""
    two_modules = _manifest(
        _symbol_node("apps/one/state/s.State", "state", "apps/one/state/s.ts", "apps/one"),
        _symbol_node("apps/two/api/a.Service", "service", "apps/two/api/a.ts", "apps/two"),
        _page("p1", "one", ["apps/one/state/s.State", "apps/two/api/a.Service"]),
        _page("p2", "two", ["apps/one/state/s.State", "apps/two/api/a.Service"]),
    )

    assert _paths(two_modules) == []
    assert _paths(_shared("apps/one/state/s", "apps/two/api/a", module="apps")) == ["apps"]


def test_repository_root_is_never_a_candidate() -> None:
    """Пустой путь в `features[].path` не объявить — и предлагать его нечего."""
    assert _paths(_shared("state/s", "api/a", module="")) == []


def test_single_kind_is_not_a_section() -> None:
    """Два общих сервиса одного вида — общие сервисы, а не раздел."""
    manifest = _manifest(
        _symbol_node("src/shared/a.Service", "service", "src/shared/a.ts", "src"),
        _symbol_node("src/shared/b.Service", "service", "src/shared/b.ts", "src"),
        _page("p1", "one", ["src/shared/a.Service", "src/shared/b.Service"]),
        _page("p2", "two", ["src/shared/a.Service", "src/shared/b.Service"]),
    )

    assert _paths(manifest) == []


def test_node_reached_by_one_page_does_not_make_a_candidate() -> None:
    """Сервис, до которого дотягивается одна страница, поглощён ею — он не общий."""
    manifest = _manifest(
        _symbol_node("src/orders/state/s.State", "state", "src/orders/state/s.ts", "src"),
        _symbol_node("src/orders/api/a.Service", "service", "src/orders/api/a.ts", "src"),
        _page("p1", "one", ["src/orders/state/s.State", "src/orders/api/a.Service"]),
        _page("p2", "two", ["src/orders/state/s.State"]),
    )

    report = feature_candidates(manifest, Overrides())

    assert report.items == []
    assert report.shared_nodes == 1


def test_long_page_list_is_cut_and_counted() -> None:
    """Каталог, до которого дотягиваются все страницы, не съедает ответ инструмента."""
    item = feature_candidates(
        _shared("src/orders/state/s", "src/orders/api/a", pages=12), Overrides()
    ).items[0]

    assert item.page_count == 12
    assert len(item.pages) == 10
    assert item.pages[0] == "/route00"
    assert "открывается со страниц (12)" in format_candidates(
        FeatureCandidates(pages_total=12, shared_nodes=2, total=1, offset=0, items=[item])
    )


def test_unresolved_route_is_marked_as_in_csv() -> None:
    targets = ["src/orders/state/s.State", "src/orders/api/a.Service"]
    manifest = _manifest(
        _symbol_node(targets[0], "state", "src/orders/state/s.ts", "src"),
        _symbol_node(targets[1], "service", "src/orders/api/a.ts", "src"),
        _page("p1", "orders", targets, unresolved=True),
        _page("p2", "orders/list", targets),
    )

    assert feature_candidates(manifest, Overrides()).items[0].pages == ["/orders/list", "?/orders"]


# --------------------------------------------------------------------------------------
# Текст, точка входа, команда
# --------------------------------------------------------------------------------------


def test_text_names_what_to_write_and_the_base(sections: WebScanResult) -> None:
    text = format_candidates(feature_candidates(sections.manifest, Overrides()))

    assert "Страниц 2; общих узлов 2." in text
    assert "src/app/shared-orders\n" in text
    assert "общих узлов 2: api-service 1, state 1" in text
    assert "открывается со страниц (2): /orders, /orders/{}" in text
    assert 'в pages.yaml: features[].path: "src/app/shared-orders"' in text
    assert text.endswith("Показаны 1–1 из 1.\n")


def test_no_pages_is_said_out_loud(tmp_path: Path) -> None:
    """Достижимость считается от страниц: без них ноль кандидатов ничего не значит."""
    report = candidates("features", CandidateInputs(tmp_path, DocpipeConfig(), use_cache=False))

    assert isinstance(report, FeatureCandidates)
    assert (report.pages_total, report.total) == (0, 0)
    assert "Страниц нет" in format_candidates(report)


def test_kind_is_known_to_the_entry_point() -> None:
    assert "features" in KINDS
    report = candidates(
        "features", CandidateInputs(WEB_SECTIONS, DocpipeConfig(), use_cache=False), limit=0
    )

    assert isinstance(report, FeatureCandidates)
    assert [item.path for item in report.items] == [SHARED]


def test_two_runs_give_the_same_bytes() -> None:
    inputs = CandidateInputs(WEB_SECTIONS, DocpipeConfig(), use_cache=False)

    assert candidates_json(candidates("features", inputs)) == candidates_json(
        candidates("features", inputs)
    )


def _copy(tmp_path: Path) -> Path:
    """Копия фикстуры: команда пишет кэш разбора под `--root`, как `web scan`."""
    root = tmp_path / "repo"
    shutil.copytree(WEB_SECTIONS, root)
    return root


def test_command_prints_json_report(tmp_path: Path) -> None:
    root = _copy(tmp_path)

    result = runner.invoke(
        app, ["setup", "candidates", "features", "--root", str(root), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema_version"] == "1.0"
    assert [item["path"] for item in data["items"]] == [SHARED]
    assert data["items"][0]["declared"] is False


def test_command_reads_declared_features_from_web_pages(tmp_path: Path) -> None:
    """Объявленный раздел команда узнаёт из того же `web.pages`, что и `web scan`."""
    root = _copy(tmp_path)
    (root / "pages.yaml").write_text(
        'version: "1"\nfeatures:\n'
        f'  - name: "orders"\n    path: "{SHARED}"\n    reason: "список и карточка"\n',
        encoding="utf-8",
    )
    config = root / "docpipe.yaml"
    config.write_text("web:\n  pages: pages.yaml\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["setup", "candidates", "features", "--root", str(root), "--config", str(config)],
    )

    assert result.exit_code == 0, result.output
    assert f"{SHARED}  [уже объявлен в pages.yaml]" in result.output
    assert "в pages.yaml: features[].path" not in result.output


def test_named_and_missing_pages_file_is_a_config_error(tmp_path: Path) -> None:
    """Названный и ненайденный `pages.yaml` — отказ, как у `web scan`, а не пустые правила.

    Иначе отметка «уже объявлен» молча пропала бы у всех кандидатов.
    """
    root = _copy(tmp_path)
    config = root / "docpipe.yaml"
    config.write_text("web:\n  pages: missing.yaml\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["setup", "candidates", "features", "--root", str(root), "--config", str(config)],
    )

    assert result.exit_code == 2
    assert "не найден" in result.output
