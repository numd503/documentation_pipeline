"""Нормализация маршрута (F03).

Функция чистая и со списком случаев, поэтому проверяется до того, как появится
хоть один разобранный файл. Она же самое вероятное место будущих ошибок: любая
из шести операций, выполненная не в свой черёд, даёт ключ, который выглядит
правдоподобно и не совпадает ни с чем.
"""

import random

import pytest

from docpipe.route import RewriteRule, RouteKey, almost_equal, normalize_route, route_key

# --------------------------------------------------------------------------------------
# Шесть шагов, по одному случаю на строку таблицы
# --------------------------------------------------------------------------------------


def test_step_1_drops_scheme_and_host() -> None:
    assert normalize_route("https://host/api/x") == "api/x"
    assert normalize_route("http://host:5000/api/x") == "api/x"
    # Хост без пути: маршрута нет, и это пустая строка, а не падение.
    assert normalize_route("https://host") == ""


def test_step_2_drops_query_and_fragment() -> None:
    """Различающий смысл параметр уезжает здесь — поэтому он и вынесен в ключ."""
    assert normalize_route("api/items?listInnerName=models") == "api/items"
    assert normalize_route("api/items#anchor") == "api/items"


def test_step_2_does_not_cut_inside_a_substitution() -> None:
    """`[HttpGet("{id?}")]` — законная и частая форма шаблона ASP.NET.

    Разделитель query, найденный внутри подстановки, обрезает маршрут до
    `api/x/{id`: подстановка после этого не распознаётся, а ключ выглядит
    правдоподобно. Четвёртый шаг починить это уже не может.
    """
    assert normalize_route("api/x/{id?}") == "api/x/{}"
    assert normalize_route("api/x/{id?}?listInnerName=models") == "api/x/{}"


def test_step_3_strips_the_application_prefix() -> None:
    assert normalize_route("/pm/api/limits", rewrite=RewriteRule("PM.Front", "/pm")) == "api/limits"


def test_step_3_adds_the_application_prefix() -> None:
    """У админки префикс не срезается, а дописывается: `^/api/` → `/admin/api/`."""
    rule = RewriteRule("Sbt.CMS.Front", add_prefix="/admin")
    assert normalize_route("/api/users", rewrite=rule) == "admin/api/users"


def test_step_4_replaces_substitutions() -> None:
    assert normalize_route("`api/ml/x/${id}`".strip("`")) == "api/ml/x/{}"
    assert normalize_route("api/ml/x/{id:guid}") == "api/ml/x/{}"
    assert normalize_route("api/ml/x/{id?}") == "api/ml/x/{}"
    assert normalize_route("api/ml/x/{*rest}") == "api/ml/x/{}"
    assert normalize_route("api/ml/x/{id}") == "api/ml/x/{}"


def test_step_5_collapses_and_trims_slashes() -> None:
    assert normalize_route("//api/x/") == "api/x"
    assert normalize_route("/api///x") == "api/x"


def test_step_6_lowercases() -> None:
    assert normalize_route("api/ML/Structure") == "api/ml/structure"


# --------------------------------------------------------------------------------------
# Комбинированные случаи: шаги обязаны складываться, а не мешать друг другу
# --------------------------------------------------------------------------------------


def test_combined_absolute_url_with_prefix_query_and_substitution() -> None:
    rule = RewriteRule("PM.Front", strip_prefix="/pm")
    raw = "https://cf.example/pm/API/Limits/${id}/?listInnerName=models#top"
    assert normalize_route(raw, rewrite=rule) == "api/limits/{}"


def test_combined_added_prefix_with_template_and_trailing_slash() -> None:
    rule = RewriteRule("Sbt.CMS.Front", add_prefix="/admin")
    assert normalize_route("/API/Users/${id}/", rewrite=rule) == "admin/api/users/{}"


# --------------------------------------------------------------------------------------
# Порядок шагов: ловушки, каждая из которых даёт правдоподобный неверный ключ
# --------------------------------------------------------------------------------------


def test_prefix_is_compared_ignoring_case_on_both_sides() -> None:
    """Приведение к нижнему регистру идёт последним шагом.

    Сложить регистр раньше — значит сравнивать уже приведённый путь с сырым
    значением из конфигурации, и у того, кто написал `/PM`, сравнение
    перестанет работать.
    """
    assert normalize_route("/PM/api/limits", rewrite=RewriteRule("PM", "/pm")) == "api/limits"
    assert normalize_route("/pm/api/limits", rewrite=RewriteRule("PM", "/PM")) == "api/limits"


def test_prefix_is_compared_by_segments_not_by_substring() -> None:
    """`pathRewrite` — регулярное выражение, и `^/pm` срезало бы `/pmloadrequests`.

    Получился бы выдуманный маршрут `/loadrequests/…`, который выглядит
    настоящим. У PM.Front в прокси есть контекст и `/pm/api`,
    и `/pm/pmloadrequests`, то есть обе формы в репозитории существуют.
    """
    rule = RewriteRule("PM.Front", strip_prefix="/pm")
    assert normalize_route("/pmloadrequests/x", rewrite=rule) == "pmloadrequests/x"
    assert normalize_route("/pm/pmloadrequests/x", rewrite=rule) == "pmloadrequests/x"


def test_added_prefix_is_not_duplicated() -> None:
    """Литерал, уже написанный с префиксом, не должен получить его дважды.

    `/admin/admin/api/x` дал бы сразу две находки — «вызов без эндпоинта»
    и «эндпоинт без вызывающего», — и обе выглядели бы дефектом.
    """
    rule = RewriteRule("Sbt.CMS.Front", add_prefix="/admin")
    assert normalize_route("/admin/api/users", rewrite=rule) == "admin/api/users"


def test_strip_runs_before_add_when_a_rule_has_both() -> None:
    """Правило с обоими полями — замена головы пути.

    В обратном порядке дописанная голова закрыла бы собой ту, которую надо
    снять, и снятие молча не сработало бы.
    """
    rule = RewriteRule("Both", strip_prefix="/pm", add_prefix="/admin")
    assert normalize_route("/pm/api/x", rewrite=rule) == "admin/api/x"


def test_empty_rewrite_fields_change_nothing() -> None:
    """`strip_prefix: ""` — это «проверено, префикса нет», и оно обязано работать."""
    rule = RewriteRule("Sbt.CMS.Cashflow.ML", strip_prefix="")
    assert normalize_route("api/ml/structure", rewrite=rule) == "api/ml/structure"
    assert normalize_route("api/ml/structure") == "api/ml/structure"


def test_leading_substitution_stays_a_dead_key() -> None:
    """Ведущая подстановка — база, а не сегмент, и разрешает её F07, не мы.

    Здесь фиксируется именно то, что своими силами получается бесполезный
    ключ: значит, невосстановленный вызов обязан быть отдельной категорией
    отчёта, а не попадать в связи с таким маршрутом.
    """
    assert normalize_route("${this.baseUrl}/saveAlternative") == "{}/savealternative"


# --------------------------------------------------------------------------------------
# Вырожденный вход
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "/", "//", "   ", "?a=1", "#top"])
def test_degenerate_input_gives_empty_route(raw: str) -> None:
    assert normalize_route(raw) == ""


# --------------------------------------------------------------------------------------
# Чистота функции
# --------------------------------------------------------------------------------------


def test_normalize_route_is_pure() -> None:
    """Одинаковый вход даёт одинаковый выход независимо от порядка вызовов."""
    rule = RewriteRule("PM.Front", strip_prefix="/pm")
    cases = [
        ("https://h/pm/API/x/${id}?q=1", rule),
        ("api/items?listInnerName=models", None),
        ("//api/ML/Structure/", None),
        ("/pm/api/limits/{id:guid}", rule),
    ]
    expected = {raw: normalize_route(raw, rewrite=r) for raw, r in cases}

    shuffled = cases * 3
    random.Random(20260809).shuffle(shuffled)
    for raw, r in shuffled:
        assert normalize_route(raw, rewrite=r) == expected[raw]


# --------------------------------------------------------------------------------------
# route_key
# --------------------------------------------------------------------------------------


def test_route_key_uppercases_the_method_and_normalizes_the_route() -> None:
    key = route_key("post", "/API/ML/Structure/")
    assert key == RouteKey(http_method="POST", route="api/ml/structure")


def test_route_key_allows_an_unknown_method() -> None:
    """Пустой глагол — законное значение: сторона может его не знать."""
    assert route_key("", "api/x").http_method == ""


def test_route_key_keeps_the_discriminator_case() -> None:
    """Дальше он сравнивается с `ref` реестра, а тот сравнивается точно.

    Сложенный здесь регистр сделал бы пару неразрешимой в бизнес-слое,
    и выглядело бы это как «инструмент не нашёл».
    """
    key = route_key("POST", "api/items/query", discriminator="OkkChain")
    assert key.discriminator == "OkkChain"


def test_route_key_separates_registry_calls_with_one_route() -> None:
    """Один маршрут платформы на много смыслов: ключи обязаны различаться."""
    users = route_key("POST", "api/items/query", discriminator="users")
    models = route_key("POST", "api/items/query", discriminator="models")
    assert users != models
    assert users.route == models.route


# --------------------------------------------------------------------------------------
# almost_equal
# --------------------------------------------------------------------------------------


def test_almost_equal_ignores_the_number_of_substitutions() -> None:
    a = route_key("GET", "api/ml/structure/getForUpdate")
    b = route_key("GET", "api/ml/structure/getForUpdate/{id}")
    assert almost_equal(a, b)
    assert almost_equal(b, a)


def test_almost_equal_is_false_for_different_segments() -> None:
    a = route_key("GET", "api/ml/structure/getForUpdate")
    b = route_key("GET", "api/ml/structure/getForDelete")
    assert not almost_equal(a, b)
    assert not almost_equal(route_key("GET", "api/x"), route_key("GET", "api/x/y"))


def test_almost_equal_is_false_for_identical_keys() -> None:
    """«Почти» исключает «точно», иначе точные связи уедут в корзину «почти»."""
    key = route_key("GET", "api/ml/structure")
    assert not almost_equal(key, key)


def test_almost_equal_requires_the_same_method_and_discriminator() -> None:
    assert not almost_equal(route_key("GET", "api/x/{id}"), route_key("POST", "api/x"))
    assert not almost_equal(
        route_key("POST", "api/items/query", discriminator="users"),
        route_key("POST", "api/items/query/{id}", discriminator="models"),
    )
