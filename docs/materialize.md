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
  doc_path: docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md
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
| файла нет | `missing` | `write` |
| есть пустые секции | `empty` | `write` |
| `signature_hash` ≠ принятого | `stale` | `write` |
| приёмки не было, а текст есть | `undeclared` | `review` |
| документ перенесён | `relocated` | `review` |
| принятый `kind` ≠ текущего | `drifted` | `review` |
| `impl_hash` ≠ принятого | `drifted` | `review` |
| иначе | `current` | `skip` |

Ещё два статуса не про содержимое: `broken` — структуру документа прочитать
не удалось, файл не тронут; `orphan` — узла с таким `node_id` в манифесте нет.

Два хэша дают два уровня приоритета: **смена контракта — переписать**, **смена
реализации при том же контракте — проверить**.

## Команды

```bash
docpipe materialize MANIFEST --root PATH [--config FILE] [--templates DIR]
                             [--ownership FILE] [--team NAME] [--dry-run] [--force]

docpipe docs status MANIFEST [PATH...] [--action write|review|skip]
                             [--fail-on STATUS] [--team NAME] [--format text|json]

docpipe docs accept MANIFEST [PATH...] [--node ID] [--team NAME] [--all]
                             [--force] [--dry-run]

docpipe docs adopt  MANIFEST --from PATH --to PATH [--dry-run]

docpipe docs owners MANIFEST [--explain NODE] [--lint]
```

Коды возврата: **0** — успех, **1** — проверка не прошла или отказ, **2** — ошибка
пользователя.

`--team` сужает множество того, что **пишется**. Множество, с которым сравнивают,
не сужается никогда — иначе прогон одной команды объявил бы сиротами документацию
остальных.

## Цикл работы

```bash
# 1. Создать или обновить скелеты
docpipe materialize artifacts/doc-tree.json --root .

# 2. Спросить, что делать. Это вход агента шага 3
docpipe docs status artifacts/doc-tree.json --root . --format json

# 3. Агент наполняет секции, front matter не трогает

# 4. Зафиксировать соответствие коду
docpipe docs accept artifacts/doc-tree.json docs/modules/X/services/y.md --root .

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

## Владение

`ownership.yaml` раздаёт документам команду-владельца правилами по узлу, а не
по модулю: шэренный `.csproj` иначе не разделить. Синтаксис условий тот же, что
у `rules/dotnet.yaml`. Пример с пояснениями — [`ownership.example.yaml`](../ownership.example.yaml).

```bash
docpipe docs owners MANIFEST --ownership ownership.yaml --lint
```

`--lint` находит мёртвые правила, узлы без владельца (со срезами по модулям
и каталогам), команды без узлов и ничьи по приоритету.

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

## Шаблоны

Семь скелетов в `templates/`, по одному на значение `template` из `rules/dotnet.yaml`;
их имена сверяются с набором правил тестом. Четыре заполненных образца
в `templates/examples/` показывают агенту глубину и стиль. Подробности и правила
правки — в [`templates/README.md`](../../templates/README.md).

Каталог задаётся ключом `templates` в `docpipe.yaml` или флагом `--templates`;
по умолчанию — `templates`. **Путь отсчитывается от текущего каталога**, как `out`
и `rules`, а не от `--root` и не от каталога с `docpipe.yaml`. В репозитории
разработки это незаметно — команды и так зовутся из корня, — а в установке, где
инструмент лежит не в корне сканируемого репозитория, значение по умолчанию
указывает в никуда, и `materialize` отказывается работать: «Каталог шаблонов
не найден». Ошибка про путь, причина в конфигурации.

Обход каталога **не рекурсивный**: `examples/` — заполненные образцы, скелетами
они не являются. `README.md` пропускается по имени.
