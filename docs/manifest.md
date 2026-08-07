# Манифест: что в нём и как им пользоваться

Справочник по выходу шага 1. Схема формально описана в
[`schema/doc-tree.schema.json`](../schema/doc-tree.schema.json) (генерируется из
[`docpipe/model.py`](../docpipe/model.py) командой `docpipe schema`), обоснование
решений — в [`parser-architecture.md`](parser-architecture.md). Здесь — короткое
объяснение, что это за объекты и что с ними делать дальше.

## Два файла

```
artifacts/doc-tree.json       манифест — детерминированный, вход для шагов 2 и 3
artifacts/doc-tree.run.json   сидкар — всё про конкретный прогон
```

Разделение не косметическое. В манифесте **нет ничего недетерминированного**: ни
времени, ни хоста, ни абсолютных путей. Поэтому проверка воспроизводимости — это
побайтовое сравнение файла, без логики «сравнить, игнорируя такие-то поля». Два
прогона на одном коде обязаны дать одинаковый байт в байт манифест; если нет —
это дефект.

Всё остальное живёт в сидкаре: `generated_at`, `host`, `duration_seconds`,
`docpipe_version`, счётчики `stats` и `parse_error_files` — список файлов, где
разбор дал ошибки и **ни одного** типа. Последнее — единственный внешний признак
того, что директива препроцессора внутри выражения уничтожила тип целиком.

Верхний уровень манифеста:

| Поле | Что |
|---|---|
| `schema_version` | версия формата, сейчас `"1.1"` |
| `ruleset_version` | метка набора правил — меняйте при правке `rules.yaml`, иначе `diff` покажет изменения без причины |
| `parser` | версии `tree-sitter` и грамматики: их апгрейд может законно изменить вывод |
| `partial` | заполнено только у прогона со `--scope` |
| `modules[]` | карта проектов |
| `nodes[]` | документы, которые предстоит создать |

## Модуль — это проект

Один `.csproj` = один модуль. В `modules[]` попадают **все** найденные проекты,
включая невключённые в документацию: их символы нужны графу наследования, а сам
список — справочная часть манифеста.

```json
{
  "id": "module:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
  "name": "Sample.Pricing.Api",
  "csproj": "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
  "target_frameworks": ["net8.0"],
  "project_references": ["src/Sample.Common/Sample.Common.csproj"],
  "package_references": ["Microsoft.AspNetCore.OpenApi"],
  "domain": "pricing",
  "enrolled": true
}
```

- **`id` строится от пути, а не от имени.** Имена проектов не уникальны — в ABP
  39 повторов;
- `name` — имя файла `.csproj` без расширения. Именно оно попадает в `doc_path`;
- **`project_references` — пути к `.csproj`, а не `id`.** Сравнивать их нужно
  с полем `csproj` другого модуля; сопоставление с `id` даёт ноль совпадений
  на любом репозитории и выглядит как «граф модулей развалился». Неразрешённая
  ссылка остаётся сырым текстом из `Include` (см. `csproj.resolve_references`);
- `domain` — смысловая группировка из `domains` в `docpipe.yaml`, глоб по пути
  `.csproj`. Без совпадения равен `name`. На сам docpipe не влияет: это метаданные
  для навигации на шаге 2;
- `enrolled` — входит ли в документацию (`enrolled` в `docpipe.yaml`). Узлы
  строятся только по enrolled-модулям;
- `target_frameworks` может быть пуст: значение бывает подстановкой MSBuild,
  а воспроизводить MSBuild мы не будем. Ни на что важное не влияет.

## Узел — это один тип и один будущий документ

Не файл и не проект. `partial`-класс из трёх файлов даёт **один** узел, файл
с пятью классами — до пяти.

```json
{
  "id": "type:src/…/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Controllers.PricingController`0",
  "title": "PricingController",
  "signature_hash": "sha256:0e29455f…",
  "impl_hash": "sha256:77af12c0…",

  "doc_path": "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md",
  "parent": "module:src/…/Sample.Pricing.Api.csproj",
  "module": "Sample.Pricing.Api",
  "domain": "pricing",

  "kind": "controller",
  "template": "controller",
  "matched_rules": ["controller.aspnet"],

  "symbol":       { "fqn": "…", "sources": [ … ], "members": [ … ] },
  "endpoints":    [ {"http_method": "GET", "route": "api/v1/Pricing/{id:guid}", "member": "GetAsync", "line": 18} ],
  "dependencies": [ {"target": "…IPricingService", "via": "constructor", "confidence": "high"} ],
  "related":      []
}
```

| Группа | Поля | Комментарий |
|---|---|---|
| идентичность | `id`, `title`, `signature_hash`, `impl_hash` | два хэша отвечают на разные вопросы |
| размещение | `doc_path` | готовый путь документа |
| принадлежность | `parent`, `module`, `domain` | `parent` — `id` из `modules[]` |
| как документировать | `kind`, `template`, `matched_rules` | |
| содержимое | `symbol` | всё, что извлечено из кода |
| связи | `endpoints`, `dependencies`, `related` | вход для перекрёстных ссылок |

Два поля заслуживают отдельного слова.

**`id` — это модуль + FQN + арность дженерика.** FQN сам по себе не уникален:
на ABP 255 коллизий на 9075 объявлений. Даже такой ключ оставляет остаток —
файлы, не компилируемые вместе (`Platforms/iOS/` против `Platforms/MacCatalyst/`).

**`signature_hash` намеренно не включает номера строк и пути.** Иначе
переформатирование файла заставило бы агента шага 3 перегенерировать документ
впустую, и вся экономия от инкрементальности пропала бы.

**`impl_hash` отвечает на вопрос, которого не задаёт `signature_hash`:**
«переписано тело при том же контракте». Описание «как работает» зависит именно
от тела, и без второго сигнала оно устаревает молча. Два хэша дают шагу 2 два
уровня приоритета: смена контракта — переписать, смена реализации — проверить.

Считается по текстам всех объявлений типа с нормализованными пробелами и
отсортированным списком (у `partial`-типа объявлений несколько, и порядок
обхода файлов источником порядка быть не может). Две границы, которые стоит
знать заранее:

- **правка XML-doc `impl_hash` не меняет** — комментарий является предшествующим
  соседом объявления, а не его потомком, и в `SourceSpan` не входит;
- **схлопываются последовательности пробельных символов, а не любые различия
  в расстановке пробелов.** Отступы, переносы строк и пустые строки покрыты;
  смена правил расстановки пробелов вокруг скобок (`(int x)` → `( int x )`)
  даст разовую волну «реализация изменилась» по всему дереву. Событие
  однократное и снимается приёмкой.

### Состав `symbol`

`fqn`, `name`, `type_kind` (`class` | `interface` | `struct` | `record` |
`record_struct` | `enum`), `namespace`, `module`, `modifiers`,
`type_parameters`, `attributes`, `xml_doc`, `ambiguous`, `impl_hash`, плюс три важных:

- **`sources[]`** — где лежит код. **Это список**: у `partial`-типа файлов
  несколько. Каждый элемент — `path`, `start`, `end`;
- **`base_types` / `base_types_raw` / `base_type_closure`** — прямые базовые типы,
  их сырой текст и **транзитивное замыкание**. На замыкании работает предикат
  `inherits`: правило «контроллер — то, что наследует `ControllerBase`» срабатывает
  и через ваш промежуточный базовый класс из другого проекта;
- **`members[]`** — `name`, `kind` (`method` | `property` | `field` |
  `constructor` | `event`), `signature`, `modifiers`, `attributes`, `line`,
  `end_line`, `xml_doc`.

### Связи

- `endpoints[]` — `http_method`, `route`, `member`, `line`. Маршрут уже склеен
  из атрибутов типа и члена;
- `dependencies[]` — `target` (FQN), `via` (`constructor` | `di` | `inheritance`),
  `confidence` (`high` | `medium` | `low`);
- `related[]` — `target`, `relation` (`implements` | `implemented_by` | `uses`).

## Три разных «пути»

**Документа** — готов, вычислять нечего:

```
docs/modules/{вид}s/{имя модуля}/{slug}.md          # doc_layout: kind-first, по умолчанию
docs/modules/{имя модуля}/{вид}s/{slug}.md          # doc_layout: module-first
```

При совпадении путей (одноимённые типы в одном модуле) к обоим добавляется
суффикс — 8 hex от хэша `id`. На ABP так разведены 315 узлов из 828: это норма.

Раскладка на это множество не влияет: обе — перестановка одной и той же тройки
(модуль, вид, slug), поэтому каждая однозначно определяется тройкой, и спорят
за файл одни и те же узлы. На ABP это одни и те же 19 путей на 47 узлов
в обеих. Выбор между раскладками — вопрос чтения дерева, а не безопасности.

**До кода** — `symbol.sources[]`, список, с точными строками. Пути
репо-относительные POSIX; абсолютный получается склейкой с корнем репозитория.
Именно это закрывает требование постановки «в документации приложены ссылки до
конкретной реализации в коде»: у члена есть свой `line`, у эндпоинта — тоже.

**В иерархии** — через `parent` в `modules[]`, либо через `domain` / `module` /
`kind`, если нужна группировка, отличная от зашитой в `doc_path`.

## Как это используется дальше

Шаг 1 отвечает на вопрос «**что** документировать и **где** это должно лежать».
Он не создаёт ни каталогов, ни файлов — это граница, проведённая намеренно.

- **Шаг 2** (`docpipe materialize`) читает манифест и создаёт по `doc_path`
  документы со служебными полями, генерируемым блоком и пустыми секциями
  под авторский текст. Индексов и карт «для людей» не рендерит: документация
  просто лежит в репозитории набором `.md`. Подробно — в
  [`materialize.md`](materialize.md);
- **Шаг 3** идёт по узлам: берёт `template` по `kind`, читает код по
  `symbol.sources[]`, наполняет документ. `dependencies` и `related` дают
  перекрёстные ссылки, `xml_doc` — то, с чего начать;
- **инкрементальность**: `docpipe diff old.json new.json` показывает, какие узлы
  изменились. Перегенерировать нужно только их — на это и работает
  `signature_hash`, нечувствительный к форматированию.

## Полезные команды

```bash
# всё про один узел
jq '.nodes[] | select(.title=="AutoMLProvider")
   | {doc_path, kind, matched_rules,
      code: [.symbol.sources[] | "\(.path):\(.start)-\(.end)"],
      members: [.symbol.members[] | "\(.kind) \(.name)"],
      routes: [.endpoints[] | "\(.http_method) \(.route)"]}' artifacts/doc-tree.json

# дерево каталогов документации
jq -r '.nodes[].doc_path | split("/")[:-1] | join("/")' artifacts/doc-tree.json | sort -u

# коллизий путей быть не должно
jq -r '.nodes[].doc_path' artifacts/doc-tree.json | sort | uniq -d

# состав по видам и доменам
jq -r '.nodes[] | "\(.domain)\t\(.kind)"' artifacts/doc-tree.json | sort | uniq -c | sort -rn

# зависимости, у которых нет своего узла: граница enrolled прошла по живому
jq -r '[.nodes[].dependencies[].target] - [.nodes[].symbol.fqn] | unique | length' artifacts/doc-tree.json

# файлы, где разбор потерял типы
jq '.parse_error_files' artifacts/doc-tree.run.json
```
