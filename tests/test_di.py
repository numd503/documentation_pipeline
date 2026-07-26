"""Проверка извлечения DI-регистраций (T13)."""

from pathlib import Path

from docpipe.dotnet.parser import parse_file, parse_source
from docpipe.model import DiRegistration


def _registrations(source: bytes) -> list[DiRegistration]:
    return parse_source(source, "x.cs").di_registrations


def _tuples(source: bytes) -> list[tuple[str, str | None, str, str]]:
    return [(r.service_type, r.impl_type, r.lifetime, r.confidence) for r in _registrations(source)]


# --------------------------------------------------------------------------------------
# Критерии приёмки на фикстурах
# --------------------------------------------------------------------------------------


def test_registrations_inside_a_method(sample_solution: Path) -> None:
    result = parse_file(sample_solution / "src/Sample.Pricing.Api/Program.cs", sample_solution)

    assert [
        (r.service_type, r.impl_type, r.lifetime, r.confidence) for r in result.di_registrations
    ] == [
        ("IPricingService", "PricingService", "scoped", "high"),
        ("IPricingProvider<string>", "CurveProvider", "singleton", "high"),
        ("ValuationWorkflow", "ValuationWorkflow", "transient", "high"),
    ]


def test_registrations_in_top_level_statements(wild_solution: Path) -> None:
    """Главная ловушка задачи.

    `Program.cs` в современном .NET — top-level statements: ни namespace,
    ни класса, ни метода. Реализация, ищущая вызовы внутри `method_declaration`,
    не найдёт здесь ничего, а на `SampleSolution` (где `Program` — обычный
    статический класс) пройдёт все критерии приёмки.

    В eShopOnWeb так записаны 14 регистраций из 37.
    """
    result = parse_file(wild_solution / "src/Wild.Api/Program.cs", wild_solution)

    assert [(r.service_type, r.lifetime) for r in result.di_registrations] == [
        ("IAuthenticateService", "scoped"),
        ("ICatalogReader", "singleton"),
        ("IClock", "transient"),
        ("CatalogWarmupWorker", "hosted"),
    ]
    assert result.declarations == []  # типов в файле нет вовсе


# --------------------------------------------------------------------------------------
# Формы вызова
# --------------------------------------------------------------------------------------


def test_two_type_arguments() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddScoped<IFoo, Foo>(); } }\n"
    assert _tuples(source) == [("IFoo", "Foo", "scoped", "high")]


def test_one_type_argument_registers_type_on_itself() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddTransient<Foo>(); } }\n"
    assert _tuples(source) == [("Foo", "Foo", "transient", "high")]


def test_typeof_pair_is_a_full_registration() -> None:
    """`typeof(X), typeof(Y)` — не запасной вариант, а обычная форма.

    В ABP и eShopOnWeb так записаны 47 регистраций из 267 — больше, чем
    двумя типами-аргументами. План эту форму не упоминал вовсе.
    """
    source = (
        b"namespace N;\nclass C { void M() { "
        b"s.TryAddTransient(typeof(IKernelAccessor<>), typeof(KernelAccessor<>)); } }\n"
    )
    assert _tuples(source) == [
        ("IKernelAccessor<>", "KernelAccessor<>", "transient", "high"),
    ]


def test_single_typeof() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddTransient(typeof(Worker<>)); } }\n"
    assert _tuples(source) == [("Worker<>", "Worker<>", "transient", "high")]


def test_typeof_with_factory_lowers_confidence() -> None:
    """Сервис назван, реализация вычисляется в рантайме."""
    source = (
        b"namespace N;\nclass C { void M() { "
        b"s.AddTransient(typeof(IBlobContainer), sp => sp.GetService<X>()); } }\n"
    )
    assert _tuples(source) == [("IBlobContainer", None, "transient", "medium")]


def test_lambda_only_registration_is_skipped() -> None:
    """Ни одного имени типа — в манифест попал бы текст лямбды в поле типа.

    План предлагал брать «service_type из текста»; текстом здесь оказывается
    тело лямбды. Регистрация, не называющая ни одного типа, документации
    ничего не даёт. Таких в ABP и eShopOnWeb 50 из 267.
    """
    source = (
        b"namespace N;\nclass C { void M() { "
        b"s.AddTransient(sp => sp.GetRequiredService<AbpOptions>()); } }\n"
    )
    assert _registrations(source) == []


def test_instance_registration_is_skipped() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddSingleton(builder); } }\n"
    assert _registrations(source) == []


def test_cast_registration_is_skipped() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddSingleton((IFactory)factory); } }\n"
    assert _registrations(source) == []


# --------------------------------------------------------------------------------------
# Имена методов и получателей
# --------------------------------------------------------------------------------------


def test_chained_receiver() -> None:
    """`builder.Services` — вложенный member_access, на разбор имени не влияет."""
    source = b"namespace N;\nclass C { void M() { builder.Services.AddScoped<IFoo, Foo>(); } }\n"
    assert _tuples(source) == [("IFoo", "Foo", "scoped", "high")]


def test_try_variants_are_recognised() -> None:
    source = b"""namespace N;
class C
{
    void M()
    {
        s.TryAddScoped<IA, A>();
        s.TryAddSingleton<IB, B>();
        s.TryAddTransient<IC, C2>();
    }
}
"""
    assert [(r.service_type, r.lifetime) for r in _registrations(source)] == [
        ("IA", "scoped"),
        ("IB", "singleton"),
        ("IC", "transient"),
    ]


def test_hosted_service_lifetime() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddHostedService<Worker>(); } }\n"
    assert _tuples(source) == [("Worker", "Worker", "hosted", "high")]


def test_other_calls_are_ignored() -> None:
    source = b"""namespace N;
class C
{
    void M()
    {
        s.AddControllers();
        s.AddMvc<Foo>();
        s.AddDbContext<AppContext>();
        Configure<Options>(o => { });
        s.AddScoped<IFoo, Foo>();
    }
}
"""
    assert _tuples(source) == [("IFoo", "Foo", "scoped", "high")]


def test_call_without_receiver_is_ignored() -> None:
    source = b"namespace N;\nclass C { void M() { AddScoped<IFoo, Foo>(); } }\n"
    assert _registrations(source) == []


# --------------------------------------------------------------------------------------
# Свойства результата
# --------------------------------------------------------------------------------------


def test_generic_arguments_are_kept_as_written() -> None:
    source = (
        b"namespace N;\nclass C { void M() { "
        b"s.AddSingleton<IPricingProvider<string>, CurveProvider>(); } }\n"
    )
    assert _tuples(source) == [
        ("IPricingProvider<string>", "CurveProvider", "singleton", "high"),
    ]


def test_file_and_line_are_recorded() -> None:
    source = (
        b"namespace N;\nclass C\n{\n    void M()\n    {\n"
        b"        s.AddScoped<IFoo, Foo>();\n    }\n}\n"
    )
    registration = _registrations(source)[0]

    assert registration.file == "x.cs"
    assert registration.line == 6


def test_registrations_are_sorted_by_line() -> None:
    source = b"""namespace N;
class C
{
    void M()
    {
        s.AddScoped<IZ, Z>();
        s.AddScoped<IA, A>();
    }
}
"""
    assert [r.service_type for r in _registrations(source)] == ["IZ", "IA"]


def test_registrations_on_one_line_are_sorted_by_type() -> None:
    source = b"namespace N;\nclass C { void M() { s.AddScoped<IZ, Z>(); s.AddScoped<IA, A>(); } }\n"
    assert [r.service_type for r in _registrations(source)] == ["IA", "IZ"]


def test_file_without_registrations() -> None:
    assert _registrations(b"namespace N;\npublic class C { }\n") == []


def test_extraction_is_deterministic(wild_solution: Path) -> None:
    first = parse_file(wild_solution / "src/Wild.Api/Program.cs", wild_solution)
    second = parse_file(wild_solution / "src/Wild.Api/Program.cs", wild_solution)
    assert first.di_registrations == second.di_registrations
