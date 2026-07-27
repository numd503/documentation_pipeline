# docpipe в репозитории АС CF

Поставка инструмента построения дерева документации. Это **не** копия
репозитория разработки: здесь только пакет, его зависимости и настройка под
проект. Тестов, фикстур, плана, журнала, `ruff` и `mypy` здесь нет и быть
не должно.

```
docs/ml/docspipe/
├── docpipe/              пакет
├── pyproject.toml        только runtime-зависимости (5 штук)
├── uv.lock               зафиксированные версии, 17 пакетов
├── rules/dotnet.yaml     эталонный набор правил — для сравнения
└── cashflow-docspipe/    всё, что настроено под АС CF
    ├── docpipe.yaml      что документируем, куда не заходим, куда пишем
    ├── rules.yaml        правила классификации под АС CF
    ├── run.sh            запуск
    ├── artifacts/        манифест (создаётся при прогоне)
    └── .cache/           кэш разбора (создаётся при прогоне)
```

## Установка и обновление

Из склонированного репозитория разработки:

```bash
cd /home/work/$USER/docspipe/documentation_pipeline
./deploy/install.sh /home/work/$USER/cfml/sbt.cms.cashflow
```

Повторный запуск обновляет код, но **не затирает** настроенные `docpipe.yaml`
и `rules.yaml` — новые версии кладутся рядом как `*.new`.

## Запуск

```bash
cd /home/work/$USER/cfml/sbt.cms.cashflow/docs/ml/docspipe/cashflow-docspipe

./run.sh stats        # срезы для настройки правил, ничего не пишет
./run.sh scan         # построить манифест
./run.sh validate     # проверить построенное
```

Всё остальное — в [`cashflow-docspipe/README.md`](cashflow-docspipe/README.md).

## Если окружение не поднимается

`uv sync` собирает сам пакет `docpipe`, для чего ему нужен `hatchling`. Если
сборочный бэкенд недоступен, окружение поднимается без установки самого проекта,
и тогда команда вызывается модулем:

```bash
cd /home/work/$USER/cfml/sbt.cms.cashflow/docs/ml/docspipe
uv sync --frozen --no-install-project
python -m docpipe version           # из этого каталога: пакет берётся из него же
```

В этом режиме `run.sh` не работает: он зовёт консольную команду, которой без
установки проекта не появляется. Полный вызов руками — из корня репозитория
АС CF. `PYTHONPATH` обязателен: пакет лежит в дереве, но не установлен, и без
него `python -m docpipe` его не найдёт.

```bash
cd /home/work/$USER/cfml/sbt.cms.cashflow
BUNDLE=docs/ml/docspipe
PYTHONPATH=$BUNDLE $BUNDLE/.venv/bin/python -m docpipe scan \
    --root . \
    --config $BUNDLE/cashflow-docspipe/docpipe.yaml \
    --rules  $BUNDLE/cashflow-docspipe/rules.yaml \
    --jobs 4
```
