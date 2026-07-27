#!/usr/bin/env bash
#
# Установка docpipe внутрь репозитория АС CF.
#
#     ./deploy/install.sh /home/work/$USER/cfml/sbt.cms.cashflow
#
# Кладёт в <репозиторий>/docs/ml/docspipe только то, что нужно для запуска:
# пакет, манифест зависимостей без dev-группы, лок-файл и настройки под проект.
# Тесты, фикстуры, план, журнал, ruff и mypy на целевую машину не попадают.
#
# Скрипт идемпотентен: повторный запуск обновляет код, но НЕ трогает уже
# настроенные `docpipe.yaml` и `rules.yaml` — они кладутся рядом как `*.new`.
# Затирать чужую настройку молча — худшее, что может сделать установщик.

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBDIR="docs/ml/docspipe"
SYNC=1

usage() {
    cat >&2 <<'EOF'
Использование: install.sh <путь-к-репозиторию-АС-CF> [--skip-sync]

  <путь>        корень репозитория sbt.cms.cashflow
  --skip-sync   только скопировать файлы, не поднимать окружение через uv
EOF
    exit 2
}

[ $# -ge 1 ] || usage
TARGET_REPO="$1"
shift
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-sync) SYNC=0 ;;
        *) usage ;;
    esac
    shift
done

[ -d "$TARGET_REPO" ] || { echo "Каталог не найден: $TARGET_REPO" >&2; exit 1; }
TARGET_REPO="$(cd "$TARGET_REPO" && pwd)"
DEST="$TARGET_REPO/$SUBDIR"

# Проверка, что это действительно репозиторий с исходниками, а не соседний
# каталог: установка в неправильное место обнаружилась бы только на прогоне.
if [ ! -d "$TARGET_REPO/.git" ] && [ -z "$(find "$TARGET_REPO" -maxdepth 2 -name '*.sln' -print -quit)" ]; then
    echo "Внимание: в $TARGET_REPO нет ни .git, ни .sln — тот ли это репозиторий?" >&2
fi

echo "Источник:   $SOURCE"
echo "Назначение: $DEST"
mkdir -p "$DEST/cashflow-docspipe"

# --- код и зависимости ------------------------------------------------------
# Пакет копируется целиком, но без следов запуска: .pyc сборки на другой версии
# Python не переносятся и в поставке только мешают.
rm -rf "$DEST/docpipe"
cp -r "$SOURCE/docpipe" "$DEST/docpipe"
find "$DEST/docpipe" -name '__pycache__' -type d -prune -exec rm -rf {} +

cp "$SOURCE/deploy/pyproject.toml" "$DEST/pyproject.toml"
cp "$SOURCE/deploy/uv.lock" "$DEST/uv.lock"
cp "$SOURCE/deploy/README.md" "$DEST/README.md"
cp "$SOURCE/deploy/gitignore" "$DEST/.gitignore"

# Эталонный набор правил — для сравнения с настроенным под АС CF.
mkdir -p "$DEST/rules"
cp "$SOURCE/rules/dotnet.yaml" "$DEST/rules/dotnet.yaml"

# --- настройки под проект ---------------------------------------------------
install_config() {
    local name="$1"
    local from="$SOURCE/deploy/cashflow-docspipe/$name"
    local to="$DEST/cashflow-docspipe/$name"

    if [ ! -e "$to" ]; then
        cp "$from" "$to"
        echo "  установлен: cashflow-docspipe/$name"
    elif cmp -s "$from" "$to"; then
        echo "  без изменений: cashflow-docspipe/$name"
    else
        cp "$from" "$to.new"
        echo "  СОХРАНЁН ваш cashflow-docspipe/$name, новая версия рядом: $name.new" >&2
    fi
}

install_config docpipe.yaml
install_config rules.yaml
cp "$SOURCE/deploy/cashflow-docspipe/run.sh" "$DEST/cashflow-docspipe/run.sh"
cp "$SOURCE/deploy/cashflow-docspipe/README.md" "$DEST/cashflow-docspipe/README.md"
chmod +x "$DEST/cashflow-docspipe/run.sh"

# --- окружение --------------------------------------------------------------
if [ "$SYNC" -eq 1 ]; then
    command -v uv >/dev/null || { echo "uv не найден в PATH" >&2; exit 1; }
    echo "Поднимаю окружение (uv sync --frozen)…"
    # --frozen: ставить ровно то, что в локе. Без него uv при малейшем
    # расхождении полез бы в индекс перерешать зависимости, и поставка
    # перестала бы быть воспроизводимой.
    uv sync --frozen --project "$DEST"
    echo -n "Проверка: docpipe "
    uv run --frozen --project "$DEST" docpipe version
fi

cat <<EOF

Готово. Дальше:

  cd $DEST/cashflow-docspipe
  ./run.sh stats          # посмотреть срезы, ничего не записывая
  ./run.sh scan           # построить манифест

Настройка под проект — в $DEST/cashflow-docspipe/README.md
EOF
