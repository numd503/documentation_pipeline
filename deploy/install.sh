#!/usr/bin/env bash
#
# Установка docpipe внутрь репозитория АС CF.
#
#     ./deploy/install.sh $WORK/cfml/sbt.cms.cashflow
#
# На закрытом контуре, где пакеты приходят из внутреннего зеркала:
#
#     ./deploy/install.sh $WORK/cfml/sbt.cms.cashflow \
#         --index https://зеркало/repository/pypi/simple \
#         --python /usr/bin/python3.12
#
# Кладёт в <репозиторий>/docs/ml/docspipe только то, что нужно для запуска:
# пакет, манифест зависимостей без dev-группы, лок-файл и настройки под проект.
# Тесты, фикстуры, план, журнал, ruff и mypy на целевую машину не попадают.
#
# Скрипт идемпотентен: повторный запуск обновляет код, но НЕ трогает уже
# настроенные `docpipe.yaml`, `rules.yaml` и `uv.toml` — они кладутся рядом
# как `*.new`. Затирать чужую настройку молча — худшее, что может сделать
# установщик.

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBDIR="docs/ml/docspipe"
SYNC=1
RELOCK=0
INSTALL_PROJECT=1
INDEX=""
PYTHON=""

usage() {
    cat >&2 <<'EOF'
Использование: install.sh <путь-к-репозиторию-АС-CF> [опции]

  <путь>                 корень репозитория sbt.cms.cashflow

  --index URL            адрес внутреннего зеркала пакетов. Записывается
                         в uv.toml поставки и ВЛЕЧЁТ ПЕРЕСБОРКУ ЛОКА: тот,
                         что лежит в репозитории, ссылается на файлы
                         pypi.org поимённо и на закрытом контуре бесполезен
  --python PATH|ВЕРСИЯ   каким интерпретатором поднимать окружение,
                         например /usr/bin/python3.12 или 3.12.13
  --relock               пересобрать лок, не задавая индекс (адрес зеркала
                         уже прописан глобально в ~/.config/uv/uv.toml)
  --no-install-project   не собирать сам пакет docpipe (не нужен hatchling).
                         Команда вызывается как `python -m docpipe`;
                         run.sh работает в обоих режимах
  --skip-sync            только скопировать файлы, окружение не поднимать
EOF
    exit 2
}

[ $# -ge 1 ] || usage
TARGET_REPO="$1"
shift
while [ $# -gt 0 ]; do
    case "$1" in
        --index) [ $# -ge 2 ] || usage; INDEX="$2"; RELOCK=1; shift ;;
        --python) [ $# -ge 2 ] || usage; PYTHON="$2"; shift ;;
        --relock) RELOCK=1 ;;
        --no-install-project) INSTALL_PROJECT=0 ;;
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

cp "$SOURCE/deploy/uv.toml.example" "$DEST/uv.toml.example"

# Лок из поставки ссылается на файлы pypi.org поимённо. Если на месте уже лежит
# лок, собранный против внутреннего зеркала, затирать его нельзя: следующий
# же `uv sync --frozen` пошёл бы в недоступный pypi.org — и обновление
# инструмента ломало бы рабочую установку.
#
# Пересобирать его при этом нужно, только если изменился состав зависимостей.
# Иначе обновление кода требовало бы связи с зеркалом на ровном месте.
if [ -f "$DEST/uv.lock" ] && ! grep -q 'registry = "https://pypi.org/simple"' "$DEST/uv.lock"; then
    if cmp -s "$SOURCE/deploy/pyproject.toml" "$DEST/pyproject.toml"; then
        echo "  сохранён uv.lock, собранный против внутреннего зеркала"
    else
        echo "  зависимости изменились — лок против внутреннего зеркала будет пересобран"
        RELOCK=1
    fi
else
    cp "$SOURCE/deploy/uv.lock" "$DEST/uv.lock"
fi

cp "$SOURCE/deploy/pyproject.toml" "$DEST/pyproject.toml"
cp "$SOURCE/deploy/README.md" "$DEST/README.md"
cp "$SOURCE/deploy/gitignore" "$DEST/.gitignore"

# Эталонный набор правил — для сравнения с настроенным под АС CF.
mkdir -p "$DEST/rules"
cp "$SOURCE/rules/dotnet.yaml" "$DEST/rules/dotnet.yaml"

# --- файлы, которые правит человек ------------------------------------------
# Кладутся только если их ещё нет. Правка правил классификации — это недели
# работы, и молча заменить её обновлением инструмента недопустимо.
keep_configured() {
    local from="$1" to="$2" label="$3"

    if [ ! -e "$to" ]; then
        cp "$from" "$to"
        echo "  установлен: $label"
    elif cmp -s "$from" "$to"; then
        echo "  без изменений: $label"
    else
        cp "$from" "$to.new"
        echo "  СОХРАНЁН ваш $label, новая версия рядом: $(basename "$to").new" >&2
    fi
}

keep_configured "$SOURCE/deploy/cashflow-docspipe/docpipe.yaml" \
    "$DEST/cashflow-docspipe/docpipe.yaml" "cashflow-docspipe/docpipe.yaml"
keep_configured "$SOURCE/deploy/cashflow-docspipe/rules.yaml" \
    "$DEST/cashflow-docspipe/rules.yaml" "cashflow-docspipe/rules.yaml"
cp "$SOURCE/deploy/cashflow-docspipe/run.sh" "$DEST/cashflow-docspipe/run.sh"
cp "$SOURCE/deploy/cashflow-docspipe/README.md" "$DEST/cashflow-docspipe/README.md"
chmod +x "$DEST/cashflow-docspipe/run.sh"

# --- настройки uv для закрытого контура -------------------------------------
if [ -n "$INDEX" ]; then
    # Адрес подставляется в шаблон, а не в сгенерированный с нуля файл:
    # комментарии в шаблоне объясняют, зачем нужны native-tls
    # и python-downloads, и терять их при установке незачем.
    tmp="$(mktemp)"
    sed "s|https://ЗАПОЛНИТЬ/repository/pypi/simple|$INDEX|" \
        "$SOURCE/deploy/uv.toml.example" > "$tmp"
    keep_configured "$tmp" "$DEST/uv.toml" "uv.toml (индекс $INDEX)"
    rm -f "$tmp"
elif [ -f "$SOURCE/deploy/uv.toml" ]; then
    # Настройки, подобранные внутри контура и сохранённые в клоне (см. OFFLINE.md).
    # Тогда `--index` при установке уже не нужен — ни здесь, ни на других машинах.
    keep_configured "$SOURCE/deploy/uv.toml" "$DEST/uv.toml" "uv.toml (из клона)"
fi

# --- окружение --------------------------------------------------------------
uv_flags=(--project "$DEST")
[ -n "$PYTHON" ] && uv_flags+=(--python "$PYTHON")

diagnose() {
    cat >&2 <<EOF

Окружение поднять не удалось. Три причины, по которым это обычно происходит
на закрытом контуре, — в порядке частоты:

  1. Пакеты тянутся не из внутреннего зеркала. Лок в поставке ссылается на
     файлы pypi.org поимённо, и с ним uv никуда больше не пойдёт. Лечится
     пересборкой лока против зеркала:

         $0 $TARGET_REPO --index https://зеркало/repository/pypi/simple

  2. \`invalid peer certificate\` — сертификат TLS-прокси подписан внутренним
     удостоверяющим центром, которого нет в наборе, вшитом в uv. В uv.toml
     поставки для этого стоит native-tls = true (системное хранилище).
     Если и в системном его нет:

         SSL_CERT_FILE=/путь/до/corp-root-ca.pem $0 $TARGET_REPO --index ...

  3. Не найден интерпретатор. Скачивать его запрещено намеренно — укажите
     явно тот, что есть:

         $0 $TARGET_REPO --index ... --python /usr/bin/python3.12

Скопированные файлы на месте, повторный запуск ничего не испортит.
EOF
    exit 1
}

if [ "$SYNC" -eq 1 ]; then
    command -v uv >/dev/null || { echo "uv не найден в PATH" >&2; exit 1; }

    if [ "$RELOCK" -eq 1 ]; then
        echo "Пересобираю лок против настроенного индекса…"
        # Лок поставки при этом не удаляется: uv предпочтёт уже записанные
        # версии, если зеркало их отдаёт, — тогда окружение совпадёт с тем,
        # на котором гонялись тесты.
        uv lock "${uv_flags[@]}" || diagnose
    fi

    sync_flags=(--frozen "${uv_flags[@]}")
    [ "$INSTALL_PROJECT" -eq 0 ] && sync_flags+=(--no-install-project)

    echo "Поднимаю окружение…"
    # --frozen: ставить ровно то, что в локе. Без него uv при малейшем
    # расхождении полез бы в индекс перерешать зависимости, и поставка
    # перестала бы быть воспроизводимой.
    uv sync "${sync_flags[@]}" || diagnose

    echo -n "Проверка: docpipe "
    PYTHONPATH="$DEST" uv run --no-sync "${uv_flags[@]}" python -m docpipe version || diagnose
fi

cat <<EOF

Готово. Дальше:

  cd $DEST/cashflow-docspipe
  ./run.sh stats          # посмотреть срезы, ничего не записывая
  ./run.sh scan           # построить манифест

Настройка под проект — в $DEST/cashflow-docspipe/README.md
EOF
