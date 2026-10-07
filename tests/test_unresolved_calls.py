"""Невосстановленные вызовы — в манифест; кандидаты в обёртки и построители (S18).

До S18 вызов фронта, адрес которого не восстановлен, жил только числом
`calls_unresolved` в сидкаре, а вызовы через обёртки (`HTTP.getVersioned`,
`restService.request`) не попадали ни в один счётчик: глагола `HttpClient`
у них нет. Здесь проверяется, что первые видны с файлом, строкой и причиной
своего вида, вторые — посчитаны находкой, и что находка остаётся находкой:
в вызовы она не превращается, пока обёртку не объявили (S19).

Половина проверок — на фикстуре `SeamWorkspace` (каждая форма — свой метод,
`tests/test_seam_fixture.py`), половина — на коротких исходниках, где видна
одна конструкция. Правила передаются явно — см. docstring
`test_seam_fixture.py` о первой ступени `resolve_input` в git worktree.
"""

import inspect
import json
import shutil
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, load_config
from docpipe.model import (
    SCHEMA_VERSION,
    DocNode,
    Manifest,
    ParserVersions,
    UnresolvedCall,
)
from docpipe.route import RouteKey
from docpipe.setup.candidates import (
    KINDS,
    HttpWrapperCandidate,
    HttpWrapperCandidates,
    UrlBuilderCandidates,
    format_http_wrappers,
    format_url_builders,
    http_wrapper_candidates,
    url_builder_candidates,
)
from docpipe.web.calls import (
    REASON_CONCAT_BASE,
    REASON_MUTABLE_FIELD,
    REASON_PARAMETER,
    REASON_VARIABLE,
    CallFacts,
    CallScan,
    address_positions,
    extract_call_facts,
    extract_calls,
    looks_like_address,
    scan_call_facts,
    scan_calls,
)
from docpipe.web.link import LinkReport, build_report, format_report
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
BUILDER_REASON: Final = "значение переменной — вызов `apiUrl.buildUrl(…)`"


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _facts(source: str, path: str = "src/a.service.ts") -> CallFacts:
    return scan_call_facts(source.encode(), path)


def _wrappers(*sources: str) -> HttpWrapperCandidates:
    """Кандидаты в обёртки по нескольким файлам, без Angular-workspace."""
    facts = [_facts(source, f"src/f{index}.ts") for index, source in enumerate(sources)]
    candidates = [call for item in facts for call in item.candidates]
    builders = [use for item in facts for use in item.builders]
    return http_wrapper_candidates(candidates, builders, CallScan(), limit=0)


def _builders(*sources: str) -> UrlBuilderCandidates:
    facts = [_facts(source, f"src/f{index}.ts") for index, source in enumerate(sources)]
    candidates = [call for item in facts for call in item.candidates]
    builders = [use for item in facts for use in item.builders]
    return url_builder_candidates(candidates, builders, limit=0)


def _copy_seam(tmp_path: Path) -> Path:
    """Копия фикстуры с абсолютным путём к правилам.

    Команда пишет кэш разбора под `--root`, поэтому фикстуру копируют; путь
    `../../../rules/rules.yaml` из её `docpipe.yaml` от копии не ведёт никуда.
    """
    root = tmp_path / "ws"
    shutil.copytree(SEAM, root)
    config = root / "docpipe.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "../../../rules/rules.yaml", str(RULES.resolve())
        ),
        encoding="utf-8",
    )
    return root


def _item(report: HttpWrapperCandidates, qualified: str) -> HttpWrapperCandidate:
    [found] = [item for item in report.items if f"{item.receiver}.{item.method}" == qualified]
    return found


@pytest.fixture(scope="module")
def settings() -> DocpipeConfig:
    return load_config(SEAM / "docpipe.yaml")


@pytest.fixture(scope="module")
def frontend(settings: DocpipeConfig, tmp_path_factory: pytest.TempPathFactory) -> WebScanResult:
    cache = tmp_path_factory.mktemp("cache-web")
    return run_web(SEAM, settings, load_ruleset(RULES, "web"), cache)


# --------------------------------------------------------------------------------------
# П. 1. Невосстановленные — в манифест
# --------------------------------------------------------------------------------------


def test_unresolved_calls_are_in_the_manifest_with_a_reason_of_their_own_kind(
    frontend: WebScanResult,
) -> None:
    """Построитель, тело обёртки, база, поле и гипермедиа — каждый со своей причиной.

    Тела обёрток в `http-extensions.ts` — функции модуля `HTTP`, а не члены
    класса: `member` у них пуст, и это состояние, а не потеря.
    """
    found = [
        (_name(item.file), item.member, item.http_method, item.reason)
        for item in frontend.manifest.unresolved_calls
    ]
    assert found == [
        ("editor.component.ts", "open", "GET", REASON_MUTABLE_FIELD),
        ("http-extensions.ts", "", "GET", REASON_PARAMETER),
        ("http-extensions.ts", "", "POST", REASON_PARAMETER),
        ("http-extensions.ts", "", "PUT", REASON_PARAMETER),
        ("http-extensions.ts", "", "DELETE", REASON_PARAMETER),
        ("apps.service.ts", "list", "GET", BUILDER_REASON),
        ("apps.service.ts", "get", "GET", BUILDER_REASON),
        ("apps.service.ts", "legacy", "GET", REASON_CONCAT_BASE),
        ("links.service.ts", "fetch", "GET", REASON_VARIABLE),
    ]


def test_unresolved_calls_are_sorted_by_file_and_line(frontend: WebScanResult) -> None:
    calls = frontend.manifest.unresolved_calls
    assert [(item.file, item.line) for item in calls] == sorted(
        (item.file, item.line) for item in calls
    )
    assert all(item.line > 0 and item.module == "seam-web" for item in calls)


def test_manifest_list_equals_the_scan_list(frontend: WebScanResult) -> None:
    """Инвариант «восстановлено + не восстановлено = всего» держится и в манифесте."""
    assert len(frontend.manifest.unresolved_calls) == len(frontend.calls.unresolved)
    assert frontend.meta.stats["calls_unresolved"] == len(frontend.manifest.unresolved_calls)


def test_builder_and_wrapper_body_are_told_apart(frontend: WebScanResult) -> None:
    """П. 5: «значение — вызов `X.m(…)`» (построитель) против «параметр функции» (тело).

    Обе причины даёт S16 поиском имени по областям; здесь они дошли до манифеста
    без склейки в одно «значение не восстановлено».
    """
    reasons = {
        _name(item.file): item.reason
        for item in frontend.manifest.unresolved_calls
        if item.expression == "url"
    }
    assert reasons == {"apps.service.ts": BUILDER_REASON, "http-extensions.ts": REASON_PARAMETER}


def test_files_outside_modules_give_no_unresolved_calls(tmp_path: Path) -> None:
    """Только файлы модулей: файл вне модуля не даёт ни вызова, ни узла."""
    root = tmp_path / "ws"
    shutil.copytree(SEAM, root)
    stray = root / "frontend" / "tools" / "stray.ts"
    stray.parent.mkdir(parents=True)
    stray.write_text(
        "export class Stray { constructor(private http: any) {}\n"
        "  go(u: string) { return this.http.get(u); } }\n",
        encoding="utf-8",
    )
    result = run_web(root, load_config(root / "docpipe.yaml"), load_ruleset(RULES, "web"))
    assert all("tools/" not in item.file for item in result.manifest.unresolved_calls)
    assert len(result.manifest.unresolved_calls) == 9


def test_old_manifest_without_the_field_reads_with_an_empty_list() -> None:
    payload = Manifest(ruleset_version="1", parser=ParserVersions(tree_sitter="0")).model_dump(
        mode="json"
    )
    payload["schema_version"] = "2.2"
    del payload["unresolved_calls"]
    restored = Manifest.model_validate_json(json.dumps(payload))
    assert restored.unresolved_calls == []
    assert restored.schema_version == "2.2"


def test_manifest_version_is_bumped_for_the_new_field() -> None:
    # S18: 2.2 → 2.3 (правило 7 плана); S19: 2.3 → 2.4 (`via`).
    assert SCHEMA_VERSION == "2.4"


def test_web_scan_writes_the_list_into_the_manifest_file(tmp_path: Path) -> None:
    out = tmp_path / "dt.web.json"
    result = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(SEAM),
            "--config",
            str(SEAM / "docpipe.yaml"),
            "--rules",
            str(RULES),
            "--out",
            str(out),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert len(payload["unresolved_calls"]) == 9
    assert {item["reason"] for item in payload["unresolved_calls"]} >= {
        BUILDER_REASON,
        REASON_PARAMETER,
    }


# --------------------------------------------------------------------------------------
# П. 2. `web link`: модуль без восстановленных вызовов тоже назван
# --------------------------------------------------------------------------------------


def _web_manifest(*, calls_in: str = "", unresolved_in: str = "") -> Manifest:
    nodes = []
    if calls_in:
        nodes.append(
            DocNode(
                id="type:a",
                kind="api-service",
                template="api-service",
                title="A",
                doc_path="docs/a.md",
                module=calls_in,
                domain=calls_in,
                signature_hash="h",
                web_calls=[
                    {
                        "file": "src/a.ts",
                        "line": 1,
                        "key": RouteKey(http_method="GET", route="api/a"),
                        "confidence": "high",
                    }
                ],
            )
        )
    unresolved = (
        [
            UnresolvedCall(
                file="src/b.ts",
                line=3,
                http_method="GET",
                reason=REASON_VARIABLE,
                expression="link.href",
                module=unresolved_in,
            )
        ]
        if unresolved_in
        else []
    )
    return Manifest(
        ruleset_version="1",
        parser=ParserVersions(tree_sitter="0"),
        nodes=nodes,
        unresolved_calls=unresolved,
    )


def _backend() -> Manifest:
    return Manifest(ruleset_version="1", parser=ParserVersions(tree_sitter="0"))


def test_module_with_only_unresolved_calls_is_named_unconfigured() -> None:
    """Сейчас так молчал `core` у abp: один вызов, и тот невосстановлен."""
    report = build_report(_backend(), _web_manifest(unresolved_in="core"))
    assert report.unconfigured_modules == ["core"]
    assert report.counts["calls_unresolved"] == 1
    assert report.counts["calls_total"] == 0


def test_configured_module_with_only_unresolved_calls_is_not_named() -> None:
    report = build_report(_backend(), _web_manifest(unresolved_in="core"), {"core"})
    assert report.unconfigured_modules == []


def test_modules_from_both_lists_are_merged() -> None:
    report = build_report(_backend(), _web_manifest(calls_in="web", unresolved_in="core"))
    assert report.unconfigured_modules == ["core", "web"]


def test_link_report_on_the_fixture_counts_unresolved(
    frontend: WebScanResult, settings: DocpipeConfig
) -> None:
    report = build_report(_backend(), frontend.manifest)
    assert report.schema_version == "1.2"
    assert report.counts["calls_unresolved"] == 9
    assert report.unconfigured_modules == ["seam-web"]
    assert "не восстановлено и в связь не идёт ещё 9" in format_report(report)


def test_link_report_version_is_1_2() -> None:
    # S17: 1.1 (`unresolved_endpoints`); S18: 1.2 (`counts.calls_unresolved`).
    assert LinkReport().schema_version == "1.2"


# --------------------------------------------------------------------------------------
# П. 3. Факты для обёрток — в извлечении, без настройки
# --------------------------------------------------------------------------------------


def test_extraction_takes_no_settings() -> None:
    """Контракт `calls.py`: факты не зависят от конфигурации — даже аргумента для неё нет."""
    assert list(inspect.signature(extract_call_facts).parameters) == ["root", "path"]
    assert list(inspect.signature(extract_calls).parameters) == ["root", "path"]


def test_facts_do_not_change_with_seam_settings(tmp_path: Path) -> None:
    """`url_rewrite` и `registry_calls` меняют ключи, а не находки для обёрток."""
    root = tmp_path / "ws"
    shutil.copytree(SEAM, root)
    ruleset = load_ruleset(RULES, "web")
    bare = run_web(root, load_config(root / "docpipe.yaml"), ruleset)
    configured = run_web(
        root,
        DocpipeConfig.model_validate(
            {
                "web": {
                    "roots": ["frontend"],
                    "url_rewrite": [{"module": "seam-web", "strip_prefix": "api"}],
                    "registry_calls": [
                        {"route": "apps", "discriminator": {"in": "query", "name": "q"}}
                    ],
                }
            }
        ),
        ruleset,
    )
    assert configured.candidate_calls == bare.candidate_calls
    assert configured.builder_uses == bare.builder_uses
    assert configured.manifest.unresolved_calls == bare.manifest.unresolved_calls


def test_extract_calls_still_returns_only_http_calls() -> None:
    source = """
      class A {
        a() { return this.http.get('api/a'); }
        b() { return this.rest.request({ url: '/api/b' }); }
      }
    """
    assert [call.url for call in scan_calls(source.encode(), "a.ts")] == ["api/a"]
    assert [(c.receiver, c.method) for c in _facts(source).candidates] == [("rest", "request")]


def test_wrapper_call_records_every_argument() -> None:
    """`HTTP` в нижнем регистре — `http`, но `getVersioned` — не глагол: это кандидат."""
    [call] = _facts(
        """
        class A {
          getApps() {
            const url = 'api/apps';
            return HTTP.getVersioned(this.http, url);
          }
        }
        """
    ).candidates
    assert (call.receiver, call.method, call.line) == ("HTTP", "getVersioned", 5)
    assert [(arg.kind, arg.text, arg.value) for arg in call.args] == [
        ("identifier", "this.http", None),
        ("identifier", "url", "api/apps"),
    ]
    assert address_positions(call.args) == ["1"]


def test_object_request_records_its_fields() -> None:
    [call] = _facts(
        "class A { create(body) { return this.rest.request({ method: 'POST', url, body }); } }\n"
        "const url = '/api/apps';"
    ).candidates
    [arg] = call.args
    assert arg.kind == "object"
    assert sorted(arg.fields) == ["body", "method", "url"]
    assert arg.fields["method"].value == "POST"
    # `{ url }` — то же имя, что `url`, и константа модуля найдена по областям.
    assert arg.fields["url"].value == "/api/apps"
    assert address_positions(call.args) == ["0.url"]


def test_object_field_with_an_address_value_counts_too() -> None:
    [call] = _facts("class A { f() { return this.api.send({ path: '/api/x' }); } }").candidates
    assert address_positions(call.args) == ["0.path"]


def test_template_argument_keeps_its_shape() -> None:
    [call] = _facts(
        "class A { put(app) {"
        " return HTTP.requestVersioned(this.http, 'PUT', `api/apps/${app}`); } }"
    ).candidates
    assert [(arg.kind, arg.value) for arg in call.args] == [
        ("identifier", None),
        ("literal", "PUT"),
        ("template", "api/apps/{}"),
    ]
    assert address_positions(call.args) == ["2"]


def test_builder_inside_a_wrapper_is_recorded_as_the_builder_use() -> None:
    """Основная форма squidex: построитель **внутри** обёртки.

    У `url` значения нет, но аргумент построителя — адрес: вызов-кандидат
    с позицией 1 и употребление построителя через обёртку.
    """
    facts = _facts(
        """
        class A {
          get(id) {
            const url = this.apiUrl.buildUrl(`api/teams/${id}/contributors`);
            return HTTP.getVersioned(this.http, url);
          }
        }
        """
    )
    wrapper = next(call for call in facts.candidates if call.method == "getVersioned")
    url = wrapper.args[1]
    assert url.callee == ("apiUrl", "buildUrl")
    assert [(arg.kind, arg.value) for arg in url.args] == [
        ("template", "api/teams/{}/contributors")
    ]
    assert address_positions(wrapper.args) == ["1"]
    [use] = facts.builders
    assert (use.receiver, use.method, use.through, use.line) == (
        "apiUrl",
        "buildUrl",
        "HTTP.getVersioned",
        5,
    )


def test_direct_http_call_with_a_builder_is_a_builder_use() -> None:
    """Прямой `get(url)` с адресом от построителя — употребление, даже у гипермедиа."""
    facts = _facts(
        """
        class A {
          a() { const url = this.apiUrl.buildUrl('/api/apps'); return this.http.get(url); }
          b(link) { const url = this.apiUrl.buildUrl(link.href); return this.http.get(url); }
        }
        """
    )
    assert [(use.receiver, use.method, use.through) for use in facts.builders] == [
        ("apiUrl", "buildUrl", ""),
        ("apiUrl", "buildUrl", ""),
    ]
    assert [address_positions(use.arg.args) for use in facts.builders] == [["0"], []]


def test_other_argument_with_a_call_is_not_a_builder_use() -> None:
    """`dto.toJSON()` рядом с адресом `requestVersioned` адреса не строит."""
    facts = _facts(
        "class A { f(dto) {"
        " return HTTP.requestVersioned(this.http, 'PUT', 'api/x', dto.toJSON()); } }"
    )
    assert facts.builders == []


def test_bare_slash_is_not_an_address() -> None:
    """`path.startsWith('/')`, `parts.join('/')` — не адреса: иначе кандидатом стал бы каждый."""
    facts = _facts(
        """
        class A {
          f(path, parts) {
            path.startsWith('/');
            parts.join('/');
            path.split('api/');
            return path.startsWith('/api');
          }
        }
        """
    )
    assert [(c.method, c.line) for c in facts.candidates] == [("startsWith", 7)]
    assert not looks_like_address("/")
    assert not looks_like_address("https://")
    assert looks_like_address("/x")
    assert looks_like_address("HTTPS://ext.example.org")


def test_comment_among_arguments_does_not_shift_the_position() -> None:
    """Комментарий — именованный узел грамматики и стоит среди аргументов."""
    [call] = _facts(
        "class A { f() { return HTTP.getVersioned(/* клиент */ this.http, 'api/x'); } }"
    ).candidates
    assert address_positions(call.args) == ["1"]


def test_call_without_a_named_receiver_is_not_a_candidate() -> None:
    """`inject(X).request(…)`: объявить обёртку на такой получатель нечем."""
    facts = _facts("const f = () => inject(Rest).request({ url: '/api/x' });")
    assert facts.candidates == []


def test_own_method_wrapper_is_named_this() -> None:
    [call] = _facts(
        "class A { load() { return this.fetchJson('/api/a'); } fetchJson(u) { return u; } }"
    ).candidates
    assert (call.receiver, call.method) == ("this", "fetchJson")


def test_a_candidate_is_a_finding_not_a_call() -> None:
    """Ловушка: обёртку по имени не распознать (`ui.state.ts` у squidex).

    `get<T>(path, default)` без всякого HTTP похож на вызов-обёртку ровно
    так же, как настоящая; в вызовы он не попадает, пока его не объявили.
    """
    source = "class A { f() { return this.uiState.get('/settings/theme', 'light'); } }"
    facts = _facts(source)
    assert facts.calls == []
    assert [(c.receiver, c.method) for c in facts.candidates] == [("uiState", "get")]


def test_value_built_by_a_call_is_recorded_without_an_address() -> None:
    """Гипермедиа через построитель на адрес не похожа, но факт сохраняется.

    Построитель ли это, решают кандидаты по всему прогону: в одном файле
    этого не видно.
    """
    [call] = _facts(
        """
        class A {
          del(link) {
            const url = this.apiUrl.buildUrl(link.href);
            return HTTP.requestVersioned(this.http, link.method, url);
          }
        }
        """
    ).candidates
    assert call.method == "requestVersioned"
    assert address_positions(call.args) == []
    assert call.args[2].callee == ("apiUrl", "buildUrl")


def test_direct_member_call_is_recorded_but_a_pipe_operator_is_not() -> None:
    """Вызов члена прямо в аргументе — факт; `map(…)` в `.pipe(…)` — нет: шум."""
    facts = _facts(
        """
        class A {
          f(link) {
            return HTTP.requestVersioned(this.http, 'GET', this.apiUrl.buildUrl(link.href));
          }
          g() { return this.items$.pipe(map((x) => x)); }
        }
        """
    )
    assert [(c.method, c.args[2].kind) for c in facts.candidates] == [("requestVersioned", "call")]


# --------------------------------------------------------------------------------------
# П. 4. Кандидаты `http-wrappers` и `url-builders`
# --------------------------------------------------------------------------------------


def test_fixture_wrappers_with_their_positions(frontend: WebScanResult) -> None:
    report = http_wrapper_candidates(
        frontend.candidate_calls, frontend.builder_uses, frontend.calls, limit=0
    )
    assert [
        (item.receiver, item.method, item.calls, item.positions, item.configured)
        for item in report.items
    ] == [
        ("HTTP", "getVersioned", 1, [("1", 1)], False),
        ("HTTP", "requestVersioned", 1, [("2", 1)], False),
        ("rest", "request", 1, [("0.url", 1)], False),
    ]
    # Построитель сам похож на вызов с аргументом-адресом — но он построитель.
    assert report.builders == ["apiUrl.buildUrl"]
    assert report.http_calls == 13
    assert report.wrapper_calls == 3
    assert report.items[0].examples == ["frontend/src/app/services/apps-versioned.service.ts:17"]


def test_fixture_url_builders(frontend: WebScanResult) -> None:
    report = url_builder_candidates(frontend.candidate_calls, frontend.builder_uses, limit=0)
    assert [
        (item.receiver, item.method, item.uses, item.http_calls, item.through, item.positions)
        for item in report.items
    ] == [("apiUrl", "buildUrl", 2, 2, [], [("0", 2)])]
    assert report.items[0].files == 1
    assert report.items[0].configured is False


def test_wrapper_position_is_its_argument_number_not_the_first() -> None:
    """Ловушка: `HTTP` проходит фильтр получателей, первым аргументом идёт `this.http`."""
    report = _wrappers(
        "class A { a() { return HTTP.getVersioned(this.http, 'api/a'); } }",
        "class B { b() { return HTTP.getVersioned(this.http, '/api/b'); } }",
    )
    item = _item(report, "HTTP.getVersioned")
    assert (item.calls, item.files, item.positions) == (2, 2, [("1", 2)])


def test_hypermedia_through_a_known_builder_counts_for_the_wrapper() -> None:
    """`requestVersioned` с адресом `buildUrl(link.href)`: на squidex 47 из 48.

    Построитель известен по другому файлу (там его аргумент — адрес),
    и построенный им аргумент — адрес и здесь.
    """
    builder = (
        "class S { a() { const url = this.apiUrl.buildUrl('/api/apps'); "
        "return this.http.get(url); } }"
    )
    hypermedia = (
        "class T { del(link) { const url = this.apiUrl.buildUrl(link.href); "
        "return HTTP.requestVersioned(this.http, link.method, url); } }"
    )
    wrappers = _wrappers(builder, hypermedia)
    assert _item(wrappers, "HTTP.requestVersioned").positions == [("2", 1)]
    [item] = _builders(builder, hypermedia).items
    assert (item.uses, item.http_calls, item.through, item.positions) == (
        2,
        1,
        [("HTTP.requestVersioned", 1)],
        [("0", 1)],
    )


def test_call_bound_to_an_unknown_call_is_not_a_candidate() -> None:
    """Без построителя в прогоне `const x = this.f.g(…)` — просто значение."""
    report = _wrappers(
        "class A { f() { const v = this.mapper.map(1); return this.form.patch(this.x, v); } }"
    )
    assert report.items == []


def test_builder_group_is_not_a_wrapper_candidate() -> None:
    report = _wrappers(
        "class S { a() { const url = this.apiUrl.buildUrl('/api/apps'); "
        "return this.http.get(url); } }",
        # Тот же построитель там, где результат не идёт в вызов: всё равно построитель.
        "class P { portal = this.apiUrl.buildUrl('/portal/'); }",
    )
    assert report.items == []
    assert report.builders == ["apiUrl.buildUrl"]


def test_order_is_by_calls_then_name() -> None:
    report = _wrappers(
        "class A { f() { this.b.send('/x'); this.a.send('/y'); this.a.send('/z'); } }"
    )
    assert [(item.receiver, item.calls) for item in report.items] == [("a", 2), ("b", 1)]


def test_page_keeps_total_and_offset(frontend: WebScanResult) -> None:
    report = http_wrapper_candidates(
        frontend.candidate_calls, frontend.builder_uses, frontend.calls, limit=1, offset=1
    )
    assert (report.total, report.offset, len(report.items)) == (3, 1, 1)
    assert report.items[0].method == "requestVersioned"
    assert "Показаны 2–2 из 3. Дальше: --offset 2." in format_http_wrappers(report)


def test_text_names_the_builders_and_the_limits(frontend: WebScanResult) -> None:
    text = format_http_wrappers(
        http_wrapper_candidates(
            frontend.candidate_calls, frontend.builder_uses, frontend.calls, limit=0
        )
    )
    assert "Вызовов HttpClient прогон видит 13" in text
    assert "apiUrl.buildUrl" in text
    assert "адрес в аргументе: 0.url ×1" in text
    assert "Ограничения отбора:" in text
    builders = format_url_builders(
        url_builder_candidates(frontend.candidate_calls, frontend.builder_uses, limit=0)
    )
    assert "адресов построено 2: у вызовов HttpClient 2, через обёртки 0" in builders
    assert "путь в аргументе: 0 ×2" in builders


@pytest.mark.parametrize("kind", ["http-wrappers", "url-builders"])
def test_command_prints_json_and_two_runs_give_the_same_bytes(kind: str, tmp_path: Path) -> None:
    root = _copy_seam(tmp_path)
    assert kind in KINDS
    args = [
        "setup",
        "candidates",
        kind,
        "--root",
        str(root),
        "--config",
        str(root / "docpipe.yaml"),
        "--format",
        "json",
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert first.output == second.output
    payload = json.loads(first.output)
    assert payload["schema_version"] == "1.0"
    assert payload["total"] == (3 if kind == "http-wrappers" else 1)
