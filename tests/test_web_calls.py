"""HTTP-вызовы фронта (F07).

Ядро требования «связь фронт↔бэк». Половина проверок здесь — про то, что
в результат НЕ попало: счётчик вызовов без получателя меряет `Map.get`
и `form.get('search')`, и посчитанная по нему доля литералов выглядит
убедительно, ничего не значая.
"""

from pathlib import Path

import pytest

from docpipe.route import RewriteRule
from docpipe.web.calls import CallScan, RawCall, RegistryCall, build_calls, scan_calls

REGISTRY = [
    RegistryCall(
        route="api/items/query", discriminator_in="body", name="listInnerName", kind="list"
    ),
    RegistryCall(route="api/items", discriminator_in="query", name="listInnerName", kind="list"),
]


@pytest.fixture
def raw(web_workspace: Path) -> list[RawCall]:
    """Все вызовы фикстуры одним списком."""
    found: list[RawCall] = []
    for path in sorted(web_workspace.rglob("*.ts")):
        if "node_modules" in path.parts:
            continue
        relative = path.relative_to(web_workspace).as_posix()
        found.extend(scan_calls(path.read_bytes(), relative))
    return found


@pytest.fixture
def scan(raw: list[RawCall]) -> CallScan:
    return build_calls(raw, registry=REGISTRY)


def _of(raw: list[RawCall], file_part: str) -> list[RawCall]:
    return [call for call in raw if file_part in call.file]


def _url(raw: list[RawCall], expression_part: str) -> str | None:
    found = [call for call in raw if expression_part in call.expression]
    assert len(found) == 1, f"{expression_part}: найдено {len(found)}"
    return found[0].url


# --------------------------------------------------------------------------------------
# Что вообще считается HTTP-вызовом
# --------------------------------------------------------------------------------------


def test_call_without_an_http_receiver_is_not_a_call() -> None:
    """`Map.get` и `FormGroup.get`: 327 против 79 настоящих на боевом модуле.

    Оба в одном тесте намеренно: реализация, отсеявшая одно и пропустившая
    другое, всё равно даёт бессмысленное число.
    """
    source = b"""
export class A {
  private readonly cache = new Map<string, unknown>();
  run(id: string): void {
    this.cache.get(id);
    this.form.get('search');
    headers.get('Accept');
    this.route.queryParams.get('id');
    this.http.get('api/real');
  }
}
"""
    calls = scan_calls(source, "src/a.ts")
    assert [call.url for call in calls] == ["api/real"]


@pytest.mark.parametrize("receiver", ["this.http", "_http", "this.httpClient", "httpService"])
def test_known_receivers(receiver: str) -> None:
    source = f"export class A {{ run() {{ {receiver}.get('api/x'); }} }}\n".encode()
    assert [call.url for call in scan_calls(source, "src/a.ts")] == ["api/x"]


def test_verb_comes_from_the_method_name() -> None:
    source = b"export class A { run() { this.http.post('api/x', {}); } }\n"
    assert scan_calls(source, "src/a.ts")[0].http_method == "POST"


def test_unknown_method_of_a_known_receiver_is_not_a_call() -> None:
    """`request(method, url)` не поддержан: первый аргумент там не URL.

    Обработка «через раз» дала бы вызовы с маршрутом `get`.
    """
    source = b"export class A { run() { this.http.request('GET', 'api/x'); } }\n"
    assert scan_calls(source, "src/a.ts") == []


# --------------------------------------------------------------------------------------
# Шесть форм первого аргумента
# --------------------------------------------------------------------------------------


def test_form_1_string_literal(raw: list[RawCall]) -> None:
    assert _url(raw, "'api/ml/structure'") == "api/ml/structure"


def test_form_2_template_string(raw: list[RawCall]) -> None:
    assert _url(raw, "`api/ml/structure/${id}`") == "api/ml/structure/{}"


def test_form_3_base_in_a_field(raw: list[RawCall]) -> None:
    """`${this.baseUrl}/x`: подстановка в начале — база, а не сегмент пути."""
    assert (
        _url(raw, "`${this.baseUrl}/saveAlternative`") == "/api/ml/debtsconsgroup/saveAlternative"
    )


def test_form_4_module_constant(raw: list[RawCall]) -> None:
    """Через `auditUrl` в боевом модуле идут десять вызовов одного сервиса."""
    calls = [call for call in _of(raw, "audit.service") if call.expression == "auditUrl"]
    assert len(calls) == 3
    assert {call.url for call in calls} == {"/integration/log/AuditJ"}


def test_form_5_concatenation(raw: list[RawCall]) -> None:
    call = next(call for call in raw if "+ id" in call.expression)
    assert call.url == "api/ml/structure/getForUpdate/{}"
    assert call.confidence == "medium"


def test_form_6_url_from_a_parameter_is_unresolved(raw: list[RawCall]) -> None:
    """Невосстановленный вызов назван файлом и строкой, а не потерян."""
    unresolved = [call for call in raw if not call.resolved]
    assert len(unresolved) == 1

    call = unresolved[0]
    assert call.file == "src/app/cf-api/resources/model.service.ts"
    assert call.line > 0
    assert call.reason


def test_concatenation_keeps_the_order_of_the_text() -> None:
    """`a + b` — это `(a + b)`, и обход обязан дать `a`, потом `b`.

    Перевёрнутый обход даёт `{}api/x/`: ключ, который не совпадёт ни с чем,
    но выглядит как настоящий.
    """
    source = b"export class A { run(id) { this.http.get('api/x/' + id + '/tail'); } }\n"
    assert scan_calls(source, "src/a.ts")[0].url == "api/x/{}/tail"


def test_unresolved_leading_substitution_is_not_a_key() -> None:
    """`${this.unknown}/x` дало бы `{}/x` — ключ-пустышку, ни с чем не совпадающий."""
    source = b"export class A { run() { this.http.get(`${this.unknown}/x`); } }\n"
    call = scan_calls(source, "src/a.ts")[0]
    assert call.url is None
    assert "база" in call.reason


# --------------------------------------------------------------------------------------
# Разрешение констант
# --------------------------------------------------------------------------------------


def test_field_literals_are_scoped_to_their_class() -> None:
    """Два сервиса в одном файле законно объявляют `baseUrl` с разными значениями.

    Общая таблица подставила бы в вызов чужую базу — молча и правдоподобно.
    """
    source = b"""
export class First {
  private readonly baseUrl: string = '/api/first';
  run() { return this.http.get(`${this.baseUrl}/x`); }
}
export class Second {
  private readonly baseUrl: string = '/api/second';
  run() { return this.http.get(`${this.baseUrl}/x`); }
}
"""
    assert [call.url for call in scan_calls(source, "src/a.ts")] == [
        "/api/first/x",
        "/api/second/x",
    ]


def test_field_without_a_literal_is_not_resolved() -> None:
    source = b"""
export class A {
  private readonly baseUrl: string = environment.apiRoot;
  run() { return this.http.get(`${this.baseUrl}/x`); }
}
"""
    assert scan_calls(source, "src/a.ts")[0].url is None


# --------------------------------------------------------------------------------------
# Обращения к реестру: один маршрут на много смыслов
# --------------------------------------------------------------------------------------


def test_registry_calls_with_one_route_give_different_keys(scan: CallScan) -> None:
    """Ключ «метод + маршрут» склеил бы пользователей, модели и справочники."""
    items = [call for call in scan.calls if call.key.route.startswith("api/items")]
    discriminators = sorted(call.key.discriminator for call in items)

    assert discriminators == ["", "dictionaries", "models", "users"]
    assert len({(call.key.route, call.key.discriminator) for call in items}) == 4


def test_body_and_query_forms_are_both_recognised(scan: CallScan) -> None:
    """Одного правила на оба случая не хватает: их два у одного API платформы."""
    by_key = {(call.key.http_method, call.key.discriminator) for call in scan.calls}

    assert ("POST", "users") in by_key  # различитель в теле
    assert ("GET", "dictionaries") in by_key  # различитель в query


def test_registry_call_without_a_literal_discriminator_is_named(scan: CallScan) -> None:
    """Маршрут известен, смысл — нет. Это состояние, а не ошибка."""
    assert len(scan.registry_unresolved) == 1
    assert scan.registry_unresolved[0].key.route == "api/items"
    assert scan.registry_unresolved[0].key.discriminator == ""
    # И этот вызов всё равно восстановлен: он в общем списке.
    assert scan.registry_unresolved[0] in scan.calls


def test_route_outside_the_registry_list_keeps_an_empty_discriminator(scan: CallScan) -> None:
    ordinary = next(call for call in scan.calls if call.key.route == "api/ml/structure")
    assert ordinary.key.discriminator == ""


# --------------------------------------------------------------------------------------
# Честность числа
# --------------------------------------------------------------------------------------


def test_resolved_plus_unresolved_equals_all_calls(raw: list[RawCall], scan: CallScan) -> None:
    """Именно это делает число честным: иначе «восстановлено 58» не значит ничего."""
    assert len(scan.calls) + len(scan.unresolved) == len(raw)
    assert len(raw) == 16


def test_registry_unresolved_is_a_subset_of_resolved(scan: CallScan) -> None:
    assert all(call in scan.calls for call in scan.registry_unresolved)


def test_scan_is_deterministic(web_workspace: Path) -> None:
    source = (web_workspace / "src/app/shared/services/items.service.ts").read_bytes()
    assert scan_calls(source, "x.ts") == scan_calls(source, "x.ts")


# --------------------------------------------------------------------------------------
# Конфигурация применяется после разбора
# --------------------------------------------------------------------------------------


def test_prefix_rewrite_is_applied_at_build_time(raw: list[RawCall]) -> None:
    """Факты о вызове от конфигурации не зависят — иначе их нельзя кэшировать.

    Литерал `/pm/api/limits/getperiods` и маршрут контроллера `api/limits/…` —
    один и тот же эндпоинт; без снятия префикса они разъедутся по двум корзинам.
    """
    widget = _of(raw, "widget.service")
    assert widget[0].url == "/pm/api/limits/getperiods"

    without = build_calls(widget)
    with_rule = build_calls(widget, rewrite=RewriteRule("widget", strip_prefix="/pm"))

    assert without.calls[0].key.route == "pm/api/limits/getperiods"
    assert with_rule.calls[0].key.route == "api/limits/getperiods"


def test_registry_table_changes_keys_without_reparsing(raw: list[RawCall]) -> None:
    """Смена настройки обязана менять ключи, не заставляя перечитывать исходники."""
    without = build_calls(raw)
    with_registry = build_calls(raw, registry=REGISTRY)

    assert all(call.key.discriminator == "" for call in without.calls)
    assert any(call.key.discriminator for call in with_registry.calls)
