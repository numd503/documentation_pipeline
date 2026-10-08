"""Кандидаты в `di_methods` (S11): вопрос строится из счёта вызовов, а не из догадки.

Без ключа `di_methods` на squidex видно 67 регистраций из 421, и ноль
незаметен: стандартная форма в репозитории тоже встречается. Предложить
значение ключа обязан не агент по памяти, а инструмент по фактам — сколько
раз метод назван с типом и на тех ли получателях, что стандартные `Add*`.

C# — инлайном в `tmp_path`: `SampleSolution` расширять нельзя, а в
`WildSolution` нет ни одной обёртки, названной дважды.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe import cache as cache_module
from docpipe.cache import ParseCache
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.dotnet.parser import parse_source
from docpipe.emit import parser_versions, run
from docpipe.hashing import stable_json_dumps
from docpipe.setup.candidates import (
    CandidateInputs,
    DiMethodCandidates,
    InputError,
    candidates,
    candidates_json,
    di_method_candidates,
    format_candidates,
)

runner = CliRunner()

_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
    "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
)

# Ровно случай из критериев приёмки: обёртка на `services` с продолжением
# цепочки, стандартная регистрация на том же получателе, частый `AddField`
# на другом получателе и `AddDays` без типа.
STARTUP = """
namespace App;

public static class Startup
{
    public static void Configure(IServiceCollection services)
    {
        services.AddSingletonAs<A>().As<IA>();
        services.AddSingletonAs<B>().As<IB>();
        services.AddSingletonAs<C>().As<IC>();
        services.AddSingleton<IFoo, Foo>();
        services.AddSingleton<IBar, Bar>();
    }
}
"""

SCHEMAS = """
namespace App;

public class SchemaBuilder
{
    public SchemaBuilder AddField<T>() => this;
}

public class Schemas
{
    public void Build(SchemaBuilder schema, DateTime dateFrom)
    {
        schema.AddField<string>();
        schema.AddField<int>();
        schema.AddField<long>();
        var a = dateFrom.AddDays(1);
        var b = dateFrom.AddDays(2);
    }
}
"""


def _repo(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "repo"
    module = root / "src" / "App"
    module.mkdir(parents=True)
    (module / "App.csproj").write_text(_CSPROJ, encoding="utf-8")
    sources = {"Startup.cs": STARTUP, "Schemas.cs": SCHEMAS} | {
        f"{name}.cs": text for name, text in files.items()
    }
    for name, text in sources.items():
        (module / name).write_text(text, encoding="utf-8")
    return root


def _candidates(
    root: Path, config: DocpipeConfig | None = None, cache_dir: Path | None = None
) -> DiMethodCandidates:
    settings = config or DocpipeConfig()
    return di_method_candidates(run(root, settings, cache_dir=cache_dir), settings)


# --------------------------------------------------------------------------------------
# Факт разбора
# --------------------------------------------------------------------------------------


def _calls(source: str) -> list[tuple[str, str, int, int, str]]:
    result = parse_source(source.encode("utf-8"), "a.cs")
    return [
        (c.method, c.receiver, c.type_args, c.typeof_args, c.member)
        for c in result.registration_calls
    ]


def test_every_add_call_is_a_fact_standard_and_not() -> None:
    """Стандартные пишутся тоже: по ним считается база получателей."""
    assert _calls(STARTUP) == [
        ("AddSingletonAs", "services", 1, 0, "Configure"),
        ("AddSingletonAs", "services", 1, 0, "Configure"),
        ("AddSingletonAs", "services", 1, 0, "Configure"),
        ("AddSingleton", "services", 2, 0, "Configure"),
        ("AddSingleton", "services", 2, 0, "Configure"),
    ]


def test_receiver_is_the_last_identifier_of_the_receiver_expression() -> None:
    source = """
    var builder = WebApplication.CreateBuilder(args);
    builder.Services.AddScoped<IFoo, Foo>();
    public class C {
        private IServiceCollection services;
        void M(object s) {
            this.services.AddThing<X>();
            ((IServiceCollection)s).AddThing<Y>();
        }
    }
    """
    assert {(method, receiver) for method, receiver, *_ in _calls(source)} == {
        ("AddScoped", "Services"),
        ("AddThing", "services"),
        ("AddThing", "s"),
    }


def test_chain_receiver_is_the_call_and_not_the_collection() -> None:
    """`services.AddMvc().AddX()`: объект — результат `AddMvc`, а не `services`.

    Склей их — и любое звено цепочки построителя выглядело бы регистрацией
    на том же получателе, что `AddScoped`.
    """
    source = "class C { void M(IServiceCollection services) { services.AddMvc().AddJson<X>(); } }"
    assert [(method, receiver) for method, receiver, *_ in _calls(source)] == [
        ("AddJson", "AddMvc()"),
        ("AddMvc", "services"),
    ]


def test_lambda_and_typeof_forms_are_counted() -> None:
    """Лямбда без типа — тоже вызов: в счёт идёт, регистрацией не станет."""
    source = """
    class C { void M(IServiceCollection services) {
        services.AddSingletonAs(_ => new X());
        services.TryAddTransient(typeof(I), typeof(J));
    } }
    """
    assert _calls(source) == [
        ("AddSingletonAs", "services", 0, 0, "M"),
        ("TryAddTransient", "services", 0, 2, "M"),
    ]


def test_plain_add_and_lowercase_tail_are_not_registration_names() -> None:
    """`list.Add(x)` — самый частый вызов в любом коде, и обёрткой он не бывает."""
    source = "class C { void M() { list.Add(x); list.Addition(); list.AddRange(y); } }"
    assert [method for method, *_ in _calls(source)] == ["AddRange"]


def test_facts_do_not_depend_on_di_methods() -> None:
    """Факт собирается одинаково при любой настройке — иначе кандидат зависел бы от ответа."""
    bare = parse_source(STARTUP.encode("utf-8"), "a.cs")
    configured = parse_source(STARTUP.encode("utf-8"), "a.cs", frozenset({"AddSingletonAs"}))
    assert bare.registration_calls == configured.registration_calls
    assert bare.di_registrations != configured.di_registrations


# --------------------------------------------------------------------------------------
# Кандидаты
# --------------------------------------------------------------------------------------


def test_wrapper_on_the_standard_receiver_goes_first(tmp_path: Path) -> None:
    """Критерий приёмки: `AddSingletonAs` первым и с 1.0, `AddField` — с 0.0, `AddDays` нет."""
    report = _candidates(_repo(tmp_path))

    assert [(c.method, c.receiver_overlap) for c in report.items] == [
        ("AddSingletonAs", 1.0),
        ("AddField", 0.0),
    ]
    first = report.items[0]
    assert (first.calls, first.calls_with_types, first.files) == (3, 3, 1)
    assert first.receivers == [("services", 3)]
    assert first.configured is False
    assert first.examples == [
        "src/App/Startup.cs:8",
        "src/App/Startup.cs:9",
        "src/App/Startup.cs:10",
    ]
    assert report.items[1].receivers == [("schema", 3)]
    # Стандартные в кандидаты не идут, но база от них видна.
    assert (report.standard_calls, report.standard_receivers) == (2, [("services", 2)])
    assert report.total == 2


def test_configured_method_is_marked(tmp_path: Path) -> None:
    report = _candidates(_repo(tmp_path), DocpipeConfig(di_methods=["AddSingletonAs"]))
    assert {c.method: c.configured for c in report.items} == {
        "AddSingletonAs": True,
        "AddField": False,
    }


def test_lambda_form_counts_as_a_call_but_not_as_a_registration(tmp_path: Path) -> None:
    """Ограничение регистраций, а не находки: с ключом лямбда-форма всё равно теряется."""
    root = _repo(
        tmp_path,
        Lambda="""
        namespace App;
        public static class More {
            public static void Configure(IServiceCollection services) {
                services.AddSingletonAs(_ => new D());
            }
        }
        """,
    )
    config = DocpipeConfig(di_methods=["AddSingletonAs"])
    scanned = run(root, config)
    wrapper = next(
        c for c in di_method_candidates(scanned, config).items if c.method == "AddSingletonAs"
    )
    assert (wrapper.calls, wrapper.calls_with_types) == (4, 3)
    # Примеры начинаются с вызовов с типом: кандидат в списке из-за них.
    assert all("Lambda.cs" not in example for example in wrapper.examples)
    registered = {
        r.impl_type for r in scanned.manifest.di_registrations if r.file.endswith("Startup.cs")
    }
    assert registered == {"A", "B", "C", "Foo", "Bar"}
    assert not [r for r in scanned.manifest.di_registrations if r.file.endswith("Lambda.cs")]


def test_declared_in_names_an_extension_method_and_not_an_instance_one(tmp_path: Path) -> None:
    """Признак расширения — `static` и `this ` в сигнатуре; одноимённый метод экземпляра — не он."""
    root = _repo(
        tmp_path,
        Extensions="""
        namespace App;
        public static class ThingExtensions {
            public static IServiceCollection AddThing<T>(this IServiceCollection services)
                => services;
        }
        public static class Wiring {
            public static void Configure(IServiceCollection services) {
                services.AddThing<X>();
                services.AddThing<Y>();
            }
        }
        """,
    )
    declared = {c.method: c.declared_in for c in _candidates(root).items}
    assert declared == {
        "AddSingletonAs": None,
        "AddThing": "src/App/Extensions.cs",
        "AddField": None,
    }


def test_single_typed_call_is_below_the_threshold(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        Once="""
        namespace App;
        public static class Once {
            public static void Configure(IServiceCollection services) {
                services.AddOnce<X>();
                services.AddOnce(_ => new Y());
            }
        }
        """,
    )
    assert "AddOnce" not in {c.method for c in _candidates(root).items}


def test_page_keeps_total_and_offset(tmp_path: Path) -> None:
    """Обрезанный список обязан говорить, что он обрезан: `total` всегда полный."""
    scanned = run(_repo(tmp_path), DocpipeConfig())
    second = di_method_candidates(scanned, DocpipeConfig(), limit=1, offset=1)
    assert (second.total, second.offset, [c.method for c in second.items]) == (
        2,
        1,
        ["AddField"],
    )
    everything = di_method_candidates(scanned, DocpipeConfig(), limit=0)
    assert [c.method for c in everything.items] == ["AddSingletonAs", "AddField"]

    text = format_candidates(di_method_candidates(scanned, DocpipeConfig(), limit=1))
    assert "Показаны 1–1 из 2. Дальше: --offset 1." in text


def test_no_standard_registrations_is_said_out_loud(tmp_path: Path) -> None:
    """Без стандартных `Add*` пересечение нулевое у всех, и об этом обязан сказать отчёт."""
    root = tmp_path / "repo"
    (root / "src" / "App").mkdir(parents=True)
    (root / "src" / "App" / "App.csproj").write_text(_CSPROJ, encoding="utf-8")
    (root / "src" / "App" / "Startup.cs").write_text(
        STARTUP.replace("services.AddSingleton<IFoo, Foo>();", "").replace(
            "services.AddSingleton<IBar, Bar>();", ""
        ),
        encoding="utf-8",
    )
    report = _candidates(root)
    assert report.standard_calls == 0
    assert [(c.method, c.receiver_overlap) for c in report.items] == [("AddSingletonAs", 0.0)]
    assert "Стандартных регистраций нет" in format_candidates(report)


# Модуль тестов рядом с модулем области: та же обёртка дважды, своя обёртка
# только тестов и одна стандартная регистрация.
FIXTURE = """
namespace App.Tests;

public static class Fixture
{
    public static void Configure(IServiceCollection services)
    {
        services.AddSingletonAs<T1>();
        services.AddSingletonAs<T2>();
        services.AddTestOnly<T3>();
        services.AddTestOnly<T4>();
        services.AddSingleton<IX, X>();
    }
}
"""

NOT_TESTS = {"not_enrolled": [{"glob": "tests/**", "reason": "тесты — не контракт продукта"}]}


def _with_tests(tmp_path: Path) -> Path:
    root = _repo(tmp_path)
    module = root / "tests" / "App.Tests"
    module.mkdir(parents=True)
    (module / "App.Tests.csproj").write_text(_CSPROJ, encoding="utf-8")
    (module / "Fixture.cs").write_text(FIXTURE, encoding="utf-8")
    return root


def test_candidates_count_only_the_area(tmp_path: Path) -> None:
    """Кандидаты — по области (S34): на semantic-kernel первая страница была из samples и тестов."""
    root = _with_tests(tmp_path)
    settings = DocpipeConfig.model_validate(NOT_TESTS)
    report = di_method_candidates(run(root, settings), settings, limit=0)

    by_method = {item.method: item for item in report.items}
    assert set(by_method) == {"AddSingletonAs", "AddField"}
    wrapper = by_method["AddSingletonAs"]
    assert (wrapper.calls, wrapper.calls_outside_area, wrapper.files) == (3, 2, 1)
    assert all(example.startswith("src/") for item in report.items for example in item.examples)
    # База — тоже по области; вне её — нестандартные: 2 `AddSingletonAs` и 2 `AddTestOnly`.
    assert (report.standard_calls, report.outside_area_calls) == (2, 4)
    assert "Вне области — 4 нестандартных вызовов Add*" in format_candidates(report)

    # Умолчание `enrolled: ["**"]` берёт и тесты: та же обёртка тестов — кандидат.
    everything = di_method_candidates(run(root, DocpipeConfig()), DocpipeConfig(), limit=0)
    assert {item.method for item in everything.items} == {
        "AddSingletonAs",
        "AddField",
        "AddTestOnly",
    }
    assert (everything.standard_calls, everything.outside_area_calls) == (3, 0)


def test_scope_conflict_is_an_input_error_before_the_area(tmp_path: Path) -> None:
    """`scope_of` бросает `ScopeConflict`; переводит его `ctx.scan`, и он зовётся раньше."""
    root = _with_tests(tmp_path)
    settings = DocpipeConfig.model_validate({"enrolled": ["**"], **NOT_TESTS})
    with pytest.raises(InputError, match="enrolled"):
        candidates("di-methods", CandidateInputs(root, settings, use_cache=False))


def test_negative_page_and_unknown_kind_are_input_errors(tmp_path: Path) -> None:
    inputs = CandidateInputs(_repo(tmp_path), DocpipeConfig(), use_cache=False)
    with pytest.raises(InputError, match="известны: di-methods"):
        candidates("di-method", inputs)
    with pytest.raises(InputError):
        candidates("di-methods", inputs, offset=-1)


# --------------------------------------------------------------------------------------
# Кэш разбора
# --------------------------------------------------------------------------------------


def test_warm_cache_gives_the_same_candidates_as_cold(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    cold = candidates_json(_candidates(root))
    cache_dir = tmp_path / "cache"
    first = candidates_json(_candidates(root, cache_dir=cache_dir))
    warm = candidates_json(_candidates(root, cache_dir=cache_dir))
    assert cold == first == warm


def test_cache_of_the_previous_version_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись кэша без нового поля разобралась бы с пустым списком — и кандидатов ноль.

    Ноль выглядел бы как «обёрток нет». Поэтому новое поле `FileParseResult`
    повышает `CACHE_VERSION` (правило 10 плана), и здесь это проверено
    двумя половинами: со старой версией ноль действительно получается,
    с повышенной — нет.
    """
    root = _repo(tmp_path)
    cold = candidates_json(_candidates(root))
    cache_dir = tmp_path / "cache"

    # Кэш, записанный версией без поля: те же записи, но `registration_calls` пуст.
    monkeypatch.setattr(cache_module, "CACHE_VERSION", "4")
    run(root, DocpipeConfig(), cache_dir=cache_dir)
    with ParseCache(cache_dir / "parse.sqlite", parser_versions(), stable_json_dumps([])) as stale:
        for path in stale.all_paths():
            result = stale.get_any(path)
            assert result is not None
            stale.put(result.model_copy(update={"registration_calls": []}))
    assert _candidates(root, cache_dir=cache_dir).total == 0

    monkeypatch.undo()
    assert candidates_json(_candidates(root, cache_dir=cache_dir)) == cold


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_command_prints_json_report(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    result = runner.invoke(
        app, ["setup", "candidates", "di-methods", "--root", str(root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert not result.output.endswith("\n\n")
    report = json.loads(result.output)
    assert report["schema_version"] == "1.1"
    assert report["outside_area_calls"] == 0
    assert [item["method"] for item in report["items"]] == ["AddSingletonAs", "AddField"]
    assert report["items"][0]["receivers"] == [["services", 3]]


def test_command_reads_di_methods_from_config(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text("di_methods: [AddSingletonAs]\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["setup", "candidates", "di-methods", "--root", str(root), "--config", str(config)],
    )
    assert result.exit_code == 0, result.output
    assert "AddSingletonAs  [уже в di_methods]" in result.output
    assert "Показаны 1–2 из 2." in result.output


def test_unreadable_rules_are_a_config_error_and_not_a_traceback(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text("rules: nowhere.yaml\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["setup", "candidates", "di-methods", "--root", str(root), "--config", str(config)],
    )
    assert result.exit_code == 2
    assert "nowhere.yaml" in result.output


@pytest.mark.parametrize(
    "extra",
    [
        ["dispatch"],
        ["di-methods", "--format", "jsno"],
        ["di-methods", "--limit", "-1"],
        ["di-methods", "--offset", "-1"],
    ],
)
def test_command_rejects_bad_arguments_with_code_2(tmp_path: Path, extra: list[str]) -> None:
    root = _repo(tmp_path)
    result = runner.invoke(app, ["setup", "candidates", *extra, "--root", str(root)])
    assert result.exit_code == 2, result.output
