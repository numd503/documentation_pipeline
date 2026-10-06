---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Controllers.PricingController`0
  doc_path: docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md
  title: PricingController
  fqn: Sample.Pricing.Api.Controllers.PricingController
  kind: controller
  template: controller
  module: Sample.Pricing.Api
  domain: Sample.Pricing.Api
  team: null
docpipe_state:
  accepted: null
  review: null
---

# PricingController

<!-- docpipe:generated:start -->
<!-- Блок собран инструментом и перезаписывается при каждом прогоне.
     В образце он сокращён: полный вид смотрите в реальном документе. -->

`Sample.Pricing.Api.Controllers.PricingController` — public class,
модуль `Sample.Pricing.Api`.

| Метод | Маршрут | Член |
| --- | --- | --- |
| `POST` | `api/v1/Pricing` | `RecalculateAsync` |
| `GET` | `api/v1/Pricing/{id:guid}` | `GetAsync` |

<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
Точка входа для пересчёта и чтения цен по инструментам. Потребитель — расчётный
клиент, которому нужно либо запустить пересчёт по портфелю, либо получить уже
посчитанную цену по идентификатору.

Контроллер собственной логики не содержит: он проверяет вход и передаёт работу
`IPricingService`. Разделение намеренное — тот же расчёт вызывается и из
воркфлоу, минуя HTTP.
<!-- docpipe:section:end purpose -->

## Контракт API

<!-- docpipe:section:start api -->
`POST api/v1/Pricing` — принимает описание запроса на пересчёт, возвращает
идентификатор результата. Пересчёт запускается синхронно; при большом портфеле
ответ может занимать десятки секунд.

`GET api/v1/Pricing/{id:guid}` — возвращает ранее посчитанную цену.
Неизвестный идентификатор — штатная ситуация, а не ошибка: клиент опрашивает
результат и до его готовности получает отсутствие.
<!-- docpipe:section:end api -->

## Поведение и правила

<!-- docpipe:section:start behaviour -->
Идентификатор запроса задаёт клиент, поэтому повторный `POST` с тем же телом
создаёт новый расчёт — идемпотентности нет. Это осознанное ограничение: цена
зависит от момента расчёта, и склейка запросов вернула бы устаревшее значение.

Авторизация выполняется на уровне пайплайна, атрибутов на самом контроллере нет.
<!-- docpipe:section:end behaviour -->

## Взаимодействие

<!-- docpipe:section:start collaboration -->
Единственная зависимость — `IPricingService`, внедряется конструктором.
При её недоступности контроллер не поднимется вовсе: сервис регистрируется
на старте, и отсутствие регистрации — ошибка конфигурации, а не рантайма.
<!-- docpipe:section:end collaboration -->

## Замечания

<!-- docpipe:section:start notes -->
Версия маршрута зафиксирована как `v1` и в коде не параметризована. Появление
`v2` потребует второго контроллера, а не флага.
<!-- docpipe:section:end notes -->
