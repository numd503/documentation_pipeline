"""Проверка разбора членов типов (T07)."""

from pathlib import Path

from docpipe.dotnet.parser import parse_file, parse_source
from docpipe.model import Attribute

# --------------------------------------------------------------------------------------
# Канонический случай из плана
# --------------------------------------------------------------------------------------


def test_controller_members(sample_solution: Path) -> None:
    declaration = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    ).declarations[0]

    assert [(m.kind, m.name) for m in declaration.members] == [
        ("field", "_pricing"),
        ("constructor", "PricingController"),
        ("method", "GetAsync"),
        ("method", "RecalculateAsync"),
    ]


def test_member_attributes(sample_solution: Path) -> None:
    declaration = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    ).declarations[0]
    by_name = {m.name: m for m in declaration.members}

    assert by_name["GetAsync"].attributes == [
        Attribute(name="HttpGet", args=["{id:guid}"], named_args={})
    ]
    assert by_name["RecalculateAsync"].attributes == [
        Attribute(name="HttpPost", args=[], named_args={})
    ]
    assert by_name["_pricing"].attributes == []


def test_method_signature(sample_solution: Path) -> None:
    """Сигнатура не включает ни атрибуты, ни тело."""
    declaration = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs",
        sample_solution,
    ).declarations[0]
    by_name = {m.name: m for m in declaration.members}

    assert (
        by_name["GetAsync"].signature
        == "public async Task<ActionResult<decimal>> GetAsync(Guid id, CancellationToken ct)"
    )
    assert by_name["RecalculateAsync"].signature == "public Task<ActionResult> RecalculateAsync()"
    assert by_name["_pricing"].signature == "private readonly IPricingService _pricing"
    assert by_name["PricingController"].signature == (
        "public PricingController(IPricingService pricing)"
    )


def test_service_has_exactly_three_methods(sample_solution: Path) -> None:
    declaration = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Grid/RiskComputeService.cs", sample_solution
    ).declarations[0]

    assert [m.name for m in declaration.members] == ["Init", "Execute", "Cancel"]
    assert {m.kind for m in declaration.members} == {"method"}


def test_record_with_primary_constructor_has_no_members(sample_solution: Path) -> None:
    """Параметры primary constructor членами не являются — падать на этом нельзя."""
    declaration = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Models/PriceDto.cs", sample_solution
    ).declarations[0]

    assert declaration.type_kind == "record"
    assert declaration.members == []


# --------------------------------------------------------------------------------------
# Препроцессор — находка ABP, см. docs/findings-abp.md
# --------------------------------------------------------------------------------------


def test_members_inside_preprocessor_are_found() -> None:
    """Главный тест задачи.

    Члены под `#if` не являются прямыми детьми `declaration_list`, они уходят
    под `preproc_if` / `preproc_else`. Реализация, перебирающая детей тела,
    вернёт здесь три члена вместо пяти — и потеряет их молча.

    Обе ветки присутствуют в дереве одновременно: препроцессор не выполняется,
    поэтому документируются оба варианта.
    """
    source = b"""namespace N;
public class A
{
    public void One() { }
#if NET8_0_OR_GREATER
    public void Two() { }
#else
    public void TwoOld() { }
#endif
#region Helpers
    public void Region() { }
#endregion
    public void Three() { }
}
"""
    declaration = parse_source(source, "N.cs").declarations[0]
    assert [m.name for m in declaration.members] == ["One", "Two", "TwoOld", "Region", "Three"]


def test_members_of_nested_type_stay_with_it() -> None:
    """Член принадлежит ближайшему охватывающему типу, а не внешнему."""
    source = b"""namespace N;
public class Outer
{
    public void OuterM() { }
    public class Inner
    {
        public void InnerM() { }
    }
    public void OuterAfter() { }
}
"""
    declarations = parse_source(source, "N.cs").declarations
    by_type = {d.name: [m.name for m in d.members] for d in declarations}
    assert by_type == {"Outer": ["OuterM", "OuterAfter"], "Inner": ["InnerM"]}


# --------------------------------------------------------------------------------------
# Виды членов и формы объявления
# --------------------------------------------------------------------------------------


def test_all_member_kinds() -> None:
    source = b"""namespace N;
public class C
{
    private int _field;
    public C() { }
    public int Property { get; set; }
    public void Method() { }
    public event EventHandler Plain;
    public event EventHandler Custom { add { } remove { } }
}
"""
    members = parse_source(source, "N.cs").declarations[0].members
    assert [(m.kind, m.name) for m in members] == [
        ("field", "_field"),
        ("constructor", "C"),
        ("property", "Property"),
        ("method", "Method"),
        ("event", "Plain"),
        ("event", "Custom"),
    ]


def test_event_field_declaration_is_not_forgotten() -> None:
    """Событие объявляется двумя разными узлами, и обычную форму легко пропустить:
    её узел называется `event_field_declaration`, а не `event_declaration`."""
    source = b"namespace N;\npublic class C { public event EventHandler Changed; }\n"
    members = parse_source(source, "N.cs").declarations[0].members
    assert [(m.kind, m.name) for m in members] == [("event", "Changed")]


def test_field_with_several_declarators_yields_several_members() -> None:
    """`private int _a = 1, _b = 2;` — один узел, но два поля.

    Правило «брать первый declarator» потеряло бы `_b` молча.
    """
    source = b"namespace N;\npublic class C { private int _a = 1, _b = 2; }\n"
    members = parse_source(source, "N.cs").declarations[0].members

    assert [m.name for m in members] == ["_a", "_b"]
    assert {m.signature for m in members} == {"private int _a = 1, _b = 2"}


def test_signature_stops_at_body_arrow_and_accessors() -> None:
    source = b"""namespace N;
public class C
{
    public int Block() { return 1; }
    public int Arrow() => 1;
    public int Auto { get; set; }
    public int Computed => 1;
    public abstract int Abstract(int a);
}
"""
    signatures = {m.name: m.signature for m in parse_source(source, "N.cs").declarations[0].members}
    assert signatures == {
        "Block": "public int Block()",
        "Arrow": "public int Arrow()",
        "Auto": "public int Auto",
        "Computed": "public int Computed",
        "Abstract": "public abstract int Abstract(int a)",
    }


def test_signature_keeps_constraints_and_constructor_initializer() -> None:
    """`where T : class` и `: base(x)` — часть сигнатуры: они стоят до тела."""
    source = b"""namespace N;
public class C
{
    public C(int x) : base(x) { }
    public T Generic<T>(T value) where T : class, new() { return value; }
}
"""
    signatures = {m.name: m.signature for m in parse_source(source, "N.cs").declarations[0].members}
    assert signatures["C"] == "public C(int x) : base(x)"
    assert signatures["Generic"] == "public T Generic<T>(T value) where T : class, new()"


def test_multiline_signature_is_collapsed() -> None:
    """Перенос строки в сигнатуре не должен попадать в `signature_hash`."""
    source = b"""namespace N;
public class C
{
    public async Task<Result> Handle(
        Request request,
        CancellationToken ct)
    {
    }
}
"""
    member = parse_source(source, "N.cs").declarations[0].members[0]
    assert member.signature == (
        "public async Task<Result> Handle( Request request, CancellationToken ct)"
    )


def test_member_modifiers_are_sorted() -> None:
    source = b"namespace N;\npublic class C { protected internal static async void M() { } }\n"
    member = parse_source(source, "N.cs").declarations[0].members[0]
    assert member.modifiers == ["async", "internal", "protected", "static"]


def test_member_xml_doc_and_lines() -> None:
    source = b"""namespace N;
public class C
{
    /// <summary>Computes the price.</summary>
    /// <param name="id">Ignored.</param>
    [HttpGet]
    public decimal Price(Guid id)
    {
        return 0m;
    }

    public void NoDoc() { }
}
"""
    by_name = {m.name: m for m in parse_source(source, "N.cs").declarations[0].members}

    assert by_name["Price"].xml_doc == "Computes the price."
    assert by_name["Price"].line == 6  # строка с [HttpGet], атрибут — потомок узла
    assert by_name["Price"].end_line == 10
    assert by_name["NoDoc"].xml_doc is None


def test_interface_members_are_extracted() -> None:
    source = b"""namespace N;
public interface IService
{
    Task<decimal> PriceAsync(Guid id, CancellationToken ct);
    decimal Rate { get; }
}
"""
    members = parse_source(source, "N.cs").declarations[0].members
    assert [(m.kind, m.name, m.signature) for m in members] == [
        ("method", "PriceAsync", "Task<decimal> PriceAsync(Guid id, CancellationToken ct)"),
        ("property", "Rate", "decimal Rate"),
    ]


def test_enum_has_no_members() -> None:
    """Элементы перечисления — не члены в смысле MemberKind, падать нельзя."""
    source = b"namespace N;\npublic enum Kind { First, Second }\n"
    declaration = parse_source(source, "N.cs").declarations[0]
    assert declaration.type_kind == "enum"
    assert declaration.members == []


def test_members_are_sorted_by_line_then_name() -> None:
    """Ключ сортировки — `(line, name)`, а не позиция в файле.

    На разных строках это одно и то же, но два члена на одной строке
    выстраиваются по имени: `B` объявлен раньше `A`, а идёт после него.
    Порядок объявления при этом сохранён в поле `line`.
    """
    source = b"namespace N;\npublic class C { void B() { } void A() { }\n void Z() { } }\n"
    members = parse_source(source, "N.cs").declarations[0].members

    assert [m.name for m in members] == ["A", "B", "Z"]
    assert [m.line for m in members] == [2, 2, 3]


def test_members_are_deterministic(sample_solution: Path) -> None:
    first = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs", sample_solution
    )
    second = parse_file(
        sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs", sample_solution
    )
    assert first.declarations[0].members == second.declarations[0].members


def test_member_without_a_name_is_dropped() -> None:
    """Обломок выражения из-под `#if` грамматика отдаёт как `field_declaration` без имени.

    Находка стресс-теста: три таких члена на OpenTelemetry и semantic-kernel.
    Безымянный член в манифесте превратился бы в безымянный раздел документации.
    """
    source = b"""namespace N;
public class C
{
    public int Real;
#if NET
    public string Value => x.Replace("a", "b");
#else
        => x.Replace("c", "d");
#endif
}
"""
    result = parse_source(source, "N.cs")
    assert result.parse_errors > 0
    assert all(member.name for member in result.declarations[0].members)
    assert "Real" in [member.name for member in result.declarations[0].members]


def test_pragma_between_attribute_and_modifier_is_not_part_of_signature() -> None:
    """`#pragma` в этом месте — **потомок** объявления, а не сосед.

    Находка T15: без вырезания сигнатура начиналась бы с
    `#pragma warning disable CS0809 // Obsolete member…`, и вместе с ней
    портился бы `signature_hash`. На четырёх реальных репозиториях так
    ломались 252 сигнатуры.
    """
    source = b"""namespace N;
public class C
{
    [Obsolete("use another")]
#pragma warning disable CS0809 // Obsolete member overrides non-obsolete member
    public override int Extract(int a) => a;
#pragma warning restore CS0809
}
"""
    member = parse_source(source, "N.cs").declarations[0].members[0]
    assert member.signature == "public override int Extract(int a)"


def test_comment_inside_parameter_list_is_removed() -> None:
    """После схлопывания пробелов границу строки не найти, и `//` съел бы остаток."""
    source = b"""namespace N;
public class C
{
    public void M(
        // todo: replace with stable
        HttpClient client)
    {
    }
}
"""
    member = parse_source(source, "N.cs").declarations[0].members[0]
    assert member.signature == "public void M( HttpClient client)"
