---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Workflows.ValuationWorkflow`0
  doc_path: docs/modules/Sample.Pricing.Api/workflows/valuation-workflow.md
  title: ValuationWorkflow
  fqn: Sample.Pricing.Api.Workflows.ValuationWorkflow
  kind: workflow
  template: workflow
  module: Sample.Pricing.Api
  domain: Sample.Pricing.Api
  team: null
docpipe_state:
  accepted: null
  review: null
---

# ValuationWorkflow

<!-- docpipe:generated:start -->
<!-- Блок собран инструментом и перезаписывается при каждом прогоне. -->

`Sample.Pricing.Api.Workflows.ValuationWorkflow` — модуль `Sample.Pricing.Api`.

> End-to-end valuation sequence.

<!-- docpipe:generated:end -->

## Назначение

<!-- docpipe:section:start purpose -->
Производит переоценку портфеля целиком: от списка инструментов до сохранённого
набора цен. Это единица работы, которую запускает расчётный цикл, — отдельные
цены считаются внутри, наружу они не выставляются.
<!-- docpipe:section:end purpose -->

## Шаги

<!-- docpipe:section:start steps -->
1. Отбор инструментов портфеля на дату расчёта.
2. Получение кривых, нужных отобранным инструментам.
3. Расчёт цены по каждому инструменту.
4. Сохранение набора цен как одного результата.

Шаги идут строго последовательно: цена зависит от кривой, а сохранение —
от полноты набора.
<!-- docpipe:section:end steps -->

## Запуск

<!-- docpipe:section:start triggers -->
Запускается расчётным циклом по завершении загрузки рыночных данных, то есть
событием, а не расписанием. Прямой вызов из API не предусмотрен.
<!-- docpipe:section:end triggers -->

## Компенсация

<!-- docpipe:section:start compensation -->
Компенсации **нет**, и это существенно. Сбой на середине оставляет портфель
без результата за дату; частично посчитанные цены не сохраняются, поэтому
несогласованного состояния не возникает, но и автоматического повтора
не происходит — перезапуск ручной.
<!-- docpipe:section:end compensation -->

## Замечания

<!-- docpipe:section:start notes -->
Параллельного исполнения по инструментам нет: на портфелях текущего размера
это не узкое место.
<!-- docpipe:section:end notes -->
