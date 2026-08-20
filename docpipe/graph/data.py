"""Узлы данных, выведенные из объявлений (G05).

Доступа к боевой базе нет и не планируется: узел данных — это **логическое
имя**, извлечённое из кода и конфигурации, а не строка из каталога СУБД.

Что берётся здесь и почему именно это. План перечисляет восемь источников
имён и честно делит их по разрешимости; отсюда доступны те, что живут
**в объявлениях**:

- атрибут `[Table("FOO", Schema="s")]` на типе сущности — прямое имя;
- `DbSet<Contract>` в сигнатуре члена контекста — сущность плюс соглашение;
- обобщённый репозиторий `IRepository<Contract>` в сигнатуре — то же самое.

Остальные (`ToTable("FOO")` в конфигурации модели, `CreateTable` в миграциях,
SQL в строковом литерале) живут **в телах методов**, а тела здесь не
разбираются. Это не «забыли»: у каждого такого источника имя лежит в литерале
внутри вызова, и чтобы его достать, нужен разбор тел — то есть отдельная
работа с собственным бюджетом. Число необъяснённых обращений печатается,
и в нём эта дыра видна.

**Ребро идёт от ТИПА, а не от члена.** Поле репозитория объявлено на типе,
и какой именно метод к нему обращается, из объявления не следует. Приписать
обращение конструктору — это ровно ошибка «внедрил ≠ зовёт», которая стоила
захода на фронте; поэтому обращение принадлежит типу, а доводит его
до конкретного члена достижимость, а не догадка.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.keys import normalize_data_name, normalize_identifier
from docpipe.model import Manifest, Symbol
from docpipe.symbols import strip_generics

# Обобщённые типы, чей аргумент называет сущность. Список — умолчание,
# а не константа: у репозитория на каждом репозитории своё имя.
DEFAULT_CARRIERS: Final[tuple[str, ...]] = ("DbSet", "IRepository", "IReadRepository")

VIA_TABLE_ATTRIBUTE: Final[str] = "declaration:table-attribute"
VIA_CARRIER: Final[str] = "declaration:carrier"
VIA_LITERAL: Final[str] = "literal:configuration"


@dataclass(frozen=True)
class DataReport:
    tables: int = 0
    references: int = 0
    by_convention: int = 0
    # Имена, ПРОЧИТАННЫЕ литералом, а не выведенные. Число рядом с числом
    # «по соглашению» отвечает на вопрос «сколько здесь достоверного».
    from_literal: int = 0
    unresolved: dict[str, int] = field(default_factory=dict)
    examples: tuple[str, ...] = ()

    def as_counts(self) -> dict[str, int]:
        counts = {
            "узлов данных из объявлений": self.tables,
            "обращений к данным из объявлений": self.references,
            "имён таблиц по соглашению (не по литералу)": self.by_convention,
            "имён таблиц прочитано литералом": self.from_literal,
        }
        for reason, number in sorted(self.unresolved.items()):
            counts[f"обращений к данным не разрешено: {reason}"] = number
        return counts


def data_key(name: str) -> str:
    """Ключ узла данных: нормализованное логическое имя.

    Нормализация та же, что у реестра и у швов: скобки и кавычки снимаются,
    регистр сворачивается, схема не дописывается. Отдельное правило здесь
    разошлось бы с реестровым, и таблица, объявленная в реестре
    и использованная через контекст, дала бы два узла вместо одного.
    """
    return f"data:{normalize_data_name(name)}"


def table_of(symbol: Symbol) -> tuple[str, str] | None:
    """Имя таблицы сущности из атрибута. Возвращает имя и способ получения."""
    for attribute in symbol.attributes:
        if attribute.name != "Table" or not attribute.args:
            continue
        schema = attribute.named_args.get("Schema", "")
        table = attribute.args[0]
        return (f"{schema}.{table}" if schema else table, VIA_TABLE_ATTRIBUTE)
    return None


def _carrier_arguments(signature: str, carriers: tuple[str, ...]) -> list[str]:
    """Аргументы обобщённых типов-носителей из текста сигнатуры.

    Ищется по имени носителя, а не по любому дженерику: `Task<Contract>`
    сущностью не делает ничего, и без этого ограничения узлом данных
    станет каждый тип, встреченный в возвращаемом значении.
    """
    found: list[str] = []
    for carrier in carriers:
        start = 0
        while True:
            position = signature.find(carrier + "<", start)
            if position == -1:
                break
            depth = 0
            for index in range(position + len(carrier), len(signature)):
                char = signature[index]
                if char == "<":
                    depth += 1
                elif char == ">":
                    depth -= 1
                    if depth == 0:
                        inside = signature[position + len(carrier) + 1 : index]
                        first = inside.split(",")[0].strip()
                        if first:
                            found.append(strip_generics(first))
                        start = index
                        break
            else:
                break
            start += 1
    return found


def collect(
    manifest: Manifest,
    nodes: tuple[GraphNode, ...],
    carriers: tuple[str, ...] = DEFAULT_CARRIERS,
) -> tuple[list[GraphNode], list[GraphEdge], DataReport]:
    """Собрать узлы данных и рёбра обращения из объявлений."""
    symbols = [node.symbol for node in manifest.nodes if node.symbol is not None]

    # Сущность → таблица. Порядок силы: литерал в конфигурации модели сильнее
    # атрибута, атрибут сильнее соглашения. Литерал сильнее не по старшинству,
    # а по факту: `ToTable("Catalog")` переопределяет и соглашение, и атрибут,
    # и именно он исполняется.
    tables: dict[str, tuple[str, str]] = {}
    for symbol in symbols:
        found = table_of(symbol)
        if found:
            tables[normalize_identifier(symbol.name)] = found
            tables[normalize_identifier(symbol.fqn)] = found

    unnamed = 0
    for literal in manifest.table_literals:
        if not literal.name:
            # Метод позвали, а имя пришло из константы или переменной.
            # Это неразрешённое обращение, и считать его обязан отчёт.
            unnamed += 1
            continue
        full = f"{literal.schema_name}.{literal.name}" if literal.schema_name else literal.name
        if literal.entity:
            tables[normalize_identifier(literal.entity)] = (full, VIA_LITERAL)

    types_by_fqn = {
        node.attributes["fqn"]: node.key
        for node in nodes
        if node.kind == "type" and node.attributes.get("fqn")
    }

    data_nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    by_convention = 0
    unresolved: dict[str, int] = {}
    examples: list[str] = []

    def data_node(name: str, source: str) -> str:
        key = data_key(name)
        if key not in data_nodes:
            data_nodes[key] = GraphNode(
                key=key,
                kind="data",
                name=name,
                source="code",
                attributes={"origin": source},
            )
        return key

    for symbol in symbols:
        owner = types_by_fqn.get(symbol.fqn)
        if owner is None:
            continue
        for member in symbol.members:
            for entity in _carrier_arguments(member.signature, carriers):
                known = tables.get(normalize_identifier(entity))
                if known is None:
                    # Имя по соглашению: у сущности нет явного атрибута,
                    # и таблицей считается сама сущность. Уверенность ниже,
                    # и число печатается — соглашение бывает переопределено
                    # в конфигурации модели, а её мы не читаем.
                    by_convention += 1
                    key = data_node(entity, "convention")
                    confidence = 0.6
                else:
                    key = data_node(known[0], known[1])
                    confidence = 1.0
                edges.append(
                    GraphEdge(
                        kind="touches",
                        source=owner,
                        target=key,
                        via=f"{VIA_CARRIER}:{member.name}",
                        confidence=confidence,
                        attributes={"entity": entity},
                    )
                )

    # Сущность с атрибутом, к которой никто не обратился по носителю, — тоже
    # узел данных: таблица объявлена, обращений не найдено, и это состояние
    # работы, а не отсутствие таблицы.
    for symbol in symbols:
        found = table_of(symbol)
        if found:
            data_node(found[0], found[1])

    # Имя, прочитанное литералом, даёт узел данных само по себе — даже если
    # сущности рядом не нашлось. Так выглядят миграции: имя таблицы там есть,
    # а сущность записана строкой. Терять прочитанное имя из-за отсутствия
    # связи значит терять единственное, что здесь достоверно.
    for literal in manifest.table_literals:
        if literal.name:
            full = f"{literal.schema_name}.{literal.name}" if literal.schema_name else literal.name
            data_node(full, VIA_LITERAL)

    if not tables and not edges:
        unresolved["объявлений с именами таблиц не найдено"] = 1
        examples.append(
            "ни атрибута таблицы, ни обобщённого носителя: имена живут "
            "в телах методов (конфигурация модели, миграции, SQL-литералы)"
        )

    if unnamed:
        unresolved["имя таблицы не литерал (константа или переменная)"] = unnamed

    report = DataReport(
        tables=len(data_nodes),
        references=len(edges),
        by_convention=by_convention,
        from_literal=sum(1 for literal in manifest.table_literals if literal.name),
        unresolved=unresolved,
        examples=tuple(examples),
    )
    return (
        sorted(data_nodes.values(), key=lambda node: node.key),
        sorted(edges, key=lambda edge: (edge.source, edge.target, edge.via)),
        report,
    )


def from_registry(
    registry: object, entries: tuple[GraphNode, ...] = ()
) -> tuple[list[GraphNode], list[GraphEdge], dict[str, int]]:
    """Узлы данных, объявленные реестром (G05b).

    Имя таблицы здесь **объявлено**, а не выведено, и это единственный источник,
    про который так можно сказать. Отсюда же приходят предметные слова:
    человеческое название списка и названия полей — на репозитории с русским
    глоссарием это единственное место, где они вообще есть, и они обязаны
    попасть в пространство поиска.

    Слияние с узлами из кода идёт по нормализованному имени: таблица,
    объявленная в реестре и использованная через контекст, — **один узел
    с двумя источниками**, а не два узла, которые никогда не встретятся.
    """
    from docpipe.arch.model import ArchRegistry, DataRecord, EntryPointRecord

    assert isinstance(registry, ArchRegistry)
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    report: dict[str, int] = {}

    keys: dict[str, str] = {}
    for record in registry.records:
        if not isinstance(record, DataRecord):
            continue
        name = record.table or record.key
        key = data_key(name)
        keys[normalize_identifier(record.key)] = key
        attributes = {
            "origin": "registry",
            "registry_key": record.key,
            "source_file": record.source.file,
            "data_kind": record.data_kind,
        }
        # Поля идут атрибутами: внутреннее имя, вид и человеческое название.
        # Вид читается как есть — перечень видов у платформы заведомо неполон.
        for field_record in record.fields:
            label = field_record.display_name or field_record.name
            attributes[f"field:{field_record.name}"] = f"{field_record.kind}|{label}"
        nodes.append(
            GraphNode(
                key=key,
                kind="data",
                name=record.name or name,
                file=record.source.file,
                source="registry",
                attributes={name: value for name, value in attributes.items() if value},
            )
        )

    for record in registry.records:
        if isinstance(record, DataRecord):
            for reference in record.references:
                target = keys.get(normalize_identifier(reference)) or data_key(reference)
                edges.append(
                    GraphEdge(
                        kind="references",
                        source=data_key(record.table or record.key),
                        target=target,
                        via="registry:lookup",
                        attributes={"declared": reference},
                    )
                )
        elif isinstance(record, EntryPointRecord):
            # «Список + EventType» сидит на своей таблице по декларации:
            # проходить за этой связью через код не нужно и нечем.
            for touched in record.touches:
                found = keys.get(normalize_identifier(touched))
                if found is None:
                    continue
                origin = next(
                    (
                        node.key
                        for node in entries
                        if node.attributes.get("ref") == record.attributes.get("ref")
                        and node.attributes.get("entry_kind") == record.entry_kind
                    ),
                    None,
                )
                if origin:
                    edges.append(
                        GraphEdge(
                            kind="touches",
                            source=origin,
                            target=found,
                            via="registry:declaration",
                            attributes={"declared": touched},
                        )
                    )

    report["узлов данных из реестра"] = len(nodes)
    report["рёбер между сущностями из реестра"] = sum(
        1 for edge in edges if edge.kind == "references"
    )
    return (
        sorted(nodes, key=lambda node: node.key),
        sorted(edges, key=lambda edge: (edge.kind, edge.source, edge.target)),
        report,
    )


def merge(existing: tuple[GraphNode, ...], added: list[GraphNode]) -> tuple[list[GraphNode], int]:
    """Свести узлы данных из разных источников по ключу.

    Таблица, объявленная в реестре и использованная через контекст, — один
    узел с двумя источниками. Два узла вместо одного означали бы, что
    обращения из кода и декларация никогда не встретятся, а выглядело бы
    это как «таблица есть, обращений нет».
    """
    by_key = {node.key: node for node in existing}
    merged = 0
    for node in added:
        current = by_key.get(node.key)
        if current is None:
            by_key[node.key] = node
            continue
        merged += 1
        by_key[node.key] = current.model_copy(
            update={
                # Человеческое имя из реестра сильнее выведенного: оно
                # написано аналитиком, а не собрано нами по соглашению.
                "name": node.name if node.source == "registry" else current.name,
                "source": f"{current.source}+{node.source}",
                "attributes": {**current.attributes, **node.attributes},
            }
        )
    return sorted(by_key.values(), key=lambda node: node.key), merged


def from_sql(
    manifest: Manifest, nodes: tuple[GraphNode, ...]
) -> tuple[list[GraphNode], list[GraphEdge], dict[str, int]]:
    """Узлы и рёбра из SQL: процедуры и обращения к таблицам (G05c).

    Две половины, и вторая существует только там, где исходники процедур
    лежат в репозитории:

    - **из кода** — SQL, записанный литералом: `FromSqlRaw("SELECT … FROM T")`.
      Даёт рёбра от члена к таблицам и к процедурам;
    - **из исходников** — тело процедуры. Даёт саму процедуру узлом и её
      обращения к таблицам. Без этой половины цепочка обрывается на «позвали
      процедуру X», и всё, что за ней, невидимо.

    **Процедура, вызванная из кода и не найденная среди исходников, не
    исчезает**: она остаётся узлом с пометкой «тело не найдено». На
    репозитории, где процедуры живут только в базе, таких будет сто процентов
    — и это нормальный исход, а не пробел разбора.
    """
    members_by_place: dict[tuple[str, str], str] = {}
    for node in nodes:
        if node.kind == "member" and node.file:
            members_by_place.setdefault((node.file, normalize_identifier(node.name)), node.key)

    produced: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    report: dict[str, int] = {}
    defined: set[str] = set()

    def count(name: str, delta: int = 1) -> None:
        report[name] = report.get(name, 0) + delta

    def node_for(name: str, kind: str, origin: str, file: str = "") -> str:
        key = data_key(name)
        if key not in produced:
            produced[key] = GraphNode(
                key=key,
                kind="data",
                name=name,
                file=file,
                source="sql",
                attributes={"origin": origin, "data_kind": kind},
            )
        return key

    for obj in manifest.sql_objects:
        key = node_for(obj.name, obj.kind, "sql:source", obj.file)
        defined.add(key)
        for table in obj.reads:
            edges.append(
                GraphEdge(
                    kind="reads",
                    source=key,
                    target=node_for(table, "table", "sql:source"),
                    via="sql:body",
                )
            )
        for table in obj.writes:
            edges.append(
                GraphEdge(
                    kind="writes",
                    source=key,
                    target=node_for(table, "table", "sql:source"),
                    via="sql:body",
                )
            )
        for called in obj.calls:
            # Вызов процедуры из процедуры: цепочка внутри SQL не обрывается
            # на первом уровне.
            edges.append(
                GraphEdge(
                    kind="calls",
                    source=key,
                    target=node_for(called, "procedure", "sql:called"),
                    via="sql:exec",
                )
            )
        if obj.dynamic:
            count("динамический SQL в процедуре")

    for usage in manifest.sql_usages:
        source = members_by_place.get((usage.file, normalize_identifier(usage.member)))
        if source is None:
            count("SQL из кода без узла-члена")
            continue
        for table in usage.reads:
            edges.append(
                GraphEdge(
                    kind="reads",
                    source=source,
                    target=node_for(table, "table", "sql:literal"),
                    via="sql:literal",
                )
            )
        for table in usage.writes:
            edges.append(
                GraphEdge(
                    kind="writes",
                    source=source,
                    target=node_for(table, "table", "sql:literal"),
                    via="sql:literal",
                )
            )
        for called in usage.calls:
            edges.append(
                GraphEdge(
                    kind="calls",
                    source=source,
                    target=node_for(called, "procedure", "sql:called"),
                    via="sql:literal",
                )
            )
        if usage.dynamic:
            count("динамический SQL в коде")

    without_body = [
        key
        for key, node in produced.items()
        if node.attributes.get("data_kind") == "procedure" and key not in defined
    ]
    if without_body:
        count("процедур вызвано, тело не найдено", len(without_body))

    report["процедур с телом"] = len(defined)
    return (
        sorted(produced.values(), key=lambda node: node.key),
        sorted(edges, key=lambda edge: (edge.kind, edge.source, edge.target)),
        report,
    )
