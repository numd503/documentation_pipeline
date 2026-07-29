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

# --------------------------------------------------------------------------------------
# Перечисления, вынесенные в псевдонимы: используются на нескольких уровнях сразу.
# --------------------------------------------------------------------------------------

TypeKind = Literal["class", "interface", "struct", "record", "record_struct", "enum"]
MemberKind = Literal["method", "property", "field", "constructor", "event"]
Lifetime = Literal["scoped", "singleton", "transient", "hosted", "unknown"]
Confidence = Literal["high", "medium", "low"]
DependencyVia = Literal["constructor", "di", "inheritance"]
RelationKind = Literal["implements", "implemented_by", "uses"]

# Аннотация обязательна: без неё константа выводится как `str`
# и не подходит полю `Literal["1.1"]`.
SCHEMA_VERSION: Final[Literal["1.1"]] = "1.1"


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


class FileParseResult(_Base):
    """Полный результат разбора одного файла. Единица кэширования."""

    path: str
    content_hash: str
    usings: list[str] = Field(default_factory=list)
    global_usings: list[str] = Field(default_factory=list)
    declarations: list[RawDeclaration] = Field(default_factory=list)
    di_registrations: list[DiRegistration] = Field(default_factory=list)
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
    """Проект .NET (`.csproj`).

    `enrolled` отвечает на вопрос «входит ли модуль в документацию».
    Неenrolled модули всё равно парсятся — их символы нужны для графа
    наследования, — но узлов документации не порождают.

    `id` строится от пути, а не от имени: имена проектов не уникальны
    (в ABP 39 повторов, три разных `MyCompanyName.MyProjectName.csproj`),
    и ключ `module:{name}` молча склеил бы разные модули в один.

    `project_references` — тоже пути, сравнимые с полем `csproj` другого
    модуля. Сопоставление по имени проекта при неуникальных именах давало бы
    ребро графа в произвольный из одноимённых модулей.
    """

    id: str
    name: str
    csproj: str
    target_frameworks: list[str] = Field(default_factory=list)
    project_references: list[str] = Field(default_factory=list)
    package_references: list[str] = Field(default_factory=list)
    domain: str
    enrolled: bool


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


class ParserVersions(_Base):
    """Версии парсера. Апгрейд грамматики может законно изменить вывод —
    поэтому версии попадают в манифест и видны в диффе."""

    tree_sitter: str
    grammar_c_sharp: str


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

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    ruleset_version: str
    parser: ParserVersions
    partial: PartialInfo | None = None
    modules: list[Module] = Field(default_factory=list)
    nodes: list[DocNode] = Field(default_factory=list)


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
