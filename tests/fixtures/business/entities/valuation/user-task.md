---
docpipe:
  schema: business/1
  id: be.valuation.user-task
  kind: entity
  title: Задача пользователя
  capability: cap.valuation
  owner_team: ML
  status: active
  entry: []
  contracts:
  - direction: state
    ref: UserTasks
docpipe_state:
  accepted: null
  review: null
---

# Задача пользователя

<!-- docpipe:generated:start -->
<!-- docpipe:generated:end -->

## Что это такое

<!-- docpipe:section:start purpose -->
Единица работы, назначаемая на пользователя. Заводится процессами оценки
и закрывается вручную либо по результату расчёта.
<!-- docpipe:section:end purpose -->

## Жизненный цикл

<!-- docpipe:section:start lifecycle -->
Создана → назначена → закрыта с указанием причины.
<!-- docpipe:section:end lifecycle -->
