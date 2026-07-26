# documentation_pipeline

Пайплайн построения, обновления и верификации технической документации проекта **АС CF**
силами AI-агентов.

Сам проект АС CF — собственность компании и в этот репозиторий не переносится. Здесь живут
инструменты пайплайна, его проектная документация и тестовые данные.

## Задача

АС CF — большой проект финансового моделирования на .NET, Python и Angular, с распределённым
кластером вычислений на Ignite (GridGain). Требуется документация, которая:

- генерируется из кода, а не пишется вручную;
- имеет понятную структуру;
- **описывает**, а не пересказывает код;
- содержит ссылки на конкретные места реализации.

Полная постановка — в [`purpose.md`](purpose.md).

## Пайплайн

```
                     ┌─────────────────────────────────────┐
   исходники .NET ──▶│  Шаг 1: docpipe scan (детермин.)    │──▶ doc-tree.json
                     └─────────────────────────────────────┘         │
                                                                     ▼
                     ┌─────────────────────────────────────┐
                     │  Шаг 2: materialize (идемпотентно)  │──▶ docs/**/*.md (скелеты)
                     └─────────────────────────────────────┘         │
                                                                     ▼
                     ┌─────────────────────────────────────┐
                     │  Шаг 3: агент + tools (недетермин.) │──▶ наполненные документы
                     └─────────────────────────────────────┘
```

Граница между детерминированным и недетерминированным проходит по одной линии:
**классификация — правила, описание — агент.** Решение «этот класс — контроллер» принимает
правило из YAML. Решение «что этот контроллер делает» принимает агент.

## Состояние

Реализуется **шаг 1** — `docpipe`, детерминированный построитель структуры документации.
Скоуп текущей версии: только .NET (C#).

Готово 14 задач из 24 (см. [журнал реализации](docs/implementation-log.md)):

| | |
|---|---|
| ✅ T00–T03 | каркас, тестовые решения, примитивы детерминизма, модели данных |
| ✅ T04–T05 | обход ФС, разбор `.csproj` / `.sln` / `.slnx` |
| ✅ T06–T08 | разбор C#: объявления типов, их члены, директивы `using` |
| ✅ T09 | кэш разобранных файлов: на ABP тёплый прогон в 10 раз быстрее холодного |
| ✅ T10–T11 | индекс символов: слияние `partial`, резолв баз, замыкание наследования |
| ✅ T12–T13 | HTTP-маршруты контроллеров, регистрации в DI-контейнере |
| ✅ T14–T17 | классификация, сборка дерева, команда `scan`, тесты детерминизма |
| ⬜ T18–T21 | скоуп-режим, diff, stats, финализация |
| ⬜ T05b | связанные исходники `<Compile Include>` — по итогам стресс-теста |

382 теста, 3087 строк кода.

## Как этим пользоваться

### Установка

```bash
uv sync
uv run docpipe --help
```

.NET SDK не требуется: C# разбирается через `tree-sitter`, без сборки проекта.
Достаточно, чтобы исходники лежали на диске.

### Первый прогон

```bash
uv run docpipe scan --root /путь/к/репозиторию --out artifacts/doc-tree.json
```

Выводит что-то вроде:

```
Модулей: 671, узлов: 828. Записано: artifacts/doc-tree.json и artifacts/doc-tree.run.json
Внимание: 1 файлов разобраны с ошибками и не дали ни одного типа — см. parse_error_files в сидкаре.
```

Попробовать можно прямо на тестовом решении из этого репозитория:

```bash
uv run docpipe scan --root tests/fixtures/SampleSolution --out /tmp/dt.json
cat /tmp/dt.run.json          # статистика прогона
jq '.nodes[].doc_path' /tmp/dt.json
```

### Что получается на выходе

Два файла, и разделение между ними принципиально.

**`doc-tree.json` — манифест.** Не содержит ни одного недетерминированного поля:
ни времени, ни имени машины, ни абсолютных путей. Два прогона дают побайтово
одинаковый файл, поэтому его можно коммитить и смотреть в диффе.

```jsonc
{
  "schema_version": "1.0",
  "ruleset_version": "2026-07-26.1",
  "parser": { "tree_sitter": "0.26.0", "grammar_c_sharp": "0.23.5" },
  "modules": [
    { "id": "module:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
      "name": "Sample.Pricing.Api", "domain": "Sample.Pricing.Api", "enrolled": true,
      "target_frameworks": ["net8.0", "net9.0"],
      "project_references": ["src/Sample.Common/Sample.Common.csproj"] }
  ],
  "nodes": [
    { "id": "type:src/…/Sample.Pricing.Api.csproj#…PricingController`0",
      "kind": "controller",
      "doc_path": "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md",
      "matched_rules": ["controller.aspnet"],       // все совпавшие правила, для аудита
      "endpoints": [{ "http_method": "GET", "route": "api/v1/Pricing/{id:guid}" }],
      "dependencies": [{ "target": "…IPricingService", "via": "constructor",
                         "confidence": "high" }],
      "signature_hash": "sha256:…",                 // без номеров строк и путей
      "symbol": { "…": "полное описание типа: члены, базы, атрибуты, XML-doc" } }
  ]
}
```

**`doc-tree.run.json` — сидкар.** Всё недетерминированное плюс статистика:

```json
{ "generated_at": "…", "host": "…", "duration_seconds": 9.7,
  "stats": { "symbols": 8966, "classified": 828, "unclassified": 7142, "excluded": 996 },
  "parse_error_files": ["modules/…/CmsKitWebUnifiedModule.cs"] }
```

`parse_error_files` — файлы, где разбор дал ошибки и **ни одного** типа. Обычно это
директива препроцессора внутри выражения; больше этот случай ничем не виден.

### Флаги `scan`

| Флаг | Зачем |
|---|---|
| `--root PATH` | корень репозитория с исходниками (обязательный) |
| `--out FILE` | куда писать манифест, по умолчанию `artifacts/doc-tree.json` |
| `--config FILE` | `docpipe.yaml`: что документировать и как группировать |
| `--rules FILE` | свой набор правил вместо `rules/dotnet.yaml` |
| `--jobs N` | разбор в N процессов. На ABP: 9,6 с → 4,9 с при `--jobs 4` |
| `--no-cache` | не использовать кэш разобранных файлов |

Кэш включён по умолчанию и создаётся **внутри сканируемого репозитория**:
`<root>/.docpipe/cache/parse.sqlite`. Каталог стоит добавить в его `.gitignore`
(в этом репозитории он уже там). Если трогать чужое дерево нежелательно —
`--no-cache` или свой `cache_dir` в конфигурации.

Кэш не меняет результат, только время: на ABP тёплый прогон 2,8 с против 10,2 с
холодного. Попадание определяется хэшем содержимого, а не временем модификации,
поэтому checkout другой ветки его не сбивает.

### Настройка под свой репозиторий

**`docpipe.yaml`** — что документировать:

```yaml
roots: ["."]
enrolled:                       # какие проекты попадают в документацию
  - "src/**"                    # тесты парсятся ради графа наследования,
                                # но документов не порождают
domains:                        # группировка модулей по смыслу
  "src/Cf.Pricing.*/**": "pricing"
  "src/Cf.Risk.*/**": "risk"
out: "artifacts/doc-tree.json"
cache_dir: ".docpipe/cache"
```

**`rules/dotnet.yaml`** — что считать сущностью. Правила — данные, а не код:

```yaml
- id: controller.aspnet
  kind: controller
  template: controller
  priority: 100
  when:
    any:
      - attribute: ["ApiController"]
      - inherits: ["ControllerBase"]     # работает и через свой базовый класс
                                         # в другом модуле
```

Доступные предикаты: `attribute`, `base_type`, `inherits`, `name_regex`, `name_suffix`,
`namespace_regex`, `path_glob`, `type_kind`, `modifier`, `has_member_with_attribute`.
Они комбинируются через `any` / `all` любой вложенности.

Набор по умолчанию намеренно консервативен — на чужом коде он покрывает 1–9 % типов.
Настраивать его нужно по статистике: `unclassified` в сидкаре показывает, каких правил
не хватает. Ошибка в имени предиката роняет загрузку сразу, а не даёт молча
не срабатывающее правило.

### Проверка

```bash
uv run pytest -q                                    # 382 теста
uv run docpipe schema --out schema/doc-tree.schema.json   # JSON Schema из моделей
```

## Как это работает

Парсинг выполняется грамматикой `tree-sitter-c-sharp` вместо Roslyn. Проверено на четырёх
реальных репозиториях — eShopOnWeb, ABP, OpenTelemetry и semantic-kernel: **11 850 файлов,
941 проект, ноль падений**. ABP целиком (671 проект, 3,9 ГБ) разбирается за 8 секунд;
Roslyn потребовал бы .NET SDK и `restore` всех проектов, то есть минуты вместо секунд.

Семантики tree-sitter не даёт, но для построения **структуры** она почти не нужна:
классификация идёт по атрибутам, именам базовых типов и конвенциям имён — это синтаксис.
Недостающее (цепочки наследования внутри решения) достраивается по индексу символов.

Артефакт шага — манифест `doc-tree.json`, не содержащий **ни одного** недетерминированного
поля. Метаданные прогона вынесены в сидкар `doc-tree.run.json`, поэтому проверка
воспроизводимости сводится к побайтовому сравнению файлов.

## Структура репозитория

```
docpipe/                  пакет шага 1
├── cli.py                команды: version, schema, scan (дальше — diff, stats, validate)
├── emit.py               сквозной прогон и запись манифеста с сидкаром
├── cache.py              sqlite + zlib: разбор переиспользуется по хэшу содержимого
├── classify.py           движок правил: Symbol -> вид сущности и шаблон
├── tree.py               символы + правила -> узлы документации
├── config.py             docpipe.yaml: roots, enrolled, domains
├── discovery.py          обход ФС с ignore-правилами и scope
├── hashing.py            content_hash, stable_json_dumps, slugify
├── model.py              16 pydantic-моделей трёх уровней
└── dotnet/
    ├── csproj.py         граф модулей: TFM, ProjectReference, PackageReference
    ├── sln.py            разбор файла решения
    ├── parser.py         C# -> FileParseResult: типы, члены, usings
    ├── di.py             регистрации services.Add* -> сервис, реализация, lifetime
    ├── endpoints.py      HTTP-маршруты контроллеров из атрибутов
    ├── resolve.py        файлы -> индекс символов: FQN, partial, наследование
    └── queries/*.scm     declarations.scm, members.scm, usings.scm, di.scm

rules/dotnet.yaml         правила классификации — данные, а не код
docs/                     проектная документация (см. ниже)
schema/                   JSON Schema манифеста, генерируется из моделей
tests/fixtures/
├── SampleSolution/       канонические случаи, выверенные количества
└── WildSolution/         конструкции, пойманные в реальных репозиториях
```

## Документация

| Документ | О чём |
|---|---|
| [`purpose.md`](purpose.md) | постановка задачи |
| [`docs/parser-architecture.md`](docs/parser-architecture.md) | архитектура шага 1: решения и их обоснование |
| [`docs/parser-implementation-plan.md`](docs/parser-implementation-plan.md) | исполнительный план на 24 задачи с критериями приёмки |
| [`docs/implementation-log.md`](docs/implementation-log.md) | журнал: что сделано, что проверено, где план разошёлся с реальностью |
| [`docs/findings-eshoponweb.md`](docs/findings-eshoponweb.md) | отчёт о прогоне на eShopOnWeb (244 файла) |
| [`docs/findings-abp.md`](docs/findings-abp.md) | отчёт о прогоне на ABP (671 проект, 7869 файлов) |
| [`docs/findings-stress.md`](docs/findings-stress.md) | стресс-тест на четырёх репозиториях: OpenTelemetry, semantic-kernel |

## Разработка

Полная проверка перед коммитом:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy docpipe && uv run pytest -q
```

Конвенции и подводные камни для тех, кто продолжает работу, — в [`CLAUDE.md`](CLAUDE.md).

## Проверочные репозитории

`examples/` содержит склонированные .NET-проекты для проверки на реальном коде
(`eshoponweb`, `abp`, `opentelemetry-dotnet`, `semantic-kernel`). Каталог в репозиторий **не входит** — тесты обязаны быть
самодостаточными и опираться только на фикстуры.
