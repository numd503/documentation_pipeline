"""Разрешение якорей: от объявленной точки контракта к разрешённым фактам.

Ступени идут по порядку, первая сработавшая выигрывает:

    1. реестр            запись объявлена в `registries.yaml` — известны
                         реализация, сборка, команда;
    2. литерал           строка встречается в исходниках, перечисленных
                         манифестом;
    3. индекс символов   только для `kind: type` — тип существует среди узлов;
    4. не разрешён       находка линта, если `verify != false`.

**Ловушка. Для якорей, диспетчеризуемых по данным, вторая ступень пуста
структурно.** Workflow, джоб, grid-сервис и обработчик события исполняются
универсальным раннером, который получает имя типа из XML. Литерала конкретного
workflow в C# не существует вовсе, и реализация, считающая пустой литеральный
поиск проблемой, дала бы вечно красный линт по всем 45 workflow — после чего
его выключили бы целиком.

**Ловушка. Литеральный поиск идёт по `symbol.sources[].path` из манифеста,
а не обходом файловой системы.** Обход втянул бы вывод сборки, тесты и каталог
самого инструмента, и совпадение в них выглядело бы доказательством.
"""

from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.business.model import Anchor, Selector
from docpipe.materialize.ownership import Ownership, owner_of
from docpipe.model import DocNode, Manifest
from docpipe.registry.anchors import AnchorTarget, ResolvedAnchor
from docpipe.registry.parse import parse_schedule
from docpipe.route import normalize_route
from docpipe.web.pages import DEFAULT_DEPTH as PAGE_DEPTH
from docpipe.web.pages import STATE_KIND, called, state_name

Confidence = Literal["registry", "literal", "symbol", "unresolved"]

# Словари видов расходятся намеренно, и это стоит держать в одном месте.
# Аналитик пишет `table` и `kafka` — слова его предметной области; реестр
# объявляет `list` и `kafka_topic` — слова платформы. Свести их к одному
# набору значило бы либо заставить аналитика писать `kafka_topic`, либо
# переименовать вид в реестре, который читается ещё и инвентаризацией.
REGISTRY_KIND: Final[dict[str, str]] = {
    "grid_service": "grid_service",
    "job": "job",
    "workflow": "workflow",
    "workflow_step": "workflow_step",
    "list_event": "list_event",
    "table": "list",
    "kafka": "kafka_topic",
}

# Обратный мост, для перевода найденной записи реестра в вид якоря документа.
# Строится из `REGISTRY_KIND`, а не пишется руками: два словаря про одно
# и то же разошлись бы на первой же новой паре, и `anchors which` начал бы
# предлагать вид, которого каталог не знает.
ANCHOR_KIND_BY_REGISTRY: Final[dict[str, str]] = {
    registry: anchor for anchor, registry in REGISTRY_KIND.items()
}

# Виды, для которых литеральная ступень пуста структурно: диспетчеризация
# идёт по данным. Перечень положительный, а не «всё, кроме»: новый вид якоря
# обязан попасть сюда осознанно.
DATA_DISPATCHED: Final[frozenset[str]] = frozenset(
    {"grid_service", "job", "workflow", "workflow_step", "list_event", "table"}
)

# Сколько байт файла читать при литеральном поиске. Исходник больше мегабайта —
# это выгрузка или сгенерированный файл, и совпадение в нём ничего не доказывает.
LITERAL_MAX_BYTES: Final[int] = 1_000_000


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Resolution(_Base):
    """Результат разрешения одного якоря.

    `facts` — то, что видно бизнесу, уже нормализованное: ни имён C#-типов,
    ни путей, ни namespace. Из них считается `business_hash`, поэтому состав
    этого словаря и есть ответ на вопрос «какой рефакторинг создаёт работу».

    `candidates` непуст, когда ссылка неоднозначна: workflow объявлен
    несколькими версиями, а `version` в якоре не указана. Выбирать самим
    нельзя — версия шагов бизнес-видима, и молчаливый выбор одной из них
    зафиксировал бы в хэше произвольную.
    """

    anchor: Anchor
    confidence: Confidence
    facts: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    targets: list[AnchorTarget] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    tried: list[str] = Field(default_factory=list)

    # Якорь нашёлся, а `only` не совпал ни с одной записью. Это отдельное
    # состояние, а не «точка входа исчезла»: чинится одной строкой в документе,
    # и находка линта у него своя, с перечнем того, что сейчас на якоре.
    selector_missed: bool = False

    @property
    def resolved(self) -> bool:
        return self.confidence != "unresolved"


class ResolveContext(_Base):
    """Всё, что нужно для разрешения, собранное один раз.

    Индексы строятся заранее: якорей в каталоге сотни, а перебор реестров
    на каждый из них превратил бы линт в минуты.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    by_key: dict[tuple[str, str, str], list[ResolvedAnchor]] = Field(default_factory=dict)
    nodes_by_id: dict[str, DocNode] = Field(default_factory=dict)

    # Страницы фронта, ключ — нормализованный маршрут. Приходят из ВТОРОГО
    # манифеста: шаг `web` пишет свой файл, и бизнес-слой читает оба.
    # Пустой словарь — законное состояние: репозиторий без фронта.
    pages_by_route: dict[str, list[DocNode]] = Field(default_factory=dict)
    web_nodes_by_fqn: dict[str, DocNode] = Field(default_factory=dict)
    source_paths: list[str] = Field(default_factory=list)
    root: Path | None = None

    # Нужно только селектору `only.team`. Без правил владения он не сужает
    # ничего и обязан сказать это вслух: молча пустой результат неотличим
    # от «на якоре нет наших записей».
    ownership: Ownership | None = None


def _key(kind: str, scope: str | None, ref: str) -> tuple[str, str, str]:
    return (kind, scope or "", ref)


def build_context(
    anchors: list[ResolvedAnchor],
    manifest: Manifest,
    root: Path | None = None,
    ownership: Ownership | None = None,
    web: Manifest | None = None,
) -> ResolveContext:
    """Собрать индексы по инвентаризации и манифесту.

    Дети индексируются со `scope` родителя: шаг workflow объявлен внутри
    workflow, и в каталоге на него ссылаются именно парой. Сама
    инвентаризация оставляет `scope` детей пустым — она показывает их
    вложенными, а не самостоятельными.
    """
    by_key: dict[tuple[str, str, str], list[ResolvedAnchor]] = {}

    def add(anchor: ResolvedAnchor, scope: str | None) -> None:
        by_key.setdefault(_key(anchor.kind, scope, anchor.ref), []).append(anchor)

    for anchor in anchors:
        add(anchor, anchor.scope)
        for child in anchor.children:
            add(child, anchor.ref)

    paths = {span.path for node in manifest.nodes if node.symbol for span in node.symbol.sources}

    pages: dict[str, list[DocNode]] = {}
    for node in web.nodes if web else []:
        if node.kind != "page":
            # Маршрут в таблице есть, а вид страницы снят руками (`pages.yaml`).
            # Считать такой узел страницей значило бы вернуть в покрытие экран,
            # который человек из документации убрал.
            continue
        for entry in node.routes:
            if entry.route_unresolved:
                # Маршрут собрать не удалось: якорь на него поставить нельзя,
                # и делать вид, что страница на пустом пути, — значит склеить
                # все такие ветки в один ключ.
                continue
            pages.setdefault(entry.path, []).append(node)

    return ResolveContext(
        by_key={
            key: sorted(items, key=lambda a: (a.version or "", a.source_path))
            for key, items in by_key.items()
        },
        nodes_by_id={node.id: node for node in manifest.nodes},
        pages_by_route={
            route: sorted(nodes, key=lambda node: node.id) for route, nodes in sorted(pages.items())
        },
        web_nodes_by_fqn={
            node.symbol.fqn: node for node in (web.nodes if web else []) if node.symbol
        },
        source_paths=sorted(paths),
        root=root,
        ownership=ownership,
    )


# --------------------------------------------------------------------------------------
# Факты по видам якорей
# --------------------------------------------------------------------------------------
#
# Состав каждого словаря — прямое следствие требования «рефакторинг без смены
# смысла не создаёт работы». Всё, что попало сюда, попадёт в `business_hash`;
# всё, чего здесь нет, менять хэш не будет.


def _steps(anchor: ResolvedAnchor) -> list[dict[str, str]]:
    """Шаги workflow: идентификатор и переход.

    `StepType` не входит: переименование класса шага бизнес-смысла не меняет,
    а сортировка по `Id` убирает влияние порядка записей в JSON.
    """
    return sorted(
        (
            {"id": child.ref, "next": child.fields.get("next", "")}
            for child in anchor.children
            if child.kind == "workflow_step"
        ),
        key=lambda step: step["id"],
    )


def _grid_service_methods(targets: list[AnchorTarget], ctx: ResolveContext) -> list[str]:
    """Публичные методы реализации grid-сервиса.

    Единственное место, где имя C#-элемента входит в `business_hash`, и это
    не оговорка: методы grid-сервиса вызываются по имени через прокси, то есть
    именно они и являются контрактом. Имя класса контрактом не является
    и в хэш не входит.
    """
    names: set[str] = set()
    for target in targets:
        node = ctx.nodes_by_id.get(target.node_id or "")
        if node is None or node.symbol is None:
            continue
        names |= {
            member.name
            for member in node.symbol.members
            if member.kind == "method" and "public" in member.modifiers
        }
    return sorted(names)


def _list_fields(anchor: ResolvedAnchor) -> list[list[str]]:
    """Состав списка: поля с их видом, обязательностью и связью.

    Отсортированы, поэтому перестановка полей в XML хэш не меняет, а
    добавление поля — меняет. Формы (`<Forms>`) не входят: это представление,
    а не состав данных.
    """
    return sorted(
        [
            field.ref,
            field.fields.get("field_type", ""),
            field.fields.get("column", ""),
            field.fields.get("required", ""),
            field.fields.get("display_name", ""),
            field.fields.get("lookup", ""),
        ]
        for field in anchor.children
        if field.kind == "list_field"
    )


def _facts(kind: str, matches: list[ResolvedAnchor], ctx: ResolveContext) -> dict[str, Any]:
    first = matches[0]

    if kind == "workflow":
        return {"id": first.ref, "version": first.version or "", "steps": _steps(first)}

    if kind == "workflow_step":
        return {"id": first.ref, "next": first.fields.get("next", "")}

    if kind == "job":
        # Флаги состояния (`JOBDISABLED`, `Override`) не читаются вовсе:
        # это политика поставки и состояние боевой БД, которой инструмент
        # не видит. Прочитанный флаг однажды прочтут как факт.
        schedule = parse_schedule(first.fields.get("schedule_raw", ""))
        return {
            "title": first.ref,
            "interval_seconds": schedule.interval_seconds if schedule else None,
            "first_time": schedule.first_time if schedule else None,
        }

    if kind == "grid_service":
        return {"name": first.ref, "methods": _grid_service_methods(first.targets, ctx)}

    if kind == "list_event":
        # Участники — сборки, а не классы: переименование класса обработчика
        # состава участников не меняет, появление обработчика из новой
        # сборки — меняет.
        assemblies = {
            match.fields.get("assembly", "") for match in matches if match.fields.get("assembly")
        }
        return {"list": first.scope or "", "event": first.ref, "assemblies": sorted(assemblies)}

    if kind == "table":
        return {"list": first.ref, "fields": _list_fields(first)}

    return {}


# --------------------------------------------------------------------------------------
# Ступени
# --------------------------------------------------------------------------------------


def holder_team(match: ResolvedAnchor, ctx: ResolveContext) -> str | None:
    """Команда записи по правилам владения, через её реализацию."""
    if ctx.ownership is None:
        return None
    for target in match.targets:
        node = ctx.nodes_by_id.get(target.node_id or "")
        if node is not None:
            decision = owner_of(node, ctx.ownership)
            if decision.team:
                return decision.team
    return None


def _holder(match: ResolvedAnchor, ctx: ResolveContext) -> str:
    """Кто сейчас занимает запись якоря — для сообщения о промахе селектора.

    Печатается всё, по чему селектор мог бы сработать: класс, сборка и команда.
    Без команды сообщение бесполезно ровно в том случае, ради которого оно
    и пишется: `only: {team: ml}` при объявленной `ML` не совпадает, и увидеть
    это в перечне из FQN и сборок нельзя — там разницы в регистре просто нет.
    """
    fqn = match.fields.get("impl_fqn") or match.fields.get("contract_fqn") or "—"
    parts = []
    if assembly := match.fields.get("assembly"):
        parts.append(f"сборка {assembly}")
    if team := holder_team(match, ctx):
        parts.append(f"команда {team}")
    return f"{fqn} ({', '.join(parts)})" if parts else fqn


def _selected(match: ResolvedAnchor, selector: Selector, ctx: ResolveContext) -> bool:
    """Попадает ли запись под селектор.

    Сборка сравнивается точным равенством, а не префиксом: `Sbt.Cashflow.ML`
    как префикс поймал бы и `Sbt.Cashflow.MLOps`, и заметили бы это не сразу.

    Команда считается по реализации через `ownership.yaml` — у обработчиков
    событий, джобов и workflow поля `@team` в реестре нет вовсе, оно есть
    только в `services.config`.
    """
    if selector.assembly:
        return match.fields.get("assembly") == selector.assembly

    if ctx.ownership is None:
        return False

    for target in match.targets:
        node = ctx.nodes_by_id.get(target.node_id or "")
        if node is not None and owner_of(node, ctx.ownership).team == selector.team:
            return True
    return False


def _registry_rung(anchor: Anchor, ctx: ResolveContext) -> Resolution | None:
    """Ступень 1: запись объявлена в реестре."""
    registry_kind = REGISTRY_KIND.get(anchor.kind)
    if registry_kind is None:
        return None

    matches = ctx.by_key.get(_key(registry_kind, anchor.scope, anchor.ref), [])
    if not matches:
        return None

    if anchor.version:
        matches = [match for match in matches if match.version == anchor.version]
        if not matches:
            return None

    # Сужение идёт ДО проверки на неоднозначность: две записи на якоре — норма,
    # если наша из них одна. Проверять раньше значило бы объявить чужого
    # подписчика неоднозначностью в нашем документе.
    if anchor.only is not None:
        narrowed = [match for match in matches if _selected(match, anchor.only, ctx)]
        if not narrowed:
            return Resolution(
                anchor=anchor,
                confidence="unresolved",
                candidates=[_holder(match, ctx) for match in matches],
                sources=sorted({match.source_path for match in matches}),
                tried=["реестр"],
                selector_missed=True,
            )
        matches = narrowed

    # Несколько записей на один якорь — норма только для обработчиков события:
    # у одной пары «список + EventType» бывает несколько обработчиков, и весь
    # их набор и есть факт. Во всех остальных видах это неоднозначность.
    if len(matches) > 1 and anchor.kind != "list_event":
        return Resolution(
            anchor=anchor,
            confidence="unresolved",
            candidates=[match.display for match in matches],
            sources=sorted({match.source_path for match in matches}),
            tried=["реестр"],
        )

    targets = [target for match in matches for target in match.targets]
    return Resolution(
        anchor=anchor,
        confidence="registry",
        facts=_facts(anchor.kind, matches, ctx),
        sources=sorted({match.source_path for match in matches}),
        targets=targets,
        tried=["реестр"],
    )


def _literal_rung(anchor: Anchor, ctx: ResolveContext) -> Resolution | None:
    """Ступень 2: строка встречается в исходниках, перечисленных манифестом.

    Для диспетчеризуемых по данным видов ступень пропускается: пустой
    результат там не факт, а свойство платформы.
    """
    if anchor.kind in DATA_DISPATCHED or ctx.root is None or not anchor.ref:
        return None

    needle = anchor.ref
    for rel in ctx.source_paths:
        path = ctx.root / rel
        try:
            data = path.read_bytes()[:LITERAL_MAX_BYTES]
            text = data.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue

        # Номер строки нужен человеку, который пойдёт смотреть: без него
        # «встречается в файле на 3000 строк» не является ответом.
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in line:
                return Resolution(
                    anchor=anchor,
                    confidence="literal",
                    facts={},
                    sources=[f"{rel}:{number}"],
                    tried=["реестр", "литерал"],
                )

    return None


def _symbol_rung(anchor: Anchor, ctx: ResolveContext) -> Resolution | None:
    """Ступень 3: тип существует среди узлов документации.

    Только для `kind: type`. Настоящего индекса символов в манифесте нет —
    он строится при `scan` и не сохраняется, — поэтому ступень видит лишь
    задокументированные типы. Из этого следует ограничение, которое обязано
    быть сказано вслух: «не разрешён» здесь означает «не найден среди узлов
    документации», а не «типа не существует».

    Формы типа (состав полей) ступень не отдаёт: в манифесте её нет —
    у `record` с позиционными параметрами объявлений свойств не существует
    вовсе, и вычисленная по членам форма оказалась бы пустой у всех записей
    сразу. Поэтому `type` разрешается, но в `business_hash` фактов не вносит.
    """
    if anchor.kind != "type":
        return None

    nodes = sorted(
        (
            node
            for node in ctx.nodes_by_id.values()
            if node.symbol and node.symbol.fqn == anchor.ref
        ),
        key=lambda node: node.id,
    )
    if not nodes:
        return None

    return Resolution(
        anchor=anchor,
        confidence="symbol",
        facts={},
        sources=sorted(
            {span.path for node in nodes if node.symbol for span in node.symbol.sources}
        ),
        targets=[
            AnchorTarget(
                field="ref",
                fqn=anchor.ref,
                via="direct",
                node_id=node.id,
                doc_path=node.doc_path,
                module=node.module,
            )
            for node in nodes
        ],
        tried=["реестр", "литерал", "индекс символов"],
    )


def _page_facts(node: DocNode, ctx: ResolveContext) -> dict[str, list[str]]:
    """Факты страницы: списки реестра, эндпоинты и состояние.

    Считаются по **графу вызовов** (`web/pages.called`) — тому же, по которому
    собирается документ страницы. Вторая реализация обхода разошлась бы с ним,
    и бизнес-хэш менялся бы от того, чего в документе не видно.

    Ни одного имени класса здесь нет, и это проверяемая формулировка требования
    «рефакторинг без смены смысла не создаёт работы»:

    - списки — слова платформы (`listInnerName`), они же ключи реестра;
    - эндпоинты — HTTP-контракт, переживающий переименование чего угодно;
    - состояние — `name` из декоратора `@State`, а не имя класса: переименование
      `DebtState` смысла не меняет, а `innerDebt` — контракт, по которому стейт
      селектят.
    """
    walk = called(node, ctx.web_nodes_by_fqn, PAGE_DEPTH)
    calls, participants = walk.calls, walk.participants

    lists = sorted({call.discriminator for call in calls if call.discriminator})
    endpoints = sorted({f"{call.http_method} {call.route}" for call in calls if call.route})
    states = sorted(
        {
            name
            for fqn in participants
            if (item := ctx.web_nodes_by_fqn.get(fqn)) is not None
            and item.kind == STATE_KIND
            and (name := state_name(item))
        }
    )

    facts: dict[str, list[str]] = {}
    if lists:
        facts["lists"] = lists
    if endpoints:
        facts["endpoints"] = endpoints
    if states:
        facts["states"] = states
    return facts


def _page_rung(anchor: Anchor, ctx: ResolveContext) -> Resolution | None:
    """Ступень: страница объявлена в манифесте шага `web`.

    Ключ — нормализованный маршрут, тем же `normalize_route`, что и на обеих
    технических сторонах: своя копия нормализации разошлась бы с ними молча,
    и якорь `/models/:id` перестал бы совпадать с узлом `models/{}`.

    Уверенность `symbol`: страница найдена среди узлов документации — та же
    природа, что у ступени типов. Отдельное значение завело бы третий словарь
    подписей ради одной строки.
    """
    if anchor.kind != "page":
        return None

    route = normalize_route(anchor.ref)
    nodes = ctx.pages_by_route.get(route, [])
    if not nodes:
        return None

    # `route` в фактах дублирует `ref` якоря, и это не избыточность: у якоря,
    # который перестал разрешаться, фактов нет вовсе, и хэш меняется. Иначе
    # исчезнувшая страница выглядела бы как «ничего не произошло» — то самое
    # событие, ради которого хэш и заводится.
    # Факты сливаются по всем узлам маршрута: один путь может обслуживаться
    # несколькими компонентами, и брать первый значило бы описать половину экрана.
    merged: dict[str, list[str]] = {}
    for node in nodes:
        for key, values in _page_facts(node, ctx).items():
            merged[key] = sorted(set(merged.get(key, [])) | set(values))

    return Resolution(
        anchor=anchor,
        confidence="symbol",
        facts={"route": route, **merged},
        sources=sorted(
            {span.path for node in nodes if node.symbol for span in node.symbol.sources}
        ),
        targets=[
            AnchorTarget(
                field="ref",
                fqn=node.symbol.fqn if node.symbol else node.title,
                via="direct",
                node_id=node.id,
                doc_path=node.doc_path,
                module=node.module,
            )
            for node in nodes
        ],
        tried=["реестр", "страница"],
    )


def resolve(anchor: Anchor, ctx: ResolveContext) -> Resolution:
    """Разрешить один якорь. Ступени по порядку, первая сработавшая выигрывает.

    Якорь с `verify: false` не разрешается вовсе: это объявленная граница зоны
    ответственности, а не то, что инструмент не сумел найти. Попытка его
    разрешить дала бы находку линта на чужом коде.
    """
    if not anchor.verify:
        return Resolution(anchor=anchor, confidence="unresolved", tried=[])

    for rung in (_registry_rung, _page_rung, _literal_rung, _symbol_rung):
        resolution = rung(anchor, ctx)
        if resolution is not None:
            return resolution

    tried = ["реестр"]
    if anchor.kind == "page":
        tried.append("страница")
    if anchor.kind not in DATA_DISPATCHED and anchor.kind != "page":
        tried.append("литерал")
    if anchor.kind == "type":
        tried.append("индекс символов")

    return Resolution(anchor=anchor, confidence="unresolved", tried=tried)


def resolve_all(anchors: list[Anchor], ctx: ResolveContext) -> list[Resolution]:
    """Разрешить набор якорей, сохранив порядок объявления."""
    return [resolve(anchor, ctx) for anchor in anchors]
