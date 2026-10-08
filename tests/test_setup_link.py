"""Сводка шва кластерами (`setup link`, S21).

Несвязанное отчёта связи — группами по ключу, с числом мест и примерами,
и у вызовов без эндпоинта по модулю — подсказка записи `web.url_rewrite`.
Главный критерий: подсказка обещает `would_link`, и ровно столько мест
кластера связывается, когда правило записано и прогон повторён.

Прогоны — на копиях `SeamWorkspace` в `tmp_path` с правилами S19 (обёртки
и построитель): без них почти все вызовы фикстуры — находки, а не вызовы.
`url_rewrite` в копиях нет. Чтобы правилу префикса было что связывать,
у одной копии префикс базы контроллеров — `v2` вместо `api` (так на squidex
до S17 эндпоинты расходились с фронтом на `api/`), у другой ещё и прямой
вызов `info.service.ts` перенаправлен на `api/orders` — связанный сейчас,
его правило развязало бы. Правила классификации — абсолютным путём:
см. docstring `tests/test_seam_fixture.py` о первой ступени `resolve_input`.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.route import RouteKey
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.link import (
    NOTE_MODULE_HAS_RULE,
    NOTE_NO_GAIN,
    LinkClusters,
    check_query,
    clusters_of,
    format_link_clusters,
    link_clusters,
    link_clusters_json,
)
from docpipe.web.calls import REASON_CONCAT_BASE, REASON_MUTABLE_FIELD, REASON_VARIABLE
from docpipe.web.link import CallRef, EndpointRef, Link, LinkReport

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
CONSTANTS: Final = "backend/Seam.Api/Web/Constants.cs"
INFO: Final = "frontend/src/app/services/info.service.ts"
APPS: Final = "frontend/src/app/services/apps.service.ts"
LINKS: Final = "frontend/src/app/services/links.service.ts"
EDITOR: Final = "frontend/src/app/components/editor.component.ts"
MODULE: Final = "seam-web"

# Правила S19 (`tests/test_http_wrappers.SEAM_RULES`) — словарём: копия
# дописывает их в секцию `web`, а тест подсказки — ещё и `url_rewrite`.
S19_WEB: Final[dict[str, Any]] = {
    "http_wrappers": [
        {
            "receiver": "HTTP",
            "method_regex": "^(get|post|put|delete)Versioned$",
            "url": {"arg": 1},
            "http_method": {"from_name": True},
        },
        {
            "receiver": "HTTP",
            "method": "requestVersioned",
            "url": {"arg": 2},
            "http_method": {"arg": 1},
        },
        {
            "receiver": "rest",
            "method": "request",
            "url": {"arg": 0, "field": "url"},
            "http_method": {"arg": 0, "field": "method"},
        },
    ],
    "url_builders": [{"receiver": "apiUrl", "method": "buildUrl", "path": {"arg": 0}}],
}


def _copy_seam(
    root: Path,
    *,
    prefix: str = "api",
    info_route: str = "api/info",
    rewrite: dict[str, str] | None = None,
    link: dict[str, Any] | None = None,
) -> Path:
    """Копия фикстуры с правилами S19; префикс базы и прямой вызов — по аргументам."""
    shutil.copytree(SEAM, root)
    constants = root / CONSTANTS
    text = constants.read_text(encoding="utf-8")
    assert 'PrefixApi = "api"' in text  # конструкция фикстуры на месте
    constants.write_text(text.replace('PrefixApi = "api"', f'PrefixApi = "{prefix}"'), "utf-8")
    info = root / INFO
    text = info.read_text(encoding="utf-8")
    assert "'api/info'" in text
    info.write_text(text.replace("'api/info'", f"'{info_route}'"), encoding="utf-8")

    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(RULES.resolve())
    raw["web"].update(S19_WEB)
    if rewrite is not None:
        raw["web"]["url_rewrite"] = [rewrite]
    if link is not None:
        raw["link"] = link
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return root


def _context(root: Path) -> SetupContext:
    # Без кэша: копия в `tmp_path`, но кэш под `--root` всё равно не нужен.
    return SetupContext.build(root, root / "docpipe.yaml", use_cache=False)


@pytest.fixture(scope="module")
def seam(tmp_path_factory: pytest.TempPathFactory) -> SetupContext:
    """Префикс как в фикстуре: связано шесть вызовов, три — без эндпоинта."""
    return _context(_copy_seam(tmp_path_factory.mktemp("seam") / "ws"))


@pytest.fixture(scope="module")
def v2(tmp_path_factory: pytest.TempPathFactory) -> SetupContext:
    """Префикс базы `v2`: фронт зовёт `api/…`, и не связано ничего."""
    return _context(_copy_seam(tmp_path_factory.mktemp("v2") / "ws", prefix="v2"))


@pytest.fixture(scope="module")
def mixed(tmp_path_factory: pytest.TempPathFactory) -> SetupContext:
    """Префикс `v2` и прямой вызов `api/orders` — связан сейчас через базу с токеном."""
    root = tmp_path_factory.mktemp("mixed") / "ws"
    return _context(_copy_seam(root, prefix="v2", info_route="api/orders"))


def _exact(report: LinkReport) -> set[tuple[str, int]]:
    return {(link.file, link.line) for link in report.links if link.match == "exact"}


def _orphans(report: LinkReport) -> set[tuple[str, int]]:
    return {(call.file, call.line) for call in report.calls_without_endpoint}


# --------------------------------------------------------------------------------------
# Критерии приёмки
# --------------------------------------------------------------------------------------


def test_suggested_rewrite_links_what_it_promises(v2: SetupContext, tmp_path: Path) -> None:
    """`would_link` — ровно те места кластера, что связались после записи правила."""
    clusters = link_clusters(v2, by="module")
    [cluster] = clusters.clusters
    assert (cluster.key, cluster.count) == (MODULE, 9)
    rewrite = cluster.suggested_rewrite
    assert rewrite is not None and cluster.rewrite_note == ""
    assert (rewrite.module, rewrite.strip_prefix, rewrite.add_prefix) == (MODULE, "api", "v2")
    assert (rewrite.would_link, rewrite.would_unlink) == (6, 0)

    before = v2.link
    rule = {"module": rewrite.module, "strip_prefix": "api", "add_prefix": "v2"}
    after = _context(_copy_seam(tmp_path / "ws", prefix="v2", rewrite=rule)).link

    assert len(_orphans(before) & _exact(after)) == rewrite.would_link
    assert len(_exact(before) - _exact(after)) == rewrite.would_unlink
    assert after.counts["calls_without_endpoint"] == (
        before.counts["calls_without_endpoint"] - rewrite.would_link
    )


def test_rule_that_unlinks_is_counted_and_verified_by_a_run(
    mixed: SetupContext, tmp_path: Path
) -> None:
    """Правило одно на модуль: развязанное считается, и прогон это подтверждает."""
    [cluster] = link_clusters(mixed, by="module").clusters
    rewrite = cluster.suggested_rewrite
    assert rewrite is not None
    assert (rewrite.strip_prefix, rewrite.add_prefix) == ("api", "v2")
    assert (rewrite.would_link, rewrite.would_unlink) == (5, 1)

    before = mixed.link
    assert (INFO, 12) in _exact(before)  # `GET api/orders` — через `api/[controller]`
    rule = {"module": MODULE, "strip_prefix": "api", "add_prefix": "v2"}
    root = _copy_seam(tmp_path / "ws", prefix="v2", info_route="api/orders", rewrite=rule)
    after = _context(root).link

    assert len(_orphans(before) & _exact(after)) == rewrite.would_link
    assert _exact(before) - _exact(after) == {(INFO, 12)}


def test_by_reason_splits_hypermedia_from_the_mutable_field(seam: SetupContext) -> None:
    clusters = link_clusters(seam, category="calls_unresolved", by="reason")
    by_key = {cluster.key: cluster for cluster in clusters.clusters}
    assert sorted(by_key) == sorted([REASON_CONCAT_BASE, REASON_MUTABLE_FIELD, REASON_VARIABLE])
    assert [item.file for item in by_key[REASON_VARIABLE].examples] == [LINKS]
    assert [item.file for item in by_key[REASON_MUTABLE_FIELD].examples] == [EDITOR]
    assert by_key[REASON_MUTABLE_FIELD].examples[0].text == "GET this.fileSource"
    assert (clusters.places, clusters.total) == (3, 3)


def test_two_runs_give_the_same_bytes(tmp_path: Path) -> None:
    root = _copy_seam(tmp_path / "ws", prefix="v2")
    outputs = []
    for output_format in ("json", "json", "text", "text"):
        result = runner.invoke(
            app,
            [
                "setup",
                "link",
                "--root",
                str(root),
                "--config",
                str(root / "docpipe.yaml"),
                "--by",
                "module",
                "--format",
                output_format,
                "--no-cache",
            ],
        )
        assert result.exit_code == 0, result.output
        outputs.append(result.output)
    assert outputs[0] == outputs[1] and outputs[2] == outputs[3]
    payload = json.loads(outputs[0])
    assert payload["clusters"][0]["suggested_rewrite"]["would_link"] == 6
    assert "strip_prefix: 'api', add_prefix: 'v2'" in outputs[2]
    assert not (root / ".docpipe").exists()  # `--no-cache`: в корень не пишется ничего


# --------------------------------------------------------------------------------------
# Подсказка: когда её нет
# --------------------------------------------------------------------------------------


def test_no_suggestion_when_no_prefix_helps(seam: SetupContext) -> None:
    """Префиксы совпадают: оставшиеся три вызова префиксом не связать."""
    [cluster] = link_clusters(seam, by="module").clusters
    assert cluster.suggested_rewrite is None
    assert cluster.rewrite_note == NOTE_NO_GAIN
    assert sorted(item.text for item in cluster.examples) == [
        "GET api/apps/archived",
        "GET api/apps/search",
        "GET feed.json",
    ]


def test_no_suggestion_over_an_existing_rule(tmp_path: Path) -> None:
    """Ключи собраны после правила модуля: пара поверх них сложила бы два правила."""
    rule = {"module": MODULE, "strip_prefix": "api", "add_prefix": "v2"}
    ctx = _context(_copy_seam(tmp_path / "ws", prefix="v2", rewrite=rule))
    [cluster] = link_clusters(ctx, by="module").clusters
    assert cluster.suggested_rewrite is None
    assert cluster.rewrite_note == NOTE_MODULE_HAS_RULE


def test_empty_rule_does_not_block_the_suggestion(tmp_path: Path) -> None:
    """Пустая запись — «проверено, преобразования нет»: ключи как написаны."""
    rule = {"module": MODULE, "reason": "proxy.conf без pathRewrite"}
    ctx = _context(_copy_seam(tmp_path / "ws", prefix="v2", rewrite=rule))
    [cluster] = link_clusters(ctx, by="module").clusters
    assert cluster.suggested_rewrite is not None
    assert cluster.suggested_rewrite.would_link == 6


# --------------------------------------------------------------------------------------
# Выбор пары и повторы — на отчёте в памяти
# --------------------------------------------------------------------------------------


def _call(route: str, *, line: int, caller: str = "svc", file: str = "a.ts") -> CallRef:
    return CallRef(
        http_method="GET",
        route=route,
        caller=caller,
        module="web",
        file=file,
        line=line,
        confidence="high",
    )


def _keys(*routes: str) -> dict[RouteKey, object]:
    return {RouteKey(http_method="GET", route=route): [] for route in routes}


def test_tie_goes_to_the_shorter_strip() -> None:
    """`api` и `api/apps` + `apps` связывают одно и то же — выигрывает короткий срез."""
    report = LinkReport(calls_without_endpoint=[_call("api/apps", line=1)])
    result = clusters_of(report, DocpipeConfig(), _keys("apps"), by="module")
    rewrite = result.clusters[0].suggested_rewrite
    assert rewrite is not None
    assert (rewrite.strip_prefix, rewrite.add_prefix, rewrite.would_link) == ("api", "", 1)


def test_pair_that_unlinks_as_much_as_it_links_is_not_a_suggestion() -> None:
    linked = Link(
        http_method="GET",
        route="api/x",
        caller="svc",
        module="web",
        file="b.ts",
        line=1,
        match="exact",
    )
    one = LinkReport(links=[linked], calls_without_endpoint=[_call("api/y", line=1)])
    keys = _keys("api/x", "y", "z")
    [cluster] = clusters_of(one, DocpipeConfig(), keys, by="module").clusters
    assert (cluster.suggested_rewrite, cluster.rewrite_note) == (None, NOTE_NO_GAIN)

    two = one.model_copy(
        update={"calls_without_endpoint": [_call("api/y", line=1), _call("api/z", line=2)]}
    )
    [cluster] = clusters_of(two, DocpipeConfig(), keys, by="module").clusters
    rewrite = cluster.suggested_rewrite
    assert rewrite is not None
    assert (rewrite.strip_prefix, rewrite.would_link, rewrite.would_unlink) == ("api", 2, 1)


def test_route_without_a_fixed_segment_does_not_choose_the_pair() -> None:
    """`GET ''` + `add_prefix: api` = корень API: совпадение, а не правило (squidex)."""
    alone = LinkReport(calls_without_endpoint=[_call("", line=1)])
    keys = _keys("api", "api/apps")
    [cluster] = clusters_of(alone, DocpipeConfig(), keys, by="module").clusters
    assert (cluster.suggested_rewrite, cluster.rewrite_note) == (None, NOTE_NO_GAIN)

    # Пару выбрал настоящий маршрут — `would_link` считает и пустой: столько свяжет прогон.
    both = LinkReport(calls_without_endpoint=[_call("", line=1), _call("apps", line=2)])
    [cluster] = clusters_of(both, DocpipeConfig(), keys, by="module").clusters
    rewrite = cluster.suggested_rewrite
    assert rewrite is not None
    assert (rewrite.strip_prefix, rewrite.add_prefix, rewrite.would_link) == ("", "api", 2)


def test_a_call_on_two_nodes_is_one_place() -> None:
    """Вызов, приписанный двум узлам файла (S16, п. 6), — одно место, а не два."""
    report = LinkReport(
        calls_without_endpoint=[
            _call("api/apps", line=7, caller="dto"),
            _call("api/apps", line=7, caller="service"),
            _call("api/other", line=9, caller="service"),
        ]
    )
    by_module = clusters_of(report, DocpipeConfig(), _keys("apps"), by="module")
    assert (by_module.places, by_module.clusters[0].count) == (2, 2)
    assert by_module.categories["calls_without_endpoint"] == 2
    rewrite = by_module.clusters[0].suggested_rewrite
    assert rewrite is not None and rewrite.would_link == 1  # место, а не две записи

    by_node = clusters_of(report, DocpipeConfig(), _keys(), by="controller")
    assert [(item.key, item.count) for item in by_node.clusters] == [("service", 2), ("dto", 1)]
    assert by_node.places == 2


def test_endpoints_count_entries_not_lines() -> None:
    """`AcceptVerbs("GET", "POST")`: одна строка, два эндпоинта — два конца шва."""

    def endpoint(method: str) -> EndpointRef:
        return EndpointRef(
            http_method=method,
            route="api/verbs",
            node="verbs",
            member="Echo",
            module="Api",
            file="Verbs.cs",
            line=10,
        )

    report = LinkReport(endpoints_without_caller=[endpoint("GET"), endpoint("POST")])
    result = clusters_of(report, DocpipeConfig(), {}, category="endpoints_without_caller")
    assert (result.by, result.places) == ("controller", 2)
    assert [item.text for item in result.clusters[0].examples] == [
        "GET api/verbs (Echo)",
        "POST api/verbs (Echo)",
    ]


# --------------------------------------------------------------------------------------
# Категории, ключи и страница
# --------------------------------------------------------------------------------------


def test_overview_counts_every_category(seam: SetupContext) -> None:
    clusters = link_clusters(seam)
    assert (clusters.category, clusters.by) == ("calls_without_endpoint", "module")
    assert clusters.linked == 6
    assert clusters.categories == {
        "calls_without_endpoint": 3,
        "calls_unresolved": 3,
        "endpoints_without_caller": 9,
        "almost": 0,
        "external_targets": 0,
        "external_callers": 0,
        "declared_unresolvable": 0,
    }


def test_decided_categories_cluster_by_decision(tmp_path: Path) -> None:
    section = {
        "external_targets": [{"host": "ext.example.org", "reason": "лента партнёра"}],
        "external_callers": [{"route": "content/**", "reason": "публичный API"}],
        "unresolvable": [{"path": "**/links.service.ts", "reason": "гипермедиа"}],
    }
    ctx = _context(_copy_seam(tmp_path / "ws", link=section))

    targets = link_clusters(ctx, category="external_targets")
    assert [(item.key, item.count) for item in targets.clusters] == [("ext.example.org", 1)]
    hosts = link_clusters(ctx, category="external_targets", by="host")
    assert [item.key for item in hosts.clusters] == ["ext.example.org"]

    callers = link_clusters(ctx, category="external_callers")
    assert [(item.key, item.count) for item in callers.clusters] == [("content/**", 3)]

    declared = link_clusters(ctx, category="declared_unresolvable", by="reason")
    assert [(item.key, item.count) for item in declared.clusters] == [(REASON_VARIABLE, 1)]
    assert link_clusters(ctx, category="calls_unresolved").places == 2
    assert link_clusters(ctx).categories["calls_without_endpoint"] == 2


def test_prefix_is_two_segments_and_file_and_host_are_keys(v2: SetupContext) -> None:
    prefixes = link_clusters(v2, by="prefix")
    assert [(item.key, item.count) for item in prefixes.clusters] == [
        ("api/apps", 7),
        ("api/info", 1),
        ("feed.json", 1),
    ]
    hosts = link_clusters(v2, by="host")
    assert [(item.key, item.count) for item in hosts.clusters] == [("", 8), ("ext.example.org", 1)]
    assert "(пусто) — 8" in format_link_clusters(hosts)


def test_page_with_continuation(v2: SetupContext) -> None:
    full = link_clusters(v2, by="file", limit=0)
    assert full.total == 5 and len(full.clusters) == 5
    assert sum(item.count for item in full.clusters) == full.places == 9

    page = link_clusters(v2, by="file", limit=2, offset=2)
    assert (page.total, page.offset) == (5, 2)
    assert [item.key for item in page.clusters] == [item.key for item in full.clusters[2:4]]
    assert "Показаны 3–4 из 5. Дальше: --offset 4." in format_link_clusters(page)

    beyond = link_clusters(v2, by="file", offset=9)
    assert beyond.clusters == [] and beyond.total == 5
    assert "за концом списка" in format_link_clusters(beyond)


def test_examples_are_at_most_three_with_the_rest_counted(v2: SetupContext) -> None:
    [cluster] = link_clusters(v2, by="module").clusters
    assert len(cluster.examples) == 3
    assert "… и ещё 6" in format_link_clusters(link_clusters(v2, by="module"))


@pytest.mark.parametrize(
    ("category", "by", "message"),
    [
        ("calls", None, "категория 'calls' неизвестна"),
        ("calls_unresolved", "host", "ключ 'host' у категории calls_unresolved не определён"),
        ("endpoints_without_caller", "reason", "допустимы: controller, module, prefix, file"),
    ],
)
def test_query_is_checked(category: str, by: str | None, message: str) -> None:
    with pytest.raises(InputError, match=message):
        check_query(category, by)


def test_query_is_checked_before_any_run() -> None:
    """Опечатка в ключе не стоит прогона: контекст на несуществующем корне не трогается."""
    ctx = SetupContext(Path("/nonexistent"), DocpipeConfig(), None, use_cache=False)
    with pytest.raises(InputError, match="неизвестна"):
        link_clusters(ctx, category="nope")
    with pytest.raises(InputError, match="отрицательными"):
        link_clusters(ctx, limit=-1)


@pytest.mark.parametrize(
    ("args", "hint"),
    [
        (["--category", "calls"], "--category"),
        (["--category", "calls_unresolved", "--by", "prefix"], "--by"),
        (["--format", "jsno"], "--format"),
        (["--limit", "-1"], "--limit"),
        (["--offset", "-1"], "--offset"),
    ],
)
def test_command_refuses_bad_arguments_with_code_2(args: list[str], hint: str) -> None:
    result = runner.invoke(app, ["setup", "link", "--root", str(SEAM), *args])
    assert result.exit_code == 2
    assert hint in result.output


def test_command_refuses_unreadable_config_with_code_2(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text("bogus_key: 1\n", encoding="utf-8")
    result = runner.invoke(
        app, ["setup", "link", "--root", str(tmp_path), "--config", str(config), "--no-cache"]
    )
    assert result.exit_code == 2
    assert "Ошибка конфигурации" in result.output


# --------------------------------------------------------------------------------------
# Поля `LinkReport` 1.4
# --------------------------------------------------------------------------------------


def test_call_ref_carries_module_confidence_member_and_via(v2: SetupContext) -> None:
    calls = {(item.file, item.line): item for item in v2.link.calls_without_endpoint}
    search = calls[(APPS, 47)]
    assert (search.module, search.confidence, search.member, search.via) == (
        MODULE,
        "medium",
        "search",
        "",
    )
    built = calls[(APPS, 28)]
    assert (built.member, built.via, built.confidence) == ("list", "apiUrl.buildUrl", "high")
    assert {item.module for item in v2.link.links} <= {MODULE}


def test_endpoint_ref_carries_module_file_and_line(seam: SetupContext) -> None:
    endpoints = {
        (item.member, item.http_method): item for item in seam.link.endpoints_without_caller
    }
    orders = endpoints[("GetOrders", "GET")]
    assert (orders.module, orders.file, orders.line) == (
        "Seam.Api",
        "backend/Seam.Api/Controllers/OrdersController.cs",
        9,
    )


def test_calls_unresolved_is_the_list_behind_the_count(seam: SetupContext) -> None:
    report = seam.link
    assert len(report.calls_unresolved) == report.counts["calls_unresolved"] == 3
    assert [(item.file, item.reason) for item in report.calls_unresolved] == [
        (EDITOR, REASON_MUTABLE_FIELD),
        (APPS, REASON_CONCAT_BASE),
        (LINKS, REASON_VARIABLE),
    ]


def test_json_is_the_model(seam: SetupContext) -> None:
    clusters = link_clusters(seam, category="calls_unresolved")
    assert LinkClusters.model_validate_json(link_clusters_json(clusters)) == clusters
