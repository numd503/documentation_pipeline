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
#     ./deploy/install.sh --repo $WORK/project --config-dir docs/docpipe
#
# Набор настройки по умолчанию нейтральный (`--bundle generic`): в нём нет
# ни одного решения о чужом репозитории, их принимает настройка с ассистентом.
# Настроенный набор АС CF — `--bundle cashflow`. На закрытом контуре
# добавляются --index, --python и --engine.
#
# Скрипт идемпотентен: повторный запуск обновляет инструмент, но НЕ трогает
# настроенные yaml и правленые шаблоны — новые версии кладутся рядом как
# `*.new`. Затирать чужую настройку молча — худшее, что может сделать
# установщик: правка правил классификации это недели работы.

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUNDLE=""
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
  --bundle НАБОР       generic (по умолчанию) — нейтральная настройка, которую
                       ведёт скилл setup; cashflow — настроенный набор АС CF
  --cache-dir ПУТЬ     где держать кэши разбора и движка. АБСОЛЮТНЫЙ и вне
                       репозитория: это гигабайты машинного мусора, дереву
                       продукта они не нужны. По умолчанию свой у каждого
                       репозитория: $WORK/.docpipe/cache/<имя каталога --repo>,
                       а без $WORK — ~/.cache/docpipe/<имя каталога --repo>
  --engine ПУТЬ        путь к codebase-memory-mcp 0.6.0. Без него команды
                       `graph *` откажутся работать, остальные — нет
  --index URL          адрес внутреннего зеркала пакетов. Записывается
                       в uv.toml клона; `uv tool install` получает этот файл
                       явно, потому что сам настроек проекта не читает
  --python ПУТЬ|ВЕРСИЯ каким интерпретатором ставить, например 3.12.13.
                       Скачивать Python запрещено намеренно, поэтому на
                       закрытом контуре его надо назвать
  --no-tool            только разложить настройку, инструмент не ставить

Инструмент ставится в UV_TOOL_DIR (запускалка — в UV_TOOL_BIN_DIR). Если они
не заданы, uv возьмёт свои умолчания внутри $HOME — на системах, где работа
идёт вне $HOME, задайте их до запуска:

    export UV_TOOL_DIR=$WORK/.uv     UV_TOOL_BIN_DIR=$WORK/.uv/bin

Кэш uv лежит в клоне (<клон>/.uv-cache), а не в ~/.cache/uv: установщик
дописывает `cache-dir` в uv.toml клона, если его там нет. Перенести кэш —
поправить эту строку.
EOF
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --bundle)     [ $# -ge 2 ] || usage; BUNDLE="$2"; shift ;;
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

# Набор, названный флагом, запоминается отдельно от умолчания: обновление
# каталога, поставленного другим набором, без флага положило бы рядом `.new`
# нейтрального набора и заменило бы README — об этом надо сказать (ниже).
BUNDLE_GIVEN="$BUNDLE"
BUNDLE="${BUNDLE:-generic}"
case "$BUNDLE" in
    generic|cashflow) ;;
    *) echo "--bundle: generic или cashflow, дано: $BUNDLE" >&2; exit 2 ;;
esac
BUNDLE_SRC="$SOURCE/deploy/$BUNDLE-docspipe"

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
#
# Умолчание — своё у каждого репозитория (по имени его каталога), а не одно
# на машину. Кэш движка мост удаляет целиком перед каждой сборкой, и общий
# каталог двух репозиториев означал бы сборку одного по чужому кэшу или
# удаление кэша соседа посреди его сборки. Кэш разбора делится тоже не во всём:
# полный прогон вычищает записи чужих путей. Два репозитория с одинаковым
# именем каталога этим умолчанием не различаются — им --cache-dir задают явно.
if [ -z "$CACHE_DIR" ]; then
    if [ -n "${WORK:-}" ]; then
        CACHE_DIR="$WORK/.docpipe/cache/$(basename "$REPO")"
    else
        CACHE_DIR="$HOME/.cache/docpipe/$(basename "$REPO")"
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

echo "Набор:       $BUNDLE"
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

# Строка для JSON: обратная косая и кавычка в пути сломали бы файл настроек
# агента, и сервер не поднялся бы без внятной ошибки.
json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# Значение для правой части `s|…|…|`, которое ляжет в строку YAML в двойных
# кавычках. Два слоя, и порядок важен: сначала YAML (`\` и `"`), затем sed
# (`\`, `&` и разделитель `|`). Без второго слоя `&` в пути подставил бы
# найденный плейсхолдер, а `|` оборвал бы выражение: путь исказился бы молча
# или sed упал бы с невнятной ошибкой.
sed_value() {
    printf '%s' "$1" | sed -e 's/[\\"]/\\&/g' -e 's/[\\&|]/\\&/g'
}

# Слияние записей MCP-серверов в настройки агента. `keep_configured` здесь
# не годится: он сравнивает файл целиком, и на машине, где запись `docpipe`
# уже лежит, вторая запись ушла бы в `.new` — агент молча остался бы без
# инструментов настройки. Поэтому ставятся ровно ключи `mcpServers.*`
# из предложения, всё остальное в файле сохраняется, а пишется он, только
# если изменился. Выход 3 — файл не читается как JSON-объект.
#
# `-I` у интерпретатора: ни PYTHONPATH, ни пользовательский site, ни каталог
# запуска не должны подменить модуль `json`.
MERGE_AGENT_SETTINGS='
import json, sys
target, proposal = sys.argv[1], sys.argv[2]
with open(proposal, encoding="utf-8") as stream:
    ours = json.load(stream)["mcpServers"]
try:
    with open(target, encoding="utf-8-sig") as stream:
        settings = json.load(stream)
except (OSError, ValueError):
    sys.exit(3)
servers = settings.get("mcpServers", {}) if isinstance(settings, dict) else None
if not isinstance(servers, dict):
    sys.exit(3)
if all(servers.get(name) == entry for name, entry in ours.items()):
    print("unchanged")
    sys.exit(0)
servers.update(ours)
settings["mcpServers"] = servers
with open(target, "w", encoding="utf-8") as stream:
    json.dump(settings, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
print("updated")
'

merge_agent_settings() {
    local proposal="$1" target="$2" label="$3" python="$4"
    local stale="$target.new" outcome="" status=0

    if [ ! -e "$target" ]; then
        cp "$proposal" "$target"
        echo "  установлен: $label"
    elif [ ! -x "$python" ]; then
        echo "  Внимание: нет интерпретатора окружения инструмента ($python) —" \
             "слить записи MCP-серверов нечем, файл сравнивается целиком" >&2
        keep_configured "$proposal" "$target" "$label"
        return
    else
        outcome="$("$python" -I -c "$MERGE_AGENT_SETTINGS" "$target" "$proposal")" || status=$?
        case "$status:$outcome" in
            0:unchanged) echo "  без изменений: $label" ;;
            0:updated)   echo "  обновлён: $label — записи серверов поставлены, остальное сохранено" ;;
            3:*)
                cp "$proposal" "$stale"
                echo "  НЕ РАЗОБРАН ваш $label: это не JSON-объект (комментариев и висящих" \
                     "запятых JSON не допускает). Файл не тронут, записи серверов рядом:" \
                     "$(basename "$target").new" >&2
                return ;;
            *)  echo "Не удалось слить записи MCP-серверов в $target" >&2
                exit 1 ;;
        esac
    fi
    # `.new` прежних установок описывает то, что теперь лежит в самом файле:
    # оставленный, он выглядел бы ждущей переноса правкой.
    if [ -e "$stale" ]; then
        rm -f "$stale"
        echo "  удалён устаревший $(basename "$stale")"
    fi
}

# docpipe.yaml приходит с плейсхолдерами: входы в нём записаны короткими
# именами и переносимы как есть, а цели записи и пути от --root переносимыми
# быть не могут — их подставляем здесь. Подстановка идёт во временный файл,
# и keep_configured дальше сравнивает уже готовый результат: при повторной
# установке с теми же параметрами он совпадёт с лежащим и не создаст `.new`.
config_tmp="$(mktemp)"
config_dir_sed="$(sed_value "$CONFIG_DIR")"
cache_dir_sed="$(sed_value "$CACHE_DIR")"
engine_sed="$(sed_value "$ENGINE")"
sed -e "s|@CONFIG_DIR@|$config_dir_sed|g" \
    -e "s|@CACHE_DIR@|$cache_dir_sed|g" \
    -e "s|@ENGINE@|$engine_sed|g" \
    "$BUNDLE_SRC/docpipe.yaml" > "$config_tmp"
other_bundle=0
if [ -z "$BUNDLE_GIVEN" ] && [ -f "$DEST/docpipe.yaml" ] &&
    ! cmp -s "$config_tmp" "$DEST/docpipe.yaml"; then
    other_bundle=1
fi
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

# Лежащий docpipe.yaml не совпал с нейтральным набором, а набор не назван:
# так выглядят и правленая настройка, и каталог, поставленный другим набором.
# Во втором случае рядом легли `.new` чужого набора, а README заменён, — и
# молчать об этом нельзя: снаружи обновление выглядит удачным.
if [ "$other_bundle" -eq 1 ]; then
    cat >&2 <<EOF

  Набор не назван (--bundle), взят нейтральный generic, а лежащий docpipe.yaml
  с ним не совпадает. Если каталог ставился другим набором, повторите установку
  с ним (АС CF — --bundle cashflow): README.md уже заменён нейтральным, его
  вернёт git. Если это ваши правки — назовите --bundle generic, и строка пропадёт.
EOF
fi

# --- инструмент -------------------------------------------------------------
if [ "$TOOL" -eq 1 ]; then
    command -v uv >/dev/null || { echo "uv не найден в PATH" >&2; exit 1; }

    UV_SETTINGS="$SOURCE/uv.toml"
    if [ -n "$INDEX" ]; then
        # Пишем в клон, а не в $HOME: проектные команды uv (`uv run`, `uv sync`,
        # `uv lock`, `uv export`) читают uv.toml из каталога, откуда их зовут,
        # а зовут их из корня клона. Файл в .gitignore клона.
        sed "s|https://ЗАПОЛНИТЬ/repository/pypi/simple|$INDEX|" \
            "$SOURCE/deploy/uv.toml.example" > "$UV_SETTINGS"
        echo "Индекс записан в $UV_SETTINGS"
    fi

    # Кэш uv — в клоне, а не в ~/.cache/uv. Строка уходит в uv.toml, чтобы
    # `uv run` и `uv sync` из корня клона брали тот же кэш, что и установка.
    #
    # Дописывается В НАЧАЛО файла: ключ верхнего уровня, поставленный после
    # `[[index]]`, TOML отнёс бы к таблице зеркала, и настройка перестала бы
    # действовать без единой ошибки. Путь абсолютный: относительный uv считает
    # от текущего каталога, а не от файла настроек.
    #
    # Уже заданный `cache-dir` не трогаем: его могли перенести намеренно.
    if [ ! -f "$UV_SETTINGS" ] ||
        ! grep -Eq '^[[:space:]]*cache-dir[[:space:]]*=' "$UV_SETTINGS"; then
        uv_tmp="$(mktemp)"
        {
            echo "# Кэш uv — в клоне, а не в ~/.cache/uv. Строку дописал install.sh."
            echo "cache-dir = \"$SOURCE/.uv-cache\""
            echo
            if [ -f "$UV_SETTINGS" ]; then cat "$UV_SETTINGS"; fi
        } > "$uv_tmp"
        mv "$uv_tmp" "$UV_SETTINGS"
    fi
    UV_CACHE="$(sed -n \
        -e 's/^[[:space:]]*cache-dir[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
        -e "s/^[[:space:]]*cache-dir[[:space:]]*=[[:space:]]*'\([^']*\)'.*/\1/p" \
        "$UV_SETTINGS" | head -n 1)"
    if [ -n "$UV_CACHE" ]; then
        # Переменной, а не через --config-file: она ничего не отключает,
        # и зеркало из пользовательского или системного uv.toml остаётся в силе.
        export UV_CACHE_DIR="$UV_CACHE"
        echo "Кэш uv:      $UV_CACHE"
    else
        echo "Внимание: не разобрал cache-dir в $UV_SETTINGS — кэш uv останется на умолчании uv" >&2
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

    # `uv tool *` НЕ читает uv.toml проекта — только пользовательский
    # и системный. Без явного файла зеркало, native-tls, find-links и offline
    # из клона на установку не действуют: проверено на uv 0.11, запрос уходит
    # на pypi.org при зеркале, вписанном в uv.toml клона.
    #
    # Файл передаётся, только когда в нём описан источник пакетов: --config-file
    # ЗАМЕЩАЕТ пользовательский конфиг, а не дополняет его. Файл с одним
    # cache-dir отнял бы зеркало, настроенное на машине в ~/.config/uv.
    if grep -Eq '^[[:space:]]*(\[\[index\]\]|find-links[[:space:]]*=|offline[[:space:]]*=)' \
        "$UV_SETTINGS"; then
        tool_flags+=(--config-file "$UV_SETTINGS")
    fi

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

    # --- MCP-серверы для агента -----------------------------------------------
    # Серверов два, оба на stdio: `docpipe` — формы вопроса графа
    # (`docpipe graph serve`), `docpipe-setup` — инструменты настройки
    # (`docpipe setup serve`): ему индекс не нужен, он работает до любой
    # сборки. Запускалка, `--config`, `--root` и `cwd` у них одни.
    #
    # Агент на контуре — gigacode, форк qwen code со своими
    # каталогами: проектные MCP-серверы он берёт из `.gigacode/settings.json`
    # каталога, откуда его запустили, а запускают его из корня клона — там же
    # лежит скилл `.gigacode/skills/recon`. `.qwen/` он не читает вовсе.
    # Файл пишется в клон, а не в ~/.gigacode: пользовательский контекст
    # на контуре держат чистым.
    #
    # Пути абсолютные и машинные — файл вне git. Запускалка названа полным
    # путём: агент поднимает сервер со своим PATH, и каталога uv в нём может
    # не быть. cwd — корень продукта: `graph.out` в docpipe.yaml — цель записи
    # и отсчитывается от текущего каталога, как у `graph build`, который
    # зовут оттуда же. В уже лежащем файле ставятся только эти две записи,
    # остальное сохраняется (`merge_agent_settings`).
    #
    # JSON читает интерпретатор окружения инструмента — того, что сейчас
    # поставил `uv tool install`: он есть на машине заведомо, а `python3`
    # в PATH на контуре может не быть или оказаться древним. Путь —
    # `<uv tool dir>/docpipe/bin/python`. Из шебанга запускалки его не взять:
    # при длинном пути uv пишет туда `#!/bin/sh` с `exec`-трамплином.
    launcher="$(uv tool dir --bin)/docpipe"
    tool_python="$(uv tool dir)/docpipe/bin/python"
    config_path="$REPO/$CONFIG_DIR/docpipe.yaml"
    mcp_tmp="$(mktemp)"
    cat > "$mcp_tmp" <<EOF
{
  "mcpServers": {
    "docpipe": {
      "command": "$(json_escape "$launcher")",
      "args": [
        "graph", "serve",
        "--config", "$(json_escape "$config_path")",
        "--root", "$(json_escape "$REPO")"
      ],
      "cwd": "$(json_escape "$REPO")"
    },
    "docpipe-setup": {
      "command": "$(json_escape "$launcher")",
      "args": [
        "setup", "serve",
        "--config", "$(json_escape "$config_path")",
        "--root", "$(json_escape "$REPO")"
      ],
      "cwd": "$(json_escape "$REPO")"
    }
  }
}
EOF
    mkdir -p "$SOURCE/.gigacode"
    merge_agent_settings "$mcp_tmp" "$SOURCE/.gigacode/settings.json" \
        ".gigacode/settings.json клона (MCP-серверы docpipe и docpipe-setup)" "$tool_python"
    rm -f "$mcp_tmp"

    # До 07.10.2026 запись лежала в `.qwen/settings.json`, которого gigacode
    # не читает. Старый файл не удаляется: в нём могут быть чужие серверы, —
    # но молчать о нём нельзя, иначе человек будет чинить не тот файл.
    if [ -f "$SOURCE/.qwen/settings.json" ] && grep -q '"docpipe"' "$SOURCE/.qwen/settings.json"; then
        echo "Внимание: .qwen/settings.json клона агент контура (gigacode) не читает;" >&2
        echo "запись MCP-сервера docpipe теперь в .gigacode/settings.json, старый файл можно удалить." >&2
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

if [ "$TOOL" -eq 1 ]; then
    cat <<EOF

Агент (gigacode) видит скиллы из .gigacode/skills/ и два MCP-сервера —
графа (docpipe) и настройки (docpipe-setup), если запущен из корня клона:
$SOURCE. При включённом доверии папкам
(security.folderTrust.enabled) клон нужно один раз отметить доверенным —
до этого проектные скиллы и MCP-серверы не подключаются.
EOF
fi

echo
echo "Настройка под проект — в $CONFIG_DIR/README.md"
