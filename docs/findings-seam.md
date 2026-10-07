# Шов фронт↔.NET: инвентарь форм на squidex и abp

Инвентарь от 07.10.2026. Прогон `scan` → `web scan` → `web link` на двух открытых
репозиториях с бэком на .NET и фронтом на Angular, без единого правила шва
(`url_rewrite`, обёрток, секции `link`). Числа сняты **до S16**: каждая задача
этапа C плана [настройки с ассистентом](setup-implementation-plan.md) (S16–S21)
меняет их. Вызовы, которых инструмент не видит, посчитаны поиском по исходникам —
потому что инструмент их не видит, и в этом находка.

Все формы воспроизведены в фикстуре [`tests/fixtures/SeamWorkspace/`](../tests/fixtures/SeamWorkspace/),
по одной на файл или метод; тест фикстуры —
[`tests/test_seam_fixture.py`](../tests/test_seam_fixture.py): он проверяет, что каждая
конструкция на месте, и фиксирует числа прогона — их правит каждая задача этапа C
(сейчас — после S18).

Здесь только факты: что найдено, где лежит, сколько. Что с этим делать — задачи S16–S21.

---

## Главный вывод: шов почти не виден, и то, что видно, неверно

| | squidex | abp (подмножество) |
|---|---|---|
| вызовов фронта восстановлено / не восстановлено | 3 / 78 | 0 / 1 |
| вызовов, которых инструмент не видит вовсе | 82 через обёртки `HTTP.*`, 15 `this.http.request` | 119 через `restService.request` |
| эндпоинтов бэка | 256 у 48 контроллеров | 77 с маршрутом (извлечено 82) |
| связей `web link` | 0 | 0 |
| «дефект» `web link` | 1 дубль `GET info` — **ложный** | — |
| с форматами S16–S20 связывается (оценка) | ~87 из ~98 вызовов со статическим путём | 60 из 62 вызовов в области |
| эндпоинтов без вызывающего останется (оценка) | 157 из 254 | 20 из 77 |

Три вывода:

1. **На squidex у всех 256 эндпоинтов нет префикса `api/`.** Префикс объявлен
   на абстрактной базе `ApiController` выражением `[Route(Constants.PrefixApi)]`,
   разбор атрибутов не наследует `[Route]` и не разрешает констант. Ни один вызов
   фронта `api/…` поэтому не ложится ни на один эндпоинт, даже прямой литерал.
2. **Почти все вызовы фронта идут не через `HttpClient` напрямую**, а через
   построитель адреса (squidex), обёртки с версией (squidex) или обёртку
   с объектом-запросом (abp). Первые дают «значение переменной не восстановлено»,
   вторые и третьи не попадают ни в один счётчик.
3. **Восстановленное правдоподобно и неверно.** Из трёх восстановленных вызовов
   squidex один получил `GET ''` (изменяемое поле), два — `squidex/sdk-fern/main/sdks.json`
   (хост срезан, и в `help.service.ts:42` подставлен `const url` соседнего метода
   со строки 47). Единственный «дефект» отчёта — дубль `GET info` у `PingController`
   и `InfoController` из `IdentityServer` — ложный: у них разные базы.

---

## Настройки прогонов

**squidex** — `roots: [backend]`, `web.roots: [frontend]`,
`di_methods: [AddSingletonAs, AddTransientAs, AddScopedAs]` (самодельные обёртки DI,
см. ловушку в `CLAUDE.md`). Фронт — один модуль `squidex`, 716 файлов, 845 узлов,
90 страниц; бэк — 22 модуля, 107 узлов.

**abp** — подмножество с Angular-клиентом: `roots` — `modules/identity`,
`modules/permission-management`, `modules/tenant-management`,
`modules/setting-management`, `modules/feature-management`, `modules/account`
и `framework/src/Volo.Abp.AspNetCore.Mvc`; `web.roots: [npm/ng-packs]`.
Бэк — 120 модулей, 133 узла, 77 эндпоинтов с маршрутом; фронт — 17 модулей,
710 узлов, 3 страницы. Клон лежит во вложенном каталоге: корень прогона —
`examples/abp/abp`, а не `examples/abp` (иначе «модулей 0, узлов 0» без ошибки).

Тексты обоих `docpipe.yaml` и команды — в разделе «Как воспроизвести».

---

## Формы шва

Колонка «Фикстура» — где форма лежит в `SeamWorkspace` (пути от
`backend/Seam.Api/` и `frontend/src/app/`); «Задача» — что её закрывает.

### Сторона .NET

| Форма | squidex | abp | Пример | Фикстура | Задача |
|---|---|---|---|---|---|
| `[Route]` с константой на абстрактной базе: `[ApiController][Route(Constants.PrefixApi)]` | 42 контроллера из 48 | — | `Squidex.Web/ApiController.cs`, `PrefixApi = "/api"` | `Web/ApiController.cs`, `Web/Constants.cs`, `Controllers/AppsController.cs` | S17 |
| вторая база со своей константой (`[Route(Constants.PrefixIdentityServer)]`) | 6 контроллеров на `IdentityServerController` | — | `Areas/IdentityServer/Controllers/IdentityServerController.cs` | `Web/TokenApiController.cs` (база с токеном `api/[controller]`) и `Controllers/OrdersController.cs` | S17 |
| один относительный маршрут под разными базами — ложный дубль | `GET info` (`PingController`, `InfoController`) | — | | `Controllers/InfoController.cs`, `Controllers/OrdersController.cs` (`[HttpGet("info")]`) | S17 |
| действие с `[Route]` без глагола | 6 | — | | `Controllers/CommentsController.cs` | S17 |
| `[AcceptVerbs("GET", "POST")]` | не считалось | не считалось | | `Controllers/VerbsController.cs` | S17 |
| «конвенциональные» в отчёте `web link` | 6, настоящих ни одного: `ApiController`, `IdentityServerController` (базы), `CommentsController`, `UsersController`, `PluginController`, `ErrorController` | 4: `AbpControllerBase`, `AbpController` (базы), `ChallengeAccountController`, `ErrorController` | | 6, настоящий один — `Controllers/LegacyController.cs` | S17 |
| `MapControllerRoute`, minimal API | 0 | 3 `MapControllerRoute`; `ConventionalControllers.Create` — 16 (13 в шаблонах решений) | | `Program.cs` (`MapControllerRoute`, `app.MapGet("/health", …)`), `LegacyController` | следующий план шва |
| публичный API для внешних клиентов | `content/{}` — 20 эндпоинтов без вызывающего | `integration-api/*` — 10 | | `Controllers/ContentController.cs` (`[Route("content/{app}")]`) | S20 |
| описание API рядом с кодом | `generated.ts` (NSwag) — только DTO; снимок OpenAPI `frontend/generator/Generator/cache.json` — 197 операций | 9 `generate-proxy.json` | | — | кандидат следующего плана |

### Сторона фронта

| Форма | squidex | abp | Пример | Фикстура | Задача |
|---|---|---|---|---|---|
| прямой литерал `this.http.get('api/…')` | 3 восстановлено (все с ошибкой ключа, см. выше) | 0 | | `services/info.service.ts` | — |
| построитель: `const url = this.apiUrl.buildUrl('/api/apps'); this.http.get(url)` | 78 невосстановленных, все — «значение переменной», выражение `url` | — | `app/shared/services/apps.service.ts` | `services/apps.service.ts`, методы `list`, `get` | S16, S19 |
| литеральный `const url` соседнего метода подставляется во все `get(url)` файла | `help.service.ts:42` получил `const url` со строки 47 | — | | `apps.service.ts`, метод `archived` → `list` и `get` | S16 |
| вызов приписан каждому узлу файла (DTO рядом с сервисом) | `help.service.ts`: в отчёте 5 вызовов вместо 3 | — | | `apps.service.ts`: `AppDto` и `AppsService` | S16 |
| обёртки с адресом позиционно: `HTTP.getVersioned(this.http, url)` | 82 вызова в 14 файлах: `requestVersioned` 48, `getVersioned` 16, `postVersioned` 11, `putVersioned` 4, `upload` 3 | — | `app/framework/angular/http/http-extensions.ts` | `framework/http-extensions.ts` (тела), `services/apps-versioned.service.ts` (вызовы) | S18, S19 |
| обёртка с методом из аргумента: `HTTP.requestVersioned(this.http, link.method, url)` | входит в 48 выше | — | | `apps-versioned.service.ts`, метод `putApp` | S18, S19 |
| обёртка с объектом-запросом: `restService.request({ method, url })` | — | 119, из них 118 — в сгенерированных `proxy/` | `npm/ng-packs/**/proxy/**` | `services/apps-proxy.service.ts`, `framework/rest.service.ts` | S18, S19 |
| гипермедиа `this.http.request(link.method, url)` | 15 мест в 7 файлах | — | `users.service.ts`, `apps.service.ts`, `rules.service.ts` | `services/links.service.ts`, метод `follow` | S20 (`unresolvable`) |
| гипермедиа через построитель `buildUrl(link.href)` | 68 | — | | `links.service.ts`, метод `fetch` (адрес из ссылки) | S20 (`unresolvable`) |
| хвостовой построитель query `` `…${StringHelper.buildQuery(…)}` `` | 13 | — | `stock-photo.service.ts:34` | `apps.service.ts`, метод `search` | S16 |
| внешний адрес | 3: `help.service.ts` (2, `raw.githubusercontent.com`), `stock-photo.service.ts` (1) | — | | `services/feed.service.ts` | S16, S20 |
| изменяемое поле с литералом: `fileSource = ''`, потом `this.fileSource = src` | 1: `asset-text-editor.component.ts:42` → `GET ''` | — | | `components/editor.component.ts` | S16 |
| конкатенация с невосстановленной базой `this.base + '/api/apps'` | не считалось | — | | `apps.service.ts`, метод `legacy` | S16 |
| адрес из данных во вложенной обёртке | — | `DynamicFormService.getOptions(url, apiName)` | | — | S20 (`unresolvable`) |
| невосстановленный вызов модуля без восстановленных | — | 1 (`ui-localization.service.ts:53`); `unconfigured_modules` пуст при 17 модулях | | — | S18 |

---

## Оценка эффекта

Оценка — ручной разбор невидимых и невосстановленных вызовов против эндпоинтов,
а не прогон: правил, которыми это выражается, ещё нет.

**squidex.** С построителем `apiUrl.buildUrl`, обёртками `HTTP.*Versioned`
и `strip_prefix: api` связывается ~87 вызовов из ~98 со статическим путём.
`strip_prefix` здесь — обход того, что эндпоинты не наследуют `api/` от базы;
после S17 он не нужен. Эндпоинтов без вызывающего остаётся 157 из 254:
гипермедиа `apps/{}/…` — 78, `content/{}` — 20 (публичный API), `account/*`,
`connect/*` (страницы и протокол IdentityServer). Остаток — решения человека
в секции `link` (S20), а не дыра разбора.

**abp.** С обёрткой `restService.request` связывается 60 из 62 вызовов в области.
Эндпоинтов без вызывающего остаётся 20 из 77, из них `integration-api/*` — 10:
это API для других модулей, а не для фронта.

---

## Как вышло после S17

Прогон 08.10.2026 с теми же настройками (раздел «Как воспроизвести»).

| | squidex до S17 | squidex после | abp до S17 | abp после |
|---|---|---|---|---|
| эндпоинтов (с маршрутом) | 256 (255) | 262 (262) | 82 (77) | 82 (77) |
| с префиксом базы (`api/`, `identity-server/`) | 0 | 261: 227 и 34; ещё 1 — абсолютный `ai-images/…` | — | — |
| `*` (`[Route]` без глагола) | — | 6 | — | 0 |
| дубль в `web link` | 1 (`GET info`, ложный) | 0 | 0 | 0 |
| «конвенциональные» | 6, настоящих 0 | 0 | 4 | 1 (`ErrorController`) |
| невосстановленных маршрутов | — | 0 | — | 0 |

Обе константы squidex (`Constants.PrefixApi = "/api"`,
`Constants.PrefixIdentityServer = "/identity-server"`) разрешились, хотя
`Constants` в репозитории два (`Squidex.Web` и `Squidex.Infrastructure.Json.System`):
константа с нужным именем есть только у одного. Связей на squidex по-прежнему 0:
восстановленных вызовов фронта 2, остальные идут через построитель и обёртки
(S18, S19). `strip_prefix: api` из оценки выше больше не нужен.

На фикстуре: эндпоинтов 11 → 14, связей 0 → 1 (`GET api/info`), дублей 1 → 0,
«конвенциональных» 6 → 1 (`LegacyController`).

Попутно:

- **у abp пять эндпоинтов с пустым маршрутом остались ни в одной категории.**
  Это `ChallengeAccountController` — абстрактная база с действиями `[HttpGet]`
  без шаблона; её наследники — в хостах вне `roots`. До S17 она числилась
  «конвенциональной», теперь абстрактная база туда не идёт по условию, а пустой
  маршрут в ключи связи не входит. Действия, унаследованные от абстрактной базы,
  — вне плана («Что намеренно не входит»);
- **`GET ''` у `asset-text-editor.component.ts:42` (squidex) после S16 остался.**
  Поле там `@Input() public fileSource = ''`: его присваивает привязка шаблона,
  а не код класса, и правило S16 «поле — константа, если ему не присваивают»
  принимает его за константу. Фикстура воспроизводит другую форму
  (`this.fileSource = src` в методе), поэтому тест S16 зелёный.

---

## Как вышло после S18

Прогон 08.10.2026 с теми же настройками; кандидаты — `docpipe setup candidates
http-wrappers` и `url-builders`.

| | squidex до S18 | squidex после | abp до S18 | abp после |
|---|---|---|---|---|
| невосстановленные вызовы в манифесте | только число в сидкаре (79) | 79 записей: построитель 73, параметр функции 4 (тела `HTTP.*Versioned`), не восстановлено 2 | число (1) | 1 запись |
| вызовы через обёртки | ни в одном счётчике | кандидаты: 306 вызовов в 20 группах; без `*.spec.ts` — 106 в 14, из них 97 в шести настоящих (`requestVersioned` 47, `getVersioned` 16, `http.request` 16, `postVersioned` 11, `putVersioned` 4, `upload` 3) | ни в одном | 202 в 17 группах; `restService.request` 121, адрес — `0.url` |
| построители адреса | — | `apiUrl.buildUrl`: 171 адрес, 73 у `HttpClient`, 98 через обёртки; путь похож на адрес у 102, остальное — гипермедиа `buildUrl(link.href)` | — | нет |
| модули без `url_rewrite` в `web link` | 1 (`squidex`) | 1 | 0 | 1 (`core`) |
| `counts.calls_unresolved` | — | 79 | — | 1 |

Видимых мест на squidex стало 79 + 97 = 176 против ~166 из оценки S18: в оценку
не вошли 16 `this.http.request` (гипермедиа через построитель). Невидимым остался
один `requestVersioned` (`assets.service.ts:230`): адрес — шаблон с построителем
внутри, `` `${this.apiUrl.buildUrl(link.href)}${query}` ``.

Попутно:

- **«17 модулей abp молчат» — точнее, у 16 из 17 нет ни одного видимого вызова.**
  Все их вызовы идут через `restService.request` и до S19 не видны вовсе;
  невосстановленный вызов есть только у `core` (`ui-localization.service.ts:53`),
  и его модуль теперь назван. Остальные назовёт `web link`, когда обёртку объявят;
- **гипермедиа через построитель на адрес не похожа ничем**: у
  `HTTP.requestVersioned(this.http, link.method, url)` при
  `const url = this.apiUrl.buildUrl(link.href)` ни один аргумент не начинается
  с `/` или `api/`. По букве S18 («аргумент похож на адрес») не стал бы
  кандидатом ни один из 47 вызовов `requestVersioned` и ни один из 16
  `this.http.request`. Поэтому факт хранит и вызов, у которого аргумент —
  значение вызова, а кандидаты засчитывают его, если этот вызов — построитель
  адреса по всему прогону;
- **тесты встают первыми.** Без `exclude: ["**/*.spec.ts"]` первая строка
  кандидатов squidex — `httpMock.expectOne` (188 вызовов в 29 файлах `*.spec.ts`),
  у abp вторая и третья — `service.request` и `spectator.expectOne` из
  `rest.service.spec.ts`;
- **сам построитель похож на обёртку**: `this.apiUrl.buildUrl('/api/apps')` —
  вызов члена с аргументом-адресом. Группа, результат которой хоть раз стал
  адресом другого вызова, в обёртки не идёт и названа в `builders`.

На фикстуре: `unresolved_calls` — 9 записей (столько же, сколько
`calls_unresolved`), кандидатов в обёртки 3 (`HTTP.getVersioned` — `1`,
`HTTP.requestVersioned` — `2`, `rest.request` — `0.url`), построитель один
(`apiUrl.buildUrl`, 2 адреса), `counts.calls_unresolved` в `web link` — 9.

---

## Что известно об АС CF

Подробно — в [`findings-cashflow-frontend.md`](findings-cashflow-frontend.md).
Для шва важны четыре вещи:

- `FixUrlInterceptor` меняет только **базу** адреса (через конвейер
  `UrlDecoratorService` и только при непустом `environment.apiUrl`); хвост — тот же
  литерал, что в сервисе. Преобразование URL задаётся настройкой, а не выводится
  из кода интерцептора;
- у модуля ML `proxy.conf.js` объявляет `context: ['/**']` без `pathRewrite`:
  правило `url_rewrite` у него пустое, а у остальных шести фронтов — свои
  `^/pm` → `''`, `^/api/` → `/admin/api/` и т. д.;
- на стороне .NET — **один** `MapControllerRoute` (таблица «Сторона .NET»
  в `findings-cashflow-frontend.md`), minimal API нет; токенов `[controller]`/`[action]`
  нет, маршруты — литералы;
- обращения к реестру: `api/items/query` с `listInnerName` в теле
  и `api/items?listInnerName=…` в query — один маршрут на много смыслов
  (`web.registry_calls`).

Ни обёрток `HTTP.*`, ни объекта-запроса там не находили: 79 вызовов модуля ML
идут через `this.http`/`httpClient`, невосстановимые 21 почти все разрешаются
константами. Насколько формы squidex и abp встретятся на контуре — вопрос
прогона S32.

---

## Попутно найдено при сборке фикстуры

- **Свойства анонимного типа в сигнатуре метода web-разбор записывает членами
  класса.** У `getInfo(): Observable<…> { return this.http.get<{ version: string }>(…) }`
  в `members` появляются два `version`, и `member_ranges` отдаёт вызов самому узкому
  «члену» — `version` вместо `getInfo`. Состав страницы считается по графу
  «член зовёт член», и такой вызов из него выпадает. На squidex и abp не мерилось.
  Фикстура этой формы избегает (`ResourceLink` — именованный тип), чтобы
  в тесты S16–S21 не попал чужой дефект. Пункт — в бэклоге.
- **Ключ без фиксированных сегментов «почти» совпадает с любым маршрутом из одних
  параметров.** `GET ''` из `editor.component.ts` связался «почти» с `GET {id}`
  у `OrdersController`: `_fixed_segments` у обоих пуст. После S16 сам вызов станет
  невосстановленным, но правило `almost_equal` остаётся прежним.
- **`rules: ../../../rules/rules.yaml` в `docpipe.yaml` фикстуры разрешается
  второй ступенью `resolve_input`, а первая — от текущего каталога.** В git worktree,
  лежащем на три уровня ниже основного клона (`.claude/worktrees/<имя>/`), этот
  путь от текущего каталога ведёт в правила **основного клона**, и прогон молча
  берёт набор другой ветки. Тест фикстуры передаёт правила явно.

---

## Как воспроизвести

Клоны — в `~/docspipe-examples/examples/` (в репозиторий не входят). Команды —
из корня клона `docpipe`; `rules` по умолчанию разрешается от текущего каталога.

`squidex.yaml`:

```yaml
roots: [backend]
di_methods: [AddSingletonAs, AddTransientAs, AddScopedAs]
web:
  roots: [frontend]
```

`abp.yaml`:

```yaml
roots:
  - modules/identity
  - modules/permission-management
  - modules/tenant-management
  - modules/setting-management
  - modules/feature-management
  - modules/account
  - framework/src/Volo.Abp.AspNetCore.Mvc
web:
  roots: [npm/ng-packs]
```

```bash
EX=~/docspipe-examples/examples
OUT=/tmp/seam            # здесь же лежат squidex.yaml и abp.yaml

uv run docpipe scan     --root $EX/squidex --config $OUT/squidex.yaml --out $OUT/squidex.json --no-cache
uv run docpipe web scan --root $EX/squidex --config $OUT/squidex.yaml --out $OUT/squidex.web.json --no-cache
uv run docpipe web link $OUT/squidex.json $OUT/squidex.web.json --config $OUT/squidex.yaml \
    --out $OUT/squidex.link.json

# abp: корень — вложенный каталог клона
uv run docpipe scan     --root $EX/abp/abp --config $OUT/abp.yaml --out $OUT/abp.json --no-cache
uv run docpipe web scan --root $EX/abp/abp --config $OUT/abp.yaml --out $OUT/abp.web.json --no-cache
uv run docpipe web link $OUT/abp.json $OUT/abp.web.json --config $OUT/abp.yaml \
    --out $OUT/abp.link.json
```

Ожидаемое до S16: squidex — «Вызовов: восстановлено 3, не восстановлено 78»,
`web link` — 5 вызовов, 255 эндпоинтов с маршрутом (256 всего, у одного маршрут
пуст), 0 связей, 1 дубль, 6 конвенциональных; abp — «Модулей: 120, узлов: 133»,
«восстановлено 0, не восстановлено 1», `web link` — 77 эндпоинтов, 0 связей,
4 конвенциональных, `unconfigured_modules` пуст.

Невидимые вызовы — поиском по `*.ts` без `*.spec.ts`:

```bash
cd $EX/squidex/frontend/src
grep -rnoE "HTTP\.[a-zA-Z]+\(" --include=*.ts . | grep -v '\.spec\.ts' | sed 's/.*HTTP\./HTTP./' | sort | uniq -c
grep -rnE "this\.http\.request\(" --include=*.ts . | grep -v '\.spec\.ts'
grep -rnE "buildUrl\(link\.href\)" --include=*.ts . | grep -v '\.spec\.ts' | wc -l
grep -c '"operationId"' ../generator/Generator/cache.json

cd $EX/abp/abp
grep -rnE "restService\.request\b" npm/ng-packs --include=*.ts | grep -v '\.spec\.ts' | wc -l
grep -rn "ConventionalControllers\.Create" --include=*.cs . | wc -l
find . -name generate-proxy.json -not -path '*/node_modules/*' | wc -l
```

Фикстура — без внешних клонов:

```bash
uv run pytest tests/test_seam_fixture.py -q
uv run docpipe scan --root tests/fixtures/SeamWorkspace --config tests/fixtures/SeamWorkspace/docpipe.yaml \
    --rules rules/rules.yaml --out /tmp/seam/fixture.json --no-cache
```
