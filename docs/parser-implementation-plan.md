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

**Директивы препроцессора: `#if` — контейнер, `#region` — нет.** Асимметрия неочевидная,
проверена на ABP (см. [findings-abp.md](findings-abp.md)).

```
declaration_list
├── method_declaration One       ← прямой потомок
├── preproc_if                   ← КОНТЕЙНЕР
│   ├── method_declaration Two       ← НЕ прямой потомок тела
│   └── preproc_else
│       └── method_declaration TwoOld
├── preproc_region               ← плоский маркер, не контейнер
├── method_declaration Three     ← остаётся прямым потомком
└── preproc_endregion
```

Препроцессор tree-sitter **не выполняет**: обе ветки `#if/#else` присутствуют в дереве
одновременно, оба варианта члена попадут в вывод. Это осознанное поведение — документируем
все варианты, а не тот, что собрался бы при конкретном наборе символов компиляции.

**`#if` внутри выражения ломает разбор, и объявление может исчезнуть целиком.** Директива
внутри списка аргументов атрибута рвёт выражение; атрибут — потомок объявления, поэтому
ломается и объявление: остаток файла переразбирается как top-level statements, и
`class_declaration` пропадает из вывода. В ABP так теряется `CmsKitWebUnifiedModule`
(8 ошибок, 0 объявлений). Восстановится ли грамматика — зависит от того, что идёт дальше
по файлу; опираться на это нельзя.

Обойти нельзя, это предел tree-sitter. Единственный надёжный признак —
**`parse_errors > 0` при пустом списке объявлений**; T20 обязан ловить эту комбинацию.
Фикстура: `WildSolution/src/Wild.Api/Modules/ConditionalModule.cs`.

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

**Создать:** дерево `tests/fixtures/WildSolution/` (13 файлов `.cs`, 2 `.csproj`, 1 `.sln`),
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
│   ├── Modules/ConditionalModule.cs      #if в аргументах атрибута — разбор ломается
│   ├── Pages/Login.cshtml.cs             вложенный InputModel
│   ├── Pages/Register.cshtml.cs          вложенный InputModel (то же имя)
│   └── Services/CrudAppService.cs        три ICrudAppService с арностями 1, 2, 3
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
| `#if` в аргументах атрибута | тип исчезает из вывода молча | T06, T20 |
| Перегрузка по арности дженерика | три типа склеиваются в один узел | T10, T15 |

**Критерии приёмки**
- ровно 13 файлов `.cs` и 2 `.csproj` по перечисленным путям;
- каждый файл, **кроме `Modules/ConditionalModule.cs`**, разбирается
  с `parse_errors == 0` и `has_error is False`;
- `ConditionalModule.cs`: `parse_errors > 0`, среди детей корня **нет**
  `class_declaration`, но есть `global_statement`, а в тексте между `[DependsOn(`
  и `)]` присутствует `#if`. Проверять обе половины: без первой тест сломается,
  если файл «починят», без второй — если грамматика научится восстанавливаться;
- `CrudAppService.cs`: три `interface_declaration` с одним именем `ICrudAppService`
  и арностями 1, 2, 3;
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
exclude: []                   # globs по пути файла: куда не заходить вовсе
domains: {}                   # glob по пути csproj -> имя домена
rules: "rules/dotnet.yaml"
out: "artifacts/doc-tree.json"
cache_dir: ".docpipe/cache"
```
Функция `load_config(path: Path | None) -> DocpipeConfig`; при `None` возвращает дефолты.

**Три похожих понятия, которые нельзя смешивать.** Читающий план обязан различать:

| | что делает | символ в индексе |
|---|---|---|
| `enrolled` здесь | что документируем | остаётся |
| `exclude` в `rules/dotnet.yaml` (T14) | что не документируем, но читаем | остаётся |
| `exclude` здесь | куда не заходим вовсе | **исчезает** |

Третье нужно ровно для чужого кода внутри репозитория: инструмента, положенного в
дерево продукта, вендоренных зависимостей, выгрузок. Наследование через исключённый
код рвётся — это цена, и для тестов такой механизм не годится.

`exclude` **складывается** со встроенным набором, а не замещает его (см. T16,
`exclude_globs`). Замещение означало бы, что одна строка в конфигурации молча
возвращает `obj/` и `bin/` в документацию.

**Ловушка:** шаблон обязан заканчиваться на `/**`. `"docs"` совпадёт с самим каталогом,
но не с файлами под ним, и будет выглядеть работающим. Тест на это обязателен —
без него ошибка обнаружится только на чужом репозитории.

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
- симлинк на каталог с исходниками не порождает дублей;
- `exclude` из конфигурации: в дереве с посторонним `.csproj` и `.cs` под `docs/`
  прогон без исключения находит их, с `exclude: ["docs/**"]` — нет;
- `exclude: ["docs"]` (без `/**`) **не** исключает файлы под каталогом. Тест
  фиксирует ловушку, а не желаемое поведение: правильный шаблон — только с `/**`.

**Проверка**
```bash
uv run pytest tests/test_discovery.py -q
```

---

## T05 — `dotnet/csproj.py` и `dotnet/sln.py`

**Цель:** граф модулей.

**Создать:** `docpipe/dotnet/__init__.py`, `docpipe/dotnet/csproj.py`, `docpipe/dotnet/sln.py`, `tests/test_project_graph.py`

**Спецификация**

**Шесть ловушек, проверенных на реальных проектах** (eShopOnWeb — 10 `.csproj`,
ABP — 671 `.csproj` и 30 решений):

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

4. **Имена проектов не уникальны.** В ABP 39 повторяющихся имён, включая три разных
   `MyCompanyName.MyProjectName.csproj`. Поэтому `id = "module:" + репо-относительный
   путь к csproj`, а **не** `"module:" + name`: путь уникален по построению и считается
   локально из одного файла, без глобального прохода по всем модулям.

5. **`ProjectReference` сопоставлять по пути, а не по имени.** По той же причине:
   `Path(include).stem` при неуникальных именах уводит ребро графа в произвольный
   из одноимённых модулей. Разрешать `Include` относительно каталога проекта
   в репо-относительный путь, сравнимый с полем `csproj` другого модуля
   (на ABP так разрешаются 2383 ссылки из 2383).

6. **Неразвёрнутые подстановки MSBuild.** MAUI-проект ABP объявляет
   `<TargetFrameworks>$(TargetFrameworks);net10.0-ios;…`, и в манифест попадала бы
   строка `$(TargetFrameworks)`, неотличимая от настоящей платформы. Значения с `$(`
   отбрасывать.

Полного вычисления свойств MSBuild не делать: условия, подстановки `$(…)` и цепочки
`Import` не разворачиваются. Задача — структурные факты, а не воспроизведение сборки.

`csproj.py` — `parse_csproj(path: Path, repo_root: Path) -> Module`:
- `name` = имя файла без `.csproj`; `id` = `"module:" + csproj` (репо-относительный путь);
- `target_frameworks`: из `<TargetFramework>` или `<TargetFrameworks>` (сплит по `;`),
  при отсутствии — унаследованные из props-файлов (см. выше), отбросить значения
  с `$(`, отсортировать;
- `project_references`: из `<ProjectReference Include="...">`, разрешить относительно
  каталога проекта в репо-относительный путь POSIX, отсортировать. Ссылку за пределы
  корня обхода оставить как есть — потерять ребро молча хуже, чем сохранить неразрешённое;
- `package_references`: из `<PackageReference Include="...">`, отсортировать;
- парсинг через `xml.etree.ElementTree`; игнорировать XML-namespace, если он присутствует
  (сравнивать по локальному имени тега);
- `domain` и `enrolled` на этом этапе заполняются заглушками (`""`, `True`) — их проставит T15.

`sln.py` — `parse_sln(path: Path, repo_root: Path) -> list[str]`: вернуть репо-относительные
пути `.csproj`. **Форматов два, выбирать по суффиксу:**

- `.sln` — текстовый, строки `Project(...) = "...", "путь", "..."`, регулярное выражение.
  Файл читать как UTF-8 с `errors="replace"`.
- `.slnx` — XML (VS 17.10+), элементы `<Project Path="…"/>`. Читать **байтами** (тот же BOM).
  Проекты лежат либо прямо под `<Solution>`, либо во вложенных `<Folder>`, поэтому обходить
  всё дерево (`root.iter()`), а не только детей корня.

> `.slnx` игнорировать нельзя: ABP мигрировал на него целиком — **30 файлов `.slnx`
> и ноль `.sln`**, поиск только по `.sln` не нашёл бы там ни одного решения.
> `discovery` собирает оба расширения в `sln_files`.

Разделители `\` → `/`, результат отсортировать.

**Фильтровать по расширению `.csproj`.** Записи `Project(...)` описывают не только
проекты C#: папки решения (тип `{2150E333-…}`) кладут в поле пути собственное имя
(`"src", "src"` — не файл), а рядом встречаются `.dcproj`, `.vcxproj`, `.esproj`.
В `eShopOnWeb.sln` из 14 записей `Project(...)` только 10 — проекты C#.

**Критерии приёмки**
- `Sample.Pricing.Api`: `id == "module:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"`,
  `target_frameworks == ["net8.0", "net9.0"]`,
  `project_references == ["src/Sample.Common/Sample.Common.csproj"]`,
  `package_references == ["Apache.Ignite"]`.
- `Sample.Common`: `target_frameworks == ["net8.0"]`, остальные списки пусты.
- `parse_sln` возвращает ровно 2 пути, оба существуют на диске.
- На `WildSolution`: `Wild.Api.csproj` разбирается несмотря на BOM;
  `Wild.Tests.csproj` **без собственного** `TargetFramework` даёт `["net8.0"]`
  из `Directory.Build.props`; `..\..\src\Wild.Api\Wild.Api.csproj` даёт
  `["src/Wild.Api/Wild.Api.csproj"]`; `PackageReference` с вложенными `PrivateAssets`
  разбирается.
- Синтетические случаи: legacy-`.csproj` с `xmlns`; `.sln` с папкой решения
  и `.dcproj`; `.slnx` с проектами на разной глубине `<Folder>` и с BOM; ближайший
  props-файл побеждает дальний; собственный TFM побеждает унаследованный;
  `$(TargetFrameworks)` отбрасывается; два одноимённых `Common.csproj` в разных
  каталогах получают **разные** `id`; битый XML падает с `ET.ParseError`, а не даёт
  пустой модуль.

**Проверка**
```bash
uv run pytest tests/test_project_graph.py -q
```

---

## T05b — связанные исходники (`<Compile Include>`)

**Статус:** задача добавлена по итогам стресс-теста, см. [findings-stress.md](findings-stress.md).
Приоритет невысокий — делать после T21, если нужно.

**Цель:** привязать к модулю файлы, подключённые ссылкой, а не расположением.

**Проблема.** Правило «файл принадлежит ближайшему `.csproj` вверх по дереву» неверно
для общего кода, подключённого через `<Compile Include>`:

```xml
<Compile Include="$(RepoRoot)\src\Shared\Guard.cs" Link="Includes\Guard.cs" />
```

Такой файл лежит вне каталога любого проекта и не получает модуля вовсе — значит,
не попадает в индекс, не порождает документа и не участвует в графе наследования.

Замер: 39 файлов и 50 типов (5,0 %) в OpenTelemetry, 131 файл и 205 типов (4,8 %)
в semantic-kernel. В ABP и eShopOnWeb — ноль.

**Почему это не одна строчка.** Связанный файл компилируется в **несколько** сборок
сразу, а нынешний `file_to_module: dict[str, str]` предполагает ровно один модуль
на файл. Придётся решить два вопроса:

1. какой тип сигнатуры у `build_symbol_index` — `dict[str, list[str]]`, и тогда один
   файл порождает символы в каждом модуле (как в реальной компиляции), либо
   «первый по алфавиту» с пометкой;
2. в каком модуле показывать документ — иначе один и тот же `Guard` появится
   в документации десяти проектов.

Плюс в `Include` те же подстановки MSBuild, что и в `ProjectReference`, — понадобится
тот же приём с резолвом по имени файла.

**Перед реализацией** проверить, есть ли приём в целевом репозитории:
`grep -rc "Compile Include" --include=*.csproj .` Если ноль, задачу можно закрыть
как неактуальную.

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
- `containing_type` — **цепочка** охватывающих типов через `.` (`Outer.Middle` для `Inner`),
  иначе `None`. Именно цепочка, а не ближайший предок: при двойной вложенности одного
  имени не хватит, чтобы собрать корректный FQN в T10;
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

**`verbatim_string_literal` — единый лист без потомка-содержимого.** Проверено:
`@"api\v1"` разбирается в один узел вместе с `@` и кавычками, `string_literal_content`
внутри нет. Снимать вручную: `raw.removeprefix("@").strip('"').replace('""', '"')`.

**Квалифицированные имена атрибутов приводить к простому.**
`[System.ComponentModel.Description]` → `Description`. Правила классификации пишутся
по простому имени, и полный путь только помешал бы совпадению.

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

**Спецификация.** В `members.scm` перечислить **шесть** узлов:
`method_declaration`, `property_declaration`, `field_declaration`, `constructor_declaration`,
`event_field_declaration`, `event_declaration`.

> **Событие объявляется двумя разными узлами.** `event_field_declaration` — обычная форма
> (`public event EventHandler Changed;`), `event_declaration` — форма с `add`/`remove`.
> Забыть первую легко: в имени узла нет слова, по которому станешь её искать. В ABP
> обычных событий вдвое больше.

Принадлежность члена типу считать **обходом вверх**: ближайший предок из
`_TYPE_KIND_BY_NODE` и есть владелец. Группировать по `Node.id` — узлы дерева
нехэшируемы, а `id` уникален в пределах одного разбора.

> **Не перебирать прямых детей `declaration_list`.** Члены под `#if` прямыми детьми тела
> **не являются** — они уходят под `preproc_if` / `preproc_else` (см. раздел о грамматике),
> и такая реализация потеряла бы их молча. Запрос находит их на любой глубине, а обход
> вверх сам разводит вложенные типы: член вложенного типа найдёт своим владельцем его,
> а не внешний тип. `#region`, в отличие от `#if`, контейнером не является и не мешает.
>
> Обе ветки `#if/#else` дают члены с одинаковым именем — это ожидаемо, дедуплицировать
> **не нужно**: документируем оба варианта. Сортировка по `(line, name)` разведёт их
> детерминированно.

- `kind` — по типу узла (оба узла события дают `"event"`);
- `name` — из `name: (identifier)`. У `field_declaration` и `event_field_declaration`
  поля `name` нет: имена лежат в `variable_declaration` → `variable_declarator`,
  и их может быть **несколько** (`private int _a = 1, _b = 2;`). Порождать по одному
  `Member` на каждый declarator; правило «взять первый» теряло бы `_b` молча
  (на ABP таких полей 0 из 1652, но цена корректности здесь нулевая);
- `signature` — текст от первого потомка, не являющегося `attribute_list` или тривией,
  до первого потомка из `{block, arrow_expression_clause, accessor_list, ";"}`,
  с схлопыванием любых последовательностей пробельных символов в один пробел
  и обрезкой по краям. Резать по узлам, а не поиском символа `{` в тексте: `{`
  встречается внутри инициализаторов и строковых литералов. Ограничения
  `where T : class` и `: base(x)` стоят до тела и в сигнатуру **входят**;

> **Комментарии и `#pragma` вырезаются из сигнатуры, в том числе изнутри диапазона.**
> Обычно это соседи объявления, но не всегда: `#pragma` между атрибутом и модификатором
> становится **потомком** узла, и сигнатура начиналась бы с
> `#pragma warning disable CS0809 // Obsolete member…`. Комментарий может стоять и внутри
> списка параметров — после схлопывания пробелов границу строки уже не найти, и `//`
> съел бы остаток сигнатуры. На четырёх реальных репозиториях так портились
> **252 сигнатуры**, а вместе с ними и `signature_hash`.
>
> Вырезать нужно по байтовым диапазонам узлов `comment` и `preproc_*`, а не текстом:
> `//` встречается и внутри строковых литералов (`"otpauth://totp/…"`).
- `modifiers` — отсортированы;
- `attributes` — тем же кодом, что в T06;
- `line` / `end_line` — 1-based;
- `xml_doc` — тем же кодом, что в T06.

Список `members` сортируется по `(line, name)`. На разных строках это совпадает
с порядком объявления; два члена на одной строке выстраиваются по имени.

**Что намеренно не извлекается:** `indexer_declaration`, `operator_declaration`,
`conversion_operator_declaration`, `destructor_declaration`, `delegate_declaration`.
В `MemberKind` таких значений нет, а на 3000 файлов ABP их суммарно 20 против 14 000
обычных членов. Понадобятся — добавляются строкой в `members.scm` и записью в
`_MEMBER_KIND_BY_NODE`, но сначала нужно расширить `MemberKind` в `model.py`.

**Сигнатура поля включает инициализатор** (`private int _a = 1`): `{` коллекции лежит
внутри `variable_declaration`, а не отдельным потомком, поэтому обрезка по узлам его
не отсекает. Обычно это полезно (значение `const` — часть контракта), но хвост тяжёлый:
на ABP медиана сигнатуры поля 52 символа, p99 — 187, максимум **1283** (поле с ASCII-графикой
в инициализаторе). Шаг 3 должен быть готов усекать сигнатуру при выводе.

**Критерии приёмки** — `PricingController`:
- 4 члена: поле `_pricing`, конструктор `PricingController`, методы `GetAsync`, `RecalculateAsync`;
- у `GetAsync` — `attributes == [Attribute(name="HttpGet", args=["{id:guid}"], named_args={})]`;
- у `RecalculateAsync` — `attributes == [Attribute(name="HttpPost", args=[], named_args={})]`;
- `signature` метода `GetAsync` равна
  `"public async Task<ActionResult<decimal>> GetAsync(Guid id, CancellationToken ct)"`.

`RiskComputeService` — ровно 3 метода. `PriceDto` (record с primary constructor) — не падает,
`members` может быть пустым.

Отдельный тест на препроцессор (разбор из строки, фикстуру не трогать):

```csharp
public class A {
    public void One() { }
#if NET8_0_OR_GREATER
    public void Two() { }
#else
    public void TwoOld() { }
#endif
#region Helpers
    public void Region() { }
#endregion
    public void Three() { }
}
```

Ожидается **пять** членов: `One`, `Two`, `TwoOld`, `Region`, `Three`. Реализация, идущая
по прямым детям тела, вернёт три — `One`, `Region`, `Three`.

Второй тест — вложенный тип не отдаёт свои члены наружу:
`class Outer { void OuterM() { } class Inner { void InnerM() { } } }` →
у `Outer` ровно один член `OuterM`, у `Inner` — один `InnerM`.

Третий — обе формы события: `public event EventHandler Plain;` и
`public event EventHandler Custom { add { } remove { } }` дают по одному члену
с `kind == "event"`.

Четвёртый — поле с двумя объявителями: `private int _a = 1, _b = 2;` даёт **два** члена
с общей сигнатурой.

Пятый — обрезка сигнатуры: тело, стрелка, аксессоры и `;` абстрактного метода
дают `"public int Block()"`, `"public int Arrow()"`, `"public int Auto"`,
`"public abstract int Abstract(int a)"`.

**Проверка**
```bash
uv run pytest tests/test_parser_members.py -q
```

---

## T08 — `dotnet/parser.py`: usings

**Цель:** заполнить `usings` и `global_usings`.

**Изменить:** `docpipe/dotnet/parser.py`; создать `docpipe/dotnet/queries/usings.scm`,
`tests/test_parser_usings.py`

**Спецификация.** Искать `using_directive` **запросом по всему дереву**, а не перебором
детей `compilation_unit`: директива может лежать внутри блочного namespace
(`namespace N { using X; … }`) — в ABP таких 8. Ложных срабатываний внутри методов
не будет: `using var s = …` разбирается как `local_declaration_statement`,
а `using (…) { }` — как `using_statement`.

Различение по **прямым потомкам** (`global` и `static` — узлы анонимные, в
`named_children` не попадают):
- есть потомок `=` → алиас (`using X = A.B.C;`), **игнорировать полностью**;
- есть потомок `static` → `using static`, **игнорировать**;
- есть потомок `global` → в `global_usings`;
- иначе → в `usings`.

Порядок проверок важен: `global using static` должен отбрасываться, а не попадать
в `global_usings`.

Имя — текст **последнего именованного потомка**: у алиаса перед именем стоит ещё
`identifier` самого алиаса, у прочих форм оно единственное. Узел имени бывает трёх
типов: `identifier` (`using System;`), `qualified_name` (`using A.B.C;`) и
`alias_qualified_name` (`using global::AutoMapper;`).

> **Префикс `global::` снимать.** Это квалификатор глобального пространства имён,
> а не часть имени: `using global::AutoMapper;` импортирует ровно `AutoMapper`,
> и без снятия префикса резолв на T10 такой namespace не найдёт. В ABP таких две штуки.
>
> Здесь же ловушка для реализации, ищущей слово `global` в тексте директивы:
> в этой форме `global` прямым потомком **не является**, и global using это не делает.
>
> Прочие extern-алиасы (`using MyLib::Some.Namespace;`) оставлять как есть: без
> разрешения алиасов сборок они всё равно не резолвятся, а врать в манифесте хуже.

Оба списка сортируются и дедуплицируются.

**Критерии приёмки** — `PricingController.cs`:
`usings == ["Microsoft.AspNetCore.Mvc", "Sample.Common.Web", "Sample.Pricing.Api.Services"]`,
`global_usings == []`.

На `WildSolution/src/Wild.Api/GlobalUsings.cs` (файл существует ровно ради этого):
`global_usings == ["System.Text.Json", "Wild.Api.Contracts"]`, `usings == []`.

Отдельные тесты на формы, каждая из которых ломает наивную реализацию:
`global using static System.Console;` → **не** в `global_usings`;
`using global::AutoMapper;` → `usings == ["AutoMapper"]`;
директива внутри блочного namespace → находится;
`using var s = …` внутри метода → директивой не считается.

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
после апгрейда грамматики: содержимое файла не изменилось, хэш тот же, и без сверки версий
кэш вернул бы разбор, сделанный старой грамматикой.

**Попадание — по хэшу содержимого, никогда по `mtime`.** Время модификации меняется при
checkout и при переносе репозитория, не меняя ни байта в файле; кэш на `mtime` то промахивался
бы там, где не должен, то попадал бы там, где нельзя.

`get` возвращает `None` при несовпадении `content_hash`.
`get_any` нужен скоуп-режиму (§2.3 архитектуры) — файлы вне скоупа не читаются вовсе,
поэтому их хэш неизвестен и сверять его не с чем. Свежесть там не гарантируется, и ровно
поэтому такой манифест помечается `partial`.
Класс должен работать как контекстный менеджер.

**Битую БД пересоздавать, а не падать.** Кэш — расходный материал: оборванная запись,
обрезанный файл или посторонний файл с тем же именем должны стоить одного лишнего разбора,
а не отказа команды с советом «удалите каталог». Открытие проверяется запросом
`SELECT count(*) FROM sqlite_master`; на `sqlite3.DatabaseError` файл удаляется и создаётся
заново. Пустой файл (0 байт) — валидная пустая БД, пересоздавать его не нужно.

**Не коммитить каждый `put`.** На десятках тысяч файлов это десятки тысяч fsync. Коммит —
в `close()`, то есть при выходе из `with`. Незакоммиченные записи видны через `get`
в том же соединении, поэтому на поведение внутри прогона это не влияет, а на аварийном
завершении теряется кэш — не результат.

Уровень сжатия `zlib` задать явно: значение по умолчанию менялось между версиями Python,
а payload хочется иметь воспроизводимым побайтово.

**Критерии приёмки**
- `put` → `get` с тем же хэшем возвращает эквивалентный объект;
- `get` с другим хэшем возвращает `None`;
- повторный `put` того же пути заменяет запись, а не добавляет вторую;
- пересоздание `ParseCache` с другими `parser_versions` очищает кэш (`all_paths() == []`);
- то же при изменённом `meta.cache_version`; при **тех же** версиях кэш сохраняется;
- `prune({"a.cs"})` при трёх записях удаляет 2 и возвращает `2`;
- кэш переживает закрытие/открытие файла БД;
- файл с мусором вместо БД не роняет открытие, а пересоздаётся;
- через кэш проходит **весь** `FileParseResult`, а не только объявления: у восстановленного
  объекта непусты и `usings`, и `members` первого объявления.

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
def symbol_key(module: str, fqn: str, arity: int) -> str: ...
def declaration_fqn(declaration: RawDeclaration) -> str: ...
def strip_generics(text: str) -> str: ...
def build_symbol_index(results: list[FileParseResult],
                       file_to_module: dict[str, str]) -> dict[str, Symbol]: ...
def index_by_fqn(index: dict[str, Symbol]) -> dict[str, list[str]]: ...
```

`file_to_module` сопоставляет репо-относительный путь файла с путём его `.csproj`
(тем же значением, что в `Module.csproj`). Файлы без модуля пропускаются: документировать
код, не входящий ни в один проект, всё равно некуда.

Шаг 1 — FQN объявления: `namespace + "." + containing_type + "." + name`, пустые части
пропускаются. Type parameters в FQN **не входят**.

Шаг 2 — ключ индекса: `symbol_key(модуль, FQN, арность)`, то есть
`{csproj}#{fqn}\`{арность}`.

> **Ключом не может быть один FQN** — проверено на ABP, 255 коллизий на 9075 объявлений.
> Причин две, и обе дают молчаливую потерю документов:
>
> - **перегрузка по арности дженерика.** `ICrudAppService`, `ICrudAppService<T>` и
>   `ICrudAppService<T, K>` — три разных типа с одинаковым FQN. Таких групп 112,
>   рекорд — шесть арностей у одного имени;
> - **один FQN в разных сборках.** Это законно в C#; ключ без модуля склеил бы 155 таких
>   пар (в ABP — шаблоны проектов и параллельные реализации вроде
>   `Volo.Abp.AutoMapper` / `Volo.Abp.LuckyPenny.AutoMapper`).
>
> Формат ключа **обязан совпадать** с id узла на T15 (там добавляется префикс `type:`).
> Две разные схемы ключей приведут к тому, что узлы перестанут сопоставляться с символами.
>
> После этого ключа на ABP остаются 3 коллизии — файлы, которые никогда не компилируются
> вместе (`Platforms/iOS/` против `Platforms/MacCatalyst/`, `EntityFrameworkCore/` против
> `MongoDB/`). Они сольются в один символ с двумя источниками; отдельного разделения
> не делаем, но T20 обязан сообщать о символе с несколькими источниками **без модификатора
> `partial`** — это либо такой случай, либо неверно заданный scope.

Шаг 3 — слияние. Объявления с одинаковым ключом сливаются в один `Symbol`:
- `sources` — все `span`, отсортированные по `(path, start)`;
- `modifiers`, `attributes` — объединение, отсортировано, дедуплицировано;
- `base_types_raw` — объединение сырых имён, отсортировано; `base_types` — **параллельный**
  ему список резолвов, а не отдельно отсортированное множество. Соответствие
  raw ↔ resolved нужно на T11, чтобы восстановить арность базового типа; две независимо
  отсортированные коллекции его теряют. Если половинки `partial` резолвят одно имя
  по-разному (usings у файлов разные), побеждает первая по порядку файлов;
- `members` — конкатенация, сортировка по `(path_из_span, line, name)`; при отсутствии
  привязки к файлу — по `(line, name)`;
- `xml_doc` — первый непустой в порядке отсортированных `sources`;
- `type_kind`, `name`, `namespace`, `type_parameters` — из первого объявления в том же порядке;
- `module` — из `file_to_module` по первому источнику.

Шаг 4 — резолв имён из `base_types_raw` в FQN. Резолвить **до** слияния, каждое объявление
со своим набором usings: половинки `partial class` лежат в разных файлах с разными usings.

Набор видимых usings для файла = его собственные `usings` **плюс все `global_usings`
своего модуля**.

> `global using` действует на весь проект, а не на файл, в котором объявлен, и обычно
> все они собраны в одном `GlobalUsings.cs`. Реализация, берущая usings только своего
> файла, не резолвила бы **ни один** тип, видимый исключительно через них. Границу модуля
> при этом пересекать нельзя: проекты изолированы.

Для имени `N` в объявлении с namespace `NS` и видимыми usings `U`:
1. отбросить `<...>` — получить базовое имя `B`;
2. если `B` содержит `.` и `B` есть в индексе → результат `B`;
3. перебрать `NS` и его префиксы **от длинного к короткому**, включая пустой префикс, —
   побеждает **первое** совпадение;
4. только если ничего не нашлось — перебрать `U`: собрать **все** совпадения, взять
   лексикографически меньшее и при количестве >1 выставить `ambiguous = True`;
5. если совпадений нет — оставить `B` как есть (внешний тип).

> **Namespace и usings проверяются последовательно, а не скопом.** Правило «собрать все
> совпадения из namespace и usings разом» пометило бы `ambiguous` обычный случай, когда
> тип виден и по namespace, и по using, — хотя в C# ближнее имя просто перекрывает дальнее.
> На ABP последовательное правило даёт **1** неоднозначность на 8966 символов, и она
> настоящая (два `IFourthDbContext`, видимые через два разных using).
>
> **Пустой префикс namespace обязателен**: глобальное пространство имён видно из любого
> места, и тип без namespace должен находиться.

`strip_generics` удаляет **сбалансированные** группы `<…>`, а не всё от первого `<`:
`A.B<C<D>>.E` — это вложенный тип `E` внутри generic-типа `B`, его FQN равен `A.B.E`,
а обрезка по первому `<` дала бы `A` и потеряла бы тип. Префикс `global::` снимается.

Результат кладётся в `base_types`. `base_types_raw` сохраняет исходный текст.

> **`base_types` хранит FQN, а не ключи символов.** По ним матчатся правила классификации
> (`base_type: ["EndpointBaseAsync"]`), и ключ с модулем и арностью там был бы нечитаем.
> Там, где по FQN нужно найти символ, используется `index_by_fqn` — он возвращает **список**
> ключей, потому что один FQN законно принадлежит нескольким типам (на ABP таких FQN 261).

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
- ни у одного символа `ambiguous is True`;
- ключи индекса отсортированы, повторный вызов даёт равный результат.

На `WildSolution`: три `Wild.Api.Services.ICrudAppService` дают **три** символа
с арностями 1, 2, 3. Реализация с ключом по одному FQN даст здесь один символ —
это главная проверка задачи.

Синтетические случаи (разбор из строк, фикстуры не трогать):
- один и тот же FQN в двух модулях → **два** символа;
- `A.B.Target` перекрывает `A.Target` для класса из `A.B`, и `ambiguous is False`;
- два using, дающие одно имя, → `ambiguous is True`, выбран лексикографически меньший;
- `global using Lib;` в отдельном файле резолвит тип в **другом** файле того же модуля,
  но не в другом модуле;
- тип в глобальном namespace находится из класса с namespace.

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
def base_type_arity(raw: str) -> int: ...
def compute_closures(index: dict[str, Symbol]) -> dict[str, Symbol]: ...
```

Для каждого символа обойти `base_types` в ширину, добавляя базовые типы найденных символов.
Нерезолвнутые имена попадают в замыкание, но дальше не раскрываются: их определений
у нас нет. Защита от циклов через множество посещённых. Результат — отсортированный
дедуплицированный список, **не включающий** сам символ.

**Переход «базовый тип -> символ» идёт по паре (FQN, арность), а не по одному FQN.**

> `base_types` хранит FQN, а FQN типы не различает. На ABP **813 рёбер наследования
> из 7400** ведут к группе символов с одинаковым FQN и разной арностью — переход
> по одному FQN раскрыл бы произвольный из них.
>
> Крайний случай — `IChatClientAccessor : IChatClientAccessor<T>` (реальный тип из ABP):
> база имеет тот же FQN, что и наследник, и переход по FQN даёт петлю на себя.
> Из-за этого же собственный FQN символа исключается из его замыкания — иначе правило
> по базовому типу начало бы матчить сам тип.
>
> Арность базового типа берётся из `base_types_raw` по **последней** группе `<…>`
> верхнего уровня: в `A.B<X>.C<Y>` базовым является `C` с одним параметром, а не `B`.
>
> Отсюда требование к T10: `base_types` и `base_types_raw` должны быть **параллельны**
> (элемент `i` одного соответствует элементу `i` другого). Две независимо
> отсортированные коллекции соответствие теряют, и арность не восстановить.

**Выбор среди символов с одинаковыми FQN и арностью:** сначала свой модуль, иначе
лексикографически меньший ключ. На ABP это снимает 846 неоднозначных рёбер до 8:
1967 разрешаются своим модулем, 4438 — тем, что кандидат ровно один.

**Критерии приёмки**
- `PricingController.base_type_closure == ["ControllerBase", "Sample.Common.Web.BaseApiController"]`
  — транзитивно через границу модуля. Это ключевой тест всей архитектуры;
- `RiskComputeService.base_type_closure == ["IService"]`;
- `PriceDto.base_type_closure == []`;
- цепочка `A : B : C : D` раскрывается целиком; ромб `A : B, C`, `B : D`, `C : D`
  даёт `D` один раз;
- искусственный тест на цикл `A : B`, `B : A` завершается и не зацикливается;
  то же для `A : A`;
- `IAccessor : IAccessor<object>`, `IAccessor<T> : IMarker` → у неродового
  `IAccessor` замыкание равно `["IMarker"]`, собственный FQN в него не попал;
- `IService<T> : IOneArg`, `IService<T,K> : ITwoArgs`, `User : IService<int,string>` →
  замыкание `User` содержит `ITwoArgs`, а не `IOneArg`. Реализация без учёта арности
  провалит именно этот тест.

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
- шаблон метода — первый аргумент `Http*`-атрибута, **а при его отсутствии — первый
  аргумент атрибута `[Route]` на том же члене**, и только потом пустая строка;

> **`[Route]` на методе — не экзотика, а преобладающая форма.** В ABP так написаны
> **245 из 356** HTTP-атрибутов:
>
> ```csharp
> [Route("api/abp/multi-tenancy")]
> public class AbpTenantController
> {
>     [HttpGet]
>     [Route("tenants/by-name/{name}")]
>     public Task<TenantDto> FindTenantByNameAsync(string name) { }
> ```
>
> Реализация, читающая только аргумент `Http*`, выдала бы **всем** методам такого
> контроллера один и тот же маршрут — маршрут типа. Таблица эндпоинтов при этом
> выглядела бы правдоподобно и была бы бесполезной.
>
> Случая, когда аргументы есть и у `Http*`, и у `[Route]`, в обоих репозиториях нет
> ни разу, поэтому приоритета достаточно простого: `Http*` важнее.

- склейка: если шаблон метода начинается с `/` или `~/` — он абсолютный, база отбрасывается;
  иначе `base + "/" + method_template`, лишние слэши схлопываются, ведущий и хвостовой убираются;
- подстановки в результате: `[controller]` → имя типа с отброшенным суффиксом `Controller`;
  `[action]` → имя члена с отброшенным суффиксом `Async`. Значение подставляется с исходным
  регистром имени (`PricingController` → `Pricing`), а сами токены сравниваются
  **без учёта регистра**: для ASP.NET `[Controller]` и `[controller]` — одно и то же;
- результат сортируется по `(route, http_method, member)`.

**Что намеренно не разбирается:**

- **конвенциональная маршрутизация** (`MapControllerRoute` с шаблоном `{controller}/{action}`) —
  она задаётся в конфигурации приложения, а не на типе. Метод такого контроллера даёт
  эндпоинт с **пустым** `route`: то, что он обрабатывает GET, — факт, а путь неизвестен.
  В ABP таких 10 из 356;
- **minimal API** (`app.MapGet(...)`) — не привязан к типу и эндпоинтом контроллера не является;
- **`[Route]` без `Http*`** — маршрут без ограничения по глаголу, а `Endpoint.http_method`
  обязателен. В ABP таких членов 11.

**Критерии приёмки** — `PricingController` (порядок именно такой: сортировка по маршруту,
а `api/v1/Pricing` — префикс второго):
```python
[Endpoint(http_method="POST", route="api/v1/Pricing",           member="RecalculateAsync", line=24),
 Endpoint(http_method="GET",  route="api/v1/Pricing/{id:guid}", member="GetAsync",         line=18)]
```
Для `PricingService` (не контроллер) — пустой список.

Отдельный тест на форму из ABP: `[Route("api")]` на типе плюс `[HttpGet]` и
`[Route("sub/{id}")]` на методе дают `api/sub/{id}`. Реализация по плану без этой правки
вернёт `api` — это главная проверка задачи.

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
- **если типов-аргументов нет, разобрать `typeof(...)` в обычных аргументах:**
  - `typeof(X), typeof(Y)` → то же, что два типа-аргумента, `confidence = "high"`;
  - `typeof(X)` → то же, что один;
  - `typeof(X), <что-то ещё>` → `service_type = X`, `impl_type = None`, `confidence = "medium"`;
- **всё остальное пропускать**;
- generic-аргументы в именах типов сохраняются как есть (`IPricingProvider<string>`,
  `IKernelAccessor<>`). Резолвом занимается T15, и он обязан снимать generic-часть
  при сопоставлении с символом;
- `file`, `line` — 1-based;
- результат сортируется по `(line, service_type, impl_type)`.

> **`typeof` — не запасной вариант, а обычная форма записи.** В ABP и eShopOnWeb так
> записаны **47 регистраций из 267** — больше, чем двумя типами-аргументами:
> `TryAddTransient(typeof(IKernelAccessor<>), typeof(KernelAccessor<>))`. Имена типов
> здесь названы так же явно, как в `<…>`, и занижать `confidence` не за что.
>
> **Регистрацию без единого имени типа нужно пропускать, а не выдумывать ей тип.**
> Первая редакция плана предписывала в этом случае брать «`service_type` из текста».
> Текстом там оказывается тело лямбды (`sp => sp.GetRequiredService<X>()`),
> переменная (`AddSingleton(builder)`) или приведение типа — в поле `service_type`
> манифеста попал бы мусор. Регистрация, не называющая ни одного типа, документации
> ничего не даёт: связать её с символом нельзя. Таких вызовов 50 из 267.

Результат заполняет `FileParseResult.di_registrations`.

**Критерии приёмки** — `Program.cs` даёт ровно 3 регистрации:

| service_type | impl_type | lifetime | confidence |
|---|---|---|---|
| `IPricingService` | `PricingService` | `scoped` | `high` |
| `IPricingProvider<string>` | `CurveProvider` | `singleton` | `high` |
| `ValuationWorkflow` | `ValuationWorkflow` | `transient` | `high` |

(порядок — по строке объявления)

**Второй обязательный тест** — `WildSolution/src/Wild.Api/Program.cs` на top-level
statements: 4 регистрации (`scoped`, `singleton`, `transient` через `TryAdd`, `hosted`)
при полном отсутствии объявлений типов в файле. Реализация, ищущая вызовы внутри
`method_declaration`, пройдёт критерии на `SampleSolution` и провалит этот.

Синтетические случаи: `typeof(X), typeof(Y)`; одиночный `typeof(X)`;
`typeof(X)` с фабрикой → `medium` и `impl_type is None`; лямбда без типов,
переменная и приведение типа → **пусто**; `builder.Services.AddScoped<…>`
(цепочка получателей); `AddControllers()` и `AddDbContext<T>()` → пусто
(имя не подходит под шаблон); `AddScoped<IFoo, Foo>()` без получателя → пусто.

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
    - "**/test/**"
    - "**/tests/**"
    - "**/*Test/**"
    - "**/*Tests/**"
  name_regex:
    - "^.*Dto$"
    - "^.*Request$"
    - "^.*Response$"
    - "^.*Options$"
    - "^.*Settings$"
    - "^.*Tests?$"
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

**Про исключение тестов.** Одного правила по имени типа мало, и это не очевидно.
Само по себе `^.*Tests?$` отсеивает **ноль** узлов на всех четырёх проверочных
репозиториях — потому что класс `PricingServiceTests` не оканчивается ни на `Service`,
ни на `Controller`, то есть и так не проходит ни одно правило классификации.

Реальный шум идёт из вспомогательных типов **внутри** тестовых проектов: они называются
как продуктовые (`AuditTestController`, `AuthorizationTestPermissionDefinitionProvider`)
и потому классифицируются на общих основаниях. На ABP их 104 узла из 828. Отсекает их
только `path_glob`.

Правило по имени всё равно нужно: оно фиксирует намерение и сработает, когда в набор
добавят более широкое правило (например, по атрибуту `[Fact]` или по `type_kind`).

> Регулярное выражение `^.*Tests?$` покрывает обе формы: `*Tests` встречается на порядок
> чаще (в ABP 777 против 54). В предметных областях, где «тест» — бизнес-понятие
> (в финансовом моделировании это `StressTest`, `BackTest`), правило нужно сузить.

Правила в файле идут по убыванию приоритета — это только для читаемости.
**Порядок строк в YAML на результат влиять не должен**, и на это есть отдельный тест:
при равном приоритете побеждает лексикографически меньший `id`, иначе перестановка
правил меняла бы манифест.

`classify.py`:
```python
@dataclass(frozen=True)
class Classification:
    kind: str; template: str; matched_rules: list[str]

def load_ruleset(path: Path) -> Ruleset: ...
def is_excluded(symbol: Symbol, ruleset: Ruleset) -> bool: ...
def classify(symbol: Symbol, ruleset: Ruleset) -> Classification | None: ...
```

`is_excluded` вынесен отдельно, потому что T20 обязан различать «исключён» и
«не подошёл ни под одно правило»: это разные счётчики, и смешивать их нельзя —
`unclassified` существует ровно для настройки правил.

**`load_ruleset` проверяет структуру целиком, а не по мере применения.** Опечатка
в имени предиката (`attribut` вместо `attribute`) иначе дала бы правило, которое просто
никогда не срабатывает: набор молча теряет вид сущности, и заметить это можно только
по счётчику `unclassified`, то есть никак. Проверять: известность предиката, тип значения
(список строк), компилируемость регулярных выражений, наличие обязательных полей
у правила, уникальность `id`.

Предикаты (каждый принимает список значений, семантика — OR внутри списка):

| Предикат | Матчится против |
|---|---|
| `attribute` | имена `symbol.attributes` |
| `base_type` | `symbol.base_types` и `base_types_raw` через `base_type_candidates` (ниже) |
| `inherits` | `symbol.base_type_closure`, тем же способом |
| `name_regex` | `re.fullmatch` по `symbol.name` |
| `name_suffix` | `symbol.name.endswith(v)` |
| `namespace_regex` | `re.fullmatch` по `symbol.namespace` |
| `path_glob` | `discovery.matches_glob` по любому `sources[*].path` — **не `fnmatch`**: тот не понимает `**` как «ноль или больше сегментов» |
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

`GeneratedService` отбрасывается ещё на обходе ФС, поэтому в тесте символ для него
строится напрямую из файла. Правило `path_glob` всё равно нужно: файл может попасть
в разбор из кэша или в скоуп-режиме.

Отдельные тесты на загрузку: неизвестный предикат, повтор `id`, битое регулярное
выражение и правило без обязательных полей обязаны падать **на `load_ruleset`**,
а не молча давать никогда не срабатывающее правило.

**Проверка**
```bash
uv run pytest tests/test_classify.py -q
```

### Чего ожидать от набора по умолчанию

Набор намеренно консервативен, и на чужом коде он покрывает мало. Замер на четырёх
репозиториях:

| | символов | классифицировано | исключено | `unclassified` |
|---|---|---|---|---|
| eShopOnWeb | 253 | 18 (7 %) | 36 | 199 |
| ABP | 8966 | 828 (9 %) | 996 | 7142 |
| OpenTelemetry | 945 | 10 (1 %) | 535 | 400 |
| semantic-kernel | 4057 | 69 (2 %) | 1492 | 2496 |

Это не дефект, а исходное состояние: словарь «контроллер / сервис / провайдер /
репозиторий» описывает прикладной код, а не SDK. Набор правил набирается по
`docpipe scan --stats` на конкретном проекте — для этого команда и нужна.

Что полезно знать при настройке: **главный рычаг исключений — `require_public`**.
В библиотечных репозиториях он отсекает больше всего (463 символа в OpenTelemetry
и 1203 в semantic-kernel — там много `internal`-реализаций), а в прикладных
на первое место выходит `name_regex` (508 из 996 в ABP).

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
4. **id** узла: `"type:" + module.csproj + "#" + symbol.fqn + "`" + арность`.
   id модуля: `"module:" + module.csproj` (уже так реализовано в T05).

   > **FQN не уникален — проверено на ABP**, см. [findings-abp.md](findings-abp.md).
   > На 9075 объявлениях: по одному только `fqn` — 255 коллизий, по `module + fqn` — 108,
   > по `module + fqn + арность` — 3. Две независимые причины:
   >
   > - **перегрузка по арности дженерика**: `IObjectMapper`, `IObjectMapper<T>` и
   >   `IObjectMapper<TSource,TDest>` — три разных типа с одним FQN (таких групп 112,
   >   рекорд — шесть арностей у одного имени);
   > - **одинаковые FQN в разных сборках** — законно в C# и встречается в шаблонах.
   >
   > Оставшиеся 3 коллизии — файлы, которые никогда не компилируются вместе
   > (`Platforms/iOS/Program.cs` против `Platforms/MacCatalyst/Program.cs`). Их разводит
   > только путь: если после ключа `module + fqn + арность` коллизия всё же осталась —
   > добавить `-{sha256(путь к файлу)[:8]}` и **записать это в `stats`**, потому что
   > почти всегда это сигнал о неверно заданном scope, а не о норме.

5. **doc_path**: `docs/modules/{module.name}/{kind_plural}/{slug}.md`, где `kind_plural` —
   `kind` + `s` (`controller` → `controllers`, `ignite_service` → `ignite_services`),
   `slug = slugify(symbol.name)`.
6. **Коллизии `doc_path`.** Сгруппировать узлы по `doc_path`. Если в группе >1 — **всем**
   участникам добавить суффикс `-{sha256(id)[:8]}` перед `.md`.

   > Суффикс считается от **id узла, а не от FQN**. Хэш от FQN не разводит ничего там,
   > где FQN совпадает: на ABP внутри одного модуля 195 групп типов дают одинаковый slug,
   > и в 105 из них FQN тоже одинаков — суффикс от FQN дал бы тем же типам тот же файл.
   > id включает арность и путь, поэтому уникален по построению.
   >
   > Имя модуля в пути (`{module.name}`) тоже может повторяться — в ABP 39 таких имён.
   > Для АС CF это маловероятно, но `validate` (T20) обязан проверять, что `doc_path`
   > всех узлов различны, и падать, если нет.
7. **endpoints** — из T12. **dependencies**:
   - параметры конструктора: тип каждого параметра резолвится по индексу; `via="constructor"`,
     `confidence="high"` если резолвнулся, иначе `"low"`;
   - DI-регистрации, где `impl_type` соответствует символу: `via="di"`, `target` —
     сервис, под которым тип зарегистрирован. Сопоставлять **через `strip_generics`**:
     T13 сохраняет имена как написаны, и открытый дженерик `KernelAccessor<>` буквально
     с именем символа `KernelAccessor` не совпадёт. Регистрация типа на самого себя
     (`AddTransient<ValuationWorkflow>()`) ребра **не даёт**: петля в графе — артефакт
     формы записи, а не факт о коде.
   Сортировать по `(target, via)`, дедуплицировать.

   > **Параметры конструктора разбираются из текста сигнатуры.** Структурированного
   > списка параметров `Member` не хранит, поэтому нужен разбор строки — и в нём две
   > ловушки, обе найдены на реальном коде:
   >
   > - **резать по парной скобке, а не по последней.** Сигнатура конструктора включает
   >   инициализатор: `public C(IOptions o) : base(o)`. По `rfind(")")` получился бы
   >   «параметр» `IOptions o) : base(o`. На ABP так ломался **каждый пятый**
   >   конструктор — 217 рёбер из 1016;
   > - **разделять запятые только на верхнем уровне.** Иначе
   >   `IReadOnlyDictionary<string, int> map` распадётся на два «параметра», и оба
   >   будут мусором.
   >
   > Тип параметра — всё, что осталось после отбрасывания атрибутов (`[FromServices]`),
   > модификаторов (`ref`, `out`, `params`, …) и значения по умолчанию, до имени;
   > имя всегда последнее слово.
   >
   > Проверка результата: на четырёх реальных репозиториях **ни одно** извлечённое имя
   > не должно быть непохожим на тип (регулярка `^[A-Za-z_][\w.]*(<.*>)?(\[\])*\??$`
   > либо кортеж в скобках).
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
  `doc_path` получают хэш-суффикс;
- на `WildSolution/src/Wild.Api/Services/CrudAppService.cs` (три `ICrudAppService`
  с арностями 1, 2, 3 — один FQN на всех) получается **три разных id и три разных
  `doc_path`**. Это прямая проверка того, что ключ учитывает арность: реализация
  на одном FQN даст здесь один узел вместо трёх.

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
DEFAULT_EXCLUDE = ["**/obj/**", "**/bin/**", "**/*.g.cs"]

def exclude_globs(config: DocpipeConfig) -> list[str]: ...  # DEFAULT_EXCLUDE + config.exclude
def write_manifest(manifest: Manifest, out: Path) -> None: ...
def write_run_meta(meta: RunMeta, out: Path) -> None: ...   # путь: out с суффиксом .run.json
```

`write_manifest` использует `stable_json_dumps`. Директории создаются при необходимости.

`exclude_globs` возвращает `sorted(set(...))`: складывает встроенные шаблоны с
заданными в конфигурации (T04), а не замещает их. Обход зовётся именно через неё —
`discover(root, exclude_globs(config), scope)`, а не с константой напрямую.

Команда:
```
docpipe scan --root PATH [--config FILE] [--rules FILE] [--out FILE]
             [--no-cache] [--jobs N]
```
Флаги `--scope`, `--from-manifest`, `--stats`, `--dry-run` добавляются в T18/T20 — сейчас их нет.

**У `--out` не должно быть значения по умолчанию на уровне флага.** Путь берётся из
`config.out`, и флаг перебивает его, только когда задан явно:
`destination = out or Path(settings.out)`, тип параметра — `Path | None`. Значение
по умолчанию у самого флага делает поле `out` в конфигурации мёртвым: она выглядит
применённой, а манифест уезжает в `artifacts/` рядом с текущим каталогом. Ровно эта
ошибка и была допущена — обнаружилась при настройке на боевом репозитории, где
конфигурация лежит не рядом с местом запуска.

Оркестрация в `scan`: `discover` → `parse_sln`/`parse_csproj` → построение `file_to_module`
(файл принадлежит модулю с самым длинным общим префиксом пути csproj) → парсинг через кэш →
`build_symbol_index` → `compute_closures` → `build_nodes` → `write_manifest` + `write_run_meta`.

`--jobs N` — параллелизм парсинга через `concurrent.futures.ProcessPoolExecutor`. Результаты
**обязательно** пересортировываются по `path` после сбора. По умолчанию `1`.

**Сидкар должен нести список сломанных файлов.** `RunMeta.parse_error_files` — пути,
где разбор дал ошибки и **ни одного** объявления. Это единственный внешний признак того,
что директива препроцессора внутри выражения уничтожила тип целиком, поэтому нужен именно
список, а не счётчик: T20 обязан по нему падать, а `scan` — предупреждать в выводе.

**Критерии приёмки**
- `uv run docpipe scan --root tests/fixtures/SampleSolution --out /tmp/dt.json` создаёт
  `/tmp/dt.json` и `/tmp/dt.run.json`;
- `dt.json` валиден по `schema/doc-tree.schema.json` (проверять через
  `Manifest.model_validate_json`);
- в `dt.json` **отсутствуют значения из сидкара**: `meta.generated_at`, `meta.host`
  и `meta.duration_seconds` не встречаются в тексте манифеста;

  > Проверять «нет подстроки текущего года» **нельзя**: `ruleset_version` сам выглядит
  > как дата (`2026-07-26.1`), и такая проверка падает на нём же, ничего не проверив.
  > Сравнение с конкретными значениями из сидкара точнее и ловит именно утечку.

- два прогона подряд дают побайтово одинаковый манифест;
- `nodes` — 6 штук, `modules` — 2;
- `--jobs 4` и `--jobs 1` дают **равные** манифесты; прогон с кэшем и без кэша — тоже;
- на `WildSolution` сидкар содержит
  `parse_error_files == ["src/Wild.Api/Modules/ConditionalModule.cs"]`,
  а вывод команды предупреждает об этом;
- без флага `--out` манифест пишется туда, куда указывает `out` из `--config`;
  с флагом — туда, куда указывает флаг;
- посторонний `.csproj` с `.cs` под `docs/` попадает в манифест отдельным модулем,
  а с `exclude: ["docs/**"]` в конфигурации манифест совпадает с прогоном по дереву
  без этого каталога. Контрольная половина проверки обязательна: иначе тест пройдёт
  и на дереве, где исключать нечего;
- golden-тест: результат совпадает с зафиксированным `tests/golden/doc-tree.json`.
  Файл создаётся в этой задаче первым прогоном и коммитится.

  > Золотой файл содержит версии грамматики, поэтому их апгрейд его ломает — и это
  > желаемое поведение. Обновлять файл нужно осознанно, разобравшись, изменение это
  > или регрессия.

**Проверка**
```bash
uv run pytest tests/test_scan_e2e.py -q
```

---

## T17 — Тесты детерминизма

**Цель:** превратить требование из `purpose.md` в проверяемое свойство.

**Создать:** `tests/test_determinism.py`

**Спецификация.** Пять групп тестов:

1. **Двойной прогон.** Два `scan` в разные файлы → байты идентичны.
2. **Независимость от порядка ФС.** Замонкипатчить **`discovery.os.walk`** так, чтобы
   он отдавал `dirnames` и `filenames` в обратном порядке → результат идентичен прогону №1.

   > В плане изначально стояло `Path.rglob` — его в `discovery` нет: обход идёт через
   > `os.walk`, чтобы можно было отсекать каталоги правкой `dirnames` на месте.
   > Разворачивать нужно **оба** списка: порядок каталогов влияет на порядок файлов.

3. **Инвариант инкремента.** `full == incremental`:
   - прогон с пустым кэшем → `A`;
   - повторный прогон с тем же кэшем → `B`; `A == B`;
   - изменить один файл фикстуры (во временной копии), прогнать → `C`;
   - прогнать то же изменение с нуля (`--no-cache`) → `D`; `C == D`;
   - отдельно: **удалить** файл и убедиться, что кэш его не воскрешает (за это отвечает
     `prune`), а инкрементальный результат по-прежнему равен полному.
4. **Инвалидация по версии грамматики.** Подменить `parser_versions` → кэш очищается,
   `modules` и `nodes` по-прежнему равны прогону `A`.

   > Сравнивать манифесты целиком **нельзя**: версии грамматики лежат в самом манифесте,
   > поэтому поле `parser` законно изменится. Сравнивать нужно всё остальное.

5. **Перенос репозитория.** Две копии фикстуры в разных каталогах дают побайтово равные
   манифесты; кэш, наполненный из одной копии, годится для другой.

   > Прямая проверка правила «пути только репо-относительные». Абсолютный путь в манифесте
   > ломал бы документацию при любом различии в расположении рабочих копий у разработчика
   > и в CI, причём незаметно — у автора-то всё сходится.

Плюс проверки свойств самого манифеста: **все** списки отсортированы, и запись-чтение
через JSON обратимы.

Все тесты работают на **временной копии** фикстуры (`tmp_path`), исходная не изменяется.

**Критерии приёмки** — все тесты зелёные.

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
3. из кэша берутся `FileParseResult` для путей **вне скоупа** через `get_any`
   (без сверки хэша) — это даёт полный индекс символов;

   > Отбор идёт по **принадлежности пути скоупу**, а не по правилу «чего не нашли
   > обходом, то возьмём из кэша». Разница видна на удалении файла: он внутри скоупа,
   > обходом уже не находится, но в кэше ещё лежит — и наивное правило **воскресило бы
   > удалённый тип**. Это не гипотеза, на этом упал первый вариант реализации.

4. файлы, которых нет ни в скоупе, ни в кэше, пропускаются с предупреждением в stderr;
5. **`cache.prune` в скоуп-режиме не вызывается.** Файлов вне скоупа обход не видел,
   и `prune` снёс бы ровно то, ради чего кэш здесь и нужен;
6. `resolve` + `compute_closures` работают по **полному** индексу. Модули вне скоупа
   известны только из `--from-manifest` (обход туда не заходил), поэтому список `.csproj`
   для привязки файлов к модулям — объединение найденного и `previous.modules[*].csproj`;
7. `build_nodes` вызывается по полному индексу, но в частичный манифест попадают только
   узлы, чьи `sources` в scope;
8. `merge.py` объединяет: узлы вне scope берутся из `--from-manifest` как есть; узлы
   в scope — новые. Узел считается «в scope», если **любой** его `sources[*].path` в scope.
   Старые узлы в скоупе отбрасываются **до** добавления новых, иначе удалённый тип
   остался бы в дереве навсегда;
9. `modules` объединяются так же по `id`;
10. `partial = PartialInfo(scope=sorted(scope), outside_from_cache=True)`;
11. финальная сортировка по `id`.

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

- изменение файла **вне** скоупа скоуп-прогон не видит — это честная граница режима,
  а не дефект, и на неё нужен отдельный тест;
- удаление файла **внутри** скоупа убирает узел из объединённого манифеста;
- `partial.scope` записан и отсортирован.

Второй пункт — то, ради чего вся конструкция с индексом символов. Если он падает,
не обходи его: значит, кэш не используется для резолва.

**Холодный кэш ломает больше, чем кажется.** При пустом кэше символов вне скоупа взять
неоткуда, и `PricingController` не просто теряет часть данных — он **исчезает
из документации целиком**: правило смотрит на `ControllerBase`, до которого два шага
наследования через `Sample.Common`, цепочка обрывается на первом звене, ни одно правило
не совпадает, узел не порождается, а старый выброшен при слиянии как «в скоупе».

Отсюда требование к команде: если файлы вне скоупа не нашлись в кэше, предупреждать
в stderr. Тест на это обязателен — он фиксирует, что режим работает только поверх
тёплого кэша.

**Замер на ABP** (671 проект, 7869 файлов): полный прогон 10,2 с, скоуп по одному
модулю — **2,1 с**, при этом 301 файл разобран, 7568 восстановлены из кэша, и `nodes`
совпали с полным прогоном ровно.

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

**Изменить:** `docpipe/cli.py`, `docpipe/emit.py`; создать `docpipe/stats.py`,
`tests/test_stats.py`

Счётчики и проверки живут в отдельном модуле, а не в `tree.py`: там сборка узлов,
и сто строк статистики её бы заслонили. `emit.run` возвращает `ScanResult`
с манифестом, метаданными и статистикой; `emit.scan` остаётся обёрткой,
отдающей первые два, — чтобы не переписывать все вызовы ради одного поля.

**Спецификация**

`docpipe scan --stats` — печатает в stdout таблицу счётчиков и **не пишет** манифест.
На фикстуре вывод должен быть в точности такой (числа реальные, тест их проверяет):

```
kind                count
------------------  -----
controller              2
ignite_service          1
provider                1
service                 1
workflow                1
interface_covered       2
unclassified            1
excluded                1
------------------  -----
total symbols          10
```

Ширина первой колонки — не 16, а по самой длинной метке: `interface_covered` длиннее,
и при фиксированной ширине метка съезжает в колонку чисел.

Разбор этих чисел (пригодится при отладке): 6 классифицированных —
`BaseApiController` и `PricingController` → `controller`, `RiskComputeService` →
`ignite_service`, `CurveProvider` → `provider`, `PricingService` → `service`,
`ValuationWorkflow` → `workflow`. 2 в `interface_covered` — `IPricingProvider`
и `IPricingService`: правила их не берут (`type_kind: [class, record]`), но у обоих
есть документируемая реализация. 1 неклассифицированный — `Program`. 1 исключённый —
`PriceDto` (по `name_regex`).

> Сумма всех счётчиков обязана равняться `total`: каждый символ учтён ровно один раз,
> иначе цифрам нельзя верить. На это есть отдельный тест.

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

**Счётчики обязаны учитывать `enrolled`.** Символ модуля, который не входит
в документацию, — это `not_enrolled`, а не `unclassified`: правила к нему и не
применялись. На semantic-kernel без этого разделения `unclassified` состоял из таких
символов **целиком** (1258 из 1258), и настраивать по нему было нельзя. В срезы
«чего не хватает правилам» неenrolled тоже не попадает.

Итоговый набор счётчиков: `<kind>…`, `interface_covered`, `unclassified`, `excluded`,
`not_enrolled`.

**Одних счётчиков для настройки правил не хватает.** Число «7142 неклассифицировано»
не говорит, *какие* правила писать. Поэтому `--stats` должен печатать ещё и срезы
по неклассифицированным символам — топ-15 по каждому:

- модули (сразу видно, что половина — тесты и примеры, то есть вопрос к `enrolled`);
- окончания имён (`Service`, `Handler`, `Module`, …);
- базовые типы **с учётом замыкания** (на ABP это мгновенно показывает
  `ITransientDependency` — 997 типов, больше, чем весь набор по умолчанию покрывает);
- атрибуты;
- namespace.

Реализовано в `docpipe/stats.py`; временный скрипт `tools/unclassified.py`, служивший
до этого, удалён.

`--dry-run` — выполняет полный прогон, но вместо записи печатает diff против существующего
`--out` (если файла нет — печатает `added` для всех узлов).

`docpipe validate MANIFEST.json` — валидация через `Manifest.model_validate_json`.
Код возврата 0 при успехе, 1 при ошибке, сообщение об ошибке в stderr.

Схемой дело не ограничивается — проверить ещё три инварианта, каждый из которых
однажды нарушался на реальном коде:

1. **Уникальность `id`** узлов и модулей. Совпадение означает, что ключ построен неверно
   и часть документации потерялась молча (на ABP `module:{name}` давал 39 дублей).
2. **Уникальность `doc_path`.** Два узла, пишущие в один файл, — это потеря документа
   на шаге 2, а не косметика.
3. **Символы с несколькими `sources` без модификатора `partial`.** Тип, объявленный
   в двух файлах и при этом не `partial`, скомпилироваться не может — значит, эти файлы
   никогда не собираются вместе (`Platforms/iOS/` против `Platforms/MacCatalyst/`)
   либо в scope попали две копии одного дерева. На ABP таких три из 8966.
   Предупреждение, а не ошибка: слияние в один документ здесь допустимо.
4. **Файлы с `parse_errors > 0`, не давшие ни одного объявления.** Это не «файл без типов»,
   а сломанный разбор: `#if` внутри аргументов атрибута рвёт объявление, и тип исчезает
   целиком (см. [findings-abp.md](findings-abp.md)). Единственный внешний признак — именно
   эта комбинация, поэтому она должна давать **ненулевой код возврата** со списком путей,
   а не строку в логе. Для этого `scan` обязан класть в сидкар `doc-tree.run.json` список
   таких файлов.

Пустой список объявлений сам по себе — норма (`Program.cs` на top-level statements,
`GlobalUsings.cs`), поэтому проверяется именно пара «ошибки есть **и** объявлений нет».

`docpipe stats MANIFEST.json` — та же таблица, но по готовому манифесту (без `unclassified`).

**Критерии приёмки**
- `--stats` на фикстуре печатает таблицу с числами из спецификации выше
  (`controller 2`, `ignite_service 1`, `provider 1`, `service 1`, `workflow 1`,
  `interface_covered 2`, `unclassified 1`, `excluded 1`, `total symbols 10`)
  и **не создаёт** ни манифест, ни сидкар;
- `--dry-run` на неизменённой фикстуре против существующего манифеста печатает «изменений нет»;
- `validate` на валидном манифесте → код 0; на файле `{}` → код 1;
- `validate` на манифесте с двумя узлами, у которых совпадает `doc_path`, → код 1;
- `scan` на `WildSolution` (там лежит `Modules/ConditionalModule.cs`, теряющий объявление)
  записывает его путь в `doc-tree.run.json`, и `validate` даёт код 1 со списком этих файлов.

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

## T22 — Поставка в репозиторий АС CF

**Цель:** запускать `docpipe` на боевом репозитории, не перенося туда среду разработки.

**Создать:** `deploy/` (`install.sh`, `pyproject.toml`, `uv.lock`, `gitignore`, `README.md`,
`cashflow-docspipe/{docpipe.yaml,rules.yaml,run.sh,README.md}`), `tests/test_deploy_bundle.py`

**Спецификация**

Инструмент кладётся **внутрь** репозитория АС CF, в `docs/ml/docspipe`. Отсюда три
требования, каждое из которых иначе даёт молчаливую ошибку:

1. **`exclude: ["docs/**"]` в конфигурации поставки.** Иначе обход заходит в каталог
   инструмента вместе с `.venv`, а любые `.cs` под ним становятся модулями продукта.
   Требование усиливается с приходом парсера Python: там входными данными станет
   сам инструмент.
2. **Отдельный `pyproject.toml` без dev-группы и отдельный `uv.lock` к нему.**
   `uv sync --no-dev` дал бы то же окружение, но лок остался бы общим, и состав
   поставки перестал бы быть виден по файлам. Лок поставки проверяется тестом
   на отсутствие `ruff`, `mypy`, `pytest`.
3. **Установщик не затирает настроенные `docpipe.yaml` и `rules.yaml`.** Настройка
   правил — недели работы; повторный запуск установщика кладёт новые версии рядом
   как `*.new` и сообщает об этом.

Дублирование манифеста зависимостей — цена за отделяемую поставку, и оно обязано
проверяться: `tests/test_deploy_bundle.py` сверяет `dependencies`, `requires-python`
и `version` с корневым `pyproject.toml`. Разъехавшаяся поставка обнаруживается иначе
только на чужой машине.

**Закрытый контур — не частный случай, а основной сценарий.** Целевая машина берёт
пакеты из внутреннего зеркала, ходит через TLS-прокси и имеет ровно один
интерпретатор. Четыре следствия, каждое из которых иначе стоит отдельного разбора
на месте:

1. **`uv.lock` бесполезен за пределами PyPI.** Он ссылается на файлы
   `files.pythonhosted.org` поимённо и с хэшами, поэтому `uv sync --frozen`
   с ним не пойдёт ни в какое зеркало. Настройка индекса **не** перенаправляет
   готовый лок — его нужно пересобрать: `uv lock` против настроенного индекса.
   Лок при этом не удаляется: uv предпочтёт уже записанные версии, если зеркало
   их отдаёт.
2. **Настройки uv живут в `uv.toml` рядом с пакетом**, а не в переменных окружения:
   uv читает его из каталога `--project` и применяет к `lock`, `sync` и `run`
   независимо от текущего каталога. В нём `[[index]] default = true` (вместо PyPI,
   а не в дополнение), `native-tls = true` (иначе `invalid peer certificate` от
   корпоративного УЦ) и `python-downloads = "never"` (иначе uv уходит за сборкой
   Python на github.com и упирается в тот же прокси).
   - ключи верхнего уровня обязаны стоять **до** первой таблицы `[[index]]`:
     TOML отнесёт их к ней, и настройка молча перестанет действовать;
   - неизвестный ключ uv.toml не игнорирует, а падает с ошибкой разбора. Поэтому
     `native-tls` не переименовывать в `system-certs` вслепую: старый uv на нём
     упадёт.
3. **Сборка самого пакета — необязательный шаг.** Если зеркало не отдаёт
   `hatchling`, окружение поднимается как `--no-install-project`. Отсюда
   требование к `run.sh`: звать `python -m docpipe` с `PYTHONPATH` на каталог
   поставки, а не консольную команду `docpipe`, — тогда оба режима работают
   одинаково.
4. **`run.sh` не ходит в сеть** (`uv run --no-sync`). Иначе каждый запуск
   сверялся бы с индексом и падал бы там, где индекс недоступен, — на команде,
   которая с зависимостями ничего не делает.

Обновление поверх установки на закрытом контуре **не должно затирать пересобранный
лок**: копирование лока из поставки отправило бы следующий `uv sync` на
недоступный pypi.org. Установщик отличает свой лок по строке
`registry = "https://pypi.org/simple"` и вместо копирования пересобирает.

`docpipe/__main__.py` — запасной вход для окружения, поднятого как
`uv sync --no-install-project`: там консольной команды нет, а `python -m docpipe`
работает. Проверяется запуском подпроцесса, а не импортом: импорт не выполняет
ветку `if __name__ == "__main__"`.

**Критерии приёмки**
- `deploy/install.sh <репозиторий>` разворачивает дерево и поднимает окружение
  из 17 пакетов; `ruff`, `mypy`, `pytest` в нём отсутствуют;
- `run.sh scan` из установленного каталога строит манифест, `run.sh validate` его
  принимает, два прогона дают побайтово одинаковый файл;
- посторонний `.cs` под `docs/` в манифест не попадает;
- повторная установка поверх правленого `docpipe.yaml` оставляет правку на месте
  и создаёт `docpipe.yaml.new`;
- ручной вызов из корня репозитория с одним только `--config` пишет манифест туда,
  куда указывает `out` в конфигурации;
- `--no-install-project` даёт рабочую установку из 15 пакетов **без** консольной
  команды `docpipe`, и `run.sh scan` в ней работает так же;
- `--index URL` записывает `uv.toml` с этим адресом и пересобирает лок; при
  недоступном адресе установщик печатает разбор трёх причин, а скопированные
  файлы остаются на месте;
- повторная установка поверх лока, пересобранного против зеркала, этот лок
  сохраняет.

**Проверка**
```bash
uv run pytest tests/test_deploy_bundle.py -q
```

---

## Что НЕ входит в этот план

Чтобы не было соблазна начать делать раньше времени:

- создание директорий и `.md`-файлов (шаг 2 пайплайна);
- шаблоны документации (Service / Provider / Workflow / Controller);
- генерация `docs/_views/**`;
- парсеры Python и TypeScript;
- любая интеграция с LLM.
