# Фаза 20: фронт

## Цель

У каждого символа фронта в области — решение: вид документа (или документ
страницы, которая его поглощает) либо «не документируем» с причиной
человека. Фронт не пуст: компоненты и сервисы получили виды.

## Инструменты

- `setup_status` — `web.undecided`, кластеры `module` и `last_word`;
- `setup_stats` (`docpipe web scan --stats`), `lang: ts` — срезы; для фронта
  главный — «последнее слово» (`authInterceptor` → `Interceptor`);
- `setup_symbols` (`docpipe symbols`), `lang: ts`, `state: undecided`;
- `setup_pages` (`docpipe web pages`) — страница ли класс и почему;
- `setup_explain` (`docpipe setup explain`) — правило-победитель для пути.

## Шаги

1. **Набор правил фронта.** Ключи `rules` и `web.rules` в `docpipe.yaml`
   обычно указывают на один файл: шаг 1 читает его секцию `dotnet`, шаг
   `web` — секцию `web`. Проверь в `rules.yaml`: секция `web` есть, в ней
   `web.exclude.require_public: false`.
2. **Фронт не пуст.** `setup_stats` с `lang: ts`: всего символов больше
   нуля. Ноль при фронтах в разведке — смотри `web.roots` (фаза 00)
   и `require_public`, а не правила вида.
3. **Группы без решения** — кластеры `web.undecided`, крупнейший первым;
   `setup_symbols` с `lang: ts` и `path` кластера. Признак — декоратор
   (`when.attribute`: `Component`, `Injectable`, без `@`), базовый класс
   или интерфейс (`when.inherits`), суффикс имени (`when.name_suffix`),
   путь (`when.path_glob`).
   - Признак доказуем кодом и вид объявлен в секции `web` — правило пишешь
     сам и показываешь разницу `web.undecided`.
   - Иначе — вопрос по разделу `web.undecided` каталога
     (`docs/setup-interview.md`). Частые группы: функции HTTP-каркаса
     (`getVersioned`, `buildQuery`), интерфейсы `State` и `Snapshot`
     в `*.state.ts`, модели транспорта — «не документируем ли».
4. **Страница — не вид правила.** Правило говорит «компонент», страницей
   класс делает маршрут из таблицы роутов (фаза 40). Поэтому вид для
   экранов — ровно `component`, для сервисов API — ровно `service`.
   Почему класс страница — `setup_pages`, а не `setup_symbols`.
5. **После правки** — `setup_explain` по каталогу группы и `setup_status`:
   `web.undecided` меньше, документируем больше нуля.

## Контрольная точка

- `setup_status` — нет `web.undecided`;
- `setup_stats` с `lang: ts` — без решения 0, документируем больше нуля;
- `setup_pages` — страниц больше нуля, если в разведке у фронта есть таблица
  роутов.

## Ловушки

- **`require_public: true` в секции `web` отсеивает весь фронт**: у
  TypeScript нет `public` на уровне объявления. Пустое дерево — без ошибки.
- **Файл правил секционный.** Плоский файл старого формата — отказ
  загрузки с командой переноса `tools/migrate_rules.py` клона; секцию
  `dotnet` для фронта не копируют.
- **Вид другого имени не повышается**: правило с видом `screen` вместо
  `component` — ни одной страницы, без ошибки.
- **Словарь окончаний зашит под .NET**: `Component`, `Guard`, `State`
  уходят в «(прочее)»; смотри срез «последнее слово».
- **`namespace_regex` у TypeScript — каталог файла**, а не пространство имён.
- **`tsconfig.json` с комментариями** разбирается как JSONC; импорт
  `@shared/…` не разрешился — смотри `paths` в `tsconfig` фронта: дочерний
  `paths` замещает родительский целиком. Это не настройка docpipe — скажи
  человеку.
- **`sourceRoot` в `project.json` nx — от корня workspace**, а не от
  проекта: файлы модуля без владельца-модуля — повод открыть `project.json`.
- **`enrolled` и `domains` на фронт не действуют**: область фронта —
  `web.roots`, `exclude` и отсев секции `web`.

## Правка файлов

`rules.yaml`, секция `web`: правило вида — в `web.rules` (`id`, `kind`,
`priority`, `when`; `template` — скелет из КАТАЛОГ/templates), отсев —
в `web.exclude.rules` с причиной человека. `id` — с префиксом `web.`,
чтобы не спутать с правилами секции `dotnet`. После правки подними
`web.ruleset_version`.

```yaml
# file: rules.yaml#web
web:
  rules:
    - id: web.resolver
      kind: service
      template: service
      priority: 40
      when:
        all:
          - name_suffix: ["Resolver"]
          - attribute: ["Injectable"]
  exclude:
    rules:
      - id: web.http-framework
        reason: "<слова человека>"
        when:
          path_glob: ["src/app/framework/**"]
      - id: web.state-shapes
        reason: "<слова человека>"
        when:
          all:
            - name_regex: ["^.*(State|Snapshot)$"]
            - type_kind: ["interface"]
```
