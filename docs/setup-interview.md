# Интервью настройки: кто решает и о чём спрашивать

Протокол интервью скилла `setup` (S29): о чём агент контура спрашивает
человека, а что делает сам, как выглядит вопрос и куда ложится ответ. Вопрос
строится из находки `docpipe setup status` (S24) по её коду, у каждого
варианта ответа есть адрес — файл настройки и ключ в нём, — а у каждого
«не берём» есть причина, которую назвал человек.

**Вопрос без адреса — анкета.** Ответ на него никуда не ляжет, прогон его
не увидит, и следующая сессия задаст тот же вопрос снова. Поэтому вариант
здесь — это правка, а не мнение: «не документируем» значит «правило отсева
в `rules.yaml` с вашей причиной», а не «согласен».

Рядом: [`setup.md`](setup.md) — команды и коды находок,
[`setup-map.md`](setup-map.md) — что значит каждый ключ и что в нём
ломается молча, [`configuration.md`](configuration.md) — `docpipe.yaml`.
Обоснования — в плане, задача S28 и решения Р-1…Р-6, П-1
([`setup-implementation-plan.md`](setup-implementation-plan.md)); граница
детерминизма — [`purpose.md`](../purpose.md), «Настройка с ассистентом».

**Каталог не отстаёт от кода по построению.** `tests/test_setup_interview.py`
держит: раздел на каждый код `FINDING_CODES` и ни одного лишнего, в порядке
кодов; каждый ключ, на который указывает правка, — строка карты
`setup-map.md` (помощник `tests/setup_map_support.py`); каждая названная
команда `docpipe` и вид `setup candidates` зарегистрированы; ключи из
«где решение» кода есть среди правок его раздела; правка записи «не берём» —
только вариантом вопроса, и причина в ней — слова человека; у находки-решения
2–4 варианта и у каждого есть адрес, у дефекта вопроса нет, есть починка;
заголовок вопроса — не длиннее 12 символов; пример ссылается на фикстуру
или тест, которые существуют.

## Кто решает

Граница проходит по смыслу решения, а не по удобству.

| Решение | Кто | Как |
|---|---|---|
| позитивное техническое правило, доказуемое кодом: вид для группы (из уже объявленных), `url_rewrite` по прокси и интерцептору, `http_wrappers`, `url_builders`, `registry_calls`, `di_methods`, `dispatch_interfaces` | агент | пишет правило (Р-1), перезапускает инструмент, показывает разницу и доказательство (`файл:строка`); человек видит `git diff` |
| любое «не документируем» / «не берём»: `not_enrolled`, `exclude`, правила отсева, `pages.yaml remove`, записи секции `link` (в том числе `document: false`), `link.unresolvable` | человек | агент спрашивает с вариантами; **причину называет человек**, агент её не дописывает |
| имена, которые становятся ключами: домены, команды владения, имя раздела (`features[].name` — ключ якоря), имя нового вида (сегмент `doc_path` и скелет) | человек | агент предлагает варианты из находки; где кода находки нет (домены, разделы) — называет кандидатов вслух, без вопроса |
| область, остановка, принятие — коммит настройки (П-1) | человек | — |

**Почему именно так.** Позитивное правило проверяет прогон: неверный
`http_wrappers` даёт вызовы, которые не связались, и это видно разницей
`setup status`. «Не документируем» прогоном не проверить: правило отсева
убирает находку при любой причине, и отсев с правдоподобной причиной
неотличим от решения человека. Догадка агента входит в пайплайн только
через файл, который человек прочитал (`purpose.md`, граница детерминизма);
в причине отсева такой файл обязан хранить слова человека.

> **Ловушка. Агент, дописывающий причину сам, ломает границу детерминизма
> тихо.** Загрузчик проверяет, что причина непуста, но не кто её написал.
> Поэтому у правки «не берём» причина — всегда ответ человека: свободным
> ответом на вопрос или отдельным вопросом, если инструмент свободного
> ответа не даёт. Подпись варианта («Не документируем») причиной не является.

**У позитивного правила `reason` — адрес доказательства**, а не довод:
«`setup candidates http-wrappers`: 47 вызовов, тело — `http-extensions.ts:16`»,
как `note` у черновика скилла `recon`. Его пишет агент, и разница с отсевом
в том, что доказательство проверяется по коду, а довод «не нужно» — нет.
Поле `reason` есть у записей `docpipe.yaml` (`di_methods`,
`dispatch_interfaces`, `web.http_wrappers`, `web.url_builders`,
`web.url_rewrite`, `web.registry_calls`) и у `add` в `pages.yaml`. **У правила
вида (`dotnet.rules[]`, `web.rules[]`) поля `reason` нет**: допустимые ключи —
`id`, `kind`, `template`, `priority`, `when` (`classify._RULE_KEYS`), лишний —
отказ загрузки всего набора. Доказательство правила вида — строкой
комментария `# доказательство: …` прямо над его `- id:`.

> **Ловушка. Набор, который не загрузился, выглядит успехом.** Отказ
> загрузки `rules.yaml` убирает `dotnet.undecided` или `web.undecided`
> целиком, и `unexplained` падает (abp в прогоне S31: 2249 → 1342 при
> `defects: 1`). После правки набора смотреть `defects` и находку
> `load.errors`, а не только `unexplained`: `config check` набор правил
> не грузит.

## Форма вопроса

У агента контура есть инструмент вопроса пользователю со структурой
(как `ask_user_question` в qwen code). Вопрос собирается так:

| Поле | Что в нём | Откуда |
|---|---|---|
| заголовок | до 12 символов: о чём вопрос (`Область`, `Типы .NET`) | каталог, «Вопрос» |
| текст | находка с числом и примером (`файл:строка`, FQN), что агент проверил сам, затем «От ответа зависит, …» | ответ `setup status` и инструмента из «Материала» |
| варианты | 2–4; подпись — короткая, описание — **конкретная правка**: файл, ключ, значение | каталог, «Варианты» |
| свободный ответ | причина, если вариант её требует, или ответ «другое» | человек |

Образец — шаг 5 скилла `recon`: «Идентификаторы в `config/handlers.xml`
превращаются в классы через фабрику в `HandlerFactory.cs`. Это реестр
обработчиков событий или список настроек сборки? От ответа зависит, станут
ли они корнями графа».

**Ограничения инструмента на контуре не проверены** (S32): сколько
вариантов он принимает, есть ли свободный ответ, работает ли он внутри
скилла. Протокол от них не ломается:

1. **Заголовок — не адрес.** Если инструмент его обрежет или не примет,
   вопрос остаётся верным: адрес лежит в описании варианта.
2. **Вариантов меньше, чем нужно**, — вопрос делится на два. Две правки
   в один вариант не склеиваются: ответ должен однозначно указывать, что
   писать.
3. **Свободного ответа нет** — причина спрашивается вторым вопросом
   обычным текстом, и агент ждёт ответа. Подставить причину самому или
   взять подпись варианта вместо неё нельзя.
4. **Инструмента нет** (режим `-p`, сбой) — тот же вопрос обычным текстом:
   находка, «от ответа зависит», нумерованные варианты с правками, просьба
   назвать причину. Порядок и адреса те же.
5. **Ответ «не сейчас»** законен на любой вопрос: правки нет, находка
   остаётся в отчёте и будет в списке при остановке. Отдельным вариантом
   его не делают — варианты заняты правками.

Пример на `tests/fixtures/SampleSolution` (умолчания, находка
`dotnet.undecided` = 1):

```text
заголовок: Типы .NET
вопрос:    В модуле src/Sample.Pricing.Api/Sample.Pricing.Api.csproj один тип
           без решения: Sample.Pricing.Api.Program (Program.cs:8) — статический
           класс с ConfigureServices, регистрация DI хоста. Ни одно правило
           классификации его не берёт. От ответа зависит, будет ли у него
           свой документ.
варианты:  1. Не документируем — rules.yaml, dotnet.exclude.rules: id entry.program,
              when.name_regex ^Program$, reason — ваши слова (назовите причину)
           2. Как service — rules.yaml, dotnet.rules: kind service по имени Program
```

Ответ «1, точка входа: конфигурация хоста, а не контракт» даёт правило
отсева с этой причиной; `setup status` после него — без этой находки,
у правила охват 1. Без находок вовсе — только при явной области и владении:
на умолчаниях остаются `scope.not_configured` и `owners.not_configured`, и тест
задаёт `enrolled` и `ownership` явно
(`tests/test_setup_status.py::test_exclusion_with_reason_closes_the_finding_and_has_coverage`).

## Как читать каталог

Раздел `### <код>` — на каждый код `FINDING_CODES`
(`docpipe/setup/status.py`), в том же порядке. Первая строка — что
это и где решение (по `title` и `decision_home` кода). Дальше блоки:

- **Материал** — какие команды дают факты для вопроса. CLI-двойники
  инструментов сервера настройки (S27);
- **Без вопроса** — что агент делает сам, когда решение доказуемо кодом;
- **Вопрос** — заголовок и шаблон текста; `{…}` — подстановка из находки;
- **Варианты** — нумерованный список, пункт начинается подписью жирным
  (``1. **Не берём** — когда это так.``), под ним строки-адреса, хотя бы одна:
  - «правка» — вид файла и ключи через стрелку, каждый ключ в обратных
    кавычках: ``- правка: `docpipe.yaml` → `not_enrolled[].glob`, `not_enrolled[].reason` ``.
    Ключи — по карте `setup-map.md`; файл — вид файла настройки
    (`docpipe.yaml`, `rules.yaml`, `pages.yaml`, `ownership.yaml`,
    `registries.yaml`), а не имя: `rules.yaml` — тот, что назван в `rules`
    или `web.rules`, `pages.yaml` — в `web.pages`. Ключи `rules.yaml` записаны
    с секцией (`dotnet.exclude.rules[].when`), предикаты условий —
    `when.<предикат>`, общие для секций;
  - «команда» — действие командой, когда правка не в настройке:
    ``- команда: `docpipe docs adopt …` ``;
  - «вне настройки» — правка исходника или документа: её делает человек,
    агент файлов продукта не пишет;

  и две строки без адреса: «значение» — что вписать, «причина» — одно из
  трёх: `слова человека`, `доказательство агента`, `не нужна`;
- **Пробел формата** — ответ, которому негде лечь: агент говорит вслух,
  что находка останется, и ничего не придумывает в файле;
- **Починка** — у дефекта вместо вопроса: дефект решением «не беру» не
  закрывается (Р-6), его чинят;
- **Пример** — та же находка на фикстуре, с числами.

Правка записи «не берём» (`not_enrolled`, `exclude`, секция `link`,
`web.not_wrappers`, `dotnet.exclude`/`web.exclude`, `remove`) стоит только
в варианте вопроса, а не в «Без вопроса» и не в «Починке»; если вариант
вписывает в такую запись причину, причина — слова человека. Это держит тест.

## Каталог

### scope.not_configured

Область не задана: по умолчанию документируется всё · решение · где:
`enrolled` и `not_enrolled`, фронт — `web.roots` или `exclude`. Бывает, пока
`enrolled` или `web.roots` не записаны явно: умолчание (`["**"]`, `["."]`) —
не решение человека (Р-6). Явность — по самому ключу, а не по значению:
явный `["**"]` — тоже ответ.

**Материал.** `docpipe setup status` — срез `directory` (каталог над модулем
.NET, примеры — пути `.csproj`) и срез `front` (каталог файла объявления
фронта, пример — файл и имя модуля); `docpipe recon` — полные списки
`projects.dotnet_projects` и `projects.fronts` в `recon.json`.

**Без вопроса.** Нет: область задаёт человек (Р-6).

**Вопрос.** Заголовок «Область». «`enrolled` не задан — по умолчанию
документируется всё: {N} модулей .NET (`{каталог}` — {n}, …), модулей фронта
{M} (`{каталог}`). Какие каталоги — код вашей команды, какие — чужие, тесты,
образцы? От ответа зависит, получат ли их типы документы и будут ли о них
следующие вопросы».

**Варианты.** Человек отвечает по каждому каталогу; несколько вариантов —
несколько правок, по одной на вариант.

1. **Берём** — каталог — код команды.
   - правка: `docpipe.yaml` → `enrolled`, `enrolled[].glob`, `enrolled[].reason`
   - значение: `{каталог}/**`. Ответ «наш весь репозиторий» — `["**"]`, но тогда новый модуль входит в область молча: сказать это в описании варианта
   - причина: не нужна
2. **Не берём** — чужой код, тесты, образцы, инструменты.
   - правка: `docpipe.yaml` → `not_enrolled[].glob`, `not_enrolled[].reason`
   - правка: `docpipe.yaml` → `exclude[].glob`, `exclude[].reason`
   - значение: `{каталог}/**`; модули .NET — в `not_enrolled`, фронт — в `exclude` (`enrolled` на фронт не действует)
   - причина: слова человека
3. **Фронт команды** — фронт `{каталог}` документируется.
   - правка: `docpipe.yaml` → `web.roots`, `web.roots[].path`, `web.roots[].reason`
   - значение: каталог фронта; при нескольких фронтах — по записи на каждый фронт команды
   - причина: не нужна
4. **Фронта нет** — в области только .NET.
   - правка: `docpipe.yaml` → `web.roots`
   - значение: `[]` — шаг `web` не читает ничего. Фронт разведки после этого всплывает находкой `scope.front_undecided`: вопрос о нём — по её разделу
   - причина: не нужна

Умолчание `web.roots: ["."]` ответом «фронта нет» не является: фронт,
добавленный позже, войдёт в манифест молча. `roots` сужать законно и ради
времени разбора большого репозитория, но код вне `roots` не виден нигде,
кроме `setup explain`, и `out_of_scope` о нём молчит — цену называть вслух.

**Пример.** `tests/fixtures/SampleSolution` с умолчаниями: находка 2, кластер
`src` — два `.csproj`, среза `front` нет; с явным `enrolled` (и с `["**"]`)
находки нет
(`tests/test_setup_status.py::test_scope_not_configured_until_enrolled_is_explicit`).
Копия `tests/fixtures/SeamWorkspace` с явным `enrolled` и без `web.roots` —
срез `front`, кластер `frontend`; с `web.roots: []` находки нет, а `frontend`
— находка `scope.front_undecided`
(`tests/test_setup_status.py::test_scope_not_configured_front_slice`).

### scope.module_undecided

Модуль .NET без решения об области · решение · где: `enrolled` или
`not_enrolled`. Бывает только при явном `enrolled`: при умолчании `["**"]`
включено всё, и об умолчании говорит находка `scope.not_configured`.

**Материал.** `docpipe setup status` — кластер `directory` (каталог над
модулем), примеры — пути `.csproj`; `docpipe setup explain <каталог>` — что
о модулях уже решено и какой записью; `docpipe recon` — полный список
`projects.dotnet_projects` в `recon.json`.

**Без вопроса.** Нет: область задаёт человек.

**Вопрос.** Заголовок «Область». «В `{каталог}` {N} модулей .NET не попали
ни в `enrolled`, ни в `not_enrolled`: `{пример}`, …. От ответа зависит,
получат ли их типы документы и будут ли о них следующие вопросы».

**Варианты.**

1. **Берём** — модули каталога в области.
   - правка: `docpipe.yaml` → `enrolled`, `enrolled[].glob`, `enrolled[].reason`
   - значение: `{каталог}/**` рядом с прежними записями, а не вместо них
   - причина: не нужна
2. **Не берём** — код чужой команды, образцы, инструменты.
   - правка: `docpipe.yaml` → `not_enrolled[].glob`, `not_enrolled[].reason`
   - значение: `{каталог}/**`
   - причина: слова человека
3. **Часть** — человек называет, какие модули берёт.
   - правка: `docpipe.yaml` → `enrolled[].glob`, `not_enrolled[].glob`, `not_enrolled[].reason`
   - значение: названные модули — в `enrolled`, остальные — в `not_enrolled`
   - причина: слова человека

Шаблон `not_enrolled` не вырезает кусок из `enrolled`: `src/Samples/**` при
`enrolled: ["src/**"]` — отказ прогона (S22). Вырезать — значит сузить
`enrolled`.

**Пример.** `tests/fixtures/SampleSolution` с `enrolled: ["src/Sample.Common/**"]`:
находка 1, кластер `src` — `src/Sample.Pricing.Api/Sample.Pricing.Api.csproj`
(`tests/test_setup_status.py::test_module_undecided_only_with_explicit_enrolled`).

### scope.front_undecided

Фронт разведки вне `web.roots` и не под `exclude` · решение · где:
`web.roots` или `exclude`. При умолчании `web.roots: ["."]` не бывает:
умолчание — находка `scope.not_configured`.

**Материал.** `docpipe setup status` — кластер `front` (каталог фронта),
пример — файл объявления (`angular.json`, `project.json`); `docpipe recon` —
`projects.fronts` в `recon.json` (`kind`, `angular_core`, `proxy_configs`);
`docpipe setup explain <каталог фронта>`.

**Без вопроса.** Нет: область задаёт человек.

**Вопрос.** Заголовок «Фронт». «Фронт `{каталог}` ({kind}, `{config}`) вне
`web.roots` и не под `exclude`. От ответа зависит, разберёт ли его шаг `web`
и будут ли его страницы и вызовы в шве с бэком».

**Варианты.**

1. **Берём** — фронт команды.
   - правка: `docpipe.yaml` → `web.roots`, `web.roots[].path`, `web.roots[].reason`
   - значение: каталог фронта рядом с прежними корнями
   - причина: не нужна
2. **Не берём** — старый, чужой или учебный фронт.
   - правка: `docpipe.yaml` → `exclude[].glob`, `exclude[].reason`
   - значение: `{каталог}/**` — с `/**`: шаблон без него совпадает с каталогом, а не с файлами
   - причина: слова человека

Загрузчик причину у `exclude` не требует (короткая форма законна), протокол —
требует: это «не берём».

**Пример.** Два фронта `app` и `legacy` в `tmp_path`, `web.roots: [app]`:
находка 1, кластер `legacy` — `legacy/angular.json`; после
`exclude: [{glob: legacy/**, reason: старый фронт}]` находки нет,
`out_of_scope.fronts` = 1
(`tests/test_setup_status.py::test_front_outside_web_roots_is_undecided_until_excluded`).

### dotnet.undecided

Символ .NET без решения · решение · где: `rules.yaml`, секция `dotnet` —
правило классификации или отсев с причиной.

**Материал.** `docpipe setup status` — кластеры `module` и `last_word`;
`docpipe symbols --root … --state undecided --module … --format json` —
наследование, атрибуты, члены, путь каждого символа группы;
`docpipe scan --stats` — срез «последнее слово»; `docpipe setup explain <путь>`
— что о группе уже решено.

**Без вопроса.** Признак вида доказуем кодом — базовый тип, атрибут, суффикс
вместе с наследованием, — и вид уже объявлен: агент пишет правило, зовёт
`setup status` и показывает, что `dotnet.undecided` уменьшился на размер
кластера, а другие находки не выросли.

- правка: `rules.yaml` → `dotnet.rules[].id`, `dotnet.rules[].kind`, `dotnet.rules[].priority`, `dotnet.rules[].when`, `when.base_type`, `when.inherits`, `when.attribute`, `when.name_suffix`

**Вопрос.** Заголовок «Типы .NET». «{N} типов без решения в `{модуль}`
(или со словом `{слово}`): `{FQN}`, …. Общее у них: {признак по `symbols`};
правила вида их не берут, потому что {…}. От ответа зависит, получит ли
каждый из них свой документ».

**Варианты.**

1. **Как {вид}** — документировать видом, который агент предложил по коду.
   - правка: `rules.yaml` → `dotnet.rules[].id`, `dotnet.rules[].kind`, `dotnet.rules[].when`
   - значение: вид из объявленных в `rules.yaml`; условие — по признаку группы, не перечнем FQN
   - причина: не нужна
2. **Не документируем** — миграции, DTO, сгенерированное, хост.
   - правка: `rules.yaml` → `dotnet.exclude.rules[].id`, `dotnet.exclude.rules[].when`, `dotnet.exclude.rules[].reason`, `when.path_glob`, `when.name_regex`, `when.namespace_regex`
   - значение: условие по кластеру (`path_glob` каталога, `name_regex` суффикса)
   - причина: слова человека
3. **Не документируем, кроме** — группа не нужна, а часть её нужна.
   - правка: `rules.yaml` → `dotnet.exclude.rules[].when`, `dotnet.exclude.rules[].unless`, `dotnet.exclude.rules[].reason`
   - значение: исключение по признаку (атрибут, базовый тип), который назвал человек
   - причина: слова человека
4. **Новый вид** — у группы свой вид документа.
   - правка: `rules.yaml` → `dotnet.rules[].kind`, `dotnet.rules[].template`, `dotnet.rules[].when`
   - значение: имя вида называет человек — оно становится сегментом `doc_path`; скелет `templates/<вид>.md`
   - причина: не нужна

Гранулярность решения — группа: перечень FQN сломается на первом
переименовании. Отсев отменяет классификацию при любом приоритете
(`stats.decide`: сначала отсев, потом правила вида): `priority`, затем `id`,
решают только, чья причина попадёт в отчёт среди правил отсева и какой вид
выиграет среди правил вида. Нужна часть группы — вырез `unless` у отсева,
а не правило вида с бо́льшим приоритетом; после правки — `setup explain`
группы, кто победил.

**Пример.** `tests/fixtures/SampleSolution` с умолчаниями: находка 1,
`Sample.Pricing.Api.Program`, кластер `module` —
`src/Sample.Pricing.Api/Sample.Pricing.Api.csproj`; правило отсева
`entry.program` с причиной «точка входа: конфигурация хоста, а не контракт»
закрывает её, охват правила 1
(`tests/test_setup_status.py::test_exclusion_with_reason_closes_the_finding_and_has_coverage`).

### web.undecided

Символ фронта без решения · решение · где: `rules.yaml`, секция `web` —
правило классификации или отсев с причиной.

**Материал.** `docpipe setup status` — кластеры `module` и `last_word`;
`docpipe symbols --root … --lang ts --state undecided --format json`;
`docpipe web scan --stats`; `docpipe web pages` — страница ли класс и почему.

**Без вопроса.** Как у `dotnet.undecided`: декоратор (`when.attribute`),
базовый класс или интерфейс, суффикс — правило вида из объявленных.

- правка: `rules.yaml` → `web.rules[].id`, `web.rules[].kind`, `web.rules[].priority`, `web.rules[].when`, `when.attribute`, `when.inherits`, `when.name_suffix`

**Вопрос.** Заголовок «Типы фронта». «{N} символов фронта без решения
в `{модуль}`: `{FQN}`, …; общее — {признак}. От ответа зависит, получит ли
каждый свой документ или войдёт в документ страницы».

**Варианты.**

1. **Как {вид}** — вид, предложенный агентом по коду.
   - правка: `rules.yaml` → `web.rules[].id`, `web.rules[].kind`, `web.rules[].when`
   - значение: вид из объявленных; условие — по признаку группы
   - причина: не нужна
2. **Не документируем** — инфраструктура, утилиты, модели транспорта.
   - правка: `rules.yaml` → `web.exclude.rules[].id`, `web.exclude.rules[].when`, `web.exclude.rules[].reason`, `when.path_glob`, `when.name_regex`
   - значение: условие по кластеру
   - причина: слова человека
3. **Не документируем, кроме** — часть группы нужна.
   - правка: `rules.yaml` → `web.exclude.rules[].when`, `web.exclude.rules[].unless`, `web.exclude.rules[].reason`
   - значение: исключение по признаку, который назвал человек
   - причина: слова человека
4. **Новый вид** — у группы свой вид документа.
   - правка: `rules.yaml` → `web.rules[].kind`, `web.rules[].template`, `web.rules[].when`
   - значение: имя вида называет человек; скелет `templates/<вид>.md`
   - причина: не нужна

`web.exclude.require_public: true` отсеивает весь фронт (у TypeScript нет
`public` на уровне объявления): если `web.undecided` вдруг стал нулём вместе
со всеми документами фронта — смотреть туда.

**Пример.** `tests/fixtures/SeamWorkspace`: находка 6, кластер `module`
`frontend/src`, `last_word` `Versioned` — 5 (`http-extensions.getVersioned`
и соседи), `Query` — 1 (`query.buildQuery`): функции HTTP-каркаса, вопрос
«не документируем ли».

### link.calls_unresolved

Вызов фронта, адрес не восстановлен · решение · где: `web.http_wrappers`,
`web.url_builders` или `link.unresolvable`.

**Материал.** `docpipe setup status` — кластер `reason`;
`docpipe setup link --category calls_unresolved --by reason` (и `--by file`);
`docpipe setup candidates http-wrappers`, `docpipe setup candidates url-builders`;
`docpipe setup explain <файл>` — `unresolved_reasons`.

**Без вопроса.** Причина «значение переменной — параметр функции» внутри
тела метода, который зовут с адресом, — обёртка; «значение переменной —
вызов `X.y(…)`» — построитель. Агент объявляет их по `setup candidates`
(позиция адреса — из `positions`) и показывает: `configured: true`,
находка меньше, вызовы появились в связи.

- правка: `docpipe.yaml` → `web.http_wrappers[].receiver`, `web.http_wrappers[].method`, `web.http_wrappers[].method_regex`, `web.http_wrappers[].url.arg`, `web.http_wrappers[].url.field`, `web.http_wrappers[].http_method.arg`, `web.http_wrappers[].http_method.from_name`, `web.http_wrappers[].reason`
- правка: `docpipe.yaml` → `web.url_builders[].receiver`, `web.url_builders[].method`, `web.url_builders[].path.arg`, `web.url_builders[].reason`

**Вопрос.** Заголовок «Адрес». «{N} вызовов фронта, адрес которых статически
не восстановлен: «{причина}», `{файл:строка  GET выражение}`, …. Обёртки или
построителя, который бы его дал, в коде я не нашёл: {что смотрел}. От ответа
зависит, войдут ли эти вызовы в связь с бэком».

**Варианты.**

1. **Не восстановить** — адрес из данных: гипермедиа, `@Input`, сборка окружения.
   - правка: `docpipe.yaml` → `link.unresolvable[].path`, `link.unresolvable[].reason`
   - значение: глоб **файла**, не каталога — глоб накрывает и будущие вызовы файла
   - причина: слова человека
2. **Есть обёртка** — человек знает обёртку, которую отбор не показал.
   - правка: `docpipe.yaml` → `web.http_wrappers[].receiver`, `web.http_wrappers[].method`, `web.http_wrappers[].url.arg`
   - значение: агент находит объявление, сверяет позицию адреса и пишет запись
   - причина: доказательство агента
3. **Есть построитель** — адрес собирает вызов, который человек назвал.
   - правка: `docpipe.yaml` → `web.url_builders[].receiver`, `web.url_builders[].method`, `web.url_builders[].path.arg`
   - значение: как у варианта 2
   - причина: доказательство агента

**Пример.** `tests/fixtures/SeamWorkspace`: находка 9 — «параметр функции» 4
(`http-extensions.ts:16  GET url`, тело обёртки), «вызов `apiUrl.buildUrl(…)`» 2
(`apps.service.ts:28`), «база в начале конкатенации не восстановлена» 1
(`apps.service.ts:52`). Правила S19 (`S19_WEB` в `tests/test_setup_link.py`)
и `link.unresolvable` для `links.service.ts`, `editor.component.ts`,
`apps.service.ts` закрывают её
(`tests/test_setup_status.py::test_seam_rules_close_the_seam_findings`).

### link.calls_invisible

Вызов через необъявленную обёртку: прогон его не видит · решение · где:
`web.http_wrappers` (обёртка) или `web.not_wrappers` (не обёртка).

**Материал.** `docpipe setup status` — кластер `wrapper` (`получатель.метод`,
число — вызовов); `docpipe setup candidates http-wrappers` — `positions`,
`files`, `examples`, `configured`; объявление метода — глазами.

**Без вопроса.** В теле метода — вызов `HttpClient` (он же в
`unresolved_calls` с причиной «параметр функции»): агент объявляет обёртку
с позицией адреса из `positions` и показывает, что группа стала
`configured: true`, а её вызовы появились в `web_calls` и в связи.

- правка: `docpipe.yaml` → `web.http_wrappers[].receiver`, `web.http_wrappers[].method`, `web.http_wrappers[].method_regex`, `web.http_wrappers[].url.arg`, `web.http_wrappers[].url.field`, `web.http_wrappers[].http_method.arg`, `web.http_wrappers[].http_method.field`, `web.http_wrappers[].http_method.from_name`, `web.http_wrappers[].http_method.fixed`, `web.http_wrappers[].reason`

**Вопрос.** Заголовок «Обёртка?». «`{получатель.метод}` зовут {N} раз
с аргументом, похожим на адрес: `{файл:строка}`, …. Объявление —
`{файл:строка}`; HTTP в его теле я {вижу через … | не вижу}. От ответа
зависит, будут ли эти вызовы обращениями к бэку в связи и в документах».

**Варианты.**

1. **Обёртка HTTP** — метод отправляет запрос.
   - правка: `docpipe.yaml` → `web.http_wrappers[].receiver`, `web.http_wrappers[].method`, `web.http_wrappers[].url.arg`, `web.http_wrappers[].http_method.arg`
   - значение: позиция адреса — из `positions` (`1` у `getVersioned(this.http, url)`: первым идёт сам `HttpClient`)
   - причина: доказательство агента
2. **Не обёртка** — `window.open`, `url.startsWith`, `req.clone`, `form.patchValue`.
   - правка: `docpipe.yaml` → `web.not_wrappers[].receiver`, `web.not_wrappers[].method`, `web.not_wrappers[].reason`
   - значение: группа `получатель.метод`, как у кандидата
   - причина: слова человека
3. **Тесты** — группа живёт в спеках (`httpMock.expectOne` в `*.spec.ts`).
   - правка: `docpipe.yaml` → `exclude[].glob`, `exclude[].reason`
   - значение: `<фронт>/**/*.spec.ts`. Цена: правило `web.spec` набора остаётся без охвата — обход читает спеки ради наследования тестов; сказать это в описании варианта
   - причина: слова человека

**Запись «не обёртка»** (`web.not_wrappers`, S24b) — решение человека
с его причиной: кандидат уходит и из этой находки, и из `setup candidates
http-wrappers` (счётчик `declared_not_wrappers`). Обёртка и «не обёртка»
на одну пару «получатель + метод» — отказ загрузки; агент не объявляет
обёрткой то, что HTTP не делает, и не пишет «не обёртку» без слов человека.

Обёртку по имени не распознать: `uiState.get('/settings/x')` у squidex —
не HTTP. Тесты первыми по числу (`httpMock.expectOne` в `*.spec.ts`,
на squidex 188 вызовов) — вариант 3, а без него — `web.not_wrappers` на каждую
пару «получатель.метод» теста (вариант 2). `this.http.request(method, url)` —
обёртка с получателем `HttpClient`, а не «не обёртка»: обход `request`
не понимает намеренно (первый аргумент — метод), и запись
`web.http_wrappers` с `url.arg` 1 и `http_method.arg` 0 закрывает группу.

**Пример.** `tests/fixtures/SeamWorkspace`: находка 3 — `HTTP.getVersioned`
(`apps-versioned.service.ts:17`), `HTTP.requestVersioned` (`:22`),
`rest.request` (`apps-proxy.service.ts:14`), все три — обёртки, закрываются
`S19_WEB` (`tests/test_setup_link.py`). `tests/fixtures/WebWorkspace`:
находка 1 — `req.clone` (`src/app/cf-api/interceptors/fix-url.interceptor.ts:25`),
интерцептор клонирует запрос с новым `url`: не обёртка, вариант 2.

### link.calls_without_endpoint

Вызов фронта без эндпоинта · решение · где: `web.url_rewrite` или
`link.external_targets`. Считается, только когда в области есть обе стороны шва.

**Материал.** `docpipe setup status` — кластер `module`;
`docpipe setup link` (по умолчанию эта категория; `--by module` —
подсказка `suggested_rewrite`, `--by host`, `--by prefix`); прокси и
окружение — `projects.fronts[].proxy_configs` в `recon.json`,
`environment.ts`, интерцептор.

**Без вопроса.** Подсказка `suggested_rewrite` с `would_link > 0`,
`would_unlink = 0`, и тот же срез префикса виден в прокси или интерцепторе
(`файл:строка`): агент пишет запись, зовёт `setup link` снова и показывает,
что `linked` вырос на `would_link`. После неё —
`setup link --category external_targets`: `url_rewrite` переписывает
и абсолютные адреса, и маска `route`, записанная раньше, перестаёт совпадать.

- правка: `docpipe.yaml` → `web.url_rewrite[].module`, `web.url_rewrite[].strip_prefix`, `web.url_rewrite[].add_prefix`, `web.url_rewrite[].reason`

**Вопрос.** Заголовок «Без бэка». «{N} вызовов `{модуль}` не нашли
эндпоинта: `{GET маршрут}` (`{файл:строка}`), …; хост — `{хост | относительный
адрес}`. Подсказки префикса нет: {`rewrite_note`}. От ответа зависит,
документируется ли вызов как внешняя зависимость или это ошибка в коде».

**Варианты.**

1. **Внешний хост** — адрес к соседней системе.
   - правка: `docpipe.yaml` → `link.external_targets[].host`, `link.external_targets[].document`, `link.external_targets[].reason`
   - значение: хост без схемы и порта (`ext.example.org`); `document` — ответ «документируем как внешнюю зависимость» (умолчание `false`)
   - причина: слова человека
2. **Внешний путь** — относительный адрес обслуживает не наш бэк.
   - правка: `docpipe.yaml` → `link.external_targets[].route`, `link.external_targets[].document`, `link.external_targets[].reason`
   - значение: маска маршрута (`api/apps/search*`)
   - причина: слова человека
3. **Бэк вне корней** — эндпоинт есть, но его код вне `roots`.
   - правка: `docpipe.yaml` → `roots`
   - значение: каталог бэка рядом с прежними корнями
   - причина: не нужна
4. **Ошибка в коде** — опечатка маршрута, удалённый эндпоинт.
   - вне настройки: правка исходника командой продукта; находка остаётся до неё
   - причина: не нужна

**Пример.** `tests/fixtures/SeamWorkspace`: находка 3, модуль `seam-web` —
`GET api/apps/archived` (`apps.service.ts:42`), `GET api/apps/search`
(`apps.service.ts:47`), `GET feed.json` с хостом `ext.example.org`
(`feed.service.ts:13`); `LINK_SECTION` в `tests/test_setup_status.py`
закрывает все три.

### link.endpoints_without_caller

Эндпоинт без вызывающего · решение · где: `link.external_callers`.
Считается, только когда в области есть обе стороны шва.

**Материал.** `docpipe setup status` — кластер `controller`;
`docpipe setup link --category endpoints_without_caller --by controller`
(и `--by prefix`); `docpipe setup explain <файл контроллера>`.

**Без вопроса.** Нет: «зовут извне» кодом репозитория не доказать. Но
спрашивать — последним в фазе шва: эндпоинт бывает «без вызывающего»
потому, что вызов фронта невидим или не восстановлен. Сначала закрываются
`link.calls_invisible` и `link.calls_unresolved`.

**Вопрос.** Заголовок «Кто зовёт». «{N} эндпоинтов `{контроллер}` фронт
не зовёт: `{GET маршрут (действие)}` (`{файл:строка}`), …. Невидимых
и невосстановленных вызовов фронта осталось {K}. От ответа зависит,
документируется ли эндпоинт как точка входа извне».

**Варианты.**

1. **Зовут извне** — SDK, мобильный клиент, интеграция.
   - правка: `docpipe.yaml` → `link.external_callers[].route`, `link.external_callers[].http_method`, `link.external_callers[].reason`
   - значение: маска маршрута (`content/**`); `document: true` — умолчание
   - причина: слова человека
2. **Не документируем** — служебный или мёртвый эндпоинт.
   - правка: `docpipe.yaml` → `link.external_callers[].route`, `link.external_callers[].document`, `link.external_callers[].reason`
   - значение: `document: false`
   - причина: слова человека
3. **Фронт зовёт** — вызов есть, прогон его не видит.
   - команда: `docpipe setup candidates http-wrappers`
   - правка: `docpipe.yaml` → `web.http_wrappers[].receiver`, `web.http_wrappers[].method`
   - значение: человек показывает место вызова, агент объявляет обёртку
   - причина: доказательство агента

**Пример.** `tests/fixtures/SeamWorkspace`: находка 13 — `AppsController` 4
(`GET api/apps (GetApps)`, `AppsController.cs:11`), `ContentController` 3,
`OrdersController` 3; `external_callers: [{route: "content/**", reason: …}]`
снимает `ContentController` (`tests/test_link_decisions.py`).

### link.almost

Связь «почти»: ключи различаются только числом `{}` · решение · где:
`web.url_rewrite`. Связь в отчёте уже есть (`match: almost`); вопрос — верна ли она.

**Материал.** `docpipe setup status` — кластер `controller`;
`docpipe setup link --category almost --by controller` (и `--by prefix`);
обе стороны глазами: как фронт передаёт параметр, как его ждёт действие.

**Без вопроса.** Разница — в голове адреса, которую срезает или добавляет
прокси или интерцептор (сегмент тенанта, версии), и это видно в
`proxy.conf`: агент пишет запись модуля и показывает, что связь стала точной.

- правка: `docpipe.yaml` → `web.url_rewrite[].module`, `web.url_rewrite[].strip_prefix`, `web.url_rewrite[].add_prefix`, `web.url_rewrite[].reason`

**Вопрос.** Заголовок «Почти». «Вызов `{GET маршрут вызова}` (`{файл:строка}`)
сведён с `{GET маршрут эндпоинта}` ({контроллер}) «почти»: различается только
число параметров. От ответа зависит, документируется ли эта связь или
вызов на самом деле без эндпоинта».

**Варианты.**

1. **Префикс** — сегмент добавляет или срезает прокси.
   - правка: `docpipe.yaml` → `web.url_rewrite[].module`, `web.url_rewrite[].strip_prefix`, `web.url_rewrite[].add_prefix`, `web.url_rewrite[].reason`
   - значение: правило модуля по конфигурации, которую человек назвал боевой
   - причина: слова человека
2. **Ошибка в коде** — фронт зовёт не тот маршрут.
   - вне настройки: правка исходника; находка остаётся до неё
   - причина: не нужна

**Пробел формата.** Самый частый ответ — «та же точка: параметр передан
иначе (query вместо сегмента)» — записи не имеет: подтвердить «почти» нечем,
а `link.external_targets` пару не забирает (S20: «почти» — тоже пара).
Находка остаётся; агент говорит это вслух.

**Пример.** `tests/fixtures/WildSolution` (бэк) и `tests/fixtures/WebWorkspace`
(фронт): вызов `api/ml/structure/getforupdate/{}` сведён с `…/getforupdate`
(`tests/test_web_link.py::test_almost_match_catches_the_named_pair`).

### link.module_without_rewrite

Модуль фронта с вызовами без записи `web.url_rewrite` · решение · где:
`web.url_rewrite`, пустая запись — «проверено, преобразования нет».

**Материал.** `docpipe setup status` — кластер `module` (число — мест вызова);
`docpipe setup link --by module` — `suggested_rewrite` и `rewrite_note`;
`projects.fronts[].proxy_configs` и `proxy_files` в `recon.json`.

**Без вопроса.** Подсказка есть — запись с преобразованием (как у
`link.calls_without_endpoint`). Подсказки нет с «ни одна пара не даёт
выигрыша», а прокси модуля без `pathRewrite` — пустая запись с адресом
прокси в `reason`.

- правка: `docpipe.yaml` → `web.url_rewrite[].module`, `web.url_rewrite[].strip_prefix`, `web.url_rewrite[].add_prefix`, `web.url_rewrite[].reason`

**Вопрос.** Заголовок «Прокси». «Модуль `{модуль}` делает {N} вызовов,
записи `web.url_rewrite` нет. Прокси: `{proxy_configs}` — {не читается |
у конфигураций разный `pathRewrite` | не объявлен}. От ответа зависит,
с какими маршрутами бэка сопоставятся его вызовы».

**Варианты.**

1. **Как есть** — в бою адрес доходит до бэка без изменений.
   - правка: `docpipe.yaml` → `web.url_rewrite[].module`, `web.url_rewrite[].reason`
   - значение: запись с пустыми `strip_prefix` и `add_prefix`
   - причина: слова человека
2. **Меняет префикс** — человек называет боевую конфигурацию.
   - правка: `docpipe.yaml` → `web.url_rewrite[].module`, `web.url_rewrite[].strip_prefix`, `web.url_rewrite[].add_prefix`, `web.url_rewrite[].reason`
   - значение: префиксы из этой конфигурации
   - причина: слова человека

Имя модуля с опечаткой ни на что не ложится и протухшим не называется:
после записи — `setup status`, находка обязана уйти.

**Пример.** `tests/fixtures/SeamWorkspace`: находка 1, модуль `seam-web`,
13 мест; подсказка S21 — «префикс не нужен», пустая запись `REWRITE`
снимает находку
(`tests/test_setup_status.py::test_seam_rules_close_the_seam_findings`).

### link.registry_unresolved

Обращение к реестру без различителя · решение · где: `web.registry_calls`.

**Материал.** `docpipe setup status` — кластер `route`;
`docpipe setup candidates registry-calls`; `docpipe setup explain <файл>` —
`calls.registry_unresolved`; место вызова глазами: откуда берётся имя списка.

**Без вопроса.** Различитель лежит не там, где сказано в записи (в теле,
а не в query; другое имя поля), и это видно в коде: агент правит запись
и показывает, что число упало.

- правка: `docpipe.yaml` → `web.registry_calls[].discriminator.in`, `web.registry_calls[].discriminator.name`

**Вопрос.** Заголовок «Реестр». «{N} обращений к `{маршрут}` без
различителя: `{файл:строка}` — имя списка собрано из `{выражение}`. От
ответа зависит, к какому смыслу реестра отнесётся вызов в связи».

**Варианты.**

1. **Другое поле** — человек знает, где имя списка.
   - правка: `docpipe.yaml` → `web.registry_calls[].discriminator.in`, `web.registry_calls[].discriminator.name`
   - значение: место и имя поля, которые назвал человек
   - причина: не нужна
2. **Не реестр** — маршрут не универсальный, различителя у него нет.
   - правка: `docpipe.yaml` → `web.registry_calls[].route`
   - значение: запись маршрута удаляется
   - причина: не нужна

**Пробел формата.** Различитель из данных (`byType(type)` —
`listInnerName=${type}`) записи не имеет: «различитель статически не
восстановить» сказать нечем, а `link.unresolvable` — про невосстановленный
адрес, у этого вызова адрес есть. Находка остаётся; агент говорит это вслух.

**Пример.** `tests/fixtures/WebWorkspace` с `web.registry_calls:
[{route: api/items, discriminator: {in: query, name: listInnerName}}]`:
находка 1 — `src/app/shared/services/items.service.ts:37  GET api/items`,
различитель — подстановка, пробел формата
(`tests/test_web_calls.py::test_registry_call_without_a_literal_discriminator_is_named`).

### pages.route_unresolved

Страница: маршрут части записей не собран · решение · где: `pages.yaml`,
`add` с маршрутом.

**Материал.** `docpipe web pages` — записи маршрута с `source` и `table`;
`docpipe setup status` — кластер `module`, пример `ListComponent: /models,
?/models` (с `?` — не собрана); таблица роутов глазами.

**Без вопроса.** Нет: маршрут, которого разбор не собрал, — утверждение
о приложении, его подтверждает человек.

**Вопрос.** Заголовок «Маршрут». «Страница `{компонент}` объявлена на {N}
маршрутах, {K} из них не собраны: `{файл:строка}` — сегмент из выражения
`{…}`. От ответа зависит, будет ли у страницы якорь на этом пути».

**Варианты.**

1. **Маршрут** — агент предлагает путь по коду.
   - правка: `pages.yaml` → `add[].route`, `add[].component`, `add[].reason`
   - значение: маршрут из записи роута; `component` — FQN из `web pages`
   - причина: доказательство агента
2. **Другой путь** — человек называет боевой маршрут.
   - правка: `pages.yaml` → `add[].route`, `add[].component`, `add[].reason`
   - значение: маршрут, который назвал человек
   - причина: слова человека

**Пробел формата.** Ответ «вторая запись — та же страница (архив, псевдоним),
отдельного якоря не нужно» записи не имеет: `add` уже собранного маршрута —
протухшее `add-redundant`. Находка остаётся.

**Пример.** `tests/fixtures/WebWorkspace`: находка 1, модуль `tr-p` —
`ListComponent: /models, ?/models`; второй путь собран выражением
`archiveSegment` (`tests/fixtures/WebWorkspace/src/app/routesPath/models.ts`).

### pages.unanchorable

Страница без собранного маршрута: якорь не поставить · решение · где:
`pages.yaml`, `add` с маршрутом или `remove`.

**Материал.** `docpipe web pages` — заметка, все записи `route_unresolved`;
`docpipe setup status` — кластер `module`; `docpipe setup explain <файл>` —
`page_overrides`.

**Без вопроса.** Нет: маршрут подтверждает человек.

**Вопрос.** Заголовок «Якорь». «`{компонент}` — страница по таблице роутов,
но ни один её маршрут не собран: `{файл:строка}`. От ответа зависит,
будет ли у неё документ страницы с якорем».

**Варианты.**

1. **Маршрут** — человек или код называют путь.
   - правка: `pages.yaml` → `add[].route`, `add[].component`, `add[].reason`
   - значение: маршрут; `component` — FQN из `web pages`
   - причина: доказательство агента
2. **Не страница** — компонент не экран.
   - правка: `pages.yaml` → `remove[].component`, `remove[].reason`
   - значение: FQN компонента
   - причина: слова человека

**Пример.** `ListComponent` из `tests/fixtures/WebWorkspace`, у которого
оставлены только несобранные записи
(`tests/test_web_pages.py::test_page_whose_routes_are_all_unresolved_is_named`).

### pages.layout

Страница похожа на layout: признаков функционала нет · решение · где:
`pages.yaml`, `remove`.

**Материал.** `docpipe web pages` — заметки «нет признаков функционала»
и «пустой маршрут», члены; шаблон компонента (`<router-outlet>`) глазами;
`docpipe setup status` — кластер `module`.

**Без вопроса.** Нет: снять страницу — «не документируем как страницу».

**Вопрос.** Заголовок «Layout?». «`{компонент}` на `{маршрут}` — страница
без признаков функционала: {нет членов | нет вызовов и стейта}. От ответа
зависит, будет ли у неё документ страницы».

**Варианты.**

1. **Layout везде** — каркас на любом маршруте.
   - правка: `pages.yaml` → `remove[].component`, `remove[].reason`
   - значение: FQN компонента
   - причина: слова человека
2. **Layout здесь** — на этом маршруте каркас, на других — экран.
   - правка: `pages.yaml` → `remove[].route`, `remove[].reason`
   - значение: маршрут
   - причина: слова человека

**Пробел формата.** «Это страница» записи не имеет: `add` того же маршрута —
протухшее `add-redundant` (S24). Поэтому находка сужена до страниц без
функционала, а у такой страницы, которая всё-таки страница, находка остаётся.

**Пример.** `tests/fixtures/WebWorkspace`: находка 2, модуль `tr-p` —
`ShellComponent: /` (пустой маршрут, членов нет),
`ForecastComponent: /forecast/daily`
(`tests/test_setup_status.py::test_pages_notes_become_findings`).

### pages.stale_overrides

Правило `pages.yaml` ни на что не легло · решение · где: `pages.yaml`,
правка или удаление правила.

**Материал.** `docpipe setup status` — кластер `kind` (`add-missed`,
`add-redundant`, `remove-missed`, `feature-empty`, …); `docpipe web scan`
— отчёт о протухших правилах; `docpipe web pages`.

**Без вопроса.** Добавление сломал перенос файла — FQN сменился, класс тот
же, и это видно по `git log --follow`: агент переносит FQN. `add-redundant` —
маршрут теперь собирается сам: агент удаляет запись и показывает, что
страница осталась на месте. Снятие (`remove`) агент сам не переносит:
перенесённое на другой класс, оно молча применило бы чужое «не документируем»
к коду, о котором человек не решал.

- правка: `pages.yaml` → `add[].component`, `add[].route`, `features[].path`

**Вопрос.** Заголовок «pages.yaml». «Правило `{вид}` `{описание}` ни на что
не легло: {компонента нет | маршрут находится сам | в разделе нет узлов}.
Похожее в коде — {…}. От ответа зависит, останется ли прежнее решение
в силе».

**Варианты.**

1. **Перенести** — код переехал, решение в силе.
   - правка: `pages.yaml` → `add[].component`, `remove[].component`, `features[].path`
   - значение: новое место; `reason` записи — прежний, его не переписывают
   - причина: не нужна
2. **Удалить** — кода больше нет или правило не нужно.
   - правка: `pages.yaml` → `add[].route`, `remove[].route`, `features[].name`
   - значение: запись удаляется
   - причина: не нужна

**Пример.** `tests/fixtures/WebWorkspace` с `add` для `src/app/gone.Missing` —
`add-missed`, с `add` для `/models` — `add-redundant`
(`tests/test_web_overrides.py::test_add_for_an_unknown_component_is_a_finding`).

### docs.orphan

Документ без узла · решение · где: `docpipe docs adopt` или удаление файла.
Сирота **обоих** планов шага 2 — дерево документов у .NET и фронта общее.

**Материал.** `docpipe setup status` — кластер `directory`;
`docpipe docs status МАНИФЕСТ --root …` и `docpipe docs explain МАНИФЕСТ ПУТЬ`
(манифест — `docpipe scan --out`); `node_id` во front matter документа;
`git log` документа и кода.

**Без вопроса.** Нет: документ — написанный текст, и что с ним делать,
решает человек.

**Вопрос.** Заголовок «Сирота». «Документ `{путь}` не принадлежит ни одному
узлу (`node_id` `{id}`). {Похожий узел без документа — `{узел}` |
похожего узла нет}. От ответа зависит, сохранится ли написанный текст».

**Варианты.**

1. **Перенести** — код переименован, текст о нём.
   - команда: `docpipe docs adopt МАНИФЕСТ --from ПУТЬ --to ПУТЬ`
   - значение: сначала с `--dry-run`
   - причина: не нужна
2. **Удалить** — кода больше нет.
   - вне настройки: удалить файл коммитом человека
   - причина: не нужна
3. **Вернуть имя** — переименован раздел, и документ осиротел.
   - правка: `pages.yaml` → `features[].name`
   - значение: прежнее имя: оно входит в `node_id` раздела
   - причина: не нужна
4. **Вернуть раскладку** — сменили префикс или раскладку `doc_path`.
   - правка: `docpipe.yaml` → `docs_root`, `modules_dir`, `doc_layout`
   - значение: прежние значения, затем `scan` заново
   - причина: не нужна

**Пример.** Копия `tests/fixtures/SampleSolution` с `docs/modules/orphan.md`
(``node_id: type:nowhere#Gone`0``): находка 1
(`tests/test_setup_status.py::test_documents_orphan_broken_and_shadowed`).

### owners.unowned

Документ без владельца · решение · где: `ownership.yaml`, правило.

**Материал.** `docpipe setup status` — кластер `module`;
`docpipe docs owners МАНИФЕСТ --lint --format json` (`dead-rule`,
`priority-tie`); `docpipe docs owners МАНИФЕСТ --explain ПУТЬ`.

**Без вопроса.** Человек уже назвал команду модуля (прошлый ответ): агент
пишет правило по модулю или пути и показывает, что находка меньше,
а `--lint` без новых `dead-rule`.

- правка: `ownership.yaml` → `rules[].id`, `rules[].team`, `rules[].priority`, `rules[].when`, `rules[].when.module_glob`, `rules[].when.path_glob`

**Вопрос.** Заголовок «Владелец». «{N} документов модуля `{модуль}` без
владельца: `{путь}`, …. Команды в `ownership.yaml`: `{id}`, …. От ответа
зависит, кому придут эти документы в очереди `worklist --team`».

**Варианты.**

1. **Команда {id}** — модуль у объявленной команды.
   - правка: `ownership.yaml` → `rules[].team`, `rules[].when`, `rules[].when.module_glob`
   - значение: `id` команды из ответа
   - причина: не нужна
2. **Новая команда** — человек называет команду.
   - правка: `ownership.yaml` → `teams[].id`, `teams[].title`, `rules[].team`, `rules[].when`
   - значение: `id` — то, что пишут в `--team`, называет человек
   - причина: не нужна

**Пример.** `tests/fixtures/SampleSolution` с одним правилом на
`src/Sample.Pricing.Api/Services/**`: находка 5
(`tests/test_setup_status.py::test_unowned_documents_are_named_by_module`).

### owners.not_configured

Владение не настроено: владельцев не назначает никто · решение · где:
`ownership` в `docpipe.yaml` и файл правил владения. Число — узлов с документом.

**Материал.** `docpipe setup status` — кластер `module`; блок 2 разведки
(`docpipe recon`, каталоги одного автора) — подсказка, а не ответ;
[`ownership.md`](ownership.md) — слои 10 → `--lint` → 50 → 100.

**Без вопроса.** Нет: команды и их имена — знание человека.

**Вопрос.** Заголовок «Владение». «Владение не настроено: {N} документов
без команды — `{модуль}` ({n}), …. От ответа зависит, разделится ли
очередь документов по командам».

**Варианты.**

1. **По модулям** — у модулей разные команды.
   - правка: `docpipe.yaml` → `ownership`
   - правка: `ownership.yaml` → `teams[].id`, `teams[].title`, `rules[].id`, `rules[].team`, `rules[].when.module`
   - значение: команды и модули — из ответа
   - причина: не нужна
2. **Одна команда** — всё у одной команды.
   - правка: `docpipe.yaml` → `ownership`
   - правка: `ownership.yaml` → `teams[].id`, `rules[].team`, `rules[].when.path_glob`
   - значение: `path_glob: ["**"]`
   - причина: не нужна

**Пример.** `tests/fixtures/SampleSolution` с умолчаниями: находка 6,
кластеры `Sample.Pricing.Api` и `Sample.Common`
(`tests/test_setup_status.py::test_without_ownership_the_finding_is_not_configured`).

### parse.errors

Файл разобран с ошибками и не дал ни одного объявления · решение · где:
`exclude` в `docpipe.yaml` с причиной или правка исходника.

**Материал.** `docpipe setup status` — кластер `directory`;
`docpipe scan --stats` и `docpipe validate` — `parse_errors`; файл глазами:
чаще всего `#if` внутри выражения (атрибут, fluent-цепочка, базовый список).

**Без вопроса.** Нет: и отсев, и правка исходника — решения человека.

**Вопрос.** Заголовок «Разбор». «{N} файлов разобраны с ошибками и не дали
ни одного объявления: `{путь}`; причина — {`#if` в аргументе атрибута}
(`{файл:строка}`). Типы файла пропали из документации. От ответа зависит,
останутся ли они вне её с объяснением».

**Варианты.**

1. **Не берём** — файл не нужен в документации.
   - правка: `docpipe.yaml` → `exclude[].glob`, `exclude[].reason`
   - значение: глоб файла
   - причина: слова человека
2. **Править код** — конструкцию переписывает команда продукта.
   - вне настройки: вынести `#if` из выражения; находка остаётся до правки
   - причина: не нужна

Закрывается `exclude`, а не отсевом `path_glob` в `rules.yaml`: у файла без
объявлений нет символов, и правилу отсева нечего отсеивать.

**Пример.** Копия `tests/fixtures/WildSolution`: находка с
`src/Wild.Api/Modules/ConditionalModule.cs`
(`tests/test_setup_status.py::test_parse_errors_are_a_decision`).

### config.problems

Проблема настройки (`config check`) · дефект. Вопроса нет: чинится.

**Материал.** `docpipe config check --config … --root … --format json` —
коды `placeholder-left`, `input-missing`, `input-shadowed`, `root-missing`,
`adapter-input-missing`, `engine-missing`; у входа с `input-shadowed` поле
`shadowed` — второй кандидат; `docpipe setup status` — кластер `code`.

**Починка.** По коду проблемы. Вход не найден — путь входа пишется от
каталога `docpipe.yaml`; вход нашёлся в каталоге продукта (`input-shadowed`:
короткое имя совпало с каталогом или файлом продукта, на abp — `templates/`)
— путь от корня продукта, как его называет текст проблемы
(`docs/docpipe/templates`); с каталогом настройки такой путь не переедет,
и это сказать человеку; цель записи — существующий каталог; плейсхолдер
установщика — значение. Спросить можно только о факте машины («где лежит
движок»), а не о решении.

- правка: `docpipe.yaml` → `rules`, `templates`, `ownership`, `registries`, `arch`, `web.rules`, `web.pages`, `arch_adapters[].options.spec`
- правка: `docpipe.yaml` → `out`, `worklist`, `web.out`, `web.link_out`, `graph.engine_path`
- вне настройки: плейсхолдер `@ENGINE@` — повторить установщик с `--engine ПУТЬ`

**Пример.** `tests/fixtures/SampleSolution` с `templates` на несуществующий
каталог: кластер `input-missing`
(`tests/test_setup_status.py::test_without_templates_the_plan_is_a_defect_not_a_failure`);
`templates/` в текущем каталоге и `cfg/templates/` рядом с `cfg/docpipe.yaml`:
кластер `input-shadowed`
(`tests/test_setup_status.py::test_short_name_found_in_the_product_is_a_config_problem`).

### docs.broken

Документ не читается: структура испорчена · дефект.

**Материал.** `docpipe docs status МАНИФЕСТ --root …` — статус `broken`;
`docpipe docs explain МАНИФЕСТ ПУТЬ`; `docpipe setup status` — кластер
`directory`.

**Починка.** Front matter документа: `---` первой строкой, YAML читается,
ключ `docpipe` целый. Документ — не файл настройки, поэтому правит человек
(или агент по его слову, показав `git diff`). `--force` не лечит: он
перезаписал бы текст поверх.

- вне настройки: восстановить front matter документа по `docs explain`

**Пример.** Копия `tests/fixtures/SampleSolution` с `docs/modules/broken.md`
(`---\ndocpipe: [`)
(`tests/test_setup_status.py::test_documents_orphan_broken_and_shadowed`).

### docs.shadowed

Файл документа есть, а обход документов его не видит · дефект. Каждый
прогон `materialize` писал бы поверх.

**Материал.** `docpipe docs explain МАНИФЕСТ ПУТЬ` — какой фильтр сработал
(BOM или `---` не первой строкой, front matter без `docpipe.schema`,
`docs_scan_exclude`, каталог за симлинком, права); `docpipe setup status` —
кластер `directory`.

**Починка.** Сработал `docs_scan_exclude` — шаблон накрыл дерево документов,
агент сужает его и показывает, что находка ушла. Иначе — файл документа,
правит человек.

- правка: `docpipe.yaml` → `docs_scan_exclude`
- вне настройки: front matter или права файла документа

**Пример.** Копия `tests/fixtures/SampleSolution` с
`docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md` без
front matter (`tests/test_setup_status.py::test_documents_orphan_broken_and_shadowed`).

### link.duplicate_endpoints

Один ключ у двух узлов бэкенда · дефект: ASP.NET не выберет из двух действий.

**Материал.** `docpipe setup status` — кластер `route` (узлы через запятую);
`docpipe web link`; оба действия глазами.

**Починка.** Код бэка. Если второй узел — из модуля, который не берут
(образец, тест), это вопрос области (`scope.module_undecided`, `not_enrolled`
с причиной человека), а не починка дефекта.

- вне настройки: правка маршрута одного из действий командой продукта

**Пример.** `tests/fixtures/WildSolution` с фронтом
`tests/fixtures/WebWorkspace`: `GET api/ml/structure` у двух узлов
(`tests/test_web_link.py::test_one_key_declared_twice_is_a_defect`).

### load.errors

Прогон не собрался или собрался с ошибками · дефект. Решения его прогона
в охват не идут: ноль у них — неизвестность.

**Материал.** `docpipe setup status` — кластер `step` (`dotnet`, `web`,
`business`) с текстом ошибки; `docpipe config check`; `docpipe scan`
и `docpipe web scan` — тот же отказ с полным текстом.

**Починка.** Файл, который не читается: названный вход без файла, нечитаемый
набор правил, неверный реестр. Нечитаемый набор правил убирает
`dotnet.undecided` или `web.undecided` целиком, и `unexplained` падает —
это не успех (частая причина — `reason` у правила вида). Два случая — не починка, а вопрос человеку:
противоречие `enrolled`/`not_enrolled` (область, раздел
`scope.module_undecided`) и неоднозначное снятие в `pages.yaml` (что
снимать, решал человек, и уточняет он же — по форме `pages.layout`).

- правка: `docpipe.yaml` → `web.pages`, `registries`, `rules`, `web.rules`
- правка: `registries.yaml` → `registries[].path`, `registries[].kind`, `registries[].item_xpath`, `registries[].fields`

**Пример.** `tests/fixtures/SampleSolution` с `web.pages` на несуществующий
файл: кластер `web`, шаг 1 отвечает как прежде
(`tests/test_setup_status.py::test_a_failed_run_is_a_load_error_and_the_rest_is_reported`).

### docs.unavailable

План шага 2 не собрался · дефект. Остальные находки есть, команда не падает.

**Материал.** `docpipe setup status` — кластер `step` с текстом ошибки;
`docpipe config check` (`templates`, `ownership` — `input-missing`,
`input-shadowed`: «не найдено ни одного шаблона» у шага 2 бывает и тогда,
когда короткое имя нашло одноимённый каталог продукта);
`docpipe docs owners МАНИФЕСТ --lint`.

**Починка.** Скелеты, раскладка, файл владения. Раскладку — окончательно до
`materialize`: смена `docs_root`/`modules_dir`/`doc_layout` после него даёт
вечный `missing`. Коллизия `doc_path` у узлов фронта (squidex — 21 пара,
abp — 13) настройкой не чинится: у шага `web` нет разведения коллизий
шага 1 — это дефект инструмента, его называют вслух.

- правка: `docpipe.yaml` → `templates`, `docs_root`, `modules_dir`, `doc_layout`, `web.modules_dir`
- правка: `ownership.yaml` → `rules[].team`, `rules[].when`

Битый `ownership.yaml` агент чинит по тексту ошибки загрузки; правило,
которое ссылается на команду вне `teams`, — вопрос: имя команды — ключ
(`--team`), его называет человек (раздел `owners.unowned`).

**Пример.** `tests/fixtures/SampleSolution` с `templates` на несуществующий
каталог: кластеры `dotnet` и `web`
(`tests/test_setup_status.py::test_without_templates_the_plan_is_a_defect_not_a_failure`).

## Кандидаты без кода находки — не вопрос

Вопрос строится только из находки с кодом — `setup status` или
`new_findings` ревью (S33). У решений ниже кода нет: их недостача не видна
числом, её показывают кандидаты `setup candidates` (S11–S14) и разведка.
Агент их не спрашивает. Доказуемое кодом пишет сам; остальное — умолчание
и кандидаты вслух одним абзацем, а правка — только по слову человека,
сказанному без вопроса. Промолчал — правки нет, и это не остаток: находки
о нём нет.

- **`di_methods`** — `docpipe setup candidates di-methods`. Решает агент:
  обёртка регистрации видна в коде (`AddSingletonAs<X>().As<I>()`),
  проверка — охват записи (`coverage`, регистрации) больше нуля.
  - правка: `docpipe.yaml` → `di_methods`, `di_methods[].name`, `di_methods[].reason`
- **`dispatch_interfaces`** — `docpipe setup candidates dispatch-interfaces`.
  Решает агент, проверка — обработчики в охвате. Исключительность 1.0
  и отправки — необходимое, а не достаточное: тип-аргумент у двух примеров —
  свой класс-сообщение, а не коллекция или DTO клиентской библиотеки.
  - правка: `docpipe.yaml` → `dispatch_interfaces[].name`, `dispatch_interfaces[].reason`
- **`features`** — `docpipe setup candidates features`. Кандидатов агент
  называет списком вслух; **имя** раздела называет человек, если захочет:
  оно становится `node_id` и ключом якоря. Промолчал — раздела нет.
  - правка: `pages.yaml` → `features[].name`, `features[].path`, `features[].title`, `features[].reason`
- **`domains`** — вслух: «домен сейчас — имя модуля»; кандидаты —
  `projects.dotnet_projects` разведки. Имя домена — ключ (`{{ domain }}`,
  предикат владения `domain`): называет человек.
  - правка: `docpipe.yaml` → `domains`
- **раскладка** — вслух умолчания `docs`, `modules`, `kind-first`, два-три
  `doc_path` из `docpipe docs status` и цена: после первого `materialize`
  смена раскладки — переезд документов.
  - правка: `docpipe.yaml` → `docs_root`, `modules_dir`, `doc_layout`, `web.modules_dir`
- **список экранов** — вслух: страниц N, компонентов без маршрута M,
  три-пять маршрутов из `docpipe web pages`. Назвал человек экран, которого
  нет, — запись `add` по его слову.
  - правка: `pages.yaml` → `add[].route`, `add[].component`, `add[].reason`

## Правила потока

1. **Один вопрос — на кластер, не на символ.** Кластер `setup status`
   и есть единица вопроса: «120 типов в `*.Migrations`», а не 120 вопросов.
   Решение тоже ложится на группу — условием, а не перечнем FQN.
2. **Порядок — фазы по зависимостям** (аналитика §4): `config check` без
   отказов и `load.errors` → область (`scope.*`) → .NET (`dotnet.undecided`,
   `parse.errors`) → фронт (`web.undecided`) → шов (`link.calls_invisible`,
   `link.calls_unresolved`, `link.module_without_rewrite`,
   `link.calls_without_endpoint`, `link.almost`, `link.registry_unresolved`,
   последним — `link.endpoints_without_caller`) → страницы (`pages.*`) →
   раскладка и документы (`docs.*`) → владение (`owners.*`). Иначе вопрос
   «документировать ли эти 120 типов» задаётся про код, который вне области,
   а «кто зовёт эндпоинт» — до того, как стали видны вызовы фронта.
   **Внутри фазы — крупнейший кластер первым.**
3. **Не больше четырёх вопросов за один вызов** инструмента вопроса — и
   меньше, если на контуре он принимает меньше.
4. **Не спрашивать того, на что уже ответили инструменты.** Что
   доказывают `setup candidates` (позиция адреса, обёртка регистрации)
   и что уже решено по `setup explain` (запись с причиной), агент не
   спрашивает: доказуемое пишет сам, решённое — показывает.
5. **Решённое не всплывает по построению**: решение есть — находки нет.
   Если находка вернулась после правки, правка её не решила — смотреть
   `setup explain`, а не спрашивать снова.
6. **После каждой правки — инструмент ещё раз и разница** (Р-1):
   `docpipe config check` и `setup status` (на сервере S27 он помнит прошлый
   ответ, в CLI — `--out`/`--baseline`). Человеку показывается, сколько
   закрыто и не появилось ли нового.
7. **Ревью спрашивает только о новом** (S25): вопросы — только
   о `new_findings`, по разделу каталога их кода. `applied` — **отчёт,
   а не вопрос**: решения, которые применились к новому коду, первыми —
   записи «не берём», забравшие новый код; человек вправе сказать «не для
   этого кода» — тогда вырез `unless` по его слову. `dead_decisions` — тоже
   отчёт: удаление записи — по слову человека. Прежние решения повторно
   не задаются; находка, о которой в этой сессии уже спросили и получили
   «не сейчас», — тоже.

## Остановка

Остановку решает человек (Р-6). По его слову агент:

1. печатает сводку `docpipe setup status`: первая строка — «В области: N
   находок без решения, M дефектов; вне области: …»;
2. если необъяснённое осталось — список: код, число, крупнейшие кластеры
   и пробелы формата, которым решения нет. Остановка с остатком законна,
   но осознанна: остаток виден числом, а не исчезает;
3. показывает `without_reason` — решения короткой формой без причины:
   через полгода спросить «почему так» будет некого;
4. предлагает закоммитить каталог настройки: коммит — это принятие (П-1),
   и следующее ревью (S25) считает новый код от него. Коммит делает
   человек или агент по его прямому слову; несохранённые решения — ещё
   не решения (`config_dirty` у ревью).
