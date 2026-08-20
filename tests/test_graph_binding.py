"""Связывание: DI-регистрации и то, что из них следует (G04).

Главное свойство, которое здесь держится: **ребро вызова не создаётся
из внедрения зависимости**. Узел, внедривший сервис на тридцать методов
и зовущий два, обязан иметь ровно два ребра — иначе отчёт о влиянии
превращается в лавину, а лавина неотличима от ответа.
"""

from pathlib import Path

from docpipe.graph.binding import binds, complete
from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.model import (
    DiRegistration,
    DispatchDeclaration,
    DispatchSend,
    Manifest,
    Module,
    ParserVersions,
)


def type_node(key: str, name: str, symbol_key: str = "", module: str = "") -> GraphNode:
    attributes = {}
    if symbol_key:
        attributes["symbol_key"] = symbol_key
    if module:
        attributes["module"] = module
    return GraphNode(key=key, kind="type", name=name, file=key.split("#")[0], attributes=attributes)


def member_node(key: str, owner: str, name: str, module: str = "") -> GraphNode:
    return GraphNode(
        key=key,
        kind="member",
        name=name,
        owner=owner,
        file=key.split("#")[0],
        attributes={"module": module} if module else {},
    )


def registration(service: str, impl: str | None, line: int = 10) -> DiRegistration:
    return DiRegistration(
        service_type=service,
        impl_type=impl,
        lifetime="scoped",
        confidence="high",
        file="src/App/Program.cs",
        line=line,
    )


def calls(source: str, target: str) -> GraphEdge:
    return GraphEdge(kind="calls", source=source, target=target, via="engine:CALLS@0.6.0")


def inherits(source: str, target: str) -> GraphEdge:
    return GraphEdge(kind="inherits", source=source, target=target, via="engine:INHERITS@0.6.0")


# ──────────────────────────────────────────────────────────────────────────────
# Рёбра `binds`
# ──────────────────────────────────────────────────────────────────────────────


def test_every_registration_becomes_an_edge() -> None:
    nodes = (
        type_node("src/A/IFoo.cs#IFoo", "IFoo"),
        type_node("src/A/Foo.cs#Foo", "Foo"),
    )
    edges, report = binds([registration("IFoo", "Foo")], nodes)
    assert len(edges) == 1
    assert edges[0].kind == "binds"
    assert edges[0].attributes["registration"] == "src/App/Program.cs:10"
    assert report.registrations == 1
    assert report.resolved == 1


def test_registration_of_a_type_on_itself_is_counted_separately() -> None:
    """`AddTransient<Foo>()` — законная регистрация, но интерфейса в ней нет,
    и ребро «интерфейс → реализация» здесь не появляется."""
    nodes = (type_node("src/A/Foo.cs#Foo", "Foo"),)
    edges, report = binds([registration("Foo", "Foo")], nodes)
    assert edges == []
    assert report.unresolved["регистрация типа на себя"] == 1


def test_registration_without_a_node_is_a_named_number() -> None:
    """Регистрация на тип, которого нет в графе, — находка с примером,
    а не молчание: так выглядит удалённый или несобираемый код."""
    edges, report = binds([registration("IGone", "Gone")], ())
    assert edges == []
    assert report.unresolved["тип не найден в графе"] == 1
    assert report.examples["регистрации без узла в графе"]


def test_generic_service_type_is_stripped() -> None:
    """`AddSingleton<IProvider<string>, Curve>()` регистрирует `IProvider`,
    а не тип, в имени которого есть угловые скобки."""
    nodes = (
        type_node("src/A/IProvider.cs#IProvider", "IProvider"),
        type_node("src/A/Curve.cs#Curve", "Curve"),
    )
    edges, _ = binds([registration("IProvider<string>", "Curve")], nodes)
    assert len(edges) == 1


def test_partial_class_is_one_logical_type() -> None:
    """`partial class` живёт в нескольких файлах и даёт несколько узлов.

    Без сведения их в один логический тип регистрация выглядит как две
    конкурирующие реализации, и уверенность падает вдвое на ровном месте.
    """
    nodes = (
        type_node("src/A/IFoo.cs#IFoo", "IFoo"),
        type_node("src/A/Foo.cs#Foo", "Foo", symbol_key="m#Ns.Foo`0"),
        type_node("src/A/Foo.Extra.cs#Foo", "Foo", symbol_key="m#Ns.Foo`0"),
    )
    edges, _ = binds([registration("IFoo", "Foo")], nodes)
    assert len(edges) == 1
    assert edges[0].confidence == 1.0


# ──────────────────────────────────────────────────────────────────────────────
# Довершение
# ──────────────────────────────────────────────────────────────────────────────


def decorator_case() -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    """Интерфейс с двумя реализациями, зарегистрирована одна.

    Случай с декоратором: `Cached…` и одноимённый простой сервис. Ребро
    обязано вести в зарегистрированную реализацию и ни одного — к однофамильцу.
    """
    nodes = (
        type_node("src/A/IService.cs#IService", "IService"),
        member_node("src/A/IService.cs#IService.Do", "IService", "Do"),
        type_node("src/A/Cached.cs#Cached", "Cached"),
        member_node("src/A/Cached.cs#Cached.Do", "Cached", "Do"),
        type_node("src/A/Plain.cs#Plain", "Plain"),
        member_node("src/A/Plain.cs#Plain.Do", "Plain", "Do"),
        type_node("src/A/Caller.cs#Caller", "Caller"),
        member_node("src/A/Caller.cs#Caller.Run", "Caller", "Run"),
    )
    edges = (
        calls("src/A/Caller.cs#Caller.Run", "src/A/IService.cs#IService.Do"),
        inherits("src/A/Cached.cs#Cached", "src/A/IService.cs#IService"),
        inherits("src/A/Plain.cs#Plain", "src/A/IService.cs#IService"),
    )
    return nodes, edges


def test_completion_goes_to_the_registered_implementation_only() -> None:
    nodes, edges = decorator_case()
    bind_edges, report = binds([registration("IService", "Cached")], nodes)
    produced, report = complete(nodes, edges, bind_edges, report)
    assert [edge.target for edge in produced] == ["src/A/Cached.cs#Cached.Do"]
    assert report.completed == 1


def test_several_registered_implementations_give_all_with_lower_confidence() -> None:
    """Интерфейс с несколькими зарегистрированными реализациями даёт
    довершение ко всем — и это видно, а не скрыто."""
    nodes, edges = decorator_case()
    bind_edges, report = binds(
        [registration("IService", "Cached"), registration("IService", "Plain", line=11)], nodes
    )
    produced, report = complete(nodes, edges, bind_edges, report)
    assert {edge.target for edge in produced} == {
        "src/A/Cached.cs#Cached.Do",
        "src/A/Plain.cs#Plain.Do",
    }
    assert {edge.confidence for edge in produced} == {0.5}


def test_call_edges_never_come_from_injection() -> None:
    """Узел внедрил сервис на тридцать методов и зовёт два — довершено
    ровно два ребра (G04 п. 4)."""
    nodes = [
        type_node("src/A/IBig.cs#IBig", "IBig"),
        type_node("src/A/Big.cs#Big", "Big"),
        type_node("src/A/Caller.cs#Caller", "Caller"),
        member_node("src/A/Caller.cs#Caller.Run", "Caller", "Run"),
    ]
    for index in range(30):
        nodes.append(member_node(f"src/A/IBig.cs#IBig.M{index}", "IBig", f"M{index}"))
        nodes.append(member_node(f"src/A/Big.cs#Big.M{index}", "Big", f"M{index}"))
    frozen = tuple(nodes)
    edges = (
        calls("src/A/Caller.cs#Caller.Run", "src/A/IBig.cs#IBig.M0"),
        calls("src/A/Caller.cs#Caller.Run", "src/A/IBig.cs#IBig.M1"),
        inherits("src/A/Big.cs#Big", "src/A/IBig.cs#IBig"),
    )
    bind_edges, report = binds([registration("IBig", "Big")], frozen)
    produced, report = complete(frozen, edges, bind_edges, report)
    assert len(produced) == 2
    assert report.completed == 2


def test_completion_does_not_duplicate_what_is_already_resolved() -> None:
    """Если разбор сам довёл вызов до реализации, второго ребра нет (п. 5)."""
    nodes, edges = decorator_case()
    edges = (*edges, calls("src/A/Caller.cs#Caller.Run", "src/A/Cached.cs#Cached.Do"))
    bind_edges, report = binds([registration("IService", "Cached")], nodes)
    produced, _ = complete(nodes, edges, bind_edges, report)
    assert produced == []


# ──────────────────────────────────────────────────────────────────────────────
# Сверка: цель уже конкретна
# ──────────────────────────────────────────────────────────────────────────────


def test_target_matching_the_registration_is_verified() -> None:
    """Версия разбора, доводящая вызов до реализации сама, проверяется
    регистрацией: совпало — ребро принимается как есть."""
    nodes, edges = decorator_case()
    edges = (
        calls("src/A/Caller.cs#Caller.Run", "src/A/Cached.cs#Cached.Do"),
        inherits("src/A/Cached.cs#Cached", "src/A/IService.cs#IService"),
    )
    bind_edges, report = binds([registration("IService", "Cached")], nodes)
    produced, report = complete(nodes, edges, bind_edges, report)
    assert report.verified == 1
    assert report.diverged == 0
    assert produced == []


def test_target_diverging_from_the_registration_is_reported_and_completed() -> None:
    """Разошлось — это категория отчёта, а не тихая правка: ребро к чужому
    выбору остаётся, ребро к зарегистрированной реализации добавляется."""
    nodes, edges = decorator_case()
    edges = (
        calls("src/A/Caller.cs#Caller.Run", "src/A/Plain.cs#Plain.Do"),
        inherits("src/A/Plain.cs#Plain", "src/A/IService.cs#IService"),
    )
    bind_edges, report = binds([registration("IService", "Cached")], nodes)
    produced, report = complete(nodes, edges, bind_edges, report)
    assert report.diverged == 1
    assert [edge.target for edge in produced] == ["src/A/Cached.cs#Cached.Do"]
    assert report.alternatives == 1
    assert report.examples["цель разошлась с регистрацией"]


# ──────────────────────────────────────────────────────────────────────────────
# Сужение по хосту
# ──────────────────────────────────────────────────────────────────────────────


def manifest_with_hosts() -> Manifest:
    return Manifest(
        schema_version="2.0",
        ruleset_version="тест",
        parser=ParserVersions(tree_sitter="0.0"),
        modules=[
            Module(
                id="module:src/HostA/HostA.csproj",
                name="HostA",
                project_file="src/HostA/HostA.csproj",
                lang="cs",
                domain="—",
                enrolled=True,
                project_references=["src/Shared/Shared.csproj"],
            ),
            Module(
                id="module:src/HostB/HostB.csproj",
                name="HostB",
                project_file="src/HostB/HostB.csproj",
                lang="cs",
                domain="—",
                enrolled=True,
                project_references=["src/Shared/Shared.csproj"],
            ),
            Module(
                id="module:src/Shared/Shared.csproj",
                name="Shared",
                project_file="src/Shared/Shared.csproj",
                lang="cs",
                domain="—",
                enrolled=True,
            ),
        ],
    )


def test_completion_is_narrowed_by_host() -> None:
    """Интерфейс, зарегистрированный в разных хостах на разные реализации,
    глобально довершать нельзя: вызывающий из хоста А получил бы реализацию,
    живущую только в хосте Б, — уверенное неверное ребро."""
    shared = "src/Shared/Shared.csproj"
    host_a = "src/HostA/HostA.csproj"
    nodes = (
        type_node("src/Shared/IService.cs#IService", "IService", module=shared),
        member_node("src/Shared/IService.cs#IService.Do", "IService", "Do", module=shared),
        type_node("src/HostA/AImpl.cs#AImpl", "AImpl", module=host_a),
        member_node("src/HostA/AImpl.cs#AImpl.Do", "AImpl", "Do", module=host_a),
        type_node("src/HostB/BImpl.cs#BImpl", "BImpl", module="src/HostB/HostB.csproj"),
        member_node("src/HostB/BImpl.cs#BImpl.Do", "BImpl", "Do", module="src/HostB/HostB.csproj"),
        type_node("src/HostA/Caller.cs#Caller", "Caller", module=host_a),
        member_node("src/HostA/Caller.cs#Caller.Run", "Caller", "Run", module=host_a),
    )
    edges = (calls("src/HostA/Caller.cs#Caller.Run", "src/Shared/IService.cs#IService.Do"),)
    manifest = manifest_with_hosts()
    registrations = [
        DiRegistration(
            service_type="IService",
            impl_type="AImpl",
            lifetime="scoped",
            confidence="high",
            file="src/HostA/Program.cs",
            line=5,
        ),
        DiRegistration(
            service_type="IService",
            impl_type="BImpl",
            lifetime="scoped",
            confidence="high",
            file="src/HostB/Program.cs",
            line=5,
        ),
    ]
    bind_edges, report = binds(registrations, nodes, manifest)
    produced, report = complete(nodes, edges, bind_edges, report, manifest)
    assert [edge.target for edge in produced] == ["src/HostA/AImpl.cs#AImpl.Do"]
    assert report.narrowed == 1
    assert report.not_narrowed == 0


def test_unknown_host_does_not_narrow_and_says_so() -> None:
    """Не сузилось — берутся все реализации, и это отдельное число,
    а не молчание."""
    nodes, edges = decorator_case()
    bind_edges, report = binds([registration("IService", "Cached")], nodes)
    _, report = complete(nodes, edges, bind_edges, report, manifest_with_hosts())
    assert report.not_narrowed == 1
    assert report.narrowed == 0


def test_binding_module_knows_nothing_about_the_parser() -> None:
    text = Path("docpipe/graph/binding.py").read_text(encoding="utf-8").lower()
    assert "codebase-memory" not in text
    assert "cypher" not in text


def test_internal_call_is_not_repointed_to_the_registered_implementation() -> None:
    """Вызов внутри типа через интерфейс не проходит.

    `this.GetBrands()` — собственный метод, а не обращение к сервису.
    Измерено на открытом репозитории: без этой проверки внутренний вызов
    порождал ребро в одноимённый метод декоратора — уверенное неверное ребро
    ровно того вида, ради которого сверка и делается.
    """
    nodes = (
        type_node("src/A/IService.cs#IService", "IService"),
        type_node("src/A/Cached.cs#Cached", "Cached"),
        member_node("src/A/Cached.cs#Cached.Inner", "Cached", "Inner"),
        type_node("src/A/Plain.cs#Plain", "Plain"),
        member_node("src/A/Plain.cs#Plain.Outer", "Plain", "Outer"),
        member_node("src/A/Plain.cs#Plain.Inner", "Plain", "Inner"),
    )
    edges = (
        calls("src/A/Plain.cs#Plain.Outer", "src/A/Plain.cs#Plain.Inner"),
        inherits("src/A/Plain.cs#Plain", "src/A/IService.cs#IService"),
    )
    bind_edges, report = binds([registration("IService", "Cached")], nodes)
    produced, report = complete(nodes, edges, bind_edges, report)
    assert produced == []
    assert report.diverged == 0


# ──────────────────────────────────────────────────────────────────────────────
# Диспетчеризация по типу запроса
# ──────────────────────────────────────────────────────────────────────────────


def handler_declaration(request: str = "GetOrders") -> DispatchDeclaration:
    return DispatchDeclaration(
        handler_fqn="Ns.GetOrdersHandler",
        interface="IRequestHandler",
        request_type=request,
        module="src/App/App.csproj",
        file="src/App/GetOrdersHandler.cs",
        line=3,
    )


def dispatch_nodes() -> tuple[GraphNode, ...]:
    return (
        type_node("src/App/GetOrders.cs#GetOrders", "GetOrders"),
        type_node("src/App/GetOrdersHandler.cs#GetOrdersHandler", "GetOrdersHandler"),
        member_node(
            "src/App/GetOrdersHandler.cs#GetOrdersHandler.Handle", "GetOrdersHandler", "Handle"
        ),
        type_node("src/App/Controller.cs#Controller", "Controller"),
        member_node("src/App/Controller.cs#Controller.MyOrders", "Controller", "MyOrders"),
    )


def send(member: str = "MyOrders", file: str = "src/App/Controller.cs") -> DispatchSend:
    return DispatchSend(
        request_type="GetOrders",
        member=member,
        module="src/App/App.csproj",
        file=file,
        line=12,
    )


def test_dispatch_puts_the_request_type_on_the_edge() -> None:
    """Тип запроса живёт на ребре, а не на вызове: тот же метод обработчика
    зовут и диспетчер, и код напрямую."""
    from docpipe.graph.binding import dispatch

    nodes = dispatch_nodes()
    edges, report = dispatch(nodes, (send(),), [handler_declaration()])
    assert len(edges) == 1
    assert edges[0].kind == "dispatches"
    assert edges[0].target == "src/App/GetOrdersHandler.cs#GetOrdersHandler.Handle"
    assert edges[0].attributes["request"] == "GetOrders"
    assert report["обработчиков без места отправки"] == 0


def test_handler_creating_its_own_request_is_not_a_send_site() -> None:
    """Создание запроса внутри самого обработчика — не отправка: так пишут
    тест или фабрику. Без проверки получается петля «обработчик
    диспетчеризует сам себя»."""
    from docpipe.graph.binding import dispatch

    nodes = dispatch_nodes()
    inside = send(member="Handle", file="src/App/GetOrdersHandler.cs")
    edges, report = dispatch(nodes, (inside,), [handler_declaration()])
    assert edges == []
    # Объявление есть, места отправки нет — это измеренное число, а не тишина.
    assert report["обработчиков без места отправки"] == 1


def test_send_site_without_a_code_node_is_counted() -> None:
    from docpipe.graph.binding import dispatch

    edges, report = dispatch(
        dispatch_nodes(), (send(file="src/App/Нет.cs"),), [handler_declaration()]
    )
    assert edges == []
    assert report["мест отправки без узла кода"] == 1
