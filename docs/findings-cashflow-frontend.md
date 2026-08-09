# Фронтенд АС CF: что показала разведка

Отчёт по разведке боевого репозитория (09.08.2026), коммит `ea1124e74b6`, 45142
отслеживаемых файла. Прогон [`tools/recon-frontend.sh`](../tools/recon-frontend.sh)
с областями:

```bash
bash tools/recon-frontend.sh \
    sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.ML \
    sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.NetCore > recon-frontend.txt 2>&1
```

Здесь только факты: что найдено, где лежит и что ломает наивную реализацию. Что
с этим делать — в [`frontend-analysis.md`](frontend-analysis.md).

---

## Главный вывод: литерал в сервисе и есть маршрут

`FixUrlInterceptor` был назван в аналитике «самой опасной неизвестной во всей задаче».
Прочитан:

```ts
@Injectable()
export class FixUrlInterceptor implements HttpInterceptor {
  constructor(private urlDecorator: UrlDecoratorService) {}

  intercept(req: HttpRequest<any>, next: HttpHandler): Observable<HttpEvent<any>> {
    if (environment?.apiUrl) {
      const url = this.urlDecorator.fixUrl(req.url);
      return next.handle(req.clone({ url }));
    } else {
      return next.handle(req);
    }
  }
}
```

Сам он ничего не решает — делегирует конвейеру `cf-api/url-decorator`, устроенному так
(со слов владельца, читавшего модуль):

- `UrlDecoratorService.fixUrl(url)` сверяет URL с `EXCLUDE_URLS` и, если совпал,
  возвращает **без изменений**; иначе прогоняет через список декораторов `reduce`-ом;
- список декораторов и исключений — DI-токены `URL_DECORATORS` и `EXCLUDE_URLS`,
  по умолчанию зарегистрирован один декоратор `SetApiUrl`;
- `SetApiUrl` подставляет `CF_API_URL` (базу) к относительному URL и не дублирует
  префикс, если база его уже содержит.

**Конвейер трогает базу, а не путь.** Отсюда три следствия, и все проверяемые:

1. в боевой сборке ветка `if` не выполняется вовсе, если `environment.apiUrl` пуст, —
   URL уходит в сеть как записан в сервисе;
2. в дев-сборке меняется только начало URL, хвост — тот же литерал;
3. решение аналитики «преобразование URL задаётся конфигурацией, а не выводится
   из кода интерцептора» подтверждено самой конструкцией: у неё **уже есть**
   конфигурация — список декораторов и список исключений в DI. Выводить эту логику
   статически означало бы исполнять `reduce` по неизвестному списку.

Тем же прогоном подтверждено с другой стороны: `proxy.conf.js` модуля ML объявляет
`context: ['/**']` и **не содержит `pathRewrite`** — префикс приложения не срезается
и не дописывается.

Остаётся дочитать одну строку: пуст ли `apiUrl` в `src/environments/environment.prod.ts`.
Пока она не прочитана, пункт 1 — обоснованное предположение, а не факт.

---

## Семь фронтов, и они устроены по-разному

| Модуль | `.ts` | Раскладка | Стор | Angular |
|---|---|---|---|---|
| `sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.ML` | 1258 | свой `angular.json`, один проект `tr-p` | NGXS 3.8.2 | 17.3.12 |
| `sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.Front` | 1119 | свой `angular.json` | — | — |
| `sbt.cashflow.tm.subrepo/TypedModels.Front.Angular` | 1061 | многопроектный `angular.json` | — | — |
| `sbt.cashflow.portfolio.subrepo/PM.Front` | 943 | `angular.json`, 7 проектов | ngrx | 10 … 19 |
| `sbt.cms.subrepo/Sbt.CMS.Front` | 704 | **nx**: `nx.json`, 6 `apps/`, 6 `libs/` | — | — |
| `sbt.cashflow.realty.subrepo/Sbt.Cashflow.Front.Angular` | 341 | `angular.json`, проект `realty` | — | — |
| `sbt.cashflow.stresstest/StressTest.Front` | 59 | `angular.json`, проект `stresstest` | — | — |

По репозиторию версии `@angular/core` разъезжаются от `~10.0.6` до `^19.0.0`, а рядом
с NGXS живёт `@ngrx/store` (10.0.1, 15, 15.4.0, 19.2.1). Единицей модуля не может быть
ни «каталог с `angular.json`», ни «проект nx» по отдельности — нужны обе формы.

### Префикс приложения — часть нормализации ключа

`pathRewrite` из всех `proxy.conf` репозитория:

| Модуль | `context` | Что происходит с путём |
|---|---|---|
| ML | `/**` | **ничего**, префикса нет |
| PM.Front | `/pm/api`, `/pm/formsx`, `/pm/files`, `/pm/pmloadrequests` | `^/pm` → `''` |
| Sbt.CMS.Cashflow.Front | `/cf/api`, `/cf/formsx`, `/cf/calculation`, `/cf/files` | `^/cf` → `''` |
| Sbt.Cashflow.Front.Angular | `/realty` | `^/realty` → `''` |
| TypedModels.Front.Angular | `/tm/api`, `/tm/formsx` | `^/tm` → `''` |
| Sbt.CMS.Front (admin) | `/api/…` | `^/api/` → `/admin/api/` — **дописывается**, а не срезается |

Литерал `'/pm/api/limits/getperiods'` во фронте и маршрут `api/limits/…` в контроллере
— один и тот же эндпоинт. Реализация, сравнивающая ключи как есть, разведёт их
по двум корзинам и объявит «вызов без эндпоинта» плюс «эндпоинт без вызывающего».
Таблица «модуль → преобразование префикса» обязана быть данными конфигурации: у ML
она пустая, и на нём одном ошибку не видно.

---

## Модуль ML в цифрах

1258 `.ts`, из них 480 — `*.spec.ts` и `*.d.ts`; 177 `*.component.html`, 191 `@Component`,
`.tsx` — ноль. Standalone-компонентов 354 против трёх `@NgModule`. Внедрение зависимостей
конструкторное: `inject(…Service)` встречается 9 раз.

Стек: Angular 17.3.12, TypeScript 5.4.5, NGXS 3.8.2 (`store` плюс `devtools`, `logger`,
`router`, `storage`), rxjs 6.5.4.

Алиасы `tsconfig.json`, без которых не резолвится ни один импорт вида `@shared/…`:
`@cf-api/*`, `@cf-ui/*`, `@shared/*`, `@declaration/*`, `@env/*`, `@routes/*`,
`@inner-debt/*`, плюс `exceljs` → `../node_modules/exceljs/dist/exceljs.bare`.

---

## HTTP-вызовы: 79 на модуль, две трети восстановимы сразу

| Форма первого аргумента | Сколько |
|---|---|
| строковый литерал | 26 |
| шаблонная строка | 27 |
| переменная или выражение | 21 |
| **всего вызовов по получателю `this.http`/`httpClient`** | **79** |
| «любых `.get/.post/…`» — **не показатель** | 327 |

Последняя строка оставлена намеренно: именно её первый прогон разведки выдал за число
HTTP-вызовов. В неё входят `Map.get`, `form.get('search')`, `headers.get(…)`,
`queryParams.get(…)`, и отношение «литералов к вызовам» по ней бессмысленно.

**Невосстановимые 21 — почти все одна и та же схема**, и она разрешается константами:

```ts
export const auditUrl = '/integration/log/AuditJ';        // → 10 вызовов audit.service.ts
private readonly baseUrl: string = '/api/ml/debtsconsgroup';  // → `${this.baseUrl}/saveAlternative` и ещё пять
```

Константа уровня модуля и `readonly`-поле с литеральным значением. Разрешение этих двух
форм переводит большинство «переменных» в разряд восстановленных; без него отчёт покажет
27 % необъяснённых вызовов там, где реальная дыра — единицы.

Префиксы путей, встреченные литералами: `api/ml` — 120, `api/items` — 14,
`api/mlmodelversion` — 3, `api/cf` — 1. Вызовы, где база лежит в переменной,
в гистограмму не попадают.

Отдельно: `'/MlModels/Download'` и `'/MlModels/ForecastDownload?'` — маршруты вне `api/`,
и `export const cfUrl = '/formsx/models/'` — ссылка на формы платформы.

---

## `api/items/query` — один маршрут на много смыслов

```ts
this.http.post('api/items/query', { listInnerName: 'users', fields, filters });
this.http.post('api/items/query', { listInnerName: 'models', … });
this.http.post('api/items/query', { listInnerName: 'OkkChain', fields: ['OkkChainTitle'] });
this.http.post('api/items/query', { listInnerName: 'MODELVERSIONPRODUCTTYPES', … });
this.http.post('api/items/query', { listInnerName: 'MVLOADREASONS', … });
```

Четырнадцать вызовов `api/items` в ML идут в **один** эндпоинт платформы, а различаются
значением `listInnerName`. Ключ `(метод, маршрут)` склеит в одну точку обращения
к пользователям, к моделям и к цепочке ОКК — то есть даст одну связь вместо пяти
и потеряет ровно тот смысл, ради которого связь строится.

Якорь здесь — пара **маршрут + `listInnerName`**, и это уже существующий якорь
бизнес-слоя: вид `list`, реестр `Cashflow.Structure.xml` (`resolve.REGISTRY_KIND`).
Фронт цепляется к реестру напрямую, минуя C#; связь фронт↔бизнес в этом месте короче,
чем фронт↔бэк.

Того же порядка находка — `itemInnerName` в `api/ml/draft/get?itemId=…&itemInnerName=…`:
различающий смысл параметр уехал в query-строку, которую нормализация маршрута
отбрасывает.

---

## Сторона .NET: маршрут склеивается из двух атрибутов

Область `Sbt.CMS.Cashflow.NetCore`:

| Что | Сколько |
|---|---|
| `[Route("…")]` всего | 433 |
| из них с токеном `[controller]` | **0** |
| из них с токеном `[action]` | **0** |
| `[HttpGet("…")]` и подобные с аргументом | 47 |
| `[HttpGet]` и подобные **без** аргумента | 556 |
| `MapControllerRoute` (конвенциональная) | 1 |
| minimal API (`app.MapGet` и т. п.) | 0 |

Токенов нет вовсе — маршруты записаны литералами, и сравнение с фронтом посимвольное.
Приведение к нижнему регистру остаётся, но уже как страховка, а не как несущая
конструкция.

Главное здесь другое. Гистограмма первых двух сегментов `[Route]` показывает, что
атрибут стоит **и на методах тоже**:

```
33 api/ml                        ← маршруты классов
 2 state/byclient                ← а это маршруты методов
 1 innerDebts/insert
 1 guarantee/delete
```

Значит 556 пустых `[HttpGet]`/`[HttpPost]` дают только **глагол**, а путь метода приходит
из отдельного `[Route]`. Резолвер обязан складывать три независимые части — маршрут
класса, маршрут метода и глагол, — и ни одна из них не обязательна. Реализация,
считающая, что пустой `[HttpGet]` означает «URL равен маршруту класса», склеит в один
ключ десятки действий одного контроллера.

Пример владельца сошёлся: `api/ml/structure` объявлен в
`Areas/MlModel/Controllers/MlStructurationController.cs`.

---

## Маршруты фронта собираются спредами из других файлов

```ts
export const appRoutes: Routes = [{
  path: '', component: RoutesComponent,
  children: [
    { path: 'models',   children: [...modelsPath] },    // ← ./routesPath/models
    { path: 'forecast', children: [...forecastPath] },  // ← ./routesPath/forecast
    { path: '**', redirectTo: 'models', pathMatch: 'full' },
  ],
}];
```

В модуле 8 массивов `Routes` и 26 записей `path:`, при этом `loadChildren` — ноль,
`RouterModule.forRoot`/`forChild` — ноль, `loadComponent` — 5. То есть дерево страниц
собирается **спредом импортированного идентификатора**, а не ленивым `import()`, под
который писалась §3.3 аналитики.

Разбор одного `app.routes.ts` даст два пути из двадцати шести, и молча: ошибок разбора
не будет, узлы просто не появятся. Резолв «идентификатор → экспортированный массив
в другом файле» — обязательное звено, а не оптимизация.

Пути литеральные, параметры — `:id`.

---

## NGXS: цепочка нужна целиком

| Что | Сколько |
|---|---|
| `@State(` | 33 |
| `@Action(` | 409 |
| `@Selector(` | 523 |
| `static readonly type` | 651 |
| `store.dispatch(` | 552 |

Компонент не зовёт сервис — он диспатчит экшен, экшен обрабатывается в стейте, стейт
зовёт сервис, сервис делает HTTP-вызов. Без прохода по этой цепочке у страницы не будет
связи с эндпоинтом — связь будет только у сервиса, то есть у файла, о котором бизнес
ничего не знает.

Единственное стабильное литеральное звено цепочки — строка типа экшена
(`'[Alternative Guarantee] …'`), и она же годится в ключ.

---

## Что осталось невыясненным

| Вопрос | Где смотреть | Почему важно |
|---|---|---|
| пуст ли `apiUrl` в боевой сборке | `src/environments/environment.prod.ts` | делает главный вывод фактом вместо предположения |
| что перечислено в `EXCLUDE_URLS` | `cf-api/url-decorator/url-decorators.ts` | эти URL минуют конвейер целиком |
| что делает `checkUrl(type)` | `shared/services/items.service.ts` | подменяет URL в debug-режиме, ветка `.json`-моков |
| природа страниц у соседних фронтов | прогон разведки с их областью | у ML страницы свои, у `PM.Front` и `scs-2` найдены признаки списков платформы |

---

## Ловушка самой разведки

Первый прогон (без области) прошёл по всему репозиторию и **не ответил ни на один
из четырёх вопросов, ради которых писался**. Фронтов семь, а каждый срез «примеры»
и «содержимое» ограничен `head -N` по алфавиту: `Sbt.CMS.Cashflow.ML` идёт седьмым
и выпал целиком — вместе с `fix-url.interceptor.ts`. В выводе это неотличимо
от «в модуле такого нет».

Разбор ловушек и починка — в журнале реализации; здесь важно следствие для чтения
любого будущего отчёта: **сначала проверить, что область в шапке та, о которой
идёт речь.**
