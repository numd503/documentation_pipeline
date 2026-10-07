"""HTTP-маршруты контроллеров из атрибутов.

Здесь только атрибутная маршрутизация. Конвенциональная (`MapControllerRoute`
с шаблоном `{controller}/{action}`) и minimal API (`app.MapGet(...)`) не
разбираются: первая задаётся не на типе, а в конфигурации приложения, вторая
вообще не привязана к типу и документируется не как эндпоинт контроллера.

Маршрут собирается из трёх независимых частей, и ни одна не обязательна:
шаблон типа (свой `[Route]` либо унаследованный от базового класса), глагол
(`Http*`, `AcceptVerbs` или ничего — тогда `*`) и шаблон действия. Аргумент
любой из них бывает выражением (`Constants.PrefixApi`); тогда он разрешается
по индексу в литерал константы, а не разрешился — эндпоинт получает пустой
маршрут и причину в `unresolved`, а не путь `Constants.PrefixApi/apps`.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from docpipe.dotnet.resolve import base_symbol_key, index_by_fqn
from docpipe.model import Attribute, Endpoint, Member, Symbol
from docpipe.symbols import symbol_key

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

ROUTE_ATTRIBUTE: Final = "Route"
ACCEPT_VERBS: Final = "AcceptVerbs"

# Глагол действия с `[Route]` без `Http*`: ASP.NET принимает его на любом методе.
ANY_METHOD: Final = "*"

# Сколько базовых классов проходим в поисках `[Route]`. Глубже десяти иерархий
# контроллеров не бывает; предел нужен на случай битого кода, где резолв
# собрал цепочку, которой в C# нет.
MAX_BASE_DEPTH: Final = 10

_SLASHES = re.compile(r"/{2,}")
# Токены маршрутизации нечувствительны к регистру: `[Controller]` и `[controller]`
# для ASP.NET одно и то же.
_CONTROLLER_TOKEN = re.compile(r"\[controller\]", re.IGNORECASE)
_ACTION_TOKEN = re.compile(r"\[action\]", re.IGNORECASE)

# Форма выражения, которую разрешаем: `Тип.Имя`, где тип может быть
# квалифицирован (`Squidex.Web.Constants.PrefixApi`). Остальное — `nameof`,
# интерполяция, конкатенация — остаётся невосстановленным с причиной.
_QUALIFIED_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")

# Литерал инициализатора константы: дословная строка `@"…"` (кавычка внутри
# удвоена) либо обычная без escape-последовательностей.
_CONST_LITERAL: Final = r'(?:@"(?P<verbatim>(?:[^"]|"")*)"|"(?P<regular>[^"\\]*)")'


@dataclass(frozen=True)
class _Template:
    """Значение аргумента маршрута: текст либо причина, почему его нет."""

    text: str = ""
    unresolved: str = ""


@dataclass(frozen=True)
class _Lookup:
    index: dict[str, Symbol]
    by_fqn: dict[str, list[str]]


def _first_attribute(attributes: list[Attribute], name: str) -> Attribute | None:
    return next((attribute for attribute in attributes if attribute.name == name), None)


# --------------------------------------------------------------------------------------
# Аргумент-выражение -> литерал константы
# --------------------------------------------------------------------------------------


def _const_literal(member: Member) -> str | None:
    """Литерал из инициализатора константы, если он литерал.

    Сигнатура поля хранится с инициализатором (`public const string X = "api"`),
    а у объявления на несколько имён (`const string A = "a", B = "b"`) — общая
    у всех членов. Поэтому значение ищется **по имени**, а не первым литералом.
    Обычная строка — только без escape-последовательностей: декодировать их
    здесь значило бы завести вторую копию правил C# ради формы, которой
    в маршрутах не встречали.
    """
    pattern = re.compile(
        rf"(?<![\w.]){re.escape(member.name)}\s*=\s*" + _CONST_LITERAL + r"\s*(?:,|$)"
    )
    found = pattern.search(member.signature)
    if found is None:
        return None
    if found["verbatim"] is not None:
        return found["verbatim"].replace('""', '"')
    return found["regular"]


def _namespace_prefixes(namespace: str) -> list[str]:
    parts = namespace.split(".") if namespace else []
    return [".".join(parts[:index]) for index in range(len(parts), 0, -1)] + [""]


def _resolve_constant(expression: str, owner: Symbol, lookup: _Lookup) -> _Template:
    """Разрешить `Тип.Имя` в литерал константы по индексу символов.

    Тип ищется, как его искал бы C#, насколько это видно без `using`:
    сначала охватывающий namespace владельца атрибута и его префиксы от
    длинного к короткому, потом — любой тип с таким окончанием FQN, у которого
    есть эта константа. Второй шаг нужен для типа из `using`
    (`IdentityServerController` у squidex видит `Squidex.Web.Constants`
    именно так), и он же даёт неоднозначность, если таких констант несколько
    с разными значениями: угадывать из двух нельзя.
    """
    text = "".join(expression.split()).removeprefix("global::")
    reason = f"аргумент маршрута — выражение `{expression.strip()}`"
    if _QUALIFIED_NAME.fullmatch(text) is None:
        return _Template(unresolved=reason)

    qualifier, _, name = text.rpartition(".")

    def constants(fqn: str) -> list[tuple[str, Member]]:
        return [
            (key, member)
            for key in lookup.by_fqn.get(fqn, [])
            for member in lookup.index[key].members
            if member.name == name and member.kind == "field" and "const" in member.modifiers
        ]

    for prefix in _namespace_prefixes(owner.namespace):
        nearest = constants(f"{prefix}.{qualifier}" if prefix else qualifier)
        if nearest:
            candidates = nearest
            break
    else:
        suffix = f".{qualifier}"
        candidates = [
            item
            for fqn in lookup.by_fqn
            if fqn == qualifier or fqn.endswith(suffix)
            for item in constants(fqn)
        ]

    if not candidates:
        return _Template(unresolved=f"{reason}: константа не найдена")

    values = _values(candidates)
    if len(values) > 1:
        # Тип своей сборки в C# перекрывает одноимённый из чужой — та же
        # предпочтительность, что у базовых типов (`base_symbol_key`).
        own = [item for item in candidates if lookup.index[item[0]].module == owner.module]
        values = _values(own) if own else values
    if len(values) > 1:
        return _Template(unresolved=f"{reason}: неоднозначно, значений {len(values)}")
    if values[0] is None:
        return _Template(unresolved=f"{reason}: значение константы — не литерал")
    return _Template(text=values[0])


def _values(candidates: list[tuple[str, Member]]) -> list[str | None]:
    """Разные значения констант-кандидатов; `None` — инициализатор не литерал."""
    return sorted(
        {_const_literal(member) for _, member in candidates}, key=lambda v: (v is None, v or "")
    )


def _positional(attribute: Attribute, position: int, owner: Symbol, lookup: _Lookup) -> _Template:
    value = attribute.args[position]
    if position in attribute.expression_args:
        return _resolve_constant(value, owner, lookup)
    return _Template(text=value)


def _named(attribute: Attribute, name: str, owner: Symbol, lookup: _Lookup) -> _Template:
    value = attribute.named_args[name]
    if name in attribute.expression_named_args:
        return _resolve_constant(value, owner, lookup)
    return _Template(text=value)


# --------------------------------------------------------------------------------------
# Шаблон типа: свой либо унаследованный
# --------------------------------------------------------------------------------------


def _base_class(symbol: Symbol, lookup: _Lookup) -> str | None:
    """Ключ базового **класса** в индексе. `None` — база внешняя или её нет.

    `base_types` отсортированы по тексту, и по позиции класс от интерфейса
    не отличить: `IDisposable` встанет раньше `ApiController`. Поэтому база —
    первый из разрешённых в индексе с видом `class`.
    """
    for position in range(len(symbol.base_types)):
        key = base_symbol_key(symbol, position, lookup.index, lookup.by_fqn)
        if key is not None and lookup.index[key].type_kind == "class":
            return key
    return None


def _type_template(symbol: Symbol, lookup: _Lookup) -> _Template:
    """Шаблон маршрута типа: свой `[Route]`, иначе ближайшего базового класса.

    `RouteAttribute` в ASP.NET Core наследуется: у squidex префикс `api/`
    объявлен на абстрактной `ApiController` выражением
    `[Route(Constants.PrefixApi)]`, и у 42 контроллеров из 48 своего маршрута
    нет. Свой `[Route]` наследника базу отменяет целиком — ASP.NET берёт первый
    класс иерархии, где маршрут объявлен.

    Константа разрешается от того класса, где атрибут **записан**: имя типа
    в выражении видно из его namespace, а не из namespace наследника.
    """
    current = symbol
    visited = {symbol_key(symbol.module, symbol.fqn, len(symbol.type_parameters))}
    for _ in range(MAX_BASE_DEPTH + 1):
        route = _first_attribute(current.attributes, ROUTE_ATTRIBUTE)
        if route is not None:
            return _positional(route, 0, current, lookup) if route.args else _Template()
        base = _base_class(current, lookup)
        # Защита от цикла: `A : B`, `B : A` в C# невозможны, но получить их
        # из битого или частично разобранного кода можно.
        if base is None or base in visited:
            break
        visited.add(base)
        current = lookup.index[base]
    return _Template()


# --------------------------------------------------------------------------------------
# Действия
# --------------------------------------------------------------------------------------


def _member_template(member: Member, owner: Symbol, lookup: _Lookup) -> _Template:
    """Шаблон отдельного `[Route]` на члене — для глагола без своего шаблона.

        [HttpGet]
        [Route("tenants/by-name/{name}")]
        public Task<TenantDto> FindTenantByNameAsync(string name)

    Это не экзотика, а **преобладающая** форма: в ABP так написаны 245 из 356
    HTTP-атрибутов. Реализация, читающая только аргумент `Http*`, выдала бы
    всем методам такого контроллера один и тот же маршрут — маршрут типа.
    """
    route = _first_attribute(member.attributes, ROUTE_ATTRIBUTE)
    if route is None or not route.args:
        return _Template()
    return _positional(route, 0, owner, lookup)


def _member_routes(
    member: Member, owner: Symbol, lookup: _Lookup
) -> Iterator[tuple[str, _Template]]:
    """Пары «глагол, шаблон действия» одного члена.

    Три формы, и каждая даёт ноль при наивном разборе:

    - `Http*` — шаблон в своём аргументе, иначе из `[Route]` того же члена;
    - `[AcceptVerbs("GET", "POST")]` — по эндпоинту на глагол, шаблон —
      `Route = …` самого атрибута, иначе из `[Route]` члена;
    - `[Route]` без глагола — один эндпоинт с методом `*`: ASP.NET принимает
      такое действие на любом методе (squidex: 6 действий).
    """
    has_verb = False
    for attribute in member.attributes:
        http_method = _HTTP_METHODS.get(attribute.name)
        if http_method is not None:
            has_verb = True
            yield (
                http_method,
                _positional(attribute, 0, owner, lookup)
                if attribute.args
                else _member_template(member, owner, lookup),
            )
        elif attribute.name == ACCEPT_VERBS:
            has_verb = True
            template = (
                _named(attribute, ROUTE_ATTRIBUTE, owner, lookup)
                if ROUTE_ATTRIBUTE in attribute.named_args
                else _member_template(member, owner, lookup)
            )
            verbs: set[str] = set()
            for position, verb in enumerate(attribute.args):
                if position in attribute.expression_args:
                    # Глагол-выражение (`HttpMethods.Get`) не выдумываем: эндпоинт
                    # есть, но какой у него метод и путь — неизвестно.
                    yield (
                        ANY_METHOD,
                        _Template(unresolved=f"глагол `AcceptVerbs` — выражение `{verb}`"),
                    )
                    continue
                verbs.add(verb.strip().upper())
            for verb in sorted(verbs):
                yield verb, template

    if not has_verb and _first_attribute(member.attributes, ROUTE_ATTRIBUTE) is not None:
        yield ANY_METHOD, _member_template(member, owner, lookup)


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


def _route(base: _Template, template: _Template) -> _Template:
    """Склеить с учётом невосстановленного: абсолютному шаблону база не нужна."""
    if template.unresolved:
        return template
    if template.text.startswith(("/", "~/")):
        return _Template(text=_combine("", template.text))
    if base.unresolved:
        return base
    return _Template(text=_combine(base.text, template.text))


def _substitute(route: str, type_name: str, member_name: str) -> str:
    """Подставить `[controller]` и `[action]`.

    Значения берутся с исходным регистром имени: `PricingController` даёт
    `Pricing`, а не `pricing`. ASP.NET подставляет их так же.

    `type_name` — тип, **для которого** считаются эндпоинты, а не тот, где
    записан `[Route]`: `[Route("api/[controller]")]` на базе даёт `api/Orders`
    у `OrdersController`, а не `api/TokenApi`.
    """
    route = _CONTROLLER_TOKEN.sub(type_name.removesuffix("Controller"), route)
    return _ACTION_TOKEN.sub(member_name.removesuffix("Async"), route)


def extract_endpoints(
    symbol: Symbol,
    index: dict[str, Symbol],
    by_fqn: dict[str, list[str]] | None = None,
) -> list[Endpoint]:
    """HTTP-эндпоинты типа. Для не-контроллера — пустой список.

    `index` нужен ради двух межтиповых шагов: `[Route]`, унаследованного
    от базового класса, и константы в аргументе маршрута. `by_fqn` — тот же
    `index_by_fqn(index)`; вызывающий по всему индексу передаёт его один раз,
    иначе группировка индекса повторялась бы на каждом типе.
    """
    lookup = _Lookup(index=index, by_fqn=by_fqn if by_fqn is not None else index_by_fqn(index))

    routes = [
        (member, http_method, template)
        for member in symbol.members
        for http_method, template in _member_routes(member, symbol, lookup)
    ]
    if not routes:
        # Иерархию не обходим вовсе: у типа без действий маршрута нет, а обход
        # каждого символа индекса до десятого предка стоил бы на ABP заметно.
        return []

    base = _type_template(symbol, lookup)
    found: list[Endpoint] = []
    for member, http_method, template in routes:
        route = _route(base, template)
        found.append(
            Endpoint(
                http_method=http_method,
                route=_substitute(route.text, symbol.name, member.name) if route.text else "",
                member=member.name,
                line=member.line,
                unresolved=route.unresolved,
            )
        )

    found.sort(
        key=lambda endpoint: (
            endpoint.route,
            endpoint.http_method,
            endpoint.member,
            endpoint.unresolved,
            endpoint.line,
        )
    )
    return found
