---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Providers.CurveProvider`0
  doc_path: docs/modules/providers/Sample.Pricing.Api/curve-provider.md
  title: CurveProvider
  fqn: Sample.Pricing.Api.Providers.CurveProvider
  kind: provider
  template: provider
  template_ref: templates/provider.md
  example_ref: templates/examples/provider.md
  module: Sample.Pricing.Api
  module_project_file: src/Sample.Pricing.Api/Sample.Pricing.Api.csproj
  domain: Sample.Pricing.Api
  team: null
  signature_hash: sha256:4417c7fa09b3233aba42f96ec0c01d969cfeebdf54aa06e4357a1937c568a876
  impl_hash: sha256:5e887d367fc40d9fe0b7e4ed494f37dc00909f7d9381ff0097dd78626d0c6952
  ruleset_version: 2026-07-30.1
  sources:
  - path: src/Sample.Pricing.Api/Providers/CurveProvider.cs
    start: 6
    end: 9
docpipe_state:
  accepted: null
  review: null
---

# CurveProvider

<!-- docpipe:generated:start -->
`Sample.Pricing.Api.Providers.CurveProvider` — public sealed class, модуль `Sample.Pricing.Api`, домен `Sample.Pricing.Api`, владелец не задан.

### Исходники

- [`src/Sample.Pricing.Api/Providers/CurveProvider.cs`](../../../../src/Sample.Pricing.Api/Providers/CurveProvider.cs) — строки 6–9

### HTTP-эндпоинты

Нет.

### Зависимости

| Тип | Через | Документ |
| --- | --- | --- |
| `Sample.Common.Abstractions.IPricingProvider` | di | вне дерева документации |

### Связи

| Тип | Связь | Документ |
| --- | --- | --- |
| `Sample.Common.Abstractions.IPricingProvider` | implements | вне дерева документации |

### XML-doc из кода

> Provides discount curves.
<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
<!-- Какие данные поставляет и кому. 2–5 предложений. -->
<!-- docpipe:section:end purpose -->

## Источник данных

<!-- docpipe:section:start data_source -->
<!-- Откуда берутся данные: таблица, внешний сервис, кэш, файл.
     Актуальность и периодичность обновления. -->
<!-- docpipe:section:end data_source -->

## Контракт

<!-- docpipe:section:start contract -->
<!-- Что на входе, что на выходе, что означает пустой результат.
     Пустой результат и ошибка — разные вещи, и различие надо назвать. -->
<!-- docpipe:section:end contract -->

## Отказы

<!-- docpipe:section:start failure_modes -->
<!-- Что происходит при недоступности источника, таймауте, частичных данных. -->
<!-- docpipe:section:end failure_modes -->

## Замечания

<!-- docpipe:section:start notes -->
<!-- Известные ограничения, легаси, планы. Пусто — нормально. -->
<!-- docpipe:section:end notes -->
