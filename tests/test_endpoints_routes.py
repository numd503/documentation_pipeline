"""Эндпоинты .NET: наследование `[Route]`, маршрут без глагола, константы (S17).

На squidex префикс `api/` объявлен на абстрактной базе выражением
`[Route(Constants.PrefixApi)]`, и до S17 у всех 256 эндпоинтов его не было:
ни один вызов фронта `api/…` не ложился ни на один эндпоинт, а единственный
«дефект» `web link` — дубль `GET info` у контроллеров с разными базами — был
ложным. Здесь три группы проверок: разбор (`extract_endpoints` на исходниках
в памяти), сопоставление (`web link` на собранных руками манифестах)
и критерии приёмки на фикстуре `SeamWorkspace`.
"""

import shutil
from pathlib import Path
from typing import Final

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig, load_config
from docpipe.dotnet.endpoints import MAX_BASE_DEPTH, extract_endpoints
from docpipe.dotnet.parser import parse_source
from docpipe.dotnet.resolve import build_symbol_index
from docpipe.emit import ScanResult
from docpipe.emit import run as run_dotnet
from docpipe.model import DocNode, Endpoint, Manifest, ParserVersions, Symbol, WebCall
from docpipe.route import RouteKey
from docpipe.web.link import LinkReport, build_report, format_report
from docpipe.web.parser import parse_source as parse_ts
from docpipe.web.tree import run as run_web

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
# Правила — явно, а не ключом `rules` настройки фикстуры: см. `test_seam_fixture.py`.
RULES: Final = Path("rules/rules.yaml")

# Тело контроллера с одним действием `GET x` — общее для половины случаев.
ACTION: Final = '{ [HttpGet("x")] public void M() { } }'


# --------------------------------------------------------------------------------------
# Помощники: индекс из исходников в памяти
# --------------------------------------------------------------------------------------


def _cs(*lines: str, namespace: str = "N", using: str = "") -> str:
    """Файл C#: необязательный `using`, файловый namespace и строки объявлений."""
    head = [f"using {using};"] if using else []
    return "\n".join([*head, f"namespace {namespace};", *lines]) + "\n"


def _index(files: dict[str, str], modules: dict[str, str] | None = None) -> dict[str, Symbol]:
    """Индекс символов из нескольких файлов; модуль по умолчанию у всех один."""
    results = [parse_source(text.encode(), path) for path, text in sorted(files.items())]
    file_to_module = {path: (modules or {}).get(path, "m/M.csproj") for path in files}
    return build_symbol_index(results, file_to_module)


def _endpoints(
    files: dict[str, str], name: str, modules: dict[str, str] | None = None
) -> list[Endpoint]:
    index = _index(files, modules)
    [symbol] = [symbol for symbol in index.values() if symbol.name == name]
    return extract_endpoints(symbol, index)


def _routes(
    files: dict[str, str], name: str, modules: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    return [(item.http_method, item.route) for item in _endpoints(files, name, modules)]


def _constants(*declarations: str, namespace: str = "N", name: str = "K") -> str:
    return _cs(f"public static class {name}", "{", *declarations, "}", namespace=namespace)


BASE_API: Final = _cs(
    "[ApiController]",
    "[Route(Constants.PrefixApi)]",
    "public abstract class ApiController : ControllerBase { }",
    namespace="App.Web",
)
CONSTANTS: Final = _constants(
    'public const string PrefixApi = "/api";', namespace="App.Web", name="Constants"
)
APPS: Final = _cs(
    "public class AppsController : ApiController",
    '{ [HttpGet("apps")] public void L() { } }',
    namespace="App.Controllers",
    using="App.Web",
)


# --------------------------------------------------------------------------------------
# 1. Наследование `[Route]`
# --------------------------------------------------------------------------------------


def test_route_is_inherited_from_the_base_class() -> None:
    files = {
        "Base.cs": _cs('[Route("api")]', "public abstract class Base : ControllerBase { }"),
        "Apps.cs": _cs("public class AppsController : Base", ACTION),
    }
    assert _routes(files, "AppsController") == [("GET", "api/x")]


def test_own_route_cancels_the_base_route() -> None:
    """ASP.NET берёт первый класс иерархии, где маршрут объявлен, — не склеивает."""
    files = {
        "Base.cs": _cs('[Route("api")]', "public abstract class Base : ControllerBase { }"),
        "Content.cs": _cs(
            '[Route("content/{app}")]',
            "public class ContentController : Base",
            '{ [HttpGet("{schema}")] public void L() { } }',
        ),
    }
    assert _routes(files, "ContentController") == [("GET", "content/{app}/{schema}")]


def test_route_of_a_grandparent_is_inherited() -> None:
    files = {
        "A.cs": _cs('[Route("api")]', "public abstract class A : ControllerBase { }"),
        "B.cs": _cs("public abstract class B : A { }"),
        "C.cs": _cs("public class C : B", ACTION),
    }
    assert _routes(files, "C") == [("GET", "api/x")]


def test_controller_token_of_the_base_is_the_name_of_the_derived() -> None:
    """`[Route("api/[controller]")]` на базе даёт `api/Orders`, а не `api/TokenApi`."""
    files = {
        "Base.cs": _cs(
            '[Route("api/[controller]/[action]")]',
            "public abstract class TokenApiController : ControllerBase { }",
        ),
        "Orders.cs": _cs(
            "public class OrdersController : TokenApiController",
            "{ [HttpGet] public void ListAsync() { } }",
        ),
    }
    assert _routes(files, "OrdersController") == [("GET", "api/Orders/List")]


def test_interface_in_the_base_list_is_not_the_base_class() -> None:
    """`base_types` отсортированы, и интерфейс встаёт раньше базы; маршрут у него не берётся.

    `IAlpha` сортируется раньше `Zeta`, а `[Route]` на интерфейсе ASP.NET
    не наследует вовсе.
    """
    files = {
        "I.cs": _cs('[Route("iface")]', "public interface IAlpha { }"),
        "Z.cs": _cs('[Route("zeta")]', "public abstract class Zeta : ControllerBase { }"),
        "C.cs": _cs("public class C : IAlpha, Zeta", ACTION),
    }
    assert _routes(files, "C") == [("GET", "zeta/x")]


def test_base_in_another_module_is_found() -> None:
    files = {
        "web/Base.cs": _cs(
            '[Route("api")]', "public abstract class Base : ControllerBase { }", namespace="W"
        ),
        "app/C.cs": _cs("public class C : Base", ACTION, namespace="A", using="W"),
    }
    modules = {"web/Base.cs": "web/Web.csproj", "app/C.cs": "app/App.csproj"}
    assert _routes(files, "C", modules) == [("GET", "api/x")]


def test_external_base_gives_no_route_and_no_error() -> None:
    assert _routes({"C.cs": _cs("public class C : ControllerBase", ACTION)}, "C") == [("GET", "x")]


def test_cycle_in_the_hierarchy_does_not_hang() -> None:
    """`A : B`, `B : A` в C# невозможны, но из битого кода резолв их соберёт."""
    files = {
        "A.cs": _cs("public class A : B", ACTION),
        "B.cs": _cs("public class B : A { }"),
    }
    assert _routes(files, "A") == [("GET", "x")]


def _chain(length: int) -> dict[str, str]:
    """`C0 : C1 : … : C{length}`; `[Route]` — только у последнего."""
    files = {
        f"C{position}.cs": _cs(f"public class C{position} : C{position + 1} {{ }}")
        for position in range(1, length)
    }
    files["C0.cs"] = _cs("public class C0 : C1", ACTION)
    files[f"C{length}.cs"] = _cs('[Route("deep")]', f"public class C{length} {{ }}")
    return files


def test_route_is_searched_at_most_ten_bases_up() -> None:
    assert _routes(_chain(MAX_BASE_DEPTH), "C0") == [("GET", "deep/x")]
    assert _routes(_chain(MAX_BASE_DEPTH + 1), "C0") == [("GET", "x")]


def test_actions_of_the_base_are_not_inherited() -> None:
    """Наследуется только маршрут (раздел плана «Что намеренно не входит»)."""
    files = {
        "Base.cs": _cs(
            '[Route("api")]',
            "public abstract class Base : ControllerBase",
            '{ [HttpGet("ping")] public void Ping() { } }',
        ),
        "C.cs": _cs("public class C : Base", ACTION),
    }
    assert _routes(files, "C") == [("GET", "api/x")]
    assert _routes(files, "Base") == [("GET", "api/ping")]


# --------------------------------------------------------------------------------------
# 2. Аргумент маршрута — выражение
# --------------------------------------------------------------------------------------


def test_parser_marks_expression_arguments() -> None:
    source = _cs(
        "[Route(Constants.PrefixApi)]",
        '[Route("Constants.PrefixApi")]',
        '[AcceptVerbs("GET", Route = Routes.Echo, Name = "echo")]',
        "public class C { }",
    )
    [declaration] = parse_source(source.encode(), "x.cs").declarations
    flags = [
        (item.name, item.args, item.expression_args, item.expression_named_args)
        for item in declaration.attributes
    ]
    assert flags == [
        ("Route", ["Constants.PrefixApi"], [0], []),
        ("Route", ["Constants.PrefixApi"], [], []),
        ("AcceptVerbs", ["GET"], [], ["Route"]),
    ]


def test_web_decorator_marks_expression_arguments() -> None:
    """Модель общая: аргумент-выражение декоратора помечается так же."""
    source = b"""
@State({ name: 'debts' })
export class DebtsState {
  @Action(LoadDebts)
  load() {}
}
"""
    [declaration] = parse_ts(source, "a.ts").declarations
    [member] = [item for item in declaration.members if item.name == "load"]
    [action] = member.attributes
    assert (action.args, action.expression_args) == (["LoadDebts"], [0])
    [state] = declaration.attributes
    assert state.expression_args == []


def test_constant_of_the_base_route_is_resolved() -> None:
    files = {"Web/ApiController.cs": BASE_API, "Web/Constants.cs": CONSTANTS, "Apps.cs": APPS}
    # Ведущий `/` константы (`"/api"` у squidex) склейке не мешает.
    assert _routes(files, "AppsController") == [("GET", "api/apps")]


@pytest.mark.parametrize(
    "expression", ["N.Web.Constants.PrefixApi", "global::N.Web.Constants.PrefixApi"]
)
def test_qualified_constant_is_resolved(expression: str) -> None:
    files = {
        "K.cs": _constants(
            'public const string PrefixApi = "api";', namespace="N.Web", name="Constants"
        ),
        "C.cs": _cs(f"[Route({expression})]", "public class C", ACTION, namespace="Other"),
    }
    assert _routes(files, "C") == [("GET", "api/x")]


def test_constant_of_the_enclosing_namespace_wins() -> None:
    """Как в C#: охватывающий namespace ищется раньше всего остального."""
    files = {
        "Near.cs": _constants('public const string P = "near";', namespace="App"),
        "Far.cs": _constants('public const string P = "far";', namespace="Lib"),
        "C.cs": _cs("[Route(K.P)]", "public class C", ACTION, namespace="App.Api"),
    }
    assert _routes(files, "C") == [("GET", "near/x")]


def test_constant_with_two_different_values_is_ambiguous() -> None:
    """Без `using` выбрать из двух нельзя — угаданный маршрут хуже пустого."""
    files = {
        "A.cs": _constants('public const string P = "a";', namespace="A"),
        "B.cs": _constants('public const string P = "b";', namespace="B"),
        "C.cs": _cs("[Route(K.P)]", "public class Ctl", ACTION, namespace="C"),
    }
    [endpoint] = _endpoints(files, "Ctl")
    assert endpoint.route == ""
    assert endpoint.unresolved == "аргумент маршрута — выражение `K.P`: неоднозначно, значений 2"


def test_same_value_under_two_types_is_not_ambiguous() -> None:
    files = {
        "A.cs": _constants('public const string P = "api";', namespace="A"),
        "B.cs": _constants('public const string P = "api";', namespace="B"),
        "C.cs": _cs("[Route(K.P)]", "public class Ctl", ACTION, namespace="C"),
    }
    assert _routes(files, "Ctl") == [("GET", "api/x")]


def test_type_of_the_own_module_wins_over_a_foreign_one() -> None:
    """Одно FQN в двух сборках: своя перекрывает чужую, как у базовых типов."""
    files = {
        "own/K.cs": _constants('public const string P = "own";', namespace="Lib"),
        "lib/K.cs": _constants('public const string P = "lib";', namespace="Lib"),
        "own/C.cs": _cs("[Route(Lib.K.P)]", "public class C", ACTION, namespace="App"),
    }
    modules = {
        "own/K.cs": "own/Own.csproj",
        "lib/K.cs": "lib/Lib.csproj",
        "own/C.cs": "own/Own.csproj",
    }
    assert _routes(files, "C", modules) == [("GET", "own/x")]


def test_unresolved_constant_gives_an_empty_route_and_a_reason() -> None:
    """Не выдуманный путь `Constants.Missing/x`, а пустой маршрут с причиной."""
    files = {"C.cs": _cs("[Route(Constants.Missing)]", "public class C", ACTION)}
    [endpoint] = _endpoints(files, "C")
    assert (endpoint.http_method, endpoint.route, endpoint.member) == ("GET", "", "M")
    assert endpoint.unresolved == (
        "аргумент маршрута — выражение `Constants.Missing`: константа не найдена"
    )


@pytest.mark.parametrize(
    ("declaration", "reason"),
    [
        ('public const string P = Base + "/x";', "значение константы — не литерал"),
        ('public static readonly string P = "api";', "константа не найдена"),
        ('public const string P = "a\\\\b";', "значение константы — не литерал"),
    ],
)
def test_constant_without_a_literal_is_not_resolved(declaration: str, reason: str) -> None:
    files = {
        "K.cs": _constants('public const string Base = "api";', declaration),
        "C.cs": _cs("[Route(K.P)]", "public class C", ACTION),
    }
    [endpoint] = _endpoints(files, "C")
    assert endpoint.route == ""
    assert endpoint.unresolved == f"аргумент маршрута — выражение `K.P`: {reason}"


@pytest.mark.parametrize(
    ("declaration", "route"),
    [
        ('public const string P = @"api/v""1";', 'api/v"1/x'),
        ('public const string A = "a", P = "b";', "b/x"),
        ('internal const string P = "api" ;', "api/x"),
    ],
)
def test_literal_forms_of_a_constant(declaration: str, route: str) -> None:
    files = {"K.cs": _constants(declaration), "C.cs": _cs("[Route(K.P)]", "public class C", ACTION)}
    assert _routes(files, "C") == [("GET", route)]


@pytest.mark.parametrize("expression", ["nameof(C)", '$"{K.P}/v1"', 'K.P + "/v1"', "P"])
def test_other_expressions_are_not_guessed(expression: str) -> None:
    files = {
        "K.cs": _constants('public const string P = "api";'),
        "C.cs": _cs(f"[Route({expression})]", "public class C", ACTION),
    }
    [endpoint] = _endpoints(files, "C")
    assert endpoint.route == ""
    assert endpoint.unresolved == f"аргумент маршрута — выражение `{expression}`"


def test_constant_in_the_member_template_is_resolved() -> None:
    files = {
        "K.cs": _constants('public const string Get = "items/{id}";', name="Routes"),
        "C.cs": _cs(
            '[Route("api")]', "public class C", "{ [HttpGet(Routes.Get)] public void M() { } }"
        ),
    }
    assert _routes(files, "C") == [("GET", "api/items/{id}")]


def test_absolute_member_template_does_not_need_the_base() -> None:
    """Абсолютный шаблон отбрасывает базу — и её невосстановленность тоже."""
    files = {
        "C.cs": _cs(
            "[Route(Constants.Missing)]",
            "public class C",
            '{ [HttpGet("/health")] public void H() { } [HttpGet("x")] public void M() { } }',
        )
    }
    assert [(e.route, bool(e.unresolved)) for e in _endpoints(files, "C")] == [
        ("", True),
        ("health", False),
    ]


def test_literal_that_looks_like_an_expression_stays_a_literal() -> None:
    files = {
        "K.cs": _constants('public const string PrefixApi = "api";', name="Constants"),
        "C.cs": _cs('[Route("Constants.PrefixApi")]', "public class C", ACTION),
    }
    assert _routes(files, "C") == [("GET", "Constants.PrefixApi/x")]


def test_constant_is_resolved_from_the_class_that_declares_the_attribute() -> None:
    """Выражение базы видно из namespace базы, а не наследника."""
    files = {
        "Web/K.cs": _constants('public const string P = "web";', namespace="Web"),
        "Other/K.cs": _constants('public const string P = "other";', namespace="Other"),
        "Web/Base.cs": _cs(
            "[Route(K.P)]", "public abstract class Base : ControllerBase { }", namespace="Web"
        ),
        "Other/C.cs": _cs("public class C : Base", ACTION, namespace="Other", using="Web"),
    }
    assert _routes(files, "C") == [("GET", "web/x")]


# --------------------------------------------------------------------------------------
# 3. `[Route]` без глагола; 4. `AcceptVerbs`
# --------------------------------------------------------------------------------------


def test_route_without_a_verb_accepts_any_method() -> None:
    files = {
        "C.cs": _cs(
            '[Route("api")]', "public class C", '{ [Route("comments/{id}")] public void M() { } }'
        )
    }
    assert _routes(files, "C") == [("*", "api/comments/{id}")]


def test_route_with_a_verb_is_not_any_method() -> None:
    files = {"C.cs": _cs("public class C", '{ [HttpGet] [Route("x")] public void M() { } }')}
    assert _routes(files, "C") == [("GET", "x")]


def test_accept_verbs_gives_an_endpoint_per_verb() -> None:
    files = {
        "C.cs": _cs(
            '[Route("api/verbs")]',
            "public class C",
            '{ [AcceptVerbs("post", "GET", "POST")] public void Echo() { } }',
        )
    }
    assert _routes(files, "C") == [("GET", "api/verbs"), ("POST", "api/verbs")]


def test_accept_verbs_takes_its_own_route_then_the_member_route() -> None:
    files = {
        "C.cs": _cs(
            '[Route("api")]',
            "public class C",
            "{",
            '    [AcceptVerbs("GET", Route = "own")] [Route("ignored")] public void Own() { }',
            '    [AcceptVerbs("PUT")] [Route("member")] public void Member() { }',
            "}",
        )
    }
    assert _routes(files, "C") == [("PUT", "api/member"), ("GET", "api/own")]


def test_accept_verbs_with_an_expression_verb_is_not_guessed() -> None:
    files = {
        "C.cs": _cs(
            '[Route("api")]',
            "public class C",
            '{ [AcceptVerbs(HttpMethods.Get, "POST")] public void M() { } }',
        )
    }
    assert [(e.http_method, e.route, e.unresolved) for e in _endpoints(files, "C")] == [
        ("*", "", "глагол `AcceptVerbs` — выражение `HttpMethods.Get`"),
        ("POST", "api", ""),
    ]


# --------------------------------------------------------------------------------------
# `web link`: метод `*`, невосстановленные, конвенциональные
# --------------------------------------------------------------------------------------


PARSER: Final = ParserVersions(tree_sitter="0.25.0")


def _node(name: str, kind: str = "controller", **fields: object) -> DocNode:
    return DocNode.model_validate(
        {
            "id": f"type:{name}",
            "kind": kind,
            "template": kind,
            "title": name,
            "doc_path": f"docs/{name}.md",
            "module": "m",
            "domain": "m",
            "signature_hash": "x",
            **fields,
        }
    )


def _call(http_method: str, route: str) -> WebCall:
    return WebCall(
        file="a.ts", line=1, key=RouteKey(http_method=http_method, route=route), confidence="high"
    )


def _link(endpoints: dict[str, list[Endpoint]], calls: list[WebCall]) -> LinkReport:
    backend = Manifest(
        ruleset_version="1",
        parser=PARSER,
        nodes=[_node(name, endpoints=items) for name, items in sorted(endpoints.items())],
    )
    web = Manifest(
        ruleset_version="1", parser=PARSER, nodes=[_node("Page", "page", web_calls=calls)]
    )
    return build_report(backend, web)


def _endpoint(http_method: str, route: str, unresolved: str = "") -> Endpoint:
    return Endpoint(http_method=http_method, route=route, member="M", line=1, unresolved=unresolved)


@pytest.mark.parametrize("http_method", ["GET", "POST", "DELETE"])
def test_any_method_endpoint_links_any_call_exactly(http_method: str) -> None:
    report = _link(
        {"Comments": [_endpoint("*", "api/comments/{id}")]}, [_call(http_method, "api/comments/{}")]
    )
    assert [(item.http_method, item.match, item.endpoints) for item in report.links] == [
        (http_method, "exact", ["type:Comments"])
    ]
    assert report.endpoints_without_caller == []


def test_any_method_endpoint_links_almost() -> None:
    report = _link(
        {"Comments": [_endpoint("*", "api/comments/{id}")]}, [_call("GET", "api/comments")]
    )
    assert [(item.match, item.endpoints) for item in report.links] == [
        ("almost", ["type:Comments"])
    ]


def test_call_reaches_both_its_verb_and_any_method() -> None:
    report = _link(
        {"A": [_endpoint("GET", "api/x")], "B": [_endpoint("*", "api/x")]}, [_call("GET", "api/x")]
    )
    [link] = report.links
    assert (link.match, link.endpoints) == ("exact", ["type:A", "type:B"])
    assert report.endpoints_without_caller == []


def test_any_method_and_a_verb_are_not_a_duplicate() -> None:
    """Разные ключи: ASP.NET выберет действие с методом, коллизии нет."""
    report = _link({"A": [_endpoint("GET", "api/x")], "B": [_endpoint("*", "api/x")]}, [])
    assert report.duplicate_endpoints == []


def test_unresolved_endpoint_is_named_and_counted() -> None:
    """Пустой маршрут в ключи не идёт; без списка эндпоинт пропал бы молча."""
    reason = "аргумент маршрута — выражение `Constants.Missing`: константа не найдена"
    report = _link({"Apps": [_endpoint("GET", "", reason)]}, [_call("GET", "api/apps")])

    assert [(item.node, item.member, item.reason) for item in report.unresolved_endpoints] == [
        ("type:Apps", "M", reason)
    ]
    assert report.counts["endpoints_unresolved"] == 1
    assert report.counts["endpoints_total"] == 0
    assert report.conventional_controllers == []
    text = format_report(report)
    assert "Эндпоинтов с невосстановленным маршрутом: 1." in text
    assert reason in text


def _scan(tmp_path: Path, files: dict[str, str]) -> ScanResult:
    (tmp_path / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk.Web" />\n', "utf-8")
    for path, text in files.items():
        (tmp_path / path).write_text(text, encoding="utf-8")
    return run_dotnet(tmp_path, DocpipeConfig(), load_ruleset(RULES, "dotnet"))


def test_only_real_conventional_controllers_are_named(tmp_path: Path) -> None:
    """Не абстрактный, есть публичный метод, маршрут из атрибутов не собирается.

    Голый `[HttpGet]` шаблона не задаёт: действие под ним в ASP.NET остаётся
    конвенциональным с ограничением по методу, поэтому `VerbController` здесь.
    """
    view = "public IActionResult Index() => View();"
    result = _scan(
        tmp_path,
        {
            "Base.cs": _cs(
                "[ApiController]",
                '[Route("api")]',
                "public abstract class ApiBase : ControllerBase { }",
            ),
            "Home.cs": _cs("public class HomeController : Controller", f"{{ {view} }}"),
            "Verb.cs": _cs("public class VerbController : Controller", f"{{ [HttpGet] {view} }}"),
            "Helpers.cs": _cs(
                "public class HelpersController : Controller", "{ protected void Help() { } }"
            ),
            "Abstract.cs": _cs(
                "public abstract class AbstractController : Controller", "{ public void Act() { } }"
            ),
            "Inherits.cs": _cs(
                "public class InheritsController : ApiBase", "{ [HttpGet] public void List() { } }"
            ),
            "Comments.cs": _cs(
                '[Route("api")]',
                "public class CommentsController : ControllerBase",
                '{ [Route("c")] public void C() { } }',
            ),
            "Own.cs": _cs(
                '[Route("own")]',
                "public class OwnController : ControllerBase",
                "{ public void Act() { } }",
            ),
        },
    )
    empty = Manifest(ruleset_version="1", parser=PARSER)
    report = build_report(result.manifest, empty)
    assert [node.rsplit(".", 1)[-1] for node in report.conventional_controllers] == [
        "HomeController`0",
        "VerbController`0",
    ]


# --------------------------------------------------------------------------------------
# Критерии приёмки на `SeamWorkspace`
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def backend(tmp_path_factory: pytest.TempPathFactory) -> ScanResult:
    cache = tmp_path_factory.mktemp("cache-dotnet")
    config = load_config(SEAM / "docpipe.yaml")
    return run_dotnet(SEAM, config, load_ruleset(RULES, "dotnet"), cache)


@pytest.fixture(scope="module")
def link(backend: ScanResult, tmp_path_factory: pytest.TempPathFactory) -> LinkReport:
    cache = tmp_path_factory.mktemp("cache-web")
    frontend = run_web(SEAM, load_config(SEAM / "docpipe.yaml"), load_ruleset(RULES, "web"), cache)
    return build_report(backend.manifest, frontend.manifest)


def _seam_routes(backend: ScanResult, title: str) -> list[tuple[str, str]]:
    [node] = [node for node in backend.manifest.nodes if node.title == title]
    return [(item.http_method, item.route) for item in node.endpoints]


def test_apps_controller_gets_the_prefix_of_its_base(backend: ScanResult) -> None:
    assert _seam_routes(backend, "AppsController") == [
        ("GET", "api/apps"),
        ("POST", "api/apps"),
        ("GET", "api/apps/{app}"),
        ("PUT", "api/apps/{app}"),
    ]


def test_orders_controller_gets_the_token_route_of_its_base(backend: ScanResult) -> None:
    assert _seam_routes(backend, "OrdersController") == [
        ("GET", "api/Orders"),
        ("GET", "api/Orders/info"),
        ("GET", "api/Orders/{id}"),
    ]


def test_comments_and_verbs(backend: ScanResult) -> None:
    assert _seam_routes(backend, "CommentsController") == [("*", "api/comments/{id}")]
    assert _seam_routes(backend, "VerbsController") == [("GET", "api/verbs"), ("POST", "api/verbs")]


def test_prefix_constant_is_resolved(backend: ScanResult) -> None:
    [base] = [node for node in backend.manifest.nodes if node.title == "ApiController"]
    assert base.symbol is not None
    [route] = [item for item in base.symbol.attributes if item.name == "Route"]
    assert (route.args, route.expression_args) == (["Constants.PrefixApi"], [0])
    assert _seam_routes(backend, "InfoController") == [("GET", "api/info")]


def test_unresolvable_constant_on_the_fixture(tmp_path: Path) -> None:
    """Фикстура без `Constants.cs`: пустой маршрут и причина, а не `Constants.PrefixApi/apps`."""
    copy = tmp_path / "seam"
    shutil.copytree(SEAM / "backend", copy / "backend")
    (copy / "backend/Seam.Api/Web/Constants.cs").unlink()
    result = run_dotnet(copy, DocpipeConfig(roots=["backend"]), load_ruleset(RULES, "dotnet"))

    [apps] = [node for node in result.manifest.nodes if node.title == "AppsController"]
    assert {(item.route, item.unresolved) for item in apps.endpoints} == {
        ("", "аргумент маршрута — выражение `Constants.PrefixApi`: константа не найдена")
    }


def test_conventional_controllers_hold_no_base(link: LinkReport) -> None:
    assert [node.rsplit(".", 1)[-1] for node in link.conventional_controllers] == [
        "LegacyController`0"
    ]


def test_one_relative_route_under_two_bases_is_not_a_duplicate(link: LinkReport) -> None:
    assert link.duplicate_endpoints == []
    assert "api/orders/info" in {item.route for item in link.endpoints_without_caller}
    assert [item.route for item in link.links] == ["api/info"]


def test_warm_cache_keeps_the_expression_flags(tmp_path: Path) -> None:
    """Флаг выражения проходит через кэш: тёплый прогон даёт тот же манифест.

    Иначе запись кэша вернула бы `Constants.PrefixApi` литералом, и маршрут
    стал бы `Constants.PrefixApi/apps` — ровно то, что S17 убирает.
    """
    config = load_config(SEAM / "docpipe.yaml")
    rules = load_ruleset(RULES, "dotnet")
    cold = run_dotnet(SEAM, config, rules, tmp_path / "cache").manifest
    warm = run_dotnet(SEAM, config, rules, tmp_path / "cache").manifest
    assert warm == cold
    [apps] = [node for node in warm.nodes if node.title == "AppsController"]
    assert ("GET", "api/apps") in [(item.http_method, item.route) for item in apps.endpoints]


def test_scoped_run_still_sees_the_base_outside_the_scope(tmp_path: Path) -> None:
    """Скоуп по проекту контроллеров: база и константа — в другом проекте, из кэша.

    Скоуп — каталог проекта (`--scope src/Cf.Pricing.Api`): файлы вне него
    приезжают из кэша, и без них в индексе не было бы ни базы, ни константы.
    """
    web, api = tmp_path / "Web", tmp_path / "Api"
    for directory in (web, api):
        directory.mkdir()
        (directory / f"{directory.name}.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk.Web" />\n', encoding="utf-8"
        )
    (web / "ApiController.cs").write_text(BASE_API, encoding="utf-8")
    (web / "Constants.cs").write_text(CONSTANTS, encoding="utf-8")
    (api / "AppsController.cs").write_text(APPS, encoding="utf-8")

    rules = load_ruleset(RULES, "dotnet")
    cache = tmp_path / ".cache"
    full = run_dotnet(tmp_path, DocpipeConfig(), rules, cache).manifest
    scoped = run_dotnet(tmp_path, DocpipeConfig(), rules, cache, scope=["Api"], previous=full)

    [apps] = [node for node in scoped.manifest.nodes if node.title == "AppsController"]
    assert [(item.http_method, item.route) for item in apps.endpoints] == [("GET", "api/apps")]
    assert scoped.meta.stats["restored_from_cache"] == 2
