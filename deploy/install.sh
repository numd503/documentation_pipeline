#!/usr/bin/env bash
#
# Раскладка docpipe на целевой машине.
#
# Две ВЕЩИ В РАЗНЫХ МЕСТАХ, и разделение здесь смысловое, а не для порядка:
#
#   инструмент  ставится на машину (`uv tool install`) и в репозитории продукта
#               не лежит. Обновляется отдельно от продукта, версией не связан
#               с его историей и не попадает в его diff;
#   настройка   лежит ВНУТРИ репозитория продукта, в каталоге, который задаёте
#               вы: правила классификации, владение, реестры, шаблоны. Это
#               решения о продукте, им место рядом с продуктом.
#
#     ./deploy/install.sh --repo $WORK/cfml/sbt.cms.cashflow \
#                         --config-dir docs/ml/docpipe
#
# На закрытом контуре добавляются --index, --python и --engine.
#
# Скрипт идемпотентен: повторный запуск обновляет инструмент, но НЕ трогает
# настроенные yaml и правленые шаблоны — новые версии кладутся рядом как
# `*.new`. Затирать чужую настройку молча — худшее, что может сделать
# установщик: правка правил классификации это недели работы.

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_SRC="$SOURCE/deploy/cashflow-docspipe"

REPO=""
CONFIG_DIR=""
CACHE_DIR=""
ENGINE=""
INDEX=""
PYTHON=""
TOOL=1

usage() {
    cat >&2 <<'EOF'
Использование: install.sh --repo ПУТЬ --config-dir ПУТЬ [опции]

  --repo ПУТЬ          корень документируемого репозитория
  --config-dir ПУТЬ    каталог настройки ВНУТРИ него, например docs/ml/docpipe.
                       Путь относительный: он попадает в docpipe.yaml и обязан
                       читаться одинаково на любой машине

Опции:
  --cache-dir ПУТЬ     где держать кэши разбора. АБСОЛЮТНЫЙ и вне репозитория:
                       это гигабайты машинного мусора, дереву продукта они
                       не нужны. По умолчанию $WORK/.docpipe/cache, а без
                       $WORK — ~/.cache/docpipe
  --engine ПУТЬ        путь к codebase-memory-mcp 0.6.0. Без него команды
                       `graph *` откажутся работать, остальные — нет
  --index URL          адрес внутреннего зеркала пакетов. Записывается
                       в uv.toml клона и действует на все вызовы uv оттуда
  --python ПУТЬ|ВЕРСИЯ каким интерпретатором ставить, например 3.12.13.
                       Скачивать Python запрещено намеренно, поэтому на
                       закрытом контуре его надо назвать
  --no-tool            только разложить настройку, инструмент не ставить

Инструмент ставится в UV_TOOL_DIR (запускалка — в UV_TOOL_BIN_DIR). Если они
не заданы, uv возьмёт свои умолчания внутри $HOME — на системах, где работа
идёт вне $HOME, задайте их до запуска:

    export UV_TOOL_DIR=$WORK/.uv     UV_TOOL_BIN_DIR=$WORK/.uv/bin
EOF
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)       [ $# -ge 2 ] || usage; REPO="$2"; shift ;;
        --config-dir) [ $# -ge 2 ] || usage; CONFIG_DIR="$2"; shift ;;
        --cache-dir)  [ $# -ge 2 ] || usage; CACHE_DIR="$2"; shift ;;
        --engine)     [ $# -ge 2 ] || usage; ENGINE="$2"; shift ;;
        --index)      [ $# -ge 2 ] || usage; INDEX="$2"; shift ;;
        --python)     [ $# -ge 2 ] || usage; PYTHON="$2"; shift ;;
        --no-tool)    TOOL=0 ;;
        -h|--help)    usage ;;
        # Прежняя форма — `install.sh <репозиторий>` — клала код инструмента
        # внутрь репозитория в жёстко зашитый docs/ml/docspipe. Принять её
        # молча значит разложить поставку не туда, где её будут искать.
        -*) echo "Неизвестный флаг: $1" >&2; usage ;;
        *)  echo "Позиционный аргумент больше не принимается: $1" >&2
            echo "Каталог настройки теперь задаётся явно: --repo ПУТЬ --config-dir ПУТЬ" >&2
            exit 2 ;;
    esac
    shift
done

[ -n "$REPO" ] || { echo "Не задан --repo" >&2; usage; }
[ -n "$CONFIG_DIR" ] || { echo "Не задан --config-dir" >&2; usage; }
[ -d "$REPO" ] || { echo "Каталог не найден: $REPO" >&2; exit 1; }
REPO="$(cd "$REPO" && pwd)"

# Каталог настройки обязан лежать внутри репозитория и записываться
# относительным путём: его значение уходит в docpipe.yaml, который читают
# и на других машинах. Абсолютный путь там сделал бы конфигурацию личной.
case "$CONFIG_DIR" in
    /*|*..*) echo "--config-dir: относительный путь внутри репозитория, дано: $CONFIG_DIR" >&2
             exit 1 ;;
esac
CONFIG_DIR="${CONFIG_DIR%/}"
DEST="$REPO/$CONFIG_DIR"

# $WORK — каталог, в котором на целевой системе ведётся вся работа, и он
# лежит ВНЕ $HOME. Умолчание идёт туда, потому что кэш обязан быть там, где
# у пользователя есть место и права, а не там, где их предполагает uv.
if [ -z "$CACHE_DIR" ]; then
    if [ -n "${WORK:-}" ]; then
        CACHE_DIR="$WORK/.docpipe/cache"
    else
        CACHE_DIR="$HOME/.cache/docpipe"
    fi
fi
case "$CACHE_DIR" in
    /*) ;;
    # Относительный кэш склеится с --root и уедет в дерево продукта — ровно то,
    # ради ухода от чего каталог и вынесен.
    *) echo "--cache-dir обязан быть абсолютным, дано: $CACHE_DIR" >&2; exit 1 ;;
esac

# Проверка, что это действительно репозиторий с исходниками, а не соседний
# каталог: установка не туда обнаружилась бы только на прогоне.
if [ ! -d "$REPO/.git" ] && [ -z "$(find "$REPO" -maxdepth 2 -name '*.sln' -print -quit)" ]; then
    echo "Внимание: в $REPO нет ни .git, ни .sln — тот ли это репозиторий?" >&2
fi

echo "Репозиторий: $REPO"
echo "Настройка:   $CONFIG_DIR"
echo "Кэши:        $CACHE_DIR"
echo

mkdir -p "$DEST"

# --- файлы, которые правит человек ------------------------------------------
keep_configured() {
    local from="$1" to="$2" label="$3"

    if [ ! -e "$to" ]; then
        mkdir -p "$(dirname "$to")"
        cp "$from" "$to"
        echo "  установлен: $label"
    elif cmp -s "$from" "$to"; then
        echo "  без изменений: $label"
    else
        cp "$from" "$to.new"
        echo "  СОХРАНЁН ваш $label, новая версия рядом: $(basename "$to").new" >&2
    fi
}

# docpipe.yaml приходит с плейсхолдерами: входы в нём записаны короткими
# именами и переносимы как есть, а цели записи и пути от --root переносимыми
# быть не могут — их подставляем здесь. Подстановка идёт во временный файл,
# и keep_configured дальше сравнивает уже готовый результат: при повторной
# установке с теми же параметрами он совпадёт с лежащим и не создаст `.new`.
config_tmp="$(mktemp)"
sed -e "s|@CONFIG_DIR@|$CONFIG_DIR|g" \
    -e "s|@CACHE_DIR@|$CACHE_DIR|g" \
    -e "s|@ENGINE@|$ENGINE|g" \
    "$BUNDLE_SRC/docpipe.yaml" > "$config_tmp"
keep_configured "$config_tmp" "$DEST/docpipe.yaml" "docpipe.yaml"
rm -f "$config_tmp"

for name in rules ownership pages registries arch-registry; do
    keep_configured "$BUNDLE_SRC/$name.yaml" "$DEST/$name.yaml" "$name.yaml"
done

# Файл правил стал секционным: `dotnet:` и `web:` в одном файле. Сохранённый
# набор старого формата после обновления не загрузится — сказать об этом обязан
# установщик, а не первый упавший прогон в CI.
if [ -f "$DEST/rules.yaml" ] && ! grep -q '^dotnet:' "$DEST/rules.yaml"; then
    cat >&2 <<EOF

  ВНИМАНИЕ: ваш rules.yaml в старом плоском формате, прогон его не примет.
  Перенос сохраняет комментарии и делается одной командой из клона:

      python3 $SOURCE/tools/migrate_rules.py \\
          --dotnet $DEST/rules.yaml --out $DEST/rules.yaml

  Затем допишите секцию \`web:\` — её эталон в rules.yaml.new.
EOF
fi

# --- шаблоны документов -----------------------------------------------------
# Скелет документа — настройка под проект ровно в той же мере, что и rules.yaml:
# его правят те же люди и под тот же продукт. Отсюда keep_configured.
#
# Двумя циклами, потому что обход каталога скелетов шага 2 НЕ рекурсивный:
# `templates/business` для него подкаталог и в набор шага 2 не попадает, а без
# этих файлов `docpipe business new` отказывается работать со «Скелет не найден».
for tpl in "$SOURCE"/templates/*.md "$SOURCE"/templates/examples/*.md \
           "$SOURCE"/templates/business/*.md "$SOURCE"/templates/business/examples/*.md; do
    rel="${tpl#"$SOURCE"/templates/}"
    keep_configured "$tpl" "$DEST/templates/$rel" "templates/$rel"
done

cp "$BUNDLE_SRC/README.md" "$DEST/README.md"
cp "$SOURCE/deploy/gitignore" "$DEST/.gitignore"
mkdir -p "$DEST/artifacts" "$CACHE_DIR"

# --- инструмент -------------------------------------------------------------
if [ "$TOOL" -eq 1 ]; then
    command -v uv >/dev/null || { echo "uv не найден в PATH" >&2; exit 1; }

    if [ -n "$INDEX" ]; then
        # Пишем в клон, а не в $HOME: настройки uv действуют на каталог, из
        # которого его зовут, и здесь это клон. Файл в .gitignore клона.
        sed "s|https://ЗАПОЛНИТЬ/repository/pypi/simple|$INDEX|" \
            "$SOURCE/deploy/uv.toml.example" > "$SOURCE/uv.toml"
        echo "Индекс записан в $SOURCE/uv.toml"
    fi

    # Версии берутся из uv.lock клона, а не решаются заново. Без этого
    # `uv tool install` подобрал бы свежие релизы, и окружение разошлось бы
    # с тем, на котором гонялись тесты, — молча и в удобный момент.
    #
    # --no-hashes намеренно: на внутреннем зеркале файл может быть пересобран,
    # и тогда сумма не сойдётся, хотя версия та же. Пин версии остаётся.
    constraints="$(mktemp)"
    echo "Снимаю версии из uv.lock…"
    (cd "$SOURCE" && uv export --frozen --no-dev --no-emit-project --no-hashes \
        --format requirements-txt -o "$constraints" -q) || {
        echo "Не удалось прочитать uv.lock клона" >&2; rm -f "$constraints"; exit 1
    }

    tool_flags=(--constraints "$constraints" --force)
    [ -n "$PYTHON" ] && tool_flags+=(--python "$PYTHON")

    echo "Ставлю docpipe в ${UV_TOOL_DIR:-каталог uv по умолчанию}…"
    if ! (cd "$SOURCE" && uv tool install "${tool_flags[@]}" .); then
        rm -f "$constraints"
        cat >&2 <<EOF

Установка не удалась. Три причины, по которым это обычно происходит
на закрытом контуре, — в порядке частоты:

  1. Пакеты тянутся не из внутреннего зеркала:
         $0 --repo $REPO --config-dir $CONFIG_DIR --index https://зеркало/…

  2. \`invalid peer certificate\` — сертификат TLS-прокси подписан внутренним
     удостоверяющим центром, которого нет в наборе uv. В uv.toml для этого
     стоит native-tls (системное хранилище). Если и там его нет:
         SSL_CERT_FILE=/путь/до/corp-root-ca.pem $0 --repo … --index …

  3. Не найден интерпретатор. Скачивать его запрещено намеренно:
         $0 --repo … --index … --python 3.12.13

Настройка разложена и повторным запуском не пострадает.
EOF
        exit 1
    fi
    rm -f "$constraints"

    echo -n "Проверка: docpipe "
    if command -v docpipe >/dev/null; then
        docpipe version
    else
        echo >&2
        echo "Команда docpipe не видна в PATH. Каталог запускалок — UV_TOOL_BIN_DIR;" >&2
        echo "добавьте его в PATH: export PATH=\"\${UV_TOOL_BIN_DIR:-\$HOME/.local/bin}:\$PATH\"" >&2
    fi
fi

cat <<EOF

Готово. Настройка — в $CONFIG_DIR, инструмент — на машине.

Первое, что стоит сделать: убедиться, что конфигурация читается оттуда,
откуда вы будете звать команды.

  cd $REPO
  docpipe config check --config $CONFIG_DIR/docpipe.yaml --root .

Дальше:

  docpipe scan --root . --config $CONFIG_DIR/docpipe.yaml --stats
  docpipe scan --root . --config $CONFIG_DIR/docpipe.yaml --jobs 4
  docpipe web scan --root . --config $CONFIG_DIR/docpipe.yaml --stats
EOF

if [ -n "$ENGINE" ]; then
    cat <<EOF
  docpipe graph build --root . --config $CONFIG_DIR/docpipe.yaml
EOF
else
    cat <<EOF

Движок разбора не задан (--engine), поэтому команды \`graph *\` откажутся
работать. Это законно для шагов 1, 2 и бизнес-слоя; для графа впишите путь
в ключ \`graph.engine_path\` или переустановите с --engine.
EOF
fi

echo
echo "Настройка под проект — в $CONFIG_DIR/README.md"
