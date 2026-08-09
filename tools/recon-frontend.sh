#!/usr/bin/env bash
#
# Разведка фронтенда АС CF: собрать факты, на которых стоит `docs/frontend-analysis.md`.
#
# Скрипт ТОЛЬКО ЧИТАЕТ: `git grep`, `git ls-files`, `sed`, `awk`. Ничего не пишет,
# в сеть не ходит, рабочее дерево не трогает.
#
# Запускать из корня репозитория АС CF:
#
#     bash tools/recon-frontend.sh > recon-frontend.txt 2>&1
#     bash tools/recon-frontend.sh ОБЛАСТЬ_ФРОНТА [ОБЛАСТЬ_БЭКА] > recon-frontend.txt 2>&1
#
# Пример для одной команды:
#
#     bash tools/recon-frontend.sh \
#         sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.ML \
#         sbt.cms.cashflow.subrepo/Sbt.CMS.Cashflow.NetCore
#
# Вывод ограничен по объёму намеренно — его пересылают целиком.
#
# Почему `git grep`, а не `grep -r`: в корне АС CF лежит `artifacts/bin` с выводом
# сборки, и обычный рекурсивный поиск даёт сотни ложных вхождений (зафиксировано
# в docs/findings-cashflow-registries.md).

set -u

SAMPLE=12              # сколько строк примеров показывать в каждом срезе
FILE_CAP=200           # сколько строк файла печатать целиком

# Исключения путей действуют во всех поисках. `:(exclude)` — синтаксис pathspec git.
#
# ЛОВУШКА, проверенная на git 2.55: `:(exclude)**/node_modules/**` НЕ исключает
# `node_modules/` в корне. В pathspec без магии `:(glob)` звёздочка сама по себе
# перекрывает `/`, поэтому ведущее `**/` требует хотя бы одного каталога перед
# `node_modules`. Это та же ловушка `**`, что записана в CLAUDE.md про `fnmatch`.
# Отсюда две формы вместо одной: корневая и вложенная. `:(glob,exclude)` решил бы
# то же самое одной строкой, но требует более свежего git — а скрипт запускают
# в закрытом контуре, где неизвестная магия pathspec означает падение.
#
# Для файловых шаблонов (`*.spec.ts`) второй формы не нужно: там нет ведущего `/`,
# и звёздочка перекрывает путь целиком.
EXCLUDES=(
    ":(exclude)node_modules/**"   ":(exclude)*/node_modules/**"
    ":(exclude)dist/**"           ":(exclude)*/dist/**"
    ":(exclude).angular/**"       ":(exclude)*/.angular/**"
)
# Область разведки. Первый аргумент сужает поиски по фронту, второй — по `.cs`.
#
# ЛОВУШКА, стоившая целого захода 09.08.2026. В АС CF семь фронтов, и без сужения
# каждый срез «показать примеры» упирается в `head -N`, который режет список
# в алфавитном порядке. Модуль, чьё имя идёт последним, выпадает из отчёта
# целиком — и в выводе это неотличимо от «в модуле такого нет».
# На том прогоне так пропал `Sbt.CMS.Cashflow.ML`: седьмой из семи по алфавиту,
# он не попал ни в один содержательный срез, включая `fix-url.interceptor.ts`,
# ради которого раздел 3 и писался. Разведка вернулась без ответа на свой
# главный вопрос, при этом выглядела отработавшей.
#
# ВТОРАЯ ЛОВУШКА pathspec, найденная при попытке сделать это очевидным способом.
# Сужение НЕЛЬЗЯ задать префиксом в самом pathspec: как только положительный
# шаблон начинается с литерального каталога, любой `:(exclude)` обнуляет выборку
# целиком — даже тот, которому нечему совпадать. Проверено на git 2.55:
#
#     git ls-files -- 'МОДУЛЬ/*.ts'                          → 6
#     git ls-files -- 'МОДУЛЬ/*.ts' ':(exclude)ничего-такого' → 0
#     git ls-files -- '*.ts'        ':(exclude)node_modules/**' → работает
#
# Отчёт при этом не пустой, а куцый: счётчики честно показывают нули, и это
# выглядит как «во фронте ничего нет». Поэтому область задаётся через `git -C`:
# шаблоны остаются теми же, что и без сужения, и ни один из них не приобретает
# литерального префикса. Плата — пути в выводе относительно модуля; поэтому
# область печатается в шапке, а `sed` по файлам берёт префикс из `$FP`.
FRONT_SCOPE="${1:-}"; FRONT_SCOPE="${FRONT_SCOPE%/}"
BACK_SCOPE="${2:-}";  BACK_SCOPE="${BACK_SCOPE%/}"
FP=""; [ -n "$FRONT_SCOPE" ] && FP="$FRONT_SCOPE/"   # только для sed по файлам
GF=(git -C "${FRONT_SCOPE:-.}")                      # поиски по фронту
GB=(git -C "${BACK_SCOPE:-.}")                       # поиски по .cs

TS_ONLY=("--" "*.ts" "${EXCLUDES[@]}")
TS_SRC=("${TS_ONLY[@]}" ":(exclude)*.spec.ts" ":(exclude)*.d.ts")
# Моки — источник примеров, а не кода: на прогоне 09.08.2026 все двенадцать
# примеров строк экшена пришли из одного `*.actions.mock.ts`.
TS_NOMOCK=("${TS_SRC[@]}" ":(exclude)*.mock.ts")
CS_ONLY=("--" "*.cs")

section() { printf '\n\n========== %s ==========\n' "$1"; }
sub() { printf '\n--- %s\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# Число строк, совпавших с шаблоном. Печатает 0, а не пустоту: пустота в отчёте
# неотличима от «поиск не отработал». Три штуки по областям: фронт, бэк, весь
# репозиторий — чтобы область считалась ровно там, где она имеет смысл.
count()  { local n; n=$("${GF[@]}" grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
countb() { local n; n=$("${GB[@]}" grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }
countr() { local n; n=$(git        grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }

# То же правило для списков: пустой срез называется вслух. Раздел 3 того прогона
# был пуст ровно потому, что фильтр не пропустил ни одного файла, — и понять это
# по выводу было нельзя.
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
if [ -n "$FRONT_SCOPE" ] && [ ! -d "$FRONT_SCOPE" ]; then
    printf 'ОШИБКА: каталога «%s» нет. Где лежат .ts:\n' "$FRONT_SCOPE"
    git ls-files -- '*.ts' "${EXCLUDES[@]}" | awk -F/ '{print $1"/"$2}' \
        | sort | uniq -c | sort -rn | head -15
    exit 2
fi
if [ -n "$BACK_SCOPE" ] && [ ! -d "$BACK_SCOPE" ]; then
    printf 'ОШИБКА: каталога «%s» нет (второй аргумент — область .cs).\n' "$BACK_SCOPE"
    exit 2
fi
if [ -z "$("${GF[@]}" ls-files "${TS_ONLY[@]}" | head -1)" ]; then
    printf 'ОШИБКА: под областью «%s» нет ни одного .ts. Где они есть:\n' "${FRONT_SCOPE:-.}"
    git ls-files -- '*.ts' "${EXCLUDES[@]}" | awk -F/ '{print $1"/"$2}' \
        | sort | uniq -c | sort -rn | head -15
    exit 2
fi

printf 'Разведка фронтенда. Корень: %s\n' "$(git rev-parse --show-toplevel)"
printf 'Коммит: %s\n' "$(git rev-parse --short HEAD 2>/dev/null || echo '—')"
printf 'Отслеживаемых файлов: %s\n' "$(git ls-files | wc -l)"
printf 'Область фронта: %s\n' "${FRONT_SCOPE:-весь репозиторий}"
printf 'Область бэка:   %s\n' "${BACK_SCOPE:-весь репозиторий}"
[ -n "$FRONT_SCOPE" ] && printf 'Пути в срезах по фронту — относительно области.\n'


# ======================================================================================
section "0. Есть ли фронт вообще и какого он размера"

sub "файлов .ts (без node_modules, dist)"
"${GF[@]}" ls-files "${TS_ONLY[@]}" | wc -l
sub "из них тестов (*.spec.ts) и деклараций (*.d.ts)"
"${GF[@]}" ls-files -- '*.spec.ts' '*.d.ts' "${EXCLUDES[@]}" | wc -l
sub "файлов .html компонентов"
"${GF[@]}" ls-files -- '*.component.html' "${EXCLUDES[@]}" | wc -l
sub "файлов .tsx (нужна ДРУГАЯ грамматика, см. §5.5 аналитики)"
"${GF[@]}" ls-files -- '*.tsx' "${EXCLUDES[@]}" | wc -l

# Считается по всему репозиторию даже при заданной области: по этому списку
# выбирают саму область, и он же показывает, сколько фронтов осталось за кадром.
sub "верхнеуровневые каталоги, где лежат .ts, — весь репозиторий (топ-15)"
git ls-files -- '*.ts' "${EXCLUDES[@]}" | awk -F/ '{print $1"/"$2}' \
    | sort | uniq -c | sort -rn | head -15 | show


# ======================================================================================
section "1. Раскладка workspace — чем является 'модуль' на фронте (§3.1, §6.4)"

# ЛОВУШКА pathspec: `*nx.json` ловит не только `nx.json`, но и любой файл, чьё имя
# кончается на `nx.json`. На прогоне 09.08.2026 первой строкой раздела встал
# `…/DuplicatedRecordsBugEvanx.json` из тестовых данных .NET. Магию `:(glob)` брать
# нельзя (см. про закрытый контур выше), поэтому имя проверяется после выборки.
sub "angular.json / nx.json / project.json"
"${GF[@]}" ls-files -- '*angular.json' '*nx.json' '*project.json' "${EXCLUDES[@]}" \
    | grep -E '(^|/)(angular|nx|project)\.json$' | head -30 | show

sub "package.json (кандидаты в модули монорепозитория)"
if [ -n "$FRONT_SCOPE" ]; then
    "${GF[@]}" ls-files -- '*package.json' "${EXCLUDES[@]}" | head -30 | show
else
    "${GF[@]}" ls-files -- '*package.json' "${EXCLUDES[@]}" | grep -v '^package.json$' | head -30 | show
fi

sub "проекты, объявленные в angular.json (имя и root)"
for f in $("${GF[@]}" ls-files -- '*angular.json' "${EXCLUDES[@]}" | head -8); do
    note "файл: $f"
    sed -n '/"projects"/,$p' "$FP$f" | grep -E '^\s{4}"[^"]+":|"(root|sourceRoot|projectType)"' | head -60
done

sub "tsconfig: алиасы путей (без них не резолвятся базовые классы, §3.4)"
for f in $("${GF[@]}" ls-files -- 'tsconfig*.json' '*/tsconfig*.json' "${EXCLUDES[@]}" | head -8); do
    note "файл: $f"
    sed -n '/"paths"/,/}/p' "$FP$f" | head -25
done


# ======================================================================================
section "2. Версии: Angular, NGXS, чем собирается"

# Обрезки здесь быть не должно. На прогоне 09.08.2026 `head -20` пришёлся ровно
# на границу между `@ngrx/store` и `@ngxs/store`: отчёт показал ngrx, счётчики
# раздела 8 показали идиомы NGXS, и стек остался неизвестен. Версии TypeScript
# и rxjs отрезало там же, а от первой зависит выбор версии грамматики.
sub "зависимости, по которым видно стек (без обрезки: от неё уже пострадали)"
"${GF[@]}" grep -hE '"(@angular/core|@angular/common|@ngxs/[a-z-]+|@ngrx/store|typescript|rxjs)"' \
    -- '*package.json' "${EXCLUDES[@]}" | sed 's/^\s*//' | sort -u | show

sub "standalone-компоненты (Angular 15+) против NgModule"
printf 'standalone: true  '; count 'standalone:\s*true' "${TS_SRC[@]}"
printf '@NgModule        '; count '@NgModule\(' "${TS_SRC[@]}"


# ======================================================================================
section "3. FixUrlInterceptor — ГЛАВНЫЙ БЛОКЕР (§2.3, §6.1)"
note "Пока не прочитан, неизвестно, совпадает ли литерал в сервисе с маршрутом контроллера."

# Регистрация ищется без учёта регистра и через дефис: в Angular 15+ интерцептор
# бывает функцией, а не классом, и имени `FixUrlInterceptor` в коде тогда нет
# вовсе — только `fixUrlInterceptor` или имя файла. Ровно так и вышло в ML:
# файл `fix-url.interceptor.ts` есть, а класс с этим именем упоминают чужие фронты.
sub "где упоминается (регистр не важен: интерцептор бывает функцией)"
"${GF[@]}" grep -lniE "fix[-_]?url" "${TS_ONLY[@]}" | head -10 | show

sub "все интерцепторы области"
"${GF[@]}" ls-files "${TS_ONLY[@]}" | grep -iE 'interceptor' | head -20 | show

# СНАЧАЛА по имени, и только потом по содержимому. Прежний порядок — «взять первые
# четыре интерцептора по алфавиту и отфильтровать по содержимому» — не напечатал
# ничего: `fix-url.interceptor.ts` был шестым, а из попавшей четвёрки фильтр
# не пропустил никого. Файл, ради которого написан весь раздел, обязан печататься
# по имени и безусловно.
sub "СОДЕРЖИМОЕ fix-url интерцептора (главный вопрос разведки)"
FIXURL=$("${GF[@]}" ls-files "${TS_ONLY[@]}" | grep -iE 'fix[-_]?url' | grep -v '\.spec\.ts$' | head -3)
if [ -n "$FIXURL" ]; then
    for f in $FIXURL; do
        printf '\n>>>>> %s\n' "$FP$f"; sed -n "1,${FILE_CAP}p" "$FP$f"
    done
else
    note "(файла с fix-url в имени нет — смотреть следующий срез)"
fi

sub "СОДЕРЖИМОЕ прочих интерцепторов, трогающих URL (не более двух)"
PRINTED=0
for f in $("${GF[@]}" ls-files "${TS_ONLY[@]}" | grep -iE 'interceptor' | grep -v '\.spec\.ts$' \
           | grep -viE 'fix[-_]?url' | head -8); do
    [ "$PRINTED" -ge 2 ] && break
    # -i: `Url`, `URL` и `url` — три написания одного и того же, и прежний
    # регистрозависимый фильтр отсекал первые два.
    if "${GF[@]}" grep -qiE 'url|clone\(' -- "$f" 2>/dev/null; then
        printf '\n>>>>> %s\n' "$FP$f"; sed -n "1,${FILE_CAP}p" "$FP$f"
        PRINTED=$((PRINTED + 1))
    fi
done
[ "$PRINTED" -eq 0 ] && note "(ни один интерцептор не упоминает url и не зовёт clone())"

sub "базовый URL: откуда берётся"
"${GF[@]}" grep -nE '(apiUrl|baseUrl|API_URL|baseHref|InjectionToken<string>)\s*[:=(]' \
    -- '*.ts' "${EXCLUDES[@]}" | head -"$SAMPLE" | show

# Префикс приложения из pathRewrite — часть нормализации маршрута, а не деталь
# разработки: литерал `/pm/api/…` во фронте и маршрут `api/…` в контроллере
# расходятся ровно на него.
sub "proxy.conf — префиксы, которые срезаются по дороге к бэку"
"${GF[@]}" ls-files -- '*proxy.conf*' | head -5 | show
for f in $("${GF[@]}" ls-files -- '*proxy.conf*' | head -2); do
    printf '\n>>>>> %s\n' "$FP$f"; sed -n '1,60p' "$FP$f"
done
sub "pathRewrite и context во всех proxy.conf (сводка по репозиторию)"
git grep -hnE "pathRewrite|context\s*:" -- '*proxy.conf*' | head -20 | show


# ======================================================================================
section "4. HTTP-вызовы: сколько их и сколько восстановимо (§2.4, §6.2)"
note "Отношение 'с литералом' к 'всего' — верхняя оценка покрытия связи фронт↔бэк."

# ЛОВУШКА, из-за которой отношение прошлого прогона (560 из 1219) оказалось
# бессмысленным: `.get(` — это ещё и `Map.get`, `form.get('search')`,
# `headers.get('Content-Disposition')`, `queryParams.get(…)`. Из двенадцати
# примеров «литерального URL» настоящим HTTP-вызовом был один. Пока в шаблоне
# нет ПОЛУЧАТЕЛЯ, счётчик меряет что угодно, кроме сети.
#
# Отсюда всё считается от `this.http` / `httpClient` / `_http`, а прежнее число
# остаётся в выводе с явной пометкой — иначе его посчитают заново кто-нибудь ещё.
RECV='(this\.)?_?(http|httpClient|httpService)\.'
VERBS='(get|post|put|delete|patch)(<[^>]*>)?'

sub "счётчики"
printf 'HTTP-вызовов (по получателю this.http/httpClient)  '
count "$RECV$VERBS\\(" "${TS_SRC[@]}"
printf '  из них первый аргумент — строка в кавычках       '
count "$RECV$VERBS\\(\\s*['\"]" "${TS_SRC[@]}"
printf '  из них первый аргумент — шаблонная строка        '
count "$RECV$VERBS\\(\\s*\`" "${TS_SRC[@]}"
printf '  из них первый аргумент — переменная/выражение    '
count "$RECV$VERBS\\(\\s*[A-Za-z_\$]" "${TS_SRC[@]}"
printf 'любых .get/.post/… — НЕ показатель (Map.get и др.) '
count "\\.$VERBS\\(" "${TS_SRC[@]}"

sub "примеры: литеральный URL"
"${GF[@]}" grep -nE "$RECV$VERBS\(\s*['\"]" "${TS_SRC[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE" | show

sub "примеры: шаблонная строка"
"${GF[@]}" grep -nE "$RECV$VERBS\(\s*\`" "${TS_SRC[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE" | show

sub "примеры: НЕ литерал — вот это и не восстановится автоматически"
"${GF[@]}" grep -nE "$RECV$VERBS\(\s*[A-Za-z_\$][A-Za-z0-9_\$.]*\s*[,)]" "${TS_SRC[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE" | show

sub "константы маршрутов, если они вынесены отдельно"
"${GF[@]}" grep -nE "(const|readonly)\s+\w*(Url|Urls|Api|Routes|Endpoints)\w*\s*[:=]" "${TS_SRC[@]}" \
    | head -"$SAMPLE" | show

# Ведущий слэш обязателен как ОПЦИЯ: `'/api/ml/guaranties'` в прошлом прогоне
# не попал в гистограмму вовсе, потому что шаблон требовал `api` сразу за кавычкой.
sub "какие префиксы путей встречаются (первые два сегмента URL)"
"${GF[@]}" grep -hoE "['\"\`]/?api/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+" "${TS_SRC[@]}" 2>/dev/null \
    | tr -d "'\"\`" | sed 's|^/||' | awk -F/ '{print $1"/"$2}' \
    | sort | uniq -c | sort -rn | head -20 | show
note 'Вызовы, где база лежит в переменной (`${this.baseUrl}/…`), сюда не попадают.'


# ======================================================================================
section "5. Сгенерированный клиент — есть ли обход HttpClient (§6.3)"

# Файлы генераторов ищутся по всему репозиторию: `.nswag` в АС CF лежит рядом
# с контрактами .NET, а вопрос раздела — есть ли генерат НА ФРОНТЕ, и на него
# отвечает второй срез.
sub "файлы генераторов (весь репозиторий)"
git ls-files | grep -iE 'nswag|openapi|swagger' | head -20 | show
sub "признаки генерата в .ts"
"${GF[@]}" grep -ln "This code was generated\|auto-generated\|@generated" "${TS_ONLY[@]}" | head -10 | show


# ======================================================================================
section "6. Маршрутизация: как объявлены страницы (§3.3, §6.5)"

sub "счётчики"
printf 'объявлений const … : Routes   '; count '(export\s+)?const\s+\w+\s*:\s*Routes' "${TS_SRC[@]}"
printf 'RouterModule.forChild         '; count 'RouterModule\.forChild' "${TS_SRC[@]}"
printf 'RouterModule.forRoot          '; count 'RouterModule\.forRoot' "${TS_SRC[@]}"
printf 'loadChildren                  '; count 'loadChildren' "${TS_SRC[@]}"
printf 'loadComponent                 '; count 'loadComponent' "${TS_SRC[@]}"
printf 'записей path:                 '; count "path\\s*:\\s*['\"]" "${TS_SRC[@]}"

sub "файлы с таблицами роутов"
"${GF[@]}" ls-files "${TS_SRC[@]}" | grep -iE 'routing|\.routes\.ts' | head -20 | show

sub "СОДЕРЖИМОЕ корневой таблицы роутов (первая найденная)"
for f in $("${GF[@]}" ls-files "${TS_SRC[@]}" | grep -iE 'app[.-]routing|app\.routes' | head -1); do
    printf '\n>>>>> %s\n' "$FP$f"; sed -n "1,${FILE_CAP}p" "$FP$f"
done

sub "примеры path: — по ним видно, литералы это или переменные"
"${GF[@]}" grep -nE "path\s*:\s*['\"]" "${TS_SRC[@]}" | sed 's/^\(.\{130\}\).*/\1…/' | head -20 | show


# ======================================================================================
section "7. Природа страниц: свои компоненты или генерат платформы (§6.6)"
note "Если фронт рендерит списки из Cashflow.Structure.xml, источник страниц — реестр,"
note "а не разбор TypeScript, и якорь page устроен иначе."

sub "ссылается ли фронт на InnerName списков платформы"
"${GF[@]}" grep -nE "(UserTasks|USER_TASKS|innerName|InnerName|listName)" "${TS_SRC[@]}" \
    | head -"$SAMPLE" | show

sub "универсальный компонент списка/формы, параметризуемый именем"
"${GF[@]}" ls-files "${TS_SRC[@]}" | grep -iE 'grid|list|universal|dynamic|generic' | head -20 | show

sub "сколько форм объявлено в реестре структуры"
printf '<Form  в Cashflow.Structure.xml  '; countr '<Form ' -- '*Cashflow.Structure.xml'
printf '<List  в Cashflow.Structure.xml  '; countr '<List ' -- '*Cashflow.Structure.xml'

sub "компонентов всего (для сравнения с числом форм)"
printf '@Component  '; count '@Component\(' "${TS_SRC[@]}"


# ======================================================================================
section "8. NGXS: нужна ли цепочка Component → Action → State → Service (§6.7)"

sub "счётчики"
printf '@State(       '; count '@State[<(]' "${TS_SRC[@]}"
printf '@Action(      '; count '@Action\(' "${TS_SRC[@]}"
printf '@Selector(    '; count '@Selector\(' "${TS_SRC[@]}"
printf 'store.dispatch '; count '\.dispatch\(' "${TS_SRC[@]}"
printf 'static readonly type '; count 'static\s+readonly\s+type' "${TS_SRC[@]}"

sub "примеры строк типа экшена (без *.mock.ts)"
"${GF[@]}" grep -nE "static\s+readonly\s+type\s*=" "${TS_NOMOCK[@]}" \
    | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE" | show

sub "зовут ли компоненты сервисы напрямую (в обход стора)"
"${GF[@]}" grep -nE "constructor\([^)]*Service" "${TS_SRC[@]}" | head -"$SAMPLE" | show
printf 'inject(…Service)  '; count 'inject\(\w+Service\)' "${TS_SRC[@]}"


# ======================================================================================
section "9. Сторона .NET: чем записан маршрут (новый пункт, см. поправку владельца)"
note "Литеральный маршрут совпадает с фронтом посимвольно. Токены [action]/[controller]"
note "подставляют имя метода и класса — там регистр разойдётся, и нормализация обязательна."

printf '[Route("…")] всего              '; countb '\[Route\("' "${CS_ONLY[@]}"
printf 'из них с токеном [controller]   '; countb '\[Route\("[^"]*\[controller\]' "${CS_ONLY[@]}"
printf 'из них с токеном [action]       '; countb '\[Route\("[^"]*\[action\]' "${CS_ONLY[@]}"
printf '[HttpGet("…")] и подобные       '; countb '\[Http(Get|Post|Put|Delete|Patch)\("' "${CS_ONLY[@]}"
printf '[HttpGet] без аргумента         '; countb '\[Http(Get|Post|Put|Delete|Patch)\]' "${CS_ONLY[@]}"
printf 'MapControllerRoute (конвенц.)   '; countb 'MapControllerRoute' "${CS_ONLY[@]}"
printf 'app.Map(Get|Post…) minimal API  '; countb 'app\.Map(Get|Post|Put|Delete)' "${CS_ONLY[@]}"

# Пустой `[HttpGet]` означает, что URL метода равен маршруту класса, и имени метода
# в адресе нет. На прогоне 09.08.2026 таких 987 против 134 с аргументом, то есть
# это основная форма, а не край. Резолвер обязан складывать маршрут класса
# с методным атрибутом и уметь методный атрибут БЕЗ аргумента.
sub "примеры пустого [HttpGet] вместе с маршрутом класса"
"${GB[@]}" grep -nE '\[Http(Get|Post|Put|Delete|Patch)\]' "${CS_ONLY[@]}" | head -6 | show

# Гистограмма префиксов бэка сравнивается с такой же гистограммой фронта из §4.
# Расхождение здесь означает префикс, срезаемый по дороге (см. proxy.conf в §3).
sub "какие префиксы объявлены на бэке (первые два сегмента [Route])"
"${GB[@]}" grep -hoE '\[Route\("/?[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+' "${CS_ONLY[@]}" 2>/dev/null \
    | sed 's/\[Route("//; s|^/||' | awk -F/ '{print $1"/"$2}' \
    | sort | uniq -c | sort -rn | head -20 | show

sub "примеры маршрутов контроллеров с префиксом api/ml"
"${GB[@]}" grep -nE '\[Route\("/?api/ml' "${CS_ONLY[@]}" | head -"$SAMPLE" | show

sub "контроллеры, отвечающие на api/ml/structure (сверка с примером владельца)"
"${GB[@]}" grep -lnE '\[Route\("/?api/ml/structure' "${CS_ONLY[@]}" | head -5 | show


# ======================================================================================
section "10. Шаблоны компонентов (§7.1)"

printf 'templateUrl        '; count 'templateUrl' "${TS_SRC[@]}"
printf 'template: `        '; count 'template:\s*`' "${TS_SRC[@]}"
printf 'routerLink в .html '; count 'routerLink' -- '*.html' "${EXCLUDES[@]}"


# ======================================================================================
section "ИТОГ: что смотреть глазами"
cat <<'TXT'
    1. Раздел 3 — содержимое fix-url интерцептора. Нужен ответ одним предложением:
       что он дописывает или меняет в URL. Это блокирует нормализацию маршрута.
       Там же pathRewrite из proxy.conf: префикс, срезаемый по дороге к бэку,
       входит в нормализацию, иначе литерал фронта и маршрут контроллера
       расходятся на первом сегменте.
    2. Раздел 4 — отношение «с литералом» к HTTP-вызовам ПО ПОЛУЧАТЕЛЮ. Общее
       число `.get/.post/…` для этого не годится: в него входят Map.get и
       form.get. Ниже половины — схема «литерал в сервисе» одна не вытянет,
       нужна отдельная задача на константы.
    3. Раздел 7 — есть ли универсальный компонент списка. Если есть, часть
       экранов не имеет своего Angular-компонента, и якорь page устроен иначе.
    4. Раздел 9 — доля токенов [action]/[controller] и доля пустых [HttpGet].
       Токенов ноль — маршруты сравниваются посимвольно. Пустых много — резолвер
       обязан складывать маршрут класса с методным атрибутом без аргумента.

    Если запускали без области, а фронтов в репозитории несколько — прогоните
    ещё раз со своим модулем первым аргументом. Срезы «примеры» и «содержимое»
    ограничены по объёму и показывают первые файлы по алфавиту, то есть чужой
    модуль вместо вашего.
TXT
