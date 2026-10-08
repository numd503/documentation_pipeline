# Карта настройки: ключ, читатель, база, проверка, источник

Один справочник на вопрос «что будет, если поправить этот ключ»: кто его
читает, от чего разрешается путь, чем значение проверяется, откуда оно
берётся и что ломается молча. Читают его скилл настройки (S29), каталог
вопросов интервью (S28) и сводка `setup status` (S24); человек — когда
правит файл настройки руками. Обоснований здесь нет — они в
[`configuration.md`](configuration.md) и в планах.

**Карта не отстаёт от кода по построению.** `tests/test_setup_map.py` держит
её полной в обе стороны: ключ модели или константы загрузчика без строки
здесь — красный тест, строка без ключа в коде — тоже. Ключи кода
перечисляет `tests/setup_map_support.py`: обход `model_fields` для моделей
и константы допустимых ключей S02 для файлов, которые грузятся словарём.

> **Колонка «Находка»** — код находки `setup status` (S24), которой отвечает
> молчаливое место ключа: опечатка или пропуск в нём видны в отчёте этой
> находкой, а не только разбором вывода. Код — из `FINDING_CODES`
> (`docpipe/setup/status.py`, справочник — [`setup.md`](setup.md)), «—» —
> находки нет: место ловит только охват решения (`coverage`, ноль у записи,
> не решившей ничего) или ничто. Что в ячейке нет кода вне `FINDING_CODES`,
> держит `tests/test_setup_map.py`.

Сверено с кодом 08.10.2026 (ветка `integ`: S01–S08, S10–S17, S22, S26); колонка «Находка» — по S24.

## Как читать таблицы

| Колонка | Что в ней |
|---|---|
| Ключ | путь через точку; элемент списка — `[]` (`web.url_rewrite[].strip_prefix`). Запись «строка или объект» (вторая форма S22) — две строки: сам ключ (короткая форма) и поля записи |
| Тип и умолчание | тип значения и что будет без ключа |
| Кто читает | `модуль:функция` от `docpipe/`, в скобках — команды |
| База пути | **`--root`** — корень репозитория команды; **вход** — текущий каталог, затем каталог `docpipe.yaml` (`config:resolve_input`, первый существующий); **цель** — только текущий каталог; «—» — не путь |
| Чем проверяется | **загрузка** — отказ при чтении файла, код 2; **`config check`** — код проблемы отчёта; **прогон** — отказ команды; **тип** — только тип pydantic |
| Откуда значение | **код** — выводится детерминированно (умолчание, установщик, команда); **разведка** — предлагает `recon`, `setup candidates` или чтение кода, подтверждает человек; **человек** — из кода не выводится |
| Что ломается молча | что случится при ошибке без единого сообщения. «Было: …, закрыто S0x» — место закрыто кодом; «открыто» — нет |
| Находка | код находки `setup status` (S24) из `FINDING_CODES`, несколько — через запятую; «—» — находки нет |

## Общее для всех файлов

Молчаливые места не одного ключа, а самого чтения YAML. Ни одно не закрыто.

- **Повтор ключа — действует последний, молча.** `yaml.safe_load` не
  отвергает дубли, и правка, вписанная в первое из двух мест, не применяется
  никогда. Сейчас так устроен сам `docpipe.example.yaml`: `di_methods`
  в нём дважды (оба `[]`, поэтому без последствий — до первой правки).
- **Скалярный ключ с одним комментарием под ним** (`rules:`, `templates:`,
  `out:`) — `None`, ключ выбрасывается, срабатывает умолчание. У списков
  то же закрыто S02 отказом; у скаляров — пункт [бэклога](backlog.md).
- **Синтаксическая ошибка YAML** — трассировка и код 1 вместо строки
  и кода 2: `yaml.YAMLError` не наследует `ValueError` (бэклог).
- **Флаг команды важнее ключа**: `--rules`, `--pages`, `--ownership`,
  `--templates`, `--out`, `--registries`, `--business-root`. Прогон
  с флагом не подтверждает, что ключ верен.

## `docpipe.yaml`

### Обход и область (шаг 1)

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `roots` | список путей; `["."]` | `emit:run` → `discovery:discover` (scan, symbols, setup candidates di-methods и dispatch-interfaces); `configcheck:check_config` | `--root` | загрузка: абсолютный путь, `..`, `\` и пустой список — отказ; `config check`: `root-missing` | разведка (`recon`: полный список проектов, S10) | было: каталога нет — ноль файлов без сообщения, закрыто S04 (`root-missing`). Открыто: `graph build` ключ не читает — движок разбирает весь `--root`, и код вне `roots` есть в графе, но не в манифесте | `config.problems` |
| `enrolled` | список; запись — глоб или объект; `["**"]` (умолчание решением не считается) | `config:scope_of` ← `tree:module_scopes` (`emit:run` до разбора, `tree:_apply_config`): scan, symbols, setup candidates | глоб по пути `.csproj` от `--root` | загрузка: пустой список — отказ; совпадение модуля с явным `enrolled` и с `not_enrolled` — отказ прогона, код 2 | человек (область); перечень проектов — разведка | было: `enrolled:` с одними комментариями давал `["**"]` — закрыто S02. Открыто: опечатка в глобе при явном `enrolled` делает модуль `undecided` — в манифесте `enrolled: false`, в `scan --stats` символы идут в `not_enrolled`, строки «модуль без решения» нет до S24. На шаг `web` не действует | `scope.module_undecided` |
| `enrolled[].glob` | строка | как `enrolled`, только через `config:DocpipeConfig.enrolled_globs` | как `enrolled` | тип; опечатка в ключе записи — одна ошибка про этот ключ (S22) | человек | поле читается только через свойство: запись-объект там, где ждут строку, ломается не там (сторож — `test_config_reasons.py`) | `scope.module_undecided` |
| `enrolled[].reason` | строка; `""` | никто: на разбор, кэш и манифест не влияет | — | тип | человек | — | — |
| `not_enrolled[].glob` | строка; списка нет по умолчанию; короткой формы нет | `config:scope_of` (`not_enrolled_globs`) ← `tree:module_scopes` | глоб по пути `.csproj` | загрузка: строка вместо объекта — отказ с подсказкой; совпадение с явным `enrolled` — отказ прогона (S22) | человек | было: «решили не брать» и «ещё не смотрели» в манифесте неразличимы — различает `config:scope_of` с S22. Шаблон не вырезает кусок из `enrolled`: `src/Samples/**` при `enrolled: ["src/**"]` — отказ, а не вырез | `scope.module_undecided` |
| `not_enrolled[].reason` | строка, обязательна | никто, кроме человека; покажут S23 и S24 | — | загрузка: нет ключа или пустая строка — отказ (S22) | человек (агент причину не дописывает, S28) | — | — |
| `exclude` | список; запись — глоб или объект; `[]`, складывается с `emit.DEFAULT_EXCLUDE` | `emit:exclude_globs` ← `emit:run` (scan), `web/tree:run` (web scan), `cli:graph_build` (фильтр узлов движка) | глоб по репо-относительному пути файла | загрузка: пустой список — отказ | разведка (вендоренный, сгенерированный, чужой код), человек | шаблон без `/**` (`"docs"`) совпадает с каталогом, а не с файлами под ним, и выглядит работающим; исключённый файл не даёт символов, и наследование через него рвётся. Было: `graph build` получал один `exclude` без встроенного списка (`*.g.cs` в графе) — закрыто S08 | — |
| `exclude[].glob` | строка | `emit:exclude_globs` (`exclude_patterns`) | как `exclude` | тип | как `exclude` | как у `enrolled[].glob`: только через свойство | — |
| `exclude[].reason` | строка; `""` | никто: на обход не влияет | — | тип | человек | — | — |
| `domains` | словарь «глоб `.csproj` → домен»; `{}` | `tree:_domain_of` ← `tree:_apply_config` (scan, symbols, setup candidates) | глоб по пути `.csproj` | тип (`dict[str, str]`) | человек | побеждает первый совпавший в порядке ключей, а не в порядке файла; опечатка в глобе — домен равен имени модуля, молча. Домен идёт в `{{ domain }}`, в предикат владения `domain` и в манифест. На шаг `web` не действует (`web/modules:_build`) | — |
| `di_methods` | список имён; запись — имя или объект; `[]` | `emit:parse_options` (ключ кэша разбора), `emit:run` → `dotnet/di` (регистрации), `setup/candidates:di_method_candidates` (`configured`) | — | загрузка: пустой список — отказ | разведка: `setup candidates di-methods` (S11) | без ключа обёртки регистрации дают ноль записей (squidex: 67 регистраций вместо 421), ошибкой это не выглядит — находку даёт `setup candidates`. Лямбда-форма (`AddX(_ => new T())`) теряется и с ключом | — |
| `di_methods[].name` | строка | как `di_methods` (`di_method_names`) | — | тип | разведка | смена имени сбрасывает кэш разбора — так и задумано | — |
| `di_methods[].reason` | строка; `""` | никто: в ключ кэша не входит | — | тип | человек | — | — |
| `dispatch_interfaces` | список имён; запись — имя или объект; `[]` | `emit:run` → `emit:collect_dispatch`; `setup/candidates:dispatch_candidates` | — | загрузка: пустой список — отказ | разведка: `setup candidates dispatch-interfaces` (S12) | без ключа нет `dispatch_handlers`, `dispatch_sends` и рёбер диспетчеризации, ошибкой не выглядит. Открыто: тип запроса с квалификатором или дженериком — обработчик есть, отправок ноль (бэклог) | — |
| `dispatch_interfaces[].name` | строка — имя без квалификатора | как `dispatch_interfaces` (`dispatch_interface_names`) | — | тип | разведка | `MediatR.IRequestHandler` не совпадёт ни с чем: сверяется голова прямой базы без пространства имён | — |
| `dispatch_interfaces[].reason` | строка; `""` | никто | — | тип | человек | — | — |

### Правила и артефакты шага 1

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `rules` | путь к файлу правил; `"rules/rules.yaml"` | `cli:scan`, `cli:symbols` (`--lang cs`), `setup/candidates:_scan` → `classify:load_ruleset` (секция `dotnet`) | вход | `config check`: `input-missing`; загрузка правил — отказ, код 2 | код (поставка кладёт `rules.yaml` рядом с настройкой) | файл бывает общим с `web.rules` — секцию выбирает команда, а не файл | — |
| `out` | путь; `"artifacts/doc-tree.json"` | `cli:scan` (флаг `--out` важнее) | цель | `config check` показывает каталог; `placeholder-left` | код (установщик: `@CONFIG_DIR@/artifacts/…`) | было: ключ не читался, манифест уезжал в `artifacts/` текущего каталога — закрыто до плана. Читатели манифеста (`materialize`, `docs *`, `graph build --manifest`) берут путь аргументом, а не из `out` | `config.problems` |
| `cache_dir` | каталог; `".docpipe/cache"` | `cli:scan`, `cli:symbols`, `cli:web_scan`, `setup/candidates:_scan`, `setup/candidates:_web_scan` → `cache:ParseCache` (`parse.sqlite`, `parse-web.sqlite`) | `--root`; абсолютный выигрывает склейку | загрузка: `..` и `\` — отказ, абсолютный разрешён; `config check` показывает | код (установщик: `@CACHE_DIR@/parse`) | открыто: общий для нескольких репозиториев кэш верен не во всём — запись ищется по паре «путь и хэш», полный прогон удаляет записи чужих путей (`prune`), разные `di_methods` очищают его целиком, а `--scope` берёт файлы вне скоупа по одному пути, без хэша, — из чужого репозитория с тем же относительным путём. Было: умолчание `--cache-dir` одно на машину — с S30 своё у каждого репозитория; общим кэш остаётся при одном явном `--cache-dir` или одноимённых каталогах репозиториев (`configuration.md`, «Три базы отсчёта») | — |

### Шаг 2 и очередь

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `templates` | каталог скелетов; `"templates"` | `step2:prepare` → `materialize/template:load_templates` (materialize, docs status, explain, accept, adopt, worklist); `cli:business_new` (подкаталог `business/`) | вход (`business new` перебирает обе ступени) | `config check`: `input-missing`; загрузка скелетов — отказ, код 2 | код (установщик кладёт `templates/`) | имени `template` без своего скелета достаётся `default.md` — прогон перечисляет подстановки, но не падает. Обход не рекурсивный: `examples/` и `business/` скелетами шага 2 не являются | `docs.unavailable` |
| `ownership` | путь или пусто; `null` | `step2:prepare` (план и бизнес-ссылки), `cli:docs_owners`, `cli:anchors_explain`, `cli:_business_context` → `materialize/ownership:load_ownership` | вход | `config check`: `input-missing`; загрузка — отказ, код 2 | человек (команды); кластеры для вопроса — разведка | без файла у всех документов `team: null` — видимый факт. Было: бизнес-ссылки читали владение мимо `--ownership` и глотали ошибку разбора — закрыто S03 | `owners.not_configured` |
| `docs_root` | каталог; `"docs"` | `config:modules_root` ← `tree:build_nodes`; `config:web_modules_root` ← `web/tree:build_nodes`; `step2:prepare` (`scan_docs`, сверка префикса); `cli:worklist` | `--root` | загрузка: абсолютный, `..`, `\` — отказ; расхождение с манифестом — отказ шага 2 | человек (как читают дерево) | замерзает в манифесте на шаге 1; смена без `scan` роняет шаг 2 сверкой префикса. «Слишком узкий» невозможен структурно: префикс `doc_path` начинается с него | `docs.unavailable` |
| `modules_dir` | сегмент внутри `docs_root`; `"modules"` | `config:modules_root` ← `tree:build_nodes`, `step2:prepare`, `cli:worklist`, `configcheck:check_config` | внутри `docs_root`, не путь целиком | загрузка: как `docs_root`; сверка префикса — отказ шага 2 | человек | было: одним ключом `modules_root` «пишем» и «ищем» можно было развести — пара держит это структурно. Смена меняет `doc_path` всех узлов бэка | `docs.unavailable` |
| `doc_layout` | `kind-first` или `module-first`; `kind-first` | `tree:build_nodes`, `web/tree:build_nodes`, `step2:prepare` (сверка раскладки) | — | загрузка: одно из двух; каталоги узлов манифеста против обеих раскладок — отказ шага 2 | человек | было: смена без `scan` проходила (префикс тот же) — закрыто S03. На коллизии не влияет. Пересобирать — полным `scan`: скоуп-прогон даёт смешанный манифест | `docs.unavailable` |
| `docs_scan_exclude` | список глобов; `[]`, складывается со встроенными `.venv`, `site-packages`, `node_modules`, `.git` | `step2:prepare` → `materialize/plan:scan_docs` и `PlanOptions`; `cli:docs_explain` | глоб по пути от `--root` | загрузка: пустой список — отказ; шаблон, накрывший `doc_path` манифеста, — блокирующая ошибка шага 2 | код (установщик: `@CONFIG_DIR@/**`) | было: накрытое дерево давало вечный `missing` и перезапись каждым прогоном — закрыто до плана. Существующий файл, выпавший из обхода, — `broken`, а не `missing` (`shadowed_docs`) | `docs.shadowed` |
| `worklist` | путь; `"artifacts/doc-worklist.json"` | `cli:worklist` (флаг `--out` важнее) | цель | `config check` показывает; `placeholder-left` | код (установщик) | префикс очереди берётся из конфигурации и сверяется с манифестом — расхождение роняет прогон | — |

### Бизнес-слой

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `registries` | путь или пусто; `null` | `cli:_registries_path` (anchors list, explain, which; business *); `step2:_business_links` (шаг 2, мягко) | вход | `config check`: `input-missing`; `registry/config:load_registries` — отказ у `anchors` и `business` | разведка (где объявлены точки входа), человек | шаг 2 читает мягко: ошибка — предупреждение «бизнес-ссылки:», код прежний (было: глоталась — закрыто S03). Без ключа `anchors` и `business` отказывают | `load.errors` |
| `business_root` | каталог; `"business"` | `cli:business_new`, `cli:_business_context`, `cli:graph_coverage`, `step2:_business_links` | `--root` | загрузка: абсолютный, `..`, `\` — отказ | код (установщик: `@CONFIG_DIR@/business`) | смена без переноса файлов — пустой каталог: линт печатает непокрытые точки входа, отказа нет | — |

### Секция `web`

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `web.roots` | список; запись — путь или объект; `["."]` | `web/tree:run` → `discovery:discover` (web scan, symbols `--lang ts`, setup candidates features и registry-calls); `configcheck:check_config` | `--root` | загрузка: абсолютный, `..`, `\` в обеих формах и пустой список — отказ; `config check`: `root-missing` | разведка (`recon`: полный список фронтов, S10) | при нескольких фронтах `.` обходит все, и чужие модули попадают в манифест без ошибки. Область фронта задаётся здесь: `enrolled` и `domains` на шаг `web` не действуют | `scope.front_undecided` |
| `web.roots[].path` | строка | `config:WebConfig.root_paths` | `--root` | загрузка: как `web.roots` | разведка | только через `root_paths` | `scope.front_undecided` |
| `web.roots[].reason` | строка; `""` | никто | — | тип | человек | — | — |
| `web.rules` | путь к файлу правил; `"rules/rules.yaml"` | `cli:web_scan`, `cli:symbols` (`--lang ts`), `setup/candidates:_web_scan` → `classify:load_ruleset` (секция `web`) | вход | `config check`: `input-missing`; нет секции `web:` или плоский файл — отказ с командой переноса | код | было: умолчание в бандле записано `rules/web.yaml` — справочник поправлен S08 | — |
| `web.out` | путь; `"artifacts/doc-tree.web.json"` | `cli:web_scan` (пишет); `cli:_business_context` (читает, мягко) | цель — и у читателя, второй ступени нет | `config check` показывает; `placeholder-left` | код (установщик) | `business build` из другого каталога видит «страниц ноль»; предупреждение — только если манифест лежит рядом с `docpipe.yaml` | — |
| `web.link_out` | путь; `"artifacts/web-link.json"` | `cli:web_link` | цель | `config check` показывает; `placeholder-left` | код (установщик) | — | — |
| `web.pages` | путь или пусто; `""` | `web/overrides:load_page_overrides` (web scan, symbols `--lang ts`, setup candidates features и registry-calls) | вход | `config check`: `input-missing`; названный и ненайденный — отказ прогона, код 2 | человек | было: ключ без файла давал пустые правила, `symbols --lang ts` шёл без `pages.yaml` — закрыто S02 | `load.errors` |
| `web.modules_dir` | сегмент внутри `docs_root`; `""` — та же ветка, что `modules_dir` | `config:web_modules_root` ← `web/tree:build_nodes`, `step2:prepare`, `cli:worklist`, `configcheck:check_config` | внутри `docs_root` | загрузка: как `docs_root`, если задан; сверка префикса — отказ шага 2 | человек | смена меняет `doc_path` всех узлов фронта; без `web scan` шаг 2 откажется и назовёт ключ | `docs.unavailable` |
| `web.url_rewrite[].module` | имя модуля фронта (проект `angular.json` или `project.json`) | `web/tree:_rewrite_for` (web scan); `cli:web_link` — только имена | — | загрузка: повтор модуля — отказ (S02) | разведка (имена модулей из обхода) | было: повтор — молча первая запись, закрыто S02. Открыто: имя с опечаткой ни на что не ложится и протухшим не называется — видно лишь строкой «модуль не настроен» про настоящее имя | `link.module_without_rewrite` |
| `web.url_rewrite[].strip_prefix` | строка; `""` | `route:normalize_route` ← `web/calls:build_calls` (web scan) | — | тип | разведка (`pathRewrite` из `proxy.conf`, интерцептор; подсказка пары — `setup link --by module`, S21), подтверждает человек | сравнение по сегментам: не совпал с началом адреса — адрес без изменений, и в `web link` пара «вызов без эндпоинта» плюс «эндпоинт без вызывающего». Правка без `web scan` не меняет ни одной связи | `link.calls_without_endpoint` |
| `web.url_rewrite[].add_prefix` | строка; `""` | как `strip_prefix` | — | тип | разведка | уже стоящий в начале адреса префикс не дублируется | — |
| `web.url_rewrite[].reason` | строка; `""` | никто | — | тип | человек | — | — |
| `web.registry_calls[].route` | маршрут | `web/tree:registry_calls` → `web/calls:registry_rules` (web scan, setup candidates registry-calls) | — | тип | разведка: `setup candidates registry-calls` (S13) | маршрут правила нормализуется без `url_rewrite`: записанный до преобразования (`api/items/query` при `add_prefix: /gw`) не совпадёт никогда. Открыто: два правила на один маршрут — действует последнее (бэклог) | — |
| `web.registry_calls[].discriminator.in` | `body` или `query` | `web/calls:discriminator_of` | — | загрузка: одно из двух | разведка (S13) | не то место — различителя нет, вызов уходит в `registry_unresolved` (числом в отчёте) | `link.registry_unresolved` |
| `web.registry_calls[].discriminator.name` | строка | `web/calls:discriminator_of` | — | тип | разведка (S13) | опечатка — тот же `registry_unresolved` | `link.registry_unresolved` |
| `web.registry_calls[].kind` | строка; `""` | загружается в `web/calls:RegistryCall` и больше никем не читается | — | тип | — | открыто: ключ из образцов (`kind: list`) ни на что не влияет — ни на ключ вызова, ни на манифест | — |
| `web.registry_calls[].reason` | строка; `""` | никто | — | тип | человек | — | — |
| `web.http_wrappers[].receiver` | строка, обязательна | `web/calls:wrapper_matches` ← `web/calls:build_calls` (web scan, setup candidates, setup explain); `setup/candidates:http_wrapper_candidates` (`configured`) | — | загрузка: пустой — отказ; две записи с одним получателем и `method` — отказ (S19) | разведка: `setup candidates http-wrappers` (S18) | сравнивается последний сегмент без регистра (`this.rest` = `rest` = `Rest`); запись на чужой получатель ни с чем не совпадает — вызовы остаются находкой, а у кандидата `configured: false`. Правка без `web scan` не меняет ни одной связи | `link.calls_invisible` |
| `web.http_wrappers[].method` | строка; `""`; ровно одно с `method_regex` | как `receiver` | — | загрузка: оба или ни одного — отказ | разведка | точное сравнение с регистром: `Request` не `request` | `link.calls_invisible` |
| `web.http_wrappers[].method_regex` | регулярка; `""` | как `receiver`; `web/calls:inside_wrapper` (тела) | — | загрузка: не компилируется — отказ; две записи на одном вызове — отказ прогона (`WrapperConflict`), код 2 | разведка | `re.fullmatch`, как `name_regex` правил: `get` без `.*` с `getVersioned` не совпадёт. Тело обёртки узнаётся по имени функции без получателя: функция того же имени в другом классе с адресом в том же параметре тоже уйдёт в `calls_inside_wrappers` | `link.calls_invisible` |
| `web.http_wrappers[].url.arg` | целое не меньше 0, обязательно | `web/calls:through_wrapper`, `web/calls:inside_wrapper` | — | загрузка: отрицательное — отказ | разведка: позиция у кандидата (`1`, `0.url`) | аргументы считаются без комментариев. Не та позиция — адресом становится `this.http`: вызов невосстановлен «значение переменной не восстановлено», а тело с адресом в другой позиции остаётся в `unresolved_calls` | `link.calls_unresolved` |
| `web.http_wrappers[].url.field` | строка; `""` — сам аргумент | как `url.arg` | — | тип | разведка | поле читается только у объектного литерала: `request(config)` с переменной — «в объявленном аргументе обёртки нет адреса» | `link.calls_unresolved` |
| `web.http_wrappers[].http_method.arg` | целое не меньше 0 или нет; способ ровно один из `arg`, `fixed`, `from_name` | `web/calls:_wrapper_method` | — | загрузка: ни одного или два способа — отказ | разведка (тело обёртки) | значение не литерал (`link.method`) — вызов невосстановлен «метод HTTP из аргумента обёртки не восстановлен», `http_method` пуст | `link.calls_unresolved` |
| `web.http_wrappers[].http_method.field` | строка; `""` | как `http_method.arg` | — | загрузка: без `arg` — отказ | разведка | — | — |
| `web.http_wrappers[].http_method.fixed` | метод HTTP; `""` | как `http_method.arg` | — | загрузка: не метод HTTP — отказ | разведка | — | — |
| `web.http_wrappers[].http_method.from_name` | логическое; `false` | `web/calls:leading_verb` | — | тип | разведка | глагол — первое слово имени: у `upload`, `requestVersioned` его нет — невосстановлен «в имени обёртки нет глагола HTTP» | `link.calls_unresolved` |
| `web.http_wrappers[].reason` | строка; `""` | `setup/explain:_wrapper_decisions` (показывает) | — | тип | человек | — | — |
| `web.url_builders[].receiver` | строка, обязательна | `web/calls:builder_for` ← `web/calls:with_builder` (web scan, setup candidates, setup explain); `setup/candidates:url_builder_candidates` (`configured`) | — | загрузка: пустой — отказ; две записи на один вызов — отказ (S19) | разведка: `setup candidates url-builders` (S18) | как у `web.http_wrappers[].receiver` | `link.calls_unresolved` |
| `web.url_builders[].method` | строка, обязательна | как `receiver` | — | тип | разведка | точное сравнение с регистром | `link.calls_unresolved` |
| `web.url_builders[].path.arg` | целое не меньше 0, обязательно | `web/calls:with_builder` | — | загрузка: отрицательное — отказ | разведка: `positions` у кандидата | путь из данных (`buildUrl(link.href)`) — невосстановлен с причиной и выражением пути, `via` — построитель. Шаблон с построителем внутри (`` `${this.apiUrl.buildUrl(x)}${q}` ``) запись не видит: «база в начале шаблона не восстановлена» | `link.calls_unresolved` |
| `web.url_builders[].path.field` | строка; `""` | как `path.arg` | — | тип | разведка | — | — |
| `web.url_builders[].reason` | строка; `""` | `setup/explain:_wrapper_decisions` (показывает) | — | тип | человек | — | — |

### Реестр графа: `arch` и адаптеры

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `arch` | путь или пусто; `null` | `cli:_arch_path` (arch validate, arch status), `arch/collect:collect_configured` (arch records), `arch/collect:registry_for_build` (graph build) | вход | `config check`: `input-missing`; `graph build`: названный и ненайденный — отказ, код 2 (S03); структура — `arch validate` | разведка (скилл `recon`, черновик под `--draft`), подтверждает человек | было: выдуманный путь давал пустой реестр в `graph build` — закрыто S03. `arch records` отсутствующий файл по-прежнему считает пустым (ноль записей из файла, без отказа) | — |
| `arch_adapters[].id` | строка | `arch/collect:adapter_specs` — подпись счётчиков и ошибок | — | загрузка: повтор — отказ (S02) | человек | было: два одинаковых `id` неразличимы в отчёте — закрыто S02 | — |
| `arch_adapters[].adapter` | имя из `arch/adapters:ADAPTERS` (`registries`, `python_code`) | `arch/adapters:run_adapter` | — | прогон: неизвестное имя — отказ (`graph build` — код 2, `arch records` — находка); `config check` такой адаптер пропускает | разведка | — | — |
| `arch_adapters[].options` | словарь параметров адаптера; `{}` | адаптер: `arch/adapters/base:unknown_options`, `arch/adapters/base:require` | у каждого параметра своя | прогон: неизвестный или недостающий параметр — ошибка адаптера | — | — | — |
| `arch_adapters[].options.spec` | путь к описанию реестров (адаптер `registries`), обязателен | `arch/adapters/declared:from_registries` → `registry/config:load_registries` | вход | `config check`: `adapter-input-missing` (S04) | разведка | обычно тот же файл, что `registries` бизнес-слоя, — и это законно, но словари видов у них разные (см. `options.kinds`) | — |
| `arch_adapters[].options.kinds` | словарь «вид записи реестра → `вид:подвид`»; поверх `declared.DEFAULT_KINDS` | `arch/adapters/declared:from_registries` | — | прогон: не словарь — отказ; вид без пары — ошибка «не отображён», записи пропущены, `graph build` — код 2 | разведка | пара, добавленная сюда, не попадает в `business/resolve.REGISTRY_KIND`, и наоборот | — |
| `arch_adapters[].options.path` | путь к модулю Python (адаптер `python_code`), обязателен | `arch/adapters/code:from_python_code` | `--root` | `config check`: `adapter-input-missing` | разведка | файл есть, регистраций ноль — ошибка адаптера (произносится вслух) | — |
| `arch_adapters[].options.variable` | имя переменной-таблицы; `""` — любая | `arch/adapters/code:from_python_code` | — | тип не проверяется | разведка | без него пары «строка → имя» берутся из **любого** присваивания модуля: словарь настроек даёт ложные точки входа | — |
| `arch_adapters[].options.call` | имя функции регистрации; `""` — не искать | `arch/adapters/code:from_python_code` | — | — | разведка | сверяется с концом точечного имени (`x.register`); динамическая регистрация не видна — ноль записей и ошибка | — |
| `arch_adapters[].options.entry_kind` | вид точки входа; `service` | `arch/adapters/code:from_python_code` | — | прогон: вид не из словаря `entry_kind` — ошибка адаптера | разведка | — | — |

### Секция `graph`

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `graph.engine_path` | путь к бинарю движка; `""`, умолчания нет | `cli:graph_build` → `graph/engine:Engine`; `configcheck:_check_engine` | текущий каталог, `~` разворачивается; второй ступени нет | `graph build`: пусто — отказ, версия по `--version` не 0.6.0 — отказ; `config check`: `engine-missing`, `placeholder-left` | код (установщик: `@ENGINE@` из `--engine`) | относительный путь из `--engine` записывается как есть и разрешается от каталога, откуда зовут `graph build`, — ловит `engine-missing` | `config.problems` |
| `graph.engine_sha256` | sha256; `""` — закреплённая в мосте | `cli:graph_build` | — | несовпадение — предупреждение, а не отказ; фактическая сумма — в паспорте индекса | код; своя сборка — человек по `graph info` | предупреждение живёт один прогон — ответ хранит паспорт (`graph info`) | — |
| `graph.mode` | `fast`, `moderate` или `full`; `fast` | `cli:graph_build` → `graph/engine:Engine` | — | загрузка: одно из трёх | код | от режима зависит собственный список пропусков движка: в `fast` и `moderate` молча не разбираются `migrations`, `generated`, `docs`, `samples` и ещё десятки имён, а мост знает пять (бэклог) | — |
| `graph.out` | путь; `"artifacts/graph.db"` | `cli:graph_build` (пишет); `cli:_open_index` (graph report, health, reaches, affects, path, resolve, eval, coverage, pr-check), `cli:graph_serve`, `cli:graph_info`, `cli:graph_entrypoints` | цель — и у читателей | `config check` показывает; `placeholder-left` | код (установщик) | MCP-сервер ищет индекс от своего `cwd` — его ставит установщик (корень продукта в `.gigacode/settings.json`) | — |
| `graph.cache_dir` | каталог; `".docpipe/engine-cache"` | `cli:graph_build` → `graph/engine:Engine.index` — удаляет каталог перед каждой сборкой | текущий каталог; абсолютный разрешён | загрузка: `..`, `\`, `.` и пусто — отказ (S02) | код (установщик: `@CACHE_DIR@/engine`) | было: валидатора не было при удалении каталога целиком — закрыто S02. Было: общий `@CACHE_DIR@` двух репозиториев по умолчанию — параллельные сборки удаляли кэш друг друга; умолчание своё у репозитория с S30, открыто — при одном явном `--cache-dir` | — |

### Секция `link`

Решения человека о концах шва без пары (S20). Читает `web link`, разбор от
секции не зависит — правка не требует повторного `web scan`. Короткой формы
у записей нет; сработала первая совпавшая запись в порядке файла.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `link.external_targets[].host` | маска хоста без регистра; `""` — ровно одно из `host` и `route` | `config:LinkConfig.target_for` ← `web/link:build_report` (web link); `setup/explain:_calls` (setup explain) | — | загрузка: оба или ни одного из `host`/`route` — отказ; `/` или `:` в маске (схема, порт, путь) — отказ; повтор условия — отказ | разведка: хост у вызова без эндпоинта (`calls_without_endpoint[].host` в `web link`), подтверждает человек | действует только на вызов без эндпоинта: вызов, «почти» совпавший с эндпоинтом, остаётся связью. У относительного адреса хоста нет — маска его не накроет | `link.calls_without_endpoint` |
| `link.external_targets[].route` | маска маршрута ключа вызова (`matches_glob`: `*` пересекает `/`); `""` | `config:route_pattern` → `config:ExternalTarget.matches` | — | загрузка: как `host`; пустая после нормализации — отказ | разведка: маршрут вызова без эндпоинта в `web link` | маска сравнивается с ключом **после** `web.url_rewrite`: записанная до преобразования не совпадёт никогда. Нормализует её `config:route_pattern`, а не `route:normalize_route` — та отрезала бы маску по `?` (знак глоба) | `link.calls_without_endpoint` |
| `link.external_targets[].reason` | строка, обязательна | `web/link:LinkDecision` (web link, `decision.reason`); `setup/explain` | — | загрузка: нет ключа, пустая строка или строка вместо записи — отказ (S20) | человек (агент причину не дописывает, S28) | — | — |
| `link.external_targets[].document` | булево; `false` | `web/link:ExternalCall` (web link) | — | тип | человек | в документы не проецируется — пункт бэклога «Фронт↔бэк в документах» | — |
| `link.external_callers[].route` | маска маршрута эндпоинта в форме ключа (`content/**`, `content/{app}/**`), обязательна | `config:LinkConfig.caller_for` ← `web/link:build_report` (web link); `setup/explain:_dotnet` (setup explain) | — | загрузка: пусто — отказ; повтор пары «метод + маршрут» — отказ | разведка: эндпоинты без вызывающего в `web link` (публичный API, интеграции), подтверждает человек | действует только на эндпоинт без вызывающего. Маршрут эндпоинта — с префиксом базы (S17): маска без `api/` при `[Route("api")]` на базе не совпадёт | `link.endpoints_without_caller` |
| `link.external_callers[].http_method` | глагол; `""` — любой | `config:ExternalCaller.matches` | — | загрузка: не из `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS` — отказ | разведка | эндпоинт `*` (`[Route]` без глагола) совпадает с правилом любого метода | — |
| `link.external_callers[].reason` | строка, обязательна | `web/link:LinkDecision`; `setup/explain` | — | загрузка: как у `external_targets[].reason` | человек | — | — |
| `link.external_callers[].document` | булево; `true` | `web/link:ExternalEndpoint` (web link) | — | тип | человек | как `external_targets[].document` | — |
| `link.unresolvable[].path` | глоб файла вызова, обязателен | `config:LinkConfig.unresolvable_for` ← `web/link:build_report` (web link); `setup/explain:_calls` (setup explain) | глоб по пути от `--root` | загрузка: пусто, абсолютный, `..`, `\` — отказ; повтор — отказ | разведка: `unresolved_calls` манифеста фронта с причиной (S18), подтверждает человек | накрывает **все** невосстановленные вызовы файла, и будущие тоже: новый вызов, который можно восстановить обёрткой или построителем, уйдёт в `declared_unresolvable` молча. Восстановленные вызовы файла не трогает | — |
| `link.unresolvable[].reason` | строка, обязательна | `web/link:LinkDecision`; `setup/explain` | — | загрузка: как у `external_targets[].reason` | человек | — | — |

## `rules.yaml`

Файл секционный: секцию называет команда (`dotnet` — scan, symbols
`--lang cs`, setup candidates di-methods и dispatch-interfaces; `web` —
web scan, symbols `--lang ts`, setup candidates features и registry-calls).
Читает всё `classify:load_ruleset`, применяют `classify:exclusion_of`
и `classify:classify`. Пути и базы — только у предиката `path_glob`.

### Верх файла и секция `dotnet`

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `version` | строка; `"1"` | `classify:load_ruleset` → `Ruleset.version`, дальше не читается | — | загрузка: `version` внутри секции — отказ с подсказкой | код | метка формата: на результат не влияет | — |
| `dotnet.ruleset_version` | строка, обязательна | `classify:load_ruleset` → манифест (`ruleset_version`), `business_hash` | — | загрузка: нет — отказ | человек (поднимает при правке набора) | не поднятая после правки версия — два разных набора с одной версией в отчётах и журнале | — |
| `dotnet.exclude.require_public` | логическое; `false` | `classify:exclusion_of` (правило `exclude.require_public`, приоритет −4) | — | тип не проверяется: значение приводится через `bool()` | человек | открыто: строка `"false"` даёт `true` — отсеивается всё непубличное, без ошибки | — |
| `dotnet.exclude.path_glob` | список глобов (краткая форма) → правило `exclude.path_glob`, приоритет −1 | `classify:_load_exclusion` | путь источника символа | загрузка: правило проверяется после разворота | разведка, человек | открыто: строка вместо списка разбирается посимвольно (`"**/Tests/**"` → `*`, `*`, `/`, `T`…) и не отсеивает почти ничего, без ошибки. Причина у краткой формы общая | `dotnet.undecided` |
| `dotnet.exclude.name_regex` | список регулярок → `exclude.name_regex`, приоритет −2 | `classify:_load_exclusion` | — | загрузка: битая регулярка — отказ | разведка | `fullmatch`: `Dto` не ловит `OrderDto`. Строка вместо списка — посимвольно (как `path_glob`) | `dotnet.undecided` |
| `dotnet.exclude.type_kind_deny` | список видов типа → `exclude.type_kind`, приоритет −3 | `classify:_load_exclusion` | — | значения не проверяются | человек | строка вместо списка — посимвольно, не отсеивает ничего; вид не из словаря (`Record`) — тоже | `dotnet.undecided` |
| `dotnet.exclude.rules[].id` | строка, обязательна, уникальна | `classify:_load_exclusion`; причины отсева в `scan --stats` и `symbols` | — | загрузка: нет, повтор или префикс `exclude.` — отказ | агент, человек | атрибуция — по `priority`, затем по `id`; порядок строк в файле на неё не влияет | — |
| `dotnet.exclude.rules[].reason` | строка, обязательна | `scan --stats` (разбивка по причинам), `symbols` | — | загрузка: нет ключа — отказ | человек (агент причину не дописывает, S28) | открыто: пустая строка принимается — проверяется ключ, а не текст; у `not_enrolled` и в `pages.yaml` пустая причина — отказ | — |
| `dotnet.exclude.rules[].priority` | целое; `0` | `ruleset:pick_winner` | — | загрузка: не число — отказ | агент | правило человека (0) забирает атрибуцию у краткой формы (−1…−4) | — |
| `dotnet.exclude.rules[].when` | условие (предикаты ниже) | `ruleset:evaluate` ← `classify:_excludes` | — | загрузка: `ruleset:validate_condition` | разведка, агент | — | — |
| `dotnet.exclude.rules[].unless` | условие; нет — вырезов нет | `classify:_excludes` (только при истинном `when`) | — | загрузка: пустой — отказ; проверка как у `when` | агент, человек | было: опечатка `unles` молча превращала вырез в отсев всего каталога — закрыто S02 | — |
| `dotnet.rules[].id` | строка, обязательна, уникальна | `classify:classify` (`matched_rules`, `winner`) | — | загрузка: нет или повтор — отказ | агент | — | — |
| `dotnet.rules[].kind` | вид сущности | манифест (`kind`), `doc_path` (каталог `{kind}s`), предикат владения `kind`, `{{ kind }}` | — | тип | агент (из кода), имя вида — человек | смена вида меняет `doc_path` узлов — документы переезжают с отметкой о пересмотре; правила владения по старому виду перестают совпадать (`docs owners --lint`: `dead-rule`) | `owners.unowned` |
| `dotnet.rules[].template` | имя скелета | `materialize/template:resolve_template` | файл `<templates>/<имя>.md` | загрузка скелетов (не правил) | агент | скелета нет — `default.md`; прогон перечисляет подстановки, но не падает | — |
| `dotnet.rules[].priority` | целое, обязательно | `ruleset:pick_winner` | — | загрузка: нет — отказ | агент | равный приоритет двух совпавших — победитель по меньшему `id`, молча; победителя показывает `symbols` (S07) | — |
| `dotnet.rules[].when` | условие | `ruleset:evaluate` ← `classify:classify` | — | загрузка: `ruleset:validate_condition`; `unless` здесь — отказ с подсказкой (S02) | разведка, агент | было: `unless` у правила классификации молча игнорировался — закрыто S02 | — |

### Секция `web`

Ключи те же; ниже — чем они отличаются на TypeScript.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `web.ruleset_version` | строка, обязательна | как `dotnet.ruleset_version`, в манифест фронта | — | загрузка: нет — отказ | человек | как в `dotnet` | — |
| `web.exclude.require_public` | логическое; `false` | `classify:exclusion_of` | — | как в `dotnet` | человек | `true` отсеивает весь фронт: у TypeScript нет `public` на уровне объявления — пустое дерево без ошибки | — |
| `web.exclude.path_glob` | список глобов | `classify:_load_exclusion` | путь источника символа | как в `dotnet` | разведка | строка вместо списка — посимвольно | `web.undecided` |
| `web.exclude.name_regex` | список регулярок | `classify:_load_exclusion` | — | как в `dotnet` | разведка | как в `dotnet` | `web.undecided` |
| `web.exclude.type_kind_deny` | список видов (`class`, `interface`, `enum`, `function`, `const`) | `classify:_load_exclusion` | — | значения не проверяются | человек | как в `dotnet` | `web.undecided` |
| `web.exclude.rules[].id` | строка | как в `dotnet` | — | как в `dotnet` | агент, человек | — | — |
| `web.exclude.rules[].reason` | строка, обязательна | как в `dotnet` | — | как в `dotnet` | человек | открыто: пустая строка принимается | — |
| `web.exclude.rules[].priority` | целое; `0` | `ruleset:pick_winner` | — | как в `dotnet` | агент | — | — |
| `web.exclude.rules[].when` | условие | `classify:_excludes` | — | `ruleset:validate_condition` | разведка, агент | — | — |
| `web.exclude.rules[].unless` | условие | `classify:_excludes` | — | как в `dotnet` | агент, человек | — | — |
| `web.rules[].id` | строка | `classify:classify` | — | как в `dotnet` | агент | — | — |
| `web.rules[].kind` | вид сущности | как в `dotnet`; плюс `web/tree:_promote` | — | тип | агент | страницей становится только вид ровно `component` с маршрутом, сервисом API — ровно `service` с вызовами: другое имя вида не повышается — ни одной страницы, без ошибки. Вид `page` правилом не выдаётся | — |
| `web.rules[].template` | имя скелета | `materialize/template:resolve_template` | файл `<templates>/<имя>.md` | загрузка скелетов | агент | повышенный узел получает скелет повышения (`page`, `api-service`), а не правила | — |
| `web.rules[].priority` | целое, обязательно | `ruleset:pick_winner` | — | как в `dotnet` | агент | — | — |
| `web.rules[].when` | условие | `classify:classify` | — | `ruleset:validate_condition` | разведка, агент | — | — |

### Предикаты условий `when` и `unless`

Одна таблица на обе секции, на `when` и на `unless`. Проверяет
`ruleset:validate_condition`: неизвестный предикат, значение не списком
строк, битая регулярка — отказ загрузки. Значения предикатов не
проверяются: опечатка в значении — правило, которое не совпадёт ни разу.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `when.attribute` | список имён атрибутов или декораторов | `classify:_attribute` | — | загрузка | разведка | имя без суффикса `Attribute` и без пространства имён (`ApiController`) | `dotnet.undecided`, `web.undecided` |
| `when.base_type` | список имён базовых типов | `classify:_base_type` | — | загрузка | разведка | только прямые базы; у fluent-базы (`EndpointBaseAsync.WithRequest<A>…`) значимо первое звено (`classify:base_type_candidates`) | `dotnet.undecided`, `web.undecided` |
| `when.inherits` | список имён | `classify:_inherits` (`base_type_closure`) | — | загрузка | разведка | замыкание рвётся на файле под `exclude` `docpipe.yaml` — правило молча не совпадает | `dotnet.undecided`, `web.undecided` |
| `when.name_regex` | список регулярок | `classify:_name_regex` | — | загрузка: битая регулярка — отказ | разведка | `re.fullmatch`, а не поиск: `Dto` не ловит `OrderDto` | `dotnet.undecided`, `web.undecided` |
| `when.name_suffix` | список суффиксов | `classify:_name_suffix` | — | загрузка | разведка | — | — |
| `when.namespace_regex` | список регулярок | `classify:_namespace_regex` | — | загрузка | разведка | `fullmatch`; у TypeScript пространство имён — каталог файла | `dotnet.undecided`, `web.undecided` |
| `when.path_glob` | список глобов | `classify:_path_glob` (`discovery:matches_glob`) | репо-относительный путь любого источника символа | загрузка | разведка | у `partial`-типа хватает одного источника | — |
| `when.type_kind` | список видов типа | `classify:_type_kind` | — | загрузка (значения — нет) | разведка | вид не из словаря модели (`Class`, `record struct`) не совпадёт ни разу | `dotnet.undecided`, `web.undecided` |
| `when.modifier` | список модификаторов | `classify:_modifier` | — | загрузка | разведка | у TypeScript `public` на уровне объявления нет | `dotnet.undecided`, `web.undecided` |
| `when.has_member_with_attribute` | список имён атрибутов | `classify:_has_member_with_attribute` | — | загрузка | разведка | атрибут у любого члена | — |
| `when.any` | непустой список условий | `ruleset:evaluate` | — | загрузка: пусто — отказ | агент | — | — |
| `when.all` | непустой список условий | `ruleset:evaluate` | — | загрузка: пусто — отказ | агент | — | — |

## `ownership.yaml`

Читает `materialize/ownership:load_ownership` (BOM снимается),
применяет `materialize/ownership:owner_of`. Кто зовёт: шаг 2
(`step2:prepare`), `docs owners`, `anchors explain`, команды `business`.
Ключи верха, команды и правила проверяются строго (S02).

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `version` | строка; `"1"` | `materialize/ownership:load_ownership`, дальше не читается | — | значение не проверяется | код | метка формата | — |
| `ownership_version` | строка; `""` | `materialize/ownership:load_ownership`, дальше не читается | — | значение не проверяется | человек | метка: на владение не влияет | — |
| `teams[].id` | строка, обязательна, уникальна | `materialize/ownership:load_ownership`; `--team` шага 2 (`step2:check_teams`) | — | загрузка: нет или повтор — отказ | человек (S28: имена, которые становятся ключами) | — | — |
| `teams[].title` | строка; равна `id` | `materialize/ownership:load_ownership` | — | загрузка: опечатка в ключе — отказ (S02) | человек | было: `titel` молча давал заголовок, равный `id`, — закрыто S02 | — |
| `rules[].id` | строка, обязательна, уникальна | `materialize/ownership:owner_of` | — | загрузка | агент | — | — |
| `rules[].team` | `id` команды | `materialize/ownership:owner_of` | — | загрузка: команды нет в `teams` — отказ | человек | — | — |
| `rules[].priority` | целое, обязательно | `ruleset:pick_winner` | — | загрузка | агент | равный максимальный приоритет — решение по `id`; `docs owners --lint` печатает `priority-tie` | — |
| `rules[].when` | условие | `ruleset:evaluate` | — | загрузка: `ruleset:validate_condition` | агент | правило, не совпавшее ни с одним узлом, — `dead-rule` в линте | `owners.unowned` |
| `rules[].when.path_glob` | список глобов | `materialize/ownership:_path_glob` | репо-относительный путь любого источника | загрузка | разведка | у `partial`-типа из каталогов разных команд хватает одного (`split-type` в линте); узел без символа не совпадает | `owners.unowned` |
| `rules[].when.module` | список имён модулей | `materialize/ownership:_module` | — | загрузка | разведка | имена проектов не уникальны (на ABP 39 повторов) — правило накроет оба; по пути — `module_glob` | — |
| `rules[].when.module_glob` | список глобов | `materialize/ownership:_module_glob` | путь `.csproj` | загрузка | разведка | — | — |
| `rules[].when.domain` | список доменов | `materialize/ownership:_domain` | — | загрузка | человек | домен — из `domains` `docpipe.yaml`: смена домена молча выводит узлы из правила; у фронта домена нет | `owners.unowned` |
| `rules[].when.kind` | список видов | `materialize/ownership:_kind` | — | загрузка | агент | вид после повышения фронта (`page`); переименование вида в правилах — `dead-rule` | `owners.unowned` |
| `rules[].when.namespace_prefix` | список префиксов | `materialize/ownership:_namespace_prefix` | — | загрузка | разведка | префикс строки, а не сегментов: `App.Pricing` накроет `App.PricingTools` | — |
| `rules[].when.namespace_regex` | список регулярок | `materialize/ownership:_namespace_regex` | — | загрузка: битая регулярка — отказ | разведка | `fullmatch` | — |
| `rules[].when.fqn_prefix` | список префиксов | `materialize/ownership:_fqn_prefix` | — | загрузка | разведка | префикс строки, как `namespace_prefix` | — |
| `rules[].when.attribute` | список имён | `materialize/ownership:_attribute` | — | загрузка | разведка | как в `rules.yaml` | — |
| `rules[].when.base_type` | список имён | `materialize/ownership:_base_type` | — | загрузка | разведка | прямые базы, сопоставление общее с классификацией (`classify:matches_any_type`) | — |
| `rules[].when.inherits` | список имён | `materialize/ownership:_inherits` | — | загрузка | разведка | рвётся на модуле под `exclude` — узел ничей или у менее специфичного правила | `owners.unowned` |
| `rules[].when.any` | непустой список условий | `ruleset:evaluate` | — | загрузка | агент | — | — |
| `rules[].when.all` | непустой список условий | `ruleset:evaluate` | — | загрузка | агент | — | — |

## `pages.yaml`

Читает `web/overrides:load_overrides`, зовёт `web/overrides:load_page_overrides`
(web scan, symbols `--lang ts`, setup candidates features и registry-calls).
Две законные формы: правила в теле `pages:` или наверху; `features` —
в любом из двух мест, но не в обоих. Каждое неприменившееся правило
печатается (`add-missed`, `add-redundant`, `remove-missed`, `feature-empty`),
код 1 — только с `--fail-on-stale-overrides`.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `version` | строка; `"1"` | `web/overrides:load_overrides`, дальше не читается | — | значение не проверяется | код | метка формата | — |
| `pages` | словарь — тело второй формы файла | `web/overrides:load_overrides` | — | загрузка: неизвестный ключ тела, `add` или `remove` рядом с `pages:`, `features` в обоих местах — отказ (S02) | — | было: правило, записанное рядом с `pages:`, не читалось молча — закрыто S02 | — |
| `add[].route` | маршрут | `web/overrides` → синтетическая запись маршрута (`source: pages.yaml`) | — | загрузка: пусто — отказ | человек | маршрут нормализуется (`route:normalize_route`); найденный сам — `add-redundant` | `pages.stale_overrides` |
| `add[].component` | FQN узла (`src/app/x/y.component.YPage`) | `web/overrides`, `web/tree` | путь файла внутри FQN | загрузка: пусто — отказ | разведка (`web pages`) | FQN зависит от пути файла: перенос — правило ни на что не легло (`add-missed`) | `pages.stale_overrides` |
| `add[].reason` | строка, обязательна | — | — | загрузка: пусто — отказ | человек | — | — |
| `remove[].route` | маршрут; ровно одно из `route` и `component` | `web/overrides`, `web/tree` | — | загрузка: оба или ни одного — отказ | человек | снятие отменяет повышение, а не удаляет узел; не легло — `remove-missed` | `pages.stale_overrides` |
| `remove[].component` | FQN узла | `web/overrides`, `web/tree` | путь файла внутри FQN | как `remove[].route` | человек | как `add[].component` | `pages.stale_overrides` |
| `remove[].reason` | строка, обязательна | — | — | загрузка: пусто — отказ | человек | — | — |
| `features[].name` | ключ якоря бизнес-слоя, `node_id` (`feature:<имя>`) и часть пути документа | `web/tree:_feature_nodes`, `web/absorb` | — | загрузка: пусто — отказ | человек (S28: имя становится ключом) | переименование меняет `node_id`: прежний документ раздела становится сиротой, новый создаётся пустым, бизнес-якорь рвётся | `docs.orphan` |
| `features[].path` | каталог раздела | `web/overrides:Feature.prefix` ← `web/tree:_feature_nodes`, `web/absorb` | `--root` | загрузка: начинается с `/` или содержит `..` — отказ (`\` не проверяется) | разведка: `setup candidates features` (S14) | граница каталога, а не подстрока; пустой раздел — `feature-empty`; путь входит в `signature_hash` раздела | `pages.stale_overrides` |
| `features[].title` | строка; пусто — `name` | `web/overrides:Feature.heading` | — | тип | человек | — | — |
| `features[].reason` | строка, обязательна | — | — | загрузка: пусто — отказ | человек | — | — |

## `registries.yaml`

Читает `registry/config:load_registries`, разбирает
`registry/reader:read_registry`. Зовут: бизнес-слой и `anchors` через
`registry/anchors:read_anchors` (по ключу `registries`), граф через адаптер
`registries` (по `arch_adapters[].options.spec`). Ошибки чтения записей
не роняют прогон — они перечисляются (`errors`), и реестр без записей
называет себя вслух.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `version` | строка | `registry/config:load_registries`, дальше не читается | — | значение не проверяется; неизвестный ключ верха — отказ | код | метка формата | — |
| `registries_version` | строка | `registry/config:load_registries`, дальше не читается | — | значение не проверяется | человек | метка: в хэши не входит | — |
| `registries[].id` | строка, обязательна, уникальна | `registry/config:load_registries`; подпись записей и ошибок | — | загрузка: нет или повтор — отказ | разведка, человек | — | — |
| `registries[].kind` | вид записей | `registry/anchors` → `business/resolve.REGISTRY_KIND`; адаптер → `options.kinds` | — | загрузка: обязателен | разведка | вид без пары в `REGISTRY_KIND` — якорь не разрешается никогда («инструмент не нашёл»); в адаптере — ошибка «не отображён» | `load.errors` |
| `registries[].format` | `xml` или `inline`, обязателен | `registry/reader:read_registry` | — | загрузка | разведка | `json` верхним уровнем не бывает — только целью `follow` | — |
| `registries[].path` | глоб файлов | `registry/reader:_iter_files` (`Path.glob`) | `--root` | загрузка: у `xml` нужен `path` или `paths` | разведка | шаблон без файлов — ошибка реестра (печатается) | `load.errors` |
| `registries[].paths` | список глобов; складывается с `path` | `registry/reader:_iter_files` | `--root` | как `path` | разведка | — | — |
| `registries[].item_xpath` | XPath записи (ElementTree) | `registry/reader:_read_xml` | — | загрузка: у `xml` обязателен | разведка | при `xmlns` у корня голое имя тега не находит ничего — ошибка «ни одной записи» с подсказкой | `load.errors` |
| `registries[].fields` | словарь «поле → выражение» (`@attr`, `путь/@attr`, `путь`, `#tag`) | `registry/reader:_fields_xml` | — | загрузка: нет `ref` ни здесь, ни в `follow.fields` — отказ | разведка | выражение без значения опускает поле молча — пусто неотличимо от «в реестре нет»; запись без `ref` — пропуск с ошибкой | `load.errors` |
| `registries[].children[].kind` | вид вложенных записей | `registry/reader:_children_xml` | — | тип | разведка | — | — |
| `registries[].children[].item_xpath` | XPath относительно записи | `registry/reader:_children_xml` | — | тип | разведка | ничего не нашёл — ноль вложенных, молча | — |
| `registries[].children[].fields` | словарь выражений | `registry/reader:_children_xml` | — | тип | разведка | вложенная запись без `ref` — пропуск с ошибкой | `load.errors` |
| `registries[].follow.field` | поле записи с путём к файлу описания | `registry/reader:_follow` | — | тип | разведка | — | — |
| `registries[].follow.base` | каталог, от которого отсчитывается путь | `registry/reader:_follow` | `--root` плюс `base` | тип | разведка | от каталога реестра не отсчитывается намеренно; файла нет — ошибка записи | `load.errors` |
| `registries[].follow.format` | `json`; `json` | `registry/reader:_follow` | — | загрузка | код | — | — |
| `registries[].follow.fields` | словарь «поле → ключ JSON» | `registry/reader:_fields_json` | — | тип | разведка | значения приводятся к строке; ключа нет — поле опускается молча | — |
| `registries[].follow.children.kind` | вид вложенных записей | `registry/reader:_follow` | — | тип | разведка | — | — |
| `registries[].follow.children.list` | имя массива в JSON (поле `items_key`) | `registry/reader:_follow` | — | тип | разведка | нет или не массив — ошибка записи | `load.errors` |
| `registries[].follow.children.fields` | словарь «поле → ключ JSON» | `registry/reader:_follow` | — | тип | разведка | — | — |
| `registries[].items` | список словарей с `ref` (формат `inline`) | `registry/reader:read_registry` | — | загрузка: у `inline` нужен список, у каждого — `ref` | человек | — | — |

## `arch-registry.yaml`

Читает `arch/load:check_document` (все находки сразу, BOM снимается);
`arch validate` — проверка, `arch status` — устаревание снимка, `graph build` —
вход графа (`graph/entrypoints`, `graph/data`, `graph/seams`). Четыре вида
записей — общие поля и поля вида в одной таблице: «вид» в колонке типа
называет, у какого вида поле есть.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `version` | строка, обязательна; `"1"` | `arch/load:check_document` | — | загрузка: нет или другая версия — отказ | код | — | — |
| `records[].kind` | `entry_point`, `data`, `seam` или `layer` | `arch/load:check_document` (дискриминатор) | — | загрузка: неизвестный — отказ | разведка (`recon`) | — | — |
| `records[].key` | непустая строка; уникальна в виде после нормализации | `arch/model:*.normalized_key`, `graph/entrypoints:entry_key` | — | загрузка: повтор после нормализации — отказ | разведка, человек | ключ — то, что знает вызывающий, а не имя класса | — |
| `records[].name` | человеческое имя; `""` | `graph/entrypoints`, `graph/data`, `graph/seams` (имя узла, поиск) | — | тип | разведка | — | — |
| `records[].source.file` | репо-относительный путь, обязателен | `arch/status`; атрибут узла графа | `--root` | загрузка: пусто — отказ; файла нет — `source_missing` в `arch status`, не отказ | разведка | — | — |
| `records[].source.record` | адрес записи внутри файла; `""` | `arch/status`, атрибут узла | — | тип | разведка | — | — |
| `records[].source.hash` | `sha256:…`; `""` | `arch/status` | — | пусто — состояние `no_hash` | код (`arch snapshot`) | без хэша устаревание снимка не ловится | — |
| `records[].provenance` | `manual`, `skill_proposed`, `skill_confirmed`, `adapter` | `arch/load:check_document` | — | загрузка: `skill_proposed` без `--draft` — отказ; в черновике без `note` — отказ | человек меняет `skill_proposed` на `skill_confirmed` | — | — |
| `records[].attributes` | словарь строк; `{}` | `graph/entrypoints` (в атрибуты узла), `graph/data` | — | тип | разведка | — | — |
| `records[].note` | обоснование; `""` | `arch/load:check_document` | — | у `skill_proposed` обязателен | разведка | — | — |
| `records[].entry_kind` | вид `entry_point`: `workflow`, `workflow_step`, `job`, `event_handler`, `http_endpoint`, `grid_service`, `page`, `service`, `cli`, `other` | `graph/entrypoints`, `graph/seams` (`ANSWERING_KINDS`), `graph/coverage` | — | загрузка: значение из словаря | разведка | — | — |
| `records[].impl` | вид `entry_point`: строка или список подсказок на код; `()` | `graph/entrypoints` → связывание с кодом | — | тип | разведка | подсказка без узла кода — «корень без узла» (состояние работы, печатается) | — |
| `records[].route` | вид `entry_point`: маршрут; `""` — берётся `key` | `arch/model:EntryPointRecord.normalized_key` | — | тип | разведка | — | — |
| `records[].http_method` | виды `entry_point` и `seam`: глагол; `""` | `normalized_key`, `graph/seams:_answering` | — | тип | разведка | у шва `http_route` без глагола сходятся все глаголы маршрута | — |
| `records[].touches` | вид `entry_point`: ключи записей `data`; `()` | `graph/data` (рёбра `touches`) | — | тип | разведка | ключ, которого нет среди записей `data`, пропускается молча — ребра нет | — |
| `records[].data_kind` | вид `data`: `table`, `view`, `procedure`, `other`; `table` | `graph/data` | — | загрузка | разведка | — | — |
| `records[].table` | вид `data`: таблица-носитель; `""` | `arch/model:DataRecord.normalized_key` (вместо `key`), `graph/data` | — | тип | разведка | — | — |
| `records[].fields[].name` | вид `data`: внутреннее имя поля, непусто | `graph/data` (атрибут `field:…`) | — | загрузка: пусто — отказ | разведка | — | — |
| `records[].fields[].kind` | вид поля, свободная строка | `graph/data` | — | тип | разведка | — | — |
| `records[].fields[].display_name` | человеческое имя поля | `graph/data` (подпись в атрибуте `field:…`) | — | тип | разведка | — | — |
| `records[].fields[].references` | ключ другого узла данных | адаптер `registries` сам складывает ссылки полей в `records[].references` (`arch/adapters/declared`); `arch snapshot` пишет; сборка графа поле не читает | — | тип | разведка | открыто: в записи, написанной руками, ссылка поля ребром не становится — рёбра `references` строятся только из `records[].references` | — |
| `records[].references` | вид `data`: ключи других `data`; `()` | `graph/data` (рёбра `references`) | — | тип | разведка | — | — |
| `records[].seam_kind` | вид `seam`: `http_route`, `grid_service`, `queue`, `topic`, `file`, `other` | `graph/seams` | — | загрузка | разведка (`recon`) | — | — |
| `records[].literal` | вид `seam`: строка шва; `""` — берётся `key` | `graph/seams` | — | тип | разведка | — | — |
| `records[].sides` | вид `seam`: стороны; `()` | `graph/seams` — атрибут узла, на связывание не влияет | — | тип | разведка | — | — |
| `records[].role` | вид `layer`: `host`, `service`, `library`, `frontend`, `tests`, `generated`, `tooling`, `other` | только `arch validate` и `arch records` (счёт) | — | загрузка | разведка | открыто: записи `layer` проверяются и считаются, но `graph build` их не читает — роль модуля в граф не попадает | — |
| `records[].path` | вид `layer`: путь модуля; `""` | как `role` | — | тип | разведка | как `role` | — |
| `records[].language` | вид `layer`: язык; `""` | как `role` | — | тип | разведка | как `role` | — |

## Скелеты `templates/*.md`

Скелет объявляет состав секций и место генерируемого блока; грузит
`materialize/template:load_templates` (не рекурсивно, `README.md`
пропускается), подставляет `materialize/plan:_substitution_values`.
Устройство скелета, которое проверяет загрузка: генерируемый блок
`docpipe:generated` есть и пуст; маркеры `docpipe:section:start ИМЯ`
распознаны все; секция `notes` есть; секции пусты (подсказки — только
HTML-комментариями); front matter нет. `default.md` — скелет для имени
без своего. Скелеты бизнес-слоя (`templates/business/`) читает только
`business new`, и проверки выше к ним не применяются: там подставляется
ровно `{{ title }}` с пробелами, а `{{title}}` остаётся текстом.

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `{{ title }}` | заголовок узла | `materialize/plan:_substitution_values` ← `materialize/plan:_compose` | — | загрузка скелетов: неизвестный ключ подстановки — отказ | код (манифест) | подставляется только при создании документа: вне генерируемого блока текст принадлежит автору, и смена значения в нём не отражается | — |
| `{{ fqn }}` | FQN символа; у узла без символа — заголовок | как `{{ title }}` | — | как `{{ title }}` | код | как `{{ title }}` | — |
| `{{ kind }}` | вид узла | как `{{ title }}` | — | как `{{ title }}` | код (правила) | как `{{ title }}` | — |
| `{{ module }}` | имя модуля | как `{{ title }}` | — | как `{{ title }}` | код | как `{{ title }}` | — |
| `{{ domain }}` | домен (`domains`) | как `{{ title }}` | — | как `{{ title }}` | человек (`domains`) | как `{{ title }}`; домен в front matter `docpipe` при этом обновляется | — |
| `{{ team }}` | команда; `""` без владения | как `{{ title }}` | — | как `{{ title }}` | человек (`ownership.yaml`) | как `{{ domain }}` | — |
| `{{ doc_path }}` | путь документа | как `{{ title }}` | — | как `{{ title }}` | код | при переезде документа текст в теле прежний | — |
| `{{ node_id }}` | идентификатор узла | как `{{ title }}` | — | как `{{ title }}` | код | — | — |

## Флаги и плейсхолдеры `install.sh`

Установщик кладёт настройку в репозиторий продукта и подставляет
плейсхолдеры в `docpipe.yaml` набора (`deploy/generic-docspipe/` или
`deploy/cashflow-docspipe/`, флаг `--bundle`) один раз: уже лежащий файл
не затирается, новая версия ложится рядом `.new`, о чём печатается строка
в stderr. Исключение — `.gigacode/settings.json` клона: в нём ставятся
только записи `mcpServers.docpipe` и `mcpServers.docpipe-setup`, остальное
сохраняется (S30).

| Ключ | Тип и умолчание | Кто читает | База пути | Чем проверяется | Откуда значение | Что ломается молча | Находка |
|---|---|---|---|---|---|---|---|
| `--repo` | путь, обязателен | `deploy/install.sh` | текущий каталог установщика | каталога нет — код 1; нет ни `.git`, ни `.sln` — предупреждение | человек | — | — |
| `--config-dir` | путь внутри `--repo`, обязателен | `deploy/install.sh` → `@CONFIG_DIR@` | от `--repo` | абсолютный или с `..` — код 1 | человек | повторная установка с другим значением не трогает лежащий `docpipe.yaml` — прежние пути остаются, новые лежат в `.new` | — |
| `--bundle` | `generic` или `cashflow`; `generic` | `deploy/install.sh` → каталог набора `deploy/<набор>-docspipe/` | — | другое значение — код 2 | человек | обновление каталога, поставленного `cashflow`, без флага кладёт рядом `.new` нейтрального набора и заменяет README — установщик говорит об этом в stderr, код не меняет | — |
| `--cache-dir` | абсолютный путь; `$WORK/.docpipe/cache/<имя каталога --repo>`, без `$WORK` — `~/.cache/docpipe/<имя каталога --repo>` | `deploy/install.sh` → `@CACHE_DIR@` | абсолютный | относительный — код 1 | код | было: умолчание общее для всех репозиториев машины — своё у каждого с S30. Открыто: два репозитория с одним именем каталога делят его по-прежнему, им флаг задают явно | — |
| `--engine` | путь к бинарю движка 0.6.0; без флага — `""` | `deploy/install.sh` → `@ENGINE@` | записывается как есть | установщиком не проверяется; без флага `graph *` откажут с указанием | человек | относительный путь разрешится от каталога `graph build`, а не установщика | `config.problems` |
| `--index` | адрес зеркала пакетов | `deploy/install.sh` → `uv.toml` клона; `uv tool install --config-file`, только если в файле описан источник | — | — | человек | было: `uv tool` не читает `uv.toml` проекта, и установка уходила на pypi.org — закрыто до плана | — |
| `--python` | путь или версия интерпретатора | `deploy/install.sh` → `uv tool install --python` | — | — | человек | скачивание интерпретатора запрещено намеренно | — |
| `--no-tool` | флаг | `deploy/install.sh` | — | — | человек | без инструмента не пишутся и записи MCP-серверов (`.gigacode/settings.json`); вручную — `deploy/OFFLINE.md` | — |
| `--help` | флаг | `deploy/install.sh:usage` | — | — | — | — | — |
| `@CONFIG_DIR@` | плейсхолдер в `out`, `worklist`, `docs_scan_exclude`, `business_root`, `web.out`, `web.link_out`, `graph.out` | `deploy/install.sh` (`sed`); `configcheck:_placeholders` | — | `config check`: `placeholder-left` (S08) | код | было: файл поставки, позванный мимо установщика, писал бы в каталог `@CONFIG_DIR@` текущего — закрыто S08 | `config.problems` |
| `@CACHE_DIR@` | плейсхолдер в `cache_dir`, `graph.cache_dir` | как `@CONFIG_DIR@` | — | `config check`: `placeholder-left` | код | как `@CONFIG_DIR@` | `config.problems` |
| `@ENGINE@` | плейсхолдер в `graph.engine_path` | как `@CONFIG_DIR@` | — | `config check`: `placeholder-left` | код | было: ловился только как `engine-missing`, причина не называлась — закрыто S08 | `config.problems` |

## Группы взаимозависимых ключей

Ключи, которые правят вместе: правка одного без остальных проходит
и ломается молча. Ключи из колонки «Ключи» тест сверяет с таблицами выше.

| Группа | Ключи | Как связаны | Что ломается, если править один |
|---|---|---|---|
| 1. Раскладка документов | `docs_root`, `modules_dir`, `web.modules_dir`, `doc_layout` | собирают `doc_path` на шаге 1 и замерзают в манифесте; шаг 2 читает их заново | без пересборки манифеста шаг 2 откажется (S03); пересобирать — полным `scan` или `web scan` |
| 2. Пишем туда, где ищем | `docs_root`, `docs_scan_exclude`, `@CONFIG_DIR@` | каталог настройки лежит внутри `docs_root`, его образцы исключаются из обхода документов | шаблон шире каталога настройки накрывает документы — блокирующая ошибка шага 2 |
| 3. Три отсева | `exclude`, `not_enrolled[].glob`, `dotnet.exclude.rules[].when`, `web.exclude.rules[].when` | «куда не заходим», «модуль не берём», «тип не документируем» | `exclude` рвёт наследование (`when.inherits`, `rules[].when.inherits`); отсев правил файлы не исключает и `--stats` считает символы, а не файлы |
| 4. Область модулей | `enrolled`, `not_enrolled[].glob`, `roots` | решение по пути `.csproj`; `roots` сужает обход | шаблон `not_enrolled` внутри явного `enrolled` — отказ; опечатка в `enrolled` — модули `undecided` |
| 5. Корни обхода | `roots`, `web.roots`, `exclude` | три фильтра обхода; граф из них читает только `exclude` | каталог пропал — `root-missing`; `graph build` разбирает весь `--root` |
| 6. Плейсхолдеры поставки | `@CONFIG_DIR@`, `@CACHE_DIR@`, `@ENGINE@`, `--config-dir`, `--cache-dir`, `--engine`, `out`, `worklist`, `business_root`, `docs_scan_exclude`, `web.out`, `web.link_out`, `graph.out`, `cache_dir`, `graph.cache_dir`, `graph.engine_path` | установщик подставляет флаги в ключи один раз | файл поставки, позванный напрямую, — `placeholder-left`; смена флага при повторной установке не доходит до лежащего файла |
| 7. Единый `--root` | `docs_root`, `business_root`, `cache_dir`, `roots`, `web.roots`, `arch_adapters[].options.path`, `registries[].path`, `registries[].follow.base`, `records[].source.file`, `features[].path` | всё отсчитывается от `--root` команды; `scan` и `graph build` обязаны получить один корень | разные корни — «сопоставлено 0» при непустых сторонах, отказ с диагнозом |
| 8. Единый текущий каталог | `out`, `worklist`, `web.out`, `web.link_out`, `graph.out`, `graph.cache_dir`, `graph.engine_path` | цели записи от текущего каталога; оттуда же зовут `config check` и запускают MCP-сервер | `business build` из другого каталога — «страниц ноль»; `config check` из другого места подтверждает чужую настройку |
| 9. Входы двумя ступенями | `rules`, `web.rules`, `templates`, `ownership`, `registries`, `arch`, `web.pages`, `arch_adapters[].options.spec` | текущий каталог, затем каталог `docpipe.yaml`; выигрывает первый существующий | одноимённый файл в текущем каталоге тихо побеждает файл рядом с настройкой; ступень показывает `config check` |
| 10. Секционный файл правил | `rules`, `web.rules`, `dotnet.exclude.require_public`, `web.exclude.require_public` | один файл, секцию называет команда | `require_public: true` в секции `web` отсеивает весь фронт |
| 11. Вид → путь, скелет, владение | `dotnet.rules[].kind`, `dotnet.rules[].template`, `templates`, `rules[].when.kind`, `{{ kind }}` | вид даёт каталог `doc_path`, скелет и предикат владения | переименование вида — переезд документов и мёртвые правила владения; скелета нет — `default.md` |
| 12. Повышение фронта | `web.rules[].kind`, `add[].component`, `remove[].component`, `features[].path` | страница — компонент `component` с маршрутом; ручной состав правит повышение | вид другого имени не повышается — ни одной страницы; перенос файла — `add-missed` |
| 13. Ключ кэша разбора | `cache_dir`, `di_methods`, `di_methods[].name`, `--cache-dir` | имена `di_methods` входят в ключ кэша, причина — нет | общий кэш двух репозиториев с разными `di_methods` очищается каждым прогоном; `--scope` читает чужие записи |
| 14. Связывание по коду | `di_methods`, `dispatch_interfaces` | ложатся в манифест (`di_registrations`, `dispatch_handlers`), граф строит по ним рёбра | без ключа связывание пусто при живом коде; находку даёт `setup candidates` |
| 15. Шов фронт↔.NET | `web.url_rewrite[].module`, `web.url_rewrite[].strip_prefix`, `web.url_rewrite[].add_prefix`, `web.registry_calls[].route`, `web.http_wrappers[].url.arg`, `web.url_builders[].path.arg`, `link.external_targets[].route` | ключ вызова строится в `web scan`: обёртка и построитель дают адрес, затем преобразование модуля; правило различителя и маска внешнего адресата пишутся в форме после него | правка `url_rewrite`, обёрток или построителей без `web scan` не меняет связей; `route` до преобразования не совпадает никогда — и правило внешнего адресата, написанное до правки `url_rewrite`, после неё молча перестаёт совпадать; адрес через обёртку получает `url_rewrite` своего модуля, как прямой |
| 16. Ручной состав страниц | `web.pages`, `add[].route`, `remove[].route`, `features[].name`, `features[].path` | флаг `--pages` важнее ключа; правила обязаны устаревать громко | переименование компонента — `add-missed` (печатается; код 1 только с `--fail-on-stale-overrides`) |
| 17. Владение | `ownership`, `teams[].id`, `rules[].team`, `rules[].when.domain`, `domains`, `rules[].priority` | команда правила обязана быть в `teams`; домен — из `domains` | смена домена молча выводит узлы из правила по домену; равный приоритет — `priority-tie` |
| 18. Бизнес-слой | `registries`, `business_root`, `registries[].kind`, `web.out`, `ownership` | реестры дают якоря; вид реестра сверяется с `REGISTRY_KIND` | новой пары нет — якорь не разрешается никогда |
| 19. Реестр графа и движок | `arch`, `arch_adapters[].options.spec`, `arch_adapters[].options.kinds`, `graph.engine_path`, `graph.engine_sha256`, `graph.mode`, `graph.cache_dir` | `options.spec` часто тот же файл, что `registries`, но словари видов разные (`DEFAULT_KINDS` против `REGISTRY_KIND`); режим меняет список пропусков движка | вид без пары в `kinds` — отказ `graph build`; `mode` молча пропускает каталоги |

## Вне карты

- **`uv.toml` клона** — источник пакетов для установки (`--index`,
  `deploy/OFFLINE.md`); `docpipe` его не читает.
- **`.codebase-memory.json` в корне репозитория** — проектная настройка
  движка разбора: мост её не глушит, но показывает предупреждением
  и суммой в паспорте индекса (`CLAUDE.md`, «Движок разбора»).
- **Флаги команд** — замещают ключи на один прогон (раздел «Общее»);
  перечислены в справке каждой команды.
