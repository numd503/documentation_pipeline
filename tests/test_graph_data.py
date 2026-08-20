"""Узлы данных: из объявлений кода и из реестра (G05, G05b).

Главное свойство — **один узел на одну таблицу**, откуда бы имя ни пришло.
Два узла вместо одного означали бы, что обращения из кода и декларация
никогда не встретятся, а выглядело бы это как «таблица есть, обращений нет».
"""

from pathlib import Path

from docpipe.arch.model import ArchRegistry, DataField, DataRecord, EntryPointRecord, Source
from docpipe.graph.data import collect, data_key, from_registry, merge
from docpipe.graph.model import GraphNode
from docpipe.model import Attribute, DocNode, Manifest, Member, ParserVersions, SourceSpan, Symbol


def symbol(
    name: str,
    *,
    attributes: tuple[Attribute, ...] = (),
    members: tuple[Member, ...] = (),
    module: str = "src/A/A.csproj",
) -> Symbol:
    return Symbol(
        fqn=f"Ns.{name}",
        name=name,
        type_kind="class",
        namespace="Ns",
        module=module,
        attributes=list(attributes),
        members=list(members),
        sources=[SourceSpan(path=f"src/A/{name}.cs", start=1, end=10)],
    )


def manifest_of(*symbols: Symbol) -> Manifest:
    return Manifest(
        schema_version="2.0",
        ruleset_version="тест",
        parser=ParserVersions(tree_sitter="0.0"),
        nodes=[
            DocNode(
                id=f"type:{item.fqn}",
                kind="service",
                template="service.md",
                title=item.name,
                doc_path=f"docs/{item.name}.md",
                module=item.module,
                domain="—",
                symbol=item,
                signature_hash="sha256:0",
            )
            for item in symbols
        ],
    )


def type_node(name: str) -> GraphNode:
    return GraphNode(
        key=f"src/A/{name}.cs#{name}",
        kind="type",
        name=name,
        file=f"src/A/{name}.cs",
        attributes={"fqn": f"Ns.{name}"},
    )


def member(name: str, signature: str) -> Member:
    return Member(name=name, kind="property", signature=signature, line=1, end_line=2)


# ──────────────────────────────────────────────────────────────────────────────
# Имена из объявлений
# ──────────────────────────────────────────────────────────────────────────────


def test_table_attribute_wins_over_convention() -> None:
    """Атрибут написан человеком ровно затем, чтобы соглашение не действовало."""
    entity = symbol("Contract", attributes=(Attribute(name="Table", args=["CONTRACTS"]),))
    context = symbol("Db", members=(member("Contracts", "public DbSet<Contract> Contracts"),))
    nodes, edges, report = collect(manifest_of(entity, context), (type_node("Db"),))
    assert data_key("CONTRACTS") in {node.key for node in nodes}
    assert edges[0].confidence == 1.0
    assert report.by_convention == 0


def test_schema_from_the_attribute_is_part_of_the_name() -> None:
    entity = symbol(
        "Contract",
        attributes=(Attribute(name="Table", args=["CONTRACTS"], named_args={"Schema": "dbo"}),),
    )
    nodes, _, _ = collect(manifest_of(entity), ())
    assert nodes[0].key == data_key("dbo.CONTRACTS")


def test_convention_is_used_and_counted() -> None:
    """Соглашение бывает переопределено в конфигурации модели, а её мы
    не читаем: уверенность ниже, и число печатается."""
    context = symbol("Db", members=(member("Baskets", "public DbSet<Basket> Baskets"),))
    nodes, edges, report = collect(manifest_of(context), (type_node("Db"),))
    assert nodes[0].key == data_key("Basket")
    assert edges[0].confidence < 1.0
    assert report.by_convention == 1


def test_generic_repository_names_the_entity() -> None:
    service = symbol(
        "Service", members=(member("Repo", "private readonly IRepository<Order> _repo"),)
    )
    nodes, edges, _ = collect(manifest_of(service), (type_node("Service"),))
    assert nodes[0].key == data_key("Order")
    assert edges[0].kind == "touches"


def test_unrelated_generics_do_not_become_tables() -> None:
    """`Task<Contract>` сущностью не делает ничего: без ограничения списком
    носителей узлом данных станет каждый тип из возвращаемого значения."""
    service = symbol("Service", members=(member("Get", "public Task<Contract> Get()"),))
    nodes, edges, _ = collect(manifest_of(service), (type_node("Service"),))
    assert nodes == []
    assert edges == []


def test_edge_starts_at_the_type_not_at_the_member() -> None:
    """Поле репозитория объявлено на типе, и какой метод к нему обращается,
    из объявления не следует. Приписать обращение конструктору — это ошибка
    «внедрил ≠ зовёт», которая стоила захода на фронте."""
    service = symbol(
        "Service", members=(member("Repo", "private readonly IRepository<Order> _repo"),)
    )
    _, edges, _ = collect(manifest_of(service), (type_node("Service"),))
    assert edges[0].source == "src/A/Service.cs#Service"


def test_no_declarations_says_where_the_names_live() -> None:
    """Ни атрибута, ни носителя — это не «таблиц нет», а «имена в телах
    методов», и отчёт обязан сказать именно это."""
    _, _, report = collect(manifest_of(symbol("Plain")), ())
    assert report.unresolved
    assert any("телах методов" in example for example in report.examples)


# ──────────────────────────────────────────────────────────────────────────────
# Имена из реестра
# ──────────────────────────────────────────────────────────────────────────────


def registry_with_lists() -> ArchRegistry:
    return ArchRegistry(
        version="1",
        records=(
            DataRecord(
                key="UserTasks",
                name="Задачи пользователей",
                table="USER_TASKS",
                fields=(
                    DataField(name="Title", kind="FieldText", display_name="Наименование"),
                    DataField(
                        name="Type",
                        kind="FieldLookup",
                        display_name="Тип",
                        references="UserTaskTypes",
                    ),
                ),
                references=("UserTaskTypes",),
                source=Source(file="Structure.xml", record="List[UserTasks]"),
                provenance="adapter",
            ),
            DataRecord(
                key="UserTaskTypes",
                name="Типы задач",
                table="USER_TASK_TYPES",
                source=Source(file="Structure.xml", record="List[UserTaskTypes]"),
                provenance="adapter",
            ),
            EntryPointRecord(
                key="UserTasks:ItemAdded",
                entry_kind="event_handler",
                touches=("UserTasks",),
                attributes={"ref": "ItemAdded"},
                source=Source(file="Structure.xml", record="EventReceiver"),
                provenance="adapter",
            ),
        ),
    )


def test_registry_gives_display_names_and_fields() -> None:
    """Человеческое название и названия полей — единственное место, где
    предметные слова вообще есть, и они обязаны попасть в узел."""
    nodes, _, report = from_registry(registry_with_lists())
    tasks = next(node for node in nodes if node.key == data_key("USER_TASKS"))
    assert tasks.name == "Задачи пользователей"
    assert tasks.attributes["field:Title"] == "FieldText|Наименование"
    assert report["узлов данных из реестра"] == 2


def test_lookup_becomes_a_reference_edge() -> None:
    """Связь читается из декларации, а не выводится из графа кода."""
    _, edges, _ = from_registry(registry_with_lists())
    references = [edge for edge in edges if edge.kind == "references"]
    assert len(references) == 1
    assert references[0].source == data_key("USER_TASKS")
    assert references[0].target == data_key("USER_TASK_TYPES")


def test_event_handler_touches_its_table_without_going_through_code() -> None:
    entry = GraphNode(
        key="entry:event_handler:usertasks:itemadded",
        kind="entry_point",
        name="ItemAdded",
        source="registry",
        attributes={"entry_kind": "event_handler", "ref": "ItemAdded"},
    )
    _, edges, _ = from_registry(registry_with_lists(), (entry,))
    touches = [edge for edge in edges if edge.kind == "touches"]
    assert len(touches) == 1
    assert touches[0].source == entry.key
    assert touches[0].target == data_key("USER_TASKS")


# ──────────────────────────────────────────────────────────────────────────────
# Слияние источников
# ──────────────────────────────────────────────────────────────────────────────


def test_one_table_from_two_sources_is_one_node() -> None:
    """Таблица, объявленная в реестре и использованная через контекст, —
    один узел с двумя источниками (G05b п. 2)."""
    entity = symbol("Contract", attributes=(Attribute(name="Table", args=["CONTRACTS"]),))
    from_code, _, _ = collect(manifest_of(entity), ())
    registry = ArchRegistry(
        version="1",
        records=(
            DataRecord(
                key="Contracts",
                name="Договоры",
                table="[dbo].[CONTRACTS]",
                source=Source(file="Structure.xml"),
                provenance="adapter",
            ),
        ),
    )
    from_declaration, _, _ = from_registry(registry)
    # Ключ нормализован одинаково: скобки и регистр дублей не создают.
    assert from_declaration[0].key == "data:dbo.contracts"
    assert from_code[0].key == "data:contracts"

    merged, joined = merge(tuple(from_code), from_declaration)
    # Схема НЕ дописывается: `CONTRACTS` и `dbo.CONTRACTS` — разные ключи,
    # и склеивать их было бы уверенной неправдой на репозитории со схемами.
    assert joined == 0
    assert len(merged) == 2


def test_same_name_from_two_sources_merges_and_keeps_the_human_name() -> None:
    entity = symbol("Contract", attributes=(Attribute(name="Table", args=["CONTRACTS"]),))
    from_code, _, _ = collect(manifest_of(entity), ())
    registry = ArchRegistry(
        version="1",
        records=(
            DataRecord(
                key="Contracts",
                name="Договоры",
                table="CONTRACTS",
                source=Source(file="Structure.xml"),
                provenance="adapter",
            ),
        ),
    )
    from_declaration, _, _ = from_registry(registry)
    merged, joined = merge(tuple(from_code), from_declaration)
    assert joined == 1
    assert len(merged) == 1
    assert merged[0].name == "Договоры"
    assert merged[0].source == "code+registry"


def test_data_module_has_no_parser_vocabulary() -> None:
    text = Path("docpipe/graph/data.py").read_text(encoding="utf-8").lower()
    assert "codebase-memory" not in text
    assert "cypher" not in text
