---
docpipe:
  schema: business/1
  id: bp.valuation.limits-load
  kind: process
  title: Загрузка лимитов
  capability: cap.limits
  owner_team: Pricing
  status: active
  entry:
  - kind: job
    ref: 'PM: Load limits'
  upstream:
  - kind: kafka
    ref: limits.published
    owner: команда лимитов
    verify: false
    note: лимиты публикует смежная система, литерала в нашем коде нет
  contracts:
  - direction: state
    ref: PositionLimits
docpipe_state:
  accepted: null
  review: null
---

# Загрузка лимитов

<!-- docpipe:generated:start -->
<!-- Блок собирается `docpipe business build` по реестрам и манифесту
     и перезаписывается при каждом прогоне. В образце он оставлен пустым:
     его содержимое зависит от репозитория. -->
<!-- docpipe:generated:end -->

## Зачем процесс существует

<!-- docpipe:section:start purpose -->
Переносит утверждённые лимиты в систему, чтобы проверка сделок опиралась
на действующие значения, а не на те, что были на момент последнего релиза.
<!-- docpipe:section:end purpose -->

## Как запускается

<!-- docpipe:section:start trigger -->
По расписанию, до начала торгового дня. Публикация лимитов смежной системой
происходит ночью и нам не принадлежит — она объявлена в `upstream`
и не проверяется.
<!-- docpipe:section:end trigger -->

## Шаги

<!-- docpipe:section:start steps -->
Забираются утверждённые лимиты, сверяются с действующими, расхождения
применяются одной транзакцией. Частичное применение недопустимо: половина
новых лимитов и половина старых — это состояние, в котором проверка сделок
даёт неверный ответ, не сообщая об этом.
<!-- docpipe:section:end steps -->

## Правила и ограничения

<!-- docpipe:section:start rules -->
Лимит, исчезнувший из источника, не удаляется автоматически: пропажа чаще
означает сбой выгрузки, чем отмену лимита. Такие строки попадают в отчёт
и снимаются руками.
<!-- docpipe:section:end rules -->

## Замечания

<!-- docpipe:section:start notes -->
Процесс описан здесь целиком; исторические решения по составу лимитов —
в системе-источнике.
<!-- docpipe:section:end notes -->
