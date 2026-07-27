#!/usr/bin/env bash
#
# Запуск docpipe на репозитории АС CF.
#
#     ./run.sh stats            # срезы для настройки правил, ничего не пишет
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

# --frozen: ставить ровно то, что в uv.lock, и не ходить в индекс за пересчётом.
dp() { uv run --frozen --project "$BUNDLE" docpipe "$@"; }

COMMON=(--root "$CF_ROOT" --config "$CONFIG" --rules "$RULES" --out "$OUT")

MODE="${1:-scan}"
shift || true

case "$MODE" in
    scan)     dp scan "${COMMON[@]}" --jobs "$JOBS" "$@" ;;
    stats)    dp scan "${COMMON[@]}" --jobs "$JOBS" --stats "$@" ;;
    dry-run)  dp scan "${COMMON[@]}" --jobs "$JOBS" --dry-run "$@" ;;
    validate) dp validate "$OUT" "$@" ;;
    diff)     [ $# -ge 1 ] || { echo "Использование: run.sh diff <старый-манифест>" >&2; exit 2; }
              dp diff "$1" "$OUT" ;;
    --)       dp "$@" ;;
    *)        echo "Неизвестный режим: $MODE. Доступны: stats, scan, dry-run, validate, diff, --" >&2
              exit 2 ;;
esac
