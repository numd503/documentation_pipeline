"""HTTP-маршруты контроллеров из атрибутов.

Здесь только атрибутная маршрутизация. Конвенциональная (`MapControllerRoute`
с шаблоном `{controller}/{action}`) и minimal API (`app.MapGet(...)`) не
разбираются: первая задаётся не на типе, а в конфигурации приложения, вторая
вообще не привязана к типу и документируется не как эндпоинт контроллера.
"""

import re

from docpipe.model import Attribute, Endpoint, Member, Symbol

# Атрибут -> HTTP-метод. Имя атрибута без префикса `Http` в верхнем регистре,
# но список явный: так видно, что именно поддерживается.
_HTTP_METHODS = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
    "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS",
}

_SLASHES = re.compile(r"/{2,}")
# Токены маршрутизации нечувствительны к регистру: `[Controller]` и `[controller]`
# для ASP.NET одно и то же.
_CONTROLLER_TOKEN = re.compile(r"\[controller\]", re.IGNORECASE)
_ACTION_TOKEN = re.compile(r"\[action\]", re.IGNORECASE)


def _first_argument(attributes: list[Attribute], name: str) -> str | None:
    """Первый позиционный аргумент атрибута с данным именем."""
    for attribute in attributes:
        if attribute.name == name:
            return attribute.args[0] if attribute.args else ""
    return None


def _member_template(member: Member, http_attribute: Attribute) -> str:
    """Шаблон маршрута метода.

    Аргумент самого `Http*`, а при его отсутствии — отдельный атрибут `[Route]`
    на том же члене:

        [HttpGet]
        [Route("tenants/by-name/{name}")]
        public Task<TenantDto> FindTenantByNameAsync(string name)

    Это не экзотика, а **преобладающая** форма: в ABP так написаны 245 из 356
    HTTP-атрибутов. Реализация, читающая только аргумент `Http*`, выдала бы
    всем методам такого контроллера один и тот же маршрут — маршрут типа.
    """
    if http_attribute.args:
        return http_attribute.args[0]
    return _first_argument(member.attributes, "Route") or ""


def _combine(base: str, template: str) -> str:
    """Склеить шаблон типа и шаблон метода.

    Шаблон, начинающийся с `/` или `~/`, абсолютный — база отбрасывается.
    """
    if template.startswith("~/"):
        combined = template[2:]
    elif template.startswith("/"):
        combined = template[1:]
    else:
        combined = f"{base}/{template}"

    return _SLASHES.sub("/", combined).strip("/")


def _substitute(route: str, type_name: str, member_name: str) -> str:
    """Подставить `[controller]` и `[action]`.

    Значения берутся с исходным регистром имени: `PricingController` даёт
    `Pricing`, а не `pricing`. ASP.NET подставляет их так же.
    """
    route = _CONTROLLER_TOKEN.sub(type_name.removesuffix("Controller"), route)
    return _ACTION_TOKEN.sub(member_name.removesuffix("Async"), route)


def extract_endpoints(symbol: Symbol) -> list[Endpoint]:
    """HTTP-эндпоинты типа. Для не-контроллера — пустой список."""
    base = _first_argument(symbol.attributes, "Route") or ""

    found: list[Endpoint] = []
    for member in symbol.members:
        for attribute in member.attributes:
            http_method = _HTTP_METHODS.get(attribute.name)
            if http_method is None:
                continue

            route = _combine(base, _member_template(member, attribute))
            found.append(
                Endpoint(
                    http_method=http_method,
                    route=_substitute(route, symbol.name, member.name),
                    member=member.name,
                    line=member.line,
                )
            )

    found.sort(key=lambda endpoint: (endpoint.route, endpoint.http_method, endpoint.member))
    return found
