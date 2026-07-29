# Декларативные реестры АС CF: где на самом деле объявлены точки входа

Отчёт по разведке боевого репозитория (29.07.2026). Здесь только факты: что найдено,
где лежит, как читается и что ломает наивную реализацию. Что с этим делать —
в [`business-implementation-plan.md`](business-implementation-plan.md).

---

## Главный вывод

**АС CF построена на платформе, управляемой метаданными.** Джобы, workflow, обработчики
событий, таблицы и их поля объявлены не в C#, а строками в XML-файлах, которые при
развёртывании кладутся в БД. Исполняют их универсальные раннеры, получающие имя типа
или идентификатор из данных.

Из этого следует жёсткое ограничение, не зависящее от инструмента разбора:

> **Статический анализ C# не восстановит граф триггеров АС CF — ни tree-sitter, ни Roslyn.
> Этой информации в коде нет.**

Проверено на трёх механизмах:

- джоб объявлен строкой `JOBCLASS="…IFinancialReportJob"`, Quartz резолвит **интерфейс**
  рефлексией;
- workflow стартует `WorkflowRequestRunner.StartWorkflow(request.WorkflowId,
  request.WorkflowVersion, …)` — идентификатор приходит из данных, поэтому литерала
  конкретного workflow в коде **не существует**;
- обработчик события объявлен атрибутом `Class=` в структуре списка, вызывается ядром.

Практическое следствие для пайплайна документации: чтение реестров даёт для бизнес-слоя
больше, чем парсер .NET. Парсер отвечает «как это устроено», реестры — «что чем
запускается».

---

## Полная цепочка одного процесса

Разобрана до конца на workflow команды ML. Ни одно звено не выведено эвристикой —
все объявлены декларативно:

```
Список UserTasks (таблица USER_TASKS), событие ItemAdded
        │   Cashflow.Structure.xml, <EventReceivers>
        ▼
Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerTwinMLWorkflowEventReceiver
        │   создаёт WorkflowRequest { WorkflowId, WorkflowVersion, … }
        ▼
WorkflowRequestRunner.StartWorkflow(request.WorkflowId, request.WorkflowVersion, data, …)
        │   универсальный диспетчер, один на все workflow
        ▼
workflow-core: TwinMLWorkflow@2          Data/Items/Workflows/*.json
        │   шаги: CollectClientIdsByUserTaskRecordsStep → IndividualThresholds → …
        ▼
агрегат состояния Sbt.Cashflow.TwinMLWorkflow.Models.TwinMLWorkflowAggregate
```

Единственное невыводимое звено — **какая версия workflow активна**. Она живёт в БД
(см. ловушку про `Override`), в репозитории её нет.

---

## Найденные реестры

| Реестр | Файл | Что объявляет | Якорь |
|---|---|---|---|
| Grid-сервисы | `sbt.cashflow.gridservice.subrepo/Sbt.CMS.GridService.Initializer/services.config` | сервисы кластера Ignite, их классы и **команды** | `@name` |
| Джобы | `sbt.cashflow.deployment/Sbt.Deployment.Db/Cashflow/Data/Items/Items_Basic.xml` | расписания Quartz, интерфейс задания | `@KeyValue` при `KeyField="JOBTITLE"` |
| Workflow | `sbt.cashflow.deployment/…/Data/Items/Items-Workflows.xml` → JSON | определения workflow-core и их шаги | `Id` + `Version` из JSON |
| Структура | `sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.Structure/Cashflow.Structure.xml` | списки, поля, связи, формы, **обработчики событий** | `List/@InnerName` + `EventType` |
| Kafka | — | — | источник не найден, ведётся вручную |

### Grid-сервисы — `services.config`

```xml
<servicesConfiguration>
  <dotNetServices>
    <add name="ICalcResult" assembly="Sbt.Cashflow.Grid.Services.CalcResult"
         class="Sbt.Cashflow.Grid.Services.CalcResult.CalcResult"
         maxPerNodeCount="1" roles="serviceNode" team="Core">
      <interceptors>
        <add type="Sbt.CMS.Cashflow.Service.IgniteServiceUserPropagator, …" />
      </interceptors>
    </add>
```

`item_xpath: "./dotNetServices/add"`, поля: `ref=@name`, `impl_fqn=@class`,
`assembly=@assembly`, `team=@team`.

**Якорь — `@name`, а не `@class`.** Доказательство в самих данных: `name="CalcDxExcel"` при
`class="…CalcService.DXExcelCalcService"`, `name="CalcDxLibre"` при `class="…DXLibreCalcService"`.
Имя сервиса не выводится из класса и не обязано быть именем интерфейса — это
независимый идентификатор, который знает вызывающий. Переименование класса якорь
не меняет; переименование `@name` ломает вызовы, то есть является контрактным изменением.

`@team` (`Core`, `TVM`, …) — готовый источник для списка команд и для сверки
с `ownership.yaml`.

### Джобы — `Items_Basic.xml`

```xml
<Project InnerName="SbtCms"><Lists>
  <List InnerName="JOBS" TableName="JOBS"><Items>
    <Item KeyField="JOBTITLE" KeyValue="PM: Load limits" Override="false"
          JOBCLASS="Sbt.CMS.Cashflow.Infrastructure.QuartzJobInterfaces.ILoadLimitsJob"
          JOBASSEMBLY="Sbt.CMS.Cashflow.Infrastructure" JOBDISABLED="0"
          JOBSCHEDULE="&lt;JobSchedule Interval=&quot;60&quot; FirstTime=&quot;2017-01-01&quot; /&gt;" />
```

`item_xpath: "./Lists/List[@InnerName='JOBS']/Items/Item[@KeyField='JOBTITLE']"`.

`JOBCLASS` — **интерфейс**, не реализация. До кода два шага: интерфейс → реализации
через `related[].implemented_by` в манифесте → узел → документ.

`Interval` — **в секундах** (подтверждено). Отсюда практический вывод для формулировок
в документации: `Interval="60"` у отчётного джоба — это опрашивающий цикл, а не
«отчёты формируются раз в минуту». Расписанием интервал является только у крупных
значений (43200 — полусутки, 86400 — сутки).

### Workflow — `Items-Workflows.xml` + JSON

```xml
<Item KeyField="WorkflowTitle" KeyValue="Онлайн УФН" Override="false"
      IsActive="true" IsCancellable="false">
  <File Path="Data\Items\Workflows\RealtyFact.v1.json" FieldInnerName="WorkflowDefinition"/>
</Item>
```

```json
{"Id": "TwinMLWorkflow", "Version": 2,
 "DataType": "Sbt.Cashflow.TwinMLWorkflow.Models.TwinMLWorkflowAggregate, Sbt.Cashflow.TwinMLWorkflow",
 "DefaultErrorBehavior": "Terminate",
 "Steps": [{"Id": "CollectClientIdsByUserTaskRecordsStep",
            "StepType": "Sbt.Cashflow.TwinMLWorkflow.Steps.CollectClientIdsByUserTaskRecordsStep, Sbt.Cashflow.TwinMLWorkflow",
            "NextStepId": "IndividualThresholds"}]}
```

Формат JSON — `DefinitionSourceV1` библиотеки **workflow-core**. Порядка 45 записей,
часть в нескольких версиях.

Два имени, оба нужны: `KeyValue` — бизнес-заголовок (по-русски, для человека),
`Id`+`Version` — идентичность в движке (якорь).

`DataType` — агрегат состояния процесса. Это лучший из найденных кандидатов
в бизнес-сущность: объявлен декларативно, связан с процессом один к одному.

База для `Path` подтверждена фактическим путём файла:
`sbt.cashflow.deployment/Sbt.Deployment.Db/Cashflow` + `Data/Items/Workflows/RealtyFact.v1.json`.

### Структура — `Cashflow.Structure.xml`

Файл на ~22 000 строк, объявляет предметную модель платформы:

```xml
<List InnerName="UserTasks" TableName="USER_TASKS" DisplayName="Задачи пользователей">
  <Fields>
    <FieldText   InnerName="UserTasksTitle" ColumnName="TITLE"
                 DisplayName="Наименование задачи" Required="false" MaximumLength="300" IsTitle="true"/>
    <FieldLookup InnerName="UserTasksType" ColumnName="TASK_TYPE_ID"
                 DisplayName="Тип задачи" Required="true" ListSource="UserTaskTypes"/>
    …
  </Fields>
  <Forms>…</Forms>
  <EventReceivers>
    <EventReceiver Class="Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerTwinMLWorkflowEventReceiver"
                   Assembly="Sbt.Cashflow.ML.EventReceivers" EventType="ItemAdded"/>
  </EventReceivers>
</List>
```

Это самая ценная находка разведки, по трём причинам:

1. **Бизнес-сущности уже описаны, причём по-русски.** `DisplayName` у списка и у каждого
   поля — готовый глоссарий, написанный аналитиками много лет назад. Каталог сущностей
   можно **генерировать**, а не сочинять заново.
2. **Связи между сущностями объявлены**, а не выводятся: `FieldLookup/@ListSource`
   и `FieldMultiLookup/@ListSource` — это внешние ключи в терминах предметной области.
   Ранее принятое решение «не выводить связи сущностей из графа кода» остаётся в силе:
   здесь связи не выводятся, а читаются из декларации.
3. **Обработчики событий — недостающий вид триггера.** Пара «список + `EventType`»
   (`ItemAdding`, `ItemAdded`, `ItemUpdating`, `ItemUpdated`) — точка входа уровня
   контракта: она не меняется от рефакторинга C#.

Виды полей, встреченные в примере: `FieldText`, `FieldFile`, `FieldNumber`, `FieldBool`,
`FieldDateTime`, `FieldLookup`, `FieldMultiLookup`, `FieldMultiUser`. Перечень неполон —
читать нужно по префиксу `Field*`, а не по белому списку.

`<Forms>` (Display / New / Edit / List, набор колонок, запрос) в объёме бизнес-слоя
не нужны, но они описывают пользовательский интерфейс и однажды пригодятся.

---

## Ловушки

Каждая однажды прошла бы поверхностную проверку и дала бы молча неверный результат.

1. **`.//add` в `services.config` ловит интерцепторы.** Внутри `<add name="…">` лежит
   `<interceptors><add type="…"/></interceptors>`. Обход по `.//add` даст записи без
   `@name` — либо падение, либо фантомные сервисы. Только `./dotNetServices/add`.
2. **`.//Item` ловит справочники.** В `Items_Basic.xml` кроме `JOBS` лежат десятки
   справочников БД. Якорить обязательно на `List[@InnerName='JOBS']`.
3. **`@KeyValue` без `@KeyField` бессмыслен**: смысл значения задаётся полем ключа.
   Предикат `[@KeyField='JOBTITLE']` — часть контракта, а не украшение.
4. **ElementTree не умеет выбирать атрибуты.** `find("./File[…]/@Path")` не работает;
   суффикс `/@Attr` придётся отрезать самому и делать `find(path).get(attr)`. Без этого
   конфигурация выглядит рабочей и молча возвращает `None`.
5. **`Path` относителен к базе развёртывания**, а не к каталогу файла реестра и не
   к корню репозитория. Наивная склейка даёт `…/Data/Items/Data/Items/Workflows/…`.
   База задаётся явно. Разделители — Windows, переводить в POSIX перед склейкой.
6. **`JOBSCHEDULE` — экранированный XML внутри атрибута.** Нужен второй `ET.fromstring`
   по разэкранированному значению.
7. **`StepType` и `DataType` — assembly-qualified**: `"FQN, Сборка"`. Резать по первой
   запятой **вне квадратных скобок**: у дженериков (`Foo\`2[[…, Asm]], Asm`) наивный
   `split(",")[0]` даёт обрубок.
8. **`Override="false"` означает, что файл объявляет намерение, а не факт.** Такая запись
   при развёртывании не перезаписывает существующую строку в БД. Расписание и признак
   активности для таких записей могут расходиться с боевыми.
9. **`IsActive="true"` не является признаком актуальности.** Проверено: стоит
   одновременно у нескольких версий одного workflow. Активную версию знает БД.
10. **Литеральной ступени разрешения для workflow, джобов и grid-сервисов не существует.**
    Это структурное свойство диспетчеризации по данным, а не дефект поиска. Линт,
    считающий пустой литеральный поиск ошибкой, будет вечно красным.
11. **BOM во всех XML.** Читать байтами: `read_text(encoding="utf-8")` уронит
    `ET.fromstring`. Та же ловушка, что у `.csproj`.
12. **Реестр в репозитории и реестр в БД — разные вещи.** В `sys_event_receivers`
    боевой БД встречено имя `…UserTasksTriggerTwinMLWorkflowEventReceiver`, тогда как
    в `Cashflow.Structure.xml` объявлен `…UserTasksAddedTriggerTwinMLWorkflowEventReceiver`.
    Требует проверки: опечатка при переносе или реальное расхождение. Инструмент видит
    только репозиторий и обязан это оговаривать.

---

## Вывод сборки: `artifacts/`

В корне репозитория лежит каталог `artifacts/` с подкаталогами `bin` и `obj`. Это
централизованная раскладка вывода сборки .NET 8+ (`UseArtifactsOutput` /
`ArtifactsPath` в `Directory.Build.props`): вместо `bin/` и `obj/` в каждом проекте
всё складывается в один каталог в корне.

**Встроенных исключений `**/obj/**` и `**/bin/**` для этого репозитория недостаточно**
только по видимости — шаблоны совпадут и здесь, потому что каталоги называются так же.
Но полагаться на это не следует: добавить `**/artifacts/**` явно и сверить `--stats`
до и после. В `artifacts/obj/**` лежат сгенерированные `.cs`, и правило `**/*.g.cs`
покрывает не все из них.

Побочный эффект той же раскладки: `grep -rn` по репозиторию даёт сотни ложных вхождений
из `artifacts/bin`. Все поиски по АС CF вести через `git grep` — он ищет только по
отслеживаемым файлам.

---

## Что осталось неизвестным

| Вопрос | Почему важно | Как проверяется |
|---|---|---|
| Какая версия workflow активна | версия входит в якорь | только БД или владелец; в репозиторий не выводится |
| Полный перечень платформенных реестров | могут быть ещё виды триггеров | `git grep -h -o 'InnerName="[^"]*"' -- 'sbt.cashflow.deployment/*.xml' \| sort \| uniq -c` |
| Кто создаёт `WorkflowRequest` | промежуточное звено цепочки | `git grep -n "WorkflowRequest" -- '*.cs'` |
| Источник перечня топиков Kafka | четвёртый вид триггера | не найден; ведётся вручную |
| Расхождение БД и `Cashflow.Structure.xml` | достоверность реестра | сверка `sys_event_receivers` с файлом |
