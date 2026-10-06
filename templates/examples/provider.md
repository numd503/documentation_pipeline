---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Providers.CurveProvider`0
  doc_path: docs/modules/Sample.Pricing.Api/providers/curve-provider.md
  title: CurveProvider
  fqn: Sample.Pricing.Api.Providers.CurveProvider
  kind: provider
  template: provider
  module: Sample.Pricing.Api
  domain: Sample.Pricing.Api
  team: null
docpipe_state:
  accepted: null
  review: null
---

# CurveProvider

<!-- docpipe:generated:start -->
<!-- Блок собран инструментом и перезаписывается при каждом прогоне. -->

`Sample.Pricing.Api.Providers.CurveProvider` — реализация
`Sample.Common.Abstractions.IPricingProvider`, модуль `Sample.Pricing.Api`.

> Provides discount curves.

<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
Поставляет кривые дисконтирования расчёту цен. Единственный потребитель —
`PricingService`; провайдер существует отдельно, чтобы источник кривых можно
было заменить, не трогая формулу.
<!-- docpipe:section:end purpose -->

## Источник данных

<!-- docpipe:section:start data_source -->
Кривые берутся из справочника, загружаемого при старте приложения. Обновление
внутри работающего процесса не предусмотрено: смена набора кривых требует
перезапуска.

Актуальность, соответственно, равна моменту старта. Для суточного расчётного
цикла этого достаточно; для внутридневного — нет, и это стоит учитывать
при планировании.
<!-- docpipe:section:end data_source -->

## Контракт

<!-- docpipe:section:start contract -->
На входе — идентификатор кривой и дата. На выходе — точка кривой.

Отсутствие кривой и ошибка получения различаются намеренно: первое возвращается
как пустой результат и является штатной ситуацией для новых инструментов,
второе поднимается исключением.
<!-- docpipe:section:end contract -->

## Отказы

<!-- docpipe:section:start failure_modes -->
Справочник не загрузился при старте — приложение не поднимается. Это осознанный
выбор: работать с пустым набором кривых хуже, чем не работать вовсе, потому что
расчёт молча вернул бы нули.

Частичной загрузки не бывает: набор применяется целиком либо не применяется.
<!-- docpipe:section:end failure_modes -->

## Замечания

<!-- docpipe:section:start notes -->
Интерполяции между точками кривой нет — возвращается только объявленная точка.
<!-- docpipe:section:end notes -->
