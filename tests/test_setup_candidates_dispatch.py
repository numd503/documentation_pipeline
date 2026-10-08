"""Кандидаты в `dispatch_interfaces` (S12): вопрос строится из счёта реализаций.

Без ключа `dispatch_interfaces` в манифесте нет ни одного обработчика
и ни одной отправки, а имя интерфейса у каждого репозитория своё. Предложить
его обязан не агент по имени библиотеки, а инструмент по фактам: сколько
классов реализуют обобщённую базу, у скольких разных типов-запросов,
встречаются ли эти типы аргументом ещё где-нибудь и где их создают.

C# — инлайном в `tmp_path`: `SampleSolution` расширять нельзя, а в
`WildSolution` нет ни одной обобщённой базы с двумя реализациями.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.emit import dispatch_name, run
from docpipe.setup.candidates import (
    CandidateInputs,
    DispatchCandidate,
    DispatchCandidates,
    _generic_base,
    candidates,
    candidates_json,
    dispatch_candidates,
    format_candidates,
)

runner = CliRunner()

# Два пакета, и только один импортирован реализациями: подсказка о библиотеке
# обязана назвать его, а не первый по алфавиту.
_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
    "<TargetFramework>net8.0</TargetFramework></PropertyGroup><ItemGroup>"
    '<PackageReference Include="AutoMapper" Version="13.0.0" />'
    '<PackageReference Include="MediatR" Version="12.0.0" />'
    "</ItemGroup></Project>"
)

# Ровно случай из критериев приёмки: два обработчика с методом `HandleAsync`,
# отправка одного запроса из контроллера и три обобщённые базы-шума, у двух
# из которых общий аргумент `Order`.
MODEL = """
namespace App.Orders;

public class Order { }
public class Customer { }
public class CreateOrder { }
public class GetOrder { }
"""

HANDLERS = """
using MediatR;
namespace App.Orders;

public class CreateOrderHandler : IRequestHandler<CreateOrder>
{
    public Task HandleAsync(CreateOrder request, CancellationToken token) => Task.CompletedTask;
    private void Log(string text) { }
}

public class GetOrderHandler : IRequestHandler<GetOrder>
{
    public Task HandleAsync(GetOrder request, CancellationToken token) => Task.CompletedTask;
}
"""

NOISE = """
namespace App.Orders;

public class OrderConfiguration : IEntityTypeConfiguration<Order>
{
    public void Configure(EntityTypeBuilder<Order> builder) { }
}

public class CustomerConfiguration : IEntityTypeConfiguration<Customer>
{
    public void Configure(EntityTypeBuilder<Customer> builder) { }
}

public class OrdersByCustomer : Specification<Order> { }
"""

CONTROLLER = """
namespace App.Orders;

public class OrdersController
{
    public void Post()
    {
        var request = new CreateOrder();
    }
}
"""


def _repo(tmp_path: Path, csproj: str = _CSPROJ, **files: str) -> Path:
    root = tmp_path / "repo"
    module = root / "src" / "App"
    module.mkdir(parents=True)
    (module / "App.csproj").write_text(csproj, encoding="utf-8")
    sources = {
        "Model.cs": MODEL,
        "Handlers.cs": HANDLERS,
        "Noise.cs": NOISE,
        "OrdersController.cs": CONTROLLER,
    } | {f"{name}.cs": text for name, text in files.items()}
    for name, text in sources.items():
        (module / name).write_text(text, encoding="utf-8")
    return root


def _candidates(root: Path, config: DocpipeConfig | None = None) -> DispatchCandidates:
    settings = config or DocpipeConfig()
    return dispatch_candidates(run(root, settings), settings, limit=0)


def _by_interface(report: DispatchCandidates) -> dict[str, DispatchCandidate]:
    return {item.interface: item for item in report.items}


# --------------------------------------------------------------------------------------
# Вход: все `new X(…)`, а не только отправки с обработчиком
# --------------------------------------------------------------------------------------


def test_scan_result_carries_every_construction_without_dispatch_key(tmp_path: Path) -> None:
    """До записи ключа обработчиков нет, и в манифест не уходит ни одной отправки.

    Кандидатам же нужно знать, где запрос создают, именно тогда — поэтому
    `ScanResult` несёт все `new`, а не отфильтрованные по обработчикам.
    """
    scanned = run(_repo(tmp_path), DocpipeConfig())
    assert scanned.manifest.dispatch_sends == []
    assert [(path, c.type_name, c.member, c.line) for path, c in scanned.constructions] == [
        ("src/App/OrdersController.cs", "CreateOrder", "Post", 8)
    ]


def test_constructions_outside_scope_come_from_cache(tmp_path: Path) -> None:
    """Скоуп-прогон берёт факты вне скоупа из кэша — и места отправки тоже."""
    root = _repo(tmp_path)
    (root / "src" / "Web").mkdir()
    (root / "src" / "Web" / "Web.csproj").write_text(_CSPROJ, encoding="utf-8")
    (root / "src" / "Web" / "Client.cs").write_text(
        "namespace Web; public class Client { void M() { var q = new App.Orders.GetOrder(); } }",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    full = run(root, DocpipeConfig(), cache_dir=cache_dir)
    scoped = run(
        root, DocpipeConfig(), cache_dir=cache_dir, scope=["src/App"], previous=full.manifest
    )
    assert [path for path, _ in full.constructions] == [
        "src/App/OrdersController.cs",
        "src/Web/Client.cs",
    ]
    assert scoped.constructions == full.constructions


# --------------------------------------------------------------------------------------
# Кандидаты
# --------------------------------------------------------------------------------------


def test_request_handler_goes_first_and_shared_argument_lowers_exclusivity(
    tmp_path: Path,
) -> None:
    """Критерий приёмки: `IRequestHandler` первым с 1.0 и одной отправкой, у шума — меньше 1."""
    report = _candidates(_repo(tmp_path))

    assert [item.interface for item in report.items] == [
        "IRequestHandler",
        "IEntityTypeConfiguration",
    ]
    first = report.items[0]
    assert (first.exclusivity, first.sent) == (1.0, 1)
    assert (first.implementations, first.request_types) == (2, 2)
    assert first.requests == ["CreateOrder", "GetOrder"]
    assert first.resolved is False
    assert first.configured is False
    assert first.examples == ["src/App/Handlers.cs:5", "src/App/Handlers.cs:11"]

    noise = report.items[1]
    # `Order` — аргумент и у `Specification`: запросом одной головы он не является.
    assert noise.exclusivity == 0.5
    assert noise.exclusivity < 1
    assert noise.requests == ["Customer", "Order"]
    # Одна реализация `Specification` — ниже порога, в список она не идёт.
    assert report.total == 2


def test_handler_members_name_the_method_taking_the_request(tmp_path: Path) -> None:
    """Критерий приёмки: `HandleAsync(CreateOrder …)` — `handler_members: ["HandleAsync"]`.

    Метод без запроса в сигнатуре (`Log`) обработчиком не считается.
    """
    report = _candidates(_repo(tmp_path))
    assert report.items[0].handler_members == ["HandleAsync"]


def test_configured_mark_matches_what_the_run_finds(tmp_path: Path) -> None:
    """Отметка «уже в настройке» и прогон читают ключ одной функцией.

    Имя для ключа берётся из отчёта; прогон с ним находит ровно столько
    обработчиков, сколько отчёт насчитал реализаций, — в том числе тот,
    что записан с квалификатором.
    """
    root = _repo(
        tmp_path,
        Qualified="""
        namespace App.Orders;
        public class CancelOrder { }
        public class CancelOrderHandler : MediatR.IRequestHandler<CancelOrder>
        {
            public Task HandleAsync(CancelOrder request, CancellationToken token)
                => Task.CompletedTask;
        }
        """,
    )
    first = _candidates(root).items[0]
    assert (first.interface, first.implementations) == ("IRequestHandler", 3)

    config = DocpipeConfig(dispatch_interfaces=[dispatch_name(first.interface)])
    scanned = run(root, config)
    marked = {
        item.interface: item.configured for item in dispatch_candidates(scanned, config).items
    }
    assert marked == {"IRequestHandler": True, "IEntityTypeConfiguration": False}
    assert len(scanned.manifest.dispatch_handlers) == 3
    assert [send.request_type for send in scanned.manifest.dispatch_sends] == ["CreateOrder"]


def test_interface_declared_in_repository_is_grouped_by_fqn(tmp_path: Path) -> None:
    """Объявленная база — по FQN и `resolved`; в ключ всё равно идёт имя без квалификатора."""
    root = _repo(
        tmp_path,
        Commands="""
        namespace App.Messaging
        {
            public interface ICommandHandler<TCommand> { void Execute(TCommand command); }
        }
        namespace App.Orders
        {
            using App.Messaging;
            public class ShipOrder { }
            public class PayOrder { }
            public class ShipOrderHandler : ICommandHandler<ShipOrder>
            {
                public void Execute(ShipOrder command) { }
            }
            public class PayOrderHandler : ICommandHandler<PayOrder>
            {
                public void Execute(PayOrder command) { }
            }
        }
        """,
    )
    found = _by_interface(_candidates(root))
    item = found["App.Messaging.ICommandHandler"]
    assert item.resolved is True
    assert item.handler_members == ["Execute"]
    # База не из пакета — и пакет ей не ищется, хотя `MediatR` в модуле есть.
    assert item.packages == []
    text = format_candidates(_candidates(root))
    assert "в ключ: ICommandHandler (объявлен в репозитории)" in text
    assert "пакеты: — (база объявлена в репозитории)" in text


def test_self_typed_base_is_not_dispatch(tmp_path: Path) -> None:
    """`Money : IEquatable<Money>` — не диспетчеризация, хотя `new Money` повсюду.

    Без отсева value object с исключительностью 1.0 и десятком отправок
    встал бы выше любого обработчика.
    """
    root = _repo(
        tmp_path,
        Values="""
        namespace App.Orders;
        public class Money : IEquatable<Money> { }
        public class Address : IEquatable<Address> { }
        public class Prices
        {
            void M() { var a = new Money(); var b = new Money(); var c = new Address(); }
        }
        """,
    )
    assert "IEquatable" not in _by_interface(_candidates(root))


def test_abstract_base_handler_yields_its_own_head(tmp_path: Path) -> None:
    """Абстрактный класс запрос не обслуживает; прогон находит голову наследника.

    `dispatch_interfaces` сверяется с прямыми базами, поэтому в ключ идёт
    `CommandHandler`, а не `IRequestHandler`, который реализует только
    абстрактный класс.
    """
    root = tmp_path / "repo"
    module = root / "src" / "App"
    module.mkdir(parents=True)
    (module / "App.csproj").write_text(_CSPROJ, encoding="utf-8")
    (module / "Base.cs").write_text(
        """
        namespace App;
        public class A { }
        public class B { }
        public abstract class CommandHandler<T> : IRequestHandler<T> { }
        public class AHandler : CommandHandler<A> { }
        public class BHandler : CommandHandler<B> { }
        public abstract class LegacyAHandler : IRequestHandler<A> { }
        public abstract class LegacyBHandler : IRequestHandler<B> { }
        """,
        encoding="utf-8",
    )
    found = _by_interface(_candidates(root))
    assert list(found) == ["App.CommandHandler"]
    # Абстрактные `IRequestHandler<A>` не делают `A` общим аргументом двух голов.
    assert found["App.CommandHandler"].exclusivity == 1.0


def test_sent_does_not_count_constructions_inside_implementations(tmp_path: Path) -> None:
    """Обработчик, создающий свой запрос сам, — не вызывающий код."""
    root = _repo(
        tmp_path,
        Retry="""
        namespace App.Orders;
        public class RetryOrder { }
        public class RetryOrderHandler : IRequestHandler<RetryOrder>
        {
            public Task HandleAsync(RetryOrder request, CancellationToken token)
            {
                var again = new RetryOrder();
                var other = new CreateOrder();
                return Task.CompletedTask;
            }
        }
        """,
    )
    first = _by_interface(_candidates(root))["IRequestHandler"]
    # `new CreateOrder()` из контроллера; оба `new` внутри реализаций не в счёт.
    assert first.sent == 1


def test_external_and_type_parameter_arguments_are_not_requests(tmp_path: Path) -> None:
    """Аргумент извне репозитория и параметр-дженерик запросом не бывают."""
    root = _repo(
        tmp_path,
        Tests="""
        namespace App.Tests;
        public class OrdersTests : IClassFixture<WebApplicationFactory<Program>> { }
        public class CustomersTests : IClassFixture<WebApplicationFactory<Program>> { }
        public class Logging<T> : IPipelineBehavior<T> { }
        public class Timing<T> : IPipelineBehavior<T> { }
        """,
    )
    found = _by_interface(_candidates(root))
    assert "IClassFixture" not in found
    assert "IPipelineBehavior" not in found


def test_packages_are_those_the_implementations_import(tmp_path: Path) -> None:
    """Пакет — тот, чьё пространство имён импортируют реализации, а не любой пакет модуля.

    На eShopOnWeb первые три пакета модуля по алфавиту — `Ardalis.*`
    и `AutoMapper…`, а `MediatR` среди них нет: подсказка указала бы
    не на ту библиотеку.
    """
    report = _candidates(_repo(tmp_path))
    assert report.items[0].packages == ["MediatR"]
    # `AutoMapper` в модуле есть, но реализации его не импортируют.
    assert all("AutoMapper" not in item.packages for item in report.items)


def test_global_using_counts_as_import(tmp_path: Path) -> None:
    """`global using` действует на весь проект — как и при резолве базовых типов."""
    root = _repo(tmp_path, GlobalUsings="global using MediatR;")
    handlers = root / "src" / "App" / "Handlers.cs"
    handlers.write_text(HANDLERS.replace("using MediatR;", ""), encoding="utf-8")
    assert _candidates(root).items[0].packages == ["MediatR"]


def test_package_inside_imported_namespace_is_a_fallback(tmp_path: Path) -> None:
    """`using MassTransit` ↔ `MassTransit.RabbitMQ`: модуль ссылается на транспорт.

    Совпадение слабое (`using System` ловит каждый `System.*`), поэтому
    оно в ходу, только когда точного нет.
    """
    transport = _CSPROJ.replace('"MediatR"', '"MediatR.Transport"')
    report = _candidates(_repo(tmp_path, csproj=transport))
    assert report.items[0].packages == ["MediatR.Transport"]

    both = _CSPROJ.replace(
        '<PackageReference Include="AutoMapper" Version="13.0.0" />',
        '<PackageReference Include="MediatR.Transport" Version="1.0.0" />',
    )
    other = tmp_path / "other"
    other.mkdir()
    assert _candidates(_repo(other, csproj=both)).items[0].packages == ["MediatR"]


def test_no_matching_package_is_said_out_loud(tmp_path: Path) -> None:
    """Пустой список не значит «библиотеки нет»: пакет мог прийти из `Directory.*.props`."""
    bare = (
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
    )
    report = _candidates(_repo(tmp_path, csproj=bare))
    assert report.items[0].packages == []
    assert "Directory.*.props" in format_candidates(report)


def test_generic_base_takes_the_paired_bracket() -> None:
    """Аргумент головы — до парной `>`, а не до последней."""
    assert _generic_base("EndpointBaseAsync.WithRequest<A>.WithActionResult<B>") == (
        "EndpointBaseAsync.WithRequest",
        ["A"],
    )
    assert _generic_base("IRequestHandler<GetOrders, IEnumerable<OrderView>>") == (
        "IRequestHandler",
        ["GetOrders", "IEnumerable<OrderView>"],
    )
    assert _generic_base("ControllerBase") is None
    assert _generic_base("IBroken<A") is None


def test_page_keeps_total_and_offset(tmp_path: Path) -> None:
    scanned = run(_repo(tmp_path), DocpipeConfig())
    second = dispatch_candidates(scanned, DocpipeConfig(), limit=1, offset=1)
    assert (second.total, second.offset) == (2, 1)
    assert [item.interface for item in second.items] == ["IEntityTypeConfiguration"]

    text = format_candidates(dispatch_candidates(scanned, DocpipeConfig(), limit=1))
    assert "Показаны 1–1 из 2. Дальше: --offset 1." in text


def test_two_runs_give_the_same_bytes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert candidates_json(_candidates(root)) == candidates_json(_candidates(root))


# Модуль тестов: подставной обработчик того же запроса, своя обобщённая база
# с двумя реализациями и отправка запроса из теста.
TESTS = """
using App.Orders;
namespace App.Tests;

public class FakeCreateHandler : IRequestHandler<CreateOrder>
{
    public Task HandleAsync(CreateOrder request, CancellationToken token) => Task.CompletedTask;
}

public class OrderProbe : ITestCase<Order> { }
public class CustomerProbe : ITestCase<Customer> { }

public class Client
{
    public void Send() { var query = new CreateOrder(); }
}
"""


def test_candidates_count_only_the_area(tmp_path: Path) -> None:
    """Кандидаты — по области (S34): `dispatch-interfaces` возглавлял `ConformanceTests…`."""
    root = _repo(tmp_path)
    module = root / "tests" / "App.Tests"
    module.mkdir(parents=True)
    (module / "App.Tests.csproj").write_text(_CSPROJ, encoding="utf-8")
    (module / "Probes.cs").write_text(TESTS, encoding="utf-8")
    settings = DocpipeConfig.model_validate(
        {"not_enrolled": [{"glob": "tests/**", "reason": "тесты — не контракт продукта"}]}
    )
    report = dispatch_candidates(run(root, settings), settings, limit=0)

    assert [item.interface for item in report.items] == [
        "IRequestHandler",
        "IEntityTypeConfiguration",
    ]
    handler = report.items[0]
    assert (handler.implementations, handler.implementations_outside_area) == (2, 1)
    # Отправка из теста не в счёт: остаётся одна, из контроллера области.
    assert handler.sent == 1
    assert all(example.startswith("src/") for example in handler.examples)
    # Вне области: подставной обработчик и две реализации `ITestCase`.
    assert report.outside_area_implementations == 3
    assert "Вне области — 3 реализаций обобщённых баз" in format_candidates(report)

    # Без области тесты в счёте: `ITestCase` — кандидат, отправок две.
    everything = _by_interface(_candidates(root))
    assert set(everything) == {"IRequestHandler", "IEntityTypeConfiguration", "ITestCase"}
    assert (everything["IRequestHandler"].implementations, everything["IRequestHandler"].sent) == (
        3,
        2,
    )


def test_kind_is_known_to_the_entry_point(tmp_path: Path) -> None:
    inputs = CandidateInputs(_repo(tmp_path), DocpipeConfig(), use_cache=False)
    report = candidates("dispatch-interfaces", inputs)
    assert isinstance(report, DispatchCandidates)
    assert report.items[0].interface == "IRequestHandler"


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_command_prints_json_report(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    result = runner.invoke(
        app,
        ["setup", "candidates", "dispatch-interfaces", "--root", str(root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert not result.output.endswith("\n\n")
    report = json.loads(result.output)
    assert report["schema_version"] == "1.1"
    assert report["outside_area_implementations"] == 0
    first = report["items"][0]
    assert (first["interface"], first["exclusivity"], first["sent"]) == ("IRequestHandler", 1.0, 1)
    assert first["handler_members"] == ["HandleAsync"]


def test_command_reads_dispatch_interfaces_from_config(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text("dispatch_interfaces: [IRequestHandler]\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "setup",
            "candidates",
            "dispatch-interfaces",
            "--root",
            str(root),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "IRequestHandler  [уже в dispatch_interfaces]" in result.output
    assert "в ключ: IRequestHandler (внешний тип)" in result.output
    assert "Показаны 1–2 из 2." in result.output


@pytest.mark.parametrize("extra", [["--format", "jsno"], ["--limit", "-1"]])
def test_command_rejects_bad_arguments_with_code_2(tmp_path: Path, extra: list[str]) -> None:
    root = _repo(tmp_path)
    result = runner.invoke(
        app, ["setup", "candidates", "dispatch-interfaces", *extra, "--root", str(root)]
    )
    assert result.exit_code == 2, result.output
