"""`web.http_wrappers` и `web.url_builders`: записи человека превращают находки в вызовы (S19).

До S19 вызов через обёртку (`HTTP.getVersioned(this.http, url)`,
`this.rest.request({ method, url })`) был только находкой S18, а адрес
от построителя (`const url = this.apiUrl.buildUrl('/api/apps')`) —
невосстановленным вызовом. Здесь проверяется, что объявленная запись
делает из них вызовы с `via` и они связываются с эндпоинтами, что тела
обёрток уходят из `unresolved_calls` в счётчик, что извлечение от записей
не зависит (контракт `web/calls.py`) и что без записей не меняется ничего.

Половина проверок — на копии фикстуры `SeamWorkspace` в `tmp_path`
с правилами из спецификации S19 (плюс запись `requestVersioned`: у неё
адрес — третий аргумент, а метод — второй), половина — на коротких
исходниках. Правила классификации передаются явно — см. docstring
`tests/test_seam_fixture.py` о первой ступени `resolve_input` в git worktree.
"""

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, HttpWrapper, UrlBuilder, load_config
from docpipe.emit import ScanResult
from docpipe.emit import run as run_dotnet
from docpipe.hashing import stable_json_dumps
from docpipe.model import SCHEMA_VERSION, Manifest, ParserVersions, UnresolvedCall, WebCall
from docpipe.route import RewriteRule
from docpipe.setup.candidates import http_wrapper_candidates, url_builder_candidates
from docpipe.setup.context import SetupContext
from docpipe.setup.explain import explain_path
from docpipe.web.calls import (
    REASON_BUILDER_NO_PATH,
    REASON_PARAMETER,
    REASON_TEMPLATE_BASE,
    REASON_VARIABLE,
    REASON_WRAPPER_METHOD,
    REASON_WRAPPER_NO_ADDRESS,
    REASON_WRAPPER_NO_VERB,
    CallScan,
    ParameterRef,
    RegistryCall,
    WrapperConflict,
    build_calls,
    leading_verb,
    scan_call_facts,
)
from docpipe.web.link import LinkReport, build_report
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
BUILDER_REASON: Final = "значение переменной — вызов `apiUrl.buildUrl(…)`"

# Правила из спецификации S19 и запись `requestVersioned`, без которой её
# вызов в фикстуре так и остался бы находкой: у неё адрес — `2`, метод — `1`.
SEAM_RULES: Final = """
  http_wrappers:
    - receiver: HTTP
      method_regex: "^(get|post|put|delete)Versioned$"
      url: {arg: 1}
      http_method: {from_name: true}
      reason: "обёртка над HttpClient с версией (framework/http-extensions.ts)"
    - receiver: HTTP
      method: requestVersioned
      url: {arg: 2}
      http_method: {arg: 1}
    - receiver: rest
      method: request
      url: {arg: 0, field: url}
      http_method: {arg: 0, field: method}
  url_builders:
    - receiver: apiUrl
      method: buildUrl
      path: {arg: 0}
"""


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _copy_seam(root: Path, extra: str = SEAM_RULES) -> Path:
    """Копия фикстуры с правилами шва в секции `web` и абсолютным путём к правилам.

    Секция `web` — последняя в `docpipe.yaml` фикстуры, поэтому записи
    дописываются в конец файла. Копия, а не сама фикстура: команды пишут
    кэш разбора под `--root`.
    """
    shutil.copytree(SEAM, root)
    config = root / "docpipe.yaml"
    text = config.read_text(encoding="utf-8").replace(
        "../../../rules/rules.yaml", str(RULES.resolve())
    )
    config.write_text(text.rstrip("\n") + "\n" + extra.lstrip("\n"), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def seam(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _copy_seam(tmp_path_factory.mktemp("seam") / "ws")


@pytest.fixture(scope="module")
def settings(seam: Path) -> DocpipeConfig:
    return load_config(seam / "docpipe.yaml")


@pytest.fixture(scope="module")
def frontend(seam: Path, settings: DocpipeConfig) -> WebScanResult:
    return run_web(seam, settings, load_ruleset(RULES, "web"))


@pytest.fixture(scope="module")
def bare() -> WebScanResult:
    """Прогон самой фикстуры, без записей: точка отсчёта S18."""
    return run_web(SEAM, load_config(SEAM / "docpipe.yaml"), load_ruleset(RULES, "web"))


@pytest.fixture(scope="module")
def backend(seam: Path, settings: DocpipeConfig) -> ScanResult:
    return run_dotnet(seam, settings, load_ruleset(RULES, "dotnet"))


@pytest.fixture(scope="module")
def link(backend: ScanResult, frontend: WebScanResult) -> LinkReport:
    return build_report(backend.manifest, frontend.manifest)


# --------------------------------------------------------------------------------------
# Фикстура с правилами: критерии приёмки
# --------------------------------------------------------------------------------------


def test_calls_through_wrappers_and_the_builder_are_resolved_with_via(
    frontend: WebScanResult,
) -> None:
    """Пять мест продукта, которых до S19 не было среди вызовов, — вызовы с `via`."""
    found = sorted(
        (_name(call.file), call.member, call.key.http_method, call.key.route, call.via)
        for call in frontend.calls.calls
        if call.via
    )
    assert found == [
        ("apps-proxy.service.ts", "create", "POST", "api/apps", "rest.request"),
        ("apps-versioned.service.ts", "getApps", "GET", "api/apps", "HTTP.getVersioned"),
        ("apps-versioned.service.ts", "putApp", "PUT", "api/apps/{}", "HTTP.requestVersioned"),
        ("apps.service.ts", "get", "GET", "api/apps/{}", "apiUrl.buildUrl"),
        ("apps.service.ts", "list", "GET", "api/apps", "apiUrl.buildUrl"),
    ]
    # Прямые вызовы с адресом в аргументе — без `via`, как и были.
    assert sorted(
        (_name(call.file), call.member) for call in frontend.calls.calls if not call.via
    ) == [
        ("apps.service.ts", "archived"),
        ("apps.service.ts", "search"),
        ("feed.service.ts", "latest"),
        ("info.service.ts", "getInfo"),
    ]


def test_they_link_to_apps_controller(link: LinkReport) -> None:
    """После S17 у `AppsController` префикс `api` базы, и все пять ложатся точно."""
    linked = sorted(
        (_name(item.file), item.http_method, item.route, item.match, item.endpoints)
        for item in link.links
        if "AppsController" in " ".join(item.endpoints)
    )
    assert [(file, method, route, match) for file, method, route, match, _ in linked] == [
        ("apps-proxy.service.ts", "POST", "api/apps", "exact"),
        ("apps-versioned.service.ts", "GET", "api/apps", "exact"),
        ("apps-versioned.service.ts", "PUT", "api/apps/{}", "exact"),
        ("apps.service.ts", "GET", "api/apps", "exact"),
        ("apps.service.ts", "GET", "api/apps/{}", "exact"),
    ]
    assert all(len(endpoints) == 1 for *_, endpoints in linked)
    # S17: 1 связь (`GET api/info`) → S19: 6.
    assert link.counts["linked"] == 6
    assert link.counts["calls_unresolved"] == 3


def test_wrapper_bodies_are_not_in_unresolved_calls(frontend: WebScanResult) -> None:
    """Тела `getVersioned`/`postVersioned`/`putVersioned`/`deleteVersioned` — счётчик, не список."""
    assert all("http-extensions.ts" not in item.file for item in frontend.manifest.unresolved_calls)
    assert sorted(
        (_name(item.file), item.http_method, item.address.parameter if item.address else None)
        for item in frontend.calls.inside_wrappers
    ) == [
        ("http-extensions.ts", method, ParameterRef(name, 1))
        for method, name in sorted(
            [
                ("DELETE", "deleteVersioned"),
                ("GET", "getVersioned"),
                ("POST", "postVersioned"),
                ("PUT", "putVersioned"),
            ]
        )
    ]
    assert frontend.meta.stats["calls_inside_wrappers"] == 4


def test_what_stays_unresolved_keeps_its_reason(frontend: WebScanResult) -> None:
    assert [
        (_name(item.file), item.member, item.reason, item.via)
        for item in frontend.manifest.unresolved_calls
    ] == [
        ("editor.component.ts", "open", "поле присваивается вне инициализатора", ""),
        ("apps.service.ts", "legacy", "база в начале конкатенации не восстановлена", ""),
        ("links.service.ts", "fetch", REASON_VARIABLE, ""),
    ]


def test_counts_add_up(frontend: WebScanResult) -> None:
    """Восстановлено + не восстановлено + тела обёрток = прямые вызовы + вызовы через обёртки."""
    stats = frontend.meta.stats
    assert (stats["calls_resolved"], stats["calls_unresolved"]) == (9, 3)
    direct, through = 13, 3
    assert stats["calls_resolved"] + stats["calls_unresolved"] + 4 == direct + through
    assert len(frontend.manifest.unresolved_calls) == stats["calls_unresolved"]


def test_without_rules_the_s18_numbers_hold(bare: WebScanResult) -> None:
    """Без записей — байт в байт S18: 4 восстановлено, 9 нет, тел обёрток 0."""
    stats = bare.meta.stats
    assert (stats["calls_resolved"], stats["calls_unresolved"]) == (4, 9)
    assert stats["calls_inside_wrappers"] == 0
    assert all(not call.via for call in bare.calls.calls)
    assert len(bare.candidate_calls) == 5
    reasons = {item.reason for item in bare.manifest.unresolved_calls}
    assert {BUILDER_REASON, REASON_PARAMETER} <= reasons


def test_facts_do_not_depend_on_the_rules(bare: WebScanResult, frontend: WebScanResult) -> None:
    """Контракт `calls.py`: записи меняют вызовы, а не находки."""
    assert frontend.candidate_calls == bare.candidate_calls
    assert frontend.builder_uses == bare.builder_uses


def test_web_scan_writes_via_and_names_the_bodies(seam: Path, tmp_path: Path) -> None:
    out = tmp_path / "dt.web.json"
    result = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(seam),
            "--config",
            str(seam / "docpipe.yaml"),
            "--out",
            str(out),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Вызовов: восстановлено 9, не восстановлено 3;" in result.output
    assert "Вызовов в телах объявленных обёрток: 4" in result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION == "2.4"
    vias = sorted(
        call["via"] for node in payload["nodes"] for call in node["web_calls"] if call["via"]
    )
    assert vias == [
        "HTTP.getVersioned",
        "HTTP.requestVersioned",
        "apiUrl.buildUrl",
        "apiUrl.buildUrl",
        "rest.request",
    ]


def test_two_runs_with_rules_give_the_same_bytes(seam: Path, settings: DocpipeConfig) -> None:
    ruleset = load_ruleset(RULES, "web")
    first, second = (run_web(seam, settings, ruleset).manifest for _ in range(2))
    assert stable_json_dumps(first.model_dump(mode="json")) == stable_json_dumps(
        second.model_dump(mode="json")
    )


def test_old_manifest_without_via_reads_with_empty_via() -> None:
    call = WebCall.model_validate(
        {
            "file": "a.ts",
            "line": 1,
            "key": {"http_method": "GET", "route": "a"},
            "confidence": "high",
        }
    )
    unresolved = UnresolvedCall(file="a.ts", line=1, http_method="GET", reason="r", module="m")
    assert (call.via, unresolved.via) == ("", "")
    payload = Manifest(ruleset_version="1", parser=ParserVersions(tree_sitter="0")).model_dump(
        mode="json"
    )
    payload["schema_version"] = "2.3"
    assert Manifest.model_validate_json(json.dumps(payload)).schema_version == "2.3"


# --------------------------------------------------------------------------------------
# Загрузка: строгая, с адресом ошибки (S02, S19 п. 5)
# --------------------------------------------------------------------------------------


def _wrapper(**fields: Any) -> dict[str, Any]:
    return {"receiver": "HTTP", "url": {"arg": 1}, "http_method": {"from_name": True}} | fields


def test_spec_yaml_loads_and_reason_is_optional(settings: DocpipeConfig) -> None:
    wrappers = settings.web.http_wrappers
    assert [item.label for item in wrappers] == [
        "HTTP./^(get|post|put|delete)Versioned$/",
        "HTTP.requestVersioned",
        "rest.request",
    ]
    assert [item.reason for item in wrappers][1:] == ["", ""]
    assert wrappers[2].url.label == "0.url"
    assert [item.label for item in settings.web.url_builders] == ["apiUrl.buildUrl"]


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (_wrapper(method="getVersioned", method_regex="get.*"), "ровно одно из `method`"),
        (_wrapper(), "ровно одно из `method`"),
        (_wrapper(method_regex="(get"), "не компилируется"),
        (_wrapper(method="x", http_method={}), "ровно один из `arg`, `fixed`, `from_name`"),
        (
            _wrapper(method="x", http_method={"arg": 1, "from_name": True}),
            "дано: arg, from_name",
        ),
        (_wrapper(method="x", http_method={"fixed": "GET", "field": "m"}), "без `arg`"),
        (_wrapper(method="x", http_method={"fixed": "FETCH"}), "не метод HTTP"),
        (_wrapper(method="x", url={"arg": -1}), "greater than or equal to 0"),
        (_wrapper(method="x", url={"arg": 1, "feild": "url"}), "Extra inputs"),
        (_wrapper(method="x", resaon="опечатка"), "Extra inputs"),
        (_wrapper(method="x", receiver=" "), "`receiver` пуст"),
    ],
)
def test_wrapper_entry_is_checked_at_load(entry: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        DocpipeConfig.model_validate({"web": {"http_wrappers": [entry]}})


def test_builder_entry_is_checked_at_load() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        DocpipeConfig.model_validate(
            {"web": {"url_builders": [{"receiver": "a", "method": "b", "url": {"arg": 0}}]}}
        )
    with pytest.raises(ValidationError, match="path"):
        DocpipeConfig.model_validate({"web": {"url_builders": [{"receiver": "a", "method": "b"}]}})


def test_the_same_call_named_twice_is_refused() -> None:
    """Как `url_rewrite` (S02): вторая запись молча не действовала бы."""
    twice = [_wrapper(method="getVersioned"), _wrapper(receiver="this.http", method="getVersioned")]
    with pytest.raises(ValidationError, match="назван больше одного раза: http.getVersioned"):
        DocpipeConfig.model_validate({"web": {"http_wrappers": twice}})
    builders = [
        {"receiver": "apiUrl", "method": "buildUrl", "path": {"arg": 0}},
        {"receiver": "this.ApiUrl", "method": "buildUrl", "path": {"arg": 1}},
    ]
    with pytest.raises(ValidationError, match="apiurl.buildUrl"):
        DocpipeConfig.model_validate({"web": {"url_builders": builders}})


@pytest.mark.parametrize("key", ["http_wrappers", "url_builders"])
def test_commented_out_list_is_refused_like_every_list(tmp_path: Path, key: str) -> None:
    """S02: закомментированный список — отказ с подсказкой, а не умолчание."""
    config = tmp_path / "docpipe.yaml"
    config.write_text(f"web:\n  {key}:\n    # - receiver: HTTP\n", encoding="utf-8")
    with pytest.raises(ValueError, match=f"`web.{key}:` без элементов") as failure:
        load_config(config)
    assert f"`{key}: []` в секции `web`" in str(failure.value)


# --------------------------------------------------------------------------------------
# Применение на коротких исходниках
# --------------------------------------------------------------------------------------


def _scan(
    source: str,
    wrappers: Sequence[dict[str, Any]] = (),
    builders: Sequence[dict[str, Any]] = (),
    **kwargs: Any,
) -> CallScan:
    facts = scan_call_facts(source.encode(), "src/a.service.ts")
    return build_calls(
        facts.calls,
        candidates=facts.candidates,
        wrappers=[HttpWrapper.model_validate(item) for item in wrappers],
        builders=[UrlBuilder.model_validate(item) for item in builders],
        **kwargs,
    )


VERSIONED: Final = _wrapper(method_regex="(get|post|put|delete)Versioned")
REQUEST: Final = {
    "receiver": "rest",
    "method": "request",
    "url": {"arg": 0, "field": "url"},
    "http_method": {"arg": 0, "field": "method"},
}
BUILD_URL: Final = {"receiver": "apiUrl", "method": "buildUrl", "path": {"arg": 0}}


def _keys(scan: CallScan) -> list[tuple[str, str, str]]:
    return [(call.key.http_method, call.key.route, call.via) for call in scan.calls]


def _unresolved(scan: CallScan) -> list[tuple[str, str, str, str]]:
    return [(item.http_method, item.reason, item.expression, item.via) for item in scan.unresolved]


@pytest.mark.parametrize(
    ("name", "verb"),
    [
        ("getVersioned", "GET"),
        ("GetItems", "GET"),
        ("get_items", "GET"),
        ("deleteVersioned", "DELETE"),
        ("get", "GET"),
        ("getter", ""),
        ("optionsMenu", "OPTIONS"),
        ("request", ""),
        ("upload", ""),
    ],
)
def test_leading_verb_is_a_word_not_a_prefix(name: str, verb: str) -> None:
    assert leading_verb(name) == verb


def test_method_from_the_name() -> None:
    scan = _scan(
        "class A { f() { return HTTP.postVersioned(this.http, 'api/apps', {}); } }", [VERSIONED]
    )
    assert _keys(scan) == [("POST", "api/apps", "HTTP.postVersioned")]


def test_name_without_a_verb_is_unresolved_with_a_reason() -> None:
    scan = _scan(
        "class A { f(file) { return HTTP.upload(this.http, 'api/assets', file); } }",
        [_wrapper(method="upload")],
    )
    assert scan.calls == []
    assert _unresolved(scan) == [("", REASON_WRAPPER_NO_VERB, "upload", "HTTP.upload")]


def test_method_from_an_argument_and_a_fixed_one() -> None:
    source = """
      class A {
        a() { return HTTP.requestVersioned(this.http, 'put', 'api/a'); }
        b() { return this.api.send('/api/b'); }
      }
    """
    scan = _scan(
        source,
        [
            _wrapper(method="requestVersioned", url={"arg": 2}, http_method={"arg": 1}),
            {
                "receiver": "api",
                "method": "send",
                "url": {"arg": 0},
                "http_method": {"fixed": "post"},
            },
        ],
    )
    assert _keys(scan) == [("PUT", "api/a", "HTTP.requestVersioned"), ("POST", "api/b", "api.send")]


def test_method_from_data_is_unresolved_but_the_address_reason_comes_first() -> None:
    """Гипермедиа: у `link.method` и `link.href` не восстановлено оба — называют адрес."""
    source = """
      class A {
        a(link) { return HTTP.requestVersioned(this.http, link.method, 'api/a'); }
        b(link) { return HTTP.requestVersioned(this.http, link.method, link.href); }
      }
    """
    scan = _scan(
        source, [_wrapper(method="requestVersioned", url={"arg": 2}, http_method={"arg": 1})]
    )
    assert _unresolved(scan) == [
        ("", REASON_WRAPPER_METHOD, "link.method", "HTTP.requestVersioned"),
        ("", REASON_VARIABLE, "link.href", "HTTP.requestVersioned"),
    ]


def test_object_request_with_a_shorthand_const() -> None:
    source = """
      const url = '/api/apps';
      class A { create(body) { return this.rest.request({ method: 'POST', url, body }); } }
    """
    assert _keys(_scan(source, [REQUEST])) == [("POST", "api/apps", "rest.request")]


def test_missing_address_argument_is_unresolved_not_dropped() -> None:
    scan = _scan(
        "class A { f() { return this.rest.request({ method: 'GET', path: '/api/x' }); } }",
        [REQUEST],
    )
    assert _unresolved(scan) == [("GET", REASON_WRAPPER_NO_ADDRESS, "", "rest.request")]


def test_receiver_is_the_last_segment_without_case_and_the_method_is_exact() -> None:
    source = """
      class A {
        a() { return this.rest.request({ method: 'GET', url: '/api/a' }); }
        b() { return this.rest.Request({ method: 'GET', url: '/api/b' }); }
      }
    """
    scan = _scan(source, [REQUEST | {"receiver": "this.Rest"}])
    assert _keys(scan) == [("GET", "api/a", "rest.request")]


def test_builder_inside_a_wrapper_names_both() -> None:
    """Основная форма squidex: построитель внутри обёртки."""
    source = """
      class A {
        get(id) {
          const url = this.apiUrl.buildUrl(`api/teams/${id}`);
          return HTTP.getVersioned(this.http, url);
        }
      }
    """
    scan = _scan(source, [VERSIONED], [BUILD_URL])
    assert _keys(scan) == [("GET", "api/teams/{}", "HTTP.getVersioned, apiUrl.buildUrl")]


def test_builder_for_a_direct_call_and_a_call_in_the_argument() -> None:
    source = """
      class A {
        a() { const url = this.apiUrl.buildUrl('/api/a'); return this.http.get(url); }
        b() { return this.http.post(this.apiUrl.buildUrl('/api/b'), {}); }
      }
    """
    assert _keys(_scan(source, builders=[BUILD_URL])) == [
        ("GET", "api/a", "apiUrl.buildUrl"),
        ("POST", "api/b", "apiUrl.buildUrl"),
    ]


def test_builder_with_a_path_from_data_names_the_path() -> None:
    """После объявления построителя вопрос уже не в нём: причина и выражение — пути."""
    source = (
        "class A { f(link) { const url = this.apiUrl.buildUrl(link.href);"
        " return this.http.get(url); } }"
    )
    assert _unresolved(_scan(source)) == [("GET", BUILDER_REASON, "url", "")]
    assert _unresolved(_scan(source, builders=[BUILD_URL])) == [
        ("GET", REASON_VARIABLE, "link.href", "apiUrl.buildUrl")
    ]
    no_path = _scan(
        "class A { f() { const url = this.apiUrl.buildUrl(); return this.http.get(url); } }",
        builders=[BUILD_URL],
    )
    assert _unresolved(no_path) == [("GET", REASON_BUILDER_NO_PATH, "url", "apiUrl.buildUrl")]


def test_wrapper_calls_go_the_usual_way_rewrite_and_registry() -> None:
    """Дальше — обычный путь: `url_rewrite` модуля и различитель реестра."""
    source = "class A { f() { return HTTP.getVersioned(this.http, '/pm/api/items?list=users'); } }"
    scan = _scan(
        source,
        [VERSIONED],
        rewrite=RewriteRule("m", strip_prefix="/pm"),
        registry=[RegistryCall(route="api/items", discriminator_in="query", name="list")],
    )
    [call] = scan.calls
    assert (call.key.route, call.key.discriminator, call.via) == (
        "api/items",
        "users",
        "HTTP.getVersioned",
    )


def test_converted_calls_are_merged_in_file_order() -> None:
    source = """
      class A {
        a() { return this.http.get('api/a'); }
        b() { return HTTP.getVersioned(this.http, 'api/b'); }
        c() { return this.http.get('api/c'); }
      }
    """
    assert [route for _, route, _ in _keys(_scan(source, [VERSIONED]))] == [
        "api/a",
        "api/b",
        "api/c",
    ]


def test_call_passing_the_client_is_seen_even_without_an_address() -> None:
    """Ловушка S18: адрес-параметр, `link.href` напрямую, шаблон с построителем внутри.

    Такие вызовы на адрес не похожи, но им передают сам `HttpClient` —
    факт пишется, и объявленная обёртка видит их невосстановленными,
    а не теряет молча.
    """
    source = """
      class A {
        load(url) { return HTTP.getVersioned(this.http, url); }
        follow(link) { return HTTP.getVersioned(this.http, link.href); }
        query(link, q) {
          return HTTP.getVersioned(this.http, `${this.apiUrl.buildUrl(link.href)}${q}`);
        }
      }
    """
    assert len(scan_call_facts(source.encode(), "a.ts").candidates) == 3
    assert [
        (reason, expression) for _, reason, expression, _ in _unresolved(_scan(source, [VERSIONED]))
    ] == [
        (REASON_PARAMETER, "url"),
        (REASON_VARIABLE, "link.href"),
        (REASON_TEMPLATE_BASE, "`${this.apiUrl.buildUrl(link.href)}${q}`"),
    ]


def test_class_token_is_not_the_client() -> None:
    """`injector.get(HttpClient)` (abp) передаёт класс, а не клиента: не факт."""
    source = "class A { f(injector) { return injector.get(HttpClient); } }"
    assert scan_call_facts(source.encode(), "a.ts").candidates == []
    with_instance = "class A { f() { return Api.load(this.httpClient, x); } }"
    assert len(scan_call_facts(with_instance.encode(), "a.ts").candidates) == 1


def test_two_records_on_one_call_refuse_the_run() -> None:
    """Пересечение регулярок видно только на вызове: отказ, а не «первая в файле»."""
    source = "class A { f() { return HTTP.getVersioned(this.http, 'api/a'); } }"
    with pytest.raises(WrapperConflict, match="совпал с несколькими записями"):
        _scan(source, [VERSIONED, _wrapper(method_regex="get.*")])


def test_web_scan_refuses_two_records_on_one_call_with_code_2(tmp_path: Path) -> None:
    extra = """
  http_wrappers:
    - receiver: HTTP
      method_regex: "get.*"
      url: {arg: 1}
      http_method: {from_name: true}
    - receiver: HTTP
      method_regex: ".*Versioned"
      url: {arg: 1}
      http_method: {from_name: true}
"""
    root = _copy_seam(tmp_path / "ws", extra)
    result = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(root),
            "--config",
            str(root / "docpipe.yaml"),
            "--out",
            str(tmp_path / "out.json"),
            "--no-cache",
        ],
    )
    assert result.exit_code == 2
    assert "Ошибка конфигурации: web.http_wrappers: вызов HTTP.getVersioned" in result.output


# --------------------------------------------------------------------------------------
# Тела обёрток (п. 3)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        (
            "export module HTTP { export function getVersioned(http, url) {"
            " return h(http.get(url)); } }",
            VERSIONED,
        ),
        (
            "const getJson = (http, url) => http.get(url);",
            _wrapper(method="getJson"),
        ),
        ("class R { request(config) { return this.http.get(config.url); } }", REQUEST),
        ("class R { request({ method, url }) { return this.http.get(url); } }", REQUEST),
    ],
)
def test_body_of_a_declared_wrapper_is_counted_not_unresolved(
    source: str, rule: dict[str, Any]
) -> None:
    scan = _scan(source, [rule])
    assert scan.unresolved == []
    assert len(scan.inside_wrappers) == 1


@pytest.mark.parametrize(
    "source",
    [
        # Адрес — не тот параметр, что запись называет адресом (`arg: 1`).
        "export module HTTP { export function getVersioned(url, http) { return http.get(url); } }",
        # Имя функции не совпало ни с одной записью.
        "class S { load(http, url) { return http.get(url); } }",
        # Адрес — параметр безымянной стрелки, а не функции-обёртки.
        "export function getVersioned(http, urls) { return urls.map(url => http.get(url)); }",
    ],
)
def test_other_parameter_calls_stay_unresolved(source: str) -> None:
    scan = _scan(source, [VERSIONED])
    assert [item.reason for item in scan.unresolved] == [REASON_PARAMETER]
    assert scan.inside_wrappers == []


# --------------------------------------------------------------------------------------
# Кандидаты: `configured` (S18 п. 4)
# --------------------------------------------------------------------------------------


def test_candidates_mark_declared_wrappers_and_builders(
    frontend: WebScanResult, settings: DocpipeConfig
) -> None:
    wrappers = http_wrapper_candidates(
        frontend.candidate_calls,
        frontend.builder_uses,
        frontend.calls,
        wrappers=settings.web.http_wrappers,
        limit=0,
    )
    assert [(item.receiver, item.method, item.configured) for item in wrappers.items] == [
        ("HTTP", "getVersioned", True),
        ("HTTP", "requestVersioned", True),
        ("rest", "request", True),
    ]
    # Вызовы `HttpClient` — те же 13, что без записей: вызовы через обёртки
    # и тела обёрток не меняют число.
    assert wrappers.http_calls == 13
    builders = url_builder_candidates(
        frontend.candidate_calls,
        frontend.builder_uses,
        builders=settings.web.url_builders,
        limit=0,
    )
    assert [(item.receiver, item.configured) for item in builders.items] == [("apiUrl", True)]


def test_regex_marks_only_the_groups_it_matches(frontend: WebScanResult) -> None:
    only_regex = [HttpWrapper.model_validate(VERSIONED)]
    report = http_wrapper_candidates(
        frontend.candidate_calls, frontend.builder_uses, frontend.calls, wrappers=only_regex
    )
    assert {item.method: item.configured for item in report.items} == {
        "getVersioned": True,
        "requestVersioned": False,
        "request": False,
    }


@pytest.mark.parametrize("kind", ["http-wrappers", "url-builders"])
def test_command_reports_configured(kind: str, tmp_path: Path) -> None:
    root = _copy_seam(tmp_path / "ws")
    args = [
        "setup",
        "candidates",
        kind,
        "--root",
        str(root),
        "--config",
        str(root / "docpipe.yaml"),
    ]
    result = runner.invoke(app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert {item["configured"] for item in json.loads(result.output)["items"]} == {True}
    text = runner.invoke(app, args)
    mark = "[уже в web.http_wrappers]" if kind == "http-wrappers" else "[уже в web.url_builders]"
    assert mark in text.output


# --------------------------------------------------------------------------------------
# `setup explain`: какая запись сделала вызов
# --------------------------------------------------------------------------------------


def test_explain_names_the_records_and_counts_the_bodies(
    seam: Path, settings: DocpipeConfig
) -> None:
    report = explain_path(
        SetupContext(seam, settings, seam / "docpipe.yaml", use_cache=False), "frontend/src/app"
    )
    decisions = {
        (ref.key, ref.value): (ref.count, ref.effect)
        for ref in report.decisions
        if ref.key in ("web.http_wrappers", "web.url_builders")
    }
    assert decisions == {
        ("web.http_wrappers", "HTTP./^(get|post|put|delete)Versioned$/"): (
            5,
            "вызовов через обёртку 1, тел обёртки 4; адрес — аргумент 1",
        ),
        ("web.http_wrappers", "HTTP.requestVersioned"): (
            1,
            "вызовов через обёртку 1, тел обёртки 0; адрес — аргумент 2",
        ),
        ("web.http_wrappers", "rest.request"): (
            1,
            "вызовов через обёртку 1, тел обёртки 0; адрес — аргумент 0.url",
        ),
        ("web.url_builders", "apiUrl.buildUrl"): (2, "адрес от построителя: путь — аргумент 0"),
    }
    assert report.calls["inside_wrappers"] == 4
