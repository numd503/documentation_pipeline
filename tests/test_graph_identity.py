"""Идентичность узла и сопоставление с манифестом (G02).

Три свойства ключа, каждое из которых ломается молча: перегрузки различаются,
номера строк на ключ не влияют, а совпадение FQN ключ не создаёт. И одно
свойство отчёта: числа не зависят от порядка на входе.
"""

from pathlib import Path

from docpipe.graph import (
    GraphNode,
    match,
    member_key,
    parameter_types,
    symbol_member_key,
    symbol_type_key,
)
from docpipe.graph.identity import generic_arity
from docpipe.graph.match import apply, diagnose_roots
from docpipe.model import Manifest, Member, ParserVersions, SourceSpan, Symbol


def symbol(
    *,
    name: str = "Service",
    fqn: str = "Ns.Service",
    module: str = "src/A/A.csproj",
    arity: int = 0,
    members: tuple[Member, ...] = (),
    path: str = "src/A/Service.cs",
) -> Symbol:
    return Symbol(
        fqn=fqn,
        name=name,
        type_kind="class",
        namespace="Ns",
        module=module,
        type_parameters=[f"T{index}" for index in range(arity)],
        members=list(members),
        sources=[SourceSpan(path=path, start=1, end=10)],
    )


def method(name: str, signature: str, line: int = 1) -> Member:
    return Member(name=name, kind="method", signature=signature, line=line, end_line=line + 2)


def manifest_of(*symbols: Symbol) -> Manifest:
    from docpipe.model import DocNode

    nodes = [
        DocNode(
            id=f"type:{item.module}#{item.fqn}",
            kind="service",
            template="service.md",
            title=item.name,
            doc_path=f"docs/modules/{item.name}.md",
            module=item.module,
            domain="—",
            symbol=item,
            signature_hash="sha256:0",
        )
        for item in symbols
    ]
    return Manifest(
        schema_version="2.0",
        ruleset_version="тест",
        parser=ParserVersions(tree_sitter="0.0"),
        modules=[],
        nodes=nodes,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Разбор сигнатуры
# ──────────────────────────────────────────────────────────────────────────────


def test_parameter_types_are_taken_without_names() -> None:
    """Имя параметра в ключ не входит: переименование перегрузки не создаёт."""
    assert parameter_types("public Task<int> Do(Guid id, CancellationToken ct)") == (
        "Guid",
        "CancellationToken",
    )


def test_generic_arguments_do_not_split_the_parameter_list() -> None:
    """`Dictionary<string, int> map` — один параметр, а не два.

    Наивный `split(",")` даёт здесь два, то есть отпечаток, который
    не совпадёт ни с чем.
    """
    assert parameter_types("public void Do(Dictionary<string, int> map, int x)") == (
        "Dictionary<string, int>",
        "int",
    )


def test_tuple_return_type_does_not_steal_the_parameter_list() -> None:
    """У метода с кортежным результатом первая группа скобок принадлежит типу
    результата; разбор по первой дал бы параметры `int, int`."""
    assert parameter_types("public (int, int) GetPair(string key)") == ("string",)


def test_default_values_and_modifiers_are_dropped() -> None:
    assert parameter_types("public void Do(params string[] args, int x = 5)") == (
        "string[]",
        "int",
    )


def test_property_has_no_parameters() -> None:
    assert parameter_types("protected string TraceId") == ()


def test_generic_arity_of_a_method() -> None:
    assert generic_arity("public T Do<T, K>(T x)", "Do") == 2
    assert generic_arity("public void Do(int x)", "Do") == 0


# ──────────────────────────────────────────────────────────────────────────────
# Ключи
# ──────────────────────────────────────────────────────────────────────────────


def test_overloads_get_different_keys() -> None:
    """Перегрузки одного имени различаются — прямое требование приёмки."""
    owner = symbol()
    first = symbol_member_key(owner, method("Do", "public void Do(int x)"))
    second = symbol_member_key(owner, method("Do", "public void Do(string x)"))
    assert first != second


def test_overload_position_is_not_part_of_the_key() -> None:
    """Порядковый номер перегрузки ключом быть не может: перегрузка,
    добавленная выше существующей, сменила бы ключи существующих."""
    owner = symbol()
    alone = symbol_member_key(owner, method("Do", "public void Do(string x)"))
    with_neighbour = symbol_member_key(
        symbol(members=(method("Do", "public void Do(int x)"),)),
        method("Do", "public void Do(string x)"),
    )
    assert alone == with_neighbour


def test_key_does_not_depend_on_line_numbers() -> None:
    """Переформатирование файла ключ не меняет."""
    owner = symbol()
    top = symbol_member_key(owner, method("Do", "public void Do(int x)", line=3))
    moved = symbol_member_key(owner, method("Do", "public void Do(int x)", line=300))
    assert top == moved


def test_same_fqn_different_arity_are_different_keys() -> None:
    """FQN не уникален: `ICrudAppService`, `ICrudAppService<T>` и
    `ICrudAppService<T, K>` — три разных типа с одним FQN."""
    keys = {symbol_type_key(symbol(fqn="Ns.ICrud", arity=arity)) for arity in (0, 1, 2)}
    assert len(keys) == 3


def test_same_fqn_different_module_are_different_keys() -> None:
    """Один и тот же FQN законно живёт в разных сборках."""
    first = symbol_type_key(symbol(module="src/A/A.csproj"))
    second = symbol_type_key(symbol(module="src/B/B.csproj"))
    assert first != second


def test_member_kind_participates_in_the_key() -> None:
    """Поле и метод без параметров с одним именем не спорят за ключ."""
    as_method = member_key("owner", "Value", 0, "method", ())
    as_field = member_key("owner", "Value", 0, "field", ())
    assert as_method != as_field


# ──────────────────────────────────────────────────────────────────────────────
# Сопоставление
# ──────────────────────────────────────────────────────────────────────────────


def graph_nodes() -> tuple[GraphNode, ...]:
    return (
        GraphNode(
            key="src/A/Service.cs#Service",
            kind="type",
            name="Service",
            file="src/A/Service.cs",
        ),
        GraphNode(
            key="src/A/Service.cs#Service.Do",
            kind="member",
            name="Do",
            owner="Service",
            file="src/A/Service.cs",
        ),
        GraphNode(
            key="src/A/Other.cs#Other",
            kind="type",
            name="Other",
            file="src/A/Other.cs",
        ),
    )


def test_match_reports_three_numbers() -> None:
    manifest = manifest_of(symbol(members=(method("Do", "public void Do(int x)"),)))
    attributes, report = match(graph_nodes(), manifest)
    assert report.matched == 2
    assert report.only_manifest == 0
    assert report.only_graph == 1
    assert "src/A/Other.cs#Other" in report.examples["есть в графе — нет в манифесте"]
    assert attributes["src/A/Service.cs#Service.Do"]["module"] == "src/A/A.csproj"


def test_documented_symbol_missing_from_the_graph_is_visible() -> None:
    """Обратное число — то, ради чего отчёт и заведён: документированный
    узел, которого нет в графе, это дыра разбора."""
    manifest = manifest_of(symbol(name="Пропавший", fqn="Ns.Lost", path="src/A/Lost.cs"))
    _, report = match(graph_nodes(), manifest)
    assert report.only_manifest == 1
    assert report.examples["есть в манифесте — нет в графе"]


def test_overloads_are_counted_not_chosen() -> None:
    """У графа один узел на все перегрузки. Выбор одного из нескольких молча
    не делается: число печатается, а ключ члена не проставляется вовсе."""
    manifest = manifest_of(
        symbol(
            members=(
                method("Do", "public void Do(int x)"),
                method("Do", "public void Do(string x)"),
            )
        )
    )
    attributes, report = match(graph_nodes(), manifest)
    assert report.ambiguous == 1
    found = attributes["src/A/Service.cs#Service.Do"]
    assert found["declarations"] == "2"
    assert "member_key" not in found


def test_partial_class_is_found_from_every_file() -> None:
    """`partial class` живёт в нескольких файлах; искать надо по каждому."""
    partial = symbol().model_copy(
        update={
            "sources": [
                SourceSpan(path="src/A/Service.cs", start=1, end=5),
                SourceSpan(path="src/A/Service.Extra.cs", start=1, end=5),
            ]
        }
    )
    nodes = (
        GraphNode(
            key="src/A/Service.Extra.cs#Service",
            kind="type",
            name="Service",
            file="src/A/Service.Extra.cs",
        ),
    )
    _, report = match(nodes, manifest_of(partial))
    assert report.matched == 1


def test_match_is_stable_to_input_order() -> None:
    manifest = manifest_of(symbol(members=(method("Do", "public void Do(int x)"),)))
    straight, first = match(graph_nodes(), manifest)
    reversed_nodes = tuple(reversed(graph_nodes()))
    shuffled, second = match(reversed_nodes, manifest)
    assert straight == shuffled
    assert first.as_counts() == second.as_counts()
    assert first.examples == second.examples


def test_apply_adds_attributes_without_touching_keys() -> None:
    """Ключ узла графа на ключ манифеста не подменяется: пространство ключей
    индекса должно быть одно."""
    manifest = manifest_of(symbol(members=(method("Do", "public void Do(int x)"),)))
    attributes, _ = match(graph_nodes(), manifest)
    updated = apply(graph_nodes(), attributes)
    assert [node.key for node in updated] == [node.key for node in graph_nodes()]
    enriched = next(node for node in updated if node.key.endswith("Service.Do"))
    assert enriched.attributes["doc_node"].startswith("type:")


def test_identity_module_has_no_engine_vocabulary() -> None:
    """Правило Р13 действует и здесь: сопоставление говорит про манифест
    и граф, а не про то, чем граф получен."""
    text = Path("docpipe/graph/identity.py").read_text(encoding="utf-8").lower()
    assert "codebase-memory" not in text
    assert "cypher" not in text


def test_no_key_collides_on_the_wild_fixture(wild_solution: Path, tmp_path: Path) -> None:
    """На фикстуре из дикой природы ни один ключ не совпадает у разных
    объявлений (G02 п. 2).

    Фикстура собрана из конструкций, пойманных в реальных репозиториях:
    два `Constants` в одном модуле, интерфейсы разной арности, вложенные типы,
    типы вне пространства имён. Именно на таких и ломается ключ, собранный
    по FQN.
    """
    from typer.testing import CliRunner

    from docpipe.cli import app

    out = tmp_path / "doc-tree.json"
    result = CliRunner().invoke(
        app, ["scan", "--root", str(wild_solution), "--out", str(out), "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    manifest = Manifest.model_validate_json(out.read_text(encoding="utf-8"))

    type_keys: list[str] = []
    member_keys: list[str] = []
    for node in manifest.nodes:
        if node.symbol is None:
            continue
        type_keys.append(symbol_type_key(node.symbol))
        for member in node.symbol.members:
            member_keys.append(symbol_member_key(node.symbol, member))

    assert type_keys, "фикстура не дала ни одного типа — тест проверяет пустоту"
    assert len(type_keys) == len(set(type_keys))
    assert len(member_keys) == len(set(member_keys))


# ------------------------------------------------------------------------------------------
# Разные корни: ноль совпадений — не неполнота
# ------------------------------------------------------------------------------------------


def test_leading_segment_is_named_when_nothing_matches() -> None:
    """Манифест снят с `repo/App`, разбор — с `repo`.

    Прогон при этом проходит целиком: индекс собирается, «сопоставлено 0»
    печатается строкой среди прочих чисел, а `affects` по выводу `git diff`
    не находит ни одного файла — и выглядит это как «правка ничего не задела».
    """
    manifest = manifest_of(symbol())
    prefixed = tuple(
        node.model_copy(
            update={
                "file": f"App/{node.file}",
                "key": f"App/{node.key}",
                "owner": node.owner,
            }
        )
        for node in graph_nodes()
    )
    _, report = match(prefixed, manifest)
    assert report.matched == 0

    diagnosis = diagnose_roots(prefixed, manifest)
    assert "App/" in diagnosis
    assert "корни разные" in diagnosis


def test_diagnosis_is_empty_when_there_is_nothing_to_diagnose() -> None:
    """Выдумывать причину хуже, чем не ставить её: у пустой стороны диагноза нет."""
    assert diagnose_roots((), manifest_of(symbol())) == ""
