"""Мост к движку разбора: единственный модуль, который о нём знает.

Всё, что выходит отсюда, уже переведено в наши термины. Ни один другой модуль
`docpipe` не знает ни имени движка, ни его видов рёбер, ни слова Cypher —
это правило Р13 плана, и оно проверяется тестом, а не соглашением. Цена замены
источника (другой движок, своя реализация разбора, новая мажорная версия)
равна цене переписывания этого файла, и запасной вариант из «Рисков» остаётся
исполнимым, а не теоретическим.

Движок зовётся как `git`: подпроцесс, без демона, без сети. Читающее
подмножество openCypher — единственный способ чтения: схема его SQLite
не документирована, и привязываться к ней напрямую нельзя.

ЛОВУШКИ ВЕРСИИ 0.6.0. Все четыре дают не ошибку, а тихий неверный ответ,
и все четыре измерены (протокол — в `docs/findings-codebase-memory.md`):

1. **у обоих концов ребра обязана быть метка.** `MATCH (a)-[:CALLS]->(b)`
   возвращает ноль строк и ни одного сообщения; работает только
   `MATCH (a:Method)-[:CALLS]->(b:Method)`. Отсюда перебор пар меток
   и проверка полноты суммой против счётчика схемы;
2. **неизвестное имя свойства даёт пустую строку**, а не ошибку. Работают
   `qualified_name` и `file_path`; `file`, `path`, `line`, `signature`
   молча пусты;
3. **агрегатов и `labels()` нет.** Счётчики берутся у `get_graph_schema`;
4. **ответ об индексации не содержит ни списка непрочитанных файлов, ни
   отметок о частичном разборе.** Протокол v0.10.8 обещал `not_indexed_files`
   с причиной на файл и `parse_partial` с диапазонами строк; в 0.6.0 ответ —
   это `project`, `status`, `nodes`, `edges`. Значит, покрытие считаем сами:
   сравнением нашего файлового множества с файлами, встреченными среди узлов.
   Пока такого счёта нет, «движок ничего не нашёл в файле» неотличимо
   от «в файле нечего находить» — и это записано как дыра, а не забыто;
5. **имя проекта движок выводит из абсолютного пути** и флага для его задания
   в 0.6.0 нет. Значит, имя берётся из ответа `index_repository` и живёт один
   прогон, а из полных имён узлов оно срезается: иначе индекс зависел бы
   от того, где лежит чекаут.
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from docpipe.hashing import content_hash

# Версия, на которой ведётся разработка, и чек-сумма той сборки, на которой
# она велась. Спрос с них разный, и путать его нельзя: несовпадение версии —
# отказ (другая версия движка — другие числа), несовпадение чек-суммы —
# предупреждение (см. `EngineIdentity`).
EXPECTED_VERSION: Final[str] = "0.6.0"
EXPECTED_SHA256: Final[str] = (
    "sha256:3a3b64911605742954709d4346506393994d0ec0e7cf6f343c13b1b0fb39e6c7"
)

# Метки узлов движка, которые нас интересуют. Перечень положительный: узел
# нового вида должен попасть сюда осознанно, а не просочиться.
NODE_LABELS: Final[tuple[str, ...]] = (
    "Method",
    "Function",
    "Class",
    "Interface",
    "Enum",
)

# Виды рёбер движка, которые проецируются в наш индекс. `USAGE` сюда
# не входит: это не «неразрешённый вызов», а смесь чтений полей, констант
# и типов, и считать по ней долю разрешённого — получить бессмысленно
# заниженное число, которое выглядит убедительно.
EDGE_TYPES: Final[tuple[str, ...]] = ("CALLS", "INHERITS", "IMPLEMENTS")

# Каталоги, которые разборщик 0.6.0 пропускает **молча** и на любом языке.
# Замерено прямым прогоном 21.08.2026: в репозитории из двенадцати каталогов
# с одинаковым файлом внутри проиндексированы `app`, `clients`, `lib`, `src`,
# `test`, `tests`, `utils`, а `tools`, `scripts`, `build`, `vendor`, `bin` —
# нет. Ни ошибки, ни строки в его выводе при этом не появляется.
#
# Почему это важно записать: интеграционный код — клиенты чужих сервисов,
# выгрузки, скрипты миграции — живёт ровно там. Символ, объявленный
# в `tools/`, есть в манифесте и отсутствует в графе, и без этого списка
# число «есть в манифесте — нет в графе» выглядит шумом разбора.
SKIPPED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {"tools", "scripts", "build", "vendor", "bin"}
)


def in_skipped_directory(path: str) -> bool:
    """Лежит ли путь под каталогом, который разборщик пропускает."""
    return any(segment in SKIPPED_DIRECTORIES for segment in path.split("/")[:-1])


# Наши имена для видов рёбер движка. Словарь движка за пределы моста
# не выходит.
EDGE_KIND: Final[dict[str, str]] = {
    "CALLS": "calls",
    "INHERITS": "inherits",
    "IMPLEMENTS": "implements",
}

NODE_KIND: Final[dict[str, str]] = {
    "Method": "member",
    "Function": "member",
    "Class": "type",
    "Interface": "type",
    "Enum": "type",
}


# Литерал регулярного выражения из JS: `/&/g`, `/---/g`, `/%20/g`.
# Маршрутом такое не является, а по форме на путь похоже.
#
# Тело **без косой черты**, флаги — только настоящие флаги регулярки.
# Наивное `^/.*/[a-z]*$` ловит и `/api/items`: жадная точка съедает `/api`,
# и «маршрут» объявляется мусором. Ошибка тихая — путь просто исчезает
# из перекрёстной проверки, и число расхождения растёт без причины.
_REGEX_LITERAL: Final[re.Pattern[str]] = re.compile(r"^/[^/]*/[gimsuy]*$")

# Имя узла маршрута у разбора собрано с префиксом: `__route__ANY__/api/x`.
_ROUTE_PREFIX: Final[str] = "__route__"


def _split_route(name: str) -> tuple[str, str]:
    """Разобрать имя узла маршрута на метод и путь."""
    if not name.startswith(_ROUTE_PREFIX):
        return "", name.strip()
    rest = name[len(_ROUTE_PREFIX) :]
    method, _, route = rest.partition("__")
    return method.strip().upper(), route.strip()


class EngineError(RuntimeError):
    """Отказ движка: не установлен, упал, вернул не-JSON.

    Отдельный тип, потому что политику отказа задаёт вызывающий: сборка графа
    падает, а проверка окружения печатает диагностику и продолжает.
    """


@dataclass(frozen=True)
class EngineNode:
    label: str
    qualified_name: str
    file: str


@dataclass(frozen=True)
class EngineEdge:
    kind: str
    source: str
    target: str


@dataclass(frozen=True)
class EngineGraph:
    """Прочитанный граф движка, уже в наших терминах и без его словаря."""

    nodes: tuple[EngineNode, ...] = ()
    edges: tuple[EngineEdge, ...] = ()
    # Счётчики схемы: сколько рёбер каждого вида движок объявил у себя.
    # Нужны, чтобы сверить полноту чтения: перебор пар меток обязан дать
    # ровно столько же, иначе часть графа не прочитана.
    declared_edges: dict[str, int] = field(default_factory=dict)
    read_edges: dict[str, int] = field(default_factory=dict)
    # Всё, что движок объявил у себя, включая виды, которых нет в нашей
    # модели (структурные `DEFINES`, `CONTAINS_*`). Они не мусор и не потеря,
    # но их число обязано быть видно: «у разборщика 83 ребра, у нас 5» без
    # объяснения выглядит как потеря семидесяти восьми.
    declared_all: dict[str, int] = field(default_factory=dict)
    declared_node_labels: dict[str, int] = field(default_factory=dict)
    # Обращения члена к типу. В индекс НЕ проецируются: это не «неразрешённый
    # вызов», а смесь чтений полей, констант и упоминаний типов. Нужны они
    # ровно для одного — увидеть, что метод создаёт объект запроса, у которого
    # есть объявленный обработчик (диспетчеризация по типу).
    usages: tuple[EngineEdge, ...] = ()
    # Маршруты, которые разбор нашёл во фронтовом коде: пара «метод, путь».
    # В индекс не идут — это перекрёстная проверка нашего разбора фронта,
    # второй независимый счёт. Мусор отсеян и посчитан отдельно.
    http_routes: tuple[tuple[str, str], ...] = ()
    http_noise: int = 0
    # Отсеянное — не молчание, а числа для отчёта о неполноте.
    filtered_nodes: dict[str, int] = field(default_factory=dict)
    not_indexed: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineRun:
    """Результат индексации: имя проекта живёт один прогон."""

    project: str
    nodes: int
    edges: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class EngineIdentity:
    """Кто именно ответил на вызовы: версия, чек-сумма файла и совпала ли она.

    Отдельный тип, потому что у двух половин удостоверения разная цена ошибки,
    и пока они были одним отказом, дешёвая половина роняла прогон.

    **Версия** отвечает на «тот ли это движок». Другая версия — другие числа
    и другое качество разрешения вызовов, продолжать нельзя.

    **Чек-сумма** различает сборки одной версии, а они законно разные: бинарь
    не воспроизводим бит в бит (BuildID зависит в том числе от путей сборки),
    и на закрытом контуре стои́т не тот файл, что собран у разработчика, — при
    той же версии и том же поведении. Поэтому расхождение здесь предупреждение,
    а не отказ.

    Предупреждение обязано быть: «другая сборка» и «подменённый бинарь»
    выглядят одинаково, и различает их человек. Молча пропустить нельзя ни то
    ни другое, поэтому фактическая сумма ещё и уходит в паспорт индекса —
    предупреждение живёт один прогон, паспорт остаётся.
    """

    version: str
    checksum: str
    expected: str
    matches_pin: bool

    @property
    def warning(self) -> str | None:
        """Текст предупреждения или `None`, если сверять было нечего или сошлось."""
        if self.matches_pin:
            return None
        return (
            "чек-сумма движка не совпала с закреплённой.\n"
            f"  ожидалась: {self.expected}\n"
            f"  получена:  {self.checksum}\n"
            f"Версия при этом та ({self.version}), и на закрытом контуре это "
            "ожидаемо: бинарь не воспроизводим бит в бит, там стои́т своя сборка. "
            "Если сборка на этой машине не менялась — разбирайтесь, чем заменён "
            "файл. Чтобы закрепить свою, положите полученную сумму в ключ "
            "`graph.engine_sha256`."
        )


@dataclass(frozen=True)
class Engine:
    """Настроенный движок. Всё, что нужно знать вызывающему, — эти три поля."""

    binary: Path
    cache_dir: Path
    mode: str = "fast"
    expected_sha256: str = EXPECTED_SHA256
    timeout: int = 1800

    # ── проверки до запуска ──────────────────────────────────────────────

    def check(self) -> EngineIdentity:
        """Удостоверить движок до первого разбора.

        Проверка идёт **до** запуска: разобрать репозиторий не тем движком
        и выяснять это потом — значит получить числа, по которым подмену
        уже не видно.

        Отказ ровно один — версия. Чек-сумма отказом быть перестала: она
        различает сборки, а не версии, и на закрытом контуре стои́т другая
        сборка того же 0.6.0 — это измерено, а не предположено. Отказ по ней
        останавливал бы работу там, где всё в порядке. Разница при этом
        не проглатывается: она уходит предупреждением и фактической суммой
        в паспорт индекса.
        """
        if not self.binary.is_file():
            raise EngineError(
                f"движок разбора не найден: {self.binary}. Ожидается версия "
                f"{EXPECTED_VERSION}; путь задаётся ключом `graph.engine_path`"
            )
        checksum = content_hash(self.binary.read_bytes())
        version = self._version()
        if version != EXPECTED_VERSION:
            raise EngineError(
                f"версия движка не та: получена {version}, ожидается {EXPECTED_VERSION}.\n"
                "Другая версия — другой движок: и числа, и качество разрешения "
                "вызовов у неё свои, а по результату это не видно. В контур прошла "
                f"{EXPECTED_VERSION}; ключ `graph.engine_path` должен вести на неё"
            )
        return EngineIdentity(
            version=version,
            checksum=checksum,
            expected=self.expected_sha256,
            matches_pin=not self.expected_sha256 or checksum == self.expected_sha256,
        )

    def _version(self) -> str:
        proc = subprocess.run(
            [str(self.binary), "--version"], capture_output=True, text=True, timeout=60
        )
        if proc.returncode != 0:
            raise EngineError(f"движок не отвечает на --version: {proc.stderr.strip()[:200]}")
        return proc.stdout.strip().split()[-1]

    # ── вызовы ───────────────────────────────────────────────────────────

    def _call(self, tool: str, payload: dict[str, Any]) -> Any:
        """Позвать инструмент движка и разобрать ответ.

        Форма вызова у 0.6.0 — `cli <tool> '<json>'` с одним позиционным
        аргументом. Флага `--json` там нет: строка с ним падает с
        `unknown tool: --json`.
        """
        env = dict(os.environ)
        # Кэш внутри рабочего каталога прогона, а не в `~/.cache`: два проекта
        # не должны делить кэш, иначе прогон по чужому кэшу и прогон с нуля —
        # два разных входа, которые выглядят одним.
        env["CBM_CACHE_DIR"] = str(self.cache_dir)
        try:
            proc = subprocess.run(
                [str(self.binary), "cli", tool, json.dumps(payload)],
                capture_output=True,
                text=True,
                env=env,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise EngineError(f"движок не ответил за {self.timeout} с на {tool}") from error
        if proc.returncode != 0:
            raise EngineError(
                f"движок вернул код {proc.returncode} на {tool}: {proc.stderr.strip()[:300]}"
            )
        try:
            envelope = json.loads(proc.stdout)
            text = envelope["content"][0]["text"]
            return json.loads(text)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            head = proc.stdout.strip()[:300]
            raise EngineError(f"движок вернул не то, что ожидалось, на {tool}: {head}") from error

    def index(self, root: Path, *, clean_cache: bool = True) -> EngineRun:
        """Проиндексировать репозиторий.

        `clean_cache` по умолчанию включён: инкрементальность движка — чужой
        непроверенный код, и прогон по несвежему кэшу против прогона с нуля —
        два разных входа, которые выглядят одним. Разрешать кэш можно только
        после теста «индекс по кэшу == индекс с нуля».
        """
        if clean_cache and self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        answer = self._call(
            "index_repository", {"repo_path": str(root.resolve()), "mode": self.mode}
        )
        if not isinstance(answer, dict) or "project" not in answer:
            raise EngineError(f"индексация не назвала проект: {str(answer)[:200]}")
        return EngineRun(
            project=str(answer["project"]),
            nodes=int(answer.get("nodes", 0)),
            edges=int(answer.get("edges", 0)),
            raw=answer,
        )

    def query(self, project: str, query: str, limit: int = 400_000) -> list[list[str]]:
        answer = self._call("query_graph", {"project": project, "query": query, "max_rows": limit})
        rows = answer.get("rows", []) if isinstance(answer, dict) else []
        return [[str(value) for value in row] for row in rows]

    def schema(self, project: str) -> dict[str, dict[str, int]]:
        """Счётчики узлов и рёбер: агрегатов в Cypher нет, счёт берётся здесь."""
        answer = self._call("get_graph_schema", {"project": project})
        if not isinstance(answer, dict) or "edge_types" not in answer:
            raise EngineError(f"схема графа не прочиталась: {str(answer)[:200]}")
        return {
            "nodes": {row["label"]: int(row["count"]) for row in answer.get("node_labels", [])},
            "edges": {row["type"]: int(row["count"]) for row in answer.get("edge_types", [])},
        }

    # ── чтение ───────────────────────────────────────────────────────────

    def _http_routes(self, project: str, prefix: str) -> tuple[tuple[tuple[str, str], ...], int]:
        """Маршруты, найденные разбором во фронтовом коде.

        **Половина из них — мусор, и это измерено.** Узлы маршрутов собираются
        по литералам, а литерал регулярного выражения из минифицированного
        JS (`/&/g`, `/---/g`) выглядит как путь. Правило отсева записано здесь
        и его срабатывания считаются: молча выброшенное ребро через месяц
        неотличимо от потерянного.
        """
        found: set[tuple[str, str]] = set()
        noise = 0
        query = (
            "MATCH (x:Method)-[:HTTP_CALLS]->(y:Route) RETURN x.qualified_name, y.qualified_name"
        )
        for row in self.query(project, query):
            target = (row + ["", ""])[1].removeprefix(prefix)
            method, route = _split_route(target)
            if not route or _REGEX_LITERAL.match(route):
                noise += 1
                continue
            found.add((method, route))
        return tuple(sorted(found)), noise

    def read(
        self,
        project: str,
        *,
        is_excluded: Callable[[str], bool] | None = None,
    ) -> EngineGraph:
        """Прочитать граф движка и перевести в наши термины.

        Перебор пар меток — не перестраховка, а единственная работающая форма
        (ловушка 1 в шапке модуля). Полнота при этом проверяется числом:
        прочитанное сверяется со счётчиком схемы, и разница объявляется,
        а не замалчивается — рёбра, у которых конец лежит вне наших меток
        (`Module`, `File`, `Folder`), в проекцию не идут по определению.

        Узлы вида `Module` в перечень меток не входят, и это же снимает
        единственный измеренный недетерминизм движка: у модулей таблиц стилей
        `qualified_name` собран без расширения, поэтому `x.css` и `x.scss`
        спорят за одно имя и в разных прогонах побеждает разный файл.
        """
        prefix = f"{project}."
        skip = is_excluded or (lambda _path: False)

        nodes: list[EngineNode] = []
        known: set[str] = set()
        filtered: dict[str, int] = {}
        for label in NODE_LABELS:
            query = f"MATCH (n:{label}) RETURN n.qualified_name, n.file_path"
            for row in self.query(project, query):
                qualified, file = (row + ["", ""])[:2]
                if not qualified:
                    filtered["без имени"] = filtered.get("без имени", 0) + 1
                    continue
                if file and skip(file):
                    filtered["отсев файлового множества"] = (
                        filtered.get("отсев файлового множества", 0) + 1
                    )
                    continue
                name = qualified.removeprefix(prefix)
                nodes.append(EngineNode(label=label, qualified_name=name, file=file))
                known.add(name)

        edges: list[EngineEdge] = []
        read_counts: dict[str, int] = {}
        for edge_type in EDGE_TYPES:
            count = 0
            for source_label in NODE_LABELS:
                for target_label in NODE_LABELS:
                    query = (
                        f"MATCH (x:{source_label})-[:{edge_type}]->(y:{target_label}) "
                        "RETURN x.qualified_name, y.qualified_name"
                    )
                    for row in self.query(project, query):
                        source, target = (row + ["", ""])[:2]
                        if not source or not target:
                            continue
                        source = source.removeprefix(prefix)
                        target = target.removeprefix(prefix)
                        # Оба конца обязаны быть среди прочитанных узлов:
                        # ребро в отсеянный файл — это ребро в никуда,
                        # и его цель ничем не подтверждена.
                        if source not in known or target not in known:
                            continue
                        edges.append(
                            EngineEdge(kind=EDGE_KIND[edge_type], source=source, target=target)
                        )
                        count += 1
            read_counts[EDGE_KIND[edge_type]] = count

        usages: list[EngineEdge] = []
        for source_label in ("Method", "Function"):
            for target_label in ("Class", "Interface", "Enum"):
                query = (
                    f"MATCH (x:{source_label})-[:USAGE]->(y:{target_label}) "
                    "RETURN x.qualified_name, y.qualified_name"
                )
                for row in self.query(project, query):
                    source, target = (row + ["", ""])[:2]
                    if not source or not target:
                        continue
                    source = source.removeprefix(prefix)
                    target = target.removeprefix(prefix)
                    if source in known and target in known:
                        usages.append(EngineEdge(kind="usage", source=source, target=target))

        routes, noise = self._http_routes(project, prefix)

        schema = self.schema(project)
        declared = schema["edges"]
        return EngineGraph(
            nodes=tuple(sorted(nodes, key=lambda item: (item.qualified_name, item.label))),
            edges=tuple(sorted(edges, key=lambda item: (item.kind, item.source, item.target))),
            declared_edges={EDGE_KIND[name]: declared.get(name, 0) for name in EDGE_TYPES},
            read_edges=read_counts,
            declared_all=declared,
            declared_node_labels=schema["nodes"],
            usages=tuple(sorted(usages, key=lambda item: (item.source, item.target))),
            http_routes=routes,
            http_noise=noise,
            filtered_nodes=filtered,
        )
