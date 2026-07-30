---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Controllers.PricingController`0
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
  signature_hash: sha256:0e29455f07023ae28072c3bef5b59c2725eead683869ad1b48c23170251613b4
  impl_hash: sha256:74ba5471180f9a88bdbc3e969e8ac5b8a802e1741f504714bbfbafaaf19e50c2
  ruleset_version: 2026-07-30.1
  sources:
  - path: src/Sample.Pricing.Api/Controllers/PricingController.cs
    start: 8
    end: 26
docpipe_state:
  accepted: null
  review: null
---

# PricingController

<!-- docpipe:generated:start -->
`Sample.Pricing.Api.Controllers.PricingController` — public sealed class, модуль `Sample.Pricing.Api`, домен `Sample.Pricing.Api`, владелец не задан.

### Исходники

- [`src/Sample.Pricing.Api/Controllers/PricingController.cs`](../../../../src/Sample.Pricing.Api/Controllers/PricingController.cs) — строки 8–26

### HTTP-эндпоинты

| Метод | Маршрут | Член | Строка |
| --- | --- | --- | --- |
| `POST` | `api/v1/Pricing` | `RecalculateAsync` | 24 |
| `GET` | `api/v1/Pricing/{id:guid}` | `GetAsync` | 18 |

### Зависимости

| Тип | Через | Документ |
| --- | --- | --- |
| `Sample.Pricing.Api.Services.IPricingService` | constructor | [PricingService](../services/pricing-service.md) — реализация интерфейса |

### Связи

Нет.

### XML-doc из кода

> Handles pricing requests.
<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
<!-- Зачем этот контроллер существует и какую задачу решает потребитель API.
     2–5 предложений. Сигнатуры не пересказывать — они выше и в исходниках. -->
<!-- docpipe:section:end purpose -->

## Контракт API

<!-- docpipe:section:start api -->
<!-- По каждому эндпоинту: что принимает, что возвращает, какие коды ответа
     значимы, какие ошибки штатны. Таблицу маршрутов не повторять. -->
<!-- docpipe:section:end api -->

## Поведение и правила

<!-- docpipe:section:start behaviour -->
<!-- Валидация, авторизация, идемпотентность, побочные эффекты. -->
<!-- docpipe:section:end behaviour -->

## Взаимодействие

<!-- docpipe:section:start collaboration -->
<!-- Кого зовёт и зачем. Что будет, если зависимость недоступна. -->
<!-- docpipe:section:end collaboration -->

## Замечания

<!-- docpipe:section:start notes -->
<!-- Известные ограничения, легаси, планы. Пусто — нормально. -->
<!-- docpipe:section:end notes -->
