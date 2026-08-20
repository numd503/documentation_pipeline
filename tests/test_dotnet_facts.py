"""Факты из тел: создание объекта и вызов с литералом.

Это **не** разбор тел: тела не строятся в граф и не хранятся. Извлекаются
два факта, каждый под названный вопрос — «где отправляют запрос» и «где имя
таблицы записано литералом», — и оба нужны потому, что иначе звено цепочки
не существует нигде.
"""

from docpipe.dotnet.parser import parse_source


def parse(source: str):
    return parse_source(source.encode("utf-8"), "a.cs")


# ──────────────────────────────────────────────────────────────────────────────
# Создание объекта: место отправки запроса
# ──────────────────────────────────────────────────────────────────────────────


def test_construction_names_the_member_it_happens_in() -> None:
    """Без члена «где-то создают X» бесполезно: ребро идёт от члена."""
    result = parse(
        """
        public class C {
            public async Task<IActionResult> MyOrders() {
                var v = await _mediator.Send(new GetMyOrders(User.Identity.Name));
                return View(v);
            }
        }
        """
    )
    assert [(item.type_name, item.member) for item in result.constructions] == [
        ("GetMyOrders", "MyOrders")
    ]


def test_generic_and_qualified_names_are_reduced_to_the_type() -> None:
    result = parse(
        """
        public class C {
            public void Run() {
                var a = new Ns.Deep.Query<int>(1);
            }
        }
        """
    )
    assert [item.type_name for item in result.constructions] == ["Query"]


def test_construction_outside_a_member_is_a_state_not_a_gap() -> None:
    """Top-level statements: члена нет вовсе, и пустое имя — это состояние,
    а не неизвестность."""
    result = parse('var query = new GetMyOrders("a");')
    assert [(item.type_name, item.member) for item in result.constructions] == [("GetMyOrders", "")]


# ──────────────────────────────────────────────────────────────────────────────
# Литерал имени таблицы
# ──────────────────────────────────────────────────────────────────────────────


def test_table_literal_carries_name_and_schema() -> None:
    result = parse(
        """
        public class Ctx {
            protected override void OnModelCreating(ModelBuilder b) {
                b.Entity<Contract>().ToTable("CONTRACTS", "dbo");
            }
        }
        """
    )
    assert [(item.method, item.arguments) for item in result.literal_calls] == [
        ("ToTable", ["CONTRACTS", "dbo"])
    ]


def test_entity_comes_from_the_receiver_generic() -> None:
    result = parse(
        """
        public class Ctx {
            protected override void OnModelCreating(ModelBuilder b) {
                b.Entity<Contract>().ToTable("CONTRACTS");
            }
        }
        """
    )
    assert result.literal_calls[0].entity == "Contract"


def test_entity_comes_from_the_method_parameter() -> None:
    """Основная форма на реальном коде: `builder.ToTable("Catalog")` внутри
    `Configure(EntityTypeBuilder<CatalogItem> builder)`. Формы
    `Entity<X>().ToTable(…)` в открытом репозитории не встретилось ни разу.
    """
    result = parse(
        """
        public class CatalogItemConfiguration : IEntityTypeConfiguration<CatalogItem> {
            public void Configure(EntityTypeBuilder<CatalogItem> builder) {
                builder.ToTable("Catalog");
            }
        }
        """
    )
    assert result.literal_calls[0].entity == "CatalogItem"
    assert result.literal_calls[0].arguments == ["Catalog"]


def test_call_without_a_literal_is_recorded_as_unresolved() -> None:
    """Метод позвали, а имя пришло из константы. Выбросив такие, мы получили бы
    «неразрешённых нет» вместо числа."""
    result = parse(
        """
        public class Ctx {
            protected override void OnModelCreating(ModelBuilder b) {
                b.Entity<Order>().ToTable(TableNames.Orders);
            }
        }
        """
    )
    assert [(item.method, item.arguments) for item in result.literal_calls] == [("ToTable", [])]


def test_other_methods_with_literals_are_not_table_names() -> None:
    """Список методов положительный: иначе именем таблицы станет любая строка
    в коде."""
    result = parse(
        """
        public class C {
            public void Run() { _logger.LogInformation("не таблица"); }
        }
        """
    )
    assert result.literal_calls == []


def test_verbatim_string_is_read_without_quotes() -> None:
    result = parse(
        """
        public class Ctx {
            public void Up(MigrationBuilder b) { b.CreateTable(@"AspNetUsers"); }
        }
        """
    )
    assert result.literal_calls[0].arguments == ["AspNetUsers"]


# ------------------------------------------------------------------------------------------
# Самодельные обёртки регистрации (G04, найдено на squidex)
# ------------------------------------------------------------------------------------------

WRAPPER_SOURCE = b"""
public static class Services
{
    public static void Configure(IServiceCollection services)
    {
        services.AddSingletonAs<TempFolderBackupArchiveLocation>()
            .As<IBackupArchiveLocation>();

        services.AddSingletonAs<AppResolver>()
            .AsSelf();

        services.AddTransientAs<Multi>()
            .As<IFirst>()
            .As<ISecond>();

        services.AddScoped<IFoo, Foo>();
    }
}
"""


def test_custom_registration_methods_are_invisible_without_configuration() -> None:
    """Без ключа конфигурации обёртки не видны — и это ноль без сообщения.

    Именно так на squidex терялись 314 регистраций из 361: отчёт показывал
    непустое число, потому что стандартная форма в репозитории тоже была.
    """
    result = parse_source(WRAPPER_SOURCE, "Services.cs")
    names = {(r.service_type, r.impl_type) for r in result.di_registrations}
    assert names == {("IFoo", "Foo")}


def test_custom_registration_methods_come_from_configuration() -> None:
    methods = frozenset({"AddSingletonAs", "AddTransientAs"})
    result = parse_source(WRAPPER_SOURCE, "Services.cs", methods)
    pairs = {(r.service_type, r.impl_type) for r in result.di_registrations}
    assert ("IBackupArchiveLocation", "TempFolderBackupArchiveLocation") in pairs
    # `.AsSelf()` тип-аргумента не несёт: регистрация остаётся на себя.
    assert ("AppResolver", "AppResolver") in pairs
    # Одно звено цепочки — одна регистрация: два интерфейса на одну реализацию
    # законны и обязаны дать два ребра, а не одно.
    assert ("IFirst", "Multi") in pairs
    assert ("ISecond", "Multi") in pairs


def test_lifetime_of_custom_method_comes_from_its_name() -> None:
    """Вид времени жизни читается из имени подстрокой, а не угадывается."""
    result = parse_source(WRAPPER_SOURCE, "Services.cs", frozenset({"AddTransientAs"}))
    lifetimes = {r.lifetime for r in result.di_registrations if r.impl_type == "Multi"}
    assert lifetimes == {"transient"}


def test_unnamed_lifetime_is_unknown_and_not_a_guess() -> None:
    source = b"""
    public static class Services
    {
        public static void Configure(IServiceCollection services)
        {
            services.RegisterCashflow<Impl>().As<IThing>();
        }
    }
    """
    result = parse_source(source, "Services.cs", frozenset({"RegisterCashflow"}))
    assert [(r.service_type, r.lifetime) for r in result.di_registrations] == [
        ("IThing", "unknown")
    ]


def test_chain_is_not_read_through_an_argument() -> None:
    """`.As<…>` снаружи чужого вызова к нашей регистрации не относится."""
    source = b"""
    public static class Services
    {
        public static void Configure(IServiceCollection services)
        {
            Wrap(services.AddSingletonAs<Impl>()).As<IWrong>();
        }
    }
    """
    result = parse_source(source, "Services.cs", frozenset({"AddSingletonAs"}))
    assert [(r.service_type, r.impl_type) for r in result.di_registrations] == [("Impl", "Impl")]
