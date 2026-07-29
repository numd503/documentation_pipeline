---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Services.PricingService`0
  doc_path: docs/modules/Sample.Pricing.Api/services/pricing-service.md
  title: PricingService
  fqn: Sample.Pricing.Api.Services.PricingService
  kind: service
  template: service
  template_ref: templates/service.md
  example_ref: templates/examples/service.md
  module: Sample.Pricing.Api
  module_csproj: src/Sample.Pricing.Api/Sample.Pricing.Api.csproj
  domain: Sample.Pricing.Api
  team: null
  signature_hash: sha256:05cf41b6d0f4f482d6403b17ccca761c8a27fa815f5b04b552aad5bf70a6f2a6
  impl_hash: sha256:3e188468eec8292d750a44b5f96624e284134d787c96b7aa6ae3e2dd5f264408
  ruleset_version: 2026-07-26.1
  sources:
  - path: src/Sample.Pricing.Api/Services/PricingService.Calculations.cs
    start: 3
    end: 6
  - path: src/Sample.Pricing.Api/Services/PricingService.cs
    start: 6
    end: 19
docpipe_state:
  accepted: null
  review: null
---

# PricingService

<!-- docpipe:generated:start -->
`Sample.Pricing.Api.Services.PricingService` — partial public sealed class, модуль `Sample.Pricing.Api`, домен `Sample.Pricing.Api`, владелец не задан.

### Исходники

- [`src/Sample.Pricing.Api/Services/PricingService.Calculations.cs`](../../../../src/Sample.Pricing.Api/Services/PricingService.Calculations.cs) — строки 3–6
- [`src/Sample.Pricing.Api/Services/PricingService.cs`](../../../../src/Sample.Pricing.Api/Services/PricingService.cs) — строки 6–19

### HTTP-эндпоинты

Нет.

### Зависимости

| Тип | Через | Документ |
| --- | --- | --- |
| `Sample.Common.Abstractions.IPricingProvider` | constructor | [CurveProvider](../providers/curve-provider.md) — реализация интерфейса |
| `Sample.Pricing.Api.Services.IPricingService` | di | вне дерева документации |

### Связи

| Тип | Связь | Документ |
| --- | --- | --- |
| `Sample.Pricing.Api.Services.IPricingService` | implements | вне дерева документации |

### XML-doc из кода

> Computes prices for instruments.
<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
<!-- Зачем сервис существует и какую задачу решает его вызывающий.
     2–5 предложений. -->
<!-- docpipe:section:end purpose -->

## Обязанности

<!-- docpipe:section:start responsibilities -->
<!-- За что сервис отвечает, а за что намеренно не отвечает.
     Границу указать явно: без неё сервис обрастает чужой логикой. -->
<!-- docpipe:section:end responsibilities -->

## Поведение и правила

<!-- docpipe:section:start behaviour -->
<!-- Инварианты, порядок вызовов, идемпотентность, побочные эффекты. -->
<!-- docpipe:section:end behaviour -->

## Взаимодействие

<!-- docpipe:section:start collaboration -->
<!-- Кого зовёт и зачем. Что будет, если зависимость недоступна. -->
<!-- docpipe:section:end collaboration -->

## Замечания

<!-- docpipe:section:start notes -->
<!-- Известные ограничения, легаси, планы. Пусто — нормально. -->
<!-- docpipe:section:end notes -->
