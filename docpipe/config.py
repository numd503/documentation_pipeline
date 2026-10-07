"""Конфигурация запуска (`docpipe.yaml`).

Отделена от правил классификации (`rules/dotnet.yaml`) намеренно: правила
описывают, *что считать контроллером*, конфигурация — *где искать код и что
из него документировать*. Первое переносится между проектами, второе нет.
"""

import json
import posixpath
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, NamedTuple, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field, Tag, field_validator, model_validator
from pydantic import Discriminator as ByForm  # `Discriminator` ниже — модель `registry_calls`

from docpipe.discovery import matches_glob

DocLayout = Literal["kind-first", "module-first"]


def _repo_relative(value: str, field: str) -> str:
    """Проверить, что путь репо-относительный и POSIX, и убрать лишние слэши.

    Значения этих полей попадают в `doc_path` каждого узла манифеста, а тот
    обязан быть репо-относительным с POSIX-разделителями даже на Windows.
    Абсолютный путь или `..` дал бы манифест, который невозможно перенести
    между машинами, и обнаружилось бы это не здесь, а на чужом компьютере.
    """
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError(
            f"{field}: путь обязан быть относительным корня репозитория, дано {value!r}"
        )
    if "\\" in value:
        raise ValueError(f"{field}: разделитель — только `/`, даже на Windows; дано {value!r}")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"{field}: выход за корень (`..`) запрещён, дано {value!r}")
    return "/".join(parts)


# --------------------------------------------------------------------------------------
# Вторая форма записи: решение с причиной (S22, Р-2)
# --------------------------------------------------------------------------------------
#
# Причина решения живёт в самом файле настройки, рядом с решением: отдельный
# журнал отстал бы от файла на первой же правке. Короткая форма (строка)
# продолжает работать — копия настройки лежит на боевом репозитории, — поэтому
# ключ принимает обе формы вперемешку, а потребители читают только
# нормализованные свойства `DocpipeConfig` (`enrolled_globs` и соседи): строки
# в порядке файла. Объект в месте, где ждут строку, ломается не там, где его
# подсунули: `sorted` строк вперемешку с записями в `emit.exclude_globs`
# (записи хэшируемы — модель замороженная, — поэтому множество их принимает
# и падает уже сортировка), суффикс `/**` в `discovery._directory_globs`,
# шаблоны движку графа, `matches_glob` в области модуля. Тест-сторож
# (`tests/test_config_reasons.py`) не даёт прочитать поле мимо свойства.


class _Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Enrolled(_Decision):
    """Запись `enrolled` с причиной: модуль, чей `.csproj` совпал с `glob`, документируем."""

    glob: str
    reason: str = ""


class NotEnrolled(_Decision):
    """Запись `not_enrolled`: модуль не документируем, и причина обязательна.

    Короткой формы у ключа нет намеренно. «Не берём» без причины — то самое
    безымянное число, от которого уходили в отчёте классификации (отсев без
    `reason` запрещён там по той же причине): через месяц никто не отличит
    «решили не брать» от «выключили, чтобы не мешало».
    """

    glob: str
    reason: str

    @model_validator(mode="before")
    @classmethod
    def _require_reason(cls, value: Any) -> Any:
        # Подсказка вместо `Input should be a valid dictionary`: строка здесь —
        # самая естественная ошибка, потому что у соседних ключей она законна.
        if isinstance(value, str):
            raise ValueError(
                f"not_enrolled: у записи {value!r} нет причины — короткой формы у ключа нет;"
                f' пишите `- glob: "{value}"` и `reason: "…"`'
            )
        if isinstance(value, dict) and "reason" not in value:
            raise ValueError(
                f"not_enrolled: у записи {value.get('glob')!r} нет `reason` — решение"
                " «не документируем» без причины не принимается"
            )
        return value

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("not_enrolled: `reason` пустой — причина обязательна")
        return value


class ExcludeEntry(_Decision):
    """Запись `exclude` с причиной: обход не заходит в файлы под `glob`."""

    glob: str
    reason: str = ""


class RootEntry(_Decision):
    """Запись `web.roots` с причиной: каталог фронта, который обходим."""

    path: str
    reason: str = ""

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return _repo_relative(value, "web.roots")


class NamedDecision(_Decision):
    """Имя с причиной: запись `di_methods` или `dispatch_interfaces`."""

    name: str
    reason: str = ""


def _form(value: Any) -> str:
    """Какая форма у записи: словарь (или готовая модель) — вторая, иначе — короткая.

    Форма выбирается по виду значения, а не перебором вариантов объединения:
    при переборе опечатка в ключе записи (`reson:`) давала две ошибки, и первой
    шла «Input should be a valid string» — подсказка превратить запись
    в строку и потерять причину. С выбором по виду ошибка одна и про ключ.
    """
    return "entry" if isinstance(value, dict | BaseModel) else "short"


EnrolledItem = Annotated[
    Annotated[str, Tag("short")] | Annotated[Enrolled, Tag("entry")], ByForm(_form)
]
ExcludeItem = Annotated[
    Annotated[str, Tag("short")] | Annotated[ExcludeEntry, Tag("entry")], ByForm(_form)
]
RootItem = Annotated[
    Annotated[str, Tag("short")] | Annotated[RootEntry, Tag("entry")], ByForm(_form)
]
NamedItem = Annotated[
    Annotated[str, Tag("short")] | Annotated[NamedDecision, Tag("entry")], ByForm(_form)
]


# Умолчания функциями, а не `lambda: ["**"]`: литерал выводится как `list[str]`,
# а поле ждёт список объединения — список инвариантен, и mypy прав.
def _all_modules() -> list[str | Enrolled]:
    return ["**"]


def _whole_repository() -> list[str | RootEntry]:
    return ["."]


def _globs(items: Sequence[str | Enrolled | ExcludeEntry]) -> list[str]:
    return [item if isinstance(item, str) else item.glob for item in items]


def _names(items: Sequence[str | NamedDecision]) -> list[str]:
    return [item if isinstance(item, str) else item.name for item in items]


def _cache_path(value: str, field: str) -> str:
    """Каталог кэша: абсолютный путь разрешён, относительный — без `..` и `\\`.

    Почему абсолютный законен, а `..` нет, — в комментарии к
    `DocpipeConfig._check_cache_dir`: написанный руками путь наружу — решение,
    видное в конфигурации, а `..` — место, зависящее от того, откуда звали.
    """
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return value
    return _repo_relative(value, field)


class UrlRewrite(BaseModel):
    """Что делает с URL прокси модуля по дороге к бэкенду.

    Повторяет `pathRewrite` из `proxy.conf`: у PM `^/pm` → `''`, у админки
    `^/api/` → `/admin/api/`. Пустые поля — законное значение: «проверено,
    преобразования нет». **Отсутствие записи о модуле — другое**: это
    ненастроенный модуль, и отчёт связи обязан назвать его вслух, иначе
    забытая настройка выглядит как исправная связь.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    module: str
    strip_prefix: str = ""
    add_prefix: str = ""
    # Откуда взято значение (`proxy.conf`, интерцептор) — для того, кто будет
    # сверять запись через полгода. На разбор не влияет.
    reason: str = ""


class Discriminator(BaseModel):
    """Где лежит имя списка у обращения к реестру."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    where: Literal["body", "query"] = Field(alias="in")
    name: str


class RegistryCallConfig(BaseModel):
    """Маршрут платформы, у которого смысл вызова определяет не маршрут.

    `api/items/query` с `listInnerName` в теле и `api/items?listInnerName=…`
    в query — один маршрут на много смыслов. Одного правила на оба случая
    не хватает: их два у одного и того же API.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    route: str
    discriminator: Discriminator
    kind: str = ""
    reason: str = ""


class WebConfig(BaseModel):
    """Секция `web` в `docpipe.yaml`: шаг разбора фронтенда.

    Отдельная секция, а не поля верхнего уровня: ключи шага 1 читает и шаг 2,
    и бизнес-слой, и подмешивать к ним настройки одного языка значило бы
    показывать их всем троим.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Читается только через `root_paths`: запись бывает строкой и объектом
    # с причиной (`RootEntry`), а обход ждёт строки.
    roots: list[RootItem] = Field(default_factory=_whole_repository)
    rules: str = "rules/rules.yaml"
    out: str = "artifacts/doc-tree.web.json"
    link_out: str = "artifacts/web-link.json"

    # Ручной состав страниц. Пустая строка — файла нет, и это законно:
    # репозиторий, где обход находит всё сам, ничего не дописывает руками.
    pages: str = ""

    # Каталог документации фронта ВНУТРИ `docs_root` — вторая ветка дерева
    # рядом с `modules_dir`, а не под ним. Пустая строка значит «та же ветка,
    # что у бэкенда»: конфигурации, написанные до появления ключа, обязаны
    # продолжать работать без правок.
    #
    # Смысл разделения в том, что дерево фронта живёт по своим правилам:
    # единица документации там страница, а не класс, и половина узлов
    # вообще не получает файла (их описывает документ страницы). Смешанные
    # в одном каталоге, две ветки читаются как одна и та же — при том, что
    # искать в них надо разное.
    modules_dir: str = ""

    # Пустой список — законное значение: значит, проверено и ничего
    # не преобразуется. Модуль называется не больше одного раза — см.
    # `_check_url_rewrite`.
    url_rewrite: list[UrlRewrite] = Field(default_factory=list)
    registry_calls: list[RegistryCallConfig] = Field(default_factory=list)

    @field_validator("roots")
    @classmethod
    def _check_roots(cls, value: list[str | RootEntry]) -> list[str | RootEntry]:
        # У записи-объекта путь проверил её собственный валидатор.
        return [
            _repo_relative(item, "web.roots") if isinstance(item, str) else item for item in value
        ]

    @property
    def root_paths(self) -> list[str]:
        """Корни обхода фронта строками, в порядке файла."""
        return [item if isinstance(item, str) else item.path for item in self.roots]

    @field_validator("url_rewrite")
    @classmethod
    def _check_url_rewrite(cls, value: list[UrlRewrite]) -> list[UrlRewrite]:
        """Повтор модуля — отказ, а не «первая запись выигрывает».

        `rewrite_for` берёт первую запись, и вторая не применялась никогда:
        правка, вписанная ниже старой строки, выглядела сделанной, а связь
        по-прежнему расходилась на префиксе. Какая из двух задумана,
        инструмент знать не может — решает человек.
        """
        repeated = sorted(
            module for module, count in Counter(item.module for item in value).items() if count > 1
        )
        if repeated:
            raise ValueError(
                f"web.url_rewrite: модуль назван больше одного раза: {', '.join(repeated)};"
                " правило на модуль одно — оставьте одну запись"
            )
        return value

    @field_validator("modules_dir")
    @classmethod
    def _check_modules_dir(cls, value: str) -> str:
        return _repo_relative(value, "web.modules_dir") if value else value

    def rewrite_for(self, module: str) -> UrlRewrite | None:
        """Правило модуля. `None` — модуль в таблице не назван вовсе."""
        return next((item for item in self.url_rewrite if item.module == module), None)


class ArchAdapterConfig(BaseModel):
    """Подключение адаптера реестра (R04).

    Адаптер подключается **по имени**, а не ветвлением в ядре: `adapter` —
    имя из списка известных, `options` — его параметры. Ядро о существовании
    конкретного реестра конкретного репозитория не знает, и это единственный
    способ удержать инструмент настраиваемым, а не приросшим к первому
    репозиторию (Р11).

    `options` намеренно не типизирован: у каждого адаптера свои параметры,
    и он же проверяет их строго — неизвестный параметр отвергается при
    запуске, а не игнорируется.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)


class GraphConfig(BaseModel):
    """Секция `graph`: сборка индекса связей.

    `engine_path` не имеет значения по умолчанию намеренно, и это не мелочь:
    умолчание вида «поищем в PATH» означает запуск того, что нашлось, —
    а версия движка разбора определяет и числа, и качество разрешения вызовов.
    Пусто — отказ с указанием, что заполнить.

    `engine_sha256` пустой означает «взять ожидаемую чек-сумму из моста»:
    версия закреплена в коде, а ключ существует, чтобы её можно было сменить
    осознанно, а не чтобы её можно было не проверять.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine_path: str = ""
    engine_sha256: str = ""
    mode: Literal["fast", "moderate", "full"] = "fast"
    # Цели записи, поэтому от текущего каталога, как `out` и `worklist`:
    # индекс и кэш разбора — артефакты прогона, а не часть репозитория.
    out: str = "artifacts/graph.db"
    cache_dir: str = ".docpipe/engine-cache"

    @field_validator("cache_dir")
    @classmethod
    def _check_cache_dir(cls, value: str) -> str:
        """Как у `cache_dir` верхнего уровня, и строже в одном.

        Этот каталог мост **удаляет целиком** перед каждой сборкой
        (`Engine.index`, `clean_cache`): `..` здесь означал бы `rmtree`
        за пределами репозитория, а пустое значение или `.` — `rmtree`
        текущего каталога, то есть репозитория, из которого зовут команду.
        """
        checked = _cache_path(value, "graph.cache_dir")
        if not checked:
            raise ValueError(
                "graph.cache_dir: нужен отдельный каталог — мост очищает его перед"
                f" каждой сборкой, а {value!r} указывает на текущий каталог"
            )
        return checked


class DocpipeConfig(BaseModel):
    """Настройки прогона.

    `enrolled`, `exclude` и scope решают разные задачи, их легко перепутать:
    scope — «что я сейчас перепарсиваю» (влияет на скорость и размер диффа),
    enrolled — «что вообще входит в документацию» (влияет на состав манифеста),
    exclude — «куда не заходить вовсе» (файл не читается и символов не даёт).
    Неenrolled модули всё равно парсятся: их символы нужны для графа наследования,
    а исключённые — нет, поэтому наследование через них рвётся. Это цена за то,
    чтобы не читать чужое дерево: каталог с самим инструментом, вендоренные
    зависимости, выгрузки.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Каталоги, в которых вообще искать исходники. Сужает обход, а не состав
    # документации: что из найденного документировать, решает `enrolled`.
    # Отличается от `--scope` тем, что постоянен и не делает манифест частичным;
    # при обоих заданных сужения складываются.
    roots: list[str] = Field(default_factory=lambda: ["."])

    # Область документации — два решения человека: «берём» и «не берём».
    # Читаются только через `enrolled_globs`/`not_enrolled_globs` и `scope_of`:
    # запись бывает строкой и объектом с причиной. Модуль, совпавший с обоими
    # списками, — отказ прогона, а не приоритет: противоречие двух решений
    # человека — дефект, и правило «кто важнее» спрятало бы его.
    enrolled: list[EnrolledItem] = Field(default_factory=_all_modules)
    not_enrolled: list[NotEnrolled] = Field(default_factory=list)
    exclude: list[ExcludeItem] = Field(default_factory=list)
    domains: dict[str, str] = Field(default_factory=dict)
    rules: str = "rules/rules.yaml"
    out: str = "artifacts/doc-tree.json"
    cache_dir: str = ".docpipe/cache"

    # Шаг 2. Все со значениями по умолчанию: модель `extra="forbid"`, но
    # существующие конфигурации обязаны продолжать работать.
    templates: str = "templates"
    ownership: str | None = None
    docs_root: str = "docs"

    # Каталог технической документации ВНУТРИ `docs_root`, а не путь целиком.
    # Пара вместо одного значения выбрана ради инварианта «то, что пишет
    # `materialize`, лежит там, где ищет `docs status`»: он держится структурно
    # и нарушить его нельзя. Одним значением (`modules_root: "other/modules"`
    # при `docs_root: "docs"`) прогон писал бы документы туда, где их никто
    # не ищет: каждый навсегда остался бы `missing` и переписывался бы заново
    # на каждом прогоне, молча.
    modules_dir: str = "modules"

    docs_scan_exclude: list[str] = Field(default_factory=list)

    # Раскладка документов. Обе — перестановка одной и той же тройки
    # (модуль, вид, slug), поэтому на **коллизии не влияют вообще**: путь
    # в каждой из них однозначно определяется тройкой, и множество конфликтов
    # у них общее (на ABP — одни и те же 19 путей на 47 узлов). Выбирать
    # приходится не по безопасности, а по тому, как дерево читают:
    #
    #   kind-first    docs/modules/gridservices/Sbt.Cf.Grid.AutoConclusion/x.md
    #   module-first  docs/modules/Sbt.Cf.Grid.AutoConclusion/gridservices/x.md
    #
    # `kind-first` собирает все сущности одного вида в один каталог — это то,
    # ради чего он и выбран по умолчанию: «покажи все grid-сервисы» становится
    # обходом каталога. Плата ровно одна: по префиксу пути больше не выбрать
    # модуль целиком (`docs status МАНИФЕСТ docs/modules/Sample.Common`),
    # потому что его документы разложены по каталогам видов. `module-first`
    # оставлен параметром для тех, кому важнее эта выборка, и как способ
    # не переезжать уже написанным деревом: смена значения меняет `doc_path`
    # у всех узлов сразу.
    doc_layout: DocLayout = "kind-first"

    # Бизнес-слой. `business_root` — параметр, а не константа: на АС CF
    # настройка инструмента лежит в своём каталоге внутри `docs/` (его задаёт
    # `install.sh --config-dir`), и первый каталог бизнес-документов заведут
    # рядом. Когда он понадобится другим командам,
    # его вынесут; при параметре это правка одной строки, при константе —
    # правка `doc_path` в каждом документе.
    registries: str | None = None
    business_root: str = "business"

    # Нормализованный реестр архитектурных элементов (R03): точки входа,
    # узлы данных, швы, слои. Вход инструмента, поэтому путь разрешается
    # двумя ступенями, как `rules` и `templates`. Значения по умолчанию нет
    # намеренно: реестр — решение о репозитории, а не файл, который заводится
    # сам. Пустой реестр валиден, а вот выдуманный путь к нему — нет.
    # Интерфейсы, объявляющие диспетчеризацию по типу запроса:
    # `IRequestHandler`, `INotificationHandler`, их аналоги в других
    # библиотеках и в самодельных шинах. Пустой список — умолчание: механика
    # общая, а имена интерфейсов у каждого репозитория свои, и угадывать
    # их значит завести правило, которое где-то сработает не на том.
    dispatch_interfaces: list[NamedItem] = Field(default_factory=list)

    # Самодельные обёртки регистрации в контейнере: `AddSingletonAs`,
    # `AddCashflowServices`. Стандартные `AddScoped`/`AddSingleton`/
    # `AddTransient`/`AddHostedService` известны и без настройки; угадывать
    # остальные нельзя — имя, придуманное по одному репозиторию, на другом
    # совпадёт не с тем вызовом. Пустой список — умолчание, и репозиторий
    # со своей обёрткой без этого ключа даёт ноль регистраций молча.
    di_methods: list[NamedItem] = Field(default_factory=list)

    arch: str | None = None

    # Адаптеры реестров: читают исходные реестры при каждой сборке. Пустой
    # список — рабочее состояние: снимок в `arch` остаётся способом, а на
    # репозитории, который видишь впервые, он единственный.
    arch_adapters: list[ArchAdapterConfig] = Field(default_factory=list)

    # Сборка индекса связей. Секция необязательна: репозиторий, для которого
    # граф не собирают, её не заводит.
    graph: GraphConfig = Field(default_factory=lambda: GraphConfig())

    # Шаг 3. Куда `docpipe worklist` кладёт очередь для внешнего исполнителя.
    # Путь относительно текущего каталога, как `out`, а не относительно `--root`:
    # очередь — артефакт прогона, а не часть документации, и на АС CF она лежит
    # рядом с манифестом, вне дерева документов.
    worklist: str = "artifacts/doc-worklist.json"

    # Шаг `web`. Секция необязательна: репозиторий без фронта её не заводит,
    # и умолчания дают рабочий прогон на репозитории, где фронт один.
    web: WebConfig = Field(default_factory=lambda: WebConfig())

    @field_validator("docs_root", "modules_dir", "business_root")
    @classmethod
    def _check_repo_relative(cls, value: str, info: Any) -> str:
        return _repo_relative(value, str(info.field_name))

    # `cache_dir` проверяется мягче остальных, и это не послабление, а разные
    # вопросы. У трёх ключей выше значение попадает в `doc_path` каждого узла,
    # и абсолютный путь там делает манифест непереносимым между машинами.
    # Кэш разбора в `doc_path` не попадает вообще: он склеивается с `--root`
    # (`Path(root) / value`), и абсолютное значение эту склейку выигрывает.
    #
    # Запрещено это было ради того, чтобы кэш не уезжал за пределы репозитория
    # **молча**. Но на закрытом контуре его туда надо унести намеренно: дерево
    # продукта не место для гигабайта машинного кэша, а каталог инструмента
    # лежит вне репозитория. Написанный руками абсолютный путь — не молчаливый
    # побег, а решение, и отличается оно тем, что видно в конфигурации.
    #
    # `..` остаётся запрещённым и в этом случае: он даёт непредсказуемое место
    # в зависимости от `--root`, то есть ровно тот побег, от которого правило
    # и заводилось. Кэш при этом можно делить между репозиториями безопасно:
    # запись адресуется хэшем содержимого файла, а не его путём.
    @field_validator("cache_dir")
    @classmethod
    def _check_cache_dir(cls, value: str) -> str:
        return _cache_path(value, "cache_dir")

    @field_validator("roots")
    @classmethod
    def _check_roots(cls, value: list[str]) -> list[str]:
        return [_repo_relative(item, "roots") for item in value]

    @model_validator(mode="after")
    def _check_adapter_ids(self) -> "DocpipeConfig":
        """`id` адаптера уникален: им подписаны ошибки и счётчики сборки.

        `collect` печатает «адаптер: сколько записей» и «адаптер: ошибка»
        по `id`, а не по позиции в списке. Два адаптера с одним `id` дают
        две строки, которые не различить, и ошибка одного читается как
        ошибка другого.
        """
        repeated = sorted(
            adapter_id
            for adapter_id, count in Counter(item.id for item in self.arch_adapters).items()
            if count > 1
        )
        if repeated:
            raise ValueError(f"arch_adapters: повтор id {', '.join(map(repr, repeated))}")
        return self

    # Нормализованные свойства — единственный путь чтения ключей со второй
    # формой. Строки, в порядке файла: порядок для потребителей безразличен
    # (они сортируют сами), а сохранённый порядок позволяет сообщению об ошибке
    # назвать запись так, как её найдёт человек.

    @property
    def enrolled_globs(self) -> list[str]:
        return _globs(self.enrolled)

    @property
    def not_enrolled_globs(self) -> list[str]:
        return [item.glob for item in self.not_enrolled]

    @property
    def exclude_patterns(self) -> list[str]:
        return _globs(self.exclude)

    @property
    def di_method_names(self) -> list[str]:
        return _names(self.di_methods)

    @property
    def dispatch_interface_names(self) -> list[str]:
        return _names(self.dispatch_interfaces)

    @property
    def enrolled_is_explicit(self) -> bool:
        """`enrolled` задан, а не взят по умолчанию `["**"]`.

        Умолчание — не решение человека: оно включает всё, пока о модуле
        ничего не сказано. Поэтому при нём `not_enrolled` вырезает модуль
        без противоречия, а `undecided` не бывает вовсе.
        """
        return "enrolled" in self.model_fields_set

    @property
    def modules_root(self) -> str:
        """Префикс `doc_path` технических документов.

        Собирается здесь, а не в `tree.py`: пара полей и вывод из неё обязаны
        жить в одном месте, иначе шаг 1 и шаг 2 однажды соберут её по-разному.
        """
        return posixpath.join(self.docs_root, self.modules_dir).strip("/")

    @property
    def web_modules_root(self) -> str:
        """Префикс `doc_path` документов фронта.

        Пустой `web.modules_dir` значит «там же, где бэкенд», и это умолчание:
        ключ появился позже, а конфигурации без него обязаны давать те же пути.

        Ветка внутри `docs_root`, а не путь целиком, — по той же причине, что
        и у `modules_dir`: инвариант «`materialize` пишет туда, где ищет
        `docs status`» держится структурно, потому что обход документов идёт
        от `docs_root` и накрывает обе ветки разом.
        """
        return posixpath.join(self.docs_root, self.web.modules_dir or self.modules_dir).strip("/")


# --------------------------------------------------------------------------------------
# Область документации: решение по модулю
# --------------------------------------------------------------------------------------

Scope = Literal["enrolled", "not_enrolled", "undecided"]


class ScopeClash(NamedTuple):
    """Модуль и шаблоны обоих списков, с которыми он совпал."""

    module: str
    enrolled: tuple[str, ...]
    not_enrolled: tuple[str, ...]


class ScopeConflict(ValueError):
    """Модуль под `enrolled` и `not_enrolled` сразу — отказ прогона.

    `ValueError`, потому что это ошибка настройки, а не сбой: команды отвечают
    на неё кодом 2 тем же путём, что на опечатку в `docpipe.yaml`. Конфликтов
    в одном отказе столько, сколько нашлось: агент правит их одним заходом,
    а не по одному на прогон.
    """

    def __init__(self, clashes: Iterable[ScopeClash]) -> None:
        self.clashes = tuple(sorted(clashes))
        lines = "\n".join(
            f"  {clash.module}: enrolled {', '.join(map(repr, clash.enrolled))}"
            f" и not_enrolled {', '.join(map(repr, clash.not_enrolled))}"
            for clash in self.clashes
        )
        super().__init__(
            "модуль под `enrolled` и `not_enrolled` сразу — два решения противоречат"
            f" друг другу:\n{lines}\n"
            "Оставьте модуль в одном списке: сузьте шаблон `enrolled` или уберите запись"
            " `not_enrolled`. Приоритета между списками нет намеренно."
        )


def scope_of(project_file: str, settings: DocpipeConfig) -> Scope:
    """Решение об области для модуля с этим `.csproj`.

    `undecided` — модуль не совпал ни с одним списком, и бывает только при
    явно заданном `enrolled`: умолчание `["**"]` накрывает всё и решением
    человека не является, поэтому при нём `not_enrolled` просто вырезает
    модуль. При явном `enrolled` совпадение с обоими списками — `ScopeConflict`.

    Отличить `not_enrolled` от `undecided` — ради этого ключ и заведён:
    «решили не брать» и «ещё не смотрели» в манифесте одинаково
    `enrolled: false`, а для настройки это противоположные состояния.
    """
    taken = tuple(glob for glob in settings.enrolled_globs if matches_glob(project_file, glob))
    dropped = tuple(
        glob for glob in settings.not_enrolled_globs if matches_glob(project_file, glob)
    )
    if dropped:
        if taken and settings.enrolled_is_explicit:
            raise ScopeConflict([ScopeClash(project_file, taken, dropped)])
        return "not_enrolled"
    return "enrolled" if taken else "undecided"


def candidate_inputs(value: str | Path, config: Path | None) -> list[Path]:
    """Кандидаты `resolve_input` по порядку: текущий каталог, затем каталог `docpipe.yaml`.

    Вынесено отдельной функцией, чтобы сообщение об отказе могло перечислить
    всё, что пробовали: «не найден» с одним путём заставляет гадать, искался ли
    второй, и это ровно тот случай, когда виновата раскладка поставки,
    а не команда.
    """
    here = Path(value)
    if here.is_absolute() or config is None:
        return [here]

    beside = config.parent / here
    return [here] if beside == here else [here, beside]


def resolve_input(value: str | Path, config: Path | None) -> Path:
    """Путь к файлу инструмента, названному ключом `docpipe.yaml`.

    Сначала от текущего каталога, потом от каталога самого `docpipe.yaml`;
    выигрывает первый существующий.

    **Порядок обратным быть не может.** Конфигурации, лежащие в корне
    репозитория, писались с путями от текущего каталога, и там он совпадает
    с каталогом конфигурации. Отдай приоритет второй ступени — на раскладках,
    где каталоги разные, прогон не упал бы, а молча взял другой набор правил
    или шаблонов. Тихо взятый не тот набор дороже отказа.

    Вторая ступень нужна потому, что конфигурация инструмента лежит не в корне
    (на АС CF — каталог `install.sh --config-dir`), а путь внутри неё естественно писать
    относительно неё же: это единственный вид пути, который автор конфигурации
    может проверить глазами, не помня, из какого каталога зовут команду.

    Не найдено нигде — возвращается первый кандидат: сообщение об ошибке
    обязано называть то, что человек написал в конфигурации, а не последнее
    из того, что перебрал инструмент.

    Применяется только к **входам** — файлам, которые инструмент читает
    (`rules`, `web.rules`, `templates`, `registries`, `ownership`). Цели записи
    (`out`, `web.out`, `web.link_out`, `worklist`) второй ступени не получают:
    «первый существующий» для них не значит ничего, а угадывать, куда писать,
    инструмент не должен.
    """
    candidates = candidate_inputs(value, config)
    return next((path for path in candidates if path.exists()), candidates[0])


def load_config(path: Path | None) -> DocpipeConfig:
    """Загрузить конфигурацию; при `None` вернуть значения по умолчанию.

    Пустой YAML-файл равнозначен отсутствию файла.
    """
    if path is None:
        return DocpipeConfig()

    if not path.is_file():
        raise FileNotFoundError(f"Файл конфигурации не найден: {path}")

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return DocpipeConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"Конфигурация должна быть словарём, получено: {type(raw).__name__}")

    _reject_empty_lists(raw, path)

    # Секция, у которой закомментированы все записи, разбирается YAML как `None`.
    # Без этого фильтра конфигурация падала бы с «Input should be a valid dictionary»:
    # закомментировать записи — самое обычное действие при настройке, и оно
    # не должно выглядеть как поломка. Ключи-списки сюда уже не доходят —
    # у них `None` отвергнут выше.
    return DocpipeConfig.model_validate({k: v for k, v in raw.items() if v is not None})


def _list_fields(model: type[BaseModel]) -> dict[str, Any]:
    """Поля-списки модели и их умолчания.

    Строится по аннотациям, а не перечислением руками: ключ-список,
    добавленный в модель, без этого остался бы без проверки на `None`,
    и ловушка вернулась бы на нём молча.
    """
    return {
        name: field.get_default(call_default_factory=True)
        for name, field in model.model_fields.items()
        if get_origin(field.annotation) is list
    }


def _reject_empty_lists(raw: dict[str, Any], path: Path) -> None:
    """Ключ-список без элементов — отказ, а не умолчание.

    `enrolled:`, под которым остались одни комментарии, YAML отдаёт как `None`.
    Выброшенный, такой ключ получает умолчание, а у `enrolled` оно `["**"]`:
    каждый модуль включён, без единого сообщения. Агент, «временно выключивший»
    строку комментарием, получил бы противоположное задуманному. Поэтому `None`
    у списка — отказ, и сообщение называет обе честные записи: `[]`, если
    список и правда пуст, и удаление ключа, если нужно умолчание.

    Вложенные секции (`web`, `graph`) проверяются так же: иначе у них `None`
    давал бы `ValidationError` без подсказки.
    """
    places: list[tuple[str, dict[str, Any], type[BaseModel]]] = [("", raw, DocpipeConfig)]
    for name, field in DocpipeConfig.model_fields.items():
        section, nested = raw.get(name), field.annotation
        if isinstance(section, dict) and isinstance(nested, type) and issubclass(nested, BaseModel):
            places.append((f"{name}.", section, nested))

    for prefix, values, model in places:
        for name, default in sorted(_list_fields(model).items()):
            if name not in values or values[name] is not None:
                continue
            where = f" в секции `{prefix.rstrip('.')}`" if prefix else ""
            shown = json.dumps(default, ensure_ascii=False, default=str)
            raise ValueError(
                f"{path}: `{prefix}{name}:` без элементов — напишите `{name}: []`{where}"
                f" или удалите ключ (умолчание: {shown})"
            )
