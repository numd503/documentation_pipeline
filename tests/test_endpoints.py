"""Проверка извлечения HTTP-маршрутов (T12)."""

from pathlib import Path

from docpipe.dotnet.endpoints import extract_endpoints
from docpipe.dotnet.parser import parse_source
from docpipe.dotnet.resolve import build_symbol_index
from docpipe.model import Endpoint, Symbol
from tests.conftest import by_fqn, index_of


def _symbol(source: bytes) -> Symbol:
    """Единственный тип из исходника."""
    result = parse_source(source, "x.cs")
    index = build_symbol_index([result], {"x.cs": "m/M.csproj"})
    return next(iter(index.values()))


def _routes(source: bytes) -> list[tuple[str, str]]:
    return [(e.http_method, e.route) for e in extract_endpoints(_symbol(source))]


# --------------------------------------------------------------------------------------
# Критерии приёмки на фикстуре
# --------------------------------------------------------------------------------------


def test_controller_endpoints(sample_solution: Path) -> None:
    index = by_fqn(index_of(sample_solution))
    endpoints = extract_endpoints(index["Sample.Pricing.Api.Controllers.PricingController"])

    assert endpoints == [
        Endpoint(http_method="POST", route="api/v1/Pricing", member="RecalculateAsync", line=24),
        Endpoint(http_method="GET", route="api/v1/Pricing/{id:guid}", member="GetAsync", line=18),
    ]


def test_non_controller_has_no_endpoints(sample_solution: Path) -> None:
    index = by_fqn(index_of(sample_solution))
    assert extract_endpoints(index["Sample.Pricing.Api.Services.PricingService"]) == []


# --------------------------------------------------------------------------------------
# Форма, преобладающая в реальном коде (находка на ABP)
# --------------------------------------------------------------------------------------


def test_route_attribute_on_member_is_used() -> None:
    """`[HttpGet]` без аргумента плюс отдельный `[Route]` на том же методе.

    В ABP так написаны **245 из 356** HTTP-атрибутов. Реализация, читающая
    только аргумент `Http*`, выдала бы всем методам такого контроллера один
    и тот же маршрут — маршрут типа.
    """
    source = b"""namespace N;
[Route("api/abp/multi-tenancy")]
public class AbpTenantController
{
    [HttpGet]
    [Route("tenants/by-name/{name}")]
    public Task<TenantDto> FindTenantByNameAsync(string name) { }

    [HttpGet]
    [Route("tenants/by-id/{id}")]
    public Task<TenantDto> FindTenantByIdAsync(Guid id) { }
}
"""
    assert _routes(source) == [
        ("GET", "api/abp/multi-tenancy/tenants/by-id/{id}"),
        ("GET", "api/abp/multi-tenancy/tenants/by-name/{name}"),
    ]


def test_http_attribute_argument_wins_over_member_route() -> None:
    source = b"""namespace N;
[Route("api")]
public class C
{
    [HttpGet("from-http")]
    [Route("from-route")]
    public void M() { }
}
"""
    assert _routes(source) == [("GET", "api/from-http")]


def test_member_without_any_template_falls_back_to_type_route() -> None:
    source = b"""namespace N;
[Route("api/values")]
public class C
{
    [HttpGet]
    public void M() { }
}
"""
    assert _routes(source) == [("GET", "api/values")]


def test_conventional_routing_yields_empty_route() -> None:
    """Контроллер без `[Route]` и метод без шаблона — маршрут задаётся конвенцией.

    Атрибутов для него нет, поэтому путь неизвестен. Эндпоинт всё равно
    выдаётся: то, что метод обрабатывает GET, — факт, и он полезен.
    В ABP таких 10 из 356.
    """
    source = b"namespace N;\npublic class C { [HttpGet] public void M() { } }\n"
    assert _routes(source) == [("GET", "")]


# --------------------------------------------------------------------------------------
# Склейка шаблонов
# --------------------------------------------------------------------------------------


def test_absolute_template_discards_base() -> None:
    source = b"""namespace N;
[Route("api/values")]
public class C
{
    [HttpGet("/health")]
    public void Slash() { }

    [HttpGet("~/root")]
    public void Tilde() { }
}
"""
    assert _routes(source) == [("GET", "health"), ("GET", "root")]


def test_slashes_are_collapsed_and_trimmed() -> None:
    source = b"""namespace N;
[Route("/api/values/")]
public class C
{
    [HttpGet("//sub///path/")]
    public void M() { }
}
"""
    # Ведущий слэш делает шаблон метода абсолютным, база отбрасывается.
    assert _routes(source) == [("GET", "sub/path")]


def test_empty_type_route() -> None:
    source = b'namespace N;\n[Route("")]\npublic class C { [HttpGet("x")] public void M() { } }\n'
    assert _routes(source) == [("GET", "x")]


def test_no_type_route_leaves_only_member_template() -> None:
    source = b'namespace N;\npublic class C { [HttpGet("items/{id}")] public void M() { } }\n'
    assert _routes(source) == [("GET", "items/{id}")]


# --------------------------------------------------------------------------------------
# Подстановки
# --------------------------------------------------------------------------------------


def test_controller_and_action_tokens() -> None:
    source = b"""namespace N;
[Route("api/[controller]/[action]")]
public class ManageController
{
    [HttpGet]
    public void ChangePasswordAsync() { }
}
"""
    assert _routes(source) == [("GET", "api/Manage/ChangePassword")]


def test_tokens_are_case_insensitive() -> None:
    """ASP.NET не различает регистр токенов; значение подставляется как в имени."""
    source = b"""namespace N;
[Route("[Controller]/[ACTION]")]
public class PricingController
{
    [HttpGet]
    public void GetAsync() { }
}
"""
    assert _routes(source) == [("GET", "Pricing/Get")]


def test_type_without_controller_suffix_keeps_its_name() -> None:
    source = (
        b'namespace N;\n[Route("[controller]")]\n'
        b"public class Pricing { [HttpGet] public void M() { } }\n"
    )
    assert _routes(source) == [("GET", "Pricing")]


# --------------------------------------------------------------------------------------
# Виды атрибутов и общие свойства
# --------------------------------------------------------------------------------------


def test_all_http_verbs() -> None:
    source = b"""namespace N;
public class C
{
    [HttpGet("g")] public void G() { }
    [HttpPost("p")] public void P() { }
    [HttpPut("u")] public void U() { }
    [HttpDelete("d")] public void D() { }
    [HttpPatch("a")] public void A() { }
    [HttpHead("h")] public void H() { }
    [HttpOptions("o")] public void O() { }
}
"""
    assert {method for method, _ in _routes(source)} == {
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "PATCH",
        "HEAD",
        "OPTIONS",
    }


def test_member_with_two_http_attributes_gives_two_endpoints() -> None:
    """Метод, обрабатывающий и GET, и POST, — два эндпоинта. В ABP таких три."""
    source = b"""namespace N;
public class C
{
    [HttpGet("x")]
    [HttpPost("x")]
    public void M() { }
}
"""
    assert _routes(source) == [("GET", "x"), ("POST", "x")]


def test_route_only_member_is_skipped() -> None:
    """`[Route]` без `Http*` — маршрут без ограничения по методу; глагола нет."""
    source = b'namespace N;\npublic class C { [Route("x")] public void M() { } }\n'
    assert _routes(source) == []


def test_non_http_attributes_are_ignored() -> None:
    source = b"""namespace N;
public class C
{
    [Authorize]
    [Obsolete("old")]
    [HttpGet("x")]
    public void M() { }
}
"""
    assert _routes(source) == [("GET", "x")]


def test_endpoints_are_sorted_by_route_then_method() -> None:
    source = b"""namespace N;
public class C
{
    [HttpPost("b")] public void Two() { }
    [HttpGet("a")] public void One() { }
    [HttpDelete("a")] public void Three() { }
}
"""
    assert _routes(source) == [("DELETE", "a"), ("GET", "a"), ("POST", "b")]


def test_member_line_is_recorded() -> None:
    source = b'namespace N;\npublic class C\n{\n    [HttpGet("x")]\n    public void M() { }\n}\n'
    endpoint = extract_endpoints(_symbol(source))[0]
    assert endpoint.member == "M"
    assert endpoint.line == 4  # строка с атрибутом: он потомок объявления члена


def test_extraction_is_deterministic(sample_solution: Path) -> None:
    symbol = by_fqn(index_of(sample_solution))["Sample.Pricing.Api.Controllers.PricingController"]
    assert extract_endpoints(symbol) == extract_endpoints(symbol)
