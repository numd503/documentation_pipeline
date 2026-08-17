#!/usr/bin/env bash
#
# Разведка Python-сервисов АС CF: собрать факты, на которых стоит `docs/python-analysis.md`.
#
# Скрипт ТОЛЬКО ЧИТАЕТ: `git grep`, `git ls-files`, `sed`, `awk`, `file`. Ничего не пишет,
# в сеть не ходит, рабочее дерево не трогает.
#
# Запускать из корня репозитория АС CF:
#
#     bash tools/recon-python.sh > recon-python.txt 2>&1
#     bash tools/recon-python.sh ОБЛАСТЬ_ПИТОНА [ОБЛАСТЬ_БЭКА] > recon-python.txt 2>&1
#
# Пример:
#
#     bash tools/recon-python.sh \
#         sbt.cms.cashflow.subrepo/ml-services \
#         sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.NetCore
#
# Вывод ограничен по объёму намеренно — его пересылают целиком.
#
# Почему `git grep`, а не `grep -r`: в корне АС CF лежит `artifacts/bin` с выводом
# сборки, и обычный рекурсивный поиск даёт сотни ложных вхождений (зафиксировано
# в docs/findings-cashflow-registries.md).
#
# Все ловушки pathspec, найденные при написании `tools/recon-frontend.sh`, действуют
# и здесь — они перечислены рядом с соответствующими строками.

set -u

SAMPLE=12              # сколько строк примеров показывать в каждом срезе
FILE_CAP=180           # сколько строк файла печатать целиком

# Исключения путей действуют во всех поисках. `:(exclude)` — синтаксис pathspec git.
#
# ЛОВУШКА, проверенная на git 2.55: `:(exclude)**/.venv/**` НЕ исключает `.venv/`
# в корне. В pathspec без магии `:(glob)` звёздочка сама по себе перекрывает `/`,
# поэтому ведущее `**/` требует хотя бы одного каталога перед именем. Это та же
# ловушка `**`, что записана в CLAUDE.md про `fnmatch`. Отсюда две формы вместо
# одной: корневая и вложенная. `:(glob,exclude)` решил бы то же одной строкой,
# но требует более свежего git — а скрипт запускают в закрытом контуре, где
# неизвестная магия pathspec означает падение.
#
# Каталоги виртуальных окружений — не косметика: `.venv` содержит тысячи `.py`,
# и без исключения любой счётчик ниже мерит чужой код, выглядящий своим.
EXCLUDES=(
    ":(exclude).venv/**"          ":(exclude)*/.venv/**"
    ":(exclude)venv/**"           ":(exclude)*/venv/**"
    ":(exclude)env/**"            ":(exclude)*/env/**"
    ":(exclude)site-packages/**"  ":(exclude)*/site-packages/**"
    ":(exclude)__pycache__/**"    ":(exclude)*/__pycache__/**"
    ":(exclude).tox/**"           ":(exclude)*/.tox/**"
    ":(exclude)*.egg-info/**"     ":(exclude)*/*.egg-info/**"
    ":(exclude)node_modules/**"   ":(exclude)*/node_modules/**"
)

# Область разведки. Первый аргумент сужает поиски по питону, второй — по `.cs`.
#
# ЛОВУШКА, стоившая целого захода на разведке фронта 09.08.2026: без сужения
# каждый срез «показать примеры» упирается в `head -N`, который режет список
# в алфавитном порядке, и модуль, чьё имя идёт последним, выпадает из отчёта
# целиком. В выводе это неотличимо от «в модуле такого нет».
#
# ВТОРАЯ ЛОВУШКА pathspec: сужение НЕЛЬЗЯ задать префиксом в самом pathspec —
# как только положительный шаблон начинается с литерального каталога, любой
# `:(exclude)` обнуляет выборку целиком, даже тот, которому нечему совпадать
# (проверено на git 2.55). Поэтому область задаётся через `git -C`: шаблоны
# остаются теми же, что и без сужения. Плата — пути в выводе относительно
# области; поэтому область печатается в шапке, а `sed` по файлам берёт префикс
# из `$PP`.
PY_SCOPE="${1:-}";   PY_SCOPE="${PY_SCOPE%/}"
BACK_SCOPE="${2:-}"; BACK_SCOPE="${BACK_SCOPE%/}"
PP=""; [ -n "$PY_SCOPE" ] && PP="$PY_SCOPE/"   # только для sed по файлам
GP=(git -C "${PY_SCOPE:-.}")                   # поиски по питону
GB=(git -C "${BACK_SCOPE:-.}")                 # поиски по .cs

PY_ONLY=("--" "*.py" "${EXCLUDES[@]}")
# Тесты и генерат отделены: и то и другое — законный код, но по нему нельзя
# судить о продуктовом. Генерат protobuf особенно: один `*_pb2.py` даёт сотни
# классов и перекашивает любой счётчик «сколько у нас классов».
PY_SRC=("${PY_ONLY[@]}" ":(exclude)test_*.py" ":(exclude)*_test.py" ":(exclude)tests/**"
        ":(exclude)*/tests/**" ":(exclude)*_pb2.py" ":(exclude)*_pb2_grpc.py")
CS_ONLY=("--" "*.cs")

section() { printf '\n\n========== %s ==========\n' "$1"; }
sub() { printf '\n--- %s\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# Число строк, совпавших с шаблоном. Печатает 0, а не пустоту: пустота в отчёте
# неотличима от «поиск не отработал». Три штуки по областям: питон, бэк, весь
# репозиторий — чтобы область считалась ровно там, где она имеет смысл.
count()  { local n; n=$("${GP[@]}" grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
countb() { local n; n=$("${GB[@]}" grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
countr() { local n; n=$(git        grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
# Число ФАЙЛОВ, а не строк: для вопросов вида «в скольких сервисах так пишут»
# строки врут — один файл с сотней вхождений выглядит как сто файлов.
countf() { local n; n=$("${GP[@]}" grep -lE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
# Регистронезависимые варианты. Отдельные функции, а не флаг внутри шаблона:
# конструкция вида «(?i)» — это PCRE, а `grep -E` принимает её за обычную группу
# и молча ищет не то, что написано.
counti()  { local n; n=$("${GP[@]}" grep -niE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
countbi() { local n; n=$("${GB[@]}" grep -niE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }

# То же правило для списков: пустой срез называется вслух.
show() {
    local out; out="$(cat)"
    if [ -z "$out" ]; then printf '    (ничего не найдено)\n'; else printf '%s\n' "$out"; fi
}

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ОШИБКА: это не git-репозиторий. Запускать из корня репозитория АС CF."
    exit 2
fi

# Опечатка в области дала бы пустой отчёт вместо ошибки — а пустой отчёт
# пересылают дальше, и разбираться в нём будет уже не тот, кто его получил.
if [ -n "$PY_SCOPE" ] && [ ! -d "$PY_SCOPE" ]; then
    printf 'ОШИБКА: каталога «%s» нет. Где лежат .py:\n' "$PY_SCOPE"
    git ls-files -- '*.py' "${EXCLUDES[@]}" | awk -F/ '{print $1"/"$2}' \
        | sort | uniq -c | sort -rn | head -15
    exit 2
fi
if [ -n "$BACK_SCOPE" ] && [ ! -d "$BACK_SCOPE" ]; then
    printf 'ОШИБКА: каталога «%s» нет (второй аргумент — область .cs).\n' "$BACK_SCOPE"
    exit 2
fi
if [ -z "$("${GP[@]}" ls-files "${PY_ONLY[@]}" | head -1)" ]; then
    printf 'ОШИБКА: под областью «%s» нет ни одного .py. Где они есть:\n' "${PY_SCOPE:-.}"
    git ls-files -- '*.py' "${EXCLUDES[@]}" | awk -F/ '{print $1"/"$2}' \
        | sort | uniq -c | sort -rn | head -15
    exit 2
fi

printf 'Разведка Python. Корень: %s\n' "$(git rev-parse --show-toplevel)"
printf 'Коммит: %s\n' "$(git rev-parse --short HEAD 2>/dev/null || echo '—')"
printf 'Отслеживаемых файлов: %s\n' "$(git ls-files | wc -l)"
printf 'Область питона: %s\n' "${PY_SCOPE:-весь репозиторий}"
printf 'Область бэка:   %s\n' "${BACK_SCOPE:-весь репозиторий}"
[ -n "$PY_SCOPE" ] && printf 'Пути в срезах по питону — относительно области.\n'


# ======================================================================================
section "0. Есть ли питон вообще и какого он размера"

sub "файлов .py (без .venv, site-packages, __pycache__)"
"${GP[@]}" ls-files "${PY_ONLY[@]}" | wc -l
# Отбор тестов идёт `grep`-ом по готовому списку, а не pathspec-ом, и на то две
# причины, обе проверены на git 2.55 в этом репозитории:
#
#   git ls-files -- 'tests/**'        → 170  (считает и фикстуры .cs/.ts/.json:
#                                             «тестов» вышло больше, чем .py всего)
#   git ls-files -- 'tests/**/*.py'   → 0    (`**` в pathspec НЕ значит «ноль или
#                                             больше сегментов» — та же ловушка,
#                                             что у `fnmatch` в CLAUDE.md)
#
# Число, превышающее собственный знаменатель, и ноль вместо шестидесяти файлов —
# оба выглядят как факт о репозитории, а не как дефект среза.
sub "из них тестов"
"${GP[@]}" ls-files "${PY_ONLY[@]}" \
    | grep -E '(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$' | wc -l
sub "из них генерата protobuf (*_pb2.py, *_pb2_grpc.py)"
"${GP[@]}" ls-files -- '*_pb2.py' '*_pb2_grpc.py' "${EXCLUDES[@]}" | wc -l
sub "файлов .pyi (заглушки типов) и .ipynb (ноутбуки — отдельный формат)"
"${GP[@]}" ls-files -- '*.pyi' '*.ipynb' "${EXCLUDES[@]}" | wc -l
sub "строк в продуктовых .py (грубая оценка объёма)"
"${GP[@]}" ls-files "${PY_SRC[@]}" | tr '\n' '\0' | xargs -0 -r wc -l 2>/dev/null | tail -1 | show

# Считается по всему репозиторию даже при заданной области: по этому списку
# выбирают саму область, и он же показывает, сколько сервисов осталось за кадром.
# `NF>1` обязателен: у файла в корне репозитория второго поля нет, и склейка
# `$1"/"$2` даёт строку вида `trim-manifest.py/` — каталог, которого не существует.
# В списке, по которому выбирают область разведки, такая строка сбивает с толку.
sub "верхнеуровневые каталоги, где лежат .py, — весь репозиторий (топ-20)"
git ls-files -- '*.py' "${EXCLUDES[@]}" | awk -F/ '{print (NF > 1 ? $1"/"$2 : "(корень)")}' \
    | sort | uniq -c | sort -rn | head -20 | show

sub "ЕСТЬ ЛИ .venv / site-packages ПРЯМО В ДЕРЕВЕ (это меняет умолчания обхода)"
git ls-files | grep -E '(^|/)(\.venv|venv|site-packages)/' | head -5 | show
printf 'всего таких путей '; countr '.' -- '*/site-packages/*' '*/.venv/*'


# ======================================================================================
section "1. Раскладка модулей и чем объявлен сервис — ВОПРОСЫ №2 и №3"
note "У .NET модуль ⇔ .csproj, у Angular ⇔ каталог-граница. У питона однозначного"
note "файла нет вовсе, и правило придётся строить из того, что реально лежит."
note ""
note "ОБЩЕГО РОДИТЕЛЯ У СЕРВИСОВ НЕТ: со слов владельца, часть модулей лежит"
note "отдельно — inner_debts на одном уровне с grid, а не под ним. Правило"
note "«модуль ⇔ каталог под grid/» нашло бы половину сервисов, и в отчёте это"
note "выглядело бы как «в остальных ничего нет»."

sub "дерево первого уровня области: сколько .py в каждом каталоге"
"${GP[@]}" ls-files "${PY_ONLY[@]}" | awk -F/ '{print (NF > 1 ? $1 : "(файлы в корне области)")}' \
    | sort | uniq -c | sort -rn | head -30 | show

sub "дерево второго уровня: где именно лежит grid и что рядом с ним"
"${GP[@]}" ls-files "${PY_ONLY[@]}" | awk -F/ 'NF > 2 {print $1"/"$2}' \
    | sort | uniq -c | sort -rn | head -30 | show

# Прямой ответ на «где grid и кто его соседи»: без него предыдущие два среза
# приходится читать глазами и сопоставлять вручную.
sub "каталоги с именем grid и их СОСЕДИ по уровню (ключевая проверка раскладки)"
GRID_DIRS=$("${GP[@]}" ls-files "${PY_ONLY[@]}" | tr '/' '\n' | grep -c '^grid$' 2>/dev/null || true)
printf '    вхождений сегмента «grid» в путях: %s\n' "$GRID_DIRS"
for d in $("${GP[@]}" ls-files "${PY_ONLY[@]}" | grep -oE '^([^/]+/)*grid/' | sort -u | head -3); do
    parent=$(dirname "${d%/}")
    note "grid найден как: $d   (родитель: $parent)"
    note "  соседи по уровню родителя:"
    if [ "$parent" = "." ]; then
        "${GP[@]}" ls-files "${PY_ONLY[@]}" | awk -F/ 'NF > 1 {print "      "$1}' | sort -u | head -25
    else
        "${GP[@]}" ls-files "${PY_ONLY[@]}" | grep -E "^$parent/" \
            | sed "s|^$parent/||" | awk -F/ 'NF > 1 {print "      "$1}' | sort -u | head -25
    fi
done
[ -z "$("${GP[@]}" ls-files "${PY_ONLY[@]}" | grep -oE '^([^/]+/)*grid/' | head -1)" ] \
    && note "(каталога grid в области не нашлось — смотрите два среза выше)"

sub "каталоги с __init__.py — по ним видно границы пакетов (топ-30 по глубине 1–2)"
"${GP[@]}" ls-files -- '*__init__.py' "${EXCLUDES[@]}" | sed 's|/__init__\.py$||' \
    | awk -F/ 'NF <= 2' | sort -u | head -30 | show

sub "точки запуска: __main__.py, main.py, *_service.py, app.py, server.py"
"${GP[@]}" ls-files -- '*__main__.py' '*main.py' '*_service.py' '*app.py' '*server.py' "${EXCLUDES[@]}" \
    | head -30 | show

sub "pyproject.toml"
"${GP[@]}" ls-files -- '*pyproject.toml' "${EXCLUDES[@]}" | head -30 | show
sub "setup.py / setup.cfg"
"${GP[@]}" ls-files -- '*setup.py' '*setup.cfg' "${EXCLUDES[@]}" | head -30 | show
sub "requirements*.txt / Pipfile / poetry.lock"
"${GP[@]}" ls-files -- '*requirements*.txt' '*Pipfile' '*poetry.lock' "${EXCLUDES[@]}" | head -30 | show
sub "Dockerfile / docker-compose (по ним видно, ОТКУДА запускается сервис, то есть корень sys.path)"
"${GP[@]}" ls-files -- '*Dockerfile*' '*docker-compose*' "${EXCLUDES[@]}" | head -20 | show

sub "СОДЕРЖИМОЕ первых двух Dockerfile: WORKDIR, CMD, ENTRYPOINT — корень импорта"
for f in $("${GP[@]}" ls-files -- '*Dockerfile*' "${EXCLUDES[@]}" | head -2); do
    printf '\n>>>>> %s\n' "$PP$f"
    grep -nE '^(FROM|WORKDIR|COPY|ENV|CMD|ENTRYPOINT|RUN pip)' "$PP$f" 2>/dev/null | head -25
done

sub "раскладка src/ (влияет на корень импорта)"
printf 'файлов под src/     '; count '.' -- 'src/**/*.py' '*/src/**/*.py'
printf 'файлов под app/     '; count '.' -- 'app/**/*.py' '*/app/**/*.py'


# ======================================================================================
section "2. tornado_service.py — РЕЕСТР ПИТОН-МОДУЛЕЙ, ВОПРОС №1, ГЛАВНЫЙ"
note "Со слов владельца: «есть tornado_service.py, где регистрируются все питон-модули»."
note "Это не точка входа, а РЕЕСТР — та же конструкция, что XML-реестры АС CF на"
note "стороне .NET, где литерала конкретного workflow в C# структурно не существует."
note ""
note "Форма регистрации и есть объём задачи: словарь имён, список кортежей, цепочка"
note "register(...) и декоратор @register('имя') — четыре разные реализации."
note "Имя зарегистрированного модуля — кандидат в ГЛАВНЫЙ якорь питон-стороны."

sub "файлы-кандидаты в реестр"
"${GP[@]}" ls-files "${PY_ONLY[@]}" | grep -iE '(^|/)[a-z_]*tornado[a-z_]*\.py$|(^|/)[a-z_]*service\.py$|(^|/)(registry|register|modules|routes)\.py$' \
    | head -20 | show

# Файл печатается ЦЕЛИКОМ и безусловно, а не «первые найденные по алфавиту».
# На разведке фронта ровно этот срез однажды не напечатал файл, ради которого
# писался весь раздел: он оказался шестым в списке, а печатались первые четыре.
REGISTRY=$("${GP[@]}" ls-files "${PY_ONLY[@]}" | grep -iE '(^|/)tornado_service\.py$' | head -2)
[ -z "$REGISTRY" ] && REGISTRY=$("${GP[@]}" ls-files "${PY_ONLY[@]}" \
    | grep -iE '(^|/)[a-z_]*tornado[a-z_]*\.py$' | head -2)

sub "СОДЕРЖИМОЕ реестра (главный срез всей разведки)"
if [ -n "$REGISTRY" ]; then
    for f in $REGISTRY; do
        printf '\n>>>>> %s   (строк: %s)\n' "$PP$f" "$(wc -l < "$PP$f" 2>/dev/null)"
        sed -n '1,400p' "$PP$f"
    done
else
    note "(файла tornado_service.py не нашлось — смотрите список кандидатов выше"
    note " и срез «где вообще регистрируют» ниже)"
fi

# Импорты реестра отвечают сразу на два вопроса: какие модули он подключает
# и ГДЕ ОНИ ФИЗИЧЕСКИ ЛЕЖАТ. Второе — прямой ответ на вопрос №2 про раскладку.
sub "ИМПОРТЫ реестра — по ним видно, где лежат зарегистрированные модули"
if [ -n "$REGISTRY" ]; then
    for f in $REGISTRY; do
        printf '\n>>>>> %s\n' "$PP$f"
        grep -nE '^\s*(from|import)\s' "$PP$f" 2>/dev/null | head -60
    done
else
    note "(реестр не найден)"
fi

sub "формы регистрации по всей области — какую придётся разбирать"
printf 'словарь {"имя": Класс}          '; count '["'"'"'][a-z_][a-z0-9_]*["'"'"']\s*:\s*[A-Z_]\w*' "${PY_SRC[@]}"
printf 'вызовы register(/add_module(    '; count '\.?(register|add_module|add_service|add_handler)\(' "${PY_SRC[@]}"
printf 'декоратор @register(            '; count '@\w*register\w*\(' "${PY_SRC[@]}"
printf 'MODULES / SERVICES как константа '; count '^\s*(MODULES|SERVICES|HANDLERS|APPS|REGISTRY)\s*[:=]' "${PY_SRC[@]}"

sub "примеры строк регистрации по всей области"
"${GP[@]}" grep -nE '\.?(register|add_module|add_service)\(|^\s*(MODULES|SERVICES|REGISTRY)\s*[:=]|@\w*register\w*\(' "${PY_SRC[@]}" \
    | sed 's/^\(.\{140\}\).*/\1…/' | head -20 | show

# Имена модулей ищутся ТОЛЬКО в самом реестре, а не по всей области. По области
# тот же шаблон ловит любой ключ любого словаря: на смоук-прогоне первыми строками
# встали `kind`, `identifier`, `json` — счётчик, который выглядит убедительно
# и не значит ничего. Ровно та ошибка, из-за которой на разведке фронта
# «литералы ко всем вызовам» пришлось считать заново.
sub "имена, объявленные в реестре (кандидаты в якоря)"
if [ -n "$REGISTRY" ]; then
    for f in $REGISTRY; do
        grep -hoE '["'"'"'][a-z][a-z0-9_]{2,}["'"'"']' "$PP$f" 2>/dev/null \
            | tr -d "'\"" | sort -u | head -40
    done | show
else
    note "(реестр не найден — без него срез считал бы ключи всех словарей области)"
fi


# ======================================================================================
section "3. Версия языка и стек — ВОПРОС №13"
note "Питона 2 в репозитории нет (подтверждено владельцем). Сейчас в основном 3.9,"
note "скоро поднимут до 3.14, то есть какое-то время в дереве будет и то, и другое."
note ""
note "Что проверено на tree-sitter-python 0.25.0: всё от 3.8 до 3.12 разбирается"
note "с НУЛЁМ ошибок, включая match/case и match как имя переменной. А вот PEP 696"
note "(умолчания TypeVar, 3.13: def f[T = int]) даёт ошибку разбора — то есть переезд"
note "на 3.13+ потребует планового апгрейда грамматики, и это видно в манифесте."

sub "объявленная версия"
"${GP[@]}" grep -hnE 'python_requires|requires-python' -- '*setup.py' '*setup.cfg' '*pyproject.toml' \
    | head -"$SAMPLE" | show
sub "базовый образ (FROM python:…)"
"${GP[@]}" grep -hniE '^FROM .*python' -- '*Dockerfile*' | sort -u | head -10 | show

sub "что реально используется из синтаксиса (по этому видно фактическую версию)"
printf 'f-строки (3.6+)                     '; count "f['\"]" "${PY_SRC[@]}"
printf 'walrus := (3.8+)                    '; count ':=' "${PY_SRC[@]}"
printf 'list[str] в аннотации (3.9+)        '; count ':\s*(list|dict|set|tuple)\[' "${PY_SRC[@]}"
printf 'match/case (3.10+)                  '; count '^\s*match\s+\w+\s*:\s*$' "${PY_SRC[@]}"
printf 'X | None в аннотации (3.10+)        '; count ':\s*\w+\s*\|\s*(None|\w+)' "${PY_SRC[@]}"
printf 'except* (3.11+)                     '; count 'except\*' "${PY_SRC[@]}"
printf 'type X = … (3.12+)                  '; count '^\s*type\s+\w+\s*=' "${PY_SRC[@]}"
printf 'class X[T] / def f[T] (3.12+)       '; count '^\s*(class|def)\s+\w+\[' "${PY_SRC[@]}"
printf 'PEP696 [T = …] (3.13+, ГРАММАТИКА НЕ ЗНАЕТ) '; count '^\s*(class|def)\s+\w+\[[^]]*=' "${PY_SRC[@]}"
printf 't-строки (3.14+)                    '; count 't"[^"]*\{' "${PY_SRC[@]}"

# Страховка, а не проверка: владелец сказал, что питона 2 нет. Оставлена потому,
# что стоит одну строку, а цена пропущенного питона-2 — неверные символы молча.
#
# ВАЖНО ПРО ЛОЖНЫЕ СРАБАТЫВАНИЯ: `except ValueError, TypeError:` — это Python 3.14
# (PEP 758), и грамматика разбирает его В ТОТ ЖЕ УЗЕЛ, что питон-2-шный
# `except ValueError, e:` — обе формы дают 0 ошибок. После переезда на 3.14
# эта строка начнёт срабатывать законно. Смотреть на неё только вместе с версией.
sub "страховка: маркеры питона 2 (ожидаются нули)"
printf 'print без скобок                '; count '^\s*print\s+[^(=]' "${PY_SRC[@]}"
printf 'except X, e:  (или 3.14 PEP 758) '; count 'except\s+\w+\s*,\s*\w+\s*:' "${PY_SRC[@]}"
printf '.iteritems() / basestring       '; count '\.iter(items|keys|values)\(|\bbasestring\b' "${PY_SRC[@]}"

sub "фреймворки и библиотеки в requirements (без обрезки: обрезка уже стоила стека на разведке фронта)"
"${GP[@]}" grep -hiE '^(tornado|flask|fastapi|aiohttp|django|celery|kafka|confluent|aiokafka|grpcio|requests|httpx|sqlalchemy|pandas|numpy|scikit|catboost|xgboost|pyignite)' \
    -- '*requirements*.txt' | sed 's/[[:space:]]*$//' | sort -u | head -40 | show

sub "они же в pyproject.toml"
"${GP[@]}" grep -hiE '"(tornado|flask|fastapi|aiohttp|django|celery|grpcio|pyignite)' -- '*pyproject.toml' \
    | sed 's/^[[:space:]]*//' | sort -u | head -20 | show



# ======================================================================================
section "4. Кодировки — ВОПРОС №14"
note "PEP 263: '# -*- coding: cp1251 -*-' в первых двух строках. Файл в cp1251,"
note "прочитанный как utf-8, даст мусор в docstring — то есть в описании символа."

printf 'объявлений coding, не utf-8   '; count '#.*coding[:=]\s*(cp1251|windows-1251|koi8|latin|iso-8859)' "${PY_ONLY[@]}"
sub "какие кодировки объявлены"
"${GP[@]}" grep -hoiE 'coding[:=][[:space:]]*[-_a-z0-9]+' "${PY_ONLY[@]}" | sort | uniq -c | sort -rn | head | show

sub "файлы, которые не являются валидным UTF-8 (проверка через file, первые 10)"
"${GP[@]}" ls-files "${PY_ONLY[@]}" | head -3000 | while read -r f; do
    enc=$(file -b --mime-encoding "$PP$f" 2>/dev/null)
    case "$enc" in
        utf-8|us-ascii|binary) ;;
        *) printf '    %-70s %s\n' "$f" "$enc" ;;
    esac
done | head -10 | show


# ======================================================================================
section "5. TORNADO: как объявлены маршруты — ВОПРОСЫ №5 и №6, БЛОКИРУЮЩИЕ"
note "Маршрут tornado — РЕГУЛЯРНОЕ ВЫРАЖЕНИЕ, а не шаблон. Общая нормализация"
note "маршрута ломается на нём тремя способами (см. §3 аналитики), поэтому важно,"
note "сколько маршрутов содержат группы, и собирается ли таблица из кусков."

sub "счётчики объявления приложения"
printf 'tornado.web.Application( / Application(  '; count '\b(tornado\.web\.)?Application\(' "${PY_SRC[@]}"
printf 'tornado.web.url( / url(                  '; count '\b(tornado\.web\.)?url\(\s*r?["'"'"']' "${PY_SRC[@]}"
printf 'add_handlers(                            '; count '\.add_handlers\(' "${PY_SRC[@]}"
printf 'классов-наследников RequestHandler       '; count 'class\s+\w+\s*\([^)]*RequestHandler' "${PY_SRC[@]}"
printf 'WebSocketHandler                         '; count 'class\s+\w+\s*\([^)]*WebSocketHandler' "${PY_SRC[@]}"
printf 'файлов с Application( (сколько сервисов) '; countf '\b(tornado\.web\.)?Application\(' "${PY_SRC[@]}"

sub "счётчики методов обработчика (это и есть будущие Endpoint)"
printf 'def get(self      '; count '(async\s+)?def\s+get\s*\(\s*self' "${PY_SRC[@]}"
printf 'def post(self     '; count '(async\s+)?def\s+post\s*\(\s*self' "${PY_SRC[@]}"
printf 'def put(self      '; count '(async\s+)?def\s+put\s*\(\s*self' "${PY_SRC[@]}"
printf 'def delete(self   '; count '(async\s+)?def\s+delete\s*\(\s*self' "${PY_SRC[@]}"
printf 'def patch(self    '; count '(async\s+)?def\s+patch\s*\(\s*self' "${PY_SRC[@]}"
printf 'SUPPORTED_METHODS '; count 'SUPPORTED_METHODS' "${PY_SRC[@]}"

sub "СОДЕРЖИМОЕ первого файла с Application( — главный срез раздела"
for f in $("${GP[@]}" grep -lE '\b(tornado\.web\.)?Application\(' "${PY_SRC[@]}" | head -2); do
    printf '\n>>>>> %s\n' "$PP$f"; sed -n "1,${FILE_CAP}p" "$PP$f"
done

sub "все строки, похожие на объявление маршрута (кортеж 'строка, Handler')"
"${GP[@]}" grep -nE '\(\s*r?["'"'"'][^"'"'"']*["'"'"']\s*,\s*[A-Z]\w*Handler' "${PY_SRC[@]}" \
    | sed 's/^\(.\{140\}\).*/\1…/' | head -25 | show

sub "СКОЛЬКО маршрутов содержат регулярные группы (их придётся переводить в шаблон)"
printf 'маршрутов с группой ( … )      '; count 'r?["'"'"'][^"'"'"']*\([^)]*\)[^"'"'"']*["'"'"']\s*,\s*\w*Handler' "${PY_SRC[@]}"
printf 'именованных групп (?P<…>)      '; count '\(\?P<' "${PY_SRC[@]}"
printf 'некаптурящих групп (?:…)       '; count '\(\?:' "${PY_SRC[@]}"
printf 'якорей $ в маршрутах           '; count '["'"'"'][^"'"'"']*\$["'"'"']\s*,\s*\w*Handler' "${PY_SRC[@]}"

sub "СОБИРАЕТСЯ ЛИ таблица из кусков (аналог спреда в Angular — §5.8 аналитики)"
printf 'Application(… + …)  конкатенация  '; count 'Application\(\s*\[?[^)]*\+' "${PY_SRC[@]}"
printf 'handlers = handlers +             '; count 'handlers\s*(\+=|=\s*\w+\s*\+)' "${PY_SRC[@]}"
printf 'HANDLERS/ROUTES/URLS как константа '; count '^\s*(HANDLERS|ROUTES|URLS|handlers|routes|urls)\s*[:=]\s*\[' "${PY_SRC[@]}"
sub "примеры"
"${GP[@]}" grep -nE '^\s*(HANDLERS|ROUTES|URLS|handlers|routes|urls)\s*[:=]\s*\[|Application\(\s*\[?[^)]*\+' "${PY_SRC[@]}" \
    | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE" | show

sub "какие префиксы путей встречаются (первые два сегмента)"
"${GP[@]}" grep -hoE '["'"'"'`]\^?/?api/[a-zA-Z0-9_-]+/?[a-zA-Z0-9_-]*' "${PY_SRC[@]}" 2>/dev/null \
    | tr -d "'\"\`^" | sed 's|^/||' | awk -F/ '{print $1"/"$2}' \
    | sort | uniq -c | sort -rn | head -20 | show
note 'Маршруты без префикса api/ сюда не попадают — смотри срез «все строки» выше.'


# ======================================================================================
section "6. Другие веб-фреймворки — ВОПРОС №8"
note "Если их заметно, ставка только на tornado неверна, и маршрут придётся"
note "извлекать ещё и из декоратора метода (см. ловушку 5.1: декоратор — РОДИТЕЛЬ)."

printf 'flask: @app.route / @bp.route   '; count '@\w+\.route\(' "${PY_SRC[@]}"
printf 'fastapi: @app.get/post/…        '; count '@\w+\.(get|post|put|delete|patch)\(' "${PY_SRC[@]}"
printf 'fastapi: APIRouter(             '; count 'APIRouter\(' "${PY_SRC[@]}"
printf 'aiohttp: router.add_get/post    '; count '\.router\.add_(get|post|put|delete|route)\(' "${PY_SRC[@]}"
printf 'django: urlpatterns             '; count 'urlpatterns\s*=' "${PY_SRC[@]}"
printf 'grpc: add_.*Servicer_to_server  '; count 'add_\w+Servicer_to_server' "${PY_SRC[@]}"
sub "примеры декораторов-маршрутов, если они есть"
"${GP[@]}" grep -nE '@\w+\.(route|get|post|put|delete|patch)\(' "${PY_SRC[@]}" \
    | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE" | show


# ======================================================================================
section "7. Точки входа НЕ по HTTP — ВОПРОС №7"
note "Если их много, HTTP-эндпоинт не единственная точка контракта, и якорей"
note "бизнес-слою нужно больше одного вида (ср. JOBTITLE и Id@Version у .NET)."

printf 'celery: @app.task / @shared_task '; count '@(\w+\.)?(task|shared_task)\(' "${PY_SRC[@]}"
printf 'kafka: Consumer( / KafkaConsumer '; count '(KafkaConsumer|Consumer)\(' "${PY_SRC[@]}"
printf 'подписка на топик                '; count '\.(subscribe|consume)\(' "${PY_SRC[@]}"
printf 'if __name__ == "__main__"        '; count '__name__\s*==\s*.__main__.' "${PY_SRC[@]}"
printf 'argparse / click / typer         '; count '(argparse\.ArgumentParser|@click\.|typer\.Typer)' "${PY_SRC[@]}"
printf 'schedule / cron / APScheduler    '; count '(APScheduler|BackgroundScheduler|schedule\.every|crontab)' "${PY_SRC[@]}"
printf 'entry_points / console_scripts   '; count '(entry_points|console_scripts)' -- '*setup.py' '*setup.cfg' '*pyproject.toml'

sub "имена топиков и очередей, если они литеральные"
"${GP[@]}" grep -hoE '(topic|queue|TOPIC|QUEUE)\s*[:=]\s*["'"'"'][^"'"'"']+["'"'"']' "${PY_SRC[@]}" \
    | sort -u | head -"$SAMPLE" | show


# ======================================================================================
section "8. СТОРОНА .NET: чем зовут питон — ВОПРОС №4, БЛОКИРУЮЩИЙ"
note "Извлечения исходящих HTTP-вызовов на стороне .NET в docpipe СЕГОДНЯ НЕТ вообще."
note "Этот раздел решает, сколько его писать и хватит ли ключа (метод + маршрут)."
note ""
note "ВНИМАНИЕ: со слов владельца, модули «каким-то образом вызываются через Grid-"
note "сервисы». Если это так, ключом связи может оказаться НЕ HTTP-маршрут, а ИМЯ"
note "МОДУЛЯ из реестра (§2). Сравнивайте два блока ниже: если grid-блок полон,"
note "а HTTP-блок пуст, то HTTP-экстрактор на .NET не нужен вовсе."

sub "БЛОК GRID: чем .NET ходит в grid-сервисы"
printf 'Ignite / GridGain (тип или using)   '; countb '\b(Ignite|GridGain|IIgnite)\b' "${CS_ONLY[@]}"
printf 'ICompute / GetCompute / Broadcast   '; countb '\b(ICompute|GetCompute|Broadcast|AffinityCall|AffinityRun)\b' "${CS_ONLY[@]}"
printf 'ExecuteAsync / Execute<             '; countb '\.Execute(Async)?[<(]' "${CS_ONLY[@]}"
printf 'GetOrCreateCache / ICache<          '; countb '\b(GetOrCreateCache|ICache<)\b' "${CS_ONLY[@]}"
printf 'ServiceProxy / GetServiceProxy      '; countb 'GetServiceProxy|ServiceProxy' "${CS_ONLY[@]}"
printf 'слово python/tornado рядом с grid   '; countbi '(grid|ignite).*(python|tornado)|(python|tornado).*(grid|ignite)' "${CS_ONLY[@]}"

sub "примеры вызова grid-сервисов (по ним видно, чем именован адресат)"
"${GB[@]}" grep -nE 'GetServiceProxy|\.Execute(Async)?[<(]|AffinityCall|Broadcast\(' "${CS_ONLY[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE" | show

# Обратная сторона того же вопроса: видит ли grid сам питон. Если в питоне есть
# клиент Ignite, цепочка может идти и в другую сторону.
sub "БЛОК GRID со стороны питона: есть ли клиент ignite/grid"
printf 'pyignite / ignite в питоне   '; counti '\b(pyignite|ignite|gridgain)\b' "${PY_SRC[@]}"
printf 'thrift / zeromq / rabbit     '; counti '\b(thrift|zmq|zeromq|pika|rabbit)\b' "${PY_SRC[@]}"
sub "примеры"
"${GP[@]}" grep -niE '\b(pyignite|ignite|gridgain|thrift|zmq)\b' "${PY_SRC[@]}" \
    | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE" | show

sub "БЛОК HTTP: счётчики клиентов"
printf 'HttpClient как тип/поле            '; countb '\bHttpClient\b' "${CS_ONLY[@]}"
printf 'IHttpClientFactory                 '; countb 'IHttpClientFactory' "${CS_ONLY[@]}"
printf 'CreateClient("имя")                '; countb 'CreateClient\(' "${CS_ONLY[@]}"
printf 'AddHttpClient<…> в DI              '; countb 'AddHttpClient' "${CS_ONLY[@]}"
printf 'BaseAddress =                      '; countb 'BaseAddress\s*=' "${CS_ONLY[@]}"
printf 'GetAsync / PostAsync / PutAsync    '; countb '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|SendAsync)\(' "${CS_ONLY[@]}"
printf 'GetFromJsonAsync / PostAsJsonAsync '; countb '\.(GetFromJsonAsync|PostAsJsonAsync|PutAsJsonAsync)\(' "${CS_ONLY[@]}"
printf 'RestSharp (RestClient/RestRequest) '; countb '\b(RestClient|RestRequest)\b' "${CS_ONLY[@]}"
printf 'Refit ([Get("…")] на интерфейсе)   '; countb '\[(Get|Post|Put|Delete|Patch)\("' "${CS_ONLY[@]}"
printf 'Flurl                              '; countb '\bFlurl\b' "${CS_ONLY[@]}"

sub "СКОЛЬКО ВЫЗОВОВ С ЛИТЕРАЛЬНЫМ ПУТЁМ — ВОПРОС №9"
note 'Отношение «с литералом» ко «всем вызовам» — верхняя оценка покрытия связи.'
note 'На фронте ровно это число решило, хватит ли схемы «литерал в сервисе».'
printf 'всего вызовов (по глаголам выше)         '; countb '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|SendAsync|GetFromJsonAsync|PostAsJsonAsync)\(' "${CS_ONLY[@]}"
printf '  первый аргумент — строка в кавычках    '; countb '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|GetFromJsonAsync|PostAsJsonAsync)\(\s*"' "${CS_ONLY[@]}"
printf '  первый аргумент — интерполяция $"…"    '; countb '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|GetFromJsonAsync|PostAsJsonAsync)\(\s*\$"' "${CS_ONLY[@]}"
printf '  первый аргумент — переменная           '; countb '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|GetFromJsonAsync|PostAsJsonAsync)\(\s*[A-Za-z_]' "${CS_ONLY[@]}"

sub "примеры: литеральный путь"
"${GB[@]}" grep -nE '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|GetFromJsonAsync|PostAsJsonAsync)\(\s*[$]?"' "${CS_ONLY[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE" | show

sub "примеры: путь в переменной — вот это автоматически не восстановится"
"${GB[@]}" grep -nE '\.(GetAsync|PostAsync|PutAsync|DeleteAsync|SendAsync)\(\s*[A-Za-z_][A-Za-z0-9_.]*\s*[,)]' "${CS_ONLY[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE" | show

sub "какие префиксы путей встречаются в .cs (сравнить с гистограммой §5)"
"${GB[@]}" grep -hoE '"/?api/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+' "${CS_ONLY[@]}" 2>/dev/null \
    | tr -d '"' | sed 's|^/||' | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn | head -20 | show
note 'Расхождение с гистограммой §5 означает префикс, дописываемый базовым адресом.'

sub "упоминания питона/ml прямо в .cs (по ним видно, как сервис называют)"
"${GB[@]}" grep -nirE '(python|tornado|ml[-_]?service|scoring|predict)' "${CS_ONLY[@]}" \
    | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE" | show


# ======================================================================================
section "9. Где лежит базовый адрес питон-сервиса — вторая половина вопроса №4"
note "Если в коде остаётся только путь, а хост приходит из конфигурации — ключ связи"
note "(метод + маршрут) сходится. Если путь собирается из настроек целиком — нужен"
note "аналог web.url_rewrite для .NET, и это отдельное решение."

sub "ключи с адресами в appsettings / конфигах"
git grep -hniE '"[^"]*(python|ml|scoring|predict|tornado)[^"]*(url|uri|address|host|endpoint)[^"]*"\s*:' \
    -- '*appsettings*.json' '*.config' '*.yaml' '*.yml' | head -20 | show
sub "любые *Url/*Uri/*Address в appsettings (первые 20)"
git grep -hoE '"[A-Za-z]*(Url|Uri|Address|Endpoint|Host)"\s*:\s*"[^"]*"' -- '*appsettings*.json' \
    | sort -u | head -20 | show
sub "переменные окружения с адресами (Dockerfile, compose, helm)"
git grep -hniE '(URL|URI|HOST|ENDPOINT)=' -- '*Dockerfile*' '*docker-compose*' '*.env*' '*values*.yaml' \
    | head -"$SAMPLE" | show
sub "порт, на котором слушает питон (listen/bind в .py)"
"${GP[@]}" grep -nE '\.(listen|bind)\(|port\s*=\s*[0-9]{4}' "${PY_SRC[@]}" | head -"$SAMPLE" | show


# ======================================================================================
section "10. Аннотации типов — ВОПРОС №10"
note "От покрытия зависит, будет ли граф зависимостей вообще: на фронте зависимость"
note "берётся из типизированного параметра конструктора, а в питоне тип необязателен."

printf 'def … всего                        '; count '^\s*(async\s+)?def\s+\w+' "${PY_SRC[@]}"
printf '  из них с аннотацией возврата ->  '; count '^\s*(async\s+)?def\s+\w+\([^)]*\)\s*->' "${PY_SRC[@]}"
printf 'def __init__ всего                 '; count '^\s*def\s+__init__' "${PY_SRC[@]}"
printf '  из них с типизированным параметром '; count '^\s*def\s+__init__\s*\(\s*self\s*,[^)]*:\s*\w' "${PY_SRC[@]}"
printf 'self.x = … в теле                  '; count '^\s+self\.\w+\s*=' "${PY_SRC[@]}"
printf 'self.x: T = … (типизированное поле) '; count '^\s+self\.\w+\s*:\s*\w+\s*=' "${PY_SRC[@]}"
printf 'dataclass / pydantic BaseModel     '; count '(@dataclass|\(BaseModel\))' "${PY_SRC[@]}"

sub "примеры конструкторов — по ним видно, есть ли внедрение зависимостей вообще"
"${GP[@]}" grep -nE '^\s*def\s+__init__\s*\(\s*self\s*,' "${PY_SRC[@]}" \
    | sed 's/^\(.\{140\}\).*/\1…/' | head -"$SAMPLE" | show


# ======================================================================================
section "11. Классы против функций, видимость — ВОПРОС №11"
note "Модель узла типоцентрична: узел — это класс (или функция/константа)."
note "Если основной объём — модульные функции, встаёт вопрос об узле-модуле (§4.2)."
note "require_public из секции dotnet здесь МИНА: у питона нет модификатора public,"
note "и перенесённое значение дало бы пустое дерево без единого сообщения (§4.5)."

printf 'class …                     '; count '^\s*class\s+\w+' "${PY_SRC[@]}"
printf 'def на верхнем уровне       '; count '^(async\s+)?def\s+\w+' "${PY_SRC[@]}"
printf 'def с ведущим _             '; count '^(async\s+)?def\s+_\w+' "${PY_SRC[@]}"
printf 'class с ведущим _           '; count '^\s*class\s+_\w+' "${PY_SRC[@]}"
printf '__all__ = […]               '; count '__all__\s*=' "${PY_SRC[@]}"
printf 'файлов с __all__            '; countf '__all__\s*=' "${PY_SRC[@]}"
printf 'ABC / Protocol (интерфейсы) '; count '\((ABC|Protocol)\)|abstractmethod' "${PY_SRC[@]}"
printf 'PEP 695: class X[T]         '; count '^\s*class\s+\w+\[' "${PY_SRC[@]}"
printf 'метаклассы                  '; count 'metaclass\s*=' "${PY_SRC[@]}"
printf 'декораторов на классах      '; count '^@\w' "${PY_SRC[@]}"

sub "самые частые имена базовых классов (по ним пишутся правила классификации)"
"${GP[@]}" grep -hoE '^\s*class\s+\w+\s*\(\s*[A-Za-z_][A-Za-z0-9_.]*' "${PY_SRC[@]}" \
    | sed 's/.*(\s*//' | sort | uniq -c | sort -rn | head -25 | show

sub "самые частые декораторы (тоже кандидаты в правила)"
"${GP[@]}" grep -hoE '^\s*@[A-Za-z_][A-Za-z0-9_.]*' "${PY_SRC[@]}" \
    | tr -d ' @' | sort | uniq -c | sort -rn | head -25 | show

sub "суффиксы имён классов (по ним пишутся правила name_suffix)"
"${GP[@]}" grep -hoE '^\s*class\s+\w+' "${PY_SRC[@]}" | awk '{print $2}' \
    | grep -oE '[A-Z][a-z]+$' | sort | uniq -c | sort -rn | head -20 | show


# ======================================================================================
section "12. Резолв импортов — ВОПРОС №12"
note "sys.path.append в коде означает таблицу резолва, которой нет в конфигурации."

printf 'sys.path.append / insert     '; count 'sys\.path\.(append|insert)' "${PY_ONLY[@]}"
printf 'относительные импорты (from .) '; count '^from\s+\.' "${PY_SRC[@]}"
printf '  из них на два уровня (from ..) '; count '^from\s+\.\.' "${PY_SRC[@]}"
printf 'звёздочные импорты (import *)  '; count '^from\s+[\w.]+\s+import\s+\*' "${PY_SRC[@]}"
printf 'импорты в try/except ImportError '; count 'except\s+ImportError' "${PY_SRC[@]}"
printf 'importlib (динамический импорт) '; count 'importlib|__import__\(' "${PY_SRC[@]}"
printf '__init__.py, не пустых          '; countf '\w' -- '*__init__.py'

sub "примеры sys.path.append (если есть — это отдельное решение в плане)"
"${GP[@]}" grep -nE 'sys\.path\.(append|insert)' "${PY_ONLY[@]}" \
    | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE" | show

sub "СОДЕРЖИМОЕ самого крупного __init__.py (бочка ли это)"
BIG=$("${GP[@]}" ls-files -- '*__init__.py' "${EXCLUDES[@]}" | head -200 | while read -r f; do
        printf '%s %s\n' "$(wc -l < "$PP$f" 2>/dev/null || echo 0)" "$f"; done | sort -rn | head -1 | awk '{print $2}')
if [ -n "$BIG" ]; then printf '\n>>>>> %s\n' "$PP$BIG"; sed -n '1,60p' "$PP$BIG"; else note "(нет)"; fi


# ======================================================================================
section "13. Docstring — ВОПРОС №14"
note "Docstring — ПЕРВЫЙ ОПЕРАТОР ВНУТРИ тела, а не предшествующий сосед (§5.2)."
note "Если их мало, пустое поле описания — законное состояние, а не дефект разбора."

printf 'классов                       '; count '^\s*class\s+\w+' "${PY_SRC[@]}"
printf 'тройных кавычек (всего/2 ≈ док) '; count '"""|'"'''" "${PY_SRC[@]}"
printf 'файлов с docstring в первой строке '; countf '^"""' "${PY_SRC[@]}"
printf 'комментариев # TODO/FIXME     '; count '#\s*(TODO|FIXME|XXX|HACK)' "${PY_SRC[@]}"


# ======================================================================================
section "14. Генерат и вендоринг — ВОПРОС №14 (умолчания обхода)"

printf 'файлов *_pb2.py / *_pb2_grpc.py '; countr '.' -- '*_pb2.py' '*_pb2_grpc.py'
printf 'файлов с пометкой generated     '; count '(Generated by the protocol buffer|DO NOT EDIT|auto-generated|@generated)' "${PY_ONLY[@]}"
printf 'каталогов migrations            '; countr '.' -- '*/migrations/*.py'
printf 'ноутбуков .ipynb                '; countr '.' -- '*.ipynb'
sub "прочие подозрительные каталоги в дереве"
git ls-files | grep -oE '(^|/)(vendor|third_party|external|libs?)/' | sort | uniq -c | sort -rn | head | show


# ======================================================================================
section "ИТОГ: что смотреть глазами"
cat <<'TXT'
    1. Раздел 2 — СОДЕРЖИМОЕ tornado_service.py. Это главный срез всей разведки.
       Нужны два ответа: КАКОЙ ФОРМОЙ регистрируются модули (словарь имён, список
       кортежей, вызовы register(...), декоратор — четыре разные задачи разбора)
       и ГДЕ ОНИ ЛЕЖАТ. На второе отвечают импорты этого же файла, поэтому они
       напечатаны отдельным срезом.
       Имя зарегистрированного модуля — кандидат в главный якорь питон-стороны:
       если модули зовутся через grid, HTTP-маршрута в цепочке может не быть вовсе.

    2. Раздел 8 — сравните два блока. Полный GRID-блок при пустом HTTP-блоке
       означает, что экстрактор исходящих HTTP-вызовов на .NET не нужен вовсе,
       а нужна цепочка «grid-сервис → имя модуля». Полный HTTP-блок — наоборот:
       смотрите долю вызовов с литеральным путём, ниже половины ключ
       (метод + маршрут) один связь не вытянет. Раздел 9 — где лежит базовый
       адрес; сравните гистограммы префиксов из §5 и §8, расхождение и есть база.

    3. Раздел 1 — раскладка. Где лежит grid и кто его соседи по уровню: со слов
       владельца, inner_debts лежит РЯДОМ с grid, а не под ним. Если соседей
       много и они разнородны, правило границы модуля обязано искать снизу вверх
       от файла, а не сверху вниз от общего родителя, — общего родителя нет.

    4. Раздел 5 — собирается ли таблица маршрутов из кусков и сколько маршрутов
       содержат регулярные группы. Первое определяет, хватит ли разбора одного
       файла (на Angular разбор одного app.routes.ts давал два пути из двадцати
       шести — и молча). Второе — сколько ключей придётся переводить из регулярки
       в шаблон, потому что общая нормализация маршрута на регулярках ломается
       тремя способами, и каждый результат выглядит правдоподобно.

    5. Раздел 11 — классов сильно меньше, чем функций верхнего уровня? Тогда
       единица документации — модуль, а такого узла в модели сегодня нет.
       Это вопрос к владельцу до планирования, а не по ходу реализации.

    6. Раздел 3 — версия языка. Питона 2 быть не должно; если маркер «except X, e:»
       сработал, сначала посмотрите на версию: в 3.14 это законная форма (PEP 758),
       разбираемая в тот же узел. Отдельно смотрите PEP 696 ([T = int], 3.13+):
       грамматика 0.25.0 его НЕ знает и даёт ошибку разбора, то есть переезд
       на 3.13+ требует планового апгрейда грамматики.

    Если запускали без области, а сервисов несколько — прогоните ещё раз со своим
    первым аргументом. Срезы «примеры» и «содержимое» ограничены по объёму
    и показывают первые файлы по алфавиту, то есть чужой сервис вместо вашего.
    Исключение — раздел 2: реестр печатается по имени и безусловно.
TXT
