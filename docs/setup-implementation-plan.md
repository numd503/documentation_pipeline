# Настройка с ассистентом: план (S01–S32)

> **Статус: в работе** (план от 07.10.2026): ✅ S01, S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S17, S18, S19, S20, S21, S22, S23, S24, S24b, S26, S28, S30.
> [`setup-assistant-analysis.md`](setup-assistant-analysis.md); цель —
> [`purpose.md`](../purpose.md), раздел «Настройка с ассистентом». При споре
> плана с `purpose.md` прав `purpose.md`; при расхождении плана с кодом
> правится план, и ловушка записывается сюда с объяснением.

Настройку `docpipe` на репозиторий ведёт агент контура (gigacode). Человек
знает, какой код принадлежит ему и команде, задаёт область и направление,
отвечает на вопросы и решает, когда остановиться. Агент разбирает то, что
в области остаётся необъяснённым: читает код, пишет правило в файл настройки,
перезапускает инструмент и показывает разницу. `docpipe` даёт ему
детерминированные факты и проверки — и ничего не решает за человека.

План собран из трёх частей, и почти вся работа — в первой:

1. **детерминированная поверхность** (этапы A–D): команды отвечают машинным
   форматом, молчаливые места дают отказ или находку, у каждого решения
   человека есть место для причины, недостающие находки посчитаны, шов
   фронт↔.NET выражается правилами;
2. **сервер инструментов** (этап E): те же функции, что у CLI, — агенту
   по MCP;
3. **скилл и протокол интервью** (этап F), поставка (G) и прогоны (H).

Предшественники: [`setup-assistant-analysis.md`](setup-assistant-analysis.md)
(разбор и числа), [`configuration.md`](configuration.md) (ключи),
скилл [`recon`](../.gigacode/skills/recon/SKILL.md) (образец формы),
[`frontend-analysis.md`](frontend-analysis.md) (шов), прогон squidex и abp
от 07.10 (числа в S15).

---

## Решения, из которых следует всё остальное

Приняты владельцем 07.10.2026; полная запись — §8 аналитики.

**Р-1. Ассистент пишет прямо в файлы настройки.** Правка лежит там, откуда
её читает прогон, поэтому агент тут же зовёт инструмент ещё раз и видит,
как правка изменила результат. Ревью — `git diff`, решение — коммит человека.
Отсюда два требования к `docpipe`: **ни одна команда не пишет в файлы
настройки** (их правит только агент своими средствами), и **каждая проверка
дешёвая и повторяемая** — её зовут после каждой правки.

**Р-2. Причина решения — в самих файлах настройки**, второй формой записи
рядом с короткой. Отдельного журнала решений нет: второй источник отстанет.
Короткая форма продолжает работать — копия настройки лежит на АС CF.

**Р-3. Отдельный сервер `docpipe setup serve`.** Работает до любой сборки,
индекс графа ему не нужен, читает `docpipe.yaml`. Общий протокольный модуль
с `graph serve`.

**Р-4. Один скилл `setup` с фазами** в файлах рядом и тремя входами:
онбординг, расширение области, ревью.

**Р-5. Первая версия: .NET, Angular, шов между ними, документы и владение.**
Шов — одна из главных задач, потому что архитектурную границу детерминированно
найти трудно, а проверить найденную — легко.

**Р-6. Остановку решает человек; проверяемо — состояние**: в области нет
находки без решения и нет дефектов. По каждой находке решение «документируем
или нет, и почему»; «принятого остатка» нет. Область — существующими ключами.

**Решения по плану** — приняты владельцем 07.10.2026 при чтении плана:

- **П-1. Принятое состояние — коммит настройки, отдельного файла нет.**
  Первая редакция плана заводила `setup-status.json` как базу для ревью;
  владелец спросил, нельзя ли обойтись без ещё одного файла настройки.
  Можно: по Р-1 решение человека и так оформляется коммитом, поэтому база
  ревью — последний коммит каталога настройки, а ревью показывает, какие
  решения применились к коду, появившемуся после него (S25). Это точнее
  сравнения охвата «вдвое больше»: широкое правило отсева, молча
  проглотившее новый код, видно по самому коду, а не по числу.
- **П-2. `not_enrolled` с обязательной причиной**; совпадение модуля
  с `enrolled` и `not_enrolled` сразу — отказ прогона (S22).
- **П-3. Секция `link`** — внешние адресаты, внешние вызывающие,
  невосстановимое, с причиной; первый случай общего формата правил связей (S20).
- **П-4. Загрузка строже**: неизвестный ключ и пустой список — отказ (S02).
- **П-5. Установщик по умолчанию кладёт нейтральный набор**; на АС CF —
  `--bundle cashflow` (S30).

**Факты и проверки — в инструментах, порядок и суждение — в скилле.** Находка,
из которой строится вопрос, и проверка, которой закрывается шаг, обязаны
воспроизводиться байт в байт. Порядок фаз, выбор кластера, толкование находки
и формулировка вопроса — работа модели (агент контура — уровня GLM-5.3 Flash /
Qwen3.8-Flash-Next-262k).

---

## Общие правила для всех задач

Каждое правило однажды стоило захода; нарушение любого проходит тесты задачи
и ломается в следующей.

**1. Логика отдельно от печати.** Новая или изменённая команда — это функция,
возвращающая pydantic-модель отчёта (`frozen=True`, `extra="forbid"`, поле
`schema_version`), и форматтер текста. CLI тонкий: разобрать флаги, позвать
функцию, напечатать. MCP-инструмент (S27) зовёт **ту же** функцию. Копия
логики в CLI и в сервере разойдётся на первом же ключе — ровно так разошлись
три копии `_prepare`.

**2. Флаг вывода — `--format text|json`**, значение проверяется: неизвестное —
код 2 с перечнем допустимых. Сейчас `--format` не проверяется нигде
(`cli.py:798` и соседи): опечатка `jsno` молча даёт текст, и скрипт, ждущий
JSON, падает не там. Существующий булев `--json` у `arch records`/`arch status`
не трогаем.

**3. JSON** — `stable_json_dumps(model.model_dump(mode="json"))`
(`hashing.py`), печать без лишнего перевода строки: `typer.echo(text.rstrip("\n"))`.
Сейчас `symbols` и `web pages` кончаются `"\n\n"`, `docs status` — одним.
Ключи JSON — латиницей и стабильные; русские подписи живут только в тексте.

**4. Детерминизм**: любой список — `sorted()` с явным `key`; ни времени,
ни хоста; пути — репо-относительные POSIX. Отчёты для агента (`config check`)
могут содержать абсолютные разрешённые пути — они не артефакт, а ответ;
тесты сравнивают их через `tmp_path`.

**5. Ответ — сводка с продолжением.** Списки в отчётах — с `limit`
(по умолчанию 20) и `offset`, всегда с `total`. Агент контура обрезает вывод
инструмента на 25 000 символов или 1000 строк, и «таких нет» становится
неотличимо от «не показали» — та же ловушка, что `head -30` в `CASHFLOW.md`
§1а. Ответ MCP-инструмента — не больше **20 000 символов**; это критерий
приёмки S27 на крупной синтетике.

**6. Совместимость настройки — в одну сторону.** Старая настройка обязана
читаться новым инструментом. Новая форма ключа — объединение `str | Model`
с нормализацией; потребители читают нормализованное свойство, а не поле.
Обратно (новая настройка старым инструментом) не работает из-за
`extra="forbid"` — это ожидаемо и пишется в справочник.

**7. Формат манифеста двигает версию.** Любое новое поле в `Manifest`
и вложенных моделях — минорная версия (`2.0` → `2.1` → …), чтение принимает
все версии своей мажорной (S16 меняет `Literal["2.0"]` на проверку мажорной).
Новое поле в отчёте с `schema_version` — тоже минорная версия. Закоммиченные
схемы `schema/*.json` перегенерируются тем же PR; тест сверки — в S06.

**8. Границы модулей.** `docpipe/materialize/*` не импортирует
`docpipe.business` и `docpipe.dotnet` (`tests/test_materialize_cli.py:420-427`);
весь контакт с движком графа — в мосте (Р13). Новое — в пакете
`docpipe/setup/`, кроме того, что по смыслу принадлежит существующему модулю
(факты разбора — в `dotnet/`, `web/`).

**9. Новый модуль — строка в [`module-review.md`](module-review.md)**:
категория, потребитель, признак смерти. Модуль без потребителя — «под
вопросом» автоматически. Раздел «Настройка (`setup/`)» заводит первая задача,
создающая модуль пакета.

**10. Кэш разбора.** Новое поле в `FileParseResult` или изменение правил
разбора — повышение `CACHE_VERSION` (`cache.py:21-28`). Иначе записи кэша
загрузятся с пустым значением по умолчанию, и находка даст ноль, который
выглядит как «таких нет». Настройка, влияющая на разбор, входит в ключ кэша
(прецедент — `di_methods`, `emit.py:447-449`); причина решения в ключ кэша
**не** входит — правка формулировки не должна сбрасывать кэш.

**11. Тесты самодостаточны.** `examples/` и `~/docspipe-examples` в тестах
не используются; конструкции воспроизводятся в `tests/fixtures/` или
`tmp_path`. `SampleSolution` не расширяется (на точные числа в нём завязаны
T04–T20). Тест фикстуры проверяет наличие **конструкции**, а не файла.
Тесты зовутся из корня репозитория (относительные пути в существующих тестах).

**12. Ничего специфичного для АС CF в `docpipe/`.** Ни путей, ни имён,
ни маршрутов. Пример из АС CF — только в комментарии и только как мотивировка.

**13. После каждой задачи** — команда проверки задачи, полная проверка
(`ruff check`, `ruff format --check`, `mypy docpipe`, `pytest -q`), запись
в [журнал](implementation-log.md) с трудностями и отклонениями, правка
справочников, перечисленных в задаче. Расхождение плана с реальностью —
правка плана, а не обход.

---

## Задачи

Слои: задача слоя зависит только от задач слоёв выше.

```
1   S01  S02  S03  S04  S05  S06  S07  S10  S11  S12  S14  S15  S26
2   S08 ← S02…S07        S16, S17 ← S15        S22 ← S02
3   S09 ← S08            S13, S18 ← S16        S23 ← S07, S22
4   S19 ← S18
5   S20 ← S16, S19
6   S21 ← S18, S20
7   S24 ← S04, S05, S06, S21, S22
8   S25 ← S24            S28 ← S09, S24
9   S27 ← S10…S14, S23…S26
10  S29 ← S27, S28       S30 ← S01, S27
11  S31 ← S29, S30
12  S32 ← S31, доступ к контуру
```

| # | Задача | Зависит от | Размер |
|---|---|---|---|
| **0** | **Доставка** | | |
| S01 | ✅ Скилл и MCP-запись — в `.gigacode/` | — | S |
| **A** | **Детерминированная поверхность** | | |
| S02 | ✅ Загрузка настройки без молчания | — | M |
| S03 | ✅ Прогоны без молчания | — | M |
| S04 | ✅ `config check`: функция, JSON, коды возврата | — | S |
| S05 | ✅ JSON у `scan --stats` и `web scan --stats`; срез «последнее слово» | — | M |
| S06 | ✅ Вход шага 2 — в библиотеку; JSON у команд шага 2 и `arch validate`; сверка схем | — | M |
| S07 | ✅ `symbols`: причина, страница, правило-победитель, `--path` | — | S |
| S08 | ✅ Справочники сверить с кодом | S02–S07 | S |
| S09 | ✅ Карта цепочек настройки и тест на её полноту | S08 | M |
| **B** | **Находки, которых нет** | | |
| S10 | ✅ Разведка: полные списки проектов и фронтов | — | S |
| S11 | ✅ Факты о вызовах регистрации; кандидаты в `di_methods` | — | M |
| S12 | ✅ Кандидаты в `dispatch_interfaces` | — | S |
| S13 | ✅ Кандидаты в `web.registry_calls` | S16 | M |
| S14 | ✅ Кандидаты в разделы без маршрута | — | M |
| **C** | **Шов фронт↔.NET** | | |
| S15 | ✅ Фикстура форм шва и инвентарь squidex/abp | — | S |
| S16 | ✅ Ключ вызова без правдоподобных ошибок; версия манифеста | S15 | M |
| S17 | ✅ Эндпоинты .NET: наследование `[Route]`, маршрут без глагола, константы | S15 | M |
| S18 | ✅ Невосстановленные вызовы — в манифест; кандидаты в обёртки | S16 | M |
| S19 | ✅ `web.http_wrappers` и `web.url_builders` | S18 | L |
| S20 | ✅ Концы без пары: секция `link` | S16, S19 | M |
| S21 | ✅ Сводка шва кластерами (`setup link`) | S18, S20 | M |
| **D** | **Решения и состояние** | | |
| S22 | ✅ Вторая форма с причиной; `not_enrolled` | S02 | L |
| S23 | ✅ «Что решено об этом коде» (`setup explain`) | S07, S22 | M |
| S24 | ✅ Необъяснённое в области (`setup status`) | S04–S06, S21, S22 | L |
| S24b | ✅ Коллизии `doc_path` фронта; `web.not_wrappers` (добавлена 08.10) | S24 | S |
| S25 | Ревью: новое и прежние решения (`setup review`) | S24 | M |
| **E** | **Сервер** | | |
| S26 | ✅ Общий протокол MCP; изоляция ошибок | — | S |
| S27 | `docpipe setup serve` | S10–S14, S23–S26 | M |
| **F** | **Скилл и интервью** | | |
| S28 | ✅ Протокол интервью: каталог вопросов | S09, S24 | M |
| S29 | Скилл `setup`: три входа, фазы, тесты | S27, S28 | M |
| **G** | **Поставка** | | |
| S30 | ✅ Нейтральный набор, `--bundle`, второй сервер, слияние настроек агента | S01, S27 | M |
| **H** | **Проверка** | | |
| S31 | Прогон на открытых репозиториях | S29, S30 | L |
| S32 | Прогон на контуре | S31, доступ | M |

S01 стоит первой: она маленькая и возвращает на контур уже готовые `recon`
и `graph serve`, которые сейчас там невидимы. Этапы A, B и S15 независимы
между собой и идут в любом порядке; S26 не зависит ни от чего. Критический
путь — S15 → S16 → S18 → S19 → S20 → S21 → S24 → S27 → S29 → S31.

Номера аналитики (§9) → номера плана: S23a→S01, S03→S02+S03, S04→S04,
S05+S13→S05, S06→S06, S07→S07, S02→S08, S01→S09, S08→S10, S09→S11, S10→S12,
S11→S13, S12→S14, S27→S15, S28→S16–S20, S26→S21, S14→S22, S30→S23,
S16→S24, S15→S25, S17→S26, S18→S27, S20→S28, S21+S22→S29, S23→S30,
S24→S31, S25→S32. Инвентарь форм шва (S27 аналитики) снят прогоном 07.10,
поэтому от него осталась фикстура, а «расширение формата» распалось на пять
задач — по одной на форму дефекта.

---
## S01 — скилл и MCP-запись в `.gigacode/` ✅

**Цель:** агент контура видит скилл `recon` и сервер `graph serve`. Сейчас
оба лежат в `.qwen/`, а gigacode читает только `.gigacode/` (подтверждено
владельцем 07.10; официальная документация GigaCode CLI называет
`.gigacode/skills/`, `.gigacode/agents/`, `.gigacode/settings.json`).

**Изменить:** `.qwen/skills/recon/` → `.gigacode/skills/recon/` (`git mv`),
ссылка `.claude/skills/recon`, `.gitignore`, `deploy/install.sh`,
`tests/test_recon_skill.py`, `tests/test_deploy_bundle.py`, справочники ниже.

**Спецификация**

1. `git mv .qwen/skills/recon .gigacode/skills/recon`. Ссылка для Claude Code
   пересоздаётся относительной: `.claude/skills/recon -> ../../.gigacode/skills/recon`
   (в git — режим `120000`). Ссылка `../../../docs/arch-registry.md` внутри
   `SKILL.md` остаётся верной: глубина каталога та же.
2. `.gitignore:40-52`: строки `/.qwen/settings.json` и `/.qwen/settings.json.new`
   заменить на `/.gigacode/settings.json` и `/.gigacode/settings.json.new`,
   комментарии — про `.gigacode/skills/`. `/.claude/*` и `!/.claude/skills/`
   остаются.
3. `install.sh:322-353`: запись `mcpServers.docpipe` — в
   `$SOURCE/.gigacode/settings.json`; подпись в `keep_configured` и сообщение
   про доверенную папку (`386-394`) — про `.gigacode`. Если в клоне лежит
   `$SOURCE/.qwen/settings.json` с ключом `mcpServers.docpipe`, установщик
   печатает в stderr: «`.qwen/settings.json` агент контура не читает; запись
   перенесена в `.gigacode/settings.json`, старый файл можно удалить» —
   и **не** удаляет его сам.
4. Тесты: `test_recon_skill.py:25` (`SKILL`), `:26` (`CLAUDE_LINK` — путь тот же,
   цель новая); `test_deploy_bundle.py:684-685, 816, 825, 835, 839, 843-844,
   857-859` — `.qwen` → `.gigacode`. Новый тест: при лежащем
   `.qwen/settings.json` с `mcpServers.docpipe` в stderr есть строка о переносе.

> **Ловушка. Шаблон `.gitignore` без ведущего `/` действует на любой глубине**
> (CLAUDE.md, «Поставка»). `.gigacode/settings.json` без `/` закроет и
> одноимённый файл в любом подкаталоге — в том числе в фикстурах.

> **Ловушка. Ссылка `.claude/skills/recon` абсолютной быть не может**: клон
> на контуре лежит по другому пути, и ссылка станет висящей. Тест
> `test_claude_sees_the_same_skill_through_a_link` сравнивает `resolve()`.

> **Ловушка. `keep_configured` сравнивает файл целиком.** Новый путь — новый
> файл, поэтому на уже установленной машине запись ляжет сразу, а старый
> `.qwen/settings.json` останется. Удалять его молча нельзя: в нём могут быть
> чужие серверы.

**Справочники:** `CLAUDE.md:115, 207, 250`; `README.md:161, 855`;
`deploy/README.md:144-199`; `deploy/OFFLINE.md:186`;
`docs/graph-implementation-plan.md:76, 857, 1967, 1969`;
`docs/module-review.md:144, 322`; `purpose.md` («Условия среды» — убрать
оговорку «пока доставляет в `.qwen/`»); `docs/backlog.md` («Доставка на контур»);
шапка этого плана — ссылка на скилл.

**Критерии приёмки**
- `git grep -n '\.qwen'` находит строки только там, где `.qwen/` назван
  прежним местом: предупреждение установщика и его тест, комментарий
  `.gitignore`, справочники с пометкой «до 07.10», журнал, аналитика и этот
  раздел плана;
- `test_recon_skill.py` и `test_deploy_bundle.py` проходят на новых путях;
- установка на копии клона пишет `.gigacode/settings.json` с прежней записью
  `mcpServers.docpipe` (тот же тест, что `:815-835`);
- при лежащем `.qwen/settings.json` установщик говорит о переносе и файл не трогает.

**Проверка**
```bash
uv run pytest tests/test_recon_skill.py tests/test_deploy_bundle.py -q
git grep -n '\.qwen' -- ':!docs/implementation-log.md' ':!docs/setup-assistant-analysis.md' ':!docs/setup-implementation-plan.md'
```

---

## S02 — загрузка настройки без молчания ✅

**Цель:** опечатка в файле настройки — отказ с адресом, а не пустой результат.
Ассистент правит файлы сам (Р-1), и опечатка агента должна стоить одного
прогона, а не вечера поисков.

**Изменить:** `docpipe/ruleset.py`, `docpipe/classify.py`,
`docpipe/materialize/ownership.py`, `docpipe/web/overrides.py`,
`docpipe/config.py`, `docpipe/cli.py`
**Создать:** `tests/test_config_strict.py`

**Спецификация**

1. **Неизвестные ключи — отказ.** `ruleset.load_rule_items(raw, path, required,
   *, allowed: frozenset[str] | None = None)` — новый параметр ключевой
   и с умолчанием: в тестах функция зовётся позиционно с тремя аргументами.
   При `allowed` лишний ключ элемента — `ValueError`:
   `"{path}: правило #{index} ({id}): неизвестный ключ {key!r}; допустимы: {…}"`.
   Допустимые множества — константы рядом с загрузчиками:

   | Где | Допустимо |
   |---|---|
   | `exclude.rules[]` (`classify.py:287`) | `id, reason, priority, when, unless` |
   | `rules[]` (`classify.py:363`) | `id, kind, template, priority, when` |
   | секция правил | `ruleset_version, exclude, rules` |
   | верх файла правил | `version` и `RULE_SECTIONS = ("dotnet", "web")` |
   | `ownership.yaml`, верх | `version, ownership_version, teams, rules` |
   | `teams[]` | `id, title` |
   | `ownership.rules[]` (`ownership.py:214`) | `id, team, priority, when` |
   | `pages.yaml`, верх | `version, pages, add, remove, features` |
   | `pages.yaml`, тело `pages:` | `add, remove, features` |

   У `unless` в правиле классификации — своя подсказка: «`unless` есть только
   у правил отсева (`exclude.rules`)»: сейчас он там молча игнорируется.
   Сверено 07.10: ни в `rules/`, ни в бандле, ни в `*.example.yaml`, ни
   в фикстурах и YAML-строках тестов лишних ключей нет — отказ ничего
   существующего не сломает.
2. **Названный `web.pages` без файла — отказ.** `cli.py:341-346`: вместо
   `else Overrides()` — `FileNotFoundError` с кандидатами из
   `candidate_inputs`; `web scan` превращает её в код 2 (`cli.py:671-673`).
   Ключ пуст — по-прежнему пустые правила. `symbols --lang ts` (`cli.py:514`)
   зовёт `run_web_scan` без правил `pages.yaml` — передать их через ту же
   `_load_page_overrides`: иначе два прогона фронта считают по-разному.
3. **Пустой список — отказ.** `load_config` (`config.py:409-413`) выбрасывает
   ключи верхнего уровня со значением `None`. Для ключей-списков (`roots`,
   `enrolled`, `exclude`, `docs_scan_exclude`, `dispatch_interfaces`,
   `di_methods`, `arch_adapters`; список строится по аннотациям
   `DocpipeConfig.model_fields`, а не руками) `None` — `ValueError`:
   «`enrolled:` без элементов — напишите `enrolled: []` или удалите ключ».
   Для вложенных списков `web.*` — то же сообщение вместо `ValidationError`.
4. **Валидаторы:** `graph.cache_dir` — как у `cache_dir` (абсолютный путь
   разрешён, иначе `_repo_relative`: без `..` и `\`); `arch_adapters[].id`
   уникальны (`model_validator` в `DocpipeConfig`); повтор `module`
   в `web.url_rewrite` — отказ (сейчас `rewrite_for` молча берёт первую,
   `config.py:125-127`).
5. **`--format` проверяется** во всех командах, где он есть: `symbols`,
   `diff`, `docs status`, `web link`, `web pages`, `anchors list/which`,
   `business status`. Общий помощник в `cli.py`
   `_format(value: str, allowed: tuple[str, ...]) -> str` бросает
   `typer.BadParameter` (код 2).

> **Ловушка. Закомментированный список молча становится «всё».** `enrolled:`
> с одними комментариями под ним — это `None`, ключ выбрасывается, и
> срабатывает умолчание `["**"]`: каждый модуль включён, без единого сообщения
> (проверено 07.10). Агент, «временно выключивший» строку комментарием,
> получит противоположное задуманному.

> **Ловушка. Список секций правил — константа, а не «любые ключи».** В ветке
> `feat/python-parser` готовится секция `python`; строгий верх файла закроет
> её, если список не расширяется в одном месте.

> **Ловушка. `pages.yaml` бывает двух форм**: с телом `pages:` и плоский
> (`add`/`remove`/`features` наверху), и `features` разрешены в обоих местах
> (`overrides.py:183-197`). Строгая проверка обязана принять обе.

**Критерии приёмки**
- лишний ключ в каждом из девяти мест таблицы — отказ с именем файла,
  номером правила и перечнем допустимых;
- `unless` в правиле классификации — отказ с подсказкой;
- `web.pages: pages.yaml` без файла — `web scan` с кодом 2 и кандидатами;
- `enrolled:` без элементов — отказ загрузки с текстом про `[]`;
- `graph.cache_dir: ../x` — отказ; два адаптера с одним `id` — отказ;
  два `url_rewrite` одного модуля — отказ;
- `--format jsno` у каждой из семи команд — код 2;
- бандл (`test_bundle_config_loads`), `docpipe.example.yaml`, `rules/rules.yaml`
  и все фикстуры загружаются без изменений.

**Проверка**
```bash
uv run pytest tests/test_config_strict.py tests/test_classify.py tests/test_ownership.py tests/test_web_overrides.py tests/test_deploy_bundle.py -q
```

**Справочники:** `docs/configuration.md` (пустой список, повтор модуля),
`README.md` («опечатка роняет загрузку» — теперь правда и для ключей правила).

---

## S03 — прогоны без молчания ✅

**Цель:** шесть мест, где прогон проходит и даёт правдоподобный неполный
результат, называют это вслух.

**Изменить:** `docpipe/materialize/plan.py`, `docpipe/cli.py`,
`tests/test_relocation.py`
**Создать:** `tests/test_silent_runs.py`

**Спецификация**

1. **Смена `doc_layout` без повторного `scan`.** `layout_drift`
   (`plan.py:631-663`) сверяет только префикс `docs_root + modules_dir`, а
   `kind-first` и `module-first` дают один префикс — смена раскладки проходит,
   хотя `CLAUDE.md` и `configuration.md` обещают отказ. В `PlanOptions`
   (`plan.py:170-188`) — поле `doc_layout`; новая проверка в `_blocking_errors`
   (`plan.py:702-713`): для каждого узла посчитать `doc_path` **той же
   функцией, которой его считает шаг 1** (`tree.doc_path_for`, `tree.py:284-307`;
   для фронта — с `web_modules_root`), при раскладке из конфигурации и при
   другой. Сравнивать **каталоги** (`PurePosixPath(p).parent`): суффикс
   коллизии (`_assign_doc_paths`) меняет только имя файла. Если каталоги
   узлов совпадают с другой раскладкой, а с текущей нет — отказ:
   «манифест собран с раскладкой `module-first`, конфигурация говорит
   `kind-first`: пересоберите манифест (`docpipe scan`) или верните ключ».
   Узлы, у которых обе раскладки дают один каталог (имя модуля равно `{kind}s`),
   в сверку не входят.
2. **`graph build` отбрасывает ошибки адаптеров** (`cli.py:1870-1876` берёт
   только `.registry`; `arch/collect.py:62` складывает исключения в `errors`).
   Вынести сборку реестра для графа в функцию
   `arch.collect.registry_for_build(settings, config, root) -> ArchRegistry`,
   которая при непустом `errors` или при названном в `arch`, но отсутствующем
   файле бросает `ValueError` с перечнем; `graph build` — код 2. Не задан
   `arch` и нет адаптеров — по-прежнему пустой реестр: `load_optional(None)`
   обязан остаться пустым (`test_arch_registry.py:64`).
3. **Шаг 2 глотает ошибки владения и реестров.** `_load_ownership_quietly`
   (`cli.py:1022-1035`) читает только `settings.ownership` и игнорирует
   `--ownership`; `_with_business_links` (`cli.py:1059`) выбрасывает ошибки
   `read_anchors` и не смотрит `catalog.errors`. Бизнес-ссылки строятся
   от того же объекта владения, что и план (`_prepare`); ошибки реестров
   и каталога — предупреждения в stderr с префиксом «бизнес-ссылки:».
   Код возврата не меняется: бизнес-ссылки — поле проекции, и отказ всего
   `materialize` из-за реестра вне цели был бы хуже молчания. В S06 эти
   предупреждения уходят в `Step2Inputs.warnings`, в S24 — в дефект
   `load.errors`.
4. **`scan --stats` и `--dry-run` молчат о неполноте.** Строки про
   `partial`, `missing_from_cache` и `parse_error_files` (`cli.py:312-327`)
   печатаются только в режиме записи — перенести их до ветвления
   `--stats`/`--dry-run` (stderr).
5. **`web scan --stats` молчит о протухших правилах.** `--stats`
   (`cli.py:700-703`) возвращается раньше печати протухших правил `pages.yaml`
   (`731-734`) и проверки `--fail-on-stale-overrides` (`741-747`) — перенести
   обе до возврата.
6. Комментарий `cli.py:288` («ничего не пишет»): кэш разбора `--stats` пишет
   (`<root>/.docpipe/cache`), не пишутся манифест и сидкар — исправить текст.

> **Ловушка. Сверять манифест с конфигурацией, а не документы на диске
> с конфигурацией.** Штатная процедура смены раскладки
> (`docs/materialize.md:429`) — пересканировать, затем `materialize` переносит
> документы. Сверка «диск против конфигурации» сделает её невыполнимой.
> Тесты `test_relocation.py:197` и `:217` гонят `materialize` с манифестом
> `module-first` без `--config`, то есть при умолчании `kind-first`: им нужна
> конфигурация с `doc_layout: module-first` на первом прогоне и на `docs accept`.

> **Ловушка. `docpipe.tree` может тянуть `docpipe.dotnet`**, а `materialize/*`
> его импортировать не вправе (`tests/test_materialize_cli.py:420`). Если так,
> `doc_path_for` выносится в `docpipe/layout.py`, и её импортируют обе стороны.
> Своя копия формулы раскладки в `plan.py` разойдётся с шагом 1 на первой
> правке.
>
> Проверено 07.10: не тянет (`tree` → `classify`, `config`, `discovery`,
> `hashing`, `model`, `symbols`, `route`, `ruleset`), поэтому выноса нет —
> `plan.py` импортирует `docpipe.tree.doc_path_for`. Тест границы в
> `test_materialize_cli.py` смотрит только прямые импорты пакета, поэтому
> транзитивные держит `test_silent_runs.py::test_plan_does_not_load_dotnet`
> (подпроцесс, `sys.modules`): потянет — выносить, а не копировать.

> **Ловушка (найдена при реализации). `missing_from_cache` всегда был нулём.**
> `_outside_scope_results` (`emit.py`) перебирал строки самого кэша и считал
> «нет в кэше» те, что не читаются, — а таких не бывает: холодный кэш пуст,
> кэш другой версии очищается целиком. Ровно в случае, ради которого счётчик
> заведён (`test_cold_cache_breaks_classification_outside_scope`: контроллер
> исчезает), он стоял на нуле, и перенос печати до ветвления п. 4 ничего бы
> не дал. Теперь «нет в кэше» считается по ожидаемым файлам — источникам узлов
> прежнего манифеста вне скоупа; при `--no-cache` в скоуп-режиме они все
> «нет в кэше». Это нижняя граница (файлы без узлов прежний манифест не знает),
> но холодный кэш она ловит всегда. Отклонение от «Изменить»: правка `emit.py`.

> **Ловушка (найдена при реализации). Скоуп-прогон после смены `doc_layout`
> даёт смешанный манифест**: `merge_manifests` берёт узлы вне скоупа из прежнего
> манифеста вместе с путями. Поэтому отказ — при **хоть одном** узле, разложенном
> по другой раскладке, а не при всех, и сообщение отсылает к полному `scan`
> (`без --scope`). У фронта команда пересборки своя — `docpipe web scan`.

> **Ловушка (найдена при реализации). Раскладку `module-first` несут не только
> `test_relocation.py`.** Синтетический манифест бизнес-слоя
> (`tests/business_support.py`, `docs/modules/App/{kind}s/…`) разложен так же,
> и `test_business_build.py::test_materialize_adds_business_context…` получил
> бы отказ; в `test_relocation.py` — три теста, а не два
> (`…_is_idempotent` тоже начинает с `module-first` и без конфигурации остался
> бы зелёным, проверяя создание дерева с нуля). Всем им — конфигурация
> с `doc_layout: module-first` на прогонах по такому манифесту.

> **Ловушка (найдена при реализации). `graph build` различает «реестра нет»
> и «реестр пуст».** `build_graph(arch=None)` не заводит ни швов, ни узлов
> данных из реестра, а пустой `ArchRegistry` добавляет их счётчики в паспорт.
> Поэтому `registry_for_build` возвращает пустой реестр по спецификации,
> а `graph build` зовёт её только при заданном `arch` или адаптерах и иначе
> передаёт `None`, как прежде.

> **Ловушка (найдена при реализации). Копия фикстуры уносит локальный кэш.**
> `tests/fixtures/SampleSolution/.docpipe/` — кэш ручных прогонов из корня
> (`docpipe scan --root tests/fixtures/SampleSolution`), git его не видит.
> `shutil.copytree` без `ignore=shutil.ignore_patterns(".docpipe")` делает кэш
> тёплым у разработчика и холодным на свежем клоне: тест холодного кэша через
> CLI падал локально и прошёл бы в CI.

Отклонения при реализации (07.10): строка `parse_error_files` в режимах
`--stats`/`--dry-run` называет файлы (до пяти) вместо «см. в сидкаре» — сидкар
там не пишется, а прежний описывает прошлый прогон; все три строки неполноты
`scan` теперь в stderr и в режиме записи. `adapter_specs` и сборка «снимок
плюс адаптеры по конфигурации» (`collect_configured`) переехали из `cli.py`
в `arch/collect.py` рядом с `registry_for_build`. Не сделано и вне S03:
`web scan --stats` по-прежнему молчит о `parse_error_files` (войдёт в
`StatsReport`, S05); синтаксическая ошибка YAML в `docpipe.yaml`,
`ownership.yaml` и других файлах настройки даёт трейсбек и код 1, а не
сообщение и код 2 — `yaml.YAMLError` не наследует `ValueError`, и ни один
загрузчик её не переводит.

**Критерии приёмки**
- манифест `module-first` при конфигурации `kind-first` — `materialize`,
  `docs status`, `worklist` отказывают с текстом про `docpipe scan`;
- манифест и конфигурация совпадают — ни одного нового отказа на существующих
  тестах шага 2 (после правки `test_relocation.py`);
- `registry_for_build` при ошибке адаптера и при названном несуществующем
  реестре — `ValueError`; без `arch` — пустой реестр;
- битый `ownership.yaml` при названном `--ownership` — та же ошибка, что
  у плана, а не молчаливое `None`;
- `scan --scope … --stats` печатает `missing_from_cache`;
  `web scan --stats --fail-on-stale-overrides` с протухшим правилом — код 1.

**Проверка**
```bash
uv run pytest tests/test_silent_runs.py tests/test_relocation.py tests/test_materialize_plan.py tests/test_arch_adapters.py -q
```

**Справочники:** `docs/materialize.md` (сверка раскладки), `docs/configuration.md`
(«смена любого из трёх ключей» — теперь правда), `docs/arch-registry.md`
(отказ `graph build`).

---

## S04 — `config check`: функция, JSON, коды возврата ✅

**Цель:** первый инструмент ассистента отвечает структурой и отказывает там,
где настройка действительно сломана.

**Создать:** `docpipe/configcheck.py`, `tests/test_config_check.py`
**Изменить:** `docpipe/cli.py:3284-3417` (только печать и коды)

**Спецификация**

`check_config(settings: DocpipeConfig, config: Path | None, root: Path, cwd: Path) -> ConfigReport`.
Модели (`frozen`, `extra="forbid"`):

```python
class InputCheck:      key: str; value: str; candidates: list[str]; found: str | None; step: Literal["cwd", "config"] | None
class TargetCheck:     key: str; value: str; resolved: str; parent_exists: bool
class RootKeyCheck:    key: str; value: str; resolved: str; exists: bool
class RootsCheck:      key: Literal["roots", "web.roots"]; entry: str; resolved: str; exists: bool
class AdapterInput:    adapter_id: str; option: Literal["spec", "path"]; base: Literal["config", "root"]; value: str; resolved: str; exists: bool
class EngineCheck:     configured: bool; path: str; exists: bool
class ConfigProblem:   code: Literal["input-missing", "root-missing", "adapter-input-missing", "engine-missing"]; key: str; message: str
class ConfigReport:    schema_version: Literal["1.0"]; config: str | None; cwd: str; root: str
                       inputs: list[InputCheck]; targets: list[TargetCheck]; root_keys: list[RootKeyCheck]
                       roots: list[RootsCheck]; adapter_inputs: list[AdapterInput]; engine: EngineCheck
                       modules_root: str; web_modules_root: str | None; problems: list[ConfigProblem]
```

Списки ключей переезжают из `cli.py` (`_INPUT_KEYS` 3294, `_TARGET_KEYS` 3303,
`_ROOT_KEYS` 3314) в модуль. Добавляются: `roots` и `web.roots` (каждый
элемент — каталог от `--root`; нет каталога — `root-missing`: сейчас такой
корень молча даёт ноль файлов, `discovery.py:170`), входы адаптеров
(`options.spec` — через `resolve_input`, как его читает `declared.py:270`;
`python_code.options.path` — от `--root`, `code.py:87`).

Коды возврата: 2 — конфигурация не читается (как сейчас); 1 — есть хотя бы
одна проблема; 0 — иначе. «КАТАЛОГА НЕТ» у цели записи проблемой **не**
является: каталоги создают все писатели (`emit.py:102`, `documents/write.py:75`,
`graph/store.py:126`), а умолчание `graph.cache_dir` даёт «КАТАЛОГА НЕТ»
на любом свежем репозитории. Комментарий `cli.py:3379-3381` неверен —
исправить. Названный, но отсутствующий движок — `engine-missing`.

Текст печатается форматтером из отчёта и сохраняет нынешние формулировки:
на них опираются тесты `tests/test_config_paths.py:336-416`.

> **Ловушка. У входов адаптеров две разные базы**: `options.spec` ищется
> по двум ступеням `resolve_input`, `python_code.options.path` — от `--root`.
> Поле `base` в отчёте обязательно: по имени опции базу не угадать — ровно
> та ловушка, ради которой `config check` и заведён.

> **Ловушка. `_key_value` (`cli.py:3321`) понимает один уровень точки.**
> `arch_adapters[]` — список, его обходят отдельно, а не строкой ключа.

**Критерии приёмки**
- `config check --format json` на конфигурации из `nested`-фикстуры
  (`test_config_paths.py:28-45`) — валидный `ConfigReport`, `step` у входов
  совпадает с тем, что печатает текст;
- `roots: [нет-такого]` — код 1, `root-missing`;
- адаптер со `spec`, которого нет, — код 1, `adapter-input-missing`, `base: config`;
- `graph.engine_path` на несуществующий файл — код 1; не задан — код 0;
- все шесть тестов `test_config_paths.py:336-416` проходят без правки
  (тест на движок — с проверкой кода 1, его можно ужесточить).

**Проверка**
```bash
uv run pytest tests/test_config_check.py tests/test_config_paths.py -q
uv run docpipe config check --config deploy/cashflow-docspipe/docpipe.yaml --root . --format json
```

**Справочники:** `docs/configuration.md` («Проверка путей»), `deploy/README.md`.
`docs/module-review.md`: `configcheck.py` — развивается, потребитель
`config check` и S27.

---

## S05 — JSON у `scan --stats` и `web scan --stats`; срез «последнее слово» ✅

**Цель:** вопрос интервью «120 типов в `*.Migrations` — документировать?»
строится из числа, которое агент получает структурой, а не разбором текста.

**Изменить:** `docpipe/stats.py`, `docpipe/hashing.py`, `docpipe/cli.py`
**Создать:** `tests/test_stats_report.py`

**Спецификация**

`build_stats_report(stats: Stats, *, lang, top, stale, scope) -> StatsReport`:

```python
class Slice:        total: int; items: list[tuple[str, int]]        # усечено до top, total — до усечения
class SkippedRule:  rule_id: str; reason: str; count: int
class StaleOverride: kind: str; key: str; reason: str
class ScopeInfo:    partial: bool; restored_from_cache: int; missing_from_cache: int
class StatsReport:  schema_version: Literal["1.0"]; lang: Literal["cs", "ts"]; total: int
                    decisions: dict[str, int]      # documented, not_documented, undecided, interface_covered, page_covered, not_enrolled
                    kinds: list[tuple[str, int]]   # виды из правил
                    skipped: list[SkippedRule]
                    breakdown: dict[Literal["modules", "suffixes", "last_words", "base_types", "attributes", "namespaces"], Slice]
                    stale_overrides: list[StaleOverride]   # только web
                    scope: ScopeInfo | None; parse_error_files: list[str]
```

`Stats.counts` (`stats.py:163-176`) смешивает виды и особые состояния
в одном словаре — в отчёте они разведены по `_SPECIAL` (`stats.py:87`).
`scan --stats --format json` и `web scan --stats --format json` печатают
отчёт; `--top` действует и на JSON (усечение срезов, `total` — до усечения).

> **Уточнено при реализации.** Полная сигнатура —
> `build_stats_report(stats, *, lang, top=TOP, stale=(), scope=None,
> parse_error_files=())`: списка файлов с ошибками разбора в `Stats` нет,
> его передаёт вызывающий из `RunMeta`. `scope` у .NET собирает
> `stats.scope_info(manifest, meta)` — одна функция на CLI и сервер (S27);
> у `web` скоупа нет, и там `None`, а не «не частичный». `stale` — протокол
> с `kind`/`key`/`reason`, а не `web.overrides.StaleRule`: счётчики — общий
> слой и от шага `web` не зависят. В отчёте всегда все шесть ключей
> `decisions` и все шесть срезов (пустой — `total: 0`): отсутствие ключа
> читалось бы как «не посчитано». `--format json` без `--stats` и
> отрицательный `--top` — код 2.

> **Ловушка. `CliRunner.invoke(...).output` смешивает stdout и stderr.**
> Предупреждения о протухших правилах `pages.yaml` и о неполноте прогона
> идут в stderr, и `json.loads(result.output)` падает ровно на тех прогонах,
> ради которых JSON и нужен. Тесты JSON читают `result.stdout`.

**Новый срез «последнее слово имени»** — по `undecided`, как остальные срезы
(`_breakdown`, `stats.py:243-266`): последнее слово CamelCase имени символа.
Разбиение — публичная `hashing.camel_words(name) -> list[str]` поверх
`_CAMEL_BOUNDARY` (`hashing.py:16`); `slugify` переходит на неё. Срез «окончания
имён» знает 27 зашитых суффиксов .NET (`stats.py:30-58`), а конвенции проекта
(`*Rq`, `*Dm` в `CASHFLOW.md` §3.1) и фронта (`Component`, `Guard`, `State`)
в него не попадают — «(прочее) 3446», и зацепиться не за что.

> **Ловушка. Ключи `breakdown` — русские строки** («модули», «окончания имён»…),
> и тесты на них опираются (`test_stats.py:126-133`). Ключи словаря `Stats`
> не трогать; в отчёте — таблица соответствия на латинские ключи.

> **Ловушка. Разбиение CamelCase.** Цифры прилипают к слову
> (`Migration20240101` → `Migration20240101`), имена TypeScript начинаются
> со строчной (`authInterceptor` → `Interceptor`), префикс интерфейса
> отделяется (`IPricingProvider` → `I`, `Pricing`, `Provider`). Тест
> на каждую форму; последнее слово `I` в срез не идёт.

**Критерии приёмки**
- на `SampleSolution`: `decisions` = 6/1/1/2 (documented / not_documented /
  undecided / interface_covered), сумма равна `total` (10);
- на `WebWorkspace`: 25/4/0, `page_covered` 5;
- `stale_overrides` непуст на фикстуре с протухшим правилом `pages.yaml`;
- срез `last_words` на тестовом наборе имён — по тесту на каждую ловушку;
- текстовый вывод прежний плюс строка нового среза.

**Проверка**
```bash
uv run pytest tests/test_stats_report.py tests/test_stats.py -q
uv run docpipe scan --root tests/fixtures/SampleSolution --stats --format json --no-cache
```

**Справочники:** `README.md` (шаг 0–1 настройки), `docs/web.md` (`--stats`).

---

## S06 — вход шага 2 в библиотеку; JSON у команд шага 2 и `arch validate`; сверка схем ✅

**Цель:** `materialize`, `docs status`, `worklist` можно позвать из сервера
без CLI; агент получает план и вердикты структурой.

**Создать:** `docpipe/step2.py`, `tests/test_step2.py`, `tests/test_schemas.py`
**Изменить:** `docpipe/cli.py:1090-1165` и вызывающие, `docpipe/materialize/status.py`,
`docpipe/materialize/explain.py`, `docpipe/materialize/ownership.py`

**Спецификация**

1. **`docpipe/step2.py`.** Переезжают `Step2Inputs` (+ поле `warnings: list[str]`
   из S03) и логика `_prepare`:
   `load_manifest(path) -> Manifest`;
   `prepare(manifest, root, settings, config, *, templates_dir=None,
   ownership_file=None, teams=(), accept=(), force=False, links=False) -> Step2Inputs`.
   Ошибки — `Step2Error(code: int, message: str)`; CLI-обёртка `_prepare`
   зовёт `load_config` **внутри** try (сейчас вне, `cli.py:1119`: битая
   конфигурация даёт traceback и код 1) и превращает `Step2Error` в печать
   и `typer.Exit(code)`. `links=True` применяет `with_links(plan,
   check_links(...))` — как сейчас в `docs status`, `docs explain`, `worklist`.
   Восемь мест вызова (`materialize` 588, `docs status` 947, `docs explain` 988,
   `worklist` 1215, `docs accept` 1287/1319, `docs adopt` 1362/1416) — через неё.
2. **`docs status --format json`**: конверт получает `schema_version: "1.0"`,
   `notes` (`MaterializePlan.notes`), `substituted: [{template, count}]`,
   `document_errors: [{doc_path, error}]` (`PlannedDoc.error`). Сами записи
   документов (`document_json`, `status.py:73-99`) **не меняются**.
3. **`materialize --dry-run --format json`** → `MaterializeReport`:
   `counts` по `file_action`, `documents: [{doc_path, node_id, file_action,
   status, relocate_from, confidence}]`, `notes`, `substituted`, `errors`.
4. **`docs explain --format json`** → `{doc_path, scan_verdict, plan: {status,
   file_action, reason, relocate_from}, zone_diff: {front_matter,
   generated_changed, sections_added, sections_changed, sections_removed,
   touches_authored}}` из `scan_verdict` (`explain.py:45-81`) и `ZoneDiff`
   (`89-117`); коды возврата прежние (`cli.py:1001-1019`).
5. **`docs owners --lint --format json`.** Новая
   `ownership.lint_findings(nodes, ownership) -> OwnershipLint` с находками
   `{code, subject, count, message}` — по одному коду на каждый вид строки,
   который печатает нынешний `lint` (`ownership.py:273-336`); `lint()`
   остаётся текстовой обёрткой над ней.
6. **`arch validate --format json`** → `{schema_version, path, valid,
   problems: [{where, message}]}` из `check_document` (`arch/load.py:41-144`).
7. **Сверка схем.** `tests/test_schemas.py`: для каждой записи `SCHEMA_MODELS`
   (`cli.py:169-173`) сгенерированная схема равна закоммиченной
   в `schema/`. Закрывает пункт бэклога «Схемы в `schema/` не сверяются»
   (`doc-tree.schema.json` однажды отстал на шесть полей незаметно).

> **Ловушка. `step2.py` — не в `docpipe/materialize/`.** Бизнес-ссылки
> (`_with_business_links`) импортируют `docpipe.business`, а
> `test_materialize_does_not_import_business` (`tests/test_materialize_cli.py:427`)
> запрещает это пакету `materialize`. Отдельный модуль верхнего уровня
> вправе импортировать оба.

> **Ловушка. `WorklistEntry` — `extra="forbid"` и собирается
> из `document_json`.** Новое поле в записи документа роняет `worklist`,
> а `test_json_shape` (`test_docs_status.py:70-99`) держит точное множество
> из 15 ключей. Поэтому новое — только в конверте `docs status`.

> **Ловушка. `lint` владения зовут семь тестов** как
> `findings, warnings = lint(...)` (`test_ownership.py`, `test_ownership_web.py`):
> структура — новой функцией, старая остаётся.

> **Ловушка (найдена при реализации). `read_anchors` жила в `cli.py`.**
> Бизнес-ссылки зовут её, а `step2.py` импортировать CLI не вправе (цикл
> и тот самый шов, который выносом убирается). Перенесена в
> `registry/anchors.py` рядом с `resolve_anchors`; `anchors *` и `business *`
> берут её оттуда же. Своя копия в `step2.py` была бы четвёртой сборкой
> цепочки «реестры → якоря», а их однажды было три.

> **Ловушка (найдена при реализации). «Одна строка» — только у отказов
> самого загрузчика.** `enrolled:` без элементов даёт одну строку, а
> неизвестный ключ — `ValidationError` pydantic на четыре строки (`extra_forbidden`
> и ссылка на errors.pydantic.dev) — у всех команд, не только у шага 2.
> Критерий проверяется на первом (`test_broken_config_is_code_2_and_one_line`),
> на втором — код 2 без трассировки. Синтаксическая ошибка YAML по-прежнему
> даёт трассировку и код 1 (`yaml.YAMLError` не `ValueError`) — пункт бэклога S03.

> **Ловушка (найдена при реализации). Срез «топ-10» линта владения зависел
> от порядка узлов.** `Counter.most_common` при равных числах оставляет
> порядок первого появления, то есть порядок узлов манифеста. Структура
> упорядочена явным ключом `(-count, имя)`, текст режет её первые десять —
> при равенстве чисел порядок строк в тексте может смениться. Списки в JSON
> полные: срез по десять — дело печати, иначе агент не узнал бы, что модулей
> без владельца больше десяти.

> **Ловушка (найдена при реализации). Код возврата `docs explain` и строка
> отчёта считали разницу зон каждая сама.** Нормализация текста с диска
> (BOM, CRLF) была записана дважды — в форматтере и в CLI. Теперь одна
> `explain.disk_text`, и код 1 берётся из `report.zone_diff.touches_authored`
> — из того же сравнения, что печатается.

Отклонения при реализации (07.10): модели отчётов лежат рядом с форматтерами
своих команд, а не в `step2.py` — `StatusReport` в `status.py`,
`MaterializeReport` в `apply.py` (рядом с `format_result`), `ExplainReport`
в `explain.py`, `OwnershipLint` в `ownership.py`, `ArchValidation`
в `arch/load.py` (`validate_document`). Поля сверх спецификации — то, что
печатает текст: у `MaterializeReport` — `schema_version`, `dry_run`,
`manifest_partial` (`--format json` работает и без `--dry-run`); у
`ExplainReport` — `schema_version`, `exists` (иначе `scan_verdict: null`
не отличает «принят» от «файла нет»), `errors` (блокирующие ошибки плана)
и в `plan` — `file_action_rule`, `confidence`, `agent_action`, `node_id`,
`team`, `error`, `empty_sections`, `orphan_sections`; у `ArchValidation` —
`version` и `counts`; у `OwnershipLint` — `nodes` (знаменатель «N из M»)
и два списка `findings`/`warnings` (stdout и stderr текста). `StatusReport.documents`
— словари `document_json`, а не модель: модель записи уже есть
(`WorklistEntry`), а `worklist.py` импортирует `status.py`, не наоборот.
`docs owners --format json` без `--lint` — код 2. Предупреждения
`Step2Inputs.warnings` хранятся с префиксом «бизнес-ссылки: », CLI печатает
их как есть. `prepare` принимает уже прочитанный `Manifest`, файл читает
`load_manifest` (`Step2Error(2, …)`). Номера строк в «Изменить» и п. 1
(`cli.py:1090-1165`, «восемь мест вызова» с номерами) к моменту S06 уехали
после S03/S04/S11; все восемь мест уже шли через `_prepare` и не менялись.

**Критерии приёмки**
- `prepare` зовётся в тесте без CLI на `SampleSolution` и даёт тот же план,
  что `materialize --dry-run`;
- битая конфигурация у `docs status` и `docs owners` — код 2 и одна строка;
- `docs status --format json` содержит `notes`, `substituted`,
  `document_errors`; `test_json_shape` проходит без правки;
- `worklist` не меняется байт в байт (двойной прогон, `cmp` с прежним выводом);
- `docs owners --lint --format json` — тот же набор находок, что текст;
- `test_schemas.py` проходит.

**Проверка**
```bash
uv run pytest tests/test_step2.py tests/test_schemas.py tests/test_docs_status.py tests/test_materialize_cli.py tests/test_ownership.py -q
```

**Справочники:** `docs/materialize.md` (JSON), `docs/backlog.md` (закрыть пункт
про схемы). `module-review.md`: `step2.py` — развивается, потребитель —
команды шага 2 и S24/S27.

---

## S07 — `symbols`: причина, страница, правило-победитель, `--path` ✅

**Цель:** на вопрос «почему этот символ так решён» и «что решено о символах
этого каталога» отвечает одна команда структурой.

**Изменить:** `docpipe/explain.py`, `docpipe/classify.py`, `docpipe/stats.py`, `docpipe/cli.py`
**Создать:** `tests/test_symbols_json.py`

**Спецификация**

1. `select(..., path: str = "")` (`explain.py:65-106`): символ проходит, если
   хотя бы один `source.path` совпал. Значение без символов глоба
   (`*?[`) — файл или каталог: совпадение по равенству или по префиксу
   `path + "/"`; с символами — `discovery.matches_glob` (тот же, что
   у предиката `path_glob`, `classify.py:170-173`). Подпись фильтра — в
   `_description` (`121-134`). Флаг `symbols --path`.
2. В JSON-строку символа (`selection_json`, `explain.py:210-239`):
   `exclusion: {id, reason} | null` (из `Decision.exclusion`),
   `page: {id, title} | null`, `winner_rule: str | null`.
3. **Правило-победитель.** `Classification` хранит только `matched_rules`
   (`classify.py:426-444`). Поле `winner: str | None` добавляется с
   `field(compare=False)` и заполняется тем же `pick_winner`, что выбирает
   вид. Нужно S24/S25: охват каждого правила считается по победам, а не
   по совпадениям.
4. **Страница символа.** `absorbed_pages` отдаёт fqn → **заголовок**
   (`stats.py:115-126`, форма закреплена `test_stats.py:569-602`). Новая
   `absorbed_page_refs(...)` — fqn → `(id, title)`; старая остаётся.

> **Ловушка. `matches_glob` построен на `fnmatch`**: `*` проходит через `/`,
> на Windows сравнение без учёта регистра. `--path` наследует поведение
> `path_glob` намеренно: один ответ на «попадает ли файл под глоб» во всём
> инструменте.

**Критерии приёмки**
- `symbols --path src/Sample.Pricing.Api/Services --state any --format json`
  на `SampleSolution` — только символы этого каталога (сервис и его
  интерфейс; частичный класс из двух файлов — одной строкой);
- у отсеянного символа в JSON `exclusion.reason` непуст; у символа внутри
  страницы — `page.id` узла страницы; у классифицированного — `winner_rule`;
- `test_classify.py:74` (равенство `Classification`) проходит без правки.

**Проверка**
```bash
uv run pytest tests/test_symbols_json.py tests/test_explain.py tests/test_classify.py -q
```

**Справочники:** `README.md` (шаг 1а), `docs/web.md` (`symbols --lang ts`).

---

## S08 — справочники сверить с кодом ✅

**Цель:** ассистент читает справочники первым (`purpose.md`, следствие 4),
и неверный справочник для него — уверенный неверный совет.

**Изменить:** перечисленные файлы. Код — только там, где сказано.

**Спецификация.** Каждая строка — одна правка; для строк, исправленных кодом
в S02–S07, — проверить, что текст теперь правда.

| # | Где | Что не так | Правка |
|---|---|---|---|
| 1 | `docpipe.example.yaml:12-13`, бандл `docpipe.yaml:23` | `roots` «не читается» | читается и сужает обход (`discovery.py:132-173`) |
| 2 | бандл `docpipe.yaml:170` | умолчание `web.rules` — `rules/web.yaml` | `rules/rules.yaml` (`config.py:90`) |
| 3 | `configuration.md:229`, бандл `README.md:69-70` | `validate` читает секцию `dotnet` | не читает ни конфигурации, ни правил (`cli.py:400-433`) |
| 4 | `configuration.md:257` | `web link` читает секцию `web` | только `url_rewrite` и `link_out` |
| 5 | `configuration.md:135` | `graph build` — тот же отсев, что у `scan` | движку идёт только пользовательский `exclude`, без встроенного списка (`cli.py:1859` против `emit.py:74-85`). Проверить, пропускает ли движок `obj/` и `node_modules/` сам; если нет — правка кода: передавать `exclude_globs(config)` |
| 6 | `configuration.md:13-18` | в таблице баз нет `graph.engine_path` и `python_code.options.path` | cwd + `~`; от `--root` |
| 7 | `docpipe.example.yaml:74-76` | неизвестный `template` — «явная ошибка» | подставляется `default.md` (`template.py:151-164`) |
| 8 | `README.md:346-352` | «настраивается два файла» | шесть YAML и скелеты |
| 9 | `docpipe.example.yaml` | нет `di_methods`, `registries`, `business_root` | добавить с комментарием |
| 10 | бандл `docpipe.yaml` | нет `modules_dir`, `doc_layout`, `dispatch_interfaces`, `di_methods` | добавить закомментированными с пояснением |
| 11 | `configuration.md`, `web.md` | нигде не сказано, что `enrolled` и `domains` на шаг `web` не действуют | сказать (`web/modules.py:151-152`) и чем область фронта задаётся вместо них |
| 12 | `configuration.md:96-127` | в матрице «кто что читает» нет столбцов `web scan` и `web link` | добавить |
| 13 | `CASHFLOW.md` §0 (`176-187`), чек-лист `:969` | `**/artifacts/**` — в `rules.yaml` и сравнить «число файлов» | место — `docpipe.yaml: exclude`; отсев правил файлы не исключает, `--stats` считает символы |
| 14 | `frontend-analysis.md` §5.4 | шаблон — `medium`, конкатенация — `low`, `identifier` не восстановлен | в коде `high`, `medium`, `identifier` разрешается через константы (`calls.py:317-337`) |
| 15 | `docpipe/recon.py:25-28` | шапка зовёт `python3 tools/recon.py` | `docpipe/recon.py`; удалить `tools/__pycache__/recon*.pyc` |
| 16 | `docpipe/materialize/plan.py:64-65` | «на АС CF `docs/` содержит инструмент» | устарело с поставки без кода (02.09) |
| 17 | `docpipe.example.yaml:124-126` | совет «инструмент лежит внутри» и закомментированный `docs/ml/**` | убрать: это ловушка «накрыть дерево документов» |
| 18 | `graph-implementation-plan.md:1961-1964` (G12 п. 8) | сервер отвечает «индекс пересобирается» | код молча перечитывает индекс (`mcp.py:87-91`) — описать фактическое |
| 19 | `deploy/gitignore` | обещает «сидкар не коммитить», строки `*.run.json` нет | добавить строку |
| 20 | `docpipe/dotnet/di.py:137-139, 185-186` | числа squidex расходятся с `manual-run.md:245-247` | привести к замеру `manual-run.md` |
| 21 | `docpipe/configcheck.py` (S04) | незаменённые `@CONFIG_DIR@`/`@CACHE_DIR@` в целях записи выглядят обычными путями; `@ENGINE@` ловится только как `engine-missing` | **правка кода:** код проблемы `placeholder-left` — значение ключа содержит `@…@` |

> **Ловушка (строка 5, проверено 08.10). Движок пропускает `obj/` и
> `node_modules/` сам, но не весь встроенный отсев.** `obj/`, `node_modules/`,
> `dist/`, `.angular/`, `coverage/` — в любом режиме; `bin/` — только в `fast`
> и `moderate`; `*.g.cs` — ни в одном. Поэтому правка кода нужна: `graph build`
> отдаёт мосту `exclude_globs(config)`. Обратное расхождение шире: свой список
> пропусков движка (`src/discover/discover.c`) длиннее `SKIPPED_DIRECTORIES`
> и зависит от `graph.mode` — пункт в бэклоге, а не в этой задаче.
>
> **Ловушка (строки 9–10). Закомментированный ключ-список — только целиком.**
> После S02 `di_methods:` с одними закомментированными элементами — отказ
> загрузки; образцы в справочниках пишутся `# di_methods: []` или `di_methods: []`
> с закомментированными элементами под ним.
>
> **Строка 21: `placeholder-left` — причина, остальные проблемы того же значения
> подавляются.** «Не найден» и `engine-missing` у значения с плейсхолдером —
> следствия; отчёт печатает одну проблему на значение. Новый код проблемы
> меняет `ConfigReport.schema_version` на `1.1` (общее правило 7).

**Критерии приёмки:** каждая строка таблицы — правка или запись в журнале,
почему правка не нужна; `grep -n 'tools/recon.py' docpipe/` пуст.

**Проверка**
```bash
uv run pytest -q
grep -rn 'tools/recon.py' docpipe/ docs/configuration.md README.md
```

---

## S09 — карта цепочек настройки и тест на её полноту ✅

**Цель:** один справочник, из которого скилл и каталог вопросов берут
«кто читает ключ, от чего разрешается, чем проверяется, откуда значение
и что ломается молча» — и который не может отстать от кода.

**Создать:** `docs/setup-map.md`, `tests/test_setup_map.py`

**Спецификация**

`docs/setup-map.md` — по таблице на файл настройки (`docpipe.yaml` по
секциям, `rules.yaml` по секциям, `ownership.yaml`, `pages.yaml`,
`registries.yaml`, `arch-registry.yaml`, скелеты, флаги `install.sh`).
Колонки: **Ключ** (в обратных кавычках, путь через точку, списки — `[]`:
`web.url_rewrite[].strip_prefix`) | Тип и умолчание | Кто читает
(модуль:функция) | База пути | Чем проверяется | Откуда значение
(код / разведка / только человек) | Что ломается молча | Находка
`setup status` (колонку заполняет S24). Сырьё — разбор поверхности
от 06.10: 158 ключей, 12 видов файлов; отдельным разделом — 19 групп
взаимозависимых ключей (раскладка, исключения, плейсхолдеры, единый
`--root`, единый cwd и т. д.).

`tests/test_setup_map.py` держит полноту в обе стороны:
- каждый листовой путь `DocpipeConfig` (рекурсивный обход `model_fields`;
  `list[Model]` → `key[].field`; `dict` → только `key`) есть в первой колонке
  таблицы раздела `docpipe.yaml`;
- каждый ключ из констант допустимых ключей S02 (`rules.yaml`,
  `ownership.yaml`, `pages.yaml`) и каждый путь моделей `ArchRegistry`
  и модели `registries.yaml` (`registry/model.py`) есть в своей таблице;
- каждый ключ таблиц существует в модели или константе — устаревших строк нет.

> **Ловушка. Карта, проверяемая глазами, отстанет за месяц** — так отстали
> 17 мест справочников (S08). Тест на обе стороны дешевле, чем следующая
> сверка.

> **Ловушка (найдена при реализации). Имя ключа — псевдоним поля, а не имя.**
> В файле пишут `web.registry_calls[].discriminator.in` и
> `registries[].follow.children.list`, а поля моделей — `where` и `items_key`.
> Обход по `model_fields` без `field.alias` дал бы карте ключи, которых никто
> не пишет, и тест требовал бы именно их.
>
> **Ловушка (найдена при реализации). `arch_adapters[].options` — словарь,
> но базы у параметров разные** (`spec` — вход, `path` — от `--root`).
> Правило «`dict` → только `key`» потеряло бы ровно это, поэтому параметры
> берутся из `_KNOWN_OPTIONS` модуля каждого адаптера `ADAPTERS`: новый
> адаптер без такого множества роняет тест.
>
> **Ловушка (найдена при реализации). `|` в ячейке сдвигает колонки молча**:
> тип «`str | Model`» в markdown-таблице — лишняя ячейка. Карта пишет
> объединения словом «или», тест сверяет число ячеек (восемь) в каждой строке.

Отклонение при реализации (08.10): помощник обхода и разбора карты —
отдельным модулем `tests/setup_map_support.py` (по образцу
`business_support.py`), а не внутри теста: S28 импортирует оттуда
`all_map_keys` и `map_keys`. Сверх спецификации в карте — предикаты условий
(`when.*`, `rules[].when.*`; из `classify._PREDICATES`, `ownership.TABLE`),
подстановки скелетов (`ALLOWED_KEYS`) и флаги с плейсхолдерами `install.sh`
(из разбора аргументов самого скрипта): у них тоже есть «что ломается молча»,
и тест держит их так же. 203 ключа, 19 групп.

**Критерии приёмки:** тест проходит; новый ключ в `DocpipeConfig` без строки
в карте роняет тест (проверить временной правкой).

**Проверка**
```bash
uv run pytest tests/test_setup_map.py -q
```

**Справочники:** ссылка на карту — из `CLAUDE.md` (таблица документов),
`configuration.md`, `README.md` («Настройка под конкретный проект»).

---
## S10 — разведка: полные списки проектов и фронтов ✅

**Цель:** черновик `docpipe.yaml` (`roots`, `enrolled`, `web.roots`,
`url_rewrite`) строится из полного списка, а не из пяти примеров.

**Изменить:** `docpipe/recon.py`, `tests/test_recon.py`, `docpipe/graph/api.py` (потребитель `overview`)

**Спецификация**

1. В данных блока `composition` (`block_composition`, `recon.py:748-834`) —
   раздел `projects`:
   ```
   {"dotnet_projects": [путь .csproj, …],
    "solutions":       [путь .sln/.slnx, …],
    "fronts":          [{"path": каталог, "kind": "angular" | "nx",
                         "config": путь angular.json | project.json,
                         "angular_core": bool, "proxy_configs": [путь, …]}],
    "proxy_files":     [путь, …]}
   ```
   Все списки — `sorted`. У строк `build_files` (`771-790`) рядом с
   `examples` — `paths` (полный список); `overview` (`graph/api.py:389`)
   берёт только `pattern` и не ломается.
2. В `BUILD_FILES` (`299-339`) — третий вид совпадения: маска по имени файла
   (`fnmatch.fnmatchcase(name, pattern)`) для `proxy.conf*.json`,
   `proxy.conf*.js`. Сейчас совпадение — только `*.ext` или точное имя
   (`772-777`).
3. `angular_core` — `@angular/core` в `dependencies`/`devDependencies`
   ближайшего вверх по дереву `package.json`; `proxy_configs` — значения
   `proxyConfig` из `projects.*.architect|targets.serve.options`
   `angular.json`. Логика та же, что у `web/modules.py:24, 87-128, 196-208`,
   но **импортировать её нельзя**: разведка — один файл на stdlib
   (`tests/test_recon.py:71-86, 435-456`). Нужен свой разбор JSONC —
   сканером, а не регулярным выражением: `//` внутри `"http://host"`.
4. `project.json` — фронт, только если выше по дереву есть `nx.json`
   (`web/modules.py:252-254`).
5. Схема — `docpipe.recon/2` (`recon.py:46`): добавление поля двигает версию.

> **Ловушка. Разведка и сканер видят разные файлы.** `EXCLUDED_SEGMENTS`
> разведки (`recon.py:198-234`) содержит `packages`, `build`, `out`, `dist`,
> `bin`: проекты nx и lerna в `packages/*` для неё невидимы. Списки этого
> раздела собираются отдельным проходом с узким списком
> `BUILD_SKIP = {".git", "node_modules", "bin", "obj", "dist"}`.

> **Ловушка. Совпадение `*.ext` чувствительно к регистру, точное имя — нет**
> (`recon.py:774/777`): `Foo.CSPROJ` пропадёт. Сравнение суффикса — по
> `name.lower()`.

> **Ловушка. Слово `requests` в любом комментарии `recon.py` роняет
> `test_no_network_calls`** (запрещены подстроки `requests`, `socket`, `curl`,
> `wget`, `urllib`).

> **Ловушка (найдена при реализации). Суффикс `.lock` из `EXCLUDED_SUFFIXES`
> вычёркивал строки таблицы сборки.** Строки `uv.lock` и `poetry.lock`
> в `BUILD_FILES` были мёртвыми с самого R01: таблица считалась по списку
> после широкого отсева. Поэтому и таблица сборки считается тем же отдельным
> проходом, что и `projects`, — заодно строка `*.csproj` и `dotnet_projects`
> одного блока не называют разного числа проектов.

> **Ловушка (найдена при реализации). Пути в `project.json` nx заданы от корня
> workspace — `proxyConfig` тоже, не только `sourceRoot`.** Разведка разрешает
> его от каталога `nx.json`. `web/modules.py` (`_nx_module` → `_proxy_conf`)
> склеивает значение с каталогом проекта и находит файл только запасным
> поиском `proxy.conf*` по имени; `serve.configurations.*.proxyConfig` он
> не читает вовсе. Это расхождение разбора с разведкой — отдельная правка
> сканера, не S10.

> **Ловушка (найдена при реализации). Второй проход дорог там, где его
> не ждёшь.** Первая версия замедлила разведку копии abp без `.git`
> с 2,1 до 3,0 с: обход без git звал `relative_to` на каждый файл, и проходов
> стало два, а шаблон сборки разбирался на каждой паре «шаблон, файл».
> Шаблоны теперь разбираются раз (`match_build_files`), относительный путь —
> раз на каталог; прогон — 2,0 с, остальные пять блоков совпали байт в байт.

Отклонения при реализации (07.10): `proxyConfig` читается из `options`
и из всех `configurations` цели `serve`, у фронта nx — тоже (от корня
workspace); у записи фронта — поле `config_readable`, иначе неразобранный
`angular.json` неотличим от фронта без прокси; `angular_core` учитывает
и `peerDependencies`, как `web/modules.py`; маски прокси — ещё
`proxy.conf*.[cm]js` (`.mjs`/`.cjs`, как `_PROXY_NAMES` сканера);
`overview` отдаёт раздел числами (`projects`), а у отчёта схемы 1 — `None`.

**Критерии приёмки**
- репозиторий в `tmp_path` (`make_repo`, `test_recon.py:55-63`) с nx
  в `packages/`, `angular.json` с комментариями и висящими запятыми,
  `proxy.conf.json`, двумя `.csproj` и `.slnx` — все попадают в `projects`;
- на `angular.json` фикстуры `WebWorkspace` свой разбор JSONC разведки
  и `web/tsconfig`-разбор дают равные словари;
- два прогона — байт в байт; одиночный файл по-прежнему запускается копией.

**Проверка**
```bash
uv run pytest tests/test_recon.py -q
uv run docpipe recon --root tests/fixtures/WebWorkspace --json /tmp/r.json
```

**Справочники:** скилл `recon` (таблица блоков), `docs/graph-implementation-plan.md` R01 (схема 2).

---

## S11 — факты о вызовах регистрации; кандидаты в `di_methods` ✅

**Цель:** вопрос «эти вызовы — ваша обёртка регистрации DI?» строится
из счёта вызовов, а не из догадки агента. Без ключа `di_methods` на squidex
67 регистраций вместо 421 (`manual-run.md:245-247, 273-274`), и ноль
незаметен.

**Изменить:** `docpipe/model.py` (`FileParseResult`), `docpipe/dotnet/di.py`,
`docpipe/dotnet/parser.py`, `docpipe/cache.py`, `docpipe/emit.py`, `docpipe/cli.py`
**Создать:** `docpipe/setup/__init__.py`, `docpipe/setup/candidates.py`,
`tests/test_setup_candidates_di.py`

**Спецификация**

1. **Факт разбора.** `RegistrationCall(method: str, receiver: str,
   type_args: int, typeof_args: int, member: str, line: int)` и поле
   `FileParseResult.registration_calls`. Собирается в `dotnet/di.py` из тех же
   узлов `invocation_expression`, что и регистрации (`di.scm`, вызов
   `parser.py:522`), для **каждого** метода с именем `^(Try)?Add[A-Z]\w*$` —
   стандартные тоже: они нужны для статистики получателей. `receiver` —
   последний идентификатор выражения получателя (`services`, `Services`,
   `schema`). `CACHE_VERSION` — повысить (правило 10).
2. **Наружу.** `ScanResult` (`emit.py:204-217`) получает
   `registration_calls: list[tuple[str, RegistrationCall]]` (файл, факт;
   `sorted` по файлу и строке). В манифест факт **не** идёт.
3. **Кандидаты.** `setup.candidates.di_method_candidates(scan, settings)
   -> DiMethodCandidates`; элемент:
   ```python
   class DiMethodCandidate:
       method: str; calls: int; calls_with_types: int; files: int
       receivers: list[tuple[str, int]]       # три самых частых
       receiver_overlap: float                # доля вызовов с получателем, которым в этом репозитории зовут стандартные Add*
       declared_in: str | None                # файл объявления метода-расширения, если он в репозитории
       configured: bool                       # уже в di_methods
       examples: list[str]                    # до трёх «файл:строка»
   ```
   Стандартные имена (`_METHOD`, `di.py:19`) в кандидаты не идут. Порог —
   `calls_with_types >= 2`. Порядок — `(-round(receiver_overlap, 2),
   -calls_with_types, method)`. `declared_in` — член с этим именем,
   модификатором `static` и `this ` в `Member.signature` (`model.py:80-90`).
4. **Команда.** Эта задача заводит группу `docpipe setup` (шаблон групп —
   `cli.py:606-610`) и команду
   `docpipe setup candidates KIND --root --config --format --limit --offset`;
   `KIND` в этой задаче — `di-methods`, следующие задачи добавляют свои.
   В `module-review.md` — раздел «Настройка (`setup/`)».

> **Ловушка. Главную обёртку не найти по объявлениям.** На squidex
> `AddSingletonAs` объявлен в пакете `Squidex.Hosting`, а не в репозитории;
> 41 объявленный `AddSquidex*(this IServiceCollection …)` — агрегаторы,
> а не обёртки. Поэтому кандидат — это **вызов**, а `declared_in` —
> необязательная подсказка.

> **Ловушка. Частота имени без получателя — шум.** На squidex `AddField`
> вызван 436 раз (получатель `schema`), `AddDays` — 99 (`dateFrom`),
> `AddSingletonAs` — 275 (в 271 случае получатель `services`). Отделяет их
> пересечение получателей со стандартными регистрациями — признак из данных
> репозитория, а не список имён.

> **Ловушка. `Graph.identity.parameter_types` выбрасывает `this`**
> (`graph/identity.py:33`) — для распознавания расширения не годится; смотреть
> `Member.signature`.

> **Ловушка. Лямбда-форма теряется даже с ключом**: `AddSingletonAs(_ => new X())`
> без типа-аргумента отбрасывается в `di.py:201-203`. Кандидаты её считают
> (`calls` > `calls_with_types`), регистрация — нет; это ограничение
> регистраций, а не находки, и оно пишется в `docs/configuration.md`.

> **Ловушка. Без стандартных регистраций пересечение молчит.** В репозитории,
> где все регистрации идут через обёртки, у каждого кандидата
> `receiver_overlap = 0.0`, и `AddSingletonAs` стоит рядом с `AddField` без
> всякого объяснения. Поэтому в отчёте база пересечения — `standard_calls`
> и `standard_receivers`, а текст при `standard_calls = 0` говорит это строкой.

> **Ловушка. Получатель-вызов — не коллекция.** «Последний идентификатор»
> у `services.AddMvc().AddX()` — это `AddMvc`, а если раскрутить цепочку
> до корня, получится `services`, и любое звено построителя
> (`AddIdentity<…>().AddEntityFrameworkStores<…>()`) встанет в кандидаты
> с пересечением 1.0. Получатель-вызов пишется со скобками: `AddMvc()`.

Отклонения при реализации (07.10): получатель-вызов — имя со скобками
(выше), скобки и приведение типа прозрачны, форма без имени (`items[0]`,
`base`) — пустая строка, и пустой получатель в базу пересечения не входит;
в отчёте сверх спецификации — `standard_calls` и `standard_receivers`
(выше); `examples` — сначала вызовы с типом; `--limit 0` — весь список,
как у `symbols`; `receiver_overlap` хранится с округлением до четырёх
знаков. `_member_of` в `dotnet/facts.py` стал публичным `member_of`:
вызов регистрации зовёт его же, вторая копия разошлась бы на поле с
несколькими объявителями. `declared_in` у `partial` — по попаданию строки
члена в часть типа, при перекрытии — меньший путь (у `Member` своего пути
нет). Вход для CLI и S27 — `candidates(kind, CandidateInputs, limit=,
offset=)`; код 2 — только на `InputError` (вид, страница, файл правил),
сбой прогона не прячется под «ошибкой конфигурации». Вызов через `?.`
(`conditional_access_expression`) запрос `di.scm` не ловит ни для
регистраций, ни для фактов — ограничение общее.

**Критерии приёмки** (C# в `tmp_path`, как `tests/test_dotnet_facts.py:157-231`)
- `services.AddSingletonAs<X>().As<I>()` ×3, `services.AddSingleton<I, X>()` ×2,
  `schema.AddField<T>()` ×3, `dateFrom.AddDays(1)` ×2 — первым кандидатом
  `AddSingletonAs` с `receiver_overlap = 1.0`, `AddField` — с `0.0`,
  `AddDays` в списке нет;
- после записи `di_methods: [AddSingletonAs]` — `configured: true`;
- прогон с тёплым кэшем после повышения `CACHE_VERSION` даёт те же кандидаты,
  что холодный.

**Проверка**
```bash
uv run pytest tests/test_setup_candidates_di.py tests/test_dotnet_facts.py -q
uv run docpipe setup candidates di-methods --root tests/fixtures/WildSolution --format json
```

**Справочники:** `docs/configuration.md` (`di_methods`), `docs/setup.md`
(создаётся здесь: справочник по командам `setup`, дополняется каждой задачей).

---

## S12 — кандидаты в `dispatch_interfaces` ✅

**Цель:** вопрос «это интерфейс диспетчеризации по типу запроса?» строится
из счёта реализаций, а не из имени библиотеки.

**Изменить:** `docpipe/emit.py` (`ScanResult`), `docpipe/setup/candidates.py`
**Создать:** `tests/test_setup_candidates_dispatch.py`

**Спецификация**

1. `ScanResult.constructions: list[tuple[str, Construction]]` — все `new X(…)`
   из `FileParseResult.constructions` (`dotnet/facts.py:129-140`), сейчас
   они доходят до манифеста только при объявленном обработчике.
2. `dispatch_candidates(scan, settings) -> DispatchCandidates`. По символам
   индекса — классам, не абстрактным, — и каждой базе из `base_types_raw`
   с аргументами дженерика: голова (`raw.partition("<")[0]`), её FQN из
   параллельного `base_types`, если резолв удался (`dotnet/resolve.py:99-135`),
   первый аргумент — `split_type_arguments` (`emit.py:274-296`). Группа —
   по FQN головы, а без резолва — по имени.
   ```python
   class DispatchCandidate:
       interface: str; resolved: bool; implementations: int; request_types: int
       exclusivity: float      # доля типов-запросов, которые встречаются первым аргументом ТОЛЬКО у этой головы
       sent: int               # конструкции типов-запросов вне классов-реализаций
       handler_members: list[str]   # имена членов реализаций, в сигнатуре которых есть тип-запрос
       packages: list[str]     # до трёх PackageReference модулей с реализациями
       configured: bool; examples: list[str]
   ```
   Порог — `implementations >= 2` и `request_types >= 2` (типы-запросы
   объявлены в репозитории). Порядок — `(-exclusivity, -sent, -implementations,
   interface)`.
3. `KIND = dispatch-interfaces` у `setup candidates`.

> **Ловушка. Обобщённых баз-шумов больше, чем обработчиков.** На eShopOnWeb
> `Specification` 8, `IClassFixture` 7, `IEntityTypeConfiguration` 7 против
> 2 `IRequestHandler`. Отделяет их `exclusivity`: запрос MediatR встречается
> аргументом только у своего обработчика, а сущность `Order` — у пяти разных
> обобщённых баз.

> **Ловушка. Метод цели в графе зашит как `Handle`** (`graph/binding.py:486`):
> у `ILocalEventHandler.HandleEventAsync` (abp) и `Consume` ребро уходит в тип.
> `handler_members` делает это видимым человеку; правка графа — вне плана.

> **Ловушка. `package_references` не видит `Directory.*.props`**
> (`dotnet/csproj.py:142`): пустой список пакетов не значит «библиотеки нет».

> **Ловушка. «Три пакета модуля» — не подсказка о библиотеке.** У модуля
> пакетов десятки, и на eShopOnWeb первые три по счёту и алфавиту —
> `Ardalis.ListStartupServices, Ardalis.Specification, AutoMapper…`, а
> `MediatR` среди них нет. Пакет берётся тот, чьё пространство имён
> реализации импортируют (`using`, включая `global using` проекта —
> `resolve.file_usings`, та же функция, что у резолва). Совпадение
> вложенного пакета (`using MassTransit` ↔ `MassTransit.RabbitMQ`) слабее
> точного: `using System` совпал бы с каждым `System.*`, поэтому вложенные
> идут в ход, только когда точных нет. У объявленной в репозитории базы
> пакет не ищется.

> **Ловушка. `IEquatable<Money>` у `Money` — не диспетчеризация, но
> выглядит лучше всех.** Аргумент встречается только у этой головы
> (исключительность 1.0), а `new Money(…)` в коде больше, чем любых
> запросов, — value object встал бы первым. Аргумент, совпадающий с самим
> классом или его параметром-дженериком, типом-запросом не считается
> (CRTP-база `Entity<Order>` у `Order` — то же самое).

> **Ловушка. Срез `rstrip(">")` портит аргумент.** Так делает
> `collect_dispatch`: у `EndpointBaseAsync.WithRequest<A>.WithActionResult<B>`
> первый аргумент получается `A>.WithActionResult<B`, у
> `IRequestHandler<GetPage<Order>>` — `GetPage<Order`. Кандидаты ищут
> парную скобку (`_generic_base`). Голова при этом та же, что у прогона, —
> текст до первой `<`, имя без квалификатора — общей функцией
> `emit.dispatch_name`: отметка `configured` обязана значить «прогон эту
> базу найдёт».

> **Ловушка. Отправку с квалифицированным или обобщённым запросом прогон
> не находит.** `collect_sends` сравнивает имя из `new` (без квалификатора
> и дженерика) с аргументом базы как написан: у `IRequestHandler<App.CreateOrder>`
> обработчик в манифесте есть, отправок ноль, а кандидат видит их по
> `bare_type`. Дефект прогона, а не находки; в бэклоге.

Отклонения при реализации (07.10): в отчёте сверх спецификации — поле
`requests` (до трёх типов-запросов по имени: `IEntityTypeConfiguration`
с запросами `Customer, Order` объясняет себя без открытия файлов);
`packages` — по `using` реализаций, а не все пакеты модуля (ловушка выше),
отсюда `ScanResult.usings` и `resolve.file_usings`, вынесенная из
`build_symbol_index`; отсев аргумента, равного самому классу (ловушка
выше). Реализация — `type_kind == "class"`: `record` с базой `IRequest<T>`
дал бы «обработчиков» ответа. Тип-запрос и место отправки называются одной
функцией `facts.bare_type` (была `_bare_type`). `handler_members` — только
методы (конструктор назвал бы обработчиком имя класса), граница слова
обязательна (`CreateOrderResult` — не `CreateOrder`); порядок — по числу
реализаций с этим методом. `sent` не считает создание внутри реализаций
этой головы. Порядок дополнен `resolved` для полной определённости.
Наблюдение, порядок не менялся: на squidex у 32 кандидатов почти у всех
исключительность 1.0, и порядок решает `sent` — `IMessageHandler` (23
отправки, `HandleAsync`) третьим после `Content<T>` (83) и
`ReadonlyList<T>` (67); на abp `ILocalEventHandler` и
`IDistributedEventHandler` (0.71 и 0.62: одни события под двумя головами)
— 32-м и 33-м из 62. Решают там `handler_members` и `sent`, а не одно
число; если скиллу (S29) этого окажется мало — довод пересмотреть порядок.

**Критерии приёмки** (C# в `tmp_path`)
- `IRequestHandler<CreateOrder>`, `IRequestHandler<GetOrder>` и `new CreateOrder()`
  в контроллере; шум — `IEntityTypeConfiguration<Order>`,
  `IEntityTypeConfiguration<Customer>`, `Specification<Order>`; первым идёт
  `IRequestHandler` с `exclusivity = 1.0` и `sent = 1`, у
  `IEntityTypeConfiguration` `exclusivity < 1`;
- у реализаций с методом `HandleAsync(CreateOrder …)` — `handler_members: ["HandleAsync"]`.

**Проверка**
```bash
uv run pytest tests/test_setup_candidates_dispatch.py tests/test_scoped.py -q
```

---

## S13 — кандидаты в `web.registry_calls` ✅

**Цель:** вопрос «один маршрут, разные значения поля `listInnerName` — это
обращения к реестру?» строится из литералов вызовов. Ключ «метод + маршрут»
склеивает такие обращения в одну точку (CLAUDE.md, «Один маршрут на много
смыслов»).

**Изменить:** `docpipe/web/calls.py`, `docpipe/web/tree.py`, `docpipe/setup/candidates.py`
**Создать:** `tests/test_setup_candidates_registry.py`

**Спецификация**

1. `CallScan` (`calls.py:365-379`) получает `resolved: list[ResolvedCall]`,
   `ResolvedCall(raw: RawCall, call: WebCall, module: str)` — пара «сырой
   вызов с `url` (вместе с query) и `body_fields` → построенный вызов»
   из `build_calls` (`394-431`). Только в памяти: `RawCall` не кэшируется
   и в манифест не идёт.
2. `registry_call_candidates(web, settings) -> RegistryCallCandidates`.
   Группа — `(module, http_method, route)` построенного ключа **без**
   различителя; в группе от двух вызовов. По каждому имени поля тела
   (`raw.body_fields`) и параметра query (`raw.url`) — множество литеральных
   значений; кандидат — `(группа, in, name)` с двумя и более значениями:
   ```python
   class RegistryCallCandidate:
       route: str               # ровно в той форме, в какой его пишут в registry_calls.route
       http_method: str; where: Literal["body", "query"]; name: str
       values: list[str]; values_total: int; calls: int
       configured: bool; unresolved_when_configured: int; examples: list[str]
   ```
3. `KIND = registry-calls`. В отчёте — раздел `limits`: «тело читается только
   из объектного литерала второго аргумента; `HttpParams` и `{params: …}`
   не разбираются».

> **Ловушка. `registry_calls.route` сравнивается с маршрутом после
> `url_rewrite`** (`calls.py:401` против `:412`): правило нормализуется без
> преобразования, вызов — с ним. Кандидат печатает маршрут в форме после
> преобразования — ту, которую надо вписать; иначе правило «не сработает»,
> и это будет выглядеть как «инструмент не нашёл».

> **Ловушка. Повтор ключа — не признак.** На фикстуре `audit.service.ts` —
> шесть вызовов одного маршрута без литералов в теле. Кандидат требует
> **разных значений одного поля**, а не повторов.

> **Ловушка. Только литералы не дают второго критерия.** У `GET api/items`
> в `items.service.ts` литерал один (`dictionaries`), второй вызов —
> `` `api/items?listInnerName=${type}` ``, и по букве п. 2 («два и более
> литеральных значения») кандидата нет. А обёртка, принимающая имя списка
> параметром, — главная форма обращения к реестру (`checkUrl(type)` на АС CF,
> `findings-cashflow-frontend.md`). Значение-выражение считается одним
> значением, отличным от литералов (`nonliteral`), — и в query (`{}`),
> и в теле: там его не было видно вовсе, `_body_fields` берёт только
> литералы, поэтому `RawCall` получил `body_nonliteral` (имена полей
> `{ a: name }` и `{ a }`). Без него тело было бы слепо к форме, которую
> query видит.

> **Ловушка. Правило не знает ни глагола, ни модуля.** `registry_calls`
> сверяется только с маршрутом (словарь по `normalize_route`), поэтому
> `DELETE api/lists` рядом с `GET api/lists?name=…` после записи правила
> тоже встанет в `registry_unresolved`. `unresolved_when_configured`
> считается по всем вызовам маршрута — иначе предсказание разошлось бы
> с `web scan --stats`; рядом `route_calls`, чтобы число больше `calls`
> не читалось как ошибка. Счёт — той же функцией, что у прогона
> (`calls.discriminator_of`).

> **Ловушка. Два правила на один маршрут — действует последнее, молча.**
> Загрузка повтор маршрута не отвергает (в отличие от `url_rewrite`, S02),
> а `build_calls` собирает правила словарём. Отметка `configured` читает
> тот же словарь (`calls.registry_rules`), поэтому с прогоном не расходится;
> сам повтор — в бэклоге.

> **Ловушка. `{}` в маршруте ломает потоковую запись YAML.** Готовая
> строка `{route: api/lists/{}/items, …}` читается как вложенный словарь.
> Маршрут и имя печатаются в кавычках JSON.

Отклонения при реализации (08.10): группа — `(http_method, route)` **без
модуля**: в модели кандидата модуля нет, и два кандидата одного маршрута из
двух фронтов были бы неразличимы, а правило пишется на маршрут, — модули
идут полем `modules`. Значение-выражение — отдельное значение (ловушка
выше), отсюда `nonliteral` и `RawCall.body_nonliteral`. Сверх спецификации:
`route_calls` и `modules` у кандидата; `calls_resolved` и `calls_unresolved`
у отчёта — база, без которой ноль кандидатов неотличим от «маршрутов
не видно» (на squidex восстановлено 2 вызова из 81, на ever-traduora 0
из 50, кандидатов 0 у обоих); второй пункт `limits` — невосстановленный
вызов в счёт не идёт; строка «в web.registry_calls: …» с готовой записью.
`configured` — точное совпадение места и имени с действующим правилом
маршрута, правило на другое поле того же маршрута — `false`.
`registry_call_candidates` принимает `CallScan`, а не весь результат
прогона: формы, которых нет в фикстуре, проверяются через `build_calls`
без Angular-workspace. Порядок — `(-values_total, -calls, route,
http_method, where, name)`; `values` — до десяти по имени. Ручной состав
страниц (`web.pages`) на вызовы не влияет, и сначала прогон кандидатов его
не читал; при сведении с S14 вспомогательный прогон фронта `_web_scan`
стал один на `features` и `registry-calls` и читает `pages.yaml` тем же
`load_page_overrides`, — названный и ненайденный файл роняет теперь и этот
вид, как `web scan`. S23 заменила `_web_scan` прогоном контекста
(`SetupContext.web` и `SetupContext.overrides`), поведение вида то же.
`_registry_calls` в `web/tree.py` стал публичным
`registry_calls`, разбор query — `calls.query_parameters` (через неё же
`_query_value`).

**Критерии приёмки** (фикстура `WebWorkspace`)
- `items.service.ts`: кандидаты `POST api/items/query` (`body`,
  `listInnerName`, значения `models`, `users`) и `GET api/items`
  (`query`, `listInnerName`); `audit.service.ts` — ни одного;
- с правилом `registry_calls` на первый — `configured: true`,
  `unresolved_when_configured` равно числу вызовов с `{}`.

**Проверка**
```bash
uv run pytest tests/test_setup_candidates_registry.py tests/test_web_calls.py -q
```

---

## S14 — кандидаты в разделы без маршрута ✅

**Цель:** вопрос «этот каталог со своим состоянием и сервисами — раздел?»
строится из достижимости страниц. Без раздела общий узел остаётся отдельными
документами (`docs/pages.md`, P16).

**Изменить:** `docpipe/setup/candidates.py`
**Создать:** `tests/fixtures/WebSections/`, `tests/test_setup_candidates_features.py`

**Спецификация**

1. `feature_candidates(web_manifest, overrides) -> FeatureCandidates`.
   Для каждой страницы — `absorb.reachable_from(page, index_by_fqn(manifest))`
   (`absorb.py:36-54`, тот же расчёт, что `materialize/build.py:450-471`).
   Узел общий, если до него дотягиваются две и больше страниц. Для каждого
   общего узла — каталог `sources[0].path`; подниматься к предку (не выше
   трёх уровней и не выше корня модуля), пока в нём не окажется общих узлов
   **двух и больше видов**; этот предок — кандидат.
   ```python
   class FeatureCandidate:
       path: str; pages: list[str]           # маршруты страниц
       nodes: int; kinds: dict[str, int]; declared: bool; examples: list[str]
   ```
   Каталог, уже накрытый `features[].path` из `pages.yaml`, — `declared: true`.
2. `KIND = features`.
3. **Фикстура `WebSections/`** — минимальный Angular-workspace (`angular.json`,
   `tsconfig.json`, `package.json`, `app.routes.ts`): страницы `orders-list`
   и `orders-detail`, обе зовут `shared-orders/state/orders.state.ts`
   и `shared-orders/api/orders-api.service.ts`; у `orders-detail` — свой
   приватный сервис. В `WebWorkspace` положительного примера нет, а дополнять
   его нельзя: на его числа завязаны тесты страниц.

> **Ловушка. `absorbed_by=""` значит и «две страницы», и «ни одной»**
> (`model.py:519-523`), а объявленный раздел меняет `absorbed_by`. Кандидатов
> считать по `uses`, а не по `absorbed_by`.

> **Ловушка. Три разные достижимости.** В `web/pages.py` есть `_reachable`
> по зависимостям с глубиной (`268-289`) и `called` по парам с глубиной 3
> (`292`). Для разделов — только `reachable_from` поглощения, иначе кандидат
> разойдётся с тем, что потом поглотит раздел.

> **Ловушка. Каталоги `state/` и `api/` — не стандарт** (в `WebWorkspace` это
> `services/`, `state/`, `cf-api/resources/`). Признак — разнообразие **видов**
> узлов, а не имена каталогов; виды же зависят от правил `web`.

> **Ловушка. Корень модуля — не `DocNode.module`.** Там имя проекта
> (`tr-p`, `orders-web`), а каталог-граница — ключ модуля фронта, и он лежит
> в `Symbol.module` (`src` у `angular.json` с `sourceRoot: src`,
> `nx-app/apps/widget` у nx). Сравнение пути с именем проекта не совпало бы
> ни разу, и подъём шёл бы до корня репозитория.

> **Ловушка. Пустой `pages.yaml` вместо названного.** Без объявленных
> разделов отметка `declared` всегда `false`, и это выглядит как «ещё
> не объявлен». Поэтому `pages.yaml` читается тем же загрузчиком, что
> у `web scan` (`web/overrides.load_page_overrides`, перенесён из CLI):
> названный и ненайденный файл — код 2.

Отклонения при реализации (07.10): в отчёте сверх спецификации — база
`pages_total` и `shared_nodes` (как `standard_calls` у S11: без страниц
достижимость молчит, и ноль кандидатов при несобравшейся таблице роутов
неотличим от «разделов нет»; текст говорит это строкой) и `page_count`.
`pages` — по маршруту на страницу (наименьший), не больше десяти
(правило 5: каталог, до которого дотягиваются все страницы, съел бы ответ);
страницы — те, что дотягиваются до **любого** узла под каталогом, то есть
ровно «Откуда открывается» будущего документа раздела
(`materialize/build._opened_from`); невосстановленный маршрут — с `?`, как
в `web pages --format csv`. `nodes` и `kinds` — только общие узлы.
Подъём проверяет сам каталог файла и до трёх предков; корень репозитория
(пустой путь) кандидатом не бывает — `features[].path` пустым не объявить.
`declared` — каталог равен объявленному или лежит под ним, по сегменту.
Порядок спецификацией не задан — по пути (вложенные кандидаты рядом).
Неоднозначное правило снятия при прогоне — `InputError` (код 2), как у
`web scan`; `ValidationError` из прогона пробрасывается как сбой. Текст
у необъявленного кандидата печатает строку «в pages.yaml: features[].path».
Наблюдение, отбор не менялся: общие сервисы одного каталога
(`shared/services/`, один вид) и общий стейт другого встречаются на общем
предке (`src/app`) не дальше трёх уровней — и он становится кандидатом
с почти всеми страницами. Отличают его страницы и виды, а не одно число;
если на боевом фронте таких окажется много — довод добавить признак
(например, долю узлов под каталогом, достижимых теми же страницами).

**Критерии приёмки**
- на `WebSections` кандидат `…/shared-orders` с двумя страницами и двумя
  видами; приватный сервис `orders-detail` — не кандидат;
- после объявления раздела в `pages.yaml` — `declared: true`;
- на `WebWorkspace` кандидатов нет (`inner-debt` достижим одной страницей).

**Проверка**
```bash
uv run pytest tests/test_setup_candidates_features.py tests/test_web_features.py -q
```

---

## S15 — фикстура форм шва и инвентарь ✅

**Цель:** все формы шва фронт↔.NET, найденные на squidex и abp, воспроизведены
в одной самодостаточной фикстуре; числа прогона записаны.

**Создать:** `tests/fixtures/SeamWorkspace/`, `tests/test_seam_fixture.py`,
`docs/findings-seam.md`

**Спецификация**

**Фикстура** — бэк и фронт в одном корне; каждая форма — отдельный файл
или метод, чтобы тесты S16–S21 указывали на неё по имени:

| Сторона | Форма | Где |
|---|---|---|
| .NET | абстрактная база `[ApiController][Route(Constants.PrefixApi)]`, `public const string PrefixApi = "api";` | `backend/Seam.Api/Web/ApiController.cs`, `Web/Constants.cs` |
| .NET | контроллер на этой базе: `[HttpGet("apps")]`, `[HttpGet("apps/{app}")]`, `[HttpPost("apps")]`, `[HttpPut("apps/{app}")]` (адресат `requestVersioned` с `'PUT'`) | `Controllers/AppsController.cs` |
| .NET | база с токеном `[Route("api/[controller]")]` и наследник `OrdersController` | `Web/TokenApiController.cs`, `Controllers/OrdersController.cs` |
| .NET | один относительный маршрут `[HttpGet("info")]` под разными базами — ложный дубль `GET info` (squidex) | `Controllers/InfoController.cs` (на `ApiController`, адресат `api/info`), `OrdersController` |
| .NET | действие с `[Route("comments/{id}")]` без глагола, класс с `[Route("api")]` | `Controllers/CommentsController.cs` |
| .NET | `[AcceptVerbs("GET", "POST")]` | `Controllers/VerbsController.cs` |
| .NET | публичный API для внешних клиентов `[Route("content/{app}")]` | `Controllers/ContentController.cs` |
| .NET | контроллер без маршрутов и `MapControllerRoute` в `Program.cs`; `app.MapGet("/health", …)` | `Controllers/LegacyController.cs`, `Program.cs` |
| фронт | прямой `this.http.get('api/info')` | `src/app/services/info.service.ts` |
| фронт | построитель: `const url = this.apiUrl.buildUrl('/api/apps'); return this.http.get(url)` | `src/app/services/apps.service.ts`, метод `list` |
| фронт | второй метод того же файла со своим `const url = this.apiUrl.buildUrl(\`/api/apps/${app}\`)` | там же, метод `get` |
| фронт | литеральный `const url = 'api/apps/archived'` третьего метода — до S16 подставляется и в `list`, и в `get` | там же, метод `archived` |
| фронт | DTO рядом с сервисом: вызовы файла достаются обоим узлам (S16, п. 6) | `apps.service.ts`, `AppDto` |
| фронт | обёртка с URL в позиционном аргументе: `HTTP.getVersioned(this.http, url)` с `const url = 'api/apps'`; тела `getVersioned`/`postVersioned`/`putVersioned`/`deleteVersioned` — `http.get(url)` и т. д. | тела — `src/app/framework/http-extensions.ts`; вызов — `src/app/services/apps-versioned.service.ts`, метод `getApps` |
| фронт | обёртка с методом из аргумента: ``HTTP.requestVersioned(this.http, 'PUT', `api/apps/${app}`, …)``; тело — `http.request(method, url, …)` | тело — там же; вызов — `apps-versioned.service.ts`, метод `putApp` |
| фронт | обёртка с объектом-запросом: `this.rest.request({ method: 'POST', url: '/api/apps' })`; тело — `this.http.request(config.method, config.url, …)` | `src/app/services/apps-proxy.service.ts`, метод `create`; `src/app/framework/rest.service.ts` |
| фронт | хвостовой построитель query: `` this.http.get(`api/apps/search${buildQuery(q)}`) `` | `apps.service.ts`, метод `search` |
| фронт | внешний адрес `this.http.get('https://ext.example.org/feed.json')` | `src/app/services/feed.service.ts` |
| фронт | гипермедиа `this.http.request(link.method, link.href)` — не видна вовсе; и та же гипермедиа через глагол `this.http.get(link.href)` — видна, не восстановлена | `src/app/services/links.service.ts`, методы `follow` и `fetch` |
| фронт | конкатенация с невосстановленной базой `this.base + '/api/apps'` | `apps.service.ts`, метод `legacy` |
| фронт | изменяемое поле `public fileSource = ''`, позже `this.fileSource = src`, вызов `this.http.get(this.fileSource)` | `src/app/components/editor.component.ts` |
| фронт | `proxy.conf.json` с `pathRewrite`, `angular.json` с `proxyConfig`, `tsconfig.json` с комментарием, страница в `app.routes.ts` | корень фронта |
| обе | `docpipe.yaml` фикстуры: `roots: [backend]`, `web.roots: [frontend]`, `rules` и `web.rules` — `../../../rules/rules.yaml` (вход, вторая ступень `resolve_input`); правил шва нет — их добавляют тесты в `tmp_path`-копии | корень фикстуры |

`tests/test_seam_fixture.py` проверяет **наличие каждой конструкции**
(регулярным выражением по файлу), а не файлов — иначе «упрощение» фикстуры
оставит тесты S16–S21 зелёными и бессмысленными. Там же — прогон `scan`
и `web scan` по фикстуре без ошибок и числа **до** S16 (их обновляет каждая
задача этапа C).

> **Ловушка. `this.http.request(…)` не виден ни одному счётчику**: `request`
> нет в `HTTP_METHODS` (`calls.py:42`), а получатель `http` — в `HTTP_RECEIVERS`,
> поэтому вызов не становится и кандидатом S18 («получатель не из
> `HTTP_RECEIVERS`»). Критерии S18 и S20 про `links.service.ts` держатся
> на втором методе `fetch` (`this.http.get(link.href)`); `follow` остаётся
> невидимым, пока S16/S18 не решат, считать ли `request(method, url)` вызовом
> с методом из аргумента (squidex: 15 мест в 7 файлах).

> **Ловушка. Кандидат S18 требует аргумента, похожего на адрес.** Поэтому
> у вызовов обёрток в `apps-versioned.service.ts` адрес — литеральный `const`
> или шаблон `api/…`, а не `const` от построителя: `HTTP.getVersioned(this.http, url)`
> с `url = this.apiUrl.buildUrl(…)` кандидатом не стал бы. Связка «построитель +
> обёртка» (основная форма squidex) в фикстуре не воспроизведена.

> **Ловушка. Первая ступень `resolve_input` для `../../../rules/rules.yaml`.**
> В git worktree на три уровня ниже основного клона (`.claude/worktrees/<имя>/`)
> путь от текущего каталога ведёт в `rules/rules.yaml` **основного клона**, и
> прогон молча берёт набор другой ветки. Тесты передают правила явно
> (`--rules rules/rules.yaml`, `load_ruleset(Path("rules/rules.yaml"), …)`).

> **Ловушка. Анонимный тип в сигнатуре метода** (`get<{ version: string }>`,
> `link: { href: string }`) web-разбор записывает членами класса, и вызов
> приписывается «члену» `version`. Фикстура этой формы избегает (`ResourceLink`
> — именованный тип); дефект — вне этапа C.

**`docs/findings-seam.md`** — инвентарь от 07.10.2026: настройки прогонов
(squidex: `roots: [backend]`, `web.roots: [frontend]`,
`di_methods: [AddSingletonAs, AddTransientAs, AddScopedAs]`; abp:
`modules/{identity, permission-management, tenant-management,
setting-management, feature-management, account}` +
`framework/src/Volo.Abp.AspNetCore.Mvc`, `web.roots: [npm/ng-packs]`), таблица
форм с числами и путями-примерами, оценка эффекта: на squidex с построителем,
обёртками и `strip_prefix: api` связывается ~87 вызовов из ~98 со статическим
путём; на abp с обёрткой `restService.request` — 60 из 62. Известное
об АС CF — из `findings-cashflow-frontend.md` (интерцептор `FixUrlInterceptor`,
один `MapControllerRoute`, обращения к реестру с `listInnerName`).

**Критерии приёмки:** тест фикстуры проходит; каждая строка таблицы форм
находит свою конструкцию; `findings-seam.md` в таблице документов `CLAUDE.md`.

**Проверка**
```bash
uv run pytest tests/test_seam_fixture.py -q
```

---

## S16 — ключ вызова без правдоподобных ошибок; версия манифеста ✅

**Цель:** вызов фронта получает либо верный ключ, либо явный отказ.
Пять форм сейчас дают ключ, который выглядит настоящим и неверен.

**Изменить:** `docpipe/web/calls.py`, `docpipe/web/tree.py`, `docpipe/model.py`,
`schema/doc-tree.schema.json`
**Создать:** `tests/test_call_keys.py`

**Спецификация**

1. **Константы — по области функции.** `_module_constants`
   (`calls.py:116-132`) собирает `const` всего файла через `setdefault`,
   и первый литеральный `const url` подставляется во **все** `this.http.get(url)`
   файла. Порядок разрешения идентификатора: `const` ближайшего охватывающего
   блока функции, объявленный до строки вызова → `const` уровня модуля →
   поле класса (п. 2). `const` другой функции — никогда.
2. **Поле — константа, только если его не присваивают.** `_field_constants`
   (`135-152`) принимает поле, если в теле класса нет `this.<имя> = …`
   вне его инициализатора; `readonly` — всегда.
3. **Ведущая невосстановленная база в конкатенации — отказ**
   («база в начале конкатенации не восстановлена»), как уже у шаблона
   (`204-205`). Сейчас `this.base + '/api/apps'` даёт ключ `{}/api/apps`.
4. **Хвостовой построитель query.** Подстановка, прилипшая к концу последнего
   литерального сегмента и последняя в шаблоне
   (`` `api/apps/search${buildQuery(q)}` ``), отбрасывается, уверенность —
   `medium`. Целый сегмент `` `api/apps/${id}` `` по-прежнему `{}`.
   Правка — в `_from_template` фронта, не в общей `route.normalize_route`:
   её делит .NET.
5. **Хост сохраняется.** `WebCall.host: str = ""` — хост в нижнем регистре,
   если URL абсолютный. Ключ (`normalize_route`, `route.py:120-124`)
   по-прежнему без хоста — правило внешнего адресата (S20) смотрит на `host`.
6. **Вызов — узлу, в чей диапазон он попал.** Сейчас вызов приписывается
   **каждому** классифицированному узлу файла (`tree.py:827-831`): в
   `help.service.ts` squidex сервис и dto дали пять вызовов вместо трёх.
   Узел получает вызов, если в его `sources` есть `SourceSpan` того же файла
   с `start <= line <= end`; ни одного такого — по-старому всем узлам файла
   и счётчик `calls_unattributed` в сидкаре.
7. **Версия манифеста** (правило 7): `SCHEMA_VERSION = "2.1"`;
   `schema_version: str` с проверкой «мажорная `2`, минорная не новее своей»:
   «манифест версии 2.3 новее инструмента (2.1): обновите docpipe». Закрывает
   пункт бэклога «`schema_version` манифеста».

> **Ловушка. Ключ при ошибке п. 1 правдоподобен.** На squidex
> `help.service.ts:42` получил маршрут из `const url` метода на строке 45,
> и ни один счётчик этого не показал. Построители (S19) поверх этой функции
> унаследовали бы ошибку — поэтому п. 1 идёт до них.

> **Ловушка. `api/apps/search{}` не совпадает ни точно, ни «почти»**:
> `_fixed_segments` (`route.py:183-185`) отбрасывает только сегменты, целиком
> равные `{}`. На squidex таких мест 13.

> **Ловушка. Объявление ниже вызова затеняет внешнее.** `const` в той же
> функции ниже вызова — TDZ: имя занято, и литерал файла с тем же именем
> брать нельзя. Через границу функции порядок не важен: `const` модуля
> под классом законен, метод зовут после выполнения файла. Параметр функции
> затеняет константу файла (`download(url)` при `const url` модуля).

> **Ловушка. `readonly` присваивается в конструкторе.** Буква п. 2
> («`readonly` — всегда») даёт `GET 'api/default'` у
> `private readonly url = 'api/default'` с `this.url = cfg.url` в конструкторе.
> Проверка присваивания идёт для всех полей.

> **Ловушка. Проверка версии полем тонет в следствиях.** `field_validator`
> на `schema_version` даёт верную строку, но рядом с ней — по
> «Extra inputs are not permitted» на каждое новое поле каждого узла.
> Проверка — `model_validator(mode="before")`: одна ошибка, одна причина.

> **Ловушка. `"host" not in text`.** `test_no_run_metadata_leaks_into_the_manifest`
> (`test_web_scan_e2e.py`) искал хост машины подстрокой, и `WebCall.host`
> его уронил. Переименовывать поле нельзя (S20 читает `host`) — проверка
> переписана: ключ `host` встречается ровно по разу на вызов, и его значение —
> не хост машины.

> **Ловушка. Хвост по п. 4 не отличим от параметра внутри сегмента.**
> `` `files/report-${id}` `` теряет `${id}` так же, как `search${buildQuery(q)}`:
> `files/report-`, `medium`, хотя маршрут .NET `report-{id}` совпал бы
> с прежним `report-{}`. Подстановка после `?`/`#` правилом не трогается.
> Конкатенация `'api/x/search' + buildQuery(q)` под п. 4 не попадает
> и по-прежнему даёт `api/x/search{}`.

> **Ловушка. `tests/test_schemas.py` — пункт S06**, а S06 от S16 не зависит
> и к ней может быть не сделана: команда проверки S16 упала бы на
> «file or directory not found». Файл заведён в S16 со сверкой всех
> `SCHEMA_MODELS` (S06 п. 7); S06 его дополняет, а не создаёт.

**Критерии приёмки** (`SeamWorkspace`)
- методы `list` и `get` в `apps.service.ts` разрешают каждый свой `const url`
  (до S19 оба невосстановлены с причиной про вызов, а не подставлены чужим
  литералом);
- `editor.component.ts` — невосстановлен, а не `GET ''`;
- `legacy` — отказ про базу; `search` — `GET api/apps/search`, `medium`;
- `feed.service.ts` — `host: ext.example.org`;
- `calls_total` у `web link` равен числу вызовов в коде;
- манифест 2.0 читается, 3.0 — отказ с текстом; схема перегенерирована
  (`test_schemas.py`).

**Проверка**
```bash
uv run pytest tests/test_call_keys.py tests/test_web_calls.py tests/test_seam_fixture.py tests/test_schemas.py -q
```

**Справочники:** `docs/web.md` (формы URL), `docs/manifest.md` (версия,
`host`), `frontend-analysis.md` §5.4, `docs/backlog.md` (закрыть пункт о версии).

---

## S17 — эндпоинты .NET: наследование `[Route]`, маршрут без глагола, константы ✅

**Цель:** эндпоинт бэка получает тот маршрут, по которому его зовут.
На squidex префикс `api/` объявлен на абстрактной базе, и у всех 256 эндпоинтов
его нет; единственный «дефект» `web link` (дубль `GET info`) — ложный.

**Изменить:** `docpipe/dotnet/endpoints.py`, `docpipe/dotnet/parser.py`,
`docpipe/model.py`, `docpipe/emit.py:490`, `docpipe/web/link.py`
**Создать:** `tests/test_endpoints_routes.py`

**Спецификация**

1. **Наследование `[Route]`.** `extract_endpoints(symbol)` (`endpoints.py:84-106`)
   берёт базу только из собственных атрибутов (`:86`), хотя `RouteAttribute`
   в ASP.NET Core наследуется. Новая сигнатура `extract_endpoints(symbol, index)`:
   нет своего `[Route]` — ближайший базовый **класс** (по разрешённым FQN
   `base_types`, первый не-интерфейс, глубина не больше 10, защита от цикла),
   у которого он есть. Вызов — `emit.py:490`, индекс там есть.
2. **Аргумент маршрута — выражение.** Для нелитерала парсер кладёт сырой текст
   (`parser.py:114`): `Constants.PrefixApi` уходит в маршрут как есть. В
   `Attribute` (`model.py:60`) различить литерал и выражение (флаг у аргумента);
   выражение `Тип.Имя` разрешается по индексу — член-константа с литералом
   в `Member.signature`; не разрешилось — эндпоинт с пустым `route` и
   `Endpoint.unresolved: str` («аргумент маршрута — выражение
   `Constants.PrefixApi`»), а не выдуманный путь. Формат манифеста — версия
   по правилу 7.
3. **`[Route]` без глагола** на действии — эндпоинт `http_method: "*"`
   (сейчас не даёт ничего, `endpoints.py:90-93`); `web link` сопоставляет `*`
   с любым методом — и точно, и «почти».
4. **`[AcceptVerbs("GET", "POST")]`** — по эндпоинту на глагол.
5. **«Конвенциональные» — только настоящие.** `_conventional` (`link.py:128-140`)
   считает абстрактные базы и контроллеры с `[Route]` без глагола: на двух
   репозиториях 4 записи из 10 — абстрактные базы, настоящих — 2. Новое
   условие: класс не абстрактный, есть публичный метод, ни у класса, ни у
   одного члена нет атрибутов маршрута.

> **Ловушка. Токен `[controller]` в маршруте базы подставляется именем
> наследника**, а не базы: `[Route("api/[controller]")]` на `TokenApiController`
> даёт `api/orders` у `OrdersController`. Подстановка (`_substitute`, `74-81`) —
> от символа, для которого считаются эндпоинты.

> **Ловушка. Действия, объявленные в абстрактной базе, ASP.NET тоже
> наследует** — это в план не входит (раздел «Что намеренно не входит»);
> наследуется только маршрут.

> **Ловушка. «Первый не-интерфейс» по позиции не найти.** `Symbol.base_types`
> отсортированы по тексту (`resolve.py`), и `IAlpha` встаёт раньше `Zeta`.
> База — первый из **разрешённых в индексе** с `type_kind == "class"`
> (`base_symbol_key`, тот же выбор по арности и модулю, что у замыкания).

> **Ловушка. Константа разрешается от класса, где записан атрибут.**
> `[Route(Constants.P)]` на базе из `namespace Web` видит `Web.Constants`,
> а не `Other.Constants` из namespace наследника. На squidex `Constants`
> два (`Squidex.Web`, `Squidex.Infrastructure.Json.System`), и выбирает
> между ними имя члена: искать тип без члена значило бы получить
> неоднозначность на ровном месте. Разные значения у нескольких кандидатов —
> отказ «неоднозначно» (своя сборка перекрывает чужую, как в C#).

> **Ловушка. Невосстановленный эндпоинт пропадал бы из `web link` молча.**
> Пустой маршрут в ключи не идёт, а по п. 5 такой контроллер больше не
> «конвенциональный» — у него есть атрибуты маршрута. Константа в проекте
> вне `roots` — обычный путь к этому исходу. Поэтому `LinkReport` 1.1:
> `unresolved_endpoints` (узел, член, причина) и `counts.endpoints_unresolved`.

> **Ловушка. Буква п. 5 («ни у одного члена нет атрибутов маршрута») теряет
> `[HttpGet]` без шаблона.** В ASP.NET такое действие без `[Route]` у класса
> остаётся конвенциональным с ограничением по методу, и его эндпоинт с пустым
> маршрутом в ключи не идёт. Условие поэтому — «маршрут не собрался»: своего
> `[Route]` у класса нет и у каждого эндпоинта пустой маршрут без `unresolved`
> (унаследованный `[Route]` даёт непустой). Наследник базы с маршрутом и
> действиями вовсе без атрибутов остаётся в списке — ложная, но видимая запись.

> **Ловушка. Абстрактная база с действиями — ни в одной категории.** У abp
> `ChallengeAccountController` (абстрактный, пять `[HttpGet]` без шаблона)
> до S17 числился конвенциональным; теперь база туда не идёт по условию,
> а пустой маршрут — в ключи. Это и есть «унаследованные действия» вне плана.

> **Ловушка. Новое поле `Attribute` — в кэше разбора.** `FileParseResult`
> хранит атрибуты; без `CACHE_VERSION` 6 тёплый прогон отдал бы
> `Constants.PrefixApi` литералом, и маршрут стал бы `Constants.PrefixApi/apps`.

> **Ловушка. Маршрут, унаследованный от базы, в `signature_hash` наследника
> не входит.** Хэш считается по своему символу, а эндпоинты теперь зависят
> от чужого (база, константа). Генерируемый блок документа пересобирается
> всегда и таблицу эндпоинтов обновит, но `docs status` смену префикса базы
> пересмотром не отметит.

> **Ловушка. Граф кода сопоставляет вызовы с эндпоинтами точным ключом.**
> `graph/entrypoints.py` заводит точку входа `* api/x`, а `graph/web.py`
> ищет `GET api/x` — `*` в графе с вызовами фронта не сходится. `web link`
> это делает (п. 3); граф — вне задачи.

**Критерии приёмки** (`SeamWorkspace`)
- `AppsController`: `GET api/apps`, `GET api/apps/{}`, `POST api/apps`;
- `OrdersController`: маршруты с префиксом `api/orders`;
- `CommentsController`: `* api/comments/{}`; `VerbsController`: `GET` и `POST`;
- `Constants.PrefixApi` разрешён в `api`; при неразрешимой константе —
  пустой маршрут и `unresolved`;
- `conventional_controllers` = `[LegacyController]`, базы в нём нет;
- два контроллера с одним относительным маршрутом под разными базами —
  не дубль.

**Проверка**
```bash
uv run pytest tests/test_endpoints_routes.py tests/test_web_link.py -q
```

**Справочники:** `docs/manifest.md` (`Endpoint`), `docs/web.md` (шов),
`frontend-analysis.md` §2.5.

---

## S18 — невосстановленные вызовы в манифест; кандидаты в обёртки ✅

**Цель:** каждый вызов фронта, который не связался, виден с файлом,
строкой и причиной; вызовы, которых не видно вовсе, посчитаны.
Сейчас `calls_unresolved` — одно число (78 на squidex), а настоящих мест
около 166 на squidex и около 120 на abp: вызовы через обёртки не попадают
ни в один счётчик.

**Изменить:** `docpipe/model.py`, `docpipe/web/calls.py`, `docpipe/web/tree.py`,
`docpipe/web/link.py`, `docpipe/setup/candidates.py`
**Создать:** `tests/test_unresolved_calls.py`

**Спецификация**

1. **В манифест.** `Manifest.unresolved_calls: list[UnresolvedCall]`,
   `UnresolvedCall(file, line, http_method, reason, expression, module, member)`,
   `sorted` по `(file, line)`; заполняет `web scan` из `CallScan.unresolved`
   (только файлы модулей). Версия — по правилу 7.
2. **`web link`.** `_unconfigured` (`link.py:143-152`) считает модули
   с восстановленными **или** невосстановленными вызовами: сейчас 17 модулей
   abp молчат, потому что у них ни одного восстановленного. В `counts` —
   `calls_unresolved`. `LinkReport` — минорная версия (S17 уже подняла её
   до 1.1: `unresolved_endpoints`; здесь — 1.2).
3. **Факты для обёрток — в `extract_calls`, без настройки.** Контракт
   `calls.py:7-10` («факты не зависят от конфигурации») сохраняется: извлечение
   записывает `CandidateCall(file, line, receiver, method, args: list[ArgFact])`
   для вызова члена, у которого получатель **не** из `HTTP_RECEIVERS`
   (`calls.py:37`) и хотя бы один аргумент похож на адрес: литерал или шаблон,
   начинающийся с `/`, `api/`, `http://`, `https://`, или объектный литерал
   с полем `url`.
   ```python
   class ArgFact:
       kind: Literal["literal", "template", "identifier", "object", "call", "other"]
       text: str; value: str | None            # разрешённое значение (литерал, шаблон, const по S16)
       fields: dict[str, "ArgFact"]            # для object
       callee: tuple[str, str] | None          # для call: (последний сегмент получателя, метод)
   ```
   И `BuilderUse(file, line, receiver, method, arg: ArgFact)` — вызов HTTP,
   у которого адрес — идентификатор, а его `const` инициализирован вызовом
   (`const url = this.apiUrl.buildUrl('/api/apps')`). Только в памяти
   (`WebScanResult`).
4. **Кандидаты.** `KIND = http-wrappers`: группа `(receiver, method)` —
   число вызовов, позиции аргументов-адресов (`arg` или `arg.field`) с числом,
   файлы, примеры, `configured`. `KIND = url-builders`: группа
   `(receiver, method)` по `BuilderUse`.
5. **Причина невосстановленного** различает «значение — вызов `X.m(…)`»
   (построитель) и «значение — параметр функции» (тело обёртки).
   Обе причины уже даёт S16 (`calls.REASON_PARAMETER`, `_call_reason`):
   поиск имени по областям их различает по построению. Здесь — только
   перенести их в `UnresolvedCall.reason`.

> **Ловушка. Обёртку по имени не распознать.** У squidex `ui.state.ts:71` —
> `get<T>(path, default)` без всякого HTTP. Кандидат — только находка; в
> разбор обёртка входит объявлением (S19), никогда — догадкой.

> **Ловушка. Получатель `HTTP` проходит фильтр `HTTP_RECEIVERS`** —
> в нижнем регистре это `http`. Достаточно добавить `getVersioned` в методы,
> и первым аргументом окажется `this.http`, а не адрес. Номер аргумента
> в объявлении обёртки обязателен. Отсюда и граница кандидата: по букве п. 3
> («получатель не из `HTTP_RECEIVERS`») `HTTP.getVersioned` кандидатом
> не стал бы вовсе — критерий приёмки противоречил бы спецификации.

> **Ловушка. Гипермедиа через построитель на адрес не похожа ничем.**
> `HTTP.requestVersioned(this.http, link.method, url)` при
> `const url = this.apiUrl.buildUrl(link.href)`: ни один аргумент не начинается
> с `/` или `api/`. На squidex так записаны все 47 видимых `requestVersioned`
> и все 16 `this.http.request` — даже с построителем, у которого путь похож
> на адрес (`built_address`), кандидатами из 82 вызовов `HTTP.*` стали бы 31,
> а по одной букве п. 3 — ни одного. Построитель ли `buildUrl`, видно только
> по всему прогону, а факт
> пишется по файлу; поэтому факт хранит и вызов с аргументом-значением вызова
> (`built_by_call`), а засчитывают его кандидаты.

> **Ловушка. Построитель сам похож на обёртку.** `this.apiUrl.buildUrl('/api/apps')`
> — вызов члена с аргументом-адресом; на squidex он встал бы в кандидаты
> в обёртки со 113 вызовами. Группа, результат которой хоть раз стал адресом
> другого вызова, в `http-wrappers` не идёт (названа в `builders`).

> **Ловушка. Голый `'/'` — не адрес.** `path.startsWith('/')`, `parts.join('/')`,
> `url.split('/')` встали бы в кандидаты все; после начала адреса обязателен
> хоть один знак.

> **Ловушка. Комментарий — именованный узел и стоит среди аргументов.**
> `getVersioned(/* клиент */ this.http, url)` сдвинул бы позицию адреса
> на единицу. Аргументы берутся без `comment` (`calls._arguments`) — и у
> `RawCall` тоже: раньше первым «аргументом» мог оказаться комментарий.

> **Ловушка. `setup candidates` пишет кэш разбора под `--root`.** Флага
> `--no-cache` у команды нет; замер на чужом клоне «только для чтения» —
> через `CandidateInputs(..., use_cache=False)`, иначе в клоне появится
> (или обновится) `.docpipe/cache/parse-web.sqlite`.

Отклонения при реализации (08.10): граница кандидата — пара «получатель +
метод» (вызов `HttpClient` — глагол при получателе из `HTTP_RECEIVERS`),
поэтому кандидат и `this.http.request(…)` (`http.request`). Факт пишется,
если аргумент похож на адрес **или** его значение — результат вызова
(`built_by_call`: имя с `const` от вызова или вызов члена прямо в аргументе;
функция без получателя прямо в аргументе — нет, это `map(…)` в каждом
`.pipe`). «Похож на адрес» — значение (литерал, шаблон, `const`, конкатенация)
с началом из `ADDRESS_PREFIXES` и хоть одним знаком после; объект с полем
`url` или с полем-адресом (`0.path`); значение вызова, у которого аргумент —
адрес (`built_address`). `ArgFact` сверх спецификации — `args` (аргументы
вызова) у `call` и у имени, связанного с вызовом (`callee` там тоже);
`identifier` — и обращение к полю (`link.href`). `CandidateCall.args` —
кортеж. Получатель `this`/`super` назван словом, получатель-выражение
(`inject(X).request`) — вызов не пишется. `BuilderUse.arg` — аргумент-адрес
внешнего вызова, `through` — обёртка (пусто у прямого `HttpClient`); пишется
и у вызовов-кандидатов с `built_address`. `extract_calls` возвращает
по-прежнему `RawCall`, полный вариант — `extract_call_facts` → `CallFacts`
(и `scan_call_facts`). `http-wrappers`: позиция засчитывается и у аргумента,
построенного построителем прогона (`_wrapper_positions`), группы-построители
отсеяны; сверх спецификации — `http_calls`, `wrapper_calls`, `builders`,
`limits`; `configured` до S19 всегда `false`. `url-builders`: `uses` — адреса
`HttpClient` по `BuilderUse` плюс аргументы вызовов-кандидатов, построенные
построителем (включая гипермедиа); сверх спецификации — `http_calls`,
`through`, `positions`. «17 модулей abp молчат» — у 16 из 17 нет ни одного
видимого вызова (всё через `restService.request`); после S18 назван один —
`core`, остальных назовёт `web link`, когда обёртку объявят. Текст
`web link` печатает «не восстановлено и в связь не идёт ещё N».

**Критерии приёмки** (`SeamWorkspace`)
- `unresolved_calls` содержит `editor.component.ts`, `links.service.ts`,
  `apps.service.ts` (`list`, `get`, `legacy`) и тела обёрток в
  `http-extensions.ts` — каждое с причиной своего вида;
- кандидаты `http-wrappers`: `HTTP.getVersioned` (позиция 1),
  `HTTP.requestVersioned` (2), `rest.request` (`0.url`); `url-builders`:
  `apiUrl.buildUrl`;
- `web link` называет модуль фронта без `url_rewrite`, даже если у него нет
  ни одного восстановленного вызова.

**Проверка**
```bash
uv run pytest tests/test_unresolved_calls.py tests/test_web_link.py -q
```

---

## S19 — `web.http_wrappers` и `web.url_builders` ✅

**Цель:** найденную агентом обёртку и построитель адреса можно записать
правилом, и вызовы через них связываются детерминированно.

**Изменить:** `docpipe/config.py`, `docpipe/web/calls.py`, `docpipe/web/tree.py`,
`docpipe/model.py`
**Создать:** `tests/test_http_wrappers.py`

**Спецификация**

```python
class ArgRef(_Base):     arg: int; field: str = ""                    # позиционный аргумент; поле объектного литерала
class MethodRef(_Base):  arg: int | None = None; field: str = ""; fixed: str = ""; from_name: bool = False   # ровно один способ
class HttpWrapper(_Base):
    receiver: str; method: str = ""; method_regex: str = ""             # ровно одно из method / method_regex
    url: ArgRef; http_method: MethodRef; reason: str = ""
class UrlBuilder(_Base): receiver: str; method: str; path: ArgRef; reason: str = ""
WebConfig.http_wrappers: list[HttpWrapper] = []
WebConfig.url_builders:  list[UrlBuilder]  = []
```

```yaml
web:
  http_wrappers:
    - receiver: HTTP
      method_regex: "^(get|post|put|delete)Versioned$"
      url: {arg: 1}
      http_method: {from_name: true}
      reason: "обёртка над HttpClient с версией (framework/angular/http)"
    - receiver: rest
      method: request
      url: {arg: 0, field: url}
      http_method: {arg: 0, field: method}
  url_builders:
    - receiver: apiUrl
      method: buildUrl
      path: {arg: 0}
```

1. **Применение — в `build_calls`, не в извлечении** (контракт `calls.py:7-10`).
   `CandidateCall` (S18), совпавший с обёрткой, превращается в `RawCall`:
   адрес — значение `ArgRef`, метод — по `MethodRef` (`from_name` — ведущий
   глагол имени: `getVersioned` → `GET`; глагола нет — невосстановлен
   с причиной). Дальше — обычный путь: `url_rewrite`, `registry_calls`,
   нормализация. Получатель сравнивается по последнему сегменту без регистра.
2. **Построитель.** Адрес вызова (прямого или через обёртку) — идентификатор,
   чей `const` инициализирован вызовом построителя: адрес — значение
   `path` этого вызова.
3. **Тела обёрток** — невосстановленный вызов внутри функции, имя которой
   совпало с объявленной обёрткой, и чей адрес — её параметр, — не идёт
   в `unresolved_calls`; счётчик `calls_inside_wrappers` в сидкаре.
4. `WebCall.via: str = ""` — «`HTTP.getVersioned`» у вызова через обёртку,
   «`apiUrl.buildUrl`» через построитель. Формат — по правилу 7.
5. Проверки загрузки: ровно одно из `method`/`method_regex`; регулярка
   компилируется; у `MethodRef` ровно один способ.

> **Ловушка. Обёртка второго уровня.** У abp `DynamicFormService.getOptions(url,
> apiName)` зовётся с `options.url` из данных: адрес не восстановим ни
> обёрткой, ни построителем. Это решение человека «не восстанавливается,
> причина» (S20, `unresolvable`), а не повод для третьего формата.

> **Ловушка. Если когда-нибудь закэшируют `extract_calls`**, обёртки
> в ключ кэша не войдут — и не должны: извлечение от них не зависит
> по построению. Перенос применения обёрток в извлечение сломает это молча.

> **Ловушка (S18). Не каждый вызов обёртки — факт.** `CandidateCall` пишется,
> только если аргумент похож на адрес или его значение — результат вызова.
> Вызов обёртки с адресом-параметром функции (`getVersioned(this.http, url)`
> внутри функции `load(url)`), с выражением без построителя (`link.href`
> прямо) и с шаблоном, внутри которого построитель
> (`` `${this.apiUrl.buildUrl(link.href)}${query}` ``, squidex
> `assets.service.ts:230`), объявленная обёртка не увидит — молча. Чинить
> расширением факта в `extract_call_facts`, а не применением.

> **Ловушка (S18). У тела обёртки нет члена.** `export module HTTP { export
> function getVersioned(…) }`: функции модуля — не члены класса, и
> `UnresolvedCall.member` у их `http.get(url)` пуст. Имя охватывающей функции
> для п. 3 в `RawCall` не записано — его надо брать из дерева при извлечении.

> **Ловушка (S18). У прямого вызова `HttpClient` нет `ArgFact`.** Для п. 2
> адрес-построитель прямого вызова лежит в `BuilderUse` (`through` пуст)
> с тем же `file` и `line`, что `RawCall`; у вызова через обёртку —
> в `CandidateCall.args[i].callee`/`args`.

> **Ловушка. «Параметр функции» не говорит, чьей.** `REASON_PARAMETER` даёт
> и `http.get(url)` в `getVersioned(http, url)`, и `urls.map(url => http.get(url))`
> внутри той же функции, и `load(http, url)` в другом классе. Тело обёртки
> узнаётся по записи `ParameterRef(функция, номер, поле)` у факта адреса:
> функция — та, чей параметр связал имя (безымянная стрелка — пустое имя),
> номер и поле сверяются с `url` записи. Без номера `getVersioned(url, http)`
> ушёл бы в тела молча.

> **Ловушка. `HttpClient` с заглавной — класс, а не клиент.** Факт «аргументом
> передан сам `HttpClient`» сравнивает имя в нижнем регистре с `HTTP_RECEIVERS`,
> и `injector.get(HttpClient)` у abp прошёл бы фильтр. Имя с заглавной фактом
> не считается.

> **Ловушка. Расширять факт по получателю нельзя.** «Всякий вызов на
> `this.http` без глагола — факт» сделал бы видимыми `follow`
> (`this.http.request(link.method, link.href)`) и тела `request`, но сдвинул бы
> точный список `candidate_calls` в `test_seam_fixture.py`, а «числа без правил
> не меняются» — критерий задачи. Поэтому третий признак факта — клиент
> **в аргументах**, а не в получателе.

> **Ловушка. `http_calls` у кандидатов вырос бы от объявления.** Было
> `len(calls) + len(unresolved)`; после S19 туда попадают вызовы через обёртки,
> а тела обёрток оттуда уходят. Число «сколько вызовов `HttpClient` видит
> прогон» обязано быть одним до и после записи: вычитаются вызовы с `wrapper`,
> прибавляются `inside_wrappers` (squidex — 81 и 81).

> **Ловушка. Отказ прогона ловят три места, и все — как «ручной состав».**
> `web scan`, `symbols --lang ts` и `SetupContext.web` переводят любой
> `ValueError` из прогона в «ошибку в ручном составе страниц». `WrapperConflict`
> — подкласс `ValueError`, и его ветка обязана стоять **раньше**.

> **Ловушка. `(rule := …)` в списковом включении живёт в области функции.**
> В `build_calls` ниже есть `rule = registry_by_route.get(route)`; моржовый
> оператор в генераторе списка связал то же имя, и mypy увидел
> `HttpWrapper | None` вместо `RegistryCall | None`. Цикл вместо включения.

> **Ловушка. `setup explain` сводит решения по записи.** `_Decisions` ключует
> строку `(file, key, value)`: «вызов через обёртку» и «тело обёртки» одной
> записи слились бы под первым `effect` с общей суммой. Строка на запись одна,
> оба числа — в `effect`.

Отклонения при реализации (08.10): сверх спецификации `UnresolvedCall.via`
(вызов через обёртку, не восстановленный, — с `via`; формат 2.4 вместе
с `WebCall.via`) и у невосстановленного метода — пустой `http_method`.
Факт: `RawCall.address` (факт об адресе, `ArgFact`), `wrapper`, `builder`
и свойство `via` (обёртка, затем построитель через запятую:
`"HTTP.getVersioned, apiUrl.buildUrl"`); у `ArgFact` — `confidence`, `reason`
и `parameter` (`ParameterRef`), `_first_argument` удалена: значение
и причину первого аргумента `HttpClient` даёт тот же `_arg_fact`.
`CandidateCall` пишется и при `HttpClient` в аргументах
(`_client_argument`, ловушка S18 о невидимых вызовах обёртки) — на squidex
+1 факт (`assets.service.ts:230`), кандидаты не меняются. Тело обёртки —
имя функции **и** номер с полем параметра (строже буквы п. 3). Построитель
применяется и к вызову прямо в аргументе, и к полю объекта `{ url }`;
у пути из данных причина и выражение — пути (`link.href`), а не `url`.
Получатель записи сравнивается последним сегментом (`this.rest` = `rest`),
регулярка — `re.fullmatch`, как `name_regex`. Загрузка: повтор пары
«получатель + `method`» (и построителя) — отказ; `fixed` — только метод HTTP;
пересечение регулярок на вызове — `WrapperConflict`, код 2. `from_name` —
первое слово имени (`getter` — не глагол). `configured` у кандидатов —
тем же сравнением, что у прогона; `setup explain` называет записи
`web.http_wrappers`/`web.url_builders` и `calls.inside_wrappers`.
Замер: squidex с пятью записями и построителем — 105 восстановлено,
101 связано (оценка ~87 из ~98), `strip_prefix` не нужен; abp
с `restService.request` — 120, 62 из 62 в области `roots` (оценка 60 из 62).

**Критерии приёмки** (`SeamWorkspace` с правилами выше)
- вызовы через `HTTP.getVersioned`, `HTTP.requestVersioned`, `rest.request`
  и через построитель — восстановлены, `via` заполнен, связаны с эндпоинтами
  `AppsController` (после S17);
- тела обёрток в `http-extensions.ts` — не в `unresolved_calls`;
- без правил — числа S18 не меняются.

**Проверка**
```bash
uv run pytest tests/test_http_wrappers.py tests/test_unresolved_calls.py -q
```

**Справочники:** `docs/web.md` (раздел «Обёртки и построители»),
`docs/configuration.md`, `docs/setup-map.md`.

---

## S20 — концы без пары: секция `link` ✅

**Цель:** у каждого конца шва без пары есть место для решения человека
с причиной: эндпоинт, который зовут извне; вызов во внешний адрес; вызов,
который статически не восстановить. Без этого «эндпоинт без вызывающего»
не получает решения никогда (Р-6).

**Изменить:** `docpipe/config.py`, `docpipe/web/link.py`, `docpipe/cli.py:750-811`
**Создать:** `tests/test_link_decisions.py`

**Спецификация**

```python
class ExternalTarget(_Base): host: str = ""; route: str = ""; reason: str; document: bool = False    # ровно одно из host / route
class ExternalCaller(_Base): route: str; http_method: str = ""; reason: str; document: bool = True
class Unresolvable(_Base):   path: str; reason: str                                                    # глоб файла вызова
class LinkConfig(_Base):
    external_targets: list[ExternalTarget] = []
    external_callers: list[ExternalCaller] = []
    unresolvable:     list[Unresolvable]   = []
DocpipeConfig.link: LinkConfig = LinkConfig()
```

1. `reason` обязателен и непуст — это решения «документируем или нет»
   (прецедент — `AddPage`, `web/overrides.py:63-70`).
2. **Сопоставление.** `host` — маска по `WebCall.host` (S16) в нижнем регистре;
   `route` — `discovery.matches_glob` по нормализованному маршруту ключа;
   `http_method` пуст — любой метод; `path` — по файлу вызова.
3. **`web link`.** Вызов **без эндпоинта**, совпавший с `external_targets`, —
   категория `external_targets`, а не `calls_without_endpoint`; эндпоинт без
   вызывающего, совпавший с `external_callers`, — `external_callers`;
   невосстановленный вызов (S18), совпавший с `unresolvable`, —
   `declared_unresolvable`. У каждой записи — `decision`: какое правило
   (индекс и причина). Вызов с хостом, у которого эндпоинт нашёлся, —
   связь, как обычно. `build_link_report(..., link: LinkConfig)`.
4. `document` пишется в отчёт; проекция в документы — пункт бэклога
   «Фронт↔бэк в документах», не этот план.

> **Ловушка. Внешний адрес неотличим от внутреннего без хоста.** На squidex
> `help.service.ts` зовёт `raw.githubusercontent.com`, а нормализация срезала
> хост — вызов числился «без эндпоинта» внутри своего бэка. Поэтому S20
> стоит после S16.

> **Ловушка. Маску маршрута нельзя нормализовать `route.normalize_route`.**
> Та отрезает query по первому `?` вне скобок, а в маске `?` — знак глоба:
> `api/v?/**` стал бы `api/v`. Маска нормализуется своей
> `config.route_pattern` — подстановки в `{}` (и `:id`), крайние `/`,
> регистр, — иначе маска, списанная с `[Route("content/{app}")]`, не совпала
> бы с ключом, который из того же атрибута собрал разбор.

> **Ловушка. Правило решает только о конце без пары, и `setup explain` без
> сведения этого не знает.** Совпавшее `external_targets` у связанного вызова
> ничего не решило, а сведение требует обоих прогонов: ответ о файле фронта
> запускал бы шаг 1, и его отказ ронял бы ответ (ловушка S23). Поэтому
> `explain` зовёт `SetupContext.link`, только если запись совпала с вызовом
> или эндпоинтом под целью; не собрался отчёт — заметка `link.unavailable`.
> Неудачу `cached_property` не запоминает, и вторая сторона повторила бы
> прогон — результат держит черновик ответа. `link.unresolvable` сведения
> не требует.

> **Ловушка. `unresolvable` — глоб файла, и он накрывает будущие вызовы.**
> Новый вызов в том же файле, который восстановила бы обёртка или
> построитель, уйдёт в `declared_unresolvable` без единого сообщения.
> Восстановленные вызовы файла правило не трогает; глоб — уже файла, а не
> каталога. Охват записи и её сдвиг покажут S24–S25.

> **Ловушка. «Почти» — тоже пара.** Вызов внешнего хоста, «почти» совпавший
> с эндпоинтом из одних параметров (`_fixed_segments` пуст у обоих, см.
> `findings-seam.md`), остаётся связью, и `external_targets` его не заберёт —
> так велит п. 3. Ложная связь видна в `links` с `match: almost`.

> **Ловушка. Маска, которая не совпадёт ни с чем, должна падать при загрузке.**
> `host: "https://ext.example.org"` и `host: "x:8080"` (у `WebCall.host`
> нет схемы и порта), `http_method: GTE`, одно условие дважды (сработала
> бы только первая запись) — отказ загрузки, а не правило, которое молча
> не действует.

**Отклонения при реализации (08.10).** Функция сведения в коде —
`web.link.build_report` (CLI импортирует её под своим именем); `link` —
ключевой аргумент с умолчанием «секции нет», как `configured_modules`.
Из настройки оба аргумента собирает одна `report_for_settings(backend, web,
settings)` — её зовут `web link` и `SetupContext.link`: ключ, учтённый в одной
копии сборки аргументов, до другой не доехал бы. `SetupContext.link` заведён
здесь, а не в S21: им пользуется `setup explain` (ниже). Сверх спецификации:
`LinkDecision` — `index` (с нуля), `rule` (условие как написано) и `reason`;
`CallRef.host` (S21 группирует по хосту, и без него правило не написать, не
открыв код); категории решений — `DECIDED_CATEGORIES`, в `--fail-on` их нет
(решение — не находка); записи категорий — подклассы `CallRef`, `EndpointRef`
и `UnresolvedCall` с `document` и `decision`. `counts.calls_unresolved` —
только невосстановленные **без решения** (так их ждёт S24: находка
`link.calls_unresolved` исчезает после `link.unresolvable`); первая строка
текста печатает сумму с `declared_unresolvable`, и с `calls_total` это по-прежнему
все вызовы в коде. Эндпоинт `*` совпадает с правилом любого метода — как
связывается с вызовом любого метода. Сработала первая совпавшая запись
в порядке файла (как `scope_entry`). Короткой формы у записей нет; строка
вместо записи — отказ с подсказкой, как у `not_enrolled`. `path` проверяется
`_repo_relative`. `setup explain` добавляет в `decisions` ключи
`link.external_targets`, `link.external_callers`, `link.unresolvable`.

**Критерии приёмки** (`SeamWorkspace`)
- `external_targets: [{host: "ext.example.org", reason: …}]` — вызов
  `feed.service.ts` в своей категории, `calls_without_endpoint` меньше на один;
- `external_callers: [{route: "content/**", reason: …}]` — эндпоинты
  `ContentController` не в «без вызывающего»;
- `unresolvable: [{path: "**/links.service.ts", reason: …}]` — гипермедиа
  в `declared_unresolvable`;
- пустой `reason` — отказ загрузки.

**Проверка**
```bash
uv run pytest tests/test_link_decisions.py tests/test_web_link.py -q
```

**Справочники:** `docs/web.md`, `docs/configuration.md`, `docs/setup-map.md`.

---

## S21 — сводка шва кластерами (`setup link`) ✅

**Цель:** агент получает несвязанное кластерами с подсказкой правила,
которое их свяжет, — и проверяет правило прогоном, а не перебором.

**Изменить:** `docpipe/web/link.py`, `docpipe/cli.py`, `docpipe/setup/context.py`
(заведён S23; `SetupContext.link` — `report_for_settings` с `url_rewrite`
и секцией `link` — заведён S20)
**Создать:** `docpipe/setup/link.py`, `tests/test_setup_link.py`

**Спецификация**

1. **Поля `LinkReport`** (минорная версия): у `CallRef` — `module`,
   `confidence`, `member`, `via`; у `EndpointRef` — `file`, `line`
   (`sources[0].path` узла и `Endpoint.line`, `model.py:350-356`).
2. **`setup/context.py`** — единственное место, где команды `setup`
   собирают прогоны: `SetupContext.build(root, config_path)` с ленивыми
   `scan` (`emit.run`), `web` (`web.tree.run` с правилами `pages.yaml`),
   `link` (`build_link_report` с `url_rewrite` и `link`). S23–S27 расширяют
   его, а не собирают прогоны сами — урок трёх копий `_prepare`.
3. **`link_clusters(ctx, *, category, by, limit, offset) -> LinkClusters`.**
   Категории: `calls_without_endpoint`, `calls_unresolved`,
   `endpoints_without_caller`, `almost`, `external_targets`,
   `external_callers`, `declared_unresolvable`. Ключ кластера `by`:
   `module`, `prefix` (два первых сегмента маршрута), `file`, `controller`,
   `reason`, `host`. Кластер: `key`, `count`, `examples` (до трёх:
   `file:line` и маршрут или выражение).
4. **Подсказка правила префикса.** Для `calls_without_endpoint` при `by=module`:
   перебрать `strip_prefix` из первых одного-двух сегментов маршрутов
   кластера (и пустой) × `add_prefix` из первых сегментов маршрутов
   эндпоинтов (и пустой); выбрать пару, при которой точно связывается больше
   всего вызовов кластера; при равенстве — более короткий `strip_prefix`,
   затем меньший лексикографически `add_prefix`. В кластер —
   `suggested_rewrite: {module, strip_prefix, add_prefix, would_link}`.
   Это то же правило, которое агент иначе подбирал бы прогонами.
5. Команда `docpipe setup link --category --by --limit --offset --format
   --root --config`.

> **Ловушка. Повторы считать по `(file, line)`.** До S16 вызов дублировался
> на все узлы файла; и после него кластер по `controller` или `module`
> обязан считать места в коде, а не пары «вызов × узел».

> **Ловушка. Подсказку выбирает и пустой маршрут.** `add_prefix: api` делает
> из `GET ''` корень API `GET api`: на squidex без записей шва единственной
> «подсказкой» было бы `add_prefix: api` ради поля `@Input() fileSource = ''`
> (`asset-text-editor.component.ts:42`), «связанного» с
> `UsersController.GetUserResources` (`[Route("")]` под базой `api`). Выбор
> пары не засчитывает маршрут без сегмента-литерала (`''`, `{}/{}`);
> `would_link` считает все места — иначе он разошёлся бы с прогоном.

> **Ловушка. Пара, связывающая кластер, развязывает остальное.** Правило
> одно на модуль и переписывает все его адреса: `strip_prefix: api`,
> связавший пять вызовов, развязал бы прямой `api/orders`. По букве п. 4
> («больше всего вызовов кластера») такая пара выигрывает; выбор — по
> `would_link − would_unlink > 0`, и оба числа в подсказке.

> **Ловушка. Ключ вызова собран после правила модуля.** Пара поверх ключа
> модуля с непустым `url_rewrite` — композиция двух правил, а не замена:
> сырого адреса в манифесте нет. Такому модулю подсказки нет
> (`rewrite_note`); пустая запись не мешает — ключи тогда как написаны.

> **Ловушка. `url_rewrite` переписывает и абсолютные адреса** (срез хоста —
> первый шаг `normalize_route`): `https://ext.example.org/feed.json` после
> `strip_prefix: api, add_prefix: v2` — `v2/feed.json`. Маска `route`
> в `link.external_targets`, записанная до правила, перестаёт совпадать
> молча; маска `host` — нет. Подсказка этого не проверяет.

> **Ловушка. Команда «Проверка» пишет кэш в фикстуру**: без `--no-cache`
> `setup link --root tests/fixtures/SeamWorkspace` кладёт
> `.docpipe/cache/` в каталог фикстуры (он в `.gitignore`, как у соседей).

**Отклонения при реализации (08.10).** П. 2 сделан до задачи: `SetupContext`
заведён S23, `link` — S20 (`report_for_settings`); здесь контекст не
расширялся — ключи эндпоинтов подсказка берёт `web.link.backend_keys`
от `ctx.scan.manifest`. `_backend_keys` и `_exact` стали публичными
(`backend_keys`, `exact_keys`): своя проверка «свяжется ли» разошлась бы
со сведением на первом эндпоинте `*`. `LinkReport` 1.4 сверх п. 1:
`module` у `EndpointRef` и у `Link` (ключ `module` у эндпоинтов и у «почти»),
список `calls_unresolved` (категория `calls_unresolved` иначе собирала бы
разбиение «решено / не решено» второй копией `_declared`). `EndpointRef.file` —
span узла, в диапазон которого попала `Endpoint.line`, иначе `sources[0]`.
Ключ `decision` (условие записи `link`) — сверх списка п. 3, у трёх решённых
категорий; у каждой категории свои ключи (`BY_KEYS`, первый — умолчание
`--by`): у невосстановленного вызова нет маршрута и узла, у эндпоинта —
хоста. `controller` у вызова — узел-вызывающий, у «почти» — контроллеры
эндпоинтов. Место эндпоинта — `(узел, член, метод, маршрут)`: `AcceptVerbs`
на одной строке — два эндпоинта. `--category` по умолчанию —
`calls_without_endpoint` (команда «Проверка» её не называет); в отчёте —
`places`, `linked` и `categories` (мест в каждой категории). Подсказка —
`{module, strip_prefix, add_prefix, would_link, would_unlink}`, выбор
по чистому выигрышу (ловушки выше), равенство — длина `strip_prefix`, затем
`add_prefix`, затем `strip_prefix`; нет подсказки — `null` и `rewrite_note`
(`NOTE_MODULE_HAS_RULE`, `NOTE_NO_GAIN`). Критерий «would_link равно
связанному» проверен на копии `SeamWorkspace` с префиксом базы `v2`
(в фикстуре префиксы совпадают, и правилу нечего связывать): `strip_prefix:
api, add_prefix: v2` — 6 из 9, прогон с записью — те же 6; с прямым
`api/orders` — 5 и развязан 1, прогон подтверждает оба. Замер на копии
squidex с `PrefixApi = ""` (бэк за прокси, срезающим `/api`):
`strip_prefix: api` — свяжет 99 из 104, прогон с записью — 99.

**Критерии приёмки** (`SeamWorkspace` без `url_rewrite`, с правилами S19)
- кластер модуля фронта предлагает `strip_prefix`/`add_prefix`, при которых
  `would_link` равно числу вызовов, которые действительно связываются после
  записи правила (проверить прогоном в тесте);
- `by=reason` по `calls_unresolved` разделяет гипермедиа и изменяемое поле;
- двойной прогон — байт в байт.

**Проверка**
```bash
uv run pytest tests/test_setup_link.py -q
uv run docpipe setup link --root tests/fixtures/SeamWorkspace --config tests/fixtures/SeamWorkspace/docpipe.yaml --by module --format json
```

**Справочники:** `docs/setup.md`.

---
## S22 — вторая форма с причиной; `not_enrolled` ✅

**Цель:** у решения человека по каждому ключу настройки есть место для
причины, и «решили не брать» отличимо от «ещё не смотрели» (Р-2, Р-6).

**Изменить:** `docpipe/config.py`, `docpipe/tree.py`, `docpipe/emit.py`,
`docpipe/web/tree.py`, `docpipe/cli.py`, `docpipe/discovery.py` (если нужно)
**Создать:** `tests/test_config_reasons.py`

**Спецификация**

```python
class Enrolled(_Base):      glob: str; reason: str = ""
class NotEnrolled(_Base):   glob: str; reason: str              # непустая
class ExcludeEntry(_Base):  glob: str; reason: str = ""
class RootEntry(_Base):     path: str; reason: str = ""
class NamedDecision(_Base): name: str; reason: str = ""

DocpipeConfig.enrolled:            list[str | Enrolled]      = ["**"]
DocpipeConfig.not_enrolled:        list[NotEnrolled]         = []        # новый ключ
DocpipeConfig.exclude:             list[str | ExcludeEntry]  = []
DocpipeConfig.di_methods:          list[str | NamedDecision] = []
DocpipeConfig.dispatch_interfaces: list[str | NamedDecision] = []
WebConfig.roots:                   list[str | RootEntry]     = ["."]
UrlRewrite.reason:                 str = ""
RegistryCallConfig.reason:         str = ""
```

```yaml
enrolled:
  - "src/**"                                   # короткая форма работает как раньше
  - glob: "tools/Integration/**"
    reason: "интеграционный код команды — документируем"
not_enrolled:
  - glob: "samples/**"
    reason: "примеры для внешних разработчиков, не продукт"
exclude:
  - "**/Migrations/**"
  - glob: "vendor/**"
    reason: "сторонний код, копия пакета"
di_methods:
  - name: AddSingletonAs
    reason: "обёртка Squidex.Hosting"
```

1. **Нормализованные свойства** — единственный путь чтения:
   `enrolled_globs`, `not_enrolled_globs`, `exclude_patterns`,
   `di_method_names`, `dispatch_interface_names`, `web.root_paths`
   (все — `list[str]` в порядке файла). Потребители: `tree.py:63`
   (`enrolled`), `emit.py:85` (`exclude`), `emit.py:447-449` (`di_methods`
   и ключ кэша), `emit.py:483` (`dispatch_interfaces`), `web/tree.py:960`
   (`web.roots`, `exclude`), `cli.py:1859` (`graph build`), `configcheck.py`
   (S04). Валидатор `_repo_relative` — к строке и к `path` записи.
2. **`not_enrolled`.** Модуль, совпавший с `not_enrolled`, не включён.
   Совпадение с `enrolled` и `not_enrolled` сразу — **отказ прогона** с именем
   модуля и обоими шаблонами: противоречие двух решений человека — дефект,
   и правило приоритета спрятало бы его. Функция `config.scope_of(project_file, settings)
   -> Literal["enrolled", "not_enrolled", "undecided"]`: `undecided` —
   ни с одним, и только когда `enrolled` задан явно (не умолчание `["**"]`).
   Её зовут `tree._apply_config` и S24; в манифест состояние не идёт —
   `Module.enrolled` прежний.
3. **Ключ кэша разбора** — `sorted(di_method_names)`: правка причины
   кэш не сбрасывает (правило 10).
4. **Тест-сторож** (по образцу `tests/test_documents_state.py`): обход AST
   `docpipe/**` не находит обращений к полям `.enrolled`, `.exclude`,
   `.di_methods`, `.dispatch_interfaces`, `web.roots` вне `config.py`
   и `configcheck.py`.

> **Ловушка. `exclude` в виде объектов ломается в трёх местах молча:**
> `emit.py:85` складывает шаблоны во множество (`{*DEFAULT_EXCLUDE,
> *config.exclude}`; запись — замороженная модель и потому хэшируема,
> множество её принимает, а падает следующий `sorted` на сравнении строки
> с объектом), `cli.py:1859` передаёт `list(settings.exclude)` движку,
> `discovery.py:77-88` обрезает каталог по строковому суффиксу `/**`.
> Отсюда правило: потребитель видит только строки.

> **Ловушка. «`enrolled` задан явно» — это `model_fields_set`, а не
> сравнение с `["**"]`.** Умолчание решением человека не считается: при нём
> `not_enrolled` вырезает модуль без противоречия. Явный `enrolled: ["**"]`
> рядом с `not_enrolled` — отказ (решение «всё» против решения «не это»).
> Сравнение по значению сделало бы `**` правилом приоритета, которого
> П-2 не допускает. `model_copy(update=…)` добавляет ключ в `fields_set`,
> поэтому копия с `enrolled` тоже «явная».

> **Ловушка. Объединение `str | Model` без дискриминатора даёт две ошибки
> на одну опечатку**, и первой идёт «Input should be a valid string» —
> подсказка слабой модели свернуть запись в строку и потерять причину.
> Форма выбирается по виду значения (`pydantic.Discriminator` с функцией,
> `Tag("short")`/`Tag("entry")`); в `config.py` уже есть своя модель
> `Discriminator` (`registry_calls`), поэтому импорт — под другим именем.
> Аннотация остаётся `list[...]`, и `_list_fields` (S02) ключ видит.

> **Ловушка. Тест S02 сверяет перечень ключей-списков с ручным списком**
> (`test_list_keys_are_taken_from_the_model_not_from_a_hand_list`):
> новый ключ `not_enrolled` обязан в него попасть — это единственная правка
> существующих тестов в задаче, и она и есть смысл того теста.

> **Ловушка. Строки в тестах напрямую** (`test_tree.py:238, 250, 259`,
> `test_scoped.py:308, 330`): конструктор обязан принимать короткую форму,
> иначе упадут тесты, к настройке отношения не имеющие.

> **Ловушка. Совместимость в одну сторону.** Старый установленный `docpipe`
> отвергнет `not_enrolled` и записи-объекты (`extra="forbid"`). Справочник
> говорит это прямо: обновлённую настройку читает только обновлённый
> инструмент.

**Критерии приёмки**
- `tests/golden/doc-tree.json` и все существующие тесты — без изменений
  (короткая форма даёт байт в байт тот же манифест);
- смешанный список строк и записей загружается во всех шести ключах;
- `not_enrolled` без `reason` — отказ загрузки; модуль под `enrolled`
  и `not_enrolled` — отказ прогона с обоими шаблонами;
- `scope_of` на `SampleSolution`: при `enrolled: ["src/Sample.Common/**"]`
  модуль `Sample.Pricing.Api` — `undecided`, с записью в `not_enrolled` —
  `not_enrolled`;
- правка `reason` у `di_methods` не меняет ключ кэша (тест сравнивает ключи);
- тест-сторож проходит и падает на временно вставленном `settings.enrolled`.

**Проверка**
```bash
uv run pytest tests/test_config_reasons.py tests/test_tree.py tests/test_scoped.py tests/test_scan_e2e.py tests/test_determinism.py -q
```

**Справочники:** `docs/configuration.md` (вторая форма, `not_enrolled`,
совместимость), `docs/setup-map.md`, `docpipe.example.yaml` (пример формы).

---

## S23 — «что решено об этом коде» (`setup explain`) ✅

**Цель:** вход «расширение области» (Р-6, п. 5): человек называет код,
агент показывает, входит ли он в область и **какие правила и с какими
причинами** о нём уже решили, — и знает, что править.

**Создать:** `docpipe/setup/explain.py`, `tests/test_setup_explain.py`
**Изменить:** `docpipe/setup/context.py`, `docpipe/cli.py`

**Спецификация**

`explain_path(ctx, target: str, *, limit: int = 20) -> PathExplain`.
`target` — файл, каталог или глоб от `--root`.

```python
class DecisionRef: file: str; key: str; value: str; reason: str      # «docpipe.yaml», «exclude», «vendor/**», «сторонний код…»
class ModuleScope: module: str; project_file: str; scope: Literal["enrolled", "not_enrolled", "undecided"]; by: DecisionRef | None
class PathExplain:
    schema_version: Literal["1.0"]; target: str; matched_files: int
    excluded_by: DecisionRef | None          # exclude или встроенный отсев («встроенный отсев», шаблон)
    root: str | None; web_root: str | None   # какая запись roots / web.roots накрывает
    modules: list[ModuleScope]
    symbols: dict[str, int]                  # по состояниям
    symbol_rows: list[...]                   # строки symbols --path (S07), до limit
    pages: list[str]; page_overrides: list[DecisionRef]
    calls: dict[str, int]; endpoints: dict[str, int]
    documents: list[tuple[str, str, str]]    # doc_path, status, file_action
    owners: dict[str, int]                   # команда → узлов; "" — без владельца
    decisions: list[DecisionRef]             # ВСЕ решения настройки, которые касаются target
```

`decisions` — главное поле: плоский список всех ключей и правил, которые
решили судьбу этого кода (шаблон `exclude`, `enrolled`/`not_enrolled`,
правила отсева и классификации с победами в этом коде, записи `pages.yaml`,
`url_rewrite` модуля, обёртки, правила `link`, правила владения).
Модуль файла — `.csproj`, чей каталог — самый длинный префикс пути
(вложенные проекты). Контекст (`setup/context.py`) получает план шага 2
(`step2.prepare` от манифеста в памяти, S06) и владение.

Команда `docpipe setup explain PATH --root --config --limit --format`.

> **Ловушка. Отсечённое обходом не доходит ни до чего.** Если `target`
> под `exclude`, символов, страниц и документов нет — и ответ обязан сказать
> «отсечено шаблоном X (причина)», а не напечатать пустые разделы, которые
> читаются как «здесь ничего нет».

> **Ловушка. Встроенный отсев** (`DEFAULT_EXCLUDE`, `emit.py:59-71`) причины
> не имеет — `DecisionRef` с `file: "встроенный отсев"`. Иначе `obj/` выглядит
> решением, которого человек не принимал.

> **Ловушка. Причину решения мимо свойства не прочитать.** Тест-сторож S22
> запрещает `settings.exclude`, `settings.enrolled` и соседей вне `config.py`,
> а нормализованные свойства отдают одни строки. Причины читаются новыми
> свойствами `*_entries` (`enrolled_entries`, `not_enrolled_entries`,
> `exclude_entries`, `di_method_entries`, `dispatch_interface_entries`,
> `web.root_entries`): короткая форма — запись с пустой причиной. Решившая
> область запись — `config.scope_entry`, производная от `scope_of`, а не
> второй подсчёт. Эти же свойства нужны S24 (`without_reason`).

> **Ловушка. `discover` отбрасывает отсечённое**, поэтому файлы цели считает
> свой обход — без отсева и без отсечения каталогов; «исходник ли это»
> отвечает общий `discovery.file_field`, а не копия списка расширений.
> С одним файлом совпадают и два встроенных шаблона (`obj/…/x.g.cs` —
> `**/obj/**` и `**/*.g.cs`): в `decisions` оба, у каждого свой счёт,
> а `excluded_by` — накрывший больше файлов, при равенстве — встроенный
> раньше настройки, в порядке записи. Повтор шаблона в одном файле — одна
> запись, иначе файл посчитался бы дважды.

> **Ловушка. Шаг `web` не нужен коду .NET.** Прогоны контекста ленивые,
> и `explain_path` зовёт только нужные: нет исходников .NET под целью —
> шаг 1 не идёт, нет исходников фронта — не идёт шаг `web`, цель отсечена
> или вне корней — ни одного. Иначе отказ шага `web` (неоднозначное снятие
> в `pages.yaml`) ронял бы ответ о каталоге .NET. Шаг 2 — по плану на
> каждый манифест (`ctx.plan`, `ctx.web_plan`), документы отбираются
> по `node_id`, поэтому «сироты» чужого манифеста в ответ не попадают.

**Отклонения реализации (S23, 08.10).** `setup/context.py` заведён здесь,
а не в S21 (S21 ещё не сделана): `scan`, `web`, `plan`, `web_plan`,
`ownership`; `link` добавит S21. Туда же переехал `InputError`,
а `setup candidates` перешёл на контекст без смены поведения
(`CandidateInputs` — имя `SetupContext`). Разрешение `web.pages` вынесено
в `web.overrides.configured_pages`: ответ «какой `pages.yaml`» обязан быть
один у `web scan` и у контекста. При сведении с S13–S14 (там загрузчик
уже переехал из CLI в `web.overrides.load_page_overrides`, а оба вида
фронта ходили через свой `_web_scan` в `candidates.py`) загрузчик остался
один: `load_page_overrides` разрешает ключ через `configured_pages`,
контекст берёт путь у `configured_pages` (`pages_file` — его называет
`explain` в решениях) и правила у `load_page_overrides`, а виды
`features` и `registry-calls` перешли на `ctx.web` и `ctx.overrides`.
При сведении с S18 (виды `http-wrappers` и `url-builders` звали тот же
`_web_scan`) `_web_scan` не возвращён: оба вида берут `ctx.web`
(`candidate_calls`, `builder_uses`, `calls`), поведение то же.

> **Ловушка. `ValidationError` pydantic — подкласс `ValueError`.** Прогон
> шага `web` переводит `ValueError` в `InputError` (неоднозначное снятие
> в `pages.yaml` — ошибка настройки), и без отдельной ветки
> `except ValidationError: raise` перед ней модель, не собравшаяся внутри
> прогона, ушла бы кодом 2 под «ошибкой конфигурации» без трассировки.
> Ветка была в `_web_scan` кандидатов (S13–S14) и при переезде
> в `SetupContext.web` сохранена.

Модель против спецификации: `root`/`web_root` → `roots`/`web_roots`
(списки: цель шире одной записи — `.` при `roots: [backend]` — накрыта несколькими,
и `None` читался бы как «не накрыта»); у `DecisionRef` добавлены `effect`
(что запись сделала: «вид service», «команда core» — по `id` правила
не видно) и `count` (охват записи в цели); `pages` — `{id, title}`
(`explain.PageRef`); `documents` — записи `{doc_path, status, file_action,
node}` вместо кортежей; добавлены `documents_total`, `unresolved_reasons`
и `notes` (`code`, `message`). `calls` — без категорий связи (`linked`,
`almost`, «без эндпоинта»): их источник — `ctx.link` (S21). У команды
есть `--no-cache`, как у `symbols`.

**Критерии приёмки** (`SampleSolution`, `SeamWorkspace`)
- `src/Sample.Pricing.Api/obj` — `excluded_by` встроенным отсевом, остальное пусто;
- `src/Sample.Pricing.Api/Services` — модуль `enrolled`, символы с решениями,
  среди `decisions` — правило-победитель вида `service`;
- с `not_enrolled: [{glob: "src/Sample.Pricing.Api/**", reason: X}]` — `scope:
  not_enrolled`, `by.reason == X`;
- файл фронта `SeamWorkspace` — вызовы по категориям и правила шва, которые его касаются.

**Проверка**
```bash
uv run pytest tests/test_setup_explain.py -q
uv run docpipe setup explain src/Sample.Pricing.Api/Services --root tests/fixtures/SampleSolution --format json
```

**Справочники:** `docs/setup.md`. `module-review.md`: `setup/explain.py`, `setup/context.py`.

---

## S24 — необъяснённое в области (`setup status`) ✅

**Цель:** одна команда отвечает, что в области ещё без решения и что сломано,
— это и есть проверяемое состояние Р-6. Остановку решает человек; команда
показывает, на чём он останавливается.

**Создать:** `docpipe/setup/status.py`, `tests/test_setup_status.py`
**Изменить:** `docpipe/discovery.py`, `docpipe/setup/context.py`, `docpipe/cli.py`,
`docs/setup-map.md` (колонка «Находка»)

**Спецификация**

1. **Коды находок** — константа `FINDING_CODES` в `status.py`
   (код, категория, заголовок, где лежит решение). Её читают S28 (каталог
   вопросов) и тесты карты (S09).

   | Код | Категория | Источник | Где решение |
   |---|---|---|---|
   | `scope.module_undecided` | решение | `scope_of` = `undecided` (S22) | `enrolled` / `not_enrolled` |
   | `scope.front_undecided` | решение | фронты разведки (S10) вне `web.roots` и не под `exclude` | `web.roots` / `exclude` |
   | `dotnet.undecided`, `web.undecided` | решение | `Stats` (S05) | `rules.yaml` |
   | `link.calls_unresolved` | решение | `unresolved_calls` (S18) | `http_wrappers`, `url_builders`, `link.unresolvable` |
   | `link.calls_invisible` | решение | кандидаты `http-wrappers` (S18) | `http_wrappers` |
   | `link.calls_without_endpoint` | решение | `web link` | `url_rewrite`, `link.external_targets` |
   | `link.endpoints_without_caller` | решение | `web link` | `link.external_callers` |
   | `link.almost` | решение | `web link` | `url_rewrite` |
   | `link.module_without_rewrite` | решение | `web link` | `url_rewrite` (пустая запись — «проверено») |
   | `link.registry_unresolved` | решение | сидкар `web scan` | `registry_calls` |
   | `pages.route_unresolved`, `pages.unanchorable`, `pages.layout` | решение | `web pages` (заметки `NOTE_*`) | `pages.yaml` |
   | `pages.stale_overrides` | решение | `OverrideReport.stale` | `pages.yaml` |
   | `docs.orphan` | решение | `docs status` | `docs adopt` / удаление |
   | `owners.unowned`, `owners.not_configured` | решение | `docs owners --lint` (S06) | `ownership.yaml` |
   | `parse.errors` | решение | `parse_error_files` | отсев `path_glob` с причиной или правка исходника |
   | `config.problems` | дефект | `config check` (S04) | — |
   | `docs.broken`, `docs.shadowed` | дефект | `docs status`, `shadowed_docs` | — |
   | `load.errors` | дефект | предупреждения шага 2 (S03), ошибки загрузки | — |
   | `docs.unavailable` | дефект | шаг 2 не собрался (нет скелетов и т. п.) | — |

2. **Модель.**
   ```python
   class Cluster:    slice: str; key: str; count: int; examples: list[str]
   class Finding:    code: str; category: Literal["decision", "defect"]; count: int; previous: int | None
                     decision_home: str; clusters: list[Cluster]
   class Coverage:   id: str; file: str; key: str; count: int; previous: int | None; reason: str
   class OutOfScope: modules: int; fronts: int; symbols: int; examples: list[str]
   class SetupStatus:
       schema_version: Literal["1.0"]
       findings: list[Finding]            # только с count > 0, порядок — по FINDING_CODES
       coverage: list[Coverage]           # охват каждого решения настройки (для S25)
       out_of_scope: OutOfScope
       without_reason: list[str]          # решения короткой формой, без причины: подсказка, не находка
       unexplained: int; defects: int
   ```
   **Охват** — сколько чего досталось каждому решению: правилу отсева
   (`Stats.skipped`), правилу классификации (победы, S07), шаблонам
   `enrolled`/`not_enrolled` (модули), шаблону `exclude` (файлы — новое
   `excluded_by: dict[str, int]` в результате обхода `discovery`), записям
   `url_rewrite`, `registry_calls`, `http_wrappers`, `url_builders`, `link.*`,
   `pages.yaml`, правилам владения, `di_methods` (регистрации),
   `dispatch_interfaces` (обработчики). `id` решения стабилен: файл + ключ +
   значение (шаблон, `id` правила, модуль).
3. **Сравнение между своими прогонами.** `--out PATH` пишет отчёт,
   `--baseline PATH` берёт `previous` из такого файла; без флага — без
   сравнения. Это инструмент агента внутри сессии (правило → прогон →
   разница); файл лежит где угодно вне настройки и не коммитится. Сервер
   (S27) помнит прошлый ответ сам. Принятого состояния в файле нет (П-1):
   его роль играет коммит настройки, и с ним работает ревью (S25).
4. **Охват по файлам** — в памяти у каждого решения есть разбивка «файл →
   сколько решено»: символы — по `sources`, вызовы — по `file`, эндпоинты —
   по файлу узла, модули — по `.csproj`. В отчёт идёт только сумма;
   разбивку берёт ревью (S25).
5. **Код возврата** — 0; с `--fail-on-unexplained` — 1, если `unexplained > 0`
   или `defects > 0`.
6. Текст: первая строка — «В области: N находок без решения, M дефектов;
   вне области: модулей A, фронтов B»; дальше по находке — заголовок,
   число и разница, три кластера.

> **Ловушка. Только прогоны в памяти, никогда — артефакты на диске.** Агент
> правит настройку и сразу зовёт `setup status` (Р-1); манифест на диске
> собран прошлой настройкой. Отчёт по нему покажет, что правка ничего не
> изменила, — и агент начнёт чинить то, что уже починено.

> **Ловушка. `scope.module_undecided` — только при явном `enrolled`.**
> При умолчании `["**"]` включено всё, и находка была бы на каждом модуле
> каждого репозитория, где область не настраивали.

> **Ловушка. Шаг 2 может не собраться** (нет скелетов, битый `ownership.yaml`).
> Это дефект `docs.unavailable` с причиной, а не падение команды: остальные
> находки агенту нужны и тогда.

> **Ловушка (найдена при реализации). Шов без второй стороны — не находка.**
> На `SampleSolution` фронта нет, и каждое действие контроллера — «эндпоинт
> без вызывающего», решения у которого нет. Концы без пары, модуль без
> `url_rewrite` и дубли ключей считаются, только когда в области есть и
> модули .NET, и модули фронта; факты одного фронта (`calls_unresolved`,
> `calls_invisible`, `registry_unresolved`) — всегда.

> **Ловушка (найдена при реализации). Сирота одного плана — не сирота.**
> Дерево документов у шага 1 и шага `web` общее: документ фронта — сирота
> для плана .NET и наоборот. `docs.orphan` — сирота **обоих** планов; не
> собрался один план — сирот не считаем.

> **Ловушка (найдена при реализации). Охват шаблона, отсёкшего каталог, —
> ноль.** `discover` в отсечённый каталог не заходит, и `**/obj/**` на свежем
> клоне «не отсёк ничего». `discover(..., count_excluded=True)` открывает
> такие каталоги отдельным проходом только ради счёта (`Discovered.excluded`,
> `excluded_by`); без флага поле `None`, а не пустое: «не считали» не читается
> как «не отсекли». `scan` и `web scan` флаг не ставят (`node_modules`).

> **Ловушка (найдена при реализации). У двух находок нет записи «решено:
> не то».** `link.calls_invisible`: на squidex все 9 кандидатов `http-wrappers`
> — не HTTP (`window.open`, `url.startsWith`, `L.tileLayer`), на abp 14
> (`form.patchValue`, `location.replace`); объявить их обёрткой нельзя,
> `link.unresolvable` — про невосстановленные вызовы. `pages.layout`:
> на squidex на `/` — `HomePageComponent`, экран входа; снять его — ошибка,
> а `add` того же маршрута — протухшее `add-redundant`. Поэтому `pages.layout`
> сужен (без признаков функционала или пустой маршрут без членов), а пара
> «не обёртка, причина» для кандидатов — вопрос следующей задачи (S28/S29
> или правка отбора S18), не S24.

**Отклонения реализации (S24, 08.10).** Модель сверх спецификации:
`Finding.title` и `clusters_total` (усечённые кластеры иначе читались бы
полными), `Coverage.value`; `id` решения — `файл::ключ::значение`
(`decision_id`), файл — та же подпись, что у `setup explain`. При `--baseline`
находка, закрытая с прошлого прогона, остаётся строкой с `count: 0`
и `previous` — иначе правка, которая её закрыла, не видна разницей;
находка, которой в базе не было, — `previous: 0`, решение — `previous: null`.
`--limit` — кластеров на срез и примеров вне области; охват не усекается
(база следующего прогона). Разбивка по файлам (п. 4) — `CoverageDetail.files`
у `decision_coverage(ctx)`, тот же подсчёт, что у отчёта; совпадение счёта
с `setup explain .` держит тест. Новый код `link.duplicate_endpoints`
(дефект: единственный дефект `web link`). `parse.errors` закрывается
`exclude` в `docpipe.yaml`, а не отсевом `path_glob`: у файла без объявлений
символов нет, и правило отсева его не уберёт. `owners.*` считаются по узлам
со своим документом (поглощённые страницей — нет), `owners.not_configured` —
число таких узлов. `link.module_without_rewrite`: число находки — модулей,
кластера — мест вызова в модуле. `pages.route_unresolved` — страница с якорем,
у которой часть записей не собрана и нет записи `pages.yaml`; целиком
несобранная — только `pages.unanchorable`. Отказ прогона шага 1 или `web`
(`InputError`) — `load.errors`, их решения в охват не идут. Контекст получил
`discovered` и `projects` (`recon.collect_projects`); `setup explain` —
публичные `covering_root` и `shown_root`. Замер (`--no-cache`): squidex 8,7 с,
abp 5,1 с; JSON 42 и 54 КБ при `--limit 20` — больше бюджета ответа S27.
Попутно: на squidex 21, на abp 13 пар узлов фронта претендуют на один
`doc_path` — план шага 2 фронта не собирается (`docs.unavailable`): у шага
`web` нет разведения коллизий `_assign_doc_paths` шага 1.

**Критерии приёмки**
- на `SampleSolution` с умолчаниями — `dotnet.undecided` = 1 (`Program`),
  кластер по модулю;
- с правилом отсева на `Program` (с причиной) — находки нет, у правила
  охват 1; `--fail-on-unexplained` — код 0;
- на `SeamWorkspace` без правил шва — `link.calls_without_endpoint`,
  `link.calls_unresolved`, `link.module_without_rewrite`; после правил
  S19–S20 и `url_rewrite` из подсказки S21 — эти находки исчезают;
- `--out A`, затем прогон с `--baseline A` — у каждой находки и охвата
  `previous` равен `count`;
- два прогона — байт в байт; без `ownership` — `owners.not_configured`;
- без скелетов — `docs.unavailable`, команда не падает.

**Проверка**
```bash
uv run pytest tests/test_setup_status.py tests/test_setup_map.py -q
uv run docpipe setup status --root tests/fixtures/SampleSolution --format json
```

**Справочники:** `docs/setup.md` (коды, модель, сравнение прогонов),
`docs/setup-map.md`.

---

## S24b — две дыры, без которых Р-6 на реальном коде недостижимо ✅

**Добавлена 08.10 по итогам S24.** На squidex и abp `setup status` нашёл две находки,
которые никаким решением не закрываются: одна — пробел формата, другая — дефект кода.
Пока они есть, «в области нет находки без решения» недостижимо на любом реальном фронте.

**Изменить:** `docpipe/web/tree.py`, `docpipe/config.py`, `docpipe/setup/candidates.py`,
`docpipe/setup/status.py`, `docpipe/setup/explain.py`, `deploy/generic-docspipe/docpipe.yaml`,
`docs/setup-map.md`, `docs/web.md`, `docs/configuration.md`
**Создать:** `tests/test_web_doc_paths.py`, `tests/test_not_wrappers.py`

**Спецификация**

1. **Коллизии `doc_path` у шага `web`.** У шага 1 их разводит `_assign_doc_paths`
   (`tree.py`): суффикс по хэшу ключа, а не по порядку вставки (`CLAUDE.md`, «Жёсткие
   правила»). У шага `web` такого нет: на squidex 21, на abp 13 пар узлов фронта
   претендуют на один путь, и план шага 2 фронта не собирается вовсе (`docs.unavailable`).
   Шаг `web` разводит коллизии **той же функцией**, что шаг 1. Своя копия правила
   разойдётся с ним на первой правке.
2. **«Это не обёртка» — решением с причиной.** `web.not_wrappers: list[NotWrapper]`,
   `NotWrapper(receiver: str, method: str = "", method_regex: str = "", reason: str)`.
   Ровно одно из `method`/`method_regex`, `reason` обязательна и непуста — это решение
   «не документируем как вызов». Кандидат S18, совпавший с записью, не идёт в
   `link.calls_invisible` и в `http-wrappers`. В отчёте кандидатов — счётчик
   `declared_not_wrappers`. Запись входит в охват решений (S24) и в `decisions` у
   `setup explain` (S23). Обёртка и не-обёртка на одну пару «получатель + метод» —
   отказ загрузки.

> **Ловушка. Разведение коллизий меняет `doc_path` только у узлов, которые спорили.**
> Документов у них не было: план шага 2 фронта не собирался. Поэтому переездов
> на уже развёрнутых репозиториях это не вызовет — но тест обязан это держать: узел
> без коллизии сохраняет прежний путь байт в байт.

**Критерии приёмки**
- два узла фронта с одним slug в одном модуле и виде получают разные пути, детерминированно,
  а узел без коллизии — прежний путь;
- на squidex и abp (`--no-cache`) `setup status` больше не даёт `docs.unavailable` от коллизий;
- `web.not_wrappers` с записью на `window.open` убирает кандидата из `link.calls_invisible`
  и из `http-wrappers`; пустой `reason` — отказ; пересечение с `http_wrappers` — отказ;
- новые ключи — в карте (`tests/test_setup_map.py`) и в нейтральном наборе
  (`tests/test_deploy_bundle.py`).

**Проверка**
```bash
uv run pytest tests/test_web_doc_paths.py tests/test_not_wrappers.py tests/test_setup_status.py -q
```

Отклонения при реализации (08.10): функция не вынесена в новый модуль —
`_assign_doc_paths` стала публичной `tree.assign_doc_paths` (шаг `web` уже
импортировал оттуда `doc_path_for`), тест держит `web.tree.assign_doc_paths
is tree.assign_doc_paths`. Применяется в `web.tree.run` ко всем узлам вместе
с разделами (`feature:`), до `absorb`: раздел и класс вида `feature` одного
модуля спорят за тот же файл. Золотые файлы и числа фикстур не изменились —
коллизий в фикстурах нет; полный прогон — 2910 тестов зелёные до новых.
Сверх «Изменить» — `docpipe/web/calls.py`: `name_matches`/`wrapper_matches`
принимают и `NotWrapper` (одно сравнение на оба ключа; имя сравнивает
`config.method_matches`, форму имени проверяет общий `_check_method_name`),
`not_wrapper_for` — первая запись в порядке файла (как у секции `link`:
исход один, спор только о причине; точный повтор — отказ загрузки),
`NotWrapperConflict` и `build_calls(..., not_wrappers=)` — только сверка,
на ключи вызовов запись не влияет (манифест с ней и без неё — байт в байт).
`declared_not_wrappers` — число **групп**; `HttpWrapperCandidates` — 1.1
(правило 7), это единственная правка существующего теста
(`test_unresolved_calls.py`). «Что запись сняла» — одна функция
`candidates.declined_calls` у отчёта кандидатов, охвата и `setup explain`
(охват — вызовов, не групп; запись на построитель или на вызов без
аргумента-адреса снимает ноль: такие не кандидаты). `decision_home`
у `link.calls_invisible` — «`web.http_wrappers` или `web.not_wrappers`
с причиной»; в `without_reason` ключа нет — причина обязательна.
Замер (`--no-cache`, настройки S24): дефектов squidex 21 → 0, abp 13 → 0,
находок без решения 3250 и 2081 — прежние; с `web.not_wrappers` на всех
кандидатов, названных S24 «не HTTP» (по 7 записей: 9 и 14 вызовов),
`link.calls_invisible` 9 → 0 и 14 → 0, находок 3250 → 3241, 2081 → 2067.

> **Ловушка (найдена при реализации). Разведённый путь — не названный.**
> «21 пара» squidex — это 21 **путь** на 104 узла: 38 интерфейсов `State`
> и 27 `Snapshot` (по одному в каждом `*.state.ts`, вид `dto`) спорили
> за `state.md` и `snapshot.md`. После разведения план собирается, но файлы
> `state-1a2b3c4d.md` читателю ничего не говорят. Это решение классификации
> (отсев с причиной), находки о нём `setup status` не даёт — символы решены.
> На abp — 13 путей на 26 узлов, все пары.

> **Ловушка (найдена при реализации). Две регулярки загрузка не сравнит.**
> Пересекаются ли `get.*` (не обёртка) и `^(get|post)Versioned$` (обёртка),
> без вызова не сказать. Загрузка ловит точное против точного, регулярку
> против точного имени и одинаковую регулярку; остальное — прогон
> (`NotWrapperConflict` в `build_calls`, код 2), как пересечение регулярок
> двух обёрток. Класс — подкласс `WrapperConflict`: три места, ловящие отказ
> прогона (`web scan`, `symbols --lang ts`, `SetupContext.web`), иначе
> назвали бы его «ошибкой в ручном составе страниц».

> **Ловушка (найдена при реализации). Имя переменной цикла уже занято.**
> В `_cover_calls` (`status.py`) и `_wrapper_decisions` (`explain.py`) ниже
> по функции `rule` и `call` связаны с `RegistryCallConfig`, `WebCall`
> и `HttpWrapper`; цикл `for rule in not_wrappers` дал mypy семь ошибок
> «несовместимый тип» не в той строке. Та же ловушка, что моржовый оператор
> S19: новое имя (`refusal`, `candidate`).

---

## S25 — ревью: новое и прежние решения (`setup review`)

**Цель:** вход «ревью» (Р-4): после изменений в репозитории ответить, что
появилось в области и **как прежние решения обошлись с новым кодом**.
База — последний коммит каталога настройки (П-1): коммит и есть принятие.

**Создать:** `docpipe/setup/review.py`, `tests/test_setup_review.py`
**Изменить:** `docpipe/cli.py`

**Спецификация**

`build_review(ctx, *, since: str | None) -> Review`.

1. **База.** `since` по умолчанию — последний коммит, затронувший каталог
   `docpipe.yaml`: `git log -1 --format=%H -- <каталог>`; явно — `--since REV`.
2. **Новый код** — `git diff --name-only <since>` (коммиты после базы и
   рабочее дерево) плюс неотслеживаемые (`git ls-files --others
   --exclude-standard`), только внутри `roots`/`web.roots`, `sorted`.
3. **Разделы отчёта:**
   - `applied` — какие решения применились к новому коду и сколько: для
     каждого решения с ненулевым охватом в новых файлах — `{id, file, key,
     reason, count}` (разбивка по файлам — S24, п. 4). Сюда попадает
     правило отсева, которое молча забрало тридцать новых типов, — ровно
     то, ради чего ревью и нужно;
   - `new_findings` — находки `setup status`, у которых есть примеры в новых
     файлах, с этими примерами;
   - `dead_decisions` — решения с охватом 0 по всему репозиторию: правило
     отсева, не отсеявшее ничего; `url_rewrite` модуля, которого нет; обёртка
     без вызовов; правило `link` без совпадений; правило владения без побед;
     шаблон `not_enrolled` без модулей;
   - `config_dirty` — каталог настройки изменён и не закоммичен (`git status
     --porcelain -- <каталог>`): ревью сравнивает код, а несохранённые
     решения — ещё не решения.
4. Команда `docpipe setup review [--since REV] --root --config --format`.
   Код 0; с `--fail-on-changes` — 1 при непустых `new_findings`
   или `dead_decisions`.
5. **Без истории.** Не git-репозиторий — код 2: «ревью опирается на git».
   Каталог настройки ни разу не коммитился — отчёт равен `setup status`
   с пометкой «настройка ещё не закоммичена». База не найдена (неглубокий
   клон в CI) — код 2 с подсказкой `--since`.

> **Ловушка. «Правило стало съедать больше» по числу не поймать надёжно.**
> Число отсеянных растёт и тогда, когда правило работает как задумано
> (новые `*Dto` под правилом «dto не документируем»). Человеку нужен не
> коэффициент, а список: какие решения применились к коду, которого он
> ещё не видел, — его он и проверяет.

> **Ловушка. Подмодули git.** `git diff` родителя видит у подмодуля только
> смену ссылки, файлы внутри — нет. Если область лежит в подмодуле, новый код
> там ревью не увидит — сказать это строкой отчёта, а на АС CF проверить
> раскладку `sbt.cms.cashflow.subrepo` (S32).

> **Ловушка. Переименование — это новый файл.** Символы переименованного
> файла попадут в «новый код», и решения по ним покажутся снова. Это
> намеренно: путь входит в предикаты (`path_glob`), и решение могло
> измениться вместе с путём.

**Критерии приёмки** (`SeamWorkspace`, git-репозиторий в `tmp_path`)
- коммит настройки → новый контроллер и новый сервис фронта → `applied`
  называет правила, решившие их символы, `new_findings` — эндпоинты нового
  контроллера без вызывающего;
- новое правило отсева, забравшее новый код, — в `applied` с причиной;
- удалить модуль, на который есть `url_rewrite`, — в `dead_decisions`;
- незакоммиченная правка настройки — `config_dirty`;
- каталог настройки без коммитов — отчёт равен `setup status` с пометкой.

**Проверка**
```bash
uv run pytest tests/test_setup_review.py -q
```

---

## S26 — общий протокол MCP; изоляция ошибок ✅

**Цель:** исключение в инструменте отвечает ошибкой, а не убивает сервер;
второй сервер (S27) не копирует протокол.

**Создать:** `docpipe/mcp.py`, `tests/test_mcp_protocol.py`
**Изменить:** `docpipe/graph/mcp.py`

**Спецификация**

```python
class ToolSet(Protocol):
    server_name: str
    instructions: str
    def tools(self) -> list[dict[str, Any]]: ...
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
def handle(toolset: ToolSet, request: Any) -> dict[str, Any] | None: ...
def serve(toolset: ToolSet, stream_in: TextIO | None = None, stream_out: TextIO | None = None) -> None: ...
```

1. Переезжают из `graph/mcp.py`: `handle` (`215-249`), `serve` (`252-269`),
   `PROTOCOL`. Граф — `GraphTools` поверх нынешних `Server`/`tools()`/`call()`;
   имена `tools`, `call`, `Server`, `handle`, `serve` остаются доступны
   из `docpipe.graph.mcp` — на них опираются тесты.
2. **Изоляция.** `tools/call`: исключение из `toolset.call` — ответ
   `isError: true` с текстом «внутренняя ошибка инструмента {name}:
   {тип}: {сообщение}» и трассировка в stderr; цикл продолжается.
   Запрос не словарь или `params` не словарь — JSON-RPC `-32600`.
   Нечитаемая строка — по-прежнему без ответа (тест
   `test_stdio_loop_answers_line_by_line` ждёт ровно один ответ) и строка в stderr.
3. **Версия протокола.** `initialize`: версия клиента из
   `SUPPORTED_PROTOCOLS` — она же в ответе; иначе — последняя поддерживаемая.
   Сейчас `2024-11-05` захардкожена (`mcp.py:29`).

> **Ловушка. Сейчас любое исключение роняет сервер** — проверено 06.10:
> `limit: "abc"` (`ValueError`), `params: null` и запрос-список
> (`AttributeError`) выходят из `handle`, и агент теряет все инструменты
> до перезапуска сессии.

> **Ловушка. `test_tools_match_the_forms_one_to_one`** (`test_graph_mcp.py:77-86`)
> держит «инструменты графа = публичные функции `graph/api.py`» через AST.
> Инструменты настройки в этот модуль не кладутся — у них свой сервер и свой
> инвариант (S27).

> **Ловушка. stdout — канал протокола** (найдено при реализации, 07.10).
> `print` внутри инструмента — а S27 зовёт те же функции, что CLI, — разорвал
> бы поток JSON-RPC посреди ответа, и клиент прочёл бы битую строку. Вызов
> инструмента идёт под `redirect_stdout(sys.stderr)`. Подпроцессы это
> не перехватывает: им — `capture_output`, как у `why` и `recon`.

Как сделано (07.10): `ToolSet.server_name` и `instructions` — свойства только
для чтения, а не атрибуты: под атрибут протокола не подошла бы реализация
с `Final`. Текст внутренней ошибки — в той же форме `{"error": …}`, что у
ошибок, которые инструмент возвращает сам. Сверх спецификации: у `tools/call`
`name` не строка или `arguments` не объект — `-32602`; несериализуемый ответ
инструмента — `isError`, несериализуемое описание инструментов — `-32603`;
уведомлению с неизвестным методом не отвечают (JSON-RPC); `serverInfo.version` —
версия пакета вместо литерала `"1"`. Тест держит, что словаря JSON-RPC нет
нигде в `docpipe/**`, кроме `docpipe/mcp.py`, — у S27 своего протокола
быть не может.

**Критерии приёмки**
- инструмент, бросающий исключение, даёт `isError` и следующий запрос
  в том же цикле обслуживается;
- `params: null` и запрос-список — `-32600`;
- `initialize` с `2025-06-18` отвечает `2025-06-18`, с `1999-01-01` — последней
  поддерживаемой;
- все тесты `test_graph_mcp.py` проходят без правки.

**Проверка**
```bash
uv run pytest tests/test_mcp_protocol.py tests/test_graph_mcp.py -q
```

**Справочники:** `docs/graph-implementation-plan.md` G12 (протокол),
`module-review.md`: `mcp.py` — развивается, потребители `graph serve`, `setup serve`.

---

## S27 — `docpipe setup serve`

**Цель:** агент контура получает инструменты настройки по MCP — те же функции,
что у CLI, без индекса графа и до любой сборки.

**Создать:** `docpipe/setup/server.py`, `tests/test_setup_server.py`
**Изменить:** `docpipe/cli.py`

**Спецификация**

`SetupTools(root: Path, config: Path | None)` реализует `ToolSet` (S26),
`server_name = "docpipe-setup"`.

| Инструмент | Аргументы | Функция | CLI-двойник |
|---|---|---|---|
| `setup_config_check` | — | `check_config` (S04) | `config check` |
| `setup_recon` | `top` | `recon.build_report`, сводка блоков 1, 4, 5, 6 + `projects` (S10) | `recon` |
| `setup_status` | `limit`, `baseline: previous \| none` | `build_status` (S24) | `setup status` |
| `setup_review` | `since` | `build_review` (S25) | `setup review` |
| `setup_explain` | `path`, `limit` | `explain_path` (S23) | `setup explain` |
| `setup_stats` | `lang`, `top` | `build_stats_report` (S05) | `scan --stats`, `web scan --stats` |
| `setup_symbols` | `lang`, `state`, `module`, `namespace`, `path`, `rule`, `limit`, `offset` | `explain.select` (S07) | `symbols` |
| `setup_candidates` | `kind`, `limit`, `offset` | `setup.candidates` (S11–S14, S18) | `setup candidates` |
| `setup_link` | `category`, `by`, `limit`, `offset` | `link_clusters` (S21) | `setup link` |
| `setup_pages` | `note`, `limit`, `offset` | `web.pages.build_report` | `web pages` |
| `setup_docs` | `status`, `limit`, `offset` | `docs status` из `step2` (S06) | `docs status` |
| `setup_docs_explain` | `path` | S06 | `docs explain` |

1. **Каждый вызов собирает контекст заново** (`setup/context.py`): агент
   правит настройку между вызовами, и ответ обязан это видеть. Помнится одно —
   прошлый `setup_status` для `baseline: previous`. Кэш разбора делает
   повторный прогон дешёвым.
2. **Размер.** `fit(answer, max_chars=20_000)`: если JSON длиннее, списки
   урезаются детерминированно, в ответ — `truncated: true` и `next_offset`.
3. **Конфигурации может не быть** (онбординг пустого репозитория):
   инструменты работают на умолчаниях, `setup_config_check` говорит
   `config_missing`. `graph serve` в этом случае падает с кодом 2
   (`cli.py:2371-2376`) — у `setup serve` так нельзя.
4. **`instructions`** — коротко: что за сервер; «после правки файла
   настройки позови инструмент ещё раз и сравни»; сервер ничего не пишет;
   списки — страницами.
5. Команда `docpipe setup serve --config --root`.

> **Ловушка. Инструмент без CLI-двойника — вторая реализация.** Тест держит
> соответствие: таблица `CLI_TWIN` в `server.py`, каждый инструмент есть
> в ней, каждый двойник — зарегистрированная команда приложения typer.

> **Ловушка. Агент контура обрезает вывод на 25 000 символов** — ответ
> на 30 000 будет прочитан как полный.

**Критерии приёмки**
- синтетический контекст на 5 000 символов: ответ `setup_symbols` не длиннее
  20 000 символов, есть `next_offset`, вторая страница продолжает первую;
- цикл stdio на `SampleSolution`: `setup_status` отдаёт `SetupStatus`;
  правка правила в `tmp_path`-копии между вызовами меняет ответ;
- без `docpipe.yaml` сервер стартует, `setup_config_check` — `config_missing`;
- инвариант `CLI_TWIN` проходит; исключение внутри инструмента — `isError` (S26).

**Проверка**
```bash
uv run pytest tests/test_setup_server.py tests/test_mcp_protocol.py -q
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run docpipe setup serve --root tests/fixtures/SampleSolution
```

**Справочники:** `docs/setup.md` (инструменты), `deploy/README.md` (агент).
`module-review.md`: `setup/server.py`.

---
## S28 — протокол интервью: каталог вопросов ✅

**Цель:** каждый вопрос человеку строится из находки, имеет варианты, адрес
ответа в файле настройки и место для причины. Вопрос без адреса — анкета.

**Создать:** `docs/setup-interview.md`, `tests/test_setup_interview.py`

**Спецификация**

1. **Кто решает.** Граница проходит по смыслу решения, а не по удобству:

   | Решение | Кто | Как |
   |---|---|---|
   | позитивное техническое правило, доказуемое кодом: вид для группы, `url_rewrite` по прокси и интерцептору, `http_wrappers`, `url_builders`, `registry_calls`, `di_methods`, `dispatch_interfaces` | агент | пишет правило (Р-1), перезапускает инструмент, показывает разницу и доказательство (`файл:строка`); человек видит `git diff` |
   | любое «не документируем» / «не берём»: `not_enrolled`, `exclude`, правила отсева, `pages.yaml remove`, `link.*` с `document: false`, `unresolvable` | человек | агент спрашивает с вариантами; **причину называет человек**, агент её не дописывает (`purpose.md`, граница детерминизма) |
   | имена, которые становятся ключами: домены, команды, имя раздела (`features[].name` — ключ якоря) | человек | агент предлагает варианты из находки |
   | область, остановка, принятие — коммит настройки (П-1) | человек | — |

2. **Форма вопроса** (для инструмента вопроса агента): заголовок до 12
   символов; текст — находка с числом и примером, затем «от ответа
   зависит…»; 2–4 варианта, у каждого — конкретная правка (файл, ключ,
   значение); причина — свободным ответом. Образец — шаг 5 скилла `recon`.
3. **Каталог** — раздел `### <код>` на каждый код `FINDING_CODES` (S24):
   какой инструмент даёт материал, шаблон вопроса, варианты и правка
   по каждому, нужна ли причина, пример на фикстуре.
4. **Правила потока:** один вопрос — на кластер, не на символ; порядок —
   фазы по зависимостям (аналитика §4), внутри фазы — крупнейший кластер;
   не больше четырёх вопросов за один вызов инструмента вопроса; не
   спрашивать того, на что уже ответили `setup candidates` или
   `setup explain`; решённое не всплывает по построению (решение есть —
   находки нет); ревью спрашивает только о `new_findings`,
   `coverage_changes`, `dead_decisions` (S25).
5. **Остановка:** по слову человека агент печатает сводку `setup status`;
   если необъяснённое осталось — список, чтобы остановка была осознанной;
   предлагает закоммитить настройку: коммит — это принятие, и следующее
   ревью (S25) считает новый код от него.

> **Ловушка. Агент, дописывающий причину сам, ломает границу детерминизма**
> тихо: отсев с правдоподобной причиной неотличим от решения человека.
> Протокол запрещает это явно, а скилл (S29) повторяет.

**Отклонения реализации (S28, 08.10).** Ревью в п. 4 спрашивает о
`new_findings`, `applied` и `dead_decisions`: `coverage_changes` — имя из
редакции до П-1, в S25 его заменил `applied`. Разметка каталога строгая
(строки `- правка: `файл` → `ключ`, …`, `- команда:`, `- вне настройки:`,
`- причина:` с тремя значениями), и тест проверяет сверх критериев: порядок
разделов — порядок кодов; у решения 2–4 варианта с адресом, у дефекта —
«Починка» без вопроса; запись «не берём» не правится без вопроса, а причина
в ней — слова человека; ключи `decision_home` кода — среди правок раздела;
команды `docpipe …` и виды `setup candidates` зарегистрированы; пример
ссылается на существующие фикстуру и тест. Ключ `web.not_wrappers` (S24b)
пропускается, пока его нет в карте (`PENDING` в тесте). Пробелы формата —
ответ, которому негде лечь, — найдены ещё у трёх находок, кроме
`link.calls_invisible` и `pages.layout` (S24): `link.registry_unresolved`
(различитель из данных, `listInnerName=${type}`), `link.almost` («та же
точка, параметр передан иначе»), `pages.route_unresolved` («вторая запись —
та же страница»). Находка в них остаётся, агент говорит это вслух. У
`di_methods`, `dispatch_interfaces`, `features` и `domains` кода находки нет —
вопросы по ним в отдельном разделе протокола, по кандидатам.

**Критерии приёмки:** тест — у каждого кода `FINDING_CODES` есть раздел;
каждый ключ, на который указывает вариант, есть в `docs/setup-map.md`
(список ключей — из помощника теста S09).

**Проверка**
```bash
uv run pytest tests/test_setup_interview.py tests/test_setup_map.py -q
```

---

## S29 — скилл `setup`: три входа, фазы, тесты

**Цель:** сценарий для gigacode от «репозиторий вижу впервые» до «в области
нет находки без решения», с возвратом для расширения и ревью.

**Создать:** `.gigacode/skills/setup/SKILL.md`, `.gigacode/skills/setup/phases/*.md`,
ссылку `.claude/skills/setup -> ../../.gigacode/skills/setup`,
`tests/test_skills.py`, `tests/test_setup_skill.py`
**Изменить:** `tests/test_recon_skill.py` (общие проверки уходят в `test_skills.py`)

**Спецификация**

1. **`SKILL.md`** (тело — не больше 250 строк: длинное тело агент обрезает):
   - front matter: `name: setup`, `description` — когда звать: настроить
     `docpipe` на репозиторий, покрыть документацией ещё кусок кода,
     проверить настройку после изменений;
   - граница: пишешь только файлы настройки, никогда — код продукта;
     «не документируем» — только с причиной от человека; не ходишь в сеть,
     не исполняешь код репозитория;
   - инструменты: сервер `docpipe-setup` (S27) и CLI-двойники на случай,
     если сервер недоступен;
   - цикл: находка → чтение кода → правило → инструмент ещё раз → разница →
     вопрос, если решение человека;
   - выбор входа: настройки нет или каталог настройки ни разу не коммитился —
     онбординг; человек назвал код — расширение; после последнего коммита
     каталога настройки менялся код — ревью;
   - остановка (S28, п. 5).
2. **Фазы** — `phases/`: `00-scope.md` (`config check`, разведка, `roots`,
   `enrolled`/`not_enrolled`, `web.roots`, вне области), `10-dotnet.md`
   (`di_methods`, `dispatch_interfaces`, группы `undecided`, правила и `unless`),
   `20-web.md` (секция `web`, `require_public: false`), `30-link.md` (кластеры,
   подсказка префикса, обёртки и построители, решения о концах без пары),
   `40-pages.md`, `50-docs.md` (раскладка окончательно до `materialize`,
   `docs_scan_exclude`, `docs status`), `60-ownership.md`, `80-expand.md`,
   `90-review.md`. Каждая — цель, инструменты, контрольная точка (результат
   инструмента, а не мнение), ловушки фазы из `CLAUDE.md`, правила правки
   её файлов. Не больше 200 строк.
3. **Правила правки файлов** (в `SKILL.md`): правка текстовая, комментарии
   сохраняются; элемент списка не «выключается» комментарием — это
   превращает список в «всё» или в пустой (S02), для отказа есть
   `not_enrolled` и отсев с причиной; после каждой правки — `setup_config_check`
   и инструмент своей фазы.
4. **Примеры YAML** размечены первой строкой `# file: docpipe.yaml`,
   `# file: rules.yaml#dotnet`, `# file: rules.yaml#web`, `# file: pages.yaml`,
   `# file: ownership.yaml` — по разметке тест знает, чем их проверять.
5. **Команды без `$(…)` и обратных кавычек**: оболочка gigacode их
   отвергает (сторонний отчёт, не проверено на контуре — S32).

**Тесты.** `tests/test_skills.py` — по всем `.gigacode/skills/*/SKILL.md`:
front matter, `name` равен каталогу, длина описания от 200 до 1024,
ссылка `.claude/skills/<имя>` ведёт на тот же каталог, файлы не под
`.gitignore`. `tests/test_setup_skill.py`:
- каждая упомянутая команда `docpipe …` зарегистрирована в приложении typer;
- каждый упомянутый инструмент `setup_*` есть в `SetupTools.tools()`;
- каждый упомянутый код находки есть в `FINDING_CODES`;
- каждый пример YAML проходит свой загрузчик (`load_config`, `load_ruleset`
  с секцией, `load_overrides`, загрузчик владения) из файла в `tmp_path`;
- каждый файл фазы упомянут в `SKILL.md`, каждый упомянутый существует;
- в блоках кода нет `$(` и обратных кавычек; длины тел в пределах.

> **Ловушка. Скилл, который ссылается на несуществующую команду, проходит
> любое ревью глазами** и ломается у агента на первом шаге. Поэтому
> имена команд, инструментов и кодов проверяет тест, а не читатель.

> **Ловушка. Проверочный прогон на машине разработки идёт на более сильной
> модели**, чем агент контура (уровень GLM-5.3 Flash / Qwen3.8-Flash-Next-262k):
> место, где модель «догадалась», скилл обязан проговорить.

**Критерии приёмки:** оба теста проходят; `test_recon_skill.py` сохраняет
содержательные проверки `recon`, общие — в `test_skills.py`.

**Проверка**
```bash
uv run pytest tests/test_skills.py tests/test_setup_skill.py tests/test_recon_skill.py -q
```

**Справочники:** `README.md` (настройка — скилл `setup`), `CLAUDE.md`
(команды и документы), `deploy/README.md`.

---

## S30 — нейтральный набор, `--bundle`, второй сервер, слияние настроек агента ✅

**Цель:** установка на любой репозиторий кладёт нейтральную настройку, агент
получает оба сервера, обновление не теряет второй сервер в файле `.new`.

**Создать:** `deploy/generic-docspipe/` (`docpipe.yaml`, `rules.yaml`,
`ownership.yaml`, `pages.yaml`, `registries.yaml`, `arch-registry.yaml`, `README.md`)
**Изменить:** `deploy/install.sh`, `deploy/README.md`, `deploy/OFFLINE.md`,
`CASHFLOW.md`, `tests/test_deploy_bundle.py`

**Спецификация**

1. **Набор.** `install.sh --bundle generic|cashflow`, по умолчанию `generic`;
   `BUNDLE_SRC` (`install.sh:27`) — по флагу. Нейтральный `docpipe.yaml` —
   все ключи с комментарием-ссылкой на `docs/setup-map.md`, плейсхолдеры
   `@CONFIG_DIR@`, `@CACHE_DIR@`, `@ENGINE@`; `rules.yaml` — копия `rules/rules.yaml`;
   остальные — пустые заготовки; `README.md` — короткий: «настройку ведёт
   скилл `setup`», ручной путь — ссылкой.
2. **Сторож:** в `deploy/generic-docspipe/` нет `sbt`, `Sbt.`, `cashflow`,
   `Cashflow`, `АС CF` (тест).
3. **Два сервера.** В `.gigacode/settings.json` клона — `mcpServers.docpipe`
   (граф, как в S01) и `mcpServers.docpipe-setup`
   (`setup serve --config <repo>/<config-dir>/docpipe.yaml --root <repo>`,
   тот же `command` и `cwd`).
4. **Слияние вместо `.new`.** Существующий файл читается интерпретатором
   окружения инструмента; ключи `mcpServers.docpipe` и
   `mcpServers.docpipe-setup` ставятся, всё остальное сохраняется; файл
   пишется, только если изменился; устаревший `.new` удаляется. Нечитаемый
   JSON — как сейчас: файл не трогается, рядом `.new`, строка в stderr.
5. **Кэш движка — свой у репозитория.** Умолчание `--cache-dir`
   (`install.sh:111-117`) — общий `$WORK/.docpipe/cache`, а кэш движка
   обязан быть у проекта своим (`docpipe.example.yaml:265-268`,
   `engine.py:124-126`): умолчание — `…/cache/<имя каталога репозитория>`.
6. `--no-tool`: в `OFFLINE.md` — ручная запись обоих серверов.
7. `CASHFLOW.md`: установка на АС CF — `--bundle cashflow`.

> **Ловушка. `keep_configured` сравнивает файл целиком** (`install.sh:141-154`):
> на машине, где запись `docpipe` уже лежит, вторая запись ушла бы в `.new`,
> и агент молча не получил бы инструменты настройки.

> **Ловушка. `sed "s|…|$X|g"` подстановки плейсхолдеров** (`install.sh:168-170`):
> `&` или `|` в пути исказят строку молча. Теста на особые символы в пути
> нет — добавить на `&`, `|` и пробел; подстановку экранировать до `sed`.

> **Ловушка (найдена при реализации). Экранировать — в переменную, форму
> `-e "s|@X@|…|g"` не трогать.** Карта настройки находит плейсхолдеры
> установщика регулярным выражением по этой форме
> (`tests/setup_map_support.py:_PLACEHOLDER`): `sed` через функцию прямо
> в выражении оставил бы раздел карты без плейсхолдеров, и тест полноты
> карты молча перестал бы их проверять. Слоёв экранирования два: YAML
> (`\` и `"` — значения стоят в двойных кавычках) и `sed` (`\`, `&`, `|`).
> И новый флаг (`--bundle`) обязан получить строку в `docs/setup-map.md` —
> это ловит `test_every_code_key_is_in_map`.

> **Ловушка (найдена при реализации). «Копия эталона» и сторож спорят.**
> В `rules/rules.yaml` стояло «АС CF» в комментарии-мотивировке (правило
> `web.module-constant`): побайтовая копия не проходила сторож. Поправлен
> комментарий эталона, а не копия и не сторож — иначе копия разошлась бы
> с эталоном при первой же правке.

> **Ловушка (найдена при реализации). Пустую заготовку `registries.yaml`
> нельзя назвать в `docpipe.yaml`.** Загрузчик отвергает описание без
> реестров («непустым списком», П-4), и названный ключ давал бы ошибку
> бизнес-ссылок на каждом прогоне шага 2, а `anchors` и `business` —
> отказ про файл, которого никто не писал. Ключ в нейтральном наборе
> закомментирован, заготовка лежит; тест держит оба факта.

> **Ловушка (найдена при реализации). Интерпретатор окружения — по пути
> `<uv tool dir>/docpipe/bin/python`, не из шебанга запускалки.** При длинном
> пути uv пишет в запускалку `#!/bin/sh` с `exec`-трамплином (проверено
> на uv 0.11.32), и разбор шебанга дал бы `/bin/sh`. Тест подставляет
> интерпретатор тем же путём — заглушка `uv tool dir` и ссылка на
> `sys.executable`, — поэтому лазейки «для тестов» в установщике нет,
> а `python3` в PATH тест подменяет падающим.

> **Ловушка (найдена при реализации). Настройки агента бывают JSONC.**
> Семейство gemini-cli, от которого идёт gigacode, допускает комментарии
> в `settings.json`, а `json.load` — нет. Такой файл — «нечитаемый»: не
> тронут, рядом `.new`, строка в stderr называет причину.

> **Ловушка (найдена при реализации). Обновление каталога АС CF без
> `--bundle`.** Прежняя команда из `CASHFLOW.md` молча взяла бы `generic`:
> yaml не затёрты, но рядом `.new` чужого набора и README заменён.
> Установщик говорит об этом в stderr, когда набор не назван, а лежащий
> `docpipe.yaml` с нейтральным не совпал; названный `--bundle` строку
> убирает. Умолчание кэша различает репозитории по имени каталога:
> одноимённые делят его по-прежнему.

**Критерии приёмки**
- тесты состава каталога настройки параметризованы по обоим наборам;
- сторож нейтрального набора проходит;
- установка поверх файла с чужим сервером: чужой сохранён, оба наших
  на месте, `.new` нет; повторная установка — «без изменений»;
- нечитаемый `settings.json` — не тронут, рядом `.new`;
- установка с `--bundle cashflow` даёт прежний набор байт в байт.

**Проверка**
```bash
uv run pytest tests/test_deploy_bundle.py -q
```

---

## S31 — прогон на открытых репозиториях

**Цель:** ассистент проходит онбординг, расширение и ревью на репозиториях,
которые воспроизводят известные ловушки; числа и найденные ловушки — в план.

**Изменить:** `docs/manual-run.md` (раздел «Настройка с ассистентом»),
журнал, этот план (ловушки).

**Спецификация**

Агент — Claude Code через `.claude/skills/setup` на машине разработки
(gigacode здесь нет — это оговорка к каждому числу). Репозитории копируются
в рабочий каталог, оригиналы в `~/docspipe-examples` не меняются; набор —
`generic`. Оператор играет человека; ответы записываются дословно.

| Репозиторий | Область | Что проверяет |
|---|---|---|
| squidex | `backend`, `frontend` | самодельные обёртки DI, `[Route]` на базе через константу, построитель адреса и обёртки HTTP, внешние адреса, гипермедиа |
| abp (подмножество из S15) | шесть модулей + `Volo.Abp.AspNetCore.Mvc`, `npm/ng-packs` | обёртка с объектом-запросом, коллизии имён, межсервисные эндпоинты (`integration-api`) |
| eshoponweb | решение во вложенном каталоге | корень и `roots` из разведки |
| semantic-kernel | `dotnet/src` | область внутри большого репозитория, `not_enrolled` |

Сценарий на каждом: онбординг до «в области нет находки без решения» или
до остановки оператором → расширение (оператор называет ещё один модуль) →
коммит настройки → синтетическая правка (новый контроллер и сервис фронта) → ревью.

Записать: находки по кодам в начале и в конце, число вопросов и коды,
по которым спрашивали, правки настройки (`git diff --stat`), вызовы
инструментов, время, наибольший ответ инструмента в символах, ловушки.

**Критерии приёмки**
- squidex: связано не меньше 85 вызовов фронта (оценка инвентаря — 87
  из ~98 со статическим путём); abp: не меньше 58 из 62;
- ни один шаг не потребовал правки кода `docpipe`, а если потребовал —
  это задача в бэклоге и строка в журнале;
- ни один вопрос не задан без находки; на ревью не повторён ни один
  решённый вопрос;
- наибольший ответ инструмента — не больше 20 000 символов.

---

## S32 — прогон на контуре

**Цель:** проверить на gigacode то, что машина разработки проверить
не может, и пройти настройку на АС CF.

**Спецификация**

1. Пять пунктов «уточнить на контуре» аналитики §3: `gigacode --version`;
   имя и ограничения инструмента вопроса (число вариантов, свободный ответ,
   работа внутри скилла); доверенные папки и системный `settings.json`;
   исполняется ли `docpipe` из каталога `uv tool` (Filesystem Guard);
   база относительного `cwd` сервера. Плюс: проходят ли команды скилла
   в оболочке gigacode.
2. Установка с `--bundle cashflow` поверх существующей настройки АС CF.
   Первый вход — ревью от последнего коммита каталога настройки АС CF:
   как решения существующей настройки обошлись с кодом, появившимся после
   неё; затем онбординг фронта `Sbt.CMS.Cashflow.ML` и шва с .NET.
   Проверить, не лежит ли область в подмодуле git (ловушка S25).
3. Числа — в `CASHFLOW.md` и журнал; расхождения с машиной разработки —
   в этот план.

**Блокирует:** доступ к контуру.

---

## Что намеренно не входит

| Что | Почему | Куда |
|---|---|---|
| Фаза Python | дерева документации для Python нет | PY01–PY17 (`feat/python-parser`); фаза добавится файлом в `phases/` |
| Общий формат правил связей для швов вне HTTP (грид, реестр модулей Python) | секция `link` (S20) — первый случай; общий формат проектируется вместе с Python | бэклог, «Правила связей между языками» |
| Конвенциональная маршрутизация шаблоном (`MapControllerRoute`), авто-API ABP (`ConventionalControllers.Create`), minimal API, SignalR, Razor Pages | на открытых репозиториях почти не встречается: abp — 2 настоящих конвенциональных контроллера в подмножестве, 13 `ConventionalControllers.Create` в шаблонах, 135 `PageModel`; squidex — 0; АС CF — один `MapControllerRoute` (`findings-cashflow-frontend.md:233`). В этом плане — находкой с числом | следующий план шва |
| Источник «описание API» (`generate-proxy.json` ABP, снимок OpenAPI squidex `frontend/generator/…/cache.json`) | даёт эндпоинты с полным путём и FQN контроллера — закрыл бы и авто-API, и конвенции, и унаследованные действия; самостоятельный формат | кандидат следующего плана, `findings-seam.md` |
| Действия, унаследованные от абстрактной базы контроллера | наследуется только маршрут (S17) | следующий план шва |
| Пометка эндпоинта в документе контроллера, связи фронт↔бэк в документах | проекция шва в документы | бэклог, «Фронт↔бэк в документах» |
| Адрес из ответа сервера (гипермедиа) | статически невыразим | решение `link.unresolvable` (S20) |
| Исключение отдельного эндпоинта из документа | единица документа — тип | — |
| Кандидаты в команды-владельцы сверх `recon` (блок 2) | владение — решение человека; материал есть | — |
| Субагент настройки | субагент отдаёт родителю один результат — интервью в нём не провести | — |
| Метод цели `Handle`, зашитый в графе (`graph/binding.py:486`) | правка графа; `handler_members` (S12) делает проблему видимой | бэклог графа |
| Разведка, запущенная по подкаталогу git-репозитория, теряет археологию | пути `git log` от корня git, а `known` — от `--root` | бэклог |
| Бизнес-слой, шаг 3, настройка графа сверх `engine_path` | вне цели ревизии 06.10 | `purpose.md` |

---

## Фикстуры

| Фикстура | Статус | Что в ней |
|---|---|---|
| `tests/fixtures/SeamWorkspace/` | новая (S15) | все формы шва фронт↔.NET; числа обновляют S16–S21 |
| `tests/fixtures/WebSections/` | новая (S14) | две страницы с общим разделом и приватный сервис |
| `SampleSolution` | не расширяется | на её числах стоят T04–T20 |
| `WebWorkspace` | не расширяется | на её числах стоят тесты страниц; положительный пример `registry_calls` (S13) уже есть |
| `WildSolution` | можно дополнять, но этот план обходится `tmp_path` | C#-примеры S11 и S12 — инлайном, как `tests/test_dotnet_facts.py:157-231` |

Тест фикстуры проверяет наличие конструкции, а не файла (`CLAUDE.md`,
«Инварианты фикстур»).

---

## Справочники: что появляется и что закрывается

| Документ | Задача |
|---|---|
| `docs/setup-map.md` — карта цепочек настройки | S09, колонка находок — S24 |
| `docs/setup.md` — команды `setup`, коды находок, ревью от коммита настройки, инструменты | S11, дополняют S12–S14, S18, S21, S23–S27 |
| `docs/setup-interview.md` — протокол и каталог вопросов | S28 |
| `docs/findings-seam.md` — инвентарь форм шва | S15 |
| `docs/module-review.md` — раздел «Настройка (`setup/`)», `step2.py`, `configcheck.py`, `mcp.py` | первая задача, создающая модуль |
| `CLAUDE.md` — таблица документов, команды `setup` | S09, S15, S28, S29 |
| `docs/backlog.md`, раздел «Настройка с ассистентом» | закрываются: карта (S09), MCP-инструменты (S27), интервью (S28), скилл (S29), повторная настройка (S25), критерий (S24), доставка (S01, S30); вне раздела — сверка схем (S06), `schema_version` манифеста (S16); «Поиск связей между языками» — частично (шов фронт↔.NET) |
