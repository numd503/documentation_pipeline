# План реализации парсера `docpipe` — шаг 1

Исполнительный план для агента-реализатора. Архитектурный контекст: [`parser-architecture.md`](parser-architecture.md).

---

## Как работать с этим планом

1. Задачи выполняются **строго по порядку**. Каждая задача опирается на предыдущие.
2. Задача считается закрытой, только когда **её команда проверки проходит зелёной**.
   Не переходи к следующей задаче с падающими тестами.
3. Перед каждым коммитом прогоняй: `uv run ruff check . && uv run ruff format --check . && uv run mypy docpipe && uv run pytest -q`.
4. **Ничего не додумывай.** Если в спецификации задачи чего-то нет — реализуй минимально
   необходимое для прохождения критериев приёмки. Не добавляй возможностей «на будущее».
5. Не меняй файлы, созданные предыдущими задачами, если задача явно этого не требует.
6. Все сообщения об ошибках и docstring — на русском; имена в коде — на английском.

---

## Глобальные соглашения (действуют во всех задачах)

**Пути.** Внутри всех структур данных пути — репо-относительные, с прямыми слэшами (`/`),
даже на Windows. Абсолютные пути не попадают в манифест никогда.

**Сортировка.** Любой список, попадающий в вывод, сортируется явным `sorted(...)` с явным
`key`. Порядок обхода файловой системы никогда не используется как источник порядка.

**Никакого времени в манифесте.** Ни `datetime.now()`, ни `time.time()` в `doc-tree.json`.

**Версии зависимостей зафиксированы** и менять их можно только отдельной задачей.

**tree-sitter API.** Используется API версии 0.25+. Именно такой, проверенный код:

```python
import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Parser, Query, QueryCursor

LANG = Language(tscs.language())
parser = Parser(LANG)
tree = parser.parse(source_bytes)          # source_bytes — именно bytes, не str

query = Query(LANG, "(class_declaration name: (identifier) @cls)")
captures = QueryCursor(query).captures(tree.root_node)   # dict[str, list[Node]]
for capture_name, nodes in captures.items():
    for node in nodes:
        text = node.text.decode("utf-8")
        line = node.start_point[0] + 1     # start_point 0-based, в модели храним 1-based
```

Устаревшие формы `LANG.query(...)` и `query.captures(node) -> list[tuple]` **не использовать** —
в закреплённой версии они не работают.

---

## Проверенные факты о грамматике `tree-sitter-c-sharp` 0.23

Всё ниже проверено на реальном парсере. Это не догадки — не переизобретай их, но и не доверяй
без проверки ничему, чего здесь нет: дерево всегда можно распечатать рекурсивным обходом
`node.children` и посмотреть глазами.

**Структура объявления типа.** `modifier` и `attribute_list` — **прямые потомки** узла
объявления, а не предшествующие соседи. Порядок потомков:

```
class_declaration
├── attribute_list        (0..n, идёт первым)
├── modifier              (0..n, по одному на модификатор)
├── class                 (анонимный узел-ключевое слово)
├── identifier            ← имя типа
├── type_parameter_list   (опционально)
├── base_list             (опционально)
└── declaration_list      ← тело
```

**XML-doc — это предшествующие соседи, а не потомки.** Каждая строка `///` — отдельный узел
`comment` на том же уровне, что и объявление. Собирать так: идти от узла объявления по
`prev_sibling`, пока тип узла `comment` и текст начинается с `///`, затем развернуть список.
Атрибуты при этом не мешают — они внутри объявления.

**Атрибуты.** `attribute_list` → `attribute` → `identifier` (имя) + `attribute_argument_list`
→ `attribute_argument`. Позиционный аргумент — один потомок; именованный — `identifier` `=`
значение. Строковый литерал: узел `string_literal` **включает кавычки**, чистое значение
лежит в потомке `string_literal_content`. Брать нужно его.

**`record_declaration` покрывает и `record`, и `record struct`.** Отдельного узла нет.
Различать по наличию прямого потомка с типом `struct` → `record_struct`, иначе `record`.

**`base_list`** содержит анонимные `:` и `,` вперемешку с элементами. Элементы бывают трёх
типов: `identifier` (`Base`), `generic_name` (`IPricingProvider<string>`), `qualified_name`
(`A.B.C`). Фильтровать по `named_children`, отбрасывая пунктуацию.

**`using_directive`** — различать по наличию прямых потомков:
`global` → global using; `static` → `using static` (игнорировать);
`=` → алиас (игнорировать). Имя namespace — узел `qualified_name` или `identifier`.

**Вызов метода с generic-аргументами** (нужно для DI):

```
invocation_expression
├── member_access_expression
│   ├── identifier            's'
│   ├── .
│   └── generic_name          ← при наличии <...>; иначе здесь просто identifier
│       ├── identifier        'AddScoped'      ← имя метода
│       └── type_argument_list
│           ├── identifier    'IPricingService'
│           └── identifier    'PricingService'
└── argument_list
```

Имя метода лежит на два уровня вглубь: `invocation_expression` → `member_access_expression` →
(`generic_name` → `identifier`) либо напрямую `identifier`. Обработать оба случая.

**`file_scoped_namespace_declaration`** (`namespace N;`) — не контейнер: последующие
объявления являются его **соседями** в `compilation_unit`, а не потомками. Обычный
`namespace_declaration` (`namespace N { }`) — контейнер, объявления внутри `declaration_list`.
Определение охватывающего namespace должно поддерживать оба случая.

---

## T00 — Каркас проекта

**Цель:** рабочее окружение, линтеры, точка входа CLI.

**Создать:** `pyproject.toml`, `docpipe/__init__.py`, `docpipe/cli.py`, `tests/__init__.py`, `.gitignore`

**Спецификация**

`pyproject.toml`:

```toml
[project]
name = "docpipe"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "tree-sitter>=0.25,<0.27",
    "tree-sitter-c-sharp>=0.23,<0.24",
    "pydantic>=2.7,<3",
    "typer>=0.12,<1",
    "pyyaml>=6,<7",
]

[project.scripts]
docpipe = "docpipe.cli:app"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "mypy>=1.11", "types-PyYAML"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["docpipe"]

[tool.ruff]
line-length = 100
target-version = "py312"
# docs/ содержит markdown с компактными псевдо-сигнатурами в python-блоках;
# ruff format их переформатирует и валит проверку. examples/ — чужие репозитории.
extend-exclude = ["docs", "examples"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`docpipe/__init__.py` содержит `__version__ = "0.1.0"`.

`docpipe/cli.py` — приложение `typer` с именем `app` и одной командой-заглушкой `version`,
печатающей `__version__`.

**Обязательно добавить пустой `@app.callback()`.** Без него typer при единственной
зарегистрированной команде схлопывает её в корневую, и `docpipe version` падает с
`Got unexpected extra argument(s) (version)`. Callback переводит приложение в
многокомандный режим:

```python
@app.callback()
def main() -> None:
    """Построение структуры документации по исходному коду .NET."""
```

`.gitignore` должен содержать как минимум: `.venv/`, `__pycache__/`, `*.pyc`, `.docpipe/`,
`.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `artifacts/`.
Строку `examples/` (если она уже есть) **сохранить** — там лежат чужие репозитории
для финальной проверки.

**Критерии приёмки**
- `uv sync` отрабатывает без ошибок.
- `uv run docpipe version` печатает `0.1.0`.
- `uv run ruff check .` — чисто.
- `uv run mypy docpipe` — чисто.

**Проверка**
```bash
uv sync && uv run docpipe version && uv run ruff check . && uv run mypy docpipe
```

---

## T01 — Фикстура: тестовое .NET-решение

**Цель:** эталонный минимальный солюшн, покрывающий все нужные случаи. На нём тестируется всё
последующее. **Файлы создаются ровно с таким содержимым** — на них завязаны golden-тесты.

**Создать:** дерево `tests/fixtures/SampleSolution/`

```
tests/fixtures/SampleSolution/
├── SampleSolution.sln
└── src/
    ├── Sample.Common/
    │   ├── Sample.Common.csproj
    │   ├── Web/BaseApiController.cs
    │   └── Abstractions/IPricingProvider.cs
    └── Sample.Pricing.Api/
        ├── Sample.Pricing.Api.csproj
        ├── Program.cs
        ├── Controllers/PricingController.cs
        ├── Services/IPricingService.cs
        ├── Services/PricingService.cs
        ├── Services/PricingService.Calculations.cs
        ├── Providers/CurveProvider.cs
        ├── Workflows/ValuationWorkflow.cs
        ├── Grid/RiskComputeService.cs
        ├── Models/PriceDto.cs
        └── obj/Debug/net8.0/Sample.Generated.g.cs
```

Что каждый файл проверяет:

| Файл | Проверяемый случай |
|---|---|
| `BaseApiController.cs` | базовый класс в **другом** модуле (транзитивное наследование) |
| `PricingController.cs` | наследование через модуль, атрибуты с аргументами, маршруты |
| `PricingService.cs` + `.Calculations.cs` | `partial class` в двух файлах → один узел, два `sources` |
| `IPricingService.cs` | интерфейс не должен стать узлом (правило требует `type_kind: class`) |
| `CurveProvider.cs` | generic-интерфейс в base list |
| `ValuationWorkflow.cs` | классификация по суффиксу имени |
| `RiskComputeService.cs` | Ignite: приоритет правила выше, чем у `service` по суффиксу |
| `PriceDto.cs` | исключение по `name_regex` |
| `Program.cs` | DI-регистрации |
| `obj/**/*.g.cs` | двойное исключение: по `**/obj/**` и по `**/*.g.cs` |

Содержимое файлов:

<details>
<summary><code>SampleSolution.sln</code></summary>

```
Microsoft Visual Studio Solution File, Format Version 12.00
Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Sample.Common", "src\Sample.Common\Sample.Common.csproj", "{11111111-1111-1111-1111-111111111111}"
EndProject
Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Sample.Pricing.Api", "src\Sample.Pricing.Api\Sample.Pricing.Api.csproj", "{22222222-2222-2222-2222-222222222222}"
EndProject
Global
EndGlobal
```
</details>

<details>
<summary><code>src/Sample.Common/Sample.Common.csproj</code></summary>

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
```
</details>

<details>
<summary><code>src/Sample.Common/Web/BaseApiController.cs</code></summary>

```csharp
using Microsoft.AspNetCore.Mvc;

namespace Sample.Common.Web;

/// <summary>Base class for all API controllers.</summary>
[ApiController]
public abstract class BaseApiController : ControllerBase
{
    protected string TraceId => HttpContext.TraceIdentifier;
}
```
</details>

<details>
<summary><code>src/Sample.Common/Abstractions/IPricingProvider.cs</code></summary>

```csharp
namespace Sample.Common.Abstractions;

/// <summary>Supplies pricing inputs.</summary>
public interface IPricingProvider<T> where T : class
{
    T Get(string key);
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Sample.Pricing.Api.csproj</code></summary>

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>net8.0;net9.0</TargetFrameworks>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="..\Sample.Common\Sample.Common.csproj" />
  </ItemGroup>
  <ItemGroup>
    <PackageReference Include="Apache.Ignite" Version="2.16.0" />
  </ItemGroup>
</Project>
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Controllers/PricingController.cs</code></summary>

```csharp
using Microsoft.AspNetCore.Mvc;
using Sample.Common.Web;
using Sample.Pricing.Api.Services;

namespace Sample.Pricing.Api.Controllers;

/// <summary>Handles pricing requests.</summary>
[Route("api/v1/[controller]")]
public sealed class PricingController : BaseApiController
{
    private readonly IPricingService _pricing;

    public PricingController(IPricingService pricing)
    {
        _pricing = pricing;
    }

    [HttpGet("{id:guid}")]
    public async Task<ActionResult<decimal>> GetAsync(Guid id, CancellationToken ct)
    {
        return await _pricing.PriceAsync(id, ct);
    }

    [HttpPost]
    public Task<ActionResult> RecalculateAsync() => Task.FromResult<ActionResult>(Ok());
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Services/IPricingService.cs</code></summary>

```csharp
namespace Sample.Pricing.Api.Services;

public interface IPricingService
{
    Task<decimal> PriceAsync(Guid id, CancellationToken ct);
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Services/PricingService.cs</code></summary>

```csharp
using Sample.Common.Abstractions;

namespace Sample.Pricing.Api.Services;

/// <summary>Computes prices for instruments.</summary>
public sealed partial class PricingService : IPricingService
{
    private readonly IPricingProvider<string> _curves;

    public PricingService(IPricingProvider<string> curves)
    {
        _curves = curves;
    }

    public Task<decimal> PriceAsync(Guid id, CancellationToken ct)
    {
        return Task.FromResult(Discount(1m));
    }
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Services/PricingService.Calculations.cs</code></summary>

```csharp
namespace Sample.Pricing.Api.Services;

public sealed partial class PricingService
{
    private static decimal Discount(decimal value) => value * 0.98m;
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Providers/CurveProvider.cs</code></summary>

```csharp
using Sample.Common.Abstractions;

namespace Sample.Pricing.Api.Providers;

/// <summary>Provides discount curves.</summary>
public sealed class CurveProvider : IPricingProvider<string>
{
    public string Get(string key) => key;
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Workflows/ValuationWorkflow.cs</code></summary>

```csharp
namespace Sample.Pricing.Api.Workflows;

/// <summary>End-to-end valuation sequence.</summary>
public sealed class ValuationWorkflow
{
    public Task RunAsync(CancellationToken ct) => Task.CompletedTask;
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Grid/RiskComputeService.cs</code></summary>

```csharp
using Apache.Ignite.Core.Services;

namespace Sample.Pricing.Api.Grid;

/// <summary>Risk aggregation running on the compute grid.</summary>
public sealed class RiskComputeService : IService
{
    public void Init(IServiceContext context) { }
    public void Execute(IServiceContext context) { }
    public void Cancel(IServiceContext context) { }
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Models/PriceDto.cs</code></summary>

```csharp
namespace Sample.Pricing.Api.Models;

public sealed record PriceDto(Guid Id, decimal Value);
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/Program.cs</code></summary>

```csharp
using Microsoft.Extensions.DependencyInjection;
using Sample.Common.Abstractions;
using Sample.Pricing.Api.Providers;
using Sample.Pricing.Api.Services;

namespace Sample.Pricing.Api;

public static class Program
{
    public static void ConfigureServices(IServiceCollection services)
    {
        services.AddScoped<IPricingService, PricingService>();
        services.AddSingleton<IPricingProvider<string>, CurveProvider>();
        services.AddTransient<ValuationWorkflow>();
    }
}
```
</details>

<details>
<summary><code>src/Sample.Pricing.Api/obj/Debug/net8.0/Sample.Generated.g.cs</code></summary>

```csharp
namespace Sample.Pricing.Api.Generated;

public sealed class GeneratedService
{
    public void ShouldNeverBeDocumented() { }
}
```
</details>

**Критерии приёмки**
- Все 15 файлов существуют по указанным путям.
- Тест `tests/test_fixture.py` проверяет: файлов `.cs` ровно 12, `.csproj` ровно 2, `.sln` ровно 1.

**Проверка**
```bash
uv run pytest tests/test_fixture.py -q
```

---

## T01b — Фикстура: конструкции из реальных репозиториев

**Цель:** второе решение с случаями, которых нет в `SampleSolution`. Все они найдены
прогоном на eShopOnWeb (см. [`findings-eshoponweb.md`](findings-eshoponweb.md)) и все
ломают наивную реализацию, написанную только под `SampleSolution`.

**Почему отдельное решение, а не расширение первого:** критерии приёмки T04–T20 завязаны
на точные количества в `SampleSolution` (11 файлов, 10 символов, 6 узлов). Расширение
той фикстуры потребовало бы перенумеровать их все.

**Создать:** дерево `tests/fixtures/WildSolution/` (11 файлов `.cs`, 2 `.csproj`, 1 `.sln`),
`tests/test_fixture_wild.py`; добавить фикстуру `wild_solution` в `tests/conftest.py`

```
tests/fixtures/WildSolution/
├── WildSolution.sln
├── src/Wild.Api/
│   ├── Wild.Api.csproj
│   ├── Program.cs                        top-level statements + 4 DI-регистрации
│   ├── GlobalUsings.cs                   global using ×2, using static, алиас
│   ├── Constants.cs                      Wild.Api.Constants
│   ├── Duplicates/Constants.cs           Wild.Api.Duplicates.Constants — коллизия slug
│   ├── Endpoints/AuthenticateEndpoint.cs многострочный base_list
│   ├── Endpoints/CatalogListEndpoint.cs  generic-интерфейс в базе
│   ├── Legacy/BlockNamespace.cs          namespace в блочной форме
│   ├── Legacy/NoNamespace.cs             тип в глобальном namespace
│   ├── Pages/Login.cshtml.cs             вложенный InputModel
│   └── Pages/Register.cshtml.cs          вложенный InputModel (то же имя)
└── tests/Wild.Tests/
    ├── Wild.Tests.csproj                 тестовый проект: парсится, но не enrolled
    └── CatalogListEndpointTests.cs
```

Что какой файл проверяет:

| Случай | Ломает без обработки | Задача |
|---|---|---|
| Многострочный `base_list` | `signature_hash`, правила `base_type` | T06, T15 |
| Top-level statements | все DI-регистрации теряются | T13 |
| Тип без namespace | FQN с ведущей точкой | T10 |
| Блочный namespace | namespace не определяется | T06 |
| `global using` / `static` / алиас | резолв FQN | T08, T10 |
| Вложенные типы с общим именем | два узла схлопываются в один | T10 |
| Два `Constants` в одном модуле | коллизия `doc_path` | T15 |
| Тестовый проект | тесты попадают в документацию | T15 |

**Критерии приёмки**
- ровно 11 файлов `.cs` и 2 `.csproj` по перечисленным путям;
- **каждый** файл разбирается с `parse_errors == 0` и `has_error is False`;
- `AuthenticateEndpoint`: сырой текст базы содержит `\n`;
- `Program.cs`: среди детей корня есть `global_statement`, **нет** `class_declaration`
  и `method_declaration`; в дереве 4 вызова `Add*`;
- `NoNamespace.cs`: нет ни `namespace_declaration`, ни `file_scoped_namespace_declaration`;
- `BlockNamespace.cs`: класс лежит **внутри** `namespace_declaration`;
- `GlobalUsings.cs`: 2 директивы с `global`, 1 со `static`, 1 с `=`;
- `Login.cshtml.cs` и `Register.cshtml.cs` дают `LoginModel.InputModel`
  и `RegisterModel.InputModel`;
- `Constants.cs` и `Duplicates/Constants.cs` дают разные namespace, но один slug.

**Проверка**
```bash
uv run pytest tests/test_fixture_wild.py -q
```

**Зависит от:** T02 (для `slugify` в тесте коллизии)

---

## T02 — `hashing.py`: хэши, стабильный дамп, slug

**Цель:** примитивы, от которых зависит детерминизм всей системы.

**Создать:** `docpipe/hashing.py`, `tests/test_hashing.py`

**Спецификация**

```python
def content_hash(data: bytes) -> str:
    """sha256 от содержимого. Формат: 'sha256:<hex>'."""

def stable_json_dumps(obj: Any) -> str:
    """json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + '\n'."""

def stable_hash(obj: Any) -> str:
    """content_hash(stable_json_dumps(obj).encode('utf-8'))."""

def slugify(name: str) -> str:
    """Имя типа -> kebab-case ASCII."""
```

Алгоритм `slugify`:
1. Отбросить всё начиная с первого `<` (type parameters).
2. Вставить разделители по регексу `(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])`.
3. Заменить все не-`[A-Za-z0-9]` на `-`.
4. Привести к нижнему регистру, схлопнуть повторяющиеся `-`, обрезать `-` по краям.
5. Если результат пуст — вернуть `"unnamed"`.

**Критерии приёмки** — тест проверяет ровно эти пары:

| Вход | Выход |
|---|---|
| `PricingController` | `pricing-controller` |
| `HTTPClientFactory` | `http-client-factory` |
| `IPricingProvider<T>` | `i-pricing-provider` |
| `Repository2` | `repository2` |
| `_weird__Name_` | `weird-name` |
| `ABC` | `abc` |
| `<>` | `unnamed` |

Плюс: `stable_json_dumps({"b":1,"a":2})` даёт ключи в порядке `a`, `b` и оканчивается `\n`;
`stable_hash` от двух словарей с разным порядком ключей совпадает.

**Проверка**
```bash
uv run pytest tests/test_hashing.py -q
```

---

## T03 — `model.py`: модели данных и JSON Schema

**Цель:** все pydantic-модели системы в одном месте + команда генерации схемы.

**Создать:** `docpipe/model.py`, изменить `docpipe/cli.py`, создать `tests/test_model.py`

**Спецификация.** Все модели — `pydantic.BaseModel` с `model_config = ConfigDict(frozen=True)`.

```python
# --- уровень парсера (чистый синтаксис) ---
class Attribute:        name: str; args: list[str]; named_args: dict[str, str]
class SourceSpan:       path: str; start: int; end: int          # 1-based, включительно
class Member:           name: str; kind: Literal["method","property","field","constructor","event"]
                        signature: str; modifiers: list[str]
                        attributes: list[Attribute]; line: int; end_line: int
                        xml_doc: str | None = None
class RawDeclaration:   name: str
                        type_kind: Literal["class","interface","struct","record","record_struct","enum"]
                        namespace: str; containing_type: str | None
                        type_parameters: list[str]; modifiers: list[str]
                        base_types: list[str]                     # сырой текст base list
                        attributes: list[Attribute]; members: list[Member]
                        span: SourceSpan; xml_doc: str | None
class DiRegistration:   service_type: str; impl_type: str | None
                        lifetime: Literal["scoped","singleton","transient","hosted","unknown"]
                        confidence: Literal["high","medium","low"]; file: str; line: int
class FileParseResult:  path: str; content_hash: str
                        usings: list[str]; global_usings: list[str]
                        declarations: list[RawDeclaration]
                        di_registrations: list[DiRegistration]
                        parse_errors: int

# --- уровень резолва ---
class Symbol:           fqn: str; name: str
                        type_kind: str; namespace: str; module: str
                        modifiers: list[str]; type_parameters: list[str]
                        base_types: list[str]            # резолвнутые где возможно
                        base_types_raw: list[str]
                        base_type_closure: list[str]     # транзитивно, отсортировано
                        attributes: list[Attribute]; members: list[Member]
                        sources: list[SourceSpan]        # >1 у partial
                        xml_doc: str | None; ambiguous: bool = False

# --- уровень манифеста ---
class Endpoint:         http_method: str; route: str; member: str; line: int
class Dependency:       target: str; via: Literal["constructor","di","inheritance"]
                        confidence: Literal["high","medium","low"]
class Relation:         target: str; relation: Literal["implements","implemented_by","uses"]
class Module:           id: str; name: str; csproj: str
                        target_frameworks: list[str]
                        project_references: list[str]; package_references: list[str]
                        domain: str; enrolled: bool
class DocNode:          id: str; kind: str; template: str; title: str
                        doc_path: str; parent: str | None
                        module: str; domain: str
                        symbol: Symbol | None
                        endpoints: list[Endpoint]; dependencies: list[Dependency]
                        related: list[Relation]
                        matched_rules: list[str]; signature_hash: str
class ParserVersions:   tree_sitter: str; grammar_c_sharp: str
class PartialInfo:      scope: list[str]; outside_from_cache: bool
class Manifest:         schema_version: Literal["1.0"]
                        ruleset_version: str
                        parser: ParserVersions
                        partial: PartialInfo | None
                        modules: list[Module]
                        nodes: list[DocNode]
class RunMeta:          generated_at: str; host: str; duration_seconds: float
                        docpipe_version: str; stats: dict[str, int]
```

Добавить в CLI команду:
```
docpipe schema --out schema/doc-tree.schema.json
```
Пишет `Manifest.model_json_schema()` через `stable_json_dumps`.

**Критерии приёмки**
- `uv run docpipe schema --out schema/doc-tree.schema.json` создаёт файл.
- Повторный запуск даёт **байт-в-байт** тот же файл.
- Тест round-trip: минимальный `Manifest` → `model_dump()` → `Manifest.model_validate()` → равен исходному.

**Проверка**
```bash
uv run docpipe schema --out schema/doc-tree.schema.json && uv run pytest tests/test_model.py -q
```

---

## T04 — `config.py` и `discovery.py`

**Цель:** детерминированный список файлов с учётом ignore, scope и enrollment.

**Создать:** `docpipe/config.py`, `docpipe/discovery.py`, `tests/test_discovery.py`

**Спецификация**

`config.py` — модель `DocpipeConfig`:
```yaml
roots: ["."]
enrolled: ["**"]              # globs по пути csproj; "**" = всё
domains: {}                   # glob по пути csproj -> имя домена
rules: "rules/dotnet.yaml"
out: "artifacts/doc-tree.json"
cache_dir: ".docpipe/cache"
```
Функция `load_config(path: Path | None) -> DocpipeConfig`; при `None` возвращает дефолты.

`discovery.py`:
```python
@dataclass(frozen=True)
class Discovered:
    cs_files: list[str]        # репо-относительные POSIX, отсортированы
    csproj_files: list[str]
    sln_files: list[str]

def discover(root: Path, exclude_globs: list[str],
             scope: list[str] | None = None) -> Discovered: ...
```

Правила:
- обход через `os.walk(followlinks=False)`, результат **всегда** через `sorted()`;
- путь исключается, если совпал хотя бы с одним glob из `exclude_globs`;

**Ловушка `fnmatch` и `**`.** `fnmatch` не понимает `**` как «ноль или больше
сегментов» — он транслирует `*` в `.*` без учёта разделителей. Проверено:

```
fnmatch("src/A/obj/x/y.cs", "**/obj/**")  -> True
fnmatch("obj/y.cs",         "**/obj/**")  -> False   ← дыра
fnmatch("src/A/Sample.g.cs", "**/*.g.cs") -> True
fnmatch("Sample.g.cs",       "**/*.g.cs") -> False   ← дыра
```

Из-за обязательного `/` в шаблоне файлы в корневых `obj/`/`bin/` и сгенерированные
файлы в корне репозитория молча просачиваются в документацию. Лечится вторым
прогоном с отброшенным префиксом:

```python
def matches_glob(path: str, glob: str) -> bool:
    if fnmatch(path, glob):
        return True
    if glob.startswith("**/"):
        return fnmatch(path, glob[3:])
    return False
```

Использовать `matches_glob` везде, где сравниваются пути с шаблонами — в T04 и в
`exclude.path_glob`/`path_glob` из T14.

**Отсечение каталогов (обязательно).** Из каждого glob, оканчивающегося на `/**`,
вывести glob каталога (`**/obj/**` → `**/obj`) и не заходить в совпавшие каталоги
через `dirnames[:] = [...]`. На большом репозитории это разница между обходом всего
`node_modules`/`.git` и мгновенным пропуском. На результат не влияет: если каталог
совпал, любой файл под ним совпал бы и с исходным шаблоном.
- при заданном `scope` файл включается, только если его путь начинается с одного из
  элементов scope (сравнение по сегментам пути, не по подстроке);
- символические ссылки не разыменовываются.

**Критерии приёмки** на фикстуре, с `exclude_globs = ["**/obj/**", "**/bin/**", "**/*.g.cs"]`:
- `cs_files` содержит ровно **11** путей и **не** содержит `Sample.Generated.g.cs`
  (в фикстуре 12 файлов `.cs`, один отсекается исключениями);
- `csproj_files` — ровно 2, `sln_files` — ровно 1;
- со `scope=["src/Sample.Common"]` возвращается ровно 2 `.cs`;
- тест «перемешанного ФС»: результат совпадает при двух вызовах подряд;
- **контрольный тест:** с пустым списком исключений находится ровно 12 `.cs`,
  включая `Sample.Generated.g.cs`. Без него тест на исключения может проходить
  просто потому, что файла нет на диске;
- `scope=["src/Sample"]` даёт **пустой** результат — сравнение идёт по сегментам
  пути, а не по подстроке (иначе scope захватил бы соседний модуль с общим префиксом);
- все 9 случаев `matches_glob` из таблицы выше, включая корневые `obj/y.cs` и `Sample.g.cs`;
- симлинк на каталог с исходниками не порождает дублей.

**Проверка**
```bash
uv run pytest tests/test_discovery.py -q
```

---

## T05 — `dotnet/csproj.py` и `dotnet/sln.py`

**Цель:** граф модулей.

**Создать:** `docpipe/dotnet/__init__.py`, `docpipe/dotnet/csproj.py`, `docpipe/dotnet/sln.py`, `tests/test_project_graph.py`

**Спецификация**

**Три ловушки, проверенные на реальных проектах** (eShopOnWeb, 10 `.csproj`):

1. **BOM.** Все 10 файлов сохранены в UTF-8 с BOM (`ef bb bf`). Чтение через
   `read_text(encoding="utf-8")` оставит BOM первым символом, и `ET.fromstring`
   упадёт с `not well-formed`. Читать **байтами**: `ET.fromstring(path.read_bytes())`.

2. **`TargetFramework` обычно отсутствует в самом `.csproj`.** В eShopOnWeb его
   не объявляет **ни один** проект — значение приходит из `Directory.Packages.props`
   уровня решения. Без обхода вверх по дереву `target_frameworks` будет пуст
   у каждого модуля. Искать `Directory.Build.props` и `Directory.Packages.props`,
   поднимаясь от каталога проекта до корня обхода; побеждает ближайший файл,
   собственное значение проекта важнее унаследованного.

3. **XML-namespace в legacy-проектах.** Формат SDK идёт без namespace, но проекты
   старого формата объявляют `xmlns="http://schemas.microsoft.com/developer/msbuild/2003"`,
   и теги выглядят как `{http://…}PropertyGroup`. Сравнивать только по локальному
   имени: `tag.rpartition("}")[2]`.

Полного вычисления свойств MSBuild не делать: условия, подстановки `$(…)` и цепочки
`Import` не разворачиваются. Задача — структурные факты, а не воспроизведение сборки.

`csproj.py` — `parse_csproj(path: Path, repo_root: Path) -> Module`:
- имя модуля = имя файла без `.csproj`;
- `target_frameworks`: из `<TargetFramework>` или `<TargetFrameworks>` (сплит по `;`),
  при отсутствии — унаследованные из props-файлов (см. выше), отсортировать;
- `project_references`: из `<ProjectReference Include="...">`, взять имя файла без расширения,
  разделители `\` привести к `/`, отсортировать;
- `package_references`: из `<PackageReference Include="...">`, отсортировать;
- парсинг через `xml.etree.ElementTree`; игнорировать XML-namespace, если он присутствует
  (сравнивать по локальному имени тега);
- `domain` и `enrolled` на этом этапе заполняются заглушками (`""`, `True`) — их проставит T15.

`sln.py` — `parse_sln(path: Path, repo_root: Path) -> list[str]`: вернуть репо-относительные
пути `.csproj` из строк `Project(...) = "...", "путь", "..."`. Разделители `\` → `/`.
Отсортировать. Файл читать как UTF-8 с `errors="replace"`.

**Фильтровать по расширению `.csproj`.** Записи `Project(...)` описывают не только
проекты C#: папки решения (тип `{2150E333-…}`) кладут в поле пути собственное имя
(`"src", "src"` — не файл), а рядом встречаются `.dcproj`, `.vcxproj`, `.esproj`.
В `eShopOnWeb.sln` из 14 записей `Project(...)` только 10 — проекты C#.

**Критерии приёмки**
- `Sample.Pricing.Api`: `target_frameworks == ["net8.0", "net9.0"]`,
  `project_references == ["Sample.Common"]`, `package_references == ["Apache.Ignite"]`.
- `Sample.Common`: `target_frameworks == ["net8.0"]`, остальные списки пусты.
- `parse_sln` возвращает ровно 2 пути, оба существуют на диске.
- На `WildSolution`: `Wild.Api.csproj` разбирается несмотря на BOM;
  `Wild.Tests.csproj` **без собственного** `TargetFramework` даёт `["net8.0"]`
  из `Directory.Build.props`; `..\..\src\Wild.Api\Wild.Api.csproj` даёт `["Wild.Api"]`;
  `PackageReference` с вложенными `PrivateAssets` разбирается.
- Синтетические случаи: legacy-`.csproj` с `xmlns`; `.sln` с папкой решения
  и `.dcproj`; ближайший props-файл побеждает дальний; собственный TFM побеждает
  унаследованный; битый XML падает с `ET.ParseError`, а не даёт пустой модуль.

**Проверка**
```bash
uv run pytest tests/test_project_graph.py -q
```

---

## T06 — `dotnet/parser.py`: объявления типов

**Цель:** файл `.cs` → список `RawDeclaration`. Только объявления, без членов.

**Создать:** `docpipe/dotnet/queries/declarations.scm`, `docpipe/dotnet/parser.py`, `tests/test_parser_declarations.py`

**Спецификация**

Извлечь для каждого объявления типа (`class_declaration`, `interface_declaration`,
`struct_declaration`, `record_declaration`, `enum_declaration`):

- `name` — прямой потомок `identifier` узла объявления;
- `type_kind` — по типу узла; для `record_declaration` — `record_struct`, если среди прямых
  потомков есть узел типа `struct`, иначе `record` (см. раздел о грамматике);
- `namespace` — по ближайшему предку `namespace_declaration` **или** по предшествующему
  `file_scoped_namespace_declaration` в том же `compilation_unit`; для вложенных namespace
  склеить через `.`; при отсутствии — пустая строка;
- `containing_type` — имя ближайшего предка-типа, иначе `None`;
- `type_parameters` — имена из `type_parameter_list`;
- `modifiers` — прямые потомки типа `modifier`, **отсортированные**;
- `base_types` — текст каждого именованного элемента `base_list` (отбросив `:` и `,`),
  **нормализованный в два шага** (второй обязателен, иначе останется `X .WithRequest<A>`):

  ```python
  text = re.sub(r"\s+", " ", raw).strip()
  text = re.sub(r"\s*\.\s*", ".", text)
  ```

  Generic-аргументы сохраняются (`IPricingProvider<string>` остаётся целиком);
- `attributes` — см. ниже;
- `span` — `SourceSpan(path, start_point[0]+1, end_point[0]+1)`;
- `xml_doc` — см. ниже.

**Атрибуты.** Прямые потомки `attribute_list` → `attribute` → `identifier` (имя) +
`attribute_argument_list`. Имя берётся с **отброшенным** суффиксом `Attribute`, если он есть
(`[RouteAttribute]` → `Route`). Для каждого `attribute_argument`:
- есть потомки `identifier` + `=` → именованный, идёт в `named_args`;
- иначе позиционный, идёт в `args`.

Значение: если узел `string_literal` — брать текст потомка `string_literal_content`
(это даёт значение **без кавычек**); иначе текст узла как есть.
`args` сохраняют порядок объявления; `named_args` сортируются по ключу.

**XML-doc.** Идти от узла объявления по `prev_sibling`, пока тип узла — `comment` и его текст
начинается с `///`; собранное развернуть в исходный порядок. Из каждой строки убрать `///`
и ведущие пробелы. Извлечь содержимое `<summary>...</summary>`, схлопнуть переводы строк и
повторяющиеся пробелы в один пробел, обрезать по краям. Если `<summary>` нет — `None`.

Публичный API модуля:
```python
def parse_file(path: Path, repo_root: Path) -> FileParseResult: ...
```
На этой задаче `members`, `usings`, `global_usings`, `di_registrations` заполняются пустыми
списками. `parse_errors` — количество узлов `ERROR` в дереве.

`Language`/`Parser` создаются **один раз** на уровне модуля, а не на каждый файл.

**Критерии приёмки** — на `Controllers/PricingController.cs`:
- ровно 1 объявление;
- `name == "PricingController"`, `type_kind == "class"`;
- `namespace == "Sample.Pricing.Api.Controllers"`;
- `modifiers == ["public", "sealed"]`;
- `base_types == ["BaseApiController"]`;
- `attributes == [Attribute(name="Route", args=["api/v1/[controller]"], named_args={})]`;
- `xml_doc == "Handles pricing requests."`;
- `parse_errors == 0`.

На `Providers/CurveProvider.cs`: `base_types == ["IPricingProvider<string>"]`.

**Схлопывание пробелов — не косметика.** В дикой природе встречается такое
(реальный случай из eShopOnWeb):

```csharp
public class AuthenticateEndpoint : EndpointBaseAsync
    .WithRequest<AuthenticateRequest>
    .WithActionResult<AuthenticateResponse>
```

Без нормализации в `base_types` попадёт строка с `\n` и отступами. Тогда
`signature_hash` (T15) начнёт зависеть от форматирования файла: переформатировали —
хэш изменился — агент на шаге 3 перегенерировал документ впустую. Это ломает
главное свойство инкрементальности. Плюс правило `base_type: ["EndpointBaseAsync"]`
перестаёт совпадать, а JSON манифеста становится нечитаемым.
На `Abstractions/IPricingProvider.cs`: `type_kind == "interface"`, `type_parameters == ["T"]`.
На `Models/PriceDto.cs`: `type_kind == "record"`.
На всех 11 неисключённых файлах фикстуры: суммарно `parse_errors == 0`.

**Проверка**
```bash
uv run pytest tests/test_parser_declarations.py -q
```

---

## T07 — `dotnet/parser.py`: члены типов

**Цель:** заполнить `RawDeclaration.members`.

**Изменить:** `docpipe/dotnet/parser.py`, `docpipe/dotnet/queries/members.scm`; создать `tests/test_parser_members.py`

**Спецификация.** Извлекать `method_declaration`, `property_declaration`, `field_declaration`,
`constructor_declaration`, `event_declaration`, объявленные **непосредственно** в теле типа
(не в теле вложенного типа).

- `kind` — по типу узла;
- `name` — из `name: (identifier)`; для поля — имя первого `variable_declarator`;
- `signature` — нормализованный текст: от начала объявления до `{`, `=>` или `;`
  (что встретится раньше), с схлопыванием любых последовательностей пробельных символов
  в один пробел и обрезкой по краям;
- `modifiers` — отсортированы;
- `attributes` — тем же кодом, что в T06;
- `line` / `end_line` — 1-based;
- `xml_doc` — тем же кодом, что в T06.

Список `members` сортируется по `(line, name)`.

**Критерии приёмки** — `PricingController`:
- 4 члена: поле `_pricing`, конструктор `PricingController`, методы `GetAsync`, `RecalculateAsync`;
- у `GetAsync` — `attributes == [Attribute(name="HttpGet", args=["{id:guid}"], named_args={})]`;
- у `RecalculateAsync` — `attributes == [Attribute(name="HttpPost", args=[], named_args={})]`;
- `signature` метода `GetAsync` равна
  `"public async Task<ActionResult<decimal>> GetAsync(Guid id, CancellationToken ct)"`.

`RiskComputeService` — ровно 3 метода. `PriceDto` (record с primary constructor) — не падает,
`members` может быть пустым.

**Проверка**
```bash
uv run pytest tests/test_parser_members.py -q
```

---

## T08 — `dotnet/parser.py`: usings

**Цель:** заполнить `usings` и `global_usings`.

**Изменить:** `docpipe/dotnet/parser.py`; создать `tests/test_parser_usings.py`

**Спецификация.** Из `using_directive`: имя namespace — текст потомка `qualified_name`
или `identifier`. Различение по прямым потомкам (см. раздел о грамматике):
- есть потомок `=` → алиас (`using X = A.B.C;`), **игнорировать полностью**;
- есть потомок `static` → `using static`, **игнорировать**;
- есть потомок `global` → в `global_usings`;
- иначе → в `usings`.

Порядок проверок важен: `global using static` должен отбрасываться, а не попадать
в `global_usings`. Оба списка сортируются и дедуплицируются.

**Критерии приёмки** — `PricingController.cs`:
`usings == ["Microsoft.AspNetCore.Mvc", "Sample.Common.Web", "Sample.Pricing.Api.Services"]`,
`global_usings == []`.

**Проверка**
```bash
uv run pytest tests/test_parser_usings.py -q
```

---

## T09 — `cache.py`: sqlite-кэш разобранных файлов

**Цель:** не парсить файлы, которые не менялись.

**Создать:** `docpipe/cache.py`, `tests/test_cache.py`

**Спецификация.** sqlite (stdlib `sqlite3`), payload — `zlib`-сжатый JSON `FileParseResult`.

```sql
CREATE TABLE IF NOT EXISTS meta  (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                                  payload BLOB NOT NULL);
```

```python
CACHE_VERSION = "1"

class ParseCache:
    def __init__(self, db_path: Path, parser_versions: ParserVersions) -> None: ...
    def get(self, path: str, content_hash: str) -> FileParseResult | None: ...
    def put(self, result: FileParseResult) -> None: ...
    def all_paths(self) -> list[str]: ...          # ORDER BY path
    def get_any(self, path: str) -> FileParseResult | None: ...   # без сверки хэша
    def prune(self, keep_paths: set[str]) -> int: ...
    def close(self) -> None: ...
```

При открытии: если `meta.cache_version` или `meta.parser_versions` не совпадают с текущими —
**вся** таблица `files` очищается и meta переписывается. Это единственный способ инвалидации
после апгрейда грамматики.

`get` возвращает `None` при несовпадении `content_hash`.
`get_any` нужен скоуп-режиму (§2.3 архитектуры) — там свежесть не гарантируется.
Класс должен работать как контекстный менеджер.

**Критерии приёмки**
- `put` → `get` с тем же хэшем возвращает эквивалентный объект;
- `get` с другим хэшем возвращает `None`;
- пересоздание `ParseCache` с другими `parser_versions` очищает кэш (`all_paths() == []`);
- `prune({"a.cs"})` при трёх записях удаляет 2 и возвращает `2`;
- кэш переживает закрытие/открытие файла БД.

**Проверка**
```bash
uv run pytest tests/test_cache.py -q
```

---

## T10 — `dotnet/resolve.py`: FQN и слияние partial

**Цель:** `list[FileParseResult]` → `SymbolIndex` (`dict[str, Symbol]`).

**Создать:** `docpipe/dotnet/resolve.py`, `tests/test_resolve_fqn.py`

**Спецификация**

```python
def build_symbol_index(results: list[FileParseResult],
                       file_to_module: dict[str, str]) -> dict[str, Symbol]: ...
```

Шаг 1 — FQN объявления: `namespace + "." + containing_type + "." + name`, пустые части
пропускаются. Type parameters в FQN **не входят**.

Шаг 2 — слияние. Объявления с одинаковым FQN сливаются в один `Symbol`:
- `sources` — все `span`, отсортированные по `(path, start)`;
- `modifiers`, `base_types_raw`, `attributes` — объединение, отсортировано, дедуплицировано;
- `members` — конкатенация, сортировка по `(path_из_span, line, name)`; при отсутствии
  привязки к файлу — по `(line, name)`;
- `xml_doc` — первый непустой в порядке отсортированных `sources`;
- `type_kind` — из первого объявления в том же порядке;
- `module` — из `file_to_module` по первому источнику.

Шаг 3 — резолв имён из `base_types_raw` в FQN. Для имени `N` в объявлении с namespace `NS`
и usings `U` (собственные + global, оба отсортированы):
1. отбросить `<...>` — получить базовое имя `B`;
2. если `B` содержит `.` и `B` есть в индексе → результат `B`;
3. перебрать `NS` и его префиксы от длинного к короткому: `NS + "." + B`;
4. перебрать `U` в лексикографическом порядке: `u + "." + B`;
5. собрать все совпадения; если >1 — взять лексикографически меньший и выставить
   `ambiguous = True`; если 0 — оставить `B` как есть (внешний тип).

Результат кладётся в `base_types`. `base_types_raw` сохраняет исходный текст.

**Критерии приёмки** — на всех 11 неисключённых файлах фикстуры:
- индекс содержит **ровно 10** символов (11 файлов, из которых два — половинки одного
  `partial class`), а именно:
  `Sample.Common.Abstractions.IPricingProvider`, `Sample.Common.Web.BaseApiController`,
  `Sample.Pricing.Api.Controllers.PricingController`, `Sample.Pricing.Api.Grid.RiskComputeService`,
  `Sample.Pricing.Api.Models.PriceDto`, `Sample.Pricing.Api.Program`,
  `Sample.Pricing.Api.Providers.CurveProvider`, `Sample.Pricing.Api.Services.IPricingService`,
  `Sample.Pricing.Api.Services.PricingService`, `Sample.Pricing.Api.Workflows.ValuationWorkflow`;
- `PricingService` имеет **ровно 2** записи в `sources`, пути отсортированы,
  `Discount` присутствует в `members`;
- `PricingController.base_types == ["Sample.Common.Web.BaseApiController"]`
  (резолвнуто через `using Sample.Common.Web`);
- `BaseApiController.base_types == ["ControllerBase"]` (внешний, не резолвнут);
- `CurveProvider.base_types == ["Sample.Common.Abstractions.IPricingProvider"]`
  (generic-аргумент отброшен при резолве);
- ни у одного символа `ambiguous is True`.

**Проверка**
```bash
uv run pytest tests/test_resolve_fqn.py -q
```

---

## T11 — `dotnet/resolve.py`: замыкание наследования

**Цель:** заполнить `Symbol.base_type_closure`.

**Изменить:** `docpipe/dotnet/resolve.py`; создать `tests/test_resolve_closure.py`

**Спецификация**

```python
def compute_closures(index: dict[str, Symbol]) -> dict[str, Symbol]: ...
```

Для каждого символа обойти `base_types` в ширину, добавляя базовые типы найденных символов.
Нерезолвнутые имена попадают в замыкание, но дальше не раскрываются. Защита от циклов
через множество посещённых. Результат — отсортированный дедуплицированный список,
**не включающий** сам символ.

**Критерии приёмки**
- `PricingController.base_type_closure == ["ControllerBase", "Sample.Common.Web.BaseApiController"]`
  — транзитивно через границу модуля. Это ключевой тест всей архитектуры;
- `RiskComputeService.base_type_closure == ["IService"]`;
- искусственный тест на цикл `A : B`, `B : A` завершается и не зацикливается.

**Проверка**
```bash
uv run pytest tests/test_resolve_closure.py -q
```

---

## T12 — `dotnet/endpoints.py`

**Цель:** HTTP-маршруты контроллеров.

**Создать:** `docpipe/dotnet/endpoints.py`, `tests/test_endpoints.py`

**Спецификация**

```python
def extract_endpoints(symbol: Symbol) -> list[Endpoint]: ...
```

- базовый шаблон — первый аргумент атрибута `Route` на типе; если атрибута нет — пустая строка;
- для каждого члена с атрибутом из `{HttpGet, HttpPost, HttpPut, HttpDelete, HttpPatch, HttpHead, HttpOptions}`:
  `http_method` — имя атрибута без префикса `Http`, в верхнем регистре;
- шаблон метода — первый аргумент атрибута, иначе пустая строка;
- склейка: если шаблон метода начинается с `/` или `~/` — он абсолютный, база отбрасывается;
  иначе `base + "/" + method_template`, лишние слэши схлопываются, ведущий и хвостовой убираются;
- подстановки в результате: `[controller]` → имя типа с отброшенным суффиксом `Controller`;
  `[action]` → имя члена с отброшенным суффиксом `Async`. Регистр подстановок сохраняется как в имени.
- результат сортируется по `(route, http_method)`.

**Критерии приёмки** — `PricingController`:
```python
[Endpoint(http_method="GET",  route="api/v1/Pricing/{id:guid}", member="GetAsync", line=…),
 Endpoint(http_method="POST", route="api/v1/Pricing",           member="RecalculateAsync", line=…)]
```
Для `PricingService` (не контроллер) — пустой список.

**Проверка**
```bash
uv run pytest tests/test_endpoints.py -q
```

---

## T13 — `dotnet/di.py`

**Цель:** регистрации DI и сервисов Ignite.

**Создать:** `docpipe/dotnet/queries/di.scm`, `docpipe/dotnet/di.py`; изменить `docpipe/dotnet/parser.py`; создать `tests/test_di.py`

**Спецификация.** Искать `invocation_expression`, где имя метода совпадает с
`^(Try)?Add(Scoped|Singleton|Transient|HostedService)$`.

**Обход — по всему дереву файла, на любой глубине вложенности.** Не ограничивайся
вызовами внутри `method_declaration`: в современном .NET `Program.cs` пишется через
top-level statements, и регистрации лежат прямо в `global_statement`, без класса
и метода вообще:

```csharp
// Program.cs целиком, без namespace и без класса
var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<IBasketService, BasketService>();
```

В eShopOnWeb так написаны все три `Program.cs`. Реализация, ищущая вызовы внутри
методов (как в фикстуре, где `Program` — обычный статический класс), **не найдёт
ни одной регистрации в реальном проекте**.

Навигация до имени метода (см. раздел о грамматике): `invocation_expression` →
`member_access_expression` → либо `generic_name` → `identifier`, либо напрямую `identifier`.
Получатель может быть цепочкой (`builder.Services`) — это `member_access_expression`
внутри `member_access_expression`, на разбор имени метода не влияет.
`type_argument_list` лежит внутри `generic_name`, **не** внутри `invocation_expression`.
Обработать оба случая; при отсутствии `member_access_expression` (вызов без получателя)
пропустить.

- `lifetime` — из имени метода в нижнем регистре (`AddHostedService` → `hosted`);
- если в `type_argument_list` два аргумента → `service_type` = первый,
  `impl_type` = второй, `confidence = "high"`;
- если один аргумент → `service_type` = `impl_type` = он же, `confidence = "high"`;
- если аргументов типа нет, но есть лямбда/`new` → `service_type` из текста, `impl_type = None`,
  `confidence = "low"`;
- generic-аргументы в именах типов сохраняются как есть (`IPricingProvider<string>`);
- `file`, `line` — 1-based;
- результат сортируется по `(file, line, service_type)`.

Результат заполняет `FileParseResult.di_registrations`.

**Критерии приёмки** — `Program.cs` даёт ровно 3 регистрации:

| service_type | impl_type | lifetime | confidence |
|---|---|---|---|
| `IPricingProvider<string>` | `CurveProvider` | `singleton` | `high` |
| `IPricingService` | `PricingService` | `scoped` | `high` |
| `ValuationWorkflow` | `ValuationWorkflow` | `transient` | `high` |

**Проверка**
```bash
uv run pytest tests/test_di.py -q
```

---

## T14 — `classify.py` и `rules/dotnet.yaml`

**Цель:** движок правил.

**Создать:** `docpipe/classify.py`, `rules/dotnet.yaml`, `tests/test_classify.py`

**Спецификация**

`rules/dotnet.yaml` — ровно такое содержимое:

```yaml
version: "1"
ruleset_version: "2026-07-26.1"

exclude:
  path_glob:
    - "**/obj/**"
    - "**/bin/**"
    - "**/*.g.cs"
    - "**/*.Designer.cs"
    - "**/*.generated.cs"
    - "**/Migrations/**"
  name_regex:
    - "^.*Dto$"
    - "^.*Request$"
    - "^.*Response$"
    - "^.*Options$"
    - "^.*Settings$"
  type_kind_deny: ["enum"]
  require_public: true

rules:
  - id: controller.aspnet
    kind: controller
    template: controller
    priority: 100
    when:
      any:
        - attribute: ["ApiController"]
        - base_type: ["ControllerBase", "Controller"]
        - inherits: ["ControllerBase", "Controller"]

  - id: ignite.service
    kind: ignite_service
    template: ignite-service
    priority: 95
    when:
      all:
        - type_kind: ["class", "record"]
        - inherits: ["IService", "Apache.Ignite.Core.Services.IService"]

  - id: ignite.compute
    kind: ignite_compute
    template: ignite-compute
    priority: 94
    when:
      all:
        - type_kind: ["class", "record"]
        - inherits: ["IComputeFunc", "IComputeJob", "IComputeTask"]

  - id: workflow
    kind: workflow
    template: workflow
    priority: 90
    when:
      all:
        - type_kind: ["class", "record"]
        - any:
            - name_suffix: ["Workflow"]
            - inherits: ["IWorkflow"]

  - id: repository
    kind: repository
    template: repository
    priority: 45
    when:
      all:
        - type_kind: ["class", "record"]
        - name_suffix: ["Repository"]

  - id: provider
    kind: provider
    template: provider
    priority: 50
    when:
      all:
        - type_kind: ["class", "record"]
        - any:
            - name_suffix: ["Provider"]
            - inherits: ["IProvider"]

  - id: service
    kind: service
    template: service
    priority: 40
    when:
      all:
        - type_kind: ["class", "record"]
        - name_suffix: ["Service"]
```

`classify.py`:
```python
@dataclass(frozen=True)
class Classification:
    kind: str; template: str; matched_rules: list[str]

def load_ruleset(path: Path) -> Ruleset: ...
def classify(symbol: Symbol, ruleset: Ruleset) -> Classification | None: ...
```

Предикаты (каждый принимает список значений, семантика — OR внутри списка):

| Предикат | Матчится против |
|---|---|
| `attribute` | имена `symbol.attributes` |
| `base_type` | `symbol.base_types` и `base_types_raw` через `base_type_candidates` (ниже) |
| `inherits` | `symbol.base_type_closure`, тем же способом |
| `name_regex` | `re.fullmatch` по `symbol.name` |
| `name_suffix` | `symbol.name.endswith(v)` |
| `namespace_regex` | `re.fullmatch` по `symbol.namespace` |
| `path_glob` | `fnmatch` по любому `sources[*].path` |
| `type_kind` | `symbol.type_kind` |
| `modifier` | `symbol.modifiers` |
| `has_member_with_attribute` | имена атрибутов любого члена |

Узлы `when` рекурсивны: `any` (OR по потомкам), `all` (AND по потомкам), либо лист-предикат.
Внутри `all`/`any` могут быть вложенные `any`/`all` — см. правило `workflow`.

**Сопоставление имён базовых типов.** Наивное «последний сегмент после точки» работает
для `Sample.Common.Web.BaseApiController` → `BaseApiController`, но ломается на
fluent-базах, которые реально встречаются:

```
EndpointBaseAsync.WithRequest<AuthenticateRequest>.WithActionResult<AuthenticateResponse>
```

Здесь значимо **первое** звено (`EndpointBaseAsync`), а последний сегмент даёт
бессмысленное `WithRequest`. Различить их можно по признаку «точка после первого `<`»:

```python
def base_type_candidates(text: str) -> set[str]:
    head = text.split("<")[0]
    candidates = {text, head, head.split(".")[-1]}
    # Fluent-база X.WithRequest<A>.WithResult<B>: значимо первое звено.
    if "<" in text and "." in text[text.index("<") :]:
        candidates.add(head.split(".")[0])
    return candidates
```

Предикат совпадает, если значение из правила есть в множестве кандидатов. Проверено:

| Базовый тип | Кандидаты |
|---|---|
| `Sample.Common.Web.BaseApiController` | `BaseApiController`, полный FQN |
| `EndpointBaseAsync.WithRequest<A>.WithActionResult<B>` | `EndpointBaseAsync`, `WithRequest`, … |
| `IEndpoint<IResult, ListRequest, IRepository<CatalogItem>>` | `IEndpoint`, полный текст |
| `IPricingProvider<string>` | `IPricingProvider`, полный текст |

Условие с `<` важно: без него `Sample.Common.Web.BaseApiController` дал бы кандидата
`Sample`, и правило `base_type: ["Sample"]` совпало бы со всем подряд.

Алгоритм:
1. Применить `exclude`. Символ отбрасывается, если: любой его `sources[*].path` совпал с
   `path_glob`; `name_regex` совпал; `type_kind` в `type_kind_deny`; `require_public: true`
   и `"public"` отсутствует в `modifiers`. Вернуть `None`.
2. Вычислить **все** правила. Собрать совпавшие.
3. Если совпавших нет → `None`.
4. Победитель: `max` по `priority`, при равенстве — лексикографически меньший `id`.
5. `matched_rules` — **отсортированные id всех** совпавших правил, не только победителя.

**Критерии приёмки** на фикстуре:

| Символ | kind | matched_rules |
|---|---|---|
| `PricingController` | `controller` | `["controller.aspnet"]` |
| `BaseApiController` | `controller` | `["controller.aspnet"]` |
| `RiskComputeService` | `ignite_service` | `["ignite.service", "service"]` |
| `ValuationWorkflow` | `workflow` | `["workflow"]` |
| `CurveProvider` | `provider` | `["provider"]` |
| `PricingService` | `service` | `["service"]` |
| `IPricingService` | `None` (интерфейс не проходит `type_kind`) | — |
| `PriceDto` | `None` (исключён по `name_regex`) | — |
| `GeneratedService` | `None` (исключён по `path_glob`) | — |

Строка `RiskComputeService` — главный тест приоритетов: совпадают два правила, побеждает
Ignite, но в аудите видны оба.

**Проверка**
```bash
uv run pytest tests/test_classify.py -q
```

---

## T15 — `tree.py`: сборка дерева

**Цель:** `Symbol[]` + `Classification[]` → `DocNode[]`.

**Создать:** `docpipe/tree.py`, `tests/test_tree.py`

**Спецификация**

```python
def build_nodes(symbols: dict[str, Symbol], modules: list[Module],
                ruleset: Ruleset, config: DocpipeConfig) -> tuple[list[Module], list[DocNode]]: ...
```

Порядок действий:

1. **Домены.** Для каждого модуля найти первый (в лексикографическом порядке ключей)
   совпавший glob из `config.domains` по пути csproj. Нет совпадения → `domain = module.name`.
2. **Enrollment.** `module.enrolled = any(fnmatch(csproj, g) for g in config.enrolled)`.
3. **Классификация.** Для символов **enrolled** модулей вызвать `classify`. Неклассифицированные
   и символы неenrolled модулей узлов не порождают.
4. **id** узла: `"type:" + symbol.fqn`. id модуля: `"module:" + module.name`.
5. **doc_path**: `docs/modules/{module}/{kind_plural}/{slug}.md`, где `kind_plural` —
   `kind` + `s` (`controller` → `controllers`, `ignite_service` → `ignite_services`),
   `slug = slugify(symbol.name)`.
6. **Коллизии.** Сгруппировать узлы по `doc_path`. Если в группе >1 — **всем** участникам
   добавить суффикс `-{sha256(fqn)[:8]}` перед `.md`.
7. **endpoints** — из T12. **dependencies**:
   - параметры конструктора: тип каждого параметра резолвится по индексу; `via="constructor"`,
     `confidence="high"` если резолвнулся, иначе `"low"`;
   - DI-регистрации, где `impl_type` соответствует символу: `via="di"`.
   Сортировать по `(target, via)`, дедуплицировать.
8. **related**: для каждого символа-класса, чьё `base_type_closure` содержит интерфейс,
   присутствующий в индексе → `relation="implements"`; обратное ребро на интерфейсе не
   создаётся, если интерфейс не стал узлом. Сортировать по `(target, relation)`.
9. **signature_hash**: `stable_hash` от словаря
   ```python
   {"fqn": …, "type_kind": …, "modifiers": sorted(…),
    "base_types": sorted(…), "attributes": [{"name":…, "args":…, "named_args":…}, …],
    "members": sorted([{"name":…, "kind":…, "signature":…,
                        "attributes":[имена]} …], key=…)}
   ```
   **Не включать** номера строк и пути. Перемещение кода или переформатирование не должно
   менять хэш — иначе экономия шага 3 обнуляется.
10. Финальная сортировка `nodes` по `id`, `modules` по `id`.

**Критерии приёмки**
- ровно 6 узлов (`PricingController`, `BaseApiController`, `RiskComputeService`,
  `ValuationWorkflow`, `CurveProvider`, `PricingService`);
- `PricingController.doc_path == "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md"`;
- `BaseApiController.module == "Sample.Common"`;
- у `PricingController` в `dependencies` есть
  `Dependency(target="Sample.Pricing.Api.Services.IPricingService", via="constructor", confidence="high")`;
- `signature_hash` не меняется, если в исходнике добавить пустую строку перед классом
  (тест делает это на временной копии фикстуры);
- искусственный тест коллизии: два класса с одинаковым именем в одном модуле → **оба**
  `doc_path` получают хэш-суффикс.

**Проверка**
```bash
uv run pytest tests/test_tree.py -q
```

---

## T16 — `emit.py` и команда `scan`

**Цель:** первый работающий сквозной прогон.

**Создать:** `docpipe/emit.py`; изменить `docpipe/cli.py`; создать `tests/test_scan_e2e.py`

**Спецификация**

```python
def write_manifest(manifest: Manifest, out: Path) -> None: ...
def write_run_meta(meta: RunMeta, out: Path) -> None: ...   # путь: out с суффиксом .run.json
```

`write_manifest` использует `stable_json_dumps`. Директории создаются при необходимости.

Команда:
```
docpipe scan --root PATH [--config FILE] [--rules FILE] [--out FILE]
             [--no-cache] [--jobs N]
```
Флаги `--scope`, `--from-manifest`, `--stats`, `--dry-run` добавляются в T18/T20 — сейчас их нет.

Оркестрация в `scan`: `discover` → `parse_sln`/`parse_csproj` → построение `file_to_module`
(файл принадлежит модулю с самым длинным общим префиксом пути csproj) → парсинг через кэш →
`build_symbol_index` → `compute_closures` → `build_nodes` → `write_manifest` + `write_run_meta`.

`--jobs N` — параллелизм парсинга через `concurrent.futures.ProcessPoolExecutor`. Результаты
**обязательно** пересортировываются по `path` после сбора. По умолчанию `1`.

**Критерии приёмки**
- `uv run docpipe scan --root tests/fixtures/SampleSolution --out /tmp/dt.json` создаёт
  `/tmp/dt.json` и `/tmp/dt.run.json`;
- `dt.json` валиден по `schema/doc-tree.schema.json` (проверять через
  `Manifest.model_validate_json`);
- в `dt.json` **отсутствует** подстрока текущего года (грубая проверка на отсутствие даты);
- `nodes` — 6 штук, `modules` — 2;
- golden-тест: результат совпадает с зафиксированным `tests/golden/doc-tree.json`.
  Файл создаётся в этой задаче первым прогоном и коммитится.

**Проверка**
```bash
uv run pytest tests/test_scan_e2e.py -q
```

---

## T17 — Тесты детерминизма

**Цель:** превратить требование из `purpose.md` в проверяемое свойство.

**Создать:** `tests/test_determinism.py`

**Спецификация.** Четыре теста:

1. **Двойной прогон.** Два `scan` в разные файлы → байты идентичны.
2. **Независимость от порядка ФС.** Замонкипатчить `Path.rglob` (или обёртку в `discovery`)
   так, чтобы она возвращала элементы в обратном порядке → результат идентичен прогону №1.
3. **Инвариант инкремента.** `full == incremental`:
   - прогон с пустым кэшем → `A`;
   - повторный прогон с тем же кэшем → `B`; `A == B`;
   - изменить один файл фикстуры (во временной копии), прогнать → `C`;
   - прогнать то же изменение с нуля (`--no-cache`) → `D`; `C == D`.
4. **Инвалидация по версии грамматики.** Подменить `parser_versions` → кэш очищается,
   результат по-прежнему равен `A`.

Все тесты работают на **временной копии** фикстуры (`tmp_path`), исходная не изменяется.

**Критерии приёмки** — все 4 теста зелёные.

**Проверка**
```bash
uv run pytest tests/test_determinism.py -q
```

---

## T18 — Скоуп-режим

**Цель:** частичное обновление, не ломающее корректность.

**Изменить:** `docpipe/cli.py`, `docpipe/discovery.py`; создать `docpipe/merge.py`, `tests/test_scoped.py`

**Спецификация**

Новые флаги:
```
--scope DIR            (можно повторять)
--from-manifest FILE   (обязателен вместе с --scope)
```

Если `--scope` задан без `--from-manifest` — завершиться с кодом 2 и сообщением
`"--scope требует --from-manifest"`.

Алгоритм скоуп-прогона:
1. `discover` с фильтром по scope → файлы **в** скоупе;
2. они парсятся (через кэш по `content_hash`);
3. для **всех остальных** файлов из кэша берутся `FileParseResult` через `get_any`
   (без сверки хэша) — это даёт полный индекс символов;
4. файлы, которых нет ни в скоупе, ни в кэше, пропускаются с предупреждением в stderr;
5. `resolve` + `compute_closures` работают по **полному** индексу;
6. `build_nodes` вызывается только для символов, чьи `sources` попадают в scope;
7. `merge.py` объединяет: узлы вне scope берутся из `--from-manifest` как есть; узлы в scope —
   новые. Узел считается «в scope», если **любой** его `sources[*].path` в scope;
8. `modules` объединяются так же по `id`;
9. `partial = PartialInfo(scope=sorted(scope), outside_from_cache=True)`;
10. финальная сортировка по `id`.

```python
def merge_manifests(previous: Manifest, partial: Manifest, scope: list[str]) -> Manifest: ...
```

**Критерии приёмки** — главный инвариант:

```python
# 1. полный прогон -> full.json
# 2. скоуп-прогон --scope src/Sample.Pricing.Api --from-manifest full.json -> scoped.json
# 3. assert scoped.nodes == full.nodes  (сравнение по всем полям, кроме manifest.partial)
```

Дополнительно:
- скоуп-прогон по `src/Sample.Common` также даёт узлы, идентичные полному прогону;
- `PricingController` в скоуп-прогоне по `src/Sample.Pricing.Api` **сохраняет**
  `kind == "controller"` — доказательство, что транзитивное наследование через
  `Sample.Common` не потерялось при том, что этот модуль не парсился;
- `--scope` без `--from-manifest` → код возврата 2.

Второй пункт — то, ради чего вся конструкция с индексом символов. Если он падает,
не обходи его: значит, кэш не используется для резолва.

**Проверка**
```bash
uv run pytest tests/test_scoped.py -q
```

---

## T19 — `diff.py` и команда `diff`

**Цель:** вход для шага 3.

**Создать:** `docpipe/diff.py`, `tests/test_diff.py`; изменить `docpipe/cli.py`

**Спецификация**

```python
class NodeChange:  node_id: str
                   change: Literal["added","removed","reclassified","signature_changed","moved"]
                   details: dict[str, str]

def diff_manifests(old: Manifest, new: Manifest) -> list[NodeChange]: ...
```

Правила (проверяются в этом порядке, на узел приходится одно изменение):
- id есть только в new → `added`;
- id есть только в old → `removed`;
- `kind` отличается → `reclassified` (`details = {"from":…, "to":…}`);
- `doc_path` отличается → `moved` (`details = {"from":…, "to":…}`);
- `signature_hash` отличается → `signature_changed`.

Узлы без изменений в результат не попадают. Результат сортируется по `(node_id, change)`.

Команда `docpipe diff OLD NEW [--format text|json]`. `json` печатает через `stable_json_dumps`.
Код возврата 0 всегда (наличие изменений — не ошибка).

**Критерии приёмки**
- diff манифеста с самим собой → пустой список;
- удаление файла `ValuationWorkflow.cs` из копии фикстуры → ровно один `removed`;
- переименование `ValuationWorkflow` → `PricingWorkflow` даёт один `added` и один `removed`;
- добавление метода в `PricingService` → ровно один `signature_changed`.

**Проверка**
```bash
uv run pytest tests/test_diff.py -q
```

---

## T20 — `stats`, `--dry-run`, `validate`

**Цель:** инструменты настройки правил на большом проекте.

**Изменить:** `docpipe/cli.py`, `docpipe/tree.py`; создать `tests/test_stats.py`

**Спецификация**

`docpipe scan --stats` — печатает в stdout таблицу счётчиков и **не пишет** манифест.
На фикстуре вывод должен быть в точности такой (числа реальные, тест их проверяет):

```
kind              count
----------------  -----
controller            2
ignite_service        1
provider              1
service               1
workflow              1
unclassified          3
excluded              1
----------------  -----
total symbols        10
```

Разбор этих чисел (пригодится при отладке): 6 классифицированных —
`BaseApiController` и `PricingController` → `controller`, `RiskComputeService` →
`ignite_service`, `CurveProvider` → `provider`, `PricingService` → `service`,
`ValuationWorkflow` → `workflow`. 3 неклассифицированных — `IPricingProvider` и
`IPricingService` (интерфейсы не проходят `type_kind: [class, record]`) и `Program`
(не подходит ни под одно правило). 1 исключённый — `PriceDto` (по `name_regex`).

`GeneratedService` в счётчики **не попадает вообще**: он отсекается на этапе discovery
и символом не становится. Это ожидаемо — `excluded` считает только то, что отбросила
секция `exclude` уже на уровне классификации.

`unclassified` — символы enrolled-модулей, не подошедшие ни под одно правило.
`excluded` — отброшенные секцией `exclude`. Для сбора этих счётчиков `classify` должен
возвращать причину: расширить возврат до `Classification | Literal["excluded"] | None`
либо добавить отдельную функцию `classify_with_reason`.

**Отдельная категория `interface_covered`.** Интерфейс, у которого в индексе есть
хотя бы одна реализация, ставшая узлом, — это не «не смогли классифицировать», а
осознанное решение документировать реализацию. В eShopOnWeb таких 9 из 199
«неклассифицированных» (`IBasketService`, `IOrderService`, …). Смешивать их с типами,
про которые правила действительно ничего не знают, нельзя: цифра `unclassified`
существует ровно для того, чтобы по ней настраивать правила, и мусор в ней
обесценивает команду.

Итоговый набор счётчиков: `<kind>…`, `interface_covered`, `unclassified`, `excluded`.

`--dry-run` — выполняет полный прогон, но вместо записи печатает diff против существующего
`--out` (если файла нет — печатает `added` для всех узлов).

`docpipe validate MANIFEST.json` — валидация через `Manifest.model_validate_json`.
Код возврата 0 при успехе, 1 при ошибке, сообщение об ошибке в stderr.

`docpipe stats MANIFEST.json` — та же таблица, но по готовому манифесту (без `unclassified`).

**Критерии приёмки**
- `--stats` на фикстуре печатает таблицу с числами из спецификации выше
  (`controller 2`, `ignite_service 1`, `provider 1`, `service 1`, `workflow 1`,
  `unclassified 3`, `excluded 1`, `total symbols 10`) и **не создаёт** выходной файл;
- `--dry-run` на неизменённой фикстуре против существующего манифеста печатает «изменений нет»;
- `validate` на валидном манифесте → код 0; на файле `{}` → код 1.

**Проверка**
```bash
uv run pytest tests/test_stats.py -q
```

---

## T21 — Финализация

**Цель:** проект готов к запуску на реальном репозитории.

**Создать:** `README.md`, `docpipe.yaml` (пример), `tests/test_smoke_cli.py`

**Спецификация**

`README.md`: установка, быстрый старт, описание каждой команды CLI, ссылка на
`docs/parser-architecture.md`, раздел «как добавить новое правило» с примером.

`docpipe.yaml` — рабочий пример конфигурации с комментариями, показывающий `enrolled`
и `domains`.

`tests/test_smoke_cli.py` — прогон всех команд через `typer.testing.CliRunner`,
проверка кодов возврата.

**Критерии приёмки** — полная проверка проходит:

```bash
uv run ruff check . \
  && uv run ruff format --check . \
  && uv run mypy docpipe \
  && uv run pytest -q
```

Плюс ручная проверка на фикстуре:
```bash
uv run docpipe scan --root tests/fixtures/SampleSolution --out /tmp/a.json
uv run docpipe scan --root tests/fixtures/SampleSolution --out /tmp/b.json
diff /tmp/a.json /tmp/b.json && echo "ДЕТЕРМИНИЗМ OK"
uv run docpipe scan --root tests/fixtures/SampleSolution --stats
```

---

## Сводка зависимостей между задачами

```
T00 ─┬─ T01 ─────────────────────────────────────────────┐
     └─ T02 ─── T03 ─┬─ T04 ─┬─ T05 ──────────────┐      │
                     │       └─ T06 ─ T07 ─ T08 ──┤      │
                     └─ T09 ─────────────────────┐│      │
                                                 ▼▼      ▼
                                          T10 ─ T11 ─┬─ T12 ─┐
                                                     ├─ T13 ─┤
                                                     └─ T14 ─┤
                                                             ▼
                                                            T15 ─ T16 ─┬─ T17
                                                                       ├─ T18
                                                                       ├─ T19
                                                                       └─ T20 ─ T21
```

Задачи T12, T13, T14 независимы друг от друга — при параллельной работе их можно брать
одновременно после T11.

---

## Что НЕ входит в этот план

Чтобы не было соблазна начать делать раньше времени:

- создание директорий и `.md`-файлов (шаг 2 пайплайна);
- шаблоны документации (Service / Provider / Workflow / Controller);
- генерация `docs/_views/**`;
- парсеры Python и TypeScript;
- любая интеграция с LLM.
