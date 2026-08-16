# Шаг 2: документы, зоны и приёмка

Справочник по выходу шага 2. Обоснование решений — в
[`materialize-implementation-plan.md`](materialize-implementation-plan.md); здесь только
то, что лежит в файле и какими командами с этим работать.

## Что делает шаг

По манифесту создаёт на каждый `doc_path` markdown-документ и отвечает на вопрос
«что делать с этим документом» — **писать**, **проверить** или **не трогать**.

```
исходники .NET ──▶ шаг 1: docpipe scan        ──▶ doc-tree.json
               ──▶ шаг 2: docpipe materialize ──▶ docs/**/*.md
               ──▶ шаг 3: агент               ──▶ наполненные документы
```

**Чего не делает:** не рендерит ничего, не строит индексы и карты «для людей»,
не вызывает LLM, не пишет текст документации и **никогда ничего не удаляет**.

## Устройство документа

```markdown
---
docpipe:                       ← проекция манифеста, перезаписывается всегда
  schema: materialize/1
  node_id: type:src/…#…PricingController`0
  doc_path: docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md
  title: PricingController
  fqn: Sample.Pricing.Api.Controllers.PricingController
  kind: controller
  template: controller
  template_ref: templates/controller.md
  example_ref: templates/examples/controller.md
  module: Sample.Pricing.Api
  module_csproj: src/Sample.Pricing.Api/Sample.Pricing.Api.csproj
  domain: Sample.Pricing.Api
  team: null
  signature_hash: sha256:0e29455f…
  impl_hash: sha256:74ba5471…
  ruleset_version: 2026-07-30.1
  sources:
  - path: src/Sample.Pricing.Api/Controllers/PricingController.cs
    start: 8
    end: 26
docpipe_state:                 ← состояние, пишет только `docs accept`
  accepted: null
  review: null
---

# PricingController

<!-- docpipe:generated:start -->   ← пересобирается всегда
…шапка, исходники, эндпоинты, зависимости, связи, XML-doc…
<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->   ← авторский текст, НИКОГДА не затирается
<!-- docpipe:section:end purpose -->
```

### Зоны и правило записи

| Зона | Кто пишет | Что происходит при `materialize` |
|---|---|---|
| ключ `docpipe:` | инструмент | перезаписывается всегда |
| ключ `docpipe_state:` | только `docs accept` | сохраняется |
| прочие ключи front matter | человек | сохраняются, порядок лексикографический |
| `<!-- docpipe:generated:… -->` | инструмент | пересобирается всегда |
| `<!-- docpipe:section:… -->` | агент шага 3 | **никогда** не затирается |
| всё остальное в теле | человек | сохраняется дословно |

Секция считается пустой, если после удаления HTML-комментариев в ней ничего
не осталось. Поэтому подсказки в шаблонах — **только** комментарии: обычный текст
сделал бы каждый новый документ «уже написанным».

Секции, которых нет в текущем шаблоне, не удаляются и не переставляются — про них
появляется строка в генерируемом блоке, и она исчезает сама, как только секцию уберут.

### Слои документации различаются по `schema`

`materialize/1` — документ шага 2. `business/1` — документ бизнес-каталога.
Формат зон у них общий, ключ `docpipe:` тоже, поэтому слои отбирают свои документы
именно по схеме. Файл без `docpipe.schema` не является документом ни одного слоя
и не станет сиротой.

## Статусы и решения

**Статус** описывает содержимое документа, **решение** — что делать агенту.
Вычисляется по порядку, первое сработавшее выигрывает.

| Условие | Статус | Решение |
|---|---|---|
| файла нет **и обход это подтвердил** | `missing` | `write` |
| есть пустые секции | `empty` | `write` |
| `signature_hash` ≠ принятого | `stale` | `write` |
| приёмки не было, а текст есть | `undeclared` | `review` |
| документ перенесён | `relocated` | `review` |
| принятый `kind` ≠ текущего | `drifted` | `review` |
| `impl_hash` ≠ принятого | `drifted` | `review` |
| иначе | `current` | `skip` |

Ещё два статуса не про содержимое: `broken` — файл трогать нельзя;
`orphan` — узла с таким `node_id` в манифесте нет.

`broken` бывает по трём причинам, и во всех трёх файл остаётся как есть:
структуру документа прочитать не удалось; файл не читается (права); **файл
на `doc_path` есть, а обход документов его не вернул**. Последнее — не
`missing`: `missing` означает `create`, то есть перезапись. Способов стать
невидимым пять (front matter без `docpipe.schema`, `---` не первой строкой,
`docs_scan_exclude`, каталог за симлинком, права), и `docs explain`
называет, какой сработал. Такой документ не перезаписывается и под `--force`:
что в нём лежит, инструменту неизвестно.

Два хэша дают два уровня приоритета: **смена контракта — переписать**, **смена
реализации при том же контракте — проверить**.

## Статус и действие с файлом — разные вещи

`status` описывает содержимое документа, `file_action` — что прогон сделает
с файлом, и совпадают они далеко не всегда:

| Условие | `file_action` |
|---|---|
| обход не нашёл файла на `doc_path` | `create` |
| узел сопоставлен с файлом на другом пути | `relocate` |
| собранный текст совпадает с файлом байт в байт | `unchanged` |
| файл трогать нельзя (`broken`) | `refuse` |
| иначе — файл есть, текст отличается | `update` |

Самый частый источник путаницы: документ со статусом `current` и решением
`skip` **переписывается**, если сменился владелец или домен. Это поля проекции,
они живут во front matter, и файл открывается на запись при том, что писать
агенту нечего. Поэтому `current` остаётся в подробном списке `docs status`,
когда его файл переписывается, и исчезает из него, когда нет.

Действие видно колонкой в `docs status`, полем `file_action` в его JSON
и в очереди шага 3, и отбирается флагом `--file-action`. Не путать
с `--action`: тот про работу агента, этот про запись.

## Почему с документом сделают именно это (`docs explain`)

```bash
docpipe docs explain artifacts/doc-tree.json docs/modules/…/pricing-controller.md
```

```
файл на диске:      есть
обход документов:   файл принят
действие с файлом:  update — файл есть, и собранный текст от него отличается
статус документа:   current
решение агенту:     skip

Что изменится:
  front matter:
    docpipe.domain: pricing → risk
  генерируемый блок: пересобран
  авторские секции:  не тронуты
```

Три вопроса, на которые отчёт по дереву не отвечает, а эта команда отвечает:
какой из пяти фильтров обхода отбросил лежащий на диске файл; чем именно
собранный текст отличается от него; не задевает ли перезапись авторские секции.

Последнее — не украшение. Изменение авторской секции означает нарушение
главного инварианта шага 2, поэтому оно печатается отдельной строкой
и **меняет код возврата**: такое ловят проверкой, а не глазами в выводе.
`--diff` добавляет обычный unified diff, когда разбивки по зонам мало.

## Команды

```bash
docpipe materialize MANIFEST --root PATH [--config FILE] [--templates DIR]
                             [--ownership FILE] [--team NAME] [--dry-run] [--force]

docpipe docs status MANIFEST [PATH...] [--action write|review|skip]
                             [--file-action create|update|unchanged|relocate|refuse]
                             [--fail-on STATUS] [--team NAME] [--format text|json]

docpipe docs explain MANIFEST PATH [--root PATH] [--config FILE] [--diff]

docpipe docs accept MANIFEST [PATH...] [--node ID] [--team NAME] [--all]
                             [--force] [--dry-run]

docpipe docs adopt  MANIFEST --from PATH --to PATH [--dry-run]

docpipe docs owners MANIFEST [--explain NODE] [--lint]

docpipe worklist MANIFEST [--root PATH] [--config FILE] [--templates DIR]
                          [--ownership FILE] [--team NAME] [--out FILE]
                          [--action write|review|skip] [--limit N]
```

Коды возврата: **0** — успех, **1** — проверка не прошла или отказ, **2** — ошибка
пользователя.

`--team` сужает множество того, что **пишется**. Множество, с которым сравнивают,
не сужается никогда — иначе прогон одной команды объявил бы сиротами документацию
остальных.

### Отчёт `materialize`

```
Что было бы сделано:          # «Сделано:» без --dry-run
  создано:      6
  обновлено:    0
  перенесено:   0
  без изменений:  0

Где было бы создано:          # раскладка по каталогам, только для созданных
  docs/modules/services/Sample.Pricing.Api        2
  docs/modules/controllers/Sample.Common          1

Своего скелета нет, применён `default`:   # только если подстановка была
  ignite-cache    412

Состояние документов:         # статусы из плана
  missing          6
```

Раскладка по каталогам отвечает на первый вопрос после `--dry-run`: цифра «создано»
говорит сколько, но не где. Порядок — по убыванию количества, при равенстве по имени
каталога. Показываются двадцать самых крупных, остаток сворачивается в строку
«и ещё каталогов: N, документов в них: M»: полный список на большом репозитории
утопил бы отчёт, а оценку объёма даёт и свёрнутый.

## Цикл работы

```bash
# 1. Создать или обновить скелеты
docpipe materialize artifacts/doc-tree.json --root .

# 2. Спросить, что делать. Человеку и CI — docs status,
#    внешнему исполнителю шага 3 — файл очереди
docpipe docs status artifacts/doc-tree.json --root .
docpipe worklist    artifacts/doc-tree.json --root .

# 3. Агент наполняет секции, front matter не трогает

# 4. Зафиксировать соответствие коду
docpipe docs accept artifacts/doc-tree.json docs/modules/services/X/y.md --root .

# 5. После изменения кода — снова scan, затем status:
#    сменился контракт  -> stale,   write, с перечнем добавленных и удалённых членов
#    сменилась реализация -> drifted, review
```

В CI полезен `--fail-on`:

```bash
docpipe docs status artifacts/doc-tree.json --root . --fail-on broken --fail-on stale
```

Значение вне перечня статусов — ошибка (код 2), а не пустой фильтр: опечатка
`--fail-on statle` иначе дала бы вечно зелёную проверку.

## Очередь для внешнего исполнителя (`docpipe worklist`)

`docs status` отвечает человеку и CI, `worklist` — **чужому процессу**: он кладёт
на диск один файл, который читает другой инструмент, и в дерево документации
не пишет ничего.

```bash
docpipe worklist artifacts/doc-tree.json --root .          # путь из docpipe.yaml
docpipe worklist artifacts/doc-tree.json --out /tmp/q.json --limit 50
```

```json
{
  "schema_version": "1.1",
  "docs_root": "docs",
  "modules_root": "docs/modules",
  "ruleset_version": "dotnet/1.0",
  "manifest_sha256": "sha256:9f2c…",
  "manifest_partial": false,
  "needs_materialize": false,
  "counts": {"stale": 3, "empty": 5, "current": 1836},
  "totals": {"documents": 1844, "selected": 8, "truncated": false},
  "documents": [
    {"action": "write", "file_action": "update", "status": "stale",
     "reason": "контракт изменился: добавлены …",
     "doc_path": "docs/modules/controllers/…/pricing-controller.md",
     "node_id": "type:src/…#…PricingController`0", "kind": "controller",
     "template_ref": "templates/controller.md", "example_ref": null, "team": null,
     "sources": [{"path": "src/…/PricingController.cs", "start": 8, "end": 26}],
     "empty_sections": [], "orphan_sections": [], "broken_links": [],
     "changes": {"members_added": ["RecalculateAsync"], "members_removed": [],
                 "kind_changed": null, "relocated_from": null}}
  ]
}
```

| Поле | Что означает |
|---|---|
| `schema_version` | версия **формата очереди**; своя, не связана со `schema_version` манифеста. `1.1` — добавлено `file_action` |
| `file_action` | что `materialize` сделает с файлом; по нему исполнитель видит, отработал ли он уже |
| `manifest_sha256` | по нему внешний модуль проверяет, что читает очередь от того самого `doc-tree.json` |
| `needs_materialize` | в очереди есть документы, которых на диске ещё нет: сначала `materialize` |
| `counts` | статусы по **всему** дереву; фильтры на них не влияют |
| `totals` | `documents` — сколько в дереве, `selected` — сколько в очереди, `truncated` — обрезал ли `--limit` |

Записи документов совпадают с записями `docs status --format json` поле в поле:
их собирает одна и та же функция.

Правила, которые легко нарушить, если читать файл невнимательно:

- **времени в файле нет.** С ним он менялся бы на каждом прогоне; свежесть даёт
  `manifest_sha256`, а время файла — файловая система;
- **пустая очередь — это записанный файл с нулём записей.** Отсутствие файла
  означает «прогон не состоялся», и путать эти два состояния нельзя;
- **при блокирующих ошибках плана файл не переписывается** (код 1, причины
  в stderr): прежняя очередь достовернее полупустой новой;
- **порядок записей — по приоритету статуса**, затем по `doc_path`. Он отличается
  от `docs status`, где сортировка только по пути: иначе `--limit` резал бы
  очередь по алфавиту;
- без `--action` в очередь попадают `write` и `review`; `skip` — никогда.

Схема файла — производная от моделей:

```bash
docpipe schema --model worklist --out schema/doc-worklist.schema.json
```

## Где лежит дерево документации

Префикс `doc_path` собирается из двух ключей `docpipe.yaml`:

| Ключ | По умолчанию | Что задаёт |
|---|---|---|
| `docs_root` | `docs` | корень дерева документации относительно `--root` |
| `modules_dir` | `modules` | каталог технической документации **внутри** `docs_root` |

```
docs_root: "documentation" + modules_dir: "tech" -> documentation/tech/controllers/…
docs_root: "docs"          + modules_dir: ""     -> docs/controllers/…
```

Пара, а не один путь целиком, — намеренно: так инвариант «то, что пишет
`materialize`, лежит там, где ищет `docs status`» держится структурно. Одним
значением его можно было бы нарушить, и документы навсегда остались бы `missing`,
переписываясь заново на каждом прогоне.

Оба значения обязаны быть репо-относительными и POSIX: абсолютный путь, `..`
или `\` — ошибка загрузки конфигурации, а не молчаливо непереносимый манифест.

**Смена любого из них меняет `doc_path` у всех узлов сразу** — ровно как смена
`doc_layout`. Документы при этом не теряются: `materialize` находит их по
`node_id` и переносит, но каждый принятый встанет в `relocated`.

## Владение

**Владение необязательно, и начинать с него не нужно.** Оно влияет ровно
на четыре вещи: поле `team` во front matter, строку «владелец» в генерируемом
блоке, колонку в `docs status` и то, что отбирает флаг `--team`. На состав
документов, их пути и статусы оно не влияет никак, поэтому заполнить его можно
когда угодно позже: при этом перепишется одна строка front matter и одна строка
генерируемого блока, а статусы и авторский текст останутся прежними.

Файла `ownership.yaml` в репозитории **нет** — его заводят копией примера:

```bash
cp ownership.example.yaml ownership.yaml
docpipe docs owners MANIFEST --ownership ownership.yaml --lint
```

В поставке для АС CF он уже лежит заготовкой, и путь к нему прописан
в `docpipe.yaml`, — но подхватывается только при `--config`:

```bash
docpipe docs owners MANIFEST --config docs/ml/docspipe/cashflow-docspipe/docpipe.yaml --lint
```

Правила раздают команду по **узлу**, а не по модулю: шэренный `.csproj` иначе
не разделить. Синтаксис условий тот же, что у правил классификации. Пример
со всеми слоями приоритета и пояснениями —
[`ownership.example.yaml`](../ownership.example.yaml).

### С чего начинать настройку

```bash
# 1. Посмотреть, по каким полям вообще можно писать условия для конкретного узла
docpipe docs owners MANIFEST --ownership ownership.yaml --explain docs/modules/services/X/y.md
```

```
PricingController  (controller)
  модуль:    Sample.Pricing.Api  (src/Sample.Pricing.Api/Sample.Pricing.Api.csproj)
  домен:     Sample.Pricing.Api
  namespace: Sample.Pricing.Api.Controllers
  источники: src/Sample.Pricing.Api/Controllers/PricingController.cs
  совпавшие правила: нет
  владелец:  не задан
```

```bash
# 2. Завести один слой приоритета 10 по module_glob и посмотреть, что осталось
docpipe docs owners MANIFEST --ownership ownership.yaml --lint
```

`--lint` находит мёртвые правила, узлы без владельца — со срезами по модулям
и каталогам, то есть с ответом «куда писать следующее правило», — команды
без узлов и ничьи по приоритету.

## Автоперенос

Переезд — это когда изменился **код** и пересчитался `doc_path`, а файл с текстом
остался на старом месте.

| Уверенность | Признак | Действие |
|---|---|---|
| `exact` | `node_id` совпал | перенос |
| `high` | пересеклись источники или совпал `impl_hash`, пара единственная в обе стороны | перенос |
| `probable` | кандидатов несколько | сообщение, перенос командой `docs adopt` |
| нет | признаков нет | остаётся `orphan` |

Ссылки на переехавший документ **из других документов** чинятся сами: генерируемые
блоки пересобираются в том же прогоне. Ссылки внутри авторских секций не чинятся —
`docs status` сообщает о битых в `broken_links`.

### Смена `doc_layout`

Единственный случай, когда переезжают сразу все документы. Механизм тот же:
`node_id` есть в front matter каждого документа, поэтому сопоставление точное —
ни один документ не пересоздаётся и ни один не теряется.

```bash
# 1. Посмотреть, что поедет, ничего не трогая
docpipe materialize artifacts/doc-tree.json --root . --dry-run

# 2. Перенести
docpipe materialize artifacts/doc-tree.json --root .

# 3. Снять пометку о пересмотре — только с переехавших, списком
docpipe docs status artifacts/doc-tree.json --root . --format json \
  | jq -r '.documents[] | select(.status == "relocated") | .doc_path' > /tmp/relocated.txt
docpipe docs accept artifacts/doc-tree.json $(cat /tmp/relocated.txt) --root .

# 4. Прибрать опустевшие каталоги: перенос их не удаляет
find docs -type d -empty -delete
```

Шаг 3 нужен потому, что переезд ставит принятому документу статус `relocated`
и просит пересмотра. Для переезда из-за смены **кода** это правильно, для смены
раскладки — шум: текст не менялся. Отдельного режима «переезд без пересмотра»
нет намеренно: он был бы вторым путём записи со своей логикой слияния зон.
Поэтому приёмка идёт обычной командой, и её видно в истории.

Списком, а не `--all`: приёмка отклоняет ненаполненные документы целиком,
поэтому на дереве, где хоть один документ ещё не написан, `--all` не примет
**ничего** — включая те, что действительно только переехали.

## Шаблоны

Семь скелетов в `templates/`, по одному на значение `template` из секции `dotnet`;
их имена сверяются с набором правил тестом. Четыре заполненных образца
в `templates/examples/` показывают агенту глубину и стиль. Подробности и правила
правки — в [`templates/README.md`](../../templates/README.md).

Восьмой скелет — `default.md`. Он применяется к узлу, для которого скелета под его
`template` нет, и в наборе правил не объявляется. Подстановка видима: прогон печатает
раздел «Своего скелета нет, применён `default`» с перечнем видов и числом узлов.
Без `default.md` неизвестный `template` остаётся блокирующей ошибкой — не записывается
ничего. `template_ref` такого документа указывает на `templates/default.md`, то есть
на применённый скелет, а `example_ref` пуст.

Каталог задаётся ключом `templates` в `docpipe.yaml` или флагом `--templates`;
по умолчанию — `templates`. **Путь отсчитывается от текущего каталога**, как `out`
и `rules`, а не от `--root` и не от каталога с `docpipe.yaml`. В репозитории
разработки это незаметно — команды и так зовутся из корня, — а в установке, где
инструмент лежит не в корне сканируемого репозитория, значение по умолчанию
указывает в никуда, и `materialize` отказывается работать: «Каталог шаблонов
не найден». Ошибка про путь, причина в конфигурации.

Обход каталога **не рекурсивный**: `examples/` — заполненные образцы, скелетами
они не являются. `README.md` пропускается по имени.
