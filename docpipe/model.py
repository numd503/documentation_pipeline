"""Модели данных всего пайплайна.

Три уровня, соответствующие потоку данных:

1. **Парсер** — `Attribute`, `Member`, `RawDeclaration`, `FileParseResult`.
   Чистый синтаксис одного файла, без какой-либо межфайловой логики.
2. **Резолв** — `Symbol`. Объявления слиты по FQN, имена базовых типов резолвнуты,
   посчитано транзитивное замыкание наследования.
3. **Манифест** — `DocNode`, `Module`, `Manifest`. То, что уходит на шаги 2 и 3.

Все модели `frozen`: пайплайн детерминированный, мутация уже собранных структур
почти всегда означает ошибку. `extra="forbid"` ловит опечатки при загрузке манифеста.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.route import RouteKey

# --------------------------------------------------------------------------------------
# Перечисления, вынесенные в псевдонимы: используются на нескольких уровнях сразу.
# --------------------------------------------------------------------------------------

# Вид объявления — **синтаксический**, а не классификационный. `component`,
# `service`, `guard` и прочее — это `DocNode.kind`, результат работы правил;
# положить их сюда значило бы, что решение классификации принимает парсер,
# то есть тот, кто правил не читает. Из TypeScript добавлены две формы,
# которых у C# нет: экспортируемая функция и экспортируемая константа —
# в Angular так объявлены функциональные интерцепторы, guard'ы и таблицы роутов.
TypeKind = Literal[
    "class", "interface", "struct", "record", "record_struct", "enum", "function", "const"
]
MemberKind = Literal["method", "property", "field", "constructor", "event"]
Lifetime = Literal["scoped", "singleton", "transient", "hosted", "unknown"]
Confidence = Literal["high", "medium", "low"]
DependencyVia = Literal["constructor", "di", "inheritance"]
RelationKind = Literal["implements", "implemented_by", "uses"]

Lang = Literal["cs", "ts"]

# Аннотация обязательна: без неё константа выводится как `str`
# и не подходит полю `Literal["2.0"]`.
#
# 2.0 — переименование `Module.csproj` в `Module.project_file` и обязательное
# `Module.lang`. Схема стала общей для двух языков, и старое имя врало бы
# в каждом отчёте по фронту.
SCHEMA_VERSION: Final[Literal["2.0"]] = "2.0"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------------------
# Уровень 1: парсер
# --------------------------------------------------------------------------------------


class Attribute(_Base):
    """Атрибут C# вида `[Route("api/v1", Name = "x")]`.

    `name` — без суффикса `Attribute`: `[RouteAttribute]` и `[Route]` неразличимы.
    Строковые значения хранятся без кавычек.
    """

    name: str
    args: list[str] = Field(default_factory=list)
    named_args: dict[str, str] = Field(default_factory=dict)


class SourceSpan(_Base):
    """Диапазон строк в файле. Пути репо-относительные POSIX, строки 1-based."""

    path: str
    start: int
    end: int


class Member(_Base):
    """Член типа: метод, свойство, поле, конструктор или событие."""

    name: str
    kind: MemberKind
    signature: str
    modifiers: list[str] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    line: int
    end_line: int
    xml_doc: str | None = None


class RawDeclaration(_Base):
    """Одно объявление типа, как оно записано в конкретном файле.

    Именно «в файле», а не «в решении»: `partial class` даёт несколько
    `RawDeclaration` с одинаковым FQN, которые сливаются в один `Symbol` на T10.
    `base_types` здесь — сырой текст (`IPricingProvider<string>`), без резолва.
    """

    name: str
    type_kind: TypeKind
    namespace: str
    containing_type: str | None = None
    type_parameters: list[str] = Field(default_factory=list)
    modifiers: list[str] = Field(default_factory=list)
    base_types: list[str] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    span: SourceSpan
    xml_doc: str | None = None
    decl_hash: str = ""
    """Хэш текста объявления с нормализованными пробелами.

    Нормализация обязательна: без неё прогон `dotnet format` по репозиторию
    изменил бы хэш у всех типов сразу, и всё дерево документации стало бы
    требовать пересмотра. Та же логика, по которой `signature_hash`
    не включает номера строк.
    """


class DiRegistration(_Base):
    """Регистрация в контейнере: `services.AddScoped<IFoo, Foo>()`.

    `confidence` ниже `high` означает, что тип извлечён из лямбды или выражения
    и может быть неточным. Динамические регистрации (сканирование сборок)
    синтаксически не видны и сюда не попадают вовсе.
    """

    service_type: str
    impl_type: str | None = None
    lifetime: Lifetime
    confidence: Confidence
    file: str
    line: int


class DispatchDeclaration(_Base):
    """Объявленная диспетчеризация по типу: «этот тип обслуживает такой запрос».

    Форма `class Handler : IRequestHandler<GetOrders, Result>` встречается
    у медиаторов, шин команд и обработчиков сообщений. Извлекается она вместе
    с остальными объявлениями и **по списку интерфейсов из конфигурации**:
    зашивать сюда имя конкретной библиотеки нельзя — на другом репозитории
    интерфейс называется иначе, а механика та же.

    Ключевое здесь то же, что и в цепочке фронта: **тип запроса живёт
    на ребре, а не на вызове**. Один и тот же метод обработчика зовут
    и диспетчер, и код напрямую.
    """

    handler_fqn: str
    interface: str
    request_type: str
    module: str
    file: str
    line: int


class ImportedName(_Base):
    """Одно имя, пришедшее из другого модуля.

    Два поля, а не одно: `import { Base as Api }` виден в этом файле как `Api`,
    а в модуле-источнике объявлен как `Base`. Хранить одно значит либо не найти
    объявление, либо не узнать имя в коде — и то, и другое рвёт цепочку
    наследования молча.
    """

    local: str
    imported: str


class ModuleImport(_Base):
    """Связь файла с другим модулем: импорт либо переэкспорт.

    Переэкспорт (`export * from './x'`) хранится здесь же и отличается флагом:
    для резолва это одно и то же действие — «имя объявлено не тут», — а бочка
    из `index.ts` встречается ровно там, где импорт.
    """

    source: str  # спецификатор как написан: './x', '@shared', 'rxjs'
    names: list[ImportedName] = Field(default_factory=list)
    star: bool = False  # `import * as ns` либо `export * from`
    re_export: bool = False
    line: int


class FileParseResult(_Base):
    """Полный результат разбора одного файла. Единица кэширования."""

    path: str
    content_hash: str
    usings: list[str] = Field(default_factory=list)
    global_usings: list[str] = Field(default_factory=list)
    imports: list[ModuleImport] = Field(default_factory=list)
    declarations: list[RawDeclaration] = Field(default_factory=list)
    di_registrations: list[DiRegistration] = Field(default_factory=list)

    # Объявленная диспетчеризация по типу запроса. Пусто, пока в конфигурации
    # не назван ни один интерфейс: механика общая, а имена интерфейсов
    # у каждого репозитория свои.
    dispatch_handlers: list[DispatchDeclaration] = Field(default_factory=list)
    parse_errors: int = 0


# --------------------------------------------------------------------------------------
# Уровень 2: резолв
# --------------------------------------------------------------------------------------


class Symbol(_Base):
    """Тип решения целиком: объявления слиты, базовые типы резолвнуты.

    `sources` — список, потому что `partial class` живёт в нескольких файлах.
    `base_types` содержит FQN там, где резолв удался, и сырое имя там, где нет
    (внешние типы вроде `ControllerBase` не резолвятся принципиально — правила
    матчатся именно по таким именам).
    """

    fqn: str
    name: str
    type_kind: TypeKind
    namespace: str
    module: str
    modifiers: list[str] = Field(default_factory=list)
    type_parameters: list[str] = Field(default_factory=list)
    base_types: list[str] = Field(default_factory=list)
    base_types_raw: list[str] = Field(default_factory=list)
    base_type_closure: list[str] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    sources: list[SourceSpan] = Field(default_factory=list)
    xml_doc: str | None = None
    ambiguous: bool = False
    impl_hash: str = ""
    """Хэш текстов всех объявлений типа.

    Отвечает на вопрос, которого не задаёт `signature_hash`: «переписано тело
    при том же контракте». Описание «как работает» зависит именно от тела,
    и без второго сигнала оно устаревает молча.

    `SourceSpan` не включает XML-doc — комментарий является предшествующим
    соседом объявления, а не его потомком. Поэтому правка XML-doc `impl_hash`
    не меняет. Это приемлемо; менять устройство span ради этого не нужно.
    """


# --------------------------------------------------------------------------------------
# Уровень 3: манифест
# --------------------------------------------------------------------------------------


class Endpoint(_Base):
    """HTTP-эндпоинт контроллера со склеенным маршрутом."""

    http_method: str
    route: str
    member: str
    line: int


class Dependency(_Base):
    """Зависимость узла: параметр конструктора, DI-регистрация или наследование."""

    target: str
    via: DependencyVia
    confidence: Confidence


class Relation(_Base):
    """Связь между узлами документации."""

    target: str
    relation: RelationKind


class Module(_Base):
    """Единица сборки: проект .NET (`.csproj`) либо модуль фронта.

    `project_file` — файл, которым модуль объявлен: `.csproj` у .NET,
    `angular.json`, `project.json` или `package.json` у фронта. Имя поля
    не называет расширение намеренно: `csproj` у TypeScript-модуля врал бы
    в каждом отчёте, а отчёты идут в журнал и в CI.

    `lang` обязателен и значения по умолчанию не имеет. Умолчание `"cs"`
    означало бы, что сборщик модулей фронта, забывший его выставить, отдаёт
    манифест, в котором все модули объявлены .NET-овскими, — и заметить это
    было бы нечем: остальные поля у него правильные.

    `enrolled` отвечает на вопрос «входит ли модуль в документацию».
    Неenrolled модули всё равно парсятся — их символы нужны для графа
    наследования, — но узлов документации не порождают.

    `id` строится от пути, а не от имени: имена проектов не уникальны
    (в ABP 39 повторов, три разных `MyCompanyName.MyProjectName.csproj`),
    и ключ `module:{name}` молча склеил бы разные модули в один. На фронте
    то же самое: у nx имя проекта уникально только внутри одного workspace.

    `project_references` — тоже пути, сравнимые с полем `project_file` другого
    модуля. Сопоставление по имени проекта при неуникальных именах давало бы
    ребро графа в произвольный из одноимённых модулей.
    """

    id: str
    name: str
    project_file: str
    lang: Lang
    target_frameworks: list[str] = Field(default_factory=list)
    project_references: list[str] = Field(default_factory=list)
    package_references: list[str] = Field(default_factory=list)
    domain: str
    enrolled: bool


class WebCall(_Base):
    """HTTP-вызов из кода фронта: кто зовёт, куда и с какой уверенностью.

    Ключ — пара `(метод, маршрут)` с различителем, а не ссылка на узел
    бэкенда: идентификатор узла содержит путь проекта и FQN, и переименование
    контроллера рвало бы связь при неизменном HTTP-контракте.

    `via_action` — тип экшена NGXS, если до вызова дошли по цепочке
    «компонент диспатчит → стейт обрабатывает → сервис зовёт». Строка типа
    экшена — единственное стабильное литеральное звено этой цепочки: имя
    класса меняется при рефакторинге, строка нет.

    `member` — член, в теле которого вызов записан. Без него «страница зовёт
    этот эндпоинт» неотличимо от «страница внедрила сервис, у которого есть
    такой метод»: на боевом модуле разница между этими утверждениями —
    51 вызов против единиц. Пустая строка значит «записан вне члена»
    (в теле фабрики, в поле модуля), и это состояние, а не неизвестность.
    """

    file: str
    line: int
    key: RouteKey
    confidence: Confidence
    via_action: str | None = None
    member: str = ""


class Usage(_Base):
    """Обращение узла к члену другого узла: ребро графа вызовов.

    Отличается от `Dependency` тем же, чем документ отличается от инвентаря:
    зависимость говорит «внедрил сервис», обращение — «зову вот этот метод».
    На боевом модуле разница между ними — 51 эндпоинт против единиц.

    `via` — член источника, из которого зовут; пустая строка значит «вне
    членов». Он же связывает цепочку: обращение из метода стейта, помеченного
    `@Action`, — это уже путь «страница → экшен → стейт → сервис».
    """

    target: str  # FQN узла-цели
    member: str  # член цели, к которому обращаются
    via: str = ""  # член источника, где обращение записано

    # Тип экшена NGXS, если до цели дошли диспатчем: `[Inner Debt] Load`.
    # Строка, а не имя класса: она переживает переименование.
    #
    # Живёт на ребре, а не на вызове, и это не мелочь. Один и тот же метод
    # сервиса зовут и обработчик экшена, и компонент напрямую; пометив вызов,
    # инструмент утверждал бы, что до него всегда доходят через стор.
    action: str = ""


class RouteEntry(_Base):
    """Ветка таблицы роутов: полный путь страницы и компонент на нём.

    `route_unresolved` — путь собрать не удалось (сегмент задан выражением).
    Это **состояние**, а не отсутствие: пропав из вывода, такая ветка занизила
    бы знаменатель покрытия страниц, и отчёт показал бы успех там, где его нет.

    `source` и `table` называют, **откуда** маршрут взялся: файл с таблицей
    и имя массива. Вид `page` — единственный, который выдаётся не правилом,
    а повышением по этой таблице, и без пары полей проверить его нечем:
    дерево собирается межфайлово, поэтому по узлу не видно, какой из восьми
    массивов `Routes` привёл сюда.
    """

    path: str
    component: str
    route_unresolved: bool = False
    source: str = ""
    table: str = ""


class DocNode(_Base):
    """Один документ будущей документации.

    `matched_rules` содержит **все** совпавшие правила, а не только победившее:
    без этого классификация превращается в чёрный ящик.

    `signature_hash` считается от нормализованных сигнатур и намеренно
    **не включает** номера строк и пути — переформатирование файла не должно
    заставлять агента на шаге 3 перегенерировать документ.
    """

    id: str
    kind: str
    template: str
    title: str
    doc_path: str
    parent: str | None = None
    module: str
    domain: str
    symbol: Symbol | None = None
    endpoints: list[Endpoint] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    related: list[Relation] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    signature_hash: str
    impl_hash: str = ""

    # Шаг `web`. У узлов .NET пусты всегда: поля общей модели, а не отдельной.
    # Держать два `DocNode` нельзя — на них стоят `materialize`, `docs status`
    # и бизнес-слой, и каждый из трёх пришлось бы учить обоим типам.
    web_calls: list[WebCall] = Field(default_factory=list)
    routes: list[RouteEntry] = Field(default_factory=list)
    uses: list[Usage] = Field(default_factory=list)

    # Идентификатор страницы, внутри документа которой этот узел описывается.
    # Пустая строка — узел документируется сам по себе: до него дотягивается
    # либо несколько страниц, либо ни одной. Порога нет: «общий» — это
    # «появился второй потребитель», а не «потребителей больше N».
    absorbed_by: str = ""


class ParserVersions(_Base):
    """Версии парсера. Апгрейд грамматики может законно изменить вывод —
    поэтому версии попадают в манифест и видны в диффе.

    Обе грамматики необязательны: манифест .NET не знает про TypeScript,
    манифест фронта — про C#. Версия чужой грамматики в нём была бы шумом,
    а `null` читается однозначно. Ключ кэша строится по этой же структуре,
    поэтому апгрейд грамматики кэш сбрасывает — иначе прогон отдал бы разбор,
    сделанный старой версией, без единого сообщения.
    """

    tree_sitter: str
    grammar_c_sharp: str | None = None
    grammar_typescript: str | None = None


class PartialInfo(_Base):
    """Пометка о том, что манифест собран скоуп-прогоном и вне scope устарел."""

    scope: list[str]
    outside_from_cache: bool


class Manifest(_Base):
    """Артефакт шага 1. Не содержит ни одного недетерминированного поля.

    Метаданные прогона (время, хост, длительность) живут в отдельном
    `RunMeta` — благодаря этому тест на детерминизм сводится к побайтовому
    сравнению файлов без логики исключения полей.
    """

    schema_version: Literal["2.0"] = SCHEMA_VERSION
    ruleset_version: str
    parser: ParserVersions
    partial: PartialInfo | None = None
    modules: list[Module] = Field(default_factory=list)
    nodes: list[DocNode] = Field(default_factory=list)

    # Регистрации контейнера — такой же извлечённый факт о коде, как эндпоинты
    # контроллеров, и извлекаются они тем же проходом. Раньше они жили внутри
    # сборки узлов и выбрасывались; теперь остаются в манифесте, потому что
    # связывание вызовов через интерфейс (G04) без них не делается вовсе,
    # а второй разбор тех же файлов ради того же факта — это удвоенный проход
    # по репозиторию.
    di_registrations: list[DiRegistration] = Field(default_factory=list)

    # Объявленная диспетчеризация по типу запроса. Пусто, пока в конфигурации
    # не назван ни один интерфейс: механика общая, а имена интерфейсов
    # у каждого репозитория свои.
    dispatch_handlers: list[DispatchDeclaration] = Field(default_factory=list)


class RunMeta(_Base):
    """Сидкар-файл `<manifest>.run.json`. Всё недетерминированное — здесь.

    `parse_error_files` — пути, где разбор дал ошибки и **ни одного** объявления.
    Это единственный внешний признак того, что директива препроцессора внутри
    выражения уничтожила тип целиком (см. findings-abp.md), поэтому список
    нужен `validate` на T20, а не просто счётчик.
    """

    generated_at: str
    host: str
    duration_seconds: float
    docpipe_version: str
    stats: dict[str, int] = Field(default_factory=dict)
    parse_error_files: list[str] = Field(default_factory=list)
