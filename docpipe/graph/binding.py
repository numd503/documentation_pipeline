"""Связывание: DI-регистрации и то, что из них следует (G04).

На коде с тотальным DI получатель вызова — почти всегда интерфейс,
внедрённый конструктором. Что с этим делает разбор, зависит от версии,
и **обе ветки нужны**:

- разбор может остановиться на интерфейсе. Тогда ребро продолжается
  по регистрации: `caller → IService.Do` даёт `caller → Service.Do`.
  Исходное ребро сохраняется — довершение это второе ребро, а не замена,
  иначе при неверной регистрации потеряется и то, что было верным;
- разбор может довести вызов до реализации **сам, по имени** — так делает
  версия 0.6.0. Довершать тогда нечего, и работа другая: **сверить** выбор
  с регистрацией и **дополнить** рёбрами к остальным зарегистрированным
  реализациям. Где кандидатов на имя несколько, чужой выбор — это догадка,
  и измерено, что одну из реализаций он при этом теряет.

**Ребро `calls` никогда не создаётся из внедрения зависимости.** Узел,
внедривший сервис на тридцать методов и зовущий два, обязан иметь ровно два
ребра. Это та же ошибка, что стоила захода на фронте (51 эндпоинт
у одной страницы), и на .NET она пришла бы не в глаза, а в отчёт о влиянии.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.discovery import map_files_to_modules
from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.keys import normalize_identifier
from docpipe.model import DiRegistration, DispatchDeclaration, DispatchSend, Manifest
from docpipe.symbols import strip_generics

VIA_REGISTRATION: Final[str] = "di:registration"
VIA_COMPLETION: Final[str] = "di:completion"
VIA_ALTERNATIVE: Final[str] = "di:alternative"

# Ребро к другой реализации, построенное поверх выбора, который **разошёлся**
# с регистрацией. Помечено отдельно намеренно: посылка у него слабее, и
# отличать его от обычной альтернативы обязан читатель ответа, а не догадка.
#
# Откуда берётся. На 0.6.0 вызов разрешается по имени члена, поэтому обращение
# к статическому помощнику попадает в одноимённый член реализации интерфейса:
# `SimpleMapper.Map(this, new AlgoliaFlowStep())` в squidex приезжает
# в `AssemblyTypeProvider.Map`, который и правда реализует `ITypeProvider`.
# Структурно этот случай неотличим от полезного — от декоратора, где движок
# выбрал внутреннюю реализацию, а реестр называет обёртку. Различить их
# нечем: получателя вызова у нас нет. Поэтому оба остаются, но считаются
# и метятся врозь.
VIA_DIVERGED: Final[str] = "di:alternative:diverged"


@dataclass(frozen=True)
class BindingReport:
    """Числа, которые дальше печатает отчёт о неполноте."""

    registrations: int = 0
    resolved: int = 0
    unresolved: dict[str, int] = field(default_factory=dict)
    completed: int = 0
    verified: int = 0
    diverged: int = 0
    alternatives: int = 0
    # Рёбра к другим реализациям, построенные поверх разошедшегося выбора.
    # Отдельным числом: посылка у них слабее, и в одной куче с обычными
    # альтернативами их не отличить.
    from_diverged: int = 0
    # Сужение по хосту: сколько раз оно сработало и сколько раз не смогло.
    # Второе число важнее: не сузилось — значит, рёбра пошли ко всем
    # реализациям, включая живущие в другом хосте.
    narrowed: int = 0
    not_narrowed: int = 0
    examples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_counts(self) -> dict[str, int]:
        counts = {
            "DI-регистраций всего": self.registrations,
            "DI-регистраций разобрано": self.resolved,
            "рёбер довершено по регистрации": self.completed,
            "целей подтверждено регистрацией": self.verified,
            "целей разошлось с регистрацией": self.diverged,
            "рёбер к другим реализациям поверх расхождения": self.from_diverged,
            "рёбер к другим зарегистрированным реализациям": self.alternatives,
            "довершений сужено по хосту": self.narrowed,
            "довершений не сужено (хост неизвестен)": self.not_narrowed,
        }
        for reason, number in sorted(self.unresolved.items()):
            counts[f"DI-регистраций не разобрано: {reason}"] = number
        return counts


class _Index:
    """Разложение узлов кода по тому, как их спрашивают.

    **`partial class` — один тип и несколько узлов.** Ключ узла графа содержит
    файл, а объявление типа живёт в нескольких файлах: у `PricingService`
    в канонической фикстуре два узла. Без сведения их в один логический тип
    регистрация выглядит как две конкурирующие реализации, уверенность падает
    вдвое на ровном месте, а члены ищутся не в том узле. Сводит их
    `symbol_key` из манифеста; без манифеста узел остаётся сам себе типом.
    """

    def __init__(self, nodes: tuple[GraphNode, ...]) -> None:
        self.by_key = {node.key: node for node in nodes}

        groups: dict[str, list[str]] = {}
        for node in nodes:
            if node.kind != "type":
                continue
            identity = node.attributes.get("symbol_key") or node.key
            groups.setdefault(identity, []).append(node.key)
        self.canonical: dict[str, str] = {}
        self.siblings: dict[str, list[str]] = {}
        for keys in groups.values():
            head = min(keys)
            self.siblings[head] = sorted(keys)
            for key in keys:
                self.canonical[key] = head

        self.types_by_fqn: dict[str, list[str]] = {}
        self.types_by_name: dict[str, list[str]] = {}
        self.members_by_type: dict[tuple[str, str], str] = {}
        for node in nodes:
            if node.kind == "type":
                head = self.canonical[node.key]
                fqn = node.attributes.get("fqn")
                if fqn:
                    self._add(self.types_by_fqn, normalize_identifier(fqn), head)
                self._add(self.types_by_name, normalize_identifier(node.name), head)
            elif node.kind == "member":
                owner = f"{node.file}#{node.owner}" if node.file else node.owner
                self.members_by_type[(owner, normalize_identifier(node.name))] = node.key

    @staticmethod
    def _add(mapping: dict[str, list[str]], key: str, value: str) -> None:
        bucket = mapping.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    def types(self, raw: str) -> list[str]:
        """Найти тип по тексту из регистрации.

        Сначала полное имя, потом короткое. Дженерик снимается тем же
        `strip_generics`, что и везде: `AddSingleton<IPricingProvider<string>,
        CurveProvider>()` регистрирует `IPricingProvider`, а не тип с именем,
        в котором есть угловые скобки.
        """
        name = strip_generics(raw).strip()
        if not name:
            return []
        exact = self.types_by_fqn.get(normalize_identifier(name))
        if exact:
            return exact
        return self.types_by_name.get(normalize_identifier(name.rsplit(".", 1)[-1]), [])

    def owner_type(self, member_key: str) -> str | None:
        node = self.by_key.get(member_key)
        if node is None or node.kind != "member":
            return None
        owner = f"{node.file}#{node.owner}" if node.file else node.owner
        return self.canonical.get(owner)

    def member_of(self, type_key: str, name: str) -> str | None:
        """Член логического типа: ищется во всех файлах, где он объявлен."""
        for sibling in self.siblings.get(type_key, [type_key]):
            found = self.members_by_type.get((sibling, normalize_identifier(name)))
            if found:
                return found
        return None


def _reachable_projects(manifest: Manifest) -> dict[str, set[str]]:
    """Какие модули достижимы из модуля по ссылкам проектов.

    Нужно для сужения по хосту: интерфейс, зарегистрированный в разных хостах
    на разные реализации, глобально довершать нельзя — вызывающий из хоста А
    получил бы реализацию, живущую только в хосте Б, то есть уверенное неверное
    ребро, худший вид ошибки.
    """
    # Ключ — путь файла проекта, а не `id` модуля: символ манифеста несёт
    # именно путь (`Symbol.module`), и ссылки между проектами записаны
    # путями же. Ключ по `id` выглядел бы правильным и не совпадал бы
    # ни с одним узлом — сужение молча не срабатывало бы никогда.
    direct = {module.project_file: set(module.project_references) for module in manifest.modules}
    reachable: dict[str, set[str]] = {}
    for project in direct:
        seen: set[str] = set()
        stack = [project]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(
                reference for reference in direct.get(current, set()) if reference not in seen
            )
        reachable[project] = seen
    return reachable


def binds(
    registrations: list[DiRegistration],
    nodes: tuple[GraphNode, ...],
    manifest: Manifest | None = None,
) -> tuple[list[GraphEdge], BindingReport]:
    """Рёбра `binds`: интерфейс → реализация, с местом регистрации.

    Каждое ребро несёт проект, в котором сделана регистрация. Один репозиторий
    собирает несколько приложений, и интерфейс, зарегистрированный в разных
    хостах на разные реализации, глобально довершать нельзя.
    """
    index = _Index(nodes)
    projects: dict[str, str] = {}
    if manifest is not None:
        # Проект хранится путём файла проекта, а не `id` модуля: символ
        # манифеста несёт именно путь, и ссылки между проектами записаны
        # путями. `id` выглядел бы правильным и не совпал бы ни с чем.
        projects = map_files_to_modules(
            sorted({registration.file for registration in registrations}),
            sorted(module.project_file for module in manifest.modules),
        )
    edges: list[GraphEdge] = []
    unresolved: dict[str, int] = {}
    examples: list[str] = []
    resolved = 0

    for registration in registrations:
        where = f"{registration.file}:{registration.line}"
        if not registration.impl_type:
            # `AddTransient<Foo>()` — регистрация самого типа, интерфейса нет.
            # Это не «не разобрано»: связывать нечего, и ребра тут не бывает.
            unresolved["регистрация без интерфейса"] = (
                unresolved.get("регистрация без интерфейса", 0) + 1
            )
            continue
        services = index.types(registration.service_type)
        implementations = index.types(registration.impl_type)
        if services and services == implementations:
            # `AddTransient<Foo>()` — тип зарегистрирован на себя. Ребро
            # «интерфейс → реализация» здесь не появляется, потому что
            # интерфейса нет; регистрация при этом законная и посчитана.
            unresolved["регистрация типа на себя"] = (
                unresolved.get("регистрация типа на себя", 0) + 1
            )
            continue
        if not services or not implementations:
            reason = "тип не найден в графе"
            unresolved[reason] = unresolved.get(reason, 0) + 1
            if len(examples) < 10:
                examples.append(f"{where}: {registration.service_type} → {registration.impl_type}")
            continue
        resolved += 1
        # Несколько кандидатов на имя — уверенность делится: выбирать одного
        # молча нельзя, а терять регистрацию тем более.
        weight = round(1 / (len(services) * len(implementations)), 3)
        for service in services:
            for implementation in implementations:
                edges.append(
                    GraphEdge(
                        kind="binds",
                        source=service,
                        target=implementation,
                        via=f"{VIA_REGISTRATION}@{where}",
                        confidence=weight if weight < 1 else 1.0,
                        attributes={
                            name: value
                            for name, value in (
                                ("lifetime", registration.lifetime),
                                ("registration", where),
                                ("project", projects.get(registration.file, "")),
                            )
                            if value
                        },
                    )
                )
    report = BindingReport(
        registrations=len(registrations),
        resolved=resolved,
        unresolved=unresolved,
        examples={"регистрации без узла в графе": tuple(examples)},
    )
    return sorted(edges, key=lambda edge: (edge.source, edge.target, edge.via)), report


def complete(
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
    bind_edges: list[GraphEdge],
    report: BindingReport,
    manifest: Manifest | None = None,
) -> tuple[list[GraphEdge], BindingReport]:
    """Довершить и сверить рёбра вызова по регистрациям.

    Две ветки, и обе обязательны, потому что поведение источника зависит
    от его версии (см. шапку модуля). Ни одна из них не создаёт ребро
    на пустом месте: обе работают только там, где вызов уже есть.
    """
    index = _Index(nodes)
    registered: dict[str, list[GraphEdge]] = {}
    for edge in bind_edges:
        registered.setdefault(edge.source, []).append(edge)

    reachable = _reachable_projects(manifest) if manifest is not None else {}
    narrowed = not_narrowed = 0

    def visible(caller: str, options: list[GraphEdge]) -> tuple[list[GraphEdge], bool]:
        """Реализации, чья регистрация достижима из проекта вызывающего.

        Не сузилось — берутся все, и это отдельное число в отчёте, а не
        молчание: вызывающий из хоста А иначе получил бы реализацию,
        живущую только в хосте Б.
        """
        node = index.by_key.get(caller)
        module = node.attributes.get("module", "") if node is not None else ""
        if not module or module not in reachable:
            return options, False
        allowed = reachable[module]
        narrowed_options = [
            option
            for option in options
            if not option.attributes.get("project") or option.attributes["project"] in allowed
        ]
        if not narrowed_options or len(narrowed_options) == len(options):
            return options, len(narrowed_options) == len(options)
        return narrowed_options, True

    # Что реализует тип: у разбора это `inherits`, и различать «наследует»
    # от «реализует» он не умеет — для связывания разница несущественна,
    # важно лишь, что тип связан с интерфейсом, у которого есть регистрация.
    implements: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind == "inherits":
            source = index.canonical.get(edge.source, edge.source)
            target = index.canonical.get(edge.target, edge.target)
            implements.setdefault(source, []).append(target)

    existing = {(edge.kind, edge.source, edge.target) for edge in edges}
    produced: list[GraphEdge] = []
    completed = verified = diverged = alternatives = from_diverged = 0
    diverged_examples: list[str] = []

    def add(source: str, target: str, via: str, confidence: float) -> bool:
        identity = ("calls", source, target)
        # Довершение не дублирует: если разбор сам довёл вызов до реализации,
        # второго ребра не создаётся.
        if identity in existing:
            return False
        existing.add(identity)
        produced.append(
            GraphEdge(kind="calls", source=source, target=target, via=via, confidence=confidence)
        )
        return True

    for edge in edges:
        if edge.kind != "calls":
            continue
        target_node = index.by_key.get(edge.target)
        if target_node is None or target_node.kind != "member":
            continue
        owner = index.owner_type(edge.target)
        if owner is None:
            continue

        # Ветка 1: цель — член интерфейса, у которого есть регистрации.
        if owner in registered:
            options, was_narrowed = visible(edge.source, registered[owner])
            narrowed += 1 if was_narrowed else 0
            not_narrowed += 0 if was_narrowed else 1
            weight = round(1 / len(options), 3)
            for bind in options:
                member = index.member_of(bind.target, target_node.name)
                if member and add(edge.source, member, VIA_COMPLETION, weight):
                    completed += 1
            continue

        # Ветка 2: цель — реализация. Сверяем выбор с регистрацией.
        #
        # Вызов ВНУТРИ типа через интерфейс не проходит: `this.GetBrands()`
        # — это не обращение к сервису, а собственный метод. Измерено:
        # без этой проверки внутренний вызов `GetCatalogItems → GetBrands`
        # порождал ребро в одноимённый метод декоратора, то есть уверенное
        # неверное ребро ровно того вида, ради которого сверка и делается.
        if index.owner_type(edge.source) == owner:
            continue
        interfaces = [name for name in implements.get(owner, []) if name in registered]
        for interface in interfaces:
            options, was_narrowed = visible(edge.source, registered[interface])
            narrowed += 1 if was_narrowed else 0
            not_narrowed += 0 if was_narrowed else 1
            targets = {bind.target for bind in options}
            confirmed = owner in targets
            if confirmed:
                verified += 1
            else:
                diverged += 1
                if len(diverged_examples) < 10:
                    diverged_examples.append(f"{edge.source} → {edge.target}")
            weight = round(1 / len(options), 3)
            # Посылка у разошедшегося выбора слабее: уверенность делится ещё
            # раз. Не отбрасывается — из расхождения бывает и польза (декоратор),
            # и вред (совпадение имён), а различить их нечем.
            via = VIA_ALTERNATIVE if confirmed else VIA_DIVERGED
            for other in sorted(targets - {owner}):
                member = index.member_of(other, target_node.name)
                if member and add(edge.source, member, via, weight if confirmed else weight / 2):
                    if confirmed:
                        alternatives += 1
                    else:
                        from_diverged += 1

    updated = BindingReport(
        registrations=report.registrations,
        resolved=report.resolved,
        unresolved=report.unresolved,
        completed=completed,
        verified=verified,
        diverged=diverged,
        alternatives=alternatives,
        from_diverged=from_diverged,
        narrowed=narrowed,
        not_narrowed=not_narrowed,
        examples={
            **report.examples,
            "цель разошлась с регистрацией": tuple(diverged_examples),
        },
    )
    return sorted(produced, key=lambda item: (item.source, item.target, item.via)), updated


def dispatch(
    nodes: tuple[GraphNode, ...],
    sends: tuple[DispatchSend, ...],
    handlers: list[DispatchDeclaration],
) -> tuple[list[GraphEdge], dict[str, int]]:
    """Диспетчеризация по типу запроса: `Send(new X())` → обработчик X.

    Две половины, и ни одной из них по отдельности не хватает:

    - **объявление** («этот тип обслуживает такой запрос») приходит из разбора
      объявлений: это базовый тип с аргументом дженерика, и аргумент есть
      только там;
    - **место отправки** («вот здесь создают X») приходит из разбора тела:
      это создание объекта запроса. Вызова с именем обработчика в коде нет
      вовсе. Первая редакция брала место отправки из обращений члена к типу
      у стороннего разбора — и получала только петли «обработчик
      диспетчеризует сам себя»: создание объекта он обращением не считает.

    Тип запроса живёт **на ребре**, а не на вызове: тот же метод обработчика
    зовут и диспетчер, и код напрямую, и различить их можно только так.
    """
    index = _Index(nodes)
    by_request: dict[str, list[DispatchDeclaration]] = {}
    for handler in handlers:
        by_request.setdefault(normalize_identifier(handler.request_type), []).append(handler)

    edges: list[GraphEdge] = []
    report: dict[str, int] = {
        "объявленных обработчиков по типу запроса": len(handlers),
        "мест отправки запроса": len(sends),
        "рёбер диспетчеризации по типу запроса": 0,
        "обработчиков без места отправки": 0,
    }
    used: set[str] = set()

    members_by_place: dict[tuple[str, str], str] = {}
    for node in nodes:
        if node.kind == "member" and node.file:
            members_by_place.setdefault((node.file, normalize_identifier(node.name)), node.key)

    for send in sends:
        source = members_by_place.get((send.file, normalize_identifier(send.member)))
        if source is None:
            report["мест отправки без узла кода"] = report.get("мест отправки без узла кода", 0) + 1
            continue
        for handler in by_request.get(normalize_identifier(send.request_type), []):
            targets = index.types(handler.handler_fqn)
            if not targets:
                continue
            # Обработчик создаёт свой же запрос — это не отправка, а, например,
            # тест или фабрика внутри самого обработчика.
            if index.owner_type(source) in targets:
                continue
            used.add(handler.handler_fqn)
            for handler_type in targets:
                # Ребро ведёт в метод обработчика, если он опознан по имени
                # интерфейса, и в сам тип, если нет: терять связь целиком
                # из-за неизвестного имени метода было бы дороже.
                member = index.member_of(handler_type, "Handle")
                edges.append(
                    GraphEdge(
                        kind="dispatches",
                        source=source,
                        target=member or handler_type,
                        via=f"dispatch:{handler.interface}",
                        attributes={"request": handler.request_type},
                    )
                )
                report["рёбер диспетчеризации по типу запроса"] += 1

    report["обработчиков без места отправки"] = len(
        {handler.handler_fqn for handler in handlers} - used
    )
    return sorted(edges, key=lambda edge: (edge.source, edge.target, edge.via)), report
