---
docpipe:
  schema: business/1
  id: bp.valuation.twinml-scoring
  kind: process
  title: Онлайн УФН
  capability: cap.valuation
  owner_team: ML
  status: active
  entry:
  - kind: list_event
    ref: ItemAdded
    scope: UserTasks
  - kind: workflow
    ref: SampleWorkflow
    version: "2"
  upstream:
  - kind: kafka
    ref: pricing.eod.requested
    owner: команда интеграции
    verify: false
    note: событие потребляем не мы, к нам приходит уже вызовом
  produces: []
  contracts:
  - direction: state
    ref: Sbt.Sample.Models.SampleAggregate
docpipe_state:
  accepted: null
  review: null
---

# Онлайн УФН

<!-- docpipe:generated:start -->
<!-- Блок собирается инструментом и перезаписывается при каждом прогоне. -->
<!-- docpipe:generated:end -->

## Зачем процесс существует

<!-- docpipe:section:start purpose -->
Оценивает задачу пользователя моделью и проставляет пороги, чтобы дальнейшая
обработка шла по правильной ветке.
<!-- docpipe:section:end purpose -->

## Как запускается

<!-- docpipe:section:start trigger -->
Создание задачи пользователя. Цепочка выше нам не принадлежит.
<!-- docpipe:section:end trigger -->

## Шаги

<!-- docpipe:section:start steps -->
Сбор идентификаторов, расчёт индивидуальных порогов, скоринг.
<!-- docpipe:section:end steps -->

## Правила и ограничения

<!-- docpipe:section:start rules -->
Пороги пересчитываются на каждый запуск.
<!-- docpipe:section:end rules -->

## Замечания

<!-- docpipe:section:start notes -->
<!-- Пусто — нормально. -->
<!-- docpipe:section:end notes -->
