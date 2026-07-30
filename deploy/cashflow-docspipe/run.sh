#!/usr/bin/env bash
#
# Запуск docpipe на репозитории АС CF.
#
#     ./run.sh stats            # состояние решений, ничего не пишет
#     ./run.sh symbols [флаги]  # какие именно символы остались без решения
#     ./run.sh scan             # построить манифест
#     ./run.sh dry-run          # что изменилось бы, не записывая
#     ./run.sh validate         # проверить готовый манифест
#     ./run.sh diff <старый>    # сравнить готовый манифест с другим
#     ./run.sh -- <аргументы>   # что угодно ещё, напрямую в docpipe
#
# Скрипт подставляет абсолютные пути, поэтому его можно звать из любого
# каталога. Все настройки — в docpipe.yaml и rules.yaml рядом.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="$(dirname "$HERE")"                       # docs/ml/docspipe
# Корень репозитория АС CF: три уровня вверх от каталога инструмента
# (docspipe -> ml -> docs -> корень). Переопределяется переменной окружения,
# если инструмент положили не по описанной раскладке:
#     CF_ROOT=/путь/к/репозиторию ./run.sh scan
CF_ROOT="${CF_ROOT:-$(cd "$BUNDLE/../../.." && pwd)}"

CONFIG="$HERE/docpipe.yaml"
RULES="$HERE/rules.yaml"
OUT="${OUT:-$HERE/artifacts/doc-tree.json}"
JOBS="${JOBS:-4}"                                 # 17988 файлов — распараллеливание оправдано

[ -d "$CF_ROOT" ] || { echo "Не найден корень репозитория: $CF_ROOT" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "Не найден $CONFIG" >&2; exit 1; }
[ -d "$BUNDLE/.venv" ] || {
    echo "Окружение не поднято: нет $BUNDLE/.venv" >&2
    echo "Поднять его — install.sh из репозитория разработки, см. $BUNDLE/README.md" >&2
    exit 1
}

# Два решения ради закрытого контура, оба обязательны:
#
# --no-sync: запуск не ходит в сеть вообще. Иначе `uv run` при каждом вызове
# сверялся бы с индексом и на машине без доступа к нему падал бы на команде,
# которая с зависимостями ничего не делает. Окружение поднимает install.sh,
# и это его работа, а не наша.
#
# `python -m docpipe` вместо консольной команды `docpipe`: работает и когда
# пакет установлен, и когда окружение поднято как --no-install-project (без
# сборочного бэкенда). PYTHONPATH нужен для второго случая — пакет лежит
# в дереве, но в site-packages его нет.
dp() { PYTHONPATH="$BUNDLE" uv run --no-sync --project "$BUNDLE" python -m docpipe "$@"; }

COMMON=(--root "$CF_ROOT" --config "$CONFIG" --rules "$RULES" --out "$OUT")

MODE="${1:-scan}"
shift || true

case "$MODE" in
    scan)     dp scan "${COMMON[@]}" --jobs "$JOBS" "$@" ;;
    stats)    dp scan "${COMMON[@]}" --jobs "$JOBS" --stats "$@" ;;
    # Отладка набора правил на одном проекте:
    #     ./run.sh symbols --module Sbt.Cashflow.Grid.Services.AutoConclusionService
    #     ./run.sh symbols --state not_documented --rule generated.schemas
    symbols)  dp symbols --root "$CF_ROOT" --config "$CONFIG" --rules "$RULES" \
                  --jobs "$JOBS" "$@" ;;
    dry-run)  dp scan "${COMMON[@]}" --jobs "$JOBS" --dry-run "$@" ;;
    validate) dp validate "$OUT" "$@" ;;
    diff)     [ $# -ge 1 ] || { echo "Использование: run.sh diff <старый-манифест>" >&2; exit 2; }
              dp diff "$1" "$OUT" ;;
    --)       dp "$@" ;;
    *)        echo "Неизвестный режим: $MODE. Доступны: stats, symbols, scan, dry-run, validate, diff, --" >&2
              exit 2 ;;
esac
