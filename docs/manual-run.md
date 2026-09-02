# Ручной прогон: проверить механизм своими руками

Сквозной прогон всего, что делает шаг «разведка и граф связей», на открытых
репозиториях и фикстурах. Каждая команда здесь была выполнена, и рядом стои́т
то, что она напечатала: **расхождение с этими числами — находка, а не шум.**

Когда это запускают:

* **после смены версии разборщика** — обновление бинаря без прогона запрещено
  планом (G18 п. 5), и вот он, прогон;
* на новой машине — проверить, что среда собрана правильно;
* перед тем как показывать результат человеку, который видит инструмент впервые;
* когда что-то «перестало находиться», а причина неясна: разделы 4 и 7 — про
  два способа потерять данные молча.

Числа привязаны к фикстурам репозитория и к состоянию открытых примеров
на 21.08.2026; при обновлении примеров они сдвинутся, и это законно —
проверять надо форму ответа и наличие объяснений, а не совпадение до цифры.

Всё, что создаётся, лежит в одном каталоге и удаляется одной командой:

```bash
export REPO=~/documentation_pipeline
export TRY=~/docpipe-try            # рабочий каталог прогона; удалить — rm -rf $TRY
mkdir -p $TRY && cd $REPO
```

---

## 0. Проверка среды: версии инструментов заморожены

```bash
uv sync
codebase-memory-mcp --version                       # 0.6.0 — другая версия недопустима
sha256sum ~/.local/bin/codebase-memory-mcp          # 3a3b6491…fb39e6c7
./venv-for-codeindex/bin/python -c "import importlib.metadata as m; print(m.version('code-index-mcp'))"
uv run ruff check . && uv run ruff format --check . && uv run mypy docpipe && uv run pytest -q
```

Ожидается `1973 passed`. Если `codebase-memory-mcp` показал не 0.6.0 —
дальше идти нельзя: числа будут от другого движка, и это вскроется
на боевом контуре, где чинить дороже всего.

---

## 1. Пять минут: весь конвейер на фикстурах

Здесь сразу видно всё разом: объявленный шов между языками, Python в общем
графе, точки входа, оценочный набор.

```bash
cat > $TRY/fixtures.yaml <<YAML
rules: $REPO/rules/rules.yaml
arch: $REPO/tests/fixtures/arch/sample-arch.yaml
graph:
  engine_path: ~/.local/bin/codebase-memory-mcp
  out: $TRY/fixtures-graph.db
  cache_dir: $TRY/engine-cache
YAML

uv run docpipe scan     --root tests/fixtures/WildSolution --config $TRY/fixtures.yaml --out $TRY/wild.json
uv run docpipe web scan --root tests/fixtures/WebWorkspace --config $TRY/fixtures.yaml --out $TRY/web.json
uv run docpipe graph build --root tests/fixtures/WildSolution --config $TRY/fixtures.yaml \
    --manifest $TRY/wild.json --web-manifest $TRY/web.json
```

Ожидается:

```
Модулей: 2, узлов: 7
Внимание: 1 файлов разобраны с ошибками…      ← так и должно быть: ConditionalModule.cs
Страниц: 6, из них маршрут не собран у 1
  узлов: 166, рёбер: 45
  корней: 18

Объявленные швы: 2, соединили обе стороны: 1
  seam:topic:prices-recalculated: зовущая сторона есть, отвечающей нет
```

Две строки в этом выводе стоит прочитать внимательно:

* **`Объявленные швы: 2, соединили обе стороны: 1`.** Объявленный шов —
  единственный способ связать два языка: вызовов между ними нет, есть
  сообщение по литералу (Р2).
* **вторая строка — честное признание**, а не ошибка: у топика
  `prices-recalculated` объявлена только зовущая сторона (питон публикует),
  а точки входа-получателя в фикстуре нет. Молча потерянный шов неотличим
  от «шва нет».

Оценочный набор на этом же индексе:

```bash
uv run docpipe graph eval evals/fixtures.yaml --index $TRY/fixtures-graph.db
```

Ожидается `ожиданий всего: 16, подтверждено: 16, запретов нарушено: 0`.

---

## 2. Шов между языками: питон дотягивается до .NET (G16 п. 4)

Главная новая связь. В `tests/fixtures/WildSolution/clients/price_client.py`
маршрут **собран из кусков** (`BASE + "/innerdebts" + "/state/byclient"`) —
статический поиск литерала не находит там ничего. Литерал приходит из реестра.

```bash
uv run docpipe graph path \
  "clients/price_client.py#PriceClient.load" \
  "src/Wild.Api/Controllers/InnerDebtsController.cs#InnerDebtsController.StateByClientAsync" \
  --index $TRY/fixtures-graph.db
```

```
clients/price_client.py#PriceClient.load
  --crosses(seam:declared)--> seam:http_route:api/ml/innerdebts/state/byclient
  --crosses(seam:declared)--> entry:http_endpoint:get api/ml/innerdebts/state/byclient
  --dispatches(entrypoint:manifest)--> …#InnerDebtsController.StateByClientAsync
```

Шов стои́т **звеном пути**, а не схлопнут в прямое ребро: без названного
литерала ответ «питон дошёл до эндпоинта» не даёт зацепиться.

Обратный вопрос — тот, ради которого шов и нужен: правя контроллер, что
сломается на другом языке?

```bash
uv run docpipe graph affects \
  "src/Wild.Api/Controllers/InnerDebtsController.cs#InnerDebtsController.StateByClientAsync" \
  --index $TRY/fixtures-graph.db
```

```
Узлов и файлов на входе: 1
  точек входа: 2
    GET api/ml/innerdebts/state/byclient (http_endpoint)
    Синхронизация цен (service)          ← питоновский сервис
```

Шов ищется и по-русски, и по куску литерала:

```bash
uv run docpipe graph resolve "состояние по клиенту" --index $TRY/fixtures-graph.db
uv run docpipe graph resolve "state/byclient"       --index $TRY/fixtures-graph.db
uv run docpipe graph reaches "Синхронизация цен"    --index $TRY/fixtures-graph.db
```

**Как добавить свой шов:** запись `kind: seam` в реестре
(`tests/fixtures/arch/sample-arch.yaml` — пример из двух записей). Зовущая
сторона берётся из `source.file` + `source.record`; если `record` пуст —
зовёт тип, а не все члены файла подряд.

---

## 3. `affects` принимает дифф (основной сценарий PR)

Команда берёт ключи узлов, пути файлов и приблизительные имена вперемешку:
требовать от вызывающего перевода путей в ключи значит требовать знания,
которого у него нет.

```bash
cd ~/docspipe-examples/examples/squidex
git show --name-only --format= -m --first-parent HEAD | \
  uv run --project $REPO docpipe graph affects --stdin --index $TRY/squidex-graph.db
cd $REPO
```

(индекс squidex собирается в разделе 5; после него команда выше отработает)

На фикстурах то же самое проверяется сразу:

```bash
uv run docpipe graph affects clients/price_client.py --index $TRY/fixtures-graph.db
printf 'clients/price_client.py\nsrc/Wild.Api/Controllers/InnerDebtsController.cs\n' | \
  uv run docpipe graph affects --stdin --index $TRY/fixtures-graph.db
```

Второй вызов даёт 6 точек входа, включая страницу фронта и питоновский
сервис — то есть влияние правки поперёк трёх языков одной командой.

---

## 4. Разные корни манифеста и разбора — отказ с диагнозом

Ловушка, которую нельзя заметить по числам: `scan` от `repo/App`, а
`graph build` от `repo`. Совпасть не может ничего, но прогон проходит
целиком, «сопоставлено 0» стои́т строкой среди прочих чисел, а `affects`
по `git diff` не находит ни файла.

```bash
E=~/docspipe-examples/examples/eshoponweb
cat > $TRY/eshop.yaml <<YAML
rules: $REPO/rules/rules.yaml
dispatch_interfaces: ["IRequestHandler", "INotificationHandler"]
graph:
  engine_path: ~/.local/bin/codebase-memory-mcp
  out: $TRY/eshop-graph.db
  cache_dir: $TRY/engine-cache
YAML

uv run docpipe scan --root $E/eShopOnWeb --config $TRY/eshop.yaml --out $TRY/eshop.json

# НАМЕРЕННО неверный корень — на один сегмент выше:
uv run docpipe graph build --root $E --config $TRY/eshop.yaml --manifest $TRY/eshop.json
```

Ожидается отказ (код 2), называющий сегмент и то, что чинить:

```
Манифест и разбор сняты с разных корней: корни разные: у разбора пути
начинаются с `eShopOnWeb/`, у манифеста — нет (`src/ApplicationCore/…`
против `eShopOnWeb/src/ApplicationCore/…`). … либо соберите манифест
от `--root …/eShopOnWeb`, либо разбор — от того же корня
```

Теперь верно:

```bash
uv run docpipe graph build --root $E/eShopOnWeb --config $TRY/eshop.yaml --manifest $TRY/eshop.json
```
→ `узлов: 750, рёбер: 368, корней: 25`.

### Ноль точек входа обязан быть объяснён

```bash
uv run docpipe graph affects src/Web/Configuration/ConfigureCoreServices.cs --index $TRY/eshop-graph.db
uv run docpipe graph affects src/Web/Program.cs --index $TRY/eshop-graph.db
```

Первое: `точек входа ноль, потому что изменение затрагивает сборку
контейнера … влияние — на всё, что здесь собирается. Сузить анализ нечем,
и это не то же самое, что «ничего не задето»`.

Второе — случай, который ловится труднее: у `Program.cs` на top-level
statements **узлов нет вовсе**, и «не найдено» было бы формально верно
и практически ложно. Ответ: `узлов в этих файлах нет …, но здесь
собирается приложение: изменение задевает всё, что регистрируется,
а не ничего`.

---

## 5. Самодельные обёртки DI: ключ `di_methods`

На squidex стандартной формы регистрации почти нет: 275 `AddSingletonAs`,
37 `AddTransientAs`, 2 `AddScopedAs`. Без ключа отчёт показывает 67
регистраций — непустое число, в котором ноль незаметен.

```bash
S=~/docspipe-examples/examples/squidex
cat > $TRY/squidex-plain.yaml <<YAML
rules: $REPO/rules/rules.yaml
graph:
  engine_path: ~/.local/bin/codebase-memory-mcp
  out: $TRY/squidex-graph.db
  cache_dir: $TRY/engine-cache
YAML
sed 's|^rules:|di_methods: ["AddSingletonAs", "AddTransientAs", "AddScopedAs"]\nrules:|' \
    $TRY/squidex-plain.yaml > $TRY/squidex-wrappers.yaml

uv run docpipe scan --root $S --config $TRY/squidex-plain.yaml    --out $TRY/sq-plain.json
uv run docpipe scan --root $S --config $TRY/squidex-wrappers.yaml --out $TRY/sq-wrap.json

for f in $TRY/sq-plain.json $TRY/sq-wrap.json; do
  uv run python -c "
import json,sys; d=json.load(open('$f')); r=d['di_registrations']
print('$f'.split('/')[-1], 'регистраций:', len(r),
      '| сервис != реализация:', sum(1 for x in r if x['service_type']!=x['impl_type']))"
done
```

```
sq-plain.json регистраций: 67  | сервис != реализация: 35
sq-wrap.json  регистраций: 421 | сервис != реализация: 345
```

Второй прогон **не требует чистки кэша**: список методов входит в ключ кэша
разбора. Не входил бы — числа остались бы прежними, и выглядело бы это как
«правка не сработала».

Сборка графа:

```bash
uv run docpipe web scan --root $S --config $TRY/squidex-wrappers.yaml --out $TRY/sq-web.json
uv run docpipe graph build --root $S --config $TRY/squidex-wrappers.yaml \
    --manifest $TRY/sq-wrap.json --web-manifest $TRY/sq-web.json
```

```
узлов: 19925, рёбер: 29385          (без ключа было 23929 рёбер)
DI-регистраций разобрано: 303       (без ключа — 30)
```

### Расхождение выбора с регистрацией — смотреть глазами

В том же выводе:

```
Цель разошлась с регистрацией: 562; рёбер поверх расхождения: 2451
  …AlgoliaAction.cs#ToFlowStep → …AssemblyTypeProvider.cs#AssemblyTypeProvider.Map
```

В коде там `SimpleMapper.Map(this, new AlgoliaFlowStep())` — статический
помощник. Движок 0.6.0 разрешает вызов **по имени члена**, и попадает
в одноимённый член реализации интерфейса. Отбросить такие рёбра нельзя:
структурно это неотличимо от декоратора, где наше ребро — исправление.
Поэтому они помечены своим `via` (`di:alternative:diverged`), уверенность
вдвое ниже, число отдельное.

---

## 6. Проверка на влитых правках (G18 п. 4)

```bash
uv run docpipe graph pr-check --root $S --index $TRY/squidex-graph.db --count 10 --scan 200
```

```
Правок просмотрено: 10, из них меняли файл точки входа: 5
Точек входа, которые обязаны были найтись: 188, пропущено: 0
```

Истина здесь — проверяемая часть: правка меняла файл точки входа, значит
`affects` обязан её назвать. `--fail-on-missed` делает это проверкой CI.

---

## 7. Разборщик молча пропускает каталоги

Замеренный факт: 0.6.0 не индексирует `tools/`, `scripts/`, `build/`,
`vendor/`, `bin/` — на любом языке, без ошибки и без строки в выводе.
Там живёт интеграционный код.

```bash
D=$TRY/skipped; rm -rf $D; mkdir -p $D/src $D/tools
cat > $D/Demo.csproj <<'XML'
<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>
XML
printf 'namespace Demo;\n\n/// <summary>Обычный сервис.</summary>\npublic class VisibleService { public void Run() { } }\n' > $D/src/VisibleService.cs
printf 'namespace Demo;\n\n/// <summary>Тот же код, но в tools/.</summary>\npublic class HiddenService { public void Run() { } }\n' > $D/tools/HiddenService.cs

cat > $TRY/skipped.yaml <<YAML
rules: $REPO/rules/rules.yaml
graph:
  engine_path: ~/.local/bin/codebase-memory-mcp
  out: $TRY/skipped-graph.db
  cache_dir: $TRY/engine-cache
YAML

uv run docpipe scan --root $D --config $TRY/skipped.yaml --out $TRY/skipped.json --no-cache
uv run docpipe graph build --root $D --config $TRY/skipped.yaml --manifest $TRY/skipped.json
```

```
сопоставлено с манифестом: 2
есть в манифесте — нет в графе: 1
из них: файл в каталоге, который разборщик пропускает: 1
пропущено вместе с каталогом — примеры:
  Demo.csproj#Demo.HiddenService`0
```

Без второй строки это выглядело бы шумом разбора.

---

## 8. Спецификации в отчёте покрытия (G17 п. 3)

```bash
cat > $TRY/coverage.yaml <<YAML
rules: $REPO/rules/rules.yaml
arch: $REPO/tests/fixtures/arch/sample-arch.yaml
business_root: tests/fixtures/business
graph:
  engine_path: ~/.local/bin/codebase-memory-mcp
  out: $TRY/fixtures-graph.db
  cache_dir: $TRY/engine-cache
YAML
uv run docpipe graph coverage --root . --config $TRY/coverage.yaml
```

```
Точек входа: 18, описано документом: 0 (0%)
Со спецификацией: 0, названо спецификаций: 0

Спецификаций не названо ни у одной точки входа. Это законно: идентификатор
ставится атрибутом `spec` у записи реестра…
```

Чтобы увидеть непустое число, добавьте в запись реестра:

```yaml
  - kind: entry_point
    key: RiskCompute
    entry_kind: grid_service
    name: Расчёт риска
    attributes:
      spec: CF-SPEC-42          # ← идентификатор чужой системы
```

и пересоберите индекс — станет `Со спецификацией: 1, названо спецификаций: 1`.

---

## 9. Общий слой документа: приёмка (G17 п. 5)

Зоны документа и приёмка живут в `docpipe/documents/` — вне шага 2 и вне
бизнес-слоя, потому что их зовут оба. Сквозная проверка, что слой работает:

```bash
uv run docpipe scan --root tests/fixtures/SampleSolution --out $TRY/dt.json --no-cache
uv run docpipe materialize $TRY/dt.json --root $TRY/docs

D=$TRY/docs/docs/modules/services/Sample.Pricing.Api/pricing-service.md
python3 -c "
import re,pathlib
p=pathlib.Path('$D'); t=p.read_text(encoding='utf-8')
p.write_text(re.sub(r'(<!-- docpipe:section:start (\w+) -->\n)', r'\1Наполнено человеком.\n', t), encoding='utf-8')"

uv run docpipe docs accept $TRY/dt.json --root $TRY/docs \
    docs/modules/services/Sample.Pricing.Api/pricing-service.md
grep -A 8 docpipe_state $D
uv run docpipe docs status $TRY/dt.json --root $TRY/docs | head -5
```

Ожидается `Принято: 1`, в документе блок `accepted:` с хэшами и
**`review: null`** (приёмка снимает отметку о пересмотре — правило теперь
живёт в одном месте на оба слоя), в статусе `current 1, empty 5`.

Что граница держится не соглашением, а тестом:

```bash
uv run pytest tests/test_documents_state.py -q
grep -rn '"review": None' docpipe/ --include=*.py    # только docpipe/documents/write.py
```

---

## 10. MCP-сервер: те же ответы агенту

```bash
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
| uv run docpipe graph serve --index $TRY/fixtures-graph.db 2>/dev/null | head -2
```

Ожидается `serverInfo: docpipe-graph` и семь инструментов:
`docpipe_resolve`, `docpipe_card`, `docpipe_reaches`, `docpipe_affects`,
`docpipe_path`, `docpipe_overview`, `docpipe_why`.

---

## Что смотреть, если что-то пошло не так

| Симптом | Куда смотреть |
|---|---|
| «сопоставлено 0» и пустые ответы | корни манифеста и разбора; прогон обязан был отказаться — если не отказался, это находка |
| правка правил разбора «не сработала» | `CACHE_VERSION` в `docpipe/cache.py`; настройки, влияющие на разбор, обязаны входить в ключ кэша |
| символ есть в документации, но не в графе | строка «файл в каталоге, который разборщик пропускает» |
| ноль точек входа без объяснения | это дефект: ноль обязан называть причину (Р7) |
| числа расходятся с этой инструкцией | `uv run docpipe graph info` — паспорт индекса: чем собран и что не вошло |

Удалить всё, что создал прогон: `rm -rf $TRY`
(плюс `.docpipe/cache` внутри примеров, если запускались разделы 5–6).
