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
TS_ONLY=("--" "*.ts" "${EXCLUDES[@]}")
TS_SRC=("${TS_ONLY[@]}" ":(exclude)*.spec.ts" ":(exclude)*.d.ts")

section() { printf '\n\n========== %s ==========\n' "$1"; }
sub() { printf '\n--- %s\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# Число строк, совпавших с шаблоном. Печатает 0, а не пустоту: пустота в отчёте
# неотличима от «поиск не отработал».
count() { local n; n=$(git grep -nE "$1" "${@:2}" 2>/dev/null | wc -l); printf '%6d\n' "$n"; }

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ОШИБКА: это не git-репозиторий. Запускать из корня репозитория АС CF."
    exit 2
fi

printf 'Разведка фронтенда. Корень: %s\n' "$(git rev-parse --show-toplevel)"
printf 'Коммит: %s\n' "$(git rev-parse --short HEAD 2>/dev/null || echo '—')"
printf 'Отслеживаемых файлов: %s\n' "$(git ls-files | wc -l)"


# ======================================================================================
section "0. Есть ли фронт вообще и какого он размера"

sub "файлов .ts (без node_modules, dist)"
git ls-files "${TS_ONLY[@]}" | wc -l
sub "из них тестов (*.spec.ts) и деклараций (*.d.ts)"
git ls-files -- '*.spec.ts' '*.d.ts' "${EXCLUDES[@]}" | wc -l
sub "файлов .html компонентов"
git ls-files -- '*.component.html' "${EXCLUDES[@]}" | wc -l
sub "файлов .tsx (нужна ДРУГАЯ грамматика, см. §5.5 аналитики)"
git ls-files -- '*.tsx' "${EXCLUDES[@]}" | wc -l

sub "верхнеуровневые каталоги, где лежат .ts (топ-15)"
git ls-files "${TS_ONLY[@]}" | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn | head -15


# ======================================================================================
section "1. Раскладка workspace — чем является 'модуль' на фронте (§3.1, §6.4)"

sub "angular.json / nx.json / project.json"
git ls-files -- '*angular.json' '*nx.json' '*project.json' "${EXCLUDES[@]}" | head -30

sub "package.json вне корня (кандидаты в модули монорепозитория)"
git ls-files -- '*package.json' "${EXCLUDES[@]}" | grep -v '^package.json$' | head -30

sub "проекты, объявленные в angular.json (имя и root)"
for f in $(git ls-files -- '*angular.json' "${EXCLUDES[@]}" | head -3); do
    note "файл: $f"
    sed -n '/"projects"/,$p' "$f" | grep -E '^\s{4}"[^"]+":|"(root|sourceRoot|projectType)"' | head -60
done

sub "tsconfig: алиасы путей (без них не резолвятся базовые классы, §3.4)"
for f in $(git ls-files -- 'tsconfig*.json' '*/tsconfig*.json' "${EXCLUDES[@]}" | head -5); do
    note "файл: $f"
    sed -n '/"paths"/,/}/p' "$f" | head -25
done


# ======================================================================================
section "2. Версии: Angular, NGXS, чем собирается"

sub "зависимости, по которым видно стек"
git grep -hE '"(@angular/core|@angular/common|@ngxs/store|@ngrx/store|typescript|rxjs)"' \
    -- '*package.json' "${EXCLUDES[@]}" | sort -u | head -20

sub "standalone-компоненты (Angular 15+) против NgModule"
printf 'standalone: true  '; count 'standalone:\s*true' "${TS_SRC[@]}"
printf '@NgModule        '; count '@NgModule\(' "${TS_SRC[@]}"


# ======================================================================================
section "3. FixUrlInterceptor — ГЛАВНЫЙ БЛОКЕР (§2.3, §6.1)"
note "Пока не прочитан, неизвестно, совпадает ли литерал в сервисе с маршрутом контроллера."

sub "где упоминается"
git grep -ln "FixUrlInterceptor" "${TS_ONLY[@]}" | head -10

sub "все интерцепторы репозитория"
git ls-files "${TS_ONLY[@]}" | grep -iE 'interceptor' | head -20

sub "СОДЕРЖИМОЕ файлов интерцепторов, работающих с URL"
for f in $(git ls-files "${TS_ONLY[@]}" | grep -iE 'interceptor' | head -4); do
    if git grep -qE 'url|clone\(' -- "$f" 2>/dev/null; then
        printf '\n>>>>> %s\n' "$f"
        sed -n "1,${FILE_CAP}p" "$f"
    fi
done

sub "базовый URL: откуда берётся"
git grep -nE '(apiUrl|baseUrl|API_URL|baseHref)\s*[:=]' \
    -- '*environment*.ts' '*.ts' "${EXCLUDES[@]}" | head -"$SAMPLE"

sub "proxy.conf — если фронт ходит через прокси разработки"
git ls-files -- '*proxy.conf*' | head -5
for f in $(git ls-files -- '*proxy.conf*' | head -2); do
    printf '\n>>>>> %s\n' "$f"; sed -n '1,60p' "$f"
done


# ======================================================================================
section "4. HTTP-вызовы: сколько их и сколько восстановимо (§2.4, §6.2)"
note "Отношение 'с литералом' к 'всего' — верхняя оценка покрытия связи фронт↔бэк."

sub "счётчики"
printf 'вызовов .get/.post/.put/.delete/.patch всего     '
count '\.(get|post|put|delete|patch)(<[^>]*>)?\(' "${TS_SRC[@]}"
printf 'из них первый аргумент — строка в кавычках       '
count "\\.(get|post|put|delete|patch)(<[^>]*>)?\\(\\s*['\"]" "${TS_SRC[@]}"
printf 'из них первый аргумент — шаблонная строка          '
count '\.(get|post|put|delete|patch)(<[^>]*>)?\(\s*`' "${TS_SRC[@]}"
printf 'вызовов через this.http / httpClient             '
count '(this\.)?(http|httpClient)\.(get|post|put|delete|patch)' "${TS_SRC[@]}"

sub "примеры: литеральный URL"
git grep -nE "\.(get|post|put|delete|patch)(<[^>]*>)?\(\s*['\"]" "${TS_SRC[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE"

sub "примеры: шаблонная строка"
git grep -nE '\.(get|post|put|delete|patch)(<[^>]*>)?\(\s*`' "${TS_SRC[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE"

sub "примеры: НЕ литерал — вот это и не восстановится автоматически"
git grep -nE '\.(get|post|put|delete|patch)(<[^>]*>)?\(\s*[A-Za-z_$][A-Za-z0-9_$.]*\s*[,)]' "${TS_SRC[@]}" \
    | sed 's/^\(.\{150\}\).*/\1…/' | head -"$SAMPLE"

sub "константы маршрутов, если они вынесены отдельно"
git grep -nE "(const|readonly)\s+\w*(Url|Urls|Api|Routes|Endpoints)\w*\s*[:=]" "${TS_SRC[@]}" | head -"$SAMPLE"

sub "какие префиксы путей встречаются (первые два сегмента URL)"
git grep -hoE "['\"\`]api/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+" "${TS_SRC[@]}" 2>/dev/null \
    | tr -d "'\"\`" | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn | head -20


# ======================================================================================
section "5. Сгенерированный клиент — есть ли обход HttpClient (§6.3)"

sub "файлы генераторов"
git ls-files | grep -iE 'nswag|openapi|swagger' | head -20
sub "признаки генерата в .ts"
git grep -ln "This code was generated\|auto-generated\|@generated" "${TS_ONLY[@]}" | head -10


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
git ls-files "${TS_SRC[@]}" | grep -iE 'routing|\.routes\.ts' | head -20

sub "СОДЕРЖИМОЕ корневой таблицы роутов (первая найденная)"
for f in $(git ls-files "${TS_SRC[@]}" | grep -iE 'app[.-]routing|app\.routes' | head -1); do
    printf '\n>>>>> %s\n' "$f"; sed -n "1,${FILE_CAP}p" "$f"
done

sub "примеры path: — по ним видно, литералы это или переменные"
git grep -nE "path\s*:\s*['\"]" "${TS_SRC[@]}" | sed 's/^\(.\{130\}\).*/\1…/' | head -20


# ======================================================================================
section "7. Природа страниц: свои компоненты или генерат платформы (§6.6)"
note "Если фронт рендерит списки из Cashflow.Structure.xml, источник страниц — реестр,"
note "а не разбор TypeScript, и якорь page устроен иначе."

sub "ссылается ли фронт на InnerName списков платформы"
git grep -nE "(UserTasks|USER_TASKS|innerName|InnerName|listName)" "${TS_SRC[@]}" | head -"$SAMPLE"

sub "универсальный компонент списка/формы, параметризуемый именем"
git ls-files "${TS_SRC[@]}" | grep -iE 'grid|list|universal|dynamic|generic' | head -20

sub "сколько форм объявлено в реестре структуры"
printf '<Form  в Cashflow.Structure.xml  '; count '<Form ' -- '*Cashflow.Structure.xml'
printf '<List  в Cashflow.Structure.xml  '; count '<List ' -- '*Cashflow.Structure.xml'

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

sub "примеры строк типа экшена"
git grep -nE "static\s+readonly\s+type\s*=" "${TS_SRC[@]}" | sed 's/^\(.\{130\}\).*/\1…/' | head -"$SAMPLE"

sub "зовут ли компоненты сервисы напрямую (в обход стора)"
git grep -nE "constructor\([^)]*Service" "${TS_SRC[@]}" | head -"$SAMPLE"
printf 'inject(…Service)  '; count 'inject\(\w+Service\)' "${TS_SRC[@]}"


# ======================================================================================
section "9. Сторона .NET: чем записан маршрут (новый пункт, см. поправку владельца)"
note "Литеральный маршрут совпадает с фронтом посимвольно. Токены [action]/[controller]"
note "подставляют имя метода и класса — там регистр разойдётся, и нормализация обязательна."

printf '[Route("…")] всего              '; count '\[Route\("' -- '*.cs'
printf 'из них с токеном [controller]   '; count '\[Route\("[^"]*\[controller\]' -- '*.cs'
printf 'из них с токеном [action]       '; count '\[Route\("[^"]*\[action\]' -- '*.cs'
printf '[HttpGet("…")] и подобные       '; count '\[Http(Get|Post|Put|Delete|Patch)\("' -- '*.cs'
printf '[HttpGet] без аргумента         '; count '\[Http(Get|Post|Put|Delete|Patch)\]' -- '*.cs'
printf 'MapControllerRoute (конвенц.)   '; count 'MapControllerRoute' -- '*.cs'
printf 'app.Map(Get|Post…) minimal API  '; count 'app\.Map(Get|Post|Put|Delete)' -- '*.cs'

sub "примеры маршрутов контроллеров с префиксом api/ml"
git grep -nE '\[Route\("api/ml' -- '*.cs' | head -"$SAMPLE"

sub "контроллеры, отвечающие на api/ml/structure (сверка с примером владельца)"
git grep -lnE '\[Route\("api/ml/structure' -- '*.cs' | head -5


# ======================================================================================
section "10. Шаблоны компонентов (§7.1)"

printf 'templateUrl        '; count 'templateUrl' "${TS_SRC[@]}"
printf 'template: `        '; count 'template:\s*`' "${TS_SRC[@]}"
printf 'routerLink в .html '; count 'routerLink' -- '*.html' "${EXCLUDES[@]}"


# ======================================================================================
section "ИТОГ: что смотреть глазами"
cat <<'TXT'
    1. Раздел 3 — содержимое интерцептора. Нужен ответ одним предложением:
       что он дописывает или меняет в URL. Это блокирует нормализацию маршрута.
    2. Раздел 4 — отношение «с литералом» к «всего». Ниже половины — схема
       «литерал в сервисе» одна не вытянет, нужна отдельная задача на константы.
    3. Раздел 7 — есть ли универсальный компонент списка. Если есть, часть
       экранов не имеет своего Angular-компонента, и якорь page устроен иначе.
    4. Раздел 9 — доля токенов [action]/[controller]. Если ноль, маршруты
       сравниваются посимвольно и связь будет точной.
TXT
