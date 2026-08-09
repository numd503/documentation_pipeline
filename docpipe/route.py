"""Нормализация HTTP-маршрута: общий ключ для стороны .NET и стороны фронта.

Модуль лежит **не** под `web/`, потому что им пользуются обе стороны. Две копии
нормализации разойдутся на первом же частном случае и разойдутся молча: маршруты
просто перестанут находить друг друга, и отчёт покажет «вызов без эндпоинта»
плюс «эндпоинт без вызывающего» — две находки вместо одной связи.

Ключ связи — пара `(HTTP-метод, маршрут)`, а не ссылка на узел: идентификатор
узла содержит путь проекта и FQN, поэтому переименование контроллера рвало бы
связь при неизменном HTTP-контракте.
"""

import re
from dataclasses import dataclass

# Подстановка любой из двух сторон: `${id}` в шаблонной строке фронта,
# `{id}`, `{id:guid}`, `{id?}`, `{*rest}` в шаблоне маршрута ASP.NET.
# Форма со знаком доллара стоит первой: иначе `$` останется висеть перед `{}`.
_SUBSTITUTION = re.compile(r"\$\{[^{}]*\}|\{[^{}]*\}")

_REPEATED_SLASHES = re.compile(r"/{2,}")


@dataclass(frozen=True)
class RewriteRule:
    """Что делает с URL прокси по дороге к бэкенду.

    Значения приходят из `web.url_rewrite` и повторяют `pathRewrite` файла
    `proxy.conf`: у PM `^/pm` → `''` (срезается), у админки `^/api/` →
    `/admin/api/` (дописывается). Правило описывает **преобразование**,
    а не сам факт наличия настройки: отсутствие правила у модуля — это
    отдельное состояние, и заводить для него `RewriteRule()` с пустыми
    полями нельзя. Пустые поля значат «проверено, преобразования нет».
    """

    module: str
    strip_prefix: str = ""
    add_prefix: str = ""


@dataclass(frozen=True)
class RouteKey:
    """Ключ, по которому сходятся вызов фронта и эндпоинт бэкенда."""

    # Верхний регистр; пустая строка — если сторона глагола не знает.
    http_method: str
    # Нормализованный путь без ведущего и замыкающего `/`.
    route: str
    # Имя списка для обращений к реестру (F07). Один маршрут платформы
    # обслуживает много смыслов, и без различителя они склеятся в одну точку.
    discriminator: str = ""


def _segments(value: str) -> list[str]:
    return [segment for segment in value.split("/") if segment]


def _matches_head(segments: list[str], head: list[str]) -> bool:
    """Начинается ли путь с этих сегментов, без учёта регистра.

    Сравнение **по сегментам**, а не по подстроке: `pathRewrite: {'^/pm': ''}`
    — регулярное выражение, и на строке `/pmloadrequests/x` оно срезало бы `/pm`,
    оставив выдуманный маршрут `/loadrequests/x`. У PM.Front контекст прокси
    `/pm/pmloadrequests` существует, то есть обе формы в репозитории есть,
    и различает их только граница сегмента.

    Регистр складывается с обеих сторон. Приведение к нижнему регистру идёт
    последним шагом нормализации (см. `normalize_route`), поэтому сюда путь
    приходит как записан; сложить регистр только у одной стороны означало бы
    сломать сравнение тому, кто написал в конфигурации `/PM`.
    """
    if len(segments) < len(head):
        return False
    return [item.lower() for item in segments[: len(head)]] == [item.lower() for item in head]


def _cut_query_and_fragment(route: str) -> str:
    """Отбросить query и фрагмент, не тронув подстановку.

    Разделитель ищется **вне фигурных скобок**. Иначе шаблон необязательного
    параметра ASP.NET — `[HttpGet("{id?}")]` — обрезается по своему же
    вопросительному знаку до `{id`, и дальше подстановка не распознаётся:
    получается ключ `api/x/{id`, который выглядит правдоподобно и не совпадает
    ни с чем. Шаг замены подстановок идёт четвёртым и починить это уже не может.
    """
    depth = 0
    for index, char in enumerate(route):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(depth - 1, 0)
        elif char in "?#" and depth == 0:
            return route[:index]
    return route


def normalize_route(raw: str, *, rewrite: RewriteRule | None = None) -> str:
    """Привести маршрут к канонической форме. Чистая функция.

    Порядок шагов фиксирован и менять его нельзя:

    1. отбросить схему и хост;
    2. отбросить query и фрагмент;
    3. применить преобразование префикса модуля;
    4. заменить подстановки на `{}`;
    5. схлопнуть `//`, снять ведущий и замыкающий `/`;
    6. привести к нижнему регистру.

    Ведущая подстановка — это **база, а не сегмент**: `` `${this.baseUrl}/x` ``
    даёт `{}/x`, что не совпадёт ни с чем. Разрешать базу обязан вызывающий
    (F07) и передавать сюда уже склеенный литерал; неразрешённый вызов —
    отдельная категория отчёта, а не мусорный ключ.
    """
    route = raw.strip()

    # 1. Схема и хост. Абсолютный URL встречается в вызовах к соседним системам.
    scheme = route.find("://")
    if scheme != -1:
        slash = route.find("/", scheme + 3)
        route = route[slash:] if slash != -1 else ""

    # 2. Query и фрагмент. Различающий смысл параметр (`listInnerName`) уезжает
    # здесь же — поэтому он выносится в `RouteKey.discriminator` до вызова.
    route = _cut_query_and_fragment(route)

    # 3. Префикс приложения. Сначала снимается срезаемый прокси, потом
    # дописывается добавляемый: у правила, где заданы оба, это замена головы
    # пути. В обратном порядке дописанная голова закрыла бы собой ту,
    # которую надо снять, и снятие не сработало бы.
    if rewrite is not None:
        segments = _segments(route)
        head = _segments(rewrite.strip_prefix)
        if head and _matches_head(segments, head):
            segments = segments[len(head) :]
        added = _segments(rewrite.add_prefix)
        # Не дублируем то, что уже написано: так же ведёт себя и боевой
        # декоратор `SetApiUrl`, не добавляющий базу, если она уже в URL.
        if added and not _matches_head(segments, added):
            segments = added + segments
        if head or added:
            route = "/".join(segments)

    # 4. Подстановки обеих сторон.
    route = _SUBSTITUTION.sub("{}", route)

    # 5. Слэши.
    route = _REPEATED_SLASHES.sub("/", route).strip("/")

    # 6. Регистр — последним шагом, см. `_matches_head`.
    return route.lower()


def route_key(
    http_method: str,
    raw: str,
    *,
    rewrite: RewriteRule | None = None,
    discriminator: str = "",
) -> RouteKey:
    """Собрать ключ связи.

    `discriminator` кладётся **как написан**, без приведения регистра: дальше
    он сравнивается с `ref` записи реестра, а бизнес-слой сравнивает `ref`
    точным равенством (`business.resolve._key`). Сложенный здесь регистр
    сделал бы пару неразрешимой там, и выглядело бы это как «инструмент
    не нашёл», а не как расхождение соглашений.
    """
    return RouteKey(
        http_method=http_method.strip().upper(),
        route=normalize_route(raw, rewrite=rewrite),
        discriminator=discriminator.strip(),
    )


def _fixed_segments(route: str) -> list[str]:
    """Сегменты маршрута без подстановок."""
    return [segment for segment in route.split("/") if segment and segment != "{}"]


def almost_equal(a: RouteKey, b: RouteKey) -> bool:
    """Различаются ли ключи **только числом** подстановок `{}`.

    Это инвентарь, а не дефект: `api/ml/structure/getforupdate` и
    `api/ml/structure/getforupdate/{}` почти наверняка один и тот же эндпоинт,
    у которого одна сторона знает про параметр, а вторая нет.

    Совпавшие целиком ключи дают **ложь**: «почти» исключает «точно». Иначе
    реализация, перебирающая кандидатов через `almost_equal` до проверки
    на точное равенство, сложила бы найденные связи в корзину «почти совпало»
    и молча занизила бы число настоящих.

    Глагол и различитель обязаны совпадать: `GET` и `POST` на одном пути —
    разные эндпоинты, а два обращения к реестру с разными именами списков —
    разные точки.
    """
    if a == b:
        return False
    if a.http_method != b.http_method or a.discriminator != b.discriminator:
        return False
    return _fixed_segments(a.route) == _fixed_segments(b.route)
