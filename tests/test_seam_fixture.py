"""Фикстура форм шва фронт↔.NET `SeamWorkspace` (S15).

Каждая форма шва, найденная на squidex и abp (`docs/findings-seam.md`),
лежит в своём файле или методе, и тесты S16–S21 указывают на неё по имени.
Поэтому первая половина проверяет **наличие конструкции** регулярным
выражением по тексту, а не существование файла: «упрощение» фикстуры
оставило бы тесты этапа C зелёными и бессмысленными.

Вторая половина фиксирует числа прогона — сейчас **после S16**. Это не
спецификация, а точка отсчёта: каждая задача этапа C меняет их и обязана
поправить здесь то, что изменила, с комментарием «задача: было → стало», —
тогда разница видна в диффе теста, а не в пересказе. Что именно проверяет
каждая форма после S16, — в `tests/test_call_keys.py`.

Правила передаются явно, а не ключом `rules` из `docpipe.yaml` фикстуры.
Ключ записан от каталога конфигурации (`../../../rules/rules.yaml`)
и разрешается второй ступенью `resolve_input`, а первая ступень — от текущего
каталога. В git worktree на три уровня ниже основного клона
(`.claude/worktrees/<имя>/`) первая ступень находит правила **основного
клона** — другой ветки, — и прогон молча берёт чужой набор.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, candidate_inputs, load_config
from docpipe.emit import ScanResult
from docpipe.emit import run as run_dotnet
from docpipe.web.link import LinkReport, build_report
from docpipe.web.resolve import parse_jsonc
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
CONFIG: Final = SEAM / "docpipe.yaml"
BACKEND: Final = "backend/Seam.Api"
FRONTEND: Final = "frontend"
APP: Final = f"{FRONTEND}/src/app"
RULES: Final = Path("rules/rules.yaml")


# --------------------------------------------------------------------------------------
# Формы: конструкция, а не файл
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Form:
    """Форма шва: где лежит и по каким признакам опознаётся.

    `member` сужает поиск до тела метода класса: две формы в одном файле
    различаются именно методом, и проверка по всему файлу не заметила бы,
    что форма переехала в соседний метод и перестала быть отдельной.
    """

    id: str
    path: str
    patterns: tuple[str, ...]
    absent: tuple[str, ...] = ()
    member: str = ""


FORMS: Final[tuple[Form, ...]] = (
    # .NET -----------------------------------------------------------------------------
    Form(
        "base-route-constant",
        f"{BACKEND}/Web/ApiController.cs",
        (
            r"\[ApiController\]\s*\[Route\(Constants\.PrefixApi\)\]\s*"
            r"public abstract class ApiController : ControllerBase\b",
        ),
    ),
    Form(
        "prefix-constant",
        f"{BACKEND}/Web/Constants.cs",
        (r'public const string PrefixApi = "api";',),
    ),
    Form(
        "controller-on-base",
        f"{BACKEND}/Controllers/AppsController.cs",
        (
            r"public sealed class AppsController : ApiController\b",
            r'\[HttpGet\("apps"\)\]',
            r'\[HttpGet\("apps/\{app\}"\)\]',
            r'\[HttpPost\("apps"\)\]',
            r'\[HttpPut\("apps/\{app\}"\)\]',
        ),
        # Свой маршрут у класса сделал бы базу ненужной, и форма пропала бы.
        absent=(r"\[Route\(",),
    ),
    Form(
        "token-base",
        f"{BACKEND}/Web/TokenApiController.cs",
        (r'\[Route\("api/\[controller\]"\)\]\s*public abstract class TokenApiController\b',),
    ),
    Form(
        "token-heir",
        f"{BACKEND}/Controllers/OrdersController.cs",
        (r"public sealed class OrdersController : TokenApiController\b", r"\[HttpGet\]"),
        absent=(r"\[Route\(",),
    ),
    Form(
        "same-relative-route",
        f"{BACKEND}/Controllers/InfoController.cs",
        (r"public sealed class InfoController : ApiController\b", r'\[HttpGet\("info"\)\]'),
        absent=(r"\[Route\(",),
    ),
    Form(
        "same-relative-route-other-base",
        f"{BACKEND}/Controllers/OrdersController.cs",
        (r'\[HttpGet\("info"\)\]',),
    ),
    Form(
        "route-without-verb",
        f"{BACKEND}/Controllers/CommentsController.cs",
        (
            r'\[Route\("api"\)\]\s*public sealed class CommentsController : ControllerBase\b',
            r'\[Route\("comments/\{id\}"\)\]\s*public IActionResult \w+\(',
        ),
        absent=(r"\[Http\w+",),
    ),
    Form(
        "accept-verbs",
        f"{BACKEND}/Controllers/VerbsController.cs",
        (r'\[AcceptVerbs\("GET", "POST"\)\]\s*public IActionResult \w+\(',),
        absent=(r"\[Http\w+",),
    ),
    Form(
        "public-api",
        f"{BACKEND}/Controllers/ContentController.cs",
        (
            r'\[Route\("content/\{app\}"\)\]\s*'
            r"public sealed class ContentController : ApiController\b",
        ),
    ),
    Form(
        "conventional-controller",
        f"{BACKEND}/Controllers/LegacyController.cs",
        (r"public sealed class LegacyController : Controller\b", r"public IActionResult \w+\("),
        absent=(r"\[Route\(", r"\[Http\w+", r"\[AcceptVerbs"),
    ),
    Form(
        "map-controller-route",
        f"{BACKEND}/Program.cs",
        (
            # Top-level statements: класса нет, регистрация — в дереве файла.
            r"^var builder = WebApplication\.CreateBuilder\(args\);",
            r'app\.MapControllerRoute\(\s*name: "legacy",\s*pattern: "legacy/',
            r'app\.MapGet\("/health",',
        ),
        absent=(r"\bclass\b",),
    ),
    # Фронт ----------------------------------------------------------------------------
    Form(
        "direct-literal",
        f"{APP}/services/info.service.ts",
        (r"return this\.http\.get\('api/info'\);",),
        member="getInfo",
    ),
    Form(
        "builder",
        f"{APP}/services/apps.service.ts",
        (
            r"const url = this\.apiUrl\.buildUrl\('/api/apps'\);",
            r"return this\.http\.get<AppDto\[\]>\(url\);",
        ),
        member="list",
    ),
    Form(
        "builder-second-method",
        f"{APP}/services/apps.service.ts",
        (
            r"const url = this\.apiUrl\.buildUrl\(`/api/apps/\$\{app\}`\);",
            r"return this\.http\.get<AppDto>\(url\);",
        ),
        member="get",
    ),
    Form(
        # Литерал под тем же именем `url` в третьем методе того же файла:
        # без него ошибку области `const` (S16, п. 1) нечем было бы показать.
        "foreign-literal-const",
        f"{APP}/services/apps.service.ts",
        (r"const url = 'api/apps/archived';", r"return this\.http\.get<AppDto\[\]>\(url\);"),
        member="archived",
    ),
    Form(
        "trailing-query-builder",
        f"{APP}/services/apps.service.ts",
        (r"this\.http\.get<AppDto\[\]>\(`api/apps/search\$\{buildQuery\(query\)\}`\)",),
        member="search",
    ),
    Form(
        "unresolved-base-concatenation",
        f"{APP}/services/apps.service.ts",
        (r"this\.http\.get<AppDto\[\]>\(this\.base \+ '/api/apps'\)",),
        member="legacy",
    ),
    Form(
        # База — поле без литерала: иначе она восстановилась бы как константа.
        "unresolved-base-field",
        f"{APP}/services/apps.service.ts",
        (r"private readonly base = environment\.apiUrl;",),
    ),
    Form(
        # DTO рядом с сервисом: вызовы сервиса приписываются и ему (S16, п. 6).
        "dto-beside-service",
        f"{APP}/services/apps.service.ts",
        (
            r"^export interface AppDto \{",
            r"^@Injectable\(\{ providedIn: 'root' \}\)\nexport class AppsService \{",
        ),
    ),
    Form(
        "wrapper-positional-definition",
        f"{APP}/framework/http-extensions.ts",
        (
            r"^export module HTTP \{",
            r"export function getVersioned\(http: HttpClient, url: string\b",
            r"return http\.get\(url, ",
            r"export function postVersioned\(http: HttpClient, url: string\b",
            r"return http\.post\(url, body, ",
        ),
    ),
    Form(
        "wrapper-method-argument-definition",
        f"{APP}/framework/http-extensions.ts",
        (
            r"export function requestVersioned\(\s*http: HttpClient,"
            r"\s*method: string,\s*url: string,",
            r"return http\.request\(method, url, ",
        ),
    ),
    Form(
        "wrapper-positional-call",
        f"{APP}/services/apps-versioned.service.ts",
        (r"const url = 'api/apps';", r"return HTTP\.getVersioned\(this\.http, url\);"),
        member="getApps",
    ),
    Form(
        "wrapper-method-argument-call",
        f"{APP}/services/apps-versioned.service.ts",
        (r"HTTP\.requestVersioned\(this\.http, 'PUT', `api/apps/\$\{app\}`",),
        member="putApp",
    ),
    Form(
        "wrapper-object-call",
        f"{APP}/services/apps-proxy.service.ts",
        (r"this\.rest\.request<unknown>\(\{ method: 'POST', url: '/api/apps'",),
        member="create",
    ),
    Form(
        "wrapper-object-definition",
        f"{APP}/framework/rest.service.ts",
        (r"this\.http\.request<T>\(config\.method, config\.url",),
        member="request",
    ),
    Form(
        "external-host",
        f"{APP}/services/feed.service.ts",
        (r"this\.http\.get\('https://ext\.example\.org/feed\.json'\)",),
        member="latest",
    ),
    Form(
        "hypermedia-request",
        f"{APP}/services/links.service.ts",
        (r"return this\.http\.request\(link\.method, link\.href\);",),
        member="follow",
    ),
    Form(
        # Видимая половина гипермедиа: без неё в невосстановленных
        # не было бы ни одного вызова этого файла (`request` не виден).
        "hypermedia-get",
        f"{APP}/services/links.service.ts",
        (r"return this\.http\.get\(link\.href\);",),
        member="fetch",
    ),
    Form(
        "mutable-field-declaration",
        f"{APP}/components/editor.component.ts",
        (r"^  public fileSource = '';",),
    ),
    Form(
        "mutable-field-call",
        f"{APP}/components/editor.component.ts",
        (r"this\.fileSource = src;", r"this\.http\.get\(this\.fileSource, "),
        member="open",
    ),
    Form(
        "page-route",
        f"{APP}/app.routes.ts",
        (r"export const routes: Routes = \[", r"\{ path: 'apps', component: AppsPageComponent \}"),
    ),
    Form(
        "page-calls-service",
        f"{APP}/pages/apps-page.component.ts",
        (
            r"import \{ AppDto, AppsService \} from '@app/services/apps\.service';",
            r"this\.appsService\.list\(\)",
        ),
    ),
    Form(
        "builder-definition",
        f"{APP}/framework/api-url.ts",
        (r"public buildUrl\(path: string\): string \{",),
    ),
    Form(
        "query-builder-definition",
        f"{APP}/framework/query.ts",
        (r"^export function buildQuery\(query: Record<string, string>\): string \{",),
    ),
)


def _member_body(text: str, name: str) -> str:
    """Тело метода класса: от строки сигнатуры до `}` на том же отступе."""
    match = re.search(
        rf"^(?P<indent>[ ]+){re.escape(name)}(?:<[^>]*>)?\([^\n]*\{{\n"
        r"(?P<body>.*?)^(?P=indent)\}$",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"метод {name} не найден"
    return match.group("body")


@pytest.mark.parametrize("form", FORMS, ids=lambda form: form.id)
def test_form_construct_is_present(form: Form) -> None:
    text = (SEAM / form.path).read_text(encoding="utf-8")
    scope = _member_body(text, form.member) if form.member else text

    missing = [p for p in form.patterns if not re.search(p, scope, re.MULTILINE)]
    present = [p for p in form.absent if re.search(p, scope, re.MULTILINE)]

    assert missing == [], f"{form.path}: формы {form.id} больше нет"
    assert present == [], f"{form.path}: у формы {form.id} появилось то, чего быть не должно"


@pytest.mark.parametrize(
    "path",
    [
        "services/info.service.ts",
        "services/apps.service.ts",
        "services/apps-versioned.service.ts",
        "services/feed.service.ts",
        "services/links.service.ts",
        "framework/rest.service.ts",
        "components/editor.component.ts",
    ],
)
def test_http_client_is_injected_through_the_constructor(path: str) -> None:
    """Получатель `this.http` — поле из параметра конструктора, как в Angular.

    Иначе фильтр получателей проверялся бы на форме, которой в коде не бывает.
    """
    text = (SEAM / APP / path).read_text(encoding="utf-8")
    assert re.search(r"constructor\(private http: HttpClient\b", text)
    assert re.search(r"^@(Injectable|Component)\(\{", text, re.MULTILINE)


# --------------------------------------------------------------------------------------
# Окружение шва: прокси, сборка, tsconfig, настройка фикстуры
# --------------------------------------------------------------------------------------


def test_angular_json_names_the_proxy() -> None:
    data = json.loads((SEAM / FRONTEND / "angular.json").read_text(encoding="utf-8"))
    project = data["projects"]["seam-web"]

    assert project["sourceRoot"] == "src"
    proxy = project["architect"]["serve"]["options"]["proxyConfig"]
    assert proxy == "proxy.conf.json"
    assert (SEAM / FRONTEND / proxy).is_file()


def test_proxy_rewrites_the_path() -> None:
    """`pathRewrite` — то, что агент переносит в `web.url_rewrite` модуля."""
    data = json.loads((SEAM / FRONTEND / "proxy.conf.json").read_text(encoding="utf-8"))
    assert data["/api"]["pathRewrite"] == {"^/api/": "/api/"}


def test_package_json_declares_angular() -> None:
    data = json.loads((SEAM / FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert "@angular/core" in data["dependencies"]


def test_tsconfig_has_comments_and_the_alias_needs_jsonc() -> None:
    """Без разбора JSONC алиас `@app/*` не читается, и импорт страницы не разрешится."""
    text = (SEAM / FRONTEND / "tsconfig.json").read_text(encoding="utf-8")

    assert text.startswith("/*")
    assert re.search(r"^\s+// ", text, re.MULTILINE)
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    assert parse_jsonc(text)["compilerOptions"]["paths"] == {"@app/*": ["src/app/*"]}


def test_fixture_config_has_scope_and_no_seam_rules() -> None:
    """Правил шва в настройке нет: их добавляют тесты S16–S21 в копии фикстуры."""
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert raw == {
        "roots": ["backend"],
        "rules": "../../../rules/rules.yaml",
        "web": {"roots": ["frontend"], "rules": "../../../rules/rules.yaml"},
    }
    settings = load_config(CONFIG)
    assert settings.web.url_rewrite == []
    assert settings.web.registry_calls == []


def test_fixture_rules_resolve_beside_the_config() -> None:
    """Вторая ступень `resolve_input` ведёт в правила этого репозитория.

    Проверяется кандидат второй ступени, а не победитель: см. docstring модуля.
    """
    settings = load_config(CONFIG)
    for value in (settings.rules, settings.web.rules):
        beside = candidate_inputs(value, CONFIG)[-1]
        assert beside.resolve() == RULES.resolve()


def test_no_fixture_file_is_gitignored() -> None:
    """Строки `obj/`, `node_modules/`, `dist/`, `environments/` в `.gitignore`
    выкинули бы файлы фикстуры из свежего клона, а локально всё бы проходило."""
    if shutil.which("git") is None or not Path(".git").exists():
        pytest.skip("нужен git-клон")
    files = sorted(str(path) for path in SEAM.rglob("*") if path.is_file())
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *files],
        capture_output=True,
        text=True,
    )
    assert result.stdout.split() == []


# --------------------------------------------------------------------------------------
# Прогоны без ошибок
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> DocpipeConfig:
    return load_config(CONFIG)


@pytest.fixture(scope="module")
def backend(settings: DocpipeConfig, tmp_path_factory: pytest.TempPathFactory) -> ScanResult:
    cache = tmp_path_factory.mktemp("cache-dotnet")
    return run_dotnet(SEAM, settings, load_ruleset(RULES, "dotnet"), cache)


@pytest.fixture(scope="module")
def frontend(settings: DocpipeConfig, tmp_path_factory: pytest.TempPathFactory) -> WebScanResult:
    cache = tmp_path_factory.mktemp("cache-web")
    return run_web(SEAM, settings, load_ruleset(RULES, "web"), cache)


@pytest.fixture(scope="module")
def link(backend: ScanResult, frontend: WebScanResult) -> LinkReport:
    # Без `configured_modules`: в настройке фикстуры `url_rewrite` нет.
    return build_report(backend.manifest, frontend.manifest)


def test_scan_runs_without_errors(backend: ScanResult) -> None:
    assert backend.meta.parse_error_files == []
    assert backend.meta.stats["parse_errors"] == 0
    assert [module.name for module in backend.manifest.modules] == ["Seam.Api"]
    # Обход сужен `roots: [backend]`: ни одного файла фронта.
    assert all(node.module == "Seam.Api" for node in backend.manifest.nodes)


def test_web_scan_runs_without_errors(frontend: WebScanResult) -> None:
    assert frontend.meta.parse_error_files == []
    assert [(m.id, m.name) for m in frontend.manifest.modules] == [
        ("module:frontend/src", "seam-web")
    ]
    by_title = {node.title: node for node in frontend.manifest.nodes}
    assert by_title["AppsPageComponent"].kind == "page"
    assert [entry.path for entry in by_title["AppsPageComponent"].routes] == ["apps"]
    # Импорт через алиас `@app/*` разрешился: tsconfig прочитан вместе с комментариями.
    assert [usage.member for usage in by_title["AppsPageComponent"].uses] == ["list"]


# --------------------------------------------------------------------------------------
# Числа после S16 — их обновляет каждая следующая задача этапа C (S17–S21)
# --------------------------------------------------------------------------------------

LINK_COUNTS: Final = {
    "linked": 0,
    # S16: 1 → 0. `GET ''` редактора «почти» совпадал с `GET {id}` заказов
    # (оба ключа без фиксированных сегментов); вызов редактора теперь
    # невосстановлен — поле присваивается вне инициализатора.
    "almost": 0,
    # S16: 12 → 4. Вызовы больше не удваиваются на DTO, и из восстановленных
    # ушли четыре ключа-ошибки (`list`, `get`, `legacy`, редактор).
    "calls_without_endpoint": 4,
    # S16: 9 → 10. `GET {id}` заказов больше не «занят» выдуманной связью.
    "endpoints_without_caller": 10,
    # `GET info` у InfoController и OrdersController: базы не наследуются (S17).
    "duplicate_endpoints": 1,
    # S16: 13 → 4. Ровно число восстановленных вызовов в коде: вызов достаётся
    # узлу, в чей диапазон попал, а не каждому узлу файла (`AppDto` — ни одного).
    "calls_total": 4,
    "endpoints_total": 10,
}


def test_endpoints(backend: ScanResult) -> None:
    """До S17: префикс базы не наследуется, `[Route]` без глагола и `AcceptVerbs` пусты."""
    endpoints = {
        node.title: [(item.http_method, item.route) for item in node.endpoints]
        for node in backend.manifest.nodes
    }
    assert endpoints == {
        "ApiController": [],
        "AppsController": [
            ("GET", "apps"),
            ("POST", "apps"),
            ("GET", "apps/{app}"),
            ("PUT", "apps/{app}"),
        ],
        "CommentsController": [],
        "ContentController": [
            ("GET", "content/{app}/{schema}"),
            ("POST", "content/{app}/{schema}"),
            ("GET", "content/{app}/{schema}/{id}"),
        ],
        "InfoController": [("GET", "info")],
        "LegacyController": [],
        # Пустой маршрут — `[HttpGet]` без базы: в ключи связи он не идёт.
        "OrdersController": [("GET", ""), ("GET", "info"), ("GET", "{id}")],
        "TokenApiController": [],
        "VerbsController": [],
    }
    assert sum(len(node.endpoints) for node in backend.manifest.nodes) == 11


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def test_resolved_calls(frontend: WebScanResult) -> None:
    """S16: восстановлено 8 → 4, и ни одного ключа-ошибки.

    До S16 у шести из восьми ключ был правдоподобен и неверен: `list` и `get`
    получали литерал метода `archived`, `legacy` — `{}/api/apps`, `search` —
    `api/apps/search{}`, редактор — `GET ''`, а у `feed.json` был срезан хост.
    Член, а не строка: правка фикстуры не должна ломать проверку смысла.
    """
    calls = sorted(
        (_name(call.file), call.member, call.key.http_method, call.key.route, call.confidence)
        for call in frontend.calls.calls
    )
    assert calls == [
        ("apps.service.ts", "archived", "GET", "api/apps/archived", "high"),
        ("apps.service.ts", "search", "GET", "api/apps/search", "medium"),  # S16: хвост query
        ("feed.service.ts", "latest", "GET", "feed.json", "high"),
        ("info.service.ts", "getInfo", "GET", "api/info", "high"),
    ]
    # S16: хост сохранён рядом с ключом, сам ключ — по-прежнему без хоста.
    assert {_name(call.file): call.host for call in frontend.calls.calls} == {
        "apps.service.ts": "",
        "feed.service.ts": "ext.example.org",
        "info.service.ts": "",
    }


def test_unresolved_calls(frontend: WebScanResult) -> None:
    """S16: не восстановлено 5 → 9, у каждого — причина своего вида.

    Четыре новых — бывшие ключи-ошибки: `list` и `get` (значение — вызов
    построителя), `legacy` (база конкатенации), редактор (изменяемое поле).
    Тела обёрток теперь названы параметром функции, а не «переменной».
    """
    unresolved = sorted(
        (_name(item.file), item.http_method, item.expression, item.reason)
        for item in frontend.calls.unresolved
    )
    builder = "значение переменной — вызов `apiUrl.buildUrl(…)`"
    parameter = "значение переменной — параметр функции"
    assert unresolved == [
        (
            "apps.service.ts",
            "GET",
            "this.base + '/api/apps'",
            "база в начале конкатенации не восстановлена",
        ),
        ("apps.service.ts", "GET", "url", builder),
        ("apps.service.ts", "GET", "url", builder),
        ("editor.component.ts", "GET", "this.fileSource", "поле присваивается вне инициализатора"),
        ("http-extensions.ts", "DELETE", "url", parameter),
        ("http-extensions.ts", "GET", "url", parameter),
        ("http-extensions.ts", "POST", "url", parameter),
        ("http-extensions.ts", "PUT", "url", parameter),
        ("links.service.ts", "GET", "link.href", "значение переменной не восстановлено"),
    ]
    assert frontend.meta.stats["calls_resolved"] == 4
    assert frontend.meta.stats["calls_unresolved"] == 9
    # Каждый восстановленный вызов лежит в диапазоне своего узла.
    assert frontend.meta.stats["calls_unattributed"] == 0


def test_calls_through_wrappers_are_invisible(frontend: WebScanResult) -> None:
    """До S18/S19: вызовы через обёртки и `HttpClient.request` не попадают ни в один счётчик.

    Это четыре места продукта (`getApps`, `putApp`, `create`, `follow`) и два
    тела обёрток (`requestVersioned`, `RestService.request`).
    """
    seen = {_name(item.file) for item in [*frontend.calls.calls, *frontend.calls.unresolved]}
    for invisible in ("apps-versioned.service.ts", "apps-proxy.service.ts", "rest.service.ts"):
        assert invisible not in seen
    # У `links.service.ts` виден только `fetch`: `follow` — это `this.http.request`.
    assert [
        item.expression
        for item in frontend.calls.unresolved
        if item.file.endswith("links.service.ts")
    ] == ["link.href"]
    # И у обёрток виден только глагол: тело `requestVersioned` — `http.request`.
    assert sorted(
        item.http_method
        for item in frontend.calls.unresolved
        if item.file.endswith("http-extensions.ts")
    ) == ["DELETE", "GET", "POST", "PUT"]


def test_calls_go_to_the_node_whose_range_holds_them(frontend: WebScanResult) -> None:
    """S16: `AppsService` 5 → 2 вызова, `AppDto` 5 → 0.

    До S16 вызов приписывался каждому узлу файла, и DTO рядом с сервисом
    получал все вызовы сервиса.
    """
    by_title = {node.title: node for node in frontend.manifest.nodes}
    assert [call.member for call in by_title["AppsService"].web_calls] == ["archived", "search"]
    assert by_title["AppDto"].web_calls == []


def test_link(link: LinkReport) -> None:
    assert link.counts == LINK_COUNTS
    assert link.unconfigured_modules == ["seam-web"]

    [duplicate] = link.duplicate_endpoints
    assert (duplicate.http_method, duplicate.route) == ("GET", "info")
    assert [node.rsplit(".", 1)[-1] for node in duplicate.nodes] == [
        "InfoController`0",
        "OrdersController`0",
    ]

    # S16: единственная «связь» (`GET ''` редактора «почти» с `GET {id}`) была
    # выдуманной; вызов редактора невосстановлен, и связей нет ни одной.
    assert link.links == []


def test_conventional_controllers(link: LinkReport) -> None:
    """До S17: шесть «конвенциональных», настоящий из них один — `LegacyController`.

    Две абстрактные базы, `[Route]` без глагола, `AcceptVerbs` и пустой маршрут
    `[HttpGet]` у наследника базы с токеном — всё это сюда попадать не должно.
    """
    assert [node.rsplit(".", 1)[-1] for node in link.conventional_controllers] == [
        "CommentsController`0",
        "LegacyController`0",
        "OrdersController`0",
        "VerbsController`0",
        "ApiController`0",
        "TokenApiController`0",
    ]


def test_cli_chain_runs_on_the_fixture_config(tmp_path: Path) -> None:
    """`scan` → `web scan` → `web link` с настройкой фикстуры: коды возврата и числа.

    Кэш выключен: иначе он лёг бы в `.docpipe/` внутри самой фикстуры.
    """
    back, web, report = tmp_path / "dt.json", tmp_path / "dt.web.json", tmp_path / "link.json"
    common = ["--root", str(SEAM), "--config", str(CONFIG), "--rules", str(RULES), "--no-cache"]

    scanned = runner.invoke(app, ["scan", *common, "--out", str(back)])
    assert scanned.exit_code == 0, scanned.output
    assert "Модулей: 1, узлов: 9." in scanned.output

    web_scanned = runner.invoke(app, ["web", "scan", *common, "--out", str(web)])
    assert web_scanned.exit_code == 0, web_scanned.output
    # S16: «восстановлено 8, не восстановлено 5» → 4 и 9.
    assert "Вызовов: восстановлено 4, не восстановлено 9;" in web_scanned.output

    linked = runner.invoke(
        app,
        ["web", "link", str(back), str(web), "--config", str(CONFIG), "--out", str(report)],
    )
    assert linked.exit_code == 0, linked.output
    counts = json.loads(report.read_text(encoding="utf-8"))["counts"]
    assert counts == LINK_COUNTS
