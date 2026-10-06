---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Services.PricingService`0
  doc_path: docs/modules/Sample.Pricing.Api/services/pricing-service.md
  title: PricingService
  fqn: Sample.Pricing.Api.Services.PricingService
  kind: service
  template: service
  module: Sample.Pricing.Api
  domain: Sample.Pricing.Api
  team: null
docpipe_state:
  accepted: null
  review: null
---

# PricingService

<!-- docpipe:generated:start -->
<!-- Блок собран инструментом и перезаписывается при каждом прогоне. -->

`Sample.Pricing.Api.Services.PricingService` — реализация `IPricingService`,
модуль `Sample.Pricing.Api`.

| Тип | Через | Документ |
| --- | --- | --- |
| `Sample.Common.Abstractions.IPricingProvider` | constructor | [CurveProvider](../providers/curve-provider.md) — реализация интерфейса |

<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
Считает цену инструмента по кривой дисконтирования. Единственное место, где
формула дисконтирования применяется к данным, — и контроллер, и воркфлоу зовут
именно его.
<!-- docpipe:section:end purpose -->

## Обязанности

<!-- docpipe:section:start responsibilities -->
Отвечает за применение кривой к сумме и за выбор точки кривой по сроку.

Намеренно **не** отвечает за получение кривой: её поставляет
`IPricingProvider`. Граница проведена так, чтобы смена источника кривых
(файл, сервис, кэш) не задевала формулу.

Не отвечает и за сохранение результата: цена возвращается вызывающему,
запись — его дело.
<!-- docpipe:section:end responsibilities -->

## Поведение и правила

<!-- docpipe:section:start behaviour -->
Расчёт чистый: одинаковый вход при одинаковой кривой даёт одинаковый выход,
состояния между вызовами не остаётся. Отсюда безопасность при параллельном
вызове.

Отсутствие кривой на нужную дату не подменяется соседней точкой — вызов
завершается ошибкой. Молчаливая подстановка соседней даты давала бы
правдоподобную и неверную цену.
<!-- docpipe:section:end behaviour -->

## Взаимодействие

<!-- docpipe:section:start collaboration -->
Зовёт `IPricingProvider` за кривой на каждый расчёт; кэширования нет.
При недоступности провайдера расчёт завершается ошибкой и наверх уходит
исходная причина — подменять её общим сообщением нельзя, по ней разбирают
инциденты.
<!-- docpipe:section:end collaboration -->

## Замечания

<!-- docpipe:section:start notes -->
Дисконтирование реализовано для одной валюты. Мультивалютный случай
не поддержан и потребует смены сигнатуры, а не настройки.
<!-- docpipe:section:end notes -->
