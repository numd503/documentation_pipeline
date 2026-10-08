# Фаза 30: шов фронт↔.NET

## Цель

Каждый вызов фронта либо связан с эндпоинтом .NET, либо о нём есть решение
(внешний адрес, адрес не восстановить); каждый эндпоинт либо зовёт фронт,
либо записано, кто зовёт его извне. Обёртки HTTP, построители адреса и
префиксы прокси записаны правилами, а не догадкой в ответе.

Концы без пары (`link.calls_without_endpoint`, `link.endpoints_without_caller`,
`link.almost`, `link.module_without_rewrite`) считаются, только когда
в области есть и .NET, и фронт. Факты одного фронта (`link.calls_invisible`,
`link.calls_unresolved`, `link.registry_unresolved`) — всегда.

## Инструменты

- `setup_candidates` (`docpipe setup candidates`), виды `http-wrappers`,
  `url-builders`, `registry-calls`;
- `setup_link` (`docpipe setup link`) — кластеры шва: `category`, `by`;
  у `calls_without_endpoint` по `module` — подсказка `suggested_rewrite`;
- `setup_status` — находки `link.*`, кластеры те же, что у `setup_link`;
- `setup_recon` — `projects.fronts[].proxy_configs`, `projects.proxy_files`;
- `setup_explain` (`docpipe setup explain`) — `unresolved_reasons` файла.

## Шаги

Порядок важен: эндпоинт бывает «без вызывающего» потому, что вызов
фронта невидим или не восстановлен.

1. **Невидимые вызовы** (`link.calls_invisible`) — `setup_candidates`,
   `kind: http-wrappers`. По каждой группе открой объявление метода:
   - в теле — вызов `HttpClient` (он же в `unresolved_calls` с причиной
     «значение переменной — параметр функции») — это обёртка: пишешь
     `web.http_wrappers` сам, позиция адреса — из `positions` (`1` у
     `getVersioned(this.http, url)`: первым идёт сам `HttpClient`, `0.url` —
     поле `url` первого аргумента), метод — `http_method`: `arg`, `field`,
     `from_name` или `fixed`, ровно один способ;
   - HTTP в теле нет (`window.open`, `url.startsWith`, `req.clone`,
     `form.patchValue`) — вопрос «Обёртка?»; ответ «не обёртка» —
     `web.not_wrappers` с причиной человека;
   - первыми по числу идут тесты (`httpMock.expectOne` в `*.spec.ts`) —
     это вопрос `exclude` с причиной человека, а не обёртки.
2. **Построители** — `setup_candidates`, `kind: url-builders`: группа,
   результат которой стал адресом (`apiUrl.buildUrl('/api/apps')`). Пишешь
   `web.url_builders` с `path.arg` из `positions`. После записи — снова
   `http-wrappers`: построитель меняет, какие вызовы похожи на адрес.
3. **Невосстановленные** (`link.calls_unresolved`) — `setup_link`,
   `category: calls_unresolved`, `by: reason`. «Параметр функции» в теле
   обёртки и «вызов X.y(…)» закрываются шагами 1–2. Адрес из данных
   (гипермедиа `buildUrl(link.href)`, `@Input`, сборка окружения) — вопрос
   «Адрес»: `link.unresolvable` с глобом **файла** и причиной человека.
4. **Префиксы прокси** (`link.module_without_rewrite`,
   `link.calls_without_endpoint`) — `setup_link`, `by: module`:
   - `suggested_rewrite` с `would_link` больше нуля и `would_unlink` 0,
     и тот же срез виден в прокси (`proxy_configs`, `pathRewrite`) или
     интерцепторе — пишешь `web.url_rewrite` сам, `reason` — файл прокси;
   - подсказки нет, а прокси модуля адрес не меняет — пустая запись
     модуля (`strip_prefix` и `add_prefix` пусты): «проверено»;
   - у конфигураций прокси разный `pathRewrite` или его не прочесть —
     вопрос «Прокси»: какая конфигурация боевая.

   После записи — `setup_link`, `category: external_targets`: маска
   `route`, записанная до правки `url_rewrite`, перестаёт совпадать.
5. **Вызовы без эндпоинта**, оставшиеся после 4 — вопрос «Без бэка»:
   внешний хост, внешний путь, бэк вне `roots`, ошибка в коде.
6. **«Почти»** (`link.almost`) — разница в голове адреса, видная в прокси,
   закрывается `web.url_rewrite`; иначе вопрос «Почти». Ответ «та же
   точка, параметр передан иначе» — пробел формата: скажи вслух.
7. **Реестр** — `setup_candidates`, `kind: registry-calls`: маршрут,
   у которого смысл задаёт поле тела или query. Пишешь
   `web.registry_calls` с маршрутом **как напечатан** (после
   `url_rewrite`). Остаток `link.registry_unresolved` с различителем из
   данных (`listInnerName=${type}`) — пробел формата: скажи вслух.
8. **Эндпоинты без вызывающего** — последними. `setup_link`,
   `category: endpoints_without_caller`, `by: controller`, вопрос
   «Кто зовёт»: зовут извне — `link.external_callers` с причиной
   человека; служебный — та же запись с `document: false`; фронт зовёт,
   но вызов не виден — назад к шагу 1.

## Контрольная точка

- `setup_status` — нет находок `link.*`, кроме названных человеку пробелов
  формата; нет дефекта `link.duplicate_endpoints`;
- `setup_link` — `linked` вырос на `would_link` записанных подсказок;
  в `categories` у `calls_without_endpoint`, `calls_unresolved`,
  `endpoints_without_caller`, `almost` — нули или названные пробелы формата;
- у каждой записи `web.http_wrappers`, `web.url_builders`, секции `link`
  охват больше нуля (`coverage` в `setup_status`).

## Ловушки

- **Обёртку по имени не распознать**: `uiState.get('/settings/x')` — не
  HTTP. Решает тело метода, а не имя.
- **Позиция адреса обязательна**: «первый аргумент — адрес» у
  `getVersioned(this.http, url)` даст маршрут `this.http`.
- **`url_rewrite` переписывает и абсолютные адреса** (после среза хоста);
  маски `link.external_targets[].route` и `web.registry_calls[].route`
  пишутся в форме **после** преобразования.
- **Запись модуля с опечаткой** ни на что не ложится и протухшей не
  называется: после записи находка `link.module_without_rewrite` обязана уйти.
- **Маршрут эндпоинта — с префиксом базы**: маска `content/**` не
  совпадёт при `[Route("api")]` на базе контроллера; пиши `api/content/**`.
- **Один маршрут на много смыслов**: `api/items/query` с разным
  `listInnerName` — один эндпоинт; различитель идёт в ключ вызова, но не в
  сопоставление с эндпоинтом.
- **Эндпоинт с пустым маршрутом в связь не входит**: `GET ''` остаётся
  без пары и после верного префикса.
- **`link.unresolvable` накрывает и будущие вызовы файла**: глоб файла,
  а не каталога.
- **Счётчик `.get(` без получателя меряет не то** (`Map.get`, `form.get`):
  верь `setup_candidates`, а не поиску по тексту.

## Правка файлов

Всё — в `docpipe.yaml`: обёртки, построители, префиксы, реестр — внутри
секции `web:`; решения о концах без пары — секция `link:` (в нейтральном
наборе закомментирована целиком: раскомментируй блок). Позитивные записи
(`http_wrappers`, `url_builders`, `url_rewrite`, `registry_calls`) — твои,
`reason` — адрес доказательства. Записи «не берём» (`not_wrappers`,
`link`) — с причиной человека.

```yaml
# file: docpipe.yaml
web:
  http_wrappers:
    - receiver: "HTTP"
      method_regex: "(get|post|put|delete)Versioned"
      url: {arg: 1}
      http_method: {from_name: true}
      reason: "setup candidates http-wrappers: 31 вызов, тело src/app/framework/http-extensions.ts:16"
    - receiver: "rest"
      method: "request"
      url: {arg: 0, field: "url"}
      http_method: {arg: 0, field: "method"}
      reason: "setup candidates http-wrappers: позиция 0.url, тело rest.service.ts:12"
  url_builders:
    - receiver: "apiUrl"
      method: "buildUrl"
      path: {arg: 0}
      reason: "setup candidates url-builders: 171 адрес, путь в аргументе 0"
  not_wrappers:
    - receiver: "window"
      method: "open"
      reason: "<слова человека>"
  url_rewrite:
    - module: "app"
      strip_prefix: "api"
      add_prefix: ""
      reason: "proxy.conf.json срезает /api; подсказка setup link"
  registry_calls:
    - route: "api/items/query"
      discriminator: {in: body, name: "listInnerName"}
      reason: "setup candidates registry-calls: 14 значений поля тела"
link:
  external_targets:
    - host: "ext.example.org"
      document: false
      reason: "<слова человека>"
  external_callers:
    - route: "api/content/**"
      reason: "<слова человека>"
  unresolvable:
    - path: "src/app/services/links.service.ts"
      reason: "<слова человека>"
```
