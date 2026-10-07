"""Ключ вызова фронта: верный или явный отказ (S16).

До S16 пять форм давали ключ, который выглядит настоящим и неверен: `const`
соседнего метода, изменяемое поле, невосстановленная база конкатенации,
хвостовой построитель query и срезанный хост. Ни один счётчик этого
не показывал — число «восстановлено» росло за счёт ошибок. Поэтому почти
каждая проверка здесь парная: верный ключ там, где он есть, и отказ
**с причиной своего вида** там, где его нет.

Половина проверок — на фикстуре `SeamWorkspace` (каждая форма — свой метод,
`tests/test_seam_fixture.py`), половина — на коротких исходниках, где
видна одна конструкция. Правила передаются явно — см. docstring
`test_seam_fixture.py` о первой ступени `resolve_input` в git worktree.
"""

import json
import re
from pathlib import Path
from typing import Final

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, load_config
from docpipe.emit import ScanResult
from docpipe.emit import run as run_dotnet
from docpipe.model import SCHEMA_VERSION, Manifest, ParserVersions, check_schema_version
from docpipe.web.calls import (
    REASON_CONCAT_BASE,
    REASON_CONCAT_NO_LITERAL,
    REASON_EXPRESSION,
    REASON_MUTABLE_FIELD,
    REASON_PARAMETER,
    REASON_REASSIGNED,
    REASON_VARIABLE,
    RawCall,
    RegistryCall,
    build_calls,
    scan_calls,
    url_host,
)
from docpipe.web.link import LinkReport, build_report
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
BUILDER_REASON: Final = "значение переменной — вызов `apiUrl.buildUrl(…)`"


def _calls(source: str) -> list[RawCall]:
    return scan_calls(source.encode(), "src/a.ts")


def _one(source: str) -> RawCall:
    [call] = _calls(source)
    return call


# --------------------------------------------------------------------------------------
# На фикстуре
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def frontend(tmp_path_factory: pytest.TempPathFactory) -> WebScanResult:
    settings = load_config(SEAM / "docpipe.yaml")
    cache = tmp_path_factory.mktemp("cache-web")
    return run_web(SEAM, settings, load_ruleset(RULES, "web"), cache)


@pytest.fixture(scope="module")
def backend(tmp_path_factory: pytest.TempPathFactory) -> ScanResult:
    settings = load_config(SEAM / "docpipe.yaml")
    cache = tmp_path_factory.mktemp("cache-dotnet")
    return run_dotnet(SEAM, settings, load_ruleset(RULES, "dotnet"), cache)


@pytest.fixture(scope="module")
def link(backend: ScanResult, frontend: WebScanResult) -> LinkReport:
    return build_report(backend.manifest, frontend.manifest)


def _resolved(frontend: WebScanResult, file: str, member: str) -> list[tuple[str, str, str]]:
    return [
        (call.key.http_method, call.key.route, call.confidence)
        for call in frontend.calls.calls
        if call.file.endswith(file) and call.member == member
    ]


def _unresolved(frontend: WebScanResult, file: str, line: int) -> RawCall:
    [found] = [
        item for item in frontend.calls.unresolved if item.file.endswith(file) and item.line == line
    ]
    return found


def _line_of(file: str, needle: str) -> int:
    """Строка конструкции в файле фикстуры: проверка не должна зависеть от номера."""
    lines = (SEAM / "frontend/src/app" / file).read_text(encoding="utf-8").splitlines()
    [line] = [index + 1 for index, text in enumerate(lines) if needle in text]
    return line


def test_each_method_resolves_its_own_const(frontend: WebScanResult) -> None:
    """`list` и `get` больше не получают литерал `archived`; `archived` — свой.

    Обе — построитель адреса (S19): значение `const url` — вызов, и причина
    называет вызов, а не безымянную «переменную».
    """
    assert _resolved(frontend, "apps.service.ts", "archived") == [
        ("GET", "api/apps/archived", "high")
    ]
    assert _resolved(frontend, "apps.service.ts", "list") == []
    assert _resolved(frontend, "apps.service.ts", "get") == []

    # Вызов — строкой ниже своего `const url`.
    for builder in ("buildUrl('/api/apps')", "buildUrl(`/api/apps/${app}`)"):
        line = _line_of("services/apps.service.ts", builder) + 1
        item = _unresolved(frontend, "apps.service.ts", line)
        assert (item.url, item.expression, item.reason) == (None, "url", BUILDER_REASON)


def test_mutable_field_is_unresolved(frontend: WebScanResult) -> None:
    """`public fileSource = ''` с `this.fileSource = src` — не `GET ''`."""
    line = _line_of("components/editor.component.ts", "this.http.get(this.fileSource")
    item = _unresolved(frontend, "editor.component.ts", line)
    assert (item.url, item.reason) == (None, REASON_MUTABLE_FIELD)
    assert not any(call.file.endswith("editor.component.ts") for call in frontend.calls.calls)


def test_leading_unresolved_base_of_a_concatenation_is_refused(frontend: WebScanResult) -> None:
    line = _line_of("services/apps.service.ts", "this.base + '/api/apps'")
    item = _unresolved(frontend, "apps.service.ts", line)
    assert (item.url, item.reason) == (None, REASON_CONCAT_BASE)
    assert _resolved(frontend, "apps.service.ts", "legacy") == []


def test_trailing_query_builder_is_dropped_with_medium_confidence(
    frontend: WebScanResult,
) -> None:
    assert _resolved(frontend, "apps.service.ts", "search") == [
        ("GET", "api/apps/search", "medium")
    ]


def test_absolute_url_keeps_its_host(frontend: WebScanResult) -> None:
    [call] = [call for call in frontend.calls.calls if call.file.endswith("feed.service.ts")]
    assert call.host == "ext.example.org"
    # Ключ по-прежнему без хоста: сопоставление с эндпоинтом от хоста не зависит.
    assert call.key.route == "feed.json"
    # Хост уходит в манифест вместе с вызовом, на узел сервиса.
    [node] = [node for node in frontend.manifest.nodes if node.title == "FeedService"]
    assert [item.host for item in node.web_calls] == ["ext.example.org"]


def test_calls_total_equals_calls_in_the_code(frontend: WebScanResult, link: LinkReport) -> None:
    """Каждый восстановленный вызов посчитан один раз: DTO рядом с сервисом — ни одного."""
    assert link.counts["calls_total"] == len(frontend.calls.calls) == 4
    by_title = {node.title: node for node in frontend.manifest.nodes}
    assert by_title["AppDto"].web_calls == []
    assert {call.member for call in by_title["AppsService"].web_calls} == {"archived", "search"}
    assert frontend.meta.stats["calls_unattributed"] == 0


# --------------------------------------------------------------------------------------
# П. 1. Константы — по области функции
# --------------------------------------------------------------------------------------


def test_const_of_another_function_is_never_used() -> None:
    calls = _calls(
        """
export class A {
  first() { const url = 'api/first'; return this.http.get(url); }
  second() { return this.http.get(url); }
}
"""
    )
    assert [(call.url, call.reason) for call in calls] == [
        ("api/first", ""),
        (None, REASON_VARIABLE),
    ]


def test_local_const_shadows_the_module_const() -> None:
    calls = _calls(
        """
const url = 'api/module';
export class A {
  local() { const url = 'api/local'; return this.http.get(url); }
  outer() { return this.http.get(url); }
}
"""
    )
    assert [call.url for call in calls] == ["api/local", "api/module"]


def test_module_const_declared_below_the_class_is_seen_from_a_method() -> None:
    """Метод зовут после того, как файл выполнился: порядок через границу функции не важен."""
    call = _one(
        """
export class A { run() { return this.http.get(URL); } }
const URL = 'api/below';
"""
    )
    assert call.url == "api/below"


def test_const_declared_below_the_call_in_the_same_function_shadows_the_outer() -> None:
    """Имя уже занято объявлением ниже (TDZ): внешний литерал — чужой."""
    call = _one(
        """
const url = 'api/module';
export class A {
  run() { this.http.get(url); const url = 'api/late'; }
}
"""
    )
    assert (call.url, call.reason) == (None, REASON_VARIABLE)


def test_closure_sees_the_const_of_its_function() -> None:
    call = _one(
        """
export class A {
  run() {
    const url = 'api/closure';
    return defer(() => this.http.get(url));
  }
}
"""
    )
    assert call.url == "api/closure"


def test_const_of_an_inner_block_is_not_seen_outside() -> None:
    calls = _calls(
        """
export class A {
  run(flag: boolean) {
    if (flag) { const url = 'api/inner'; this.http.get(url); }
    return this.http.get(url);
  }
}
"""
    )
    assert [call.url for call in calls] == ["api/inner", None]


def test_parameter_shadows_the_module_const() -> None:
    """`download(url)` с `const url` модуля: адрес — параметр, а не литерал модуля."""
    call = _one(
        """
const url = 'api/module';
export class A { download(url: string) { return this.http.get(url); } }
"""
    )
    assert (call.url, call.reason) == (None, REASON_PARAMETER)


def test_arrow_parameter_without_parentheses_is_a_parameter() -> None:
    call = _one("const url = 'api/m';\nexport const f = url => this.http.get(url);\n")
    assert (call.url, call.reason) == (None, REASON_PARAMETER)


def test_let_is_a_constant_only_while_nobody_assigns_it() -> None:
    calls = _calls(
        """
export class A {
  stable() { let url = 'api/stable'; return this.http.get(url); }
  moving(id: string) { let url = 'api/a'; if (id) { url = 'api/b'; } return this.http.get(url); }
}
"""
    )
    assert [(call.url, call.reason) for call in calls] == [
        ("api/stable", ""),
        (None, REASON_REASSIGNED),
    ]


def test_const_initialised_by_a_call_names_the_call() -> None:
    """Причина различает построитель адреса: по ней группирует сводка шва."""
    calls = _calls(
        """
export class A {
  a() { const url = this.apiUrl.buildUrl('/api/a'); return this.http.get(url); }
  b() { const url = buildUrl('/api/b'); return this.http.get(url); }
  c() { const url = environment.apiUrl; return this.http.get(url); }
}
"""
    )
    assert [call.reason for call in calls] == [
        "значение переменной — вызов `apiUrl.buildUrl(…)`",
        "значение переменной — вызов `buildUrl(…)`",
        REASON_VARIABLE,
    ]


def test_scoped_const_works_inside_a_template_substitution() -> None:
    calls = _calls(
        """
export class A {
  first() { const id = 'x'; return this.http.get(`api/items/${id}`); }
  second(id: string) { return this.http.get(`api/items/${id}`); }
}
"""
    )
    assert [call.url for call in calls] == ["api/items/x", "api/items/{}"]


# --------------------------------------------------------------------------------------
# П. 2. Поле — константа, только если его не присваивают
# --------------------------------------------------------------------------------------


def test_field_assigned_outside_its_initializer_is_not_a_constant() -> None:
    calls = _calls(
        """
export class A {
  public base = '/api/default';
  public fixed = '/api/fixed';
  configure(base: string) { this.base = base; }
  run() { this.http.get(`${this.base}/x`); return this.http.get(this.fixed); }
}
"""
    )
    assert [(call.url, call.reason) for call in calls] == [
        (None, "база в начале шаблона не восстановлена"),
        ("/api/fixed", ""),
    ]


def test_compound_assignment_also_mutates_the_field() -> None:
    call = _one(
        """
export class A {
  public url = 'api/x';
  more() { this.url += '/more'; }
  run() { return this.http.get(this.url); }
}
"""
    )
    assert (call.url, call.reason) == (None, REASON_MUTABLE_FIELD)


def test_readonly_field_assigned_in_the_constructor_is_not_a_constant() -> None:
    """Отступление от буквы S16 п. 2 («`readonly` — всегда»): TypeScript
    разрешает присвоить `readonly` в конструкторе, и значение инициализатора
    тогда так же неверно, как у изменяемого поля."""
    calls = _calls(
        """
export class A {
  private readonly url = 'api/default';
  private readonly base = 'api/base';
  constructor(cfg: Cfg) { this.url = cfg.url; }
  run() { this.http.get(this.url); return this.http.get(this.base); }
}
"""
    )
    assert {call.expression: (call.url, call.reason) for call in calls} == {
        "this.url": (None, REASON_MUTABLE_FIELD),
        "this.base": ("api/base", ""),
    }


def test_assignment_in_another_class_does_not_mutate_the_field() -> None:
    calls = _calls(
        """
export class First {
  public url = 'api/first';
  run() { return this.http.get(this.url); }
}
export class Second {
  public url = 'api/second';
  set(value: string) { this.url = value; }
  run() { return this.http.get(this.url); }
}
"""
    )
    assert [(call.url, call.reason) for call in calls] == [
        ("api/first", ""),
        (None, REASON_MUTABLE_FIELD),
    ]


# --------------------------------------------------------------------------------------
# П. 3. Конкатенация
# --------------------------------------------------------------------------------------


def test_unresolved_head_of_a_concatenation_is_refused() -> None:
    calls = _calls(
        """
export class A {
  run(id: string) {
    this.http.get(this.base + '/api/apps');
    this.http.get(id + '/tail');
    this.http.get(id + other);
    return this.http.get('api/x/' + id);
  }
}
"""
    )
    assert [(call.url, call.reason) for call in calls] == [
        (None, REASON_CONCAT_BASE),
        (None, REASON_CONCAT_BASE),
        (None, REASON_CONCAT_NO_LITERAL),
        ("api/x/{}", ""),
    ]


def test_resolved_head_of_a_concatenation_is_kept() -> None:
    call = _one(
        """
export class A {
  private readonly base = '/api/apps';
  run(id: string) { return this.http.get(this.base + '/' + id); }
}
"""
    )
    assert (call.url, call.confidence) == ("/api/apps/{}", "medium")


def test_template_inside_a_concatenation_is_resolved() -> None:
    call = _one("export class A { run(id) { this.http.get(`api/x/${id}` + '/tail'); } }\n")
    assert call.url == "api/x/{}/tail"


def test_choice_is_not_a_concatenation() -> None:
    """`url || 'api/x'` — выбор: развёртка как `+` дала бы `{}api/x`."""
    call = _one("export class A { run(url) { this.http.get(url || 'api/default'); } }\n")
    assert (call.url, call.reason) == (None, REASON_EXPRESSION)


# --------------------------------------------------------------------------------------
# П. 4. Хвостовой построитель query
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argument", "url", "confidence"),
    [
        # Подстановка прилипла к последнему сегменту и стоит последней — отбрасывается.
        ("`api/apps/search${buildQuery(q)}`", "api/apps/search", "medium"),
        ("`api/apps${StringHelper.buildQuery(q)}`", "api/apps", "medium"),
        # Целый сегмент — параметр маршрута, как и был.
        ("`api/apps/${id}`", "api/apps/{}", "high"),
        # Не последняя — середина пути, не хвост.
        ("`api/apps/x${id}/tail`", "api/apps/x{}/tail", "high"),
        # После `?` — query: её срежет нормализация, уверенность не падает.
        ("`api/apps?page=${page}`", "api/apps?page={}", "high"),
        # Восстановленная подстановка — просто литерал.
        ("`api/apps/search${SUFFIX}`", "api/apps/search/all", "high"),
    ],
)
def test_trailing_substitution(argument: str, url: str, confidence: str) -> None:
    call = _one(
        "const SUFFIX = '/all';\n"
        f"export class A {{ run(id, q, page) {{ this.http.get({argument}); }} }}\n"
    )
    assert (call.url, call.confidence) == (url, confidence)


def test_trailing_rule_keeps_the_registry_discriminator_unresolved() -> None:
    """Подстановка после `?` — не хвост пути: `{}` остаётся, уверенность не падает."""
    raw = _calls(
        "export class A { run(type) { this.http.get(`api/items?listInnerName=${type}`); } }\n"
    )
    scan = build_calls(
        raw,
        registry=[RegistryCall(route="api/items", discriminator_in="query", name="listInnerName")],
    )
    assert len(scan.registry_unresolved) == 1
    assert (raw[0].url, raw[0].confidence) == ("api/items?listInnerName={}", "high")


# --------------------------------------------------------------------------------------
# П. 5. Хост
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://ext.example.org/feed.json", "ext.example.org"),
        ("HTTPS://Ext.Example.ORG:8443/feed", "ext.example.org"),
        ("http://user:secret@cdn.example.org/x", "cdn.example.org"),
        ("http://[::1]:8080/x", "[::1]"),
        ("https://ext.example.org", "ext.example.org"),
        ("api/feed.json", ""),
        ("/api/feed.json", ""),
        # Схема не в начале — относительный адрес с параметром, а не внешний.
        ("api/redirect?to=https://ext.example.org/x", ""),
    ],
)
def test_url_host(url: str, host: str) -> None:
    assert url_host(url) == host


def test_host_is_on_the_call_and_not_in_the_key() -> None:
    raw = _calls("export class A { run() { this.http.get('https://Ext.Example.org/api/x'); } }\n")
    [call] = build_calls(raw).calls
    assert call.host == "ext.example.org"
    assert call.key.route == "api/x"


# --------------------------------------------------------------------------------------
# П. 6. Вызов — узлу, в чей диапазон он попал
# --------------------------------------------------------------------------------------


def _workspace(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "angular.json").write_text(
        json.dumps({"projects": {"app": {"root": "", "sourceRoot": "src"}}}), encoding="utf-8"
    )
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_call_outside_every_node_goes_to_all_nodes_of_the_file_and_is_counted(
    tmp_path: Path,
) -> None:
    """Вызов вне диапазона любого узла раздаётся по-старому — и виден числом."""
    root = _workspace(
        tmp_path,
        {
            "src/app/items.service.ts": """import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class ItemsService {
  constructor(private http: HttpClient) {}

  list() {
    return this.http.get('api/items');
  }
}

@Injectable({ providedIn: 'root' })
export class OtherService {
  constructor(private http: HttpClient) {}

  load() {
    return this.http.get('api/other');
  }
}

function boot(http: HttpClient) {
  return http.get('api/boot');
}
""",
        },
    )
    result = run_web(root, DocpipeConfig(), load_ruleset(RULES, "web"))
    by_title = {node.title: node for node in result.manifest.nodes}

    assert sorted(call.key.route for call in by_title["ItemsService"].web_calls) == [
        "api/boot",
        "api/items",
    ]
    assert sorted(call.key.route for call in by_title["OtherService"].web_calls) == [
        "api/boot",
        "api/other",
    ]
    assert result.meta.stats["calls_unattributed"] == 1


def test_web_scan_names_unattributed_calls(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path / "repo",
        {
            "src/app/a.service.ts": """import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class AService {
  constructor(private http: HttpClient) {}
  run() { return this.http.get('api/a'); }
}

function boot(http: HttpClient) { return http.get('api/boot'); }
""",
        },
    )
    out = tmp_path / "w.json"
    result = runner.invoke(
        app, ["web", "scan", "--root", str(root), "--rules", str(RULES), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Вызовов вне диапазона узлов: 1" in result.output


# --------------------------------------------------------------------------------------
# П. 7. Версия манифеста
# --------------------------------------------------------------------------------------


def _manifest_json(version: str) -> str:
    manifest = Manifest(
        ruleset_version="1", parser=ParserVersions(tree_sitter="0.25.0")
    ).model_dump(mode="json")
    manifest["schema_version"] = version
    return json.dumps(manifest)


def test_tool_writes_its_own_version() -> None:
    # S17: 2.1 → 2.2 (`Endpoint.unresolved`, флаги выражений у `Attribute`).
    assert SCHEMA_VERSION == "2.2"
    assert Manifest(ruleset_version="1", parser=ParserVersions(tree_sitter="0")).schema_version == (
        "2.2"
    )


@pytest.mark.parametrize("version", ["2.0", "2.1", "2.2"])
def test_every_minor_of_its_major_up_to_its_own_is_read(version: str) -> None:
    """Манифест 2.0 читается и сохраняет свою версию: она про файл, а не про читателя."""
    assert Manifest.model_validate_json(_manifest_json(version)).schema_version == version


def test_manifest_2_0_without_host_reads_with_empty_host() -> None:
    manifest = json.loads(_manifest_json("2.0"))
    manifest["nodes"] = [
        {
            "id": "type:a",
            "kind": "api-service",
            "template": "api-service",
            "title": "A",
            "doc_path": "docs/a.md",
            "module": "m",
            "domain": "m",
            "signature_hash": "x",
            "web_calls": [
                {
                    "file": "a.ts",
                    "line": 1,
                    "key": {"http_method": "GET", "route": "api/a"},
                    "confidence": "high",
                }
            ],
        }
    ]
    restored = Manifest.model_validate_json(json.dumps(manifest))
    assert restored.nodes[0].web_calls[0].host == ""


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("2.3", "манифест версии 2.3 новее инструмента (2.2): обновите docpipe"),
        ("3.0", "манифест версии 3.0 новее инструмента (2.2): обновите docpipe"),
        ("1.9", "манифест версии 1.9 устарел"),
        ("2", "версия манифеста «2» не распознана"),
        ("2.x", "версия манифеста «2.x» не распознана"),
    ],
)
def test_foreign_version_is_refused_with_a_reason(version: str, message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate_json(_manifest_json(version))
    assert message in str(caught.value)
    with pytest.raises(ValueError, match=re.escape(message)):
        check_schema_version(version)


def test_newer_manifest_gives_one_reason_not_a_list_of_extra_fields() -> None:
    """Новое поле 2.3 на каждом узле дало бы сотню «Extra inputs» вокруг причины."""
    manifest = json.loads(_manifest_json("2.3"))
    manifest["future_field"] = []
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate_json(json.dumps(manifest))
    assert caught.value.error_count() == 1
    assert "обновите docpipe" in str(caught.value)


def test_cli_refuses_a_newer_manifest_with_the_reason(tmp_path: Path) -> None:
    path = tmp_path / "doc-tree.json"
    path.write_text(_manifest_json("3.0"), encoding="utf-8")

    result = runner.invoke(app, ["stats", str(path)])

    assert result.exit_code == 2
    assert "манифест версии 3.0 новее инструмента (2.2): обновите docpipe" in result.output
