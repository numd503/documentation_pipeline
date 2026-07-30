---
docpipe:
  schema: business/1
  id: be.valuation.user-task
  kind: entity
  title: Задача пользователя
  capability: cap.valuation
  owner_team: Pricing
  status: active
  entry:
  - kind: table
    ref: UserTasks
docpipe_state:
  accepted: null
  review: null
---

# Задача пользователя

<!-- docpipe:generated:start -->
<!-- Блок собирается `docpipe business build`: состав полей списка приезжает
     из реестра структуры, перечислять его руками не нужно. -->
<!-- docpipe:generated:end -->

## Что это такое

<!-- docpipe:section:start purpose -->
Единица работы, назначаемая на пользователя: посчитать оценку, проверить
расхождение, подтвердить результат. Заводится процессами оценки и закрывается
либо вручную, либо по результату расчёта.
<!-- docpipe:section:end purpose -->

## Поля

<!-- docpipe:section:start fields -->
Тип задачи определяет, кому она достанется и какой срок считается нарушенным;
справочник типов ведётся отдельно и меняется без правки кода. Время старта
проставляется в момент назначения, а не создания: задача может пролежать
в очереди дольше, чем выполняться, и смешивать эти два интервала нельзя.
<!-- docpipe:section:end fields -->

## Жизненный цикл

<!-- docpipe:section:start lifecycle -->
Создана → назначена → закрыта с указанием причины. Закрытая задача
не переоткрывается: вместо этого заводится новая со ссылкой на прежнюю,
иначе история назначений теряется.
<!-- docpipe:section:end lifecycle -->

## Связи

<!-- docpipe:section:start relations -->
Задача ссылается на тип задачи; связь объявлена в реестре структуры полем
`ListSource` и потому видна инструменту — перечислять её здесь не нужно.
<!-- docpipe:section:end relations -->

## Замечания

<!-- docpipe:section:start notes -->
Массовое закрытие задач при перезапуске расчёта — известное поведение,
про которое регулярно спрашивают. Оно намеренное.
<!-- docpipe:section:end notes -->
