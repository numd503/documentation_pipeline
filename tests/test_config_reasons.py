"""Вторая форма записи с причиной и `not_enrolled` (S22).

Решение человека по ключу настройки получает место для причины, и «решили
не брать» отличимо от «ещё не смотрели». Короткая форма продолжает работать:
копия настройки лежит на боевом репозитории, а старые строки обязаны давать
байт в байт тот же манифест.

Самое хрупкое здесь — не загрузка, а потребители: объект в месте, где ждут
строку, ломается молча (множество шаблонов, суффикс `/**`, список движку
графа). Поэтому, кроме проверок формы, тут же лежит тест-сторож: обход AST
`docpipe/**` не находит чтения сырых полей мимо нормализованных свойств.
"""

import ast
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import (
    DocpipeConfig,
    Enrolled,
    ExcludeEntry,
    NamedDecision,
    NotEnrolled,
    RootEntry,
    ScopeConflict,
    load_config,
    scope_of,
)
from docpipe.emit import exclude_globs, parse_options, run, scan, write_manifest
from docpipe.setup.candidates import CandidateInputs, InputError, candidates

runner = CliRunner()

PRICING = "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"
COMMON = "src/Sample.Common/Sample.Common.csproj"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "docpipe.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# Форма записи
# --------------------------------------------------------------------------------------

MIXED = """\
enrolled:
  - "src/**"
  - glob: "tools/Integration/**"
    reason: "интеграционный код команды — документируем"
not_enrolled:
  - glob: "samples/**"
    reason: "примеры для внешних разработчиков, не продукт"
exclude:
  - "**/Migrations/**"
  - glob: "vendor/**"
    reason: "сторонний код, копия пакета"
di_methods:
  - AddTransientAs
  - name: AddSingletonAs
    reason: "обёртка Squidex.Hosting"
dispatch_interfaces:
  - name: IRequestHandler
    reason: "MediatR"
  - INotificationHandler
web:
  roots:
    - "./frontend/"
    - path: "admin//"
      reason: "второй фронт, своя команда"
  url_rewrite:
    - module: admin
      add_prefix: /admin
      reason: "proxy.conf.json: pathRewrite ^/api/ → /admin/api/"
  registry_calls:
    - route: api/items/query
      discriminator: {in: body, name: listInnerName}
      reason: "реестр списков платформы"
"""


def test_mixed_list_loads_in_every_key(tmp_path: Path) -> None:
    """Строки и записи вперемешку — во всех шести ключах; свойства дают строки в порядке файла."""
    settings = load_config(_write(tmp_path, MIXED))

    assert settings.enrolled_globs == ["src/**", "tools/Integration/**"]
    assert settings.not_enrolled_globs == ["samples/**"]
    assert settings.exclude_patterns == ["**/Migrations/**", "vendor/**"]
    assert settings.di_method_names == ["AddTransientAs", "AddSingletonAs"]
    assert settings.dispatch_interface_names == ["IRequestHandler", "INotificationHandler"]
    # Путь записи проверен и нормализован тем же валидатором, что и строка.
    assert settings.web.root_paths == ["frontend", "admin"]

    # Причина при этом сохранена — ради неё вторая форма и заведена.
    assert settings.enrolled[1] == Enrolled(
        glob="tools/Integration/**", reason="интеграционный код команды — документируем"
    )
    assert settings.not_enrolled[0].reason == "примеры для внешних разработчиков, не продукт"
    assert settings.exclude[1] == ExcludeEntry(
        glob="vendor/**", reason="сторонний код, копия пакета"
    )
    assert settings.di_methods[1] == NamedDecision(
        name="AddSingletonAs", reason="обёртка Squidex.Hosting"
    )
    assert settings.web.roots[1] == RootEntry(path="admin", reason="второй фронт, своя команда")
    assert settings.web.url_rewrite[0].reason.startswith("proxy.conf.json")
    assert settings.web.registry_calls[0].reason == "реестр списков платформы"


def test_short_form_reads_as_before() -> None:
    """Конструктор принимает строки: на этом стоят тесты, к настройке не относящиеся."""
    settings = DocpipeConfig(
        enrolled=["src/A/**"],
        exclude=["docs/**"],
        di_methods=["AddSingletonAs"],
        dispatch_interfaces=["IRequestHandler"],
    )

    assert settings.enrolled == ["src/A/**"]
    assert settings.enrolled_globs == ["src/A/**"]
    assert settings.exclude_patterns == ["docs/**"]
    assert settings.di_method_names == ["AddSingletonAs"]
    assert settings.dispatch_interface_names == ["IRequestHandler"]
    assert DocpipeConfig().web.root_paths == ["."]
    assert DocpipeConfig().enrolled_globs == ["**"]


def test_typo_in_an_entry_is_one_error_about_the_key(tmp_path: Path) -> None:
    """Форма выбирается по виду значения: ошибка одна, и она про ключ записи.

    При переборе вариантов объединения первой шла «Input should be a valid
    string» — подсказка свернуть запись в строку и потерять причину.
    """
    with pytest.raises(ValueError) as failure:
        load_config(_write(tmp_path, 'enrolled:\n  - glob: "src/**"\n    reson: "x"\n'))

    message = str(failure.value)
    assert "1 validation error" in message
    assert "enrolled.0.entry.reson" in message
    assert "valid string" not in message


def test_root_entry_path_is_checked_like_a_string(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"web\.roots: выход за корень"):
        load_config(_write(tmp_path, 'web:\n  roots:\n    - path: "../front"\n'))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('not_enrolled:\n  - "samples/**"\n', "короткой формы у ключа нет"),
        ('not_enrolled:\n  - glob: "samples/**"\n', "без причины не принимается"),
        ('not_enrolled:\n  - glob: "samples/**"\n    reason: "  "\n', "причина обязательна"),
    ],
)
def test_not_enrolled_without_reason_is_refused(tmp_path: Path, text: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        load_config(_write(tmp_path, text))


def test_commented_out_not_enrolled_is_refused(tmp_path: Path) -> None:
    """Новый ключ-список подпадает под правило S02 сам — список строится по модели."""
    with pytest.raises(ValueError, match="`not_enrolled:` без элементов"):
        load_config(_write(tmp_path, "not_enrolled:\n  # - glob: x\n"))


# --------------------------------------------------------------------------------------
# Область: `scope_of`
# --------------------------------------------------------------------------------------


def _not_enrolled(glob: str, reason: str = "решили не брать") -> NotEnrolled:
    return NotEnrolled(glob=glob, reason=reason)


def test_scope_of_sample_solution() -> None:
    """Критерий приёмки: `undecided` без записи, `not_enrolled` с ней."""
    narrowed = DocpipeConfig(enrolled=["src/Sample.Common/**"])
    assert scope_of(PRICING, narrowed) == "undecided"
    assert scope_of(COMMON, narrowed) == "enrolled"

    decided = narrowed.model_copy(
        update={"not_enrolled": [_not_enrolled("src/Sample.Pricing.Api/**")]}
    )
    assert scope_of(PRICING, decided) == "not_enrolled"
    assert scope_of(COMMON, decided) == "enrolled"


def test_default_enrolled_is_not_a_decision() -> None:
    """Умолчание `["**"]` — не решение человека: `not_enrolled` вырезает без противоречия,
    а `undecided` не бывает вовсе."""
    settings = DocpipeConfig(not_enrolled=[_not_enrolled("src/Sample.Pricing.Api/**")])

    assert scope_of(PRICING, settings) == "not_enrolled"
    assert scope_of(COMMON, settings) == "enrolled"
    assert scope_of("anything/X.csproj", DocpipeConfig()) == "enrolled"


def test_explicit_empty_enrolled_leaves_every_module_undecided() -> None:
    assert scope_of(PRICING, DocpipeConfig(enrolled=[])) == "undecided"


def test_module_under_both_lists_is_a_conflict_naming_both_globs() -> None:
    settings = DocpipeConfig(
        enrolled=["src/**"], not_enrolled=[_not_enrolled("src/Sample.Pricing.Api/**")]
    )

    with pytest.raises(ScopeConflict) as failure:
        scope_of(PRICING, settings)

    message = str(failure.value)
    assert "'src/**'" in message
    assert "'src/Sample.Pricing.Api/**'" in message


def test_conflict_refuses_the_run_with_module_name(sample_solution: Path) -> None:
    """Отказ прогона, а не правило приоритета: оба противоречия — в одном сообщении."""
    settings = DocpipeConfig(
        enrolled=["src/**", Enrolled(glob="src/Sample.Common/**", reason="ядро")],
        not_enrolled=[_not_enrolled("src/*/**")],
    )

    with pytest.raises(ScopeConflict) as failure:
        run(sample_solution, settings)

    message = str(failure.value)
    assert f"Sample.Pricing.Api ({PRICING})" in message
    assert f"Sample.Common ({COMMON})" in message
    assert "'src/**', 'src/Sample.Common/**'" in message
    assert "'src/*/**'" in message
    assert len(failure.value.clashes) == 2


def test_not_enrolled_module_is_parsed_but_gives_no_nodes(sample_solution: Path) -> None:
    """В манифест решение не идёт: `Module.enrolled` прежний, наследование цело."""
    settings = DocpipeConfig(not_enrolled=[_not_enrolled("src/Sample.Common/**")])

    manifest, _ = scan(sample_solution, settings)

    enrolled = {module.name: module.enrolled for module in manifest.modules}
    assert enrolled == {"Sample.Common": False, "Sample.Pricing.Api": True}
    assert {node.module for node in manifest.nodes} == {"Sample.Pricing.Api"}


def test_scan_refuses_conflict_with_code_2(sample_solution: Path, tmp_path: Path) -> None:
    config = _write(
        tmp_path,
        'enrolled: ["src/**"]\n'
        'not_enrolled:\n  - glob: "src/Sample.Common/**"\n    reason: "общий код"\n',
    )

    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--config",
            str(config),
            "--out",
            str(tmp_path / "dt.json"),
            "--no-cache",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "Ошибка конфигурации" in result.output
    assert "'src/Sample.Common/**'" in result.output
    assert not (tmp_path / "dt.json").exists()


def test_setup_candidates_refuse_conflict_as_input_error(sample_solution: Path) -> None:
    settings = DocpipeConfig(
        enrolled=["src/**"], not_enrolled=[_not_enrolled("src/Sample.Common/**")]
    )

    with pytest.raises(InputError, match="not_enrolled"):
        candidates("di-methods", CandidateInputs(sample_solution, settings, use_cache=False))


# --------------------------------------------------------------------------------------
# Вторая форма даёт тот же манифест
# --------------------------------------------------------------------------------------

_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
    "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
)

_STARTUP = """
namespace App;

public static class Startup
{
    public static void Configure(IServiceCollection services)
    {
        services.AddSingletonAs<OrderService>().As<IOrderService>();
    }
}

public interface IOrderService { }
public class OrderService : IOrderService { }
public class GetOrder { }
public class GetOrderHandler : IRequestHandler<GetOrder> { }
"""

_VENDORED = "namespace Vendor; public class Copied { }\n"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for project in ("src/App", "samples/Demo"):
        directory = root / project
        directory.mkdir(parents=True)
        name = project.rsplit("/", 1)[-1]
        (directory / f"{name}.csproj").write_text(_CSPROJ, encoding="utf-8")
    (root / "src/App/Startup.cs").write_text(_STARTUP, encoding="utf-8")
    (root / "samples/Demo/Demo.cs").write_text(_STARTUP.replace("App", "Demo"), encoding="utf-8")
    (root / "src/App/vendor").mkdir()
    (root / "src/App/vendor/Copied.cs").write_text(_VENDORED, encoding="utf-8")
    return root


def test_second_form_gives_the_same_manifest_byte_for_byte(tmp_path: Path) -> None:
    """Потребители видят строки: множество шаблонов, суффикс `/**`, ключ обёрток.

    Числа проверены на непустоту — иначе равенство пустого с пустым прошло бы
    при любой поломке потребителя.
    """
    root = _repo(tmp_path)
    short = DocpipeConfig(
        enrolled=["src/**"],
        exclude=["src/App/vendor/**"],
        di_methods=["AddSingletonAs"],
        dispatch_interfaces=["IRequestHandler"],
    )
    full = DocpipeConfig.model_validate(
        {
            "enrolled": [{"glob": "src/**", "reason": "продукт"}],
            "not_enrolled": [{"glob": "samples/**", "reason": "примеры"}],
            "exclude": [{"glob": "src/App/vendor/**", "reason": "сторонний код"}],
            "di_methods": [{"name": "AddSingletonAs", "reason": "обёртка"}],
            "dispatch_interfaces": [{"name": "IRequestHandler", "reason": "MediatR"}],
        }
    )

    first, _ = scan(root, short)
    second, _ = scan(root, full)

    assert first.di_registrations
    assert first.dispatch_handlers
    assert {module.name: module.enrolled for module in first.modules} == {
        "App": True,
        "Demo": False,
    }
    assert not any("vendor" in node.doc_path for node in first.nodes)
    write_manifest(first, tmp_path / "short.json")
    write_manifest(second, tmp_path / "full.json")
    assert (tmp_path / "short.json").read_bytes() == (tmp_path / "full.json").read_bytes()
    assert exclude_globs(short) == exclude_globs(full)


# --------------------------------------------------------------------------------------
# Ключ кэша разбора
# --------------------------------------------------------------------------------------


def test_reason_does_not_change_the_cache_key() -> None:
    """Правка формулировки разбора не меняет — и кэш сбрасывать незачем (правило 10)."""
    first = DocpipeConfig(di_methods=[NamedDecision(name="AddSingletonAs", reason="обёртка")])
    second = DocpipeConfig(di_methods=[NamedDecision(name="AddSingletonAs", reason="иначе")])
    short = DocpipeConfig(di_methods=["AddSingletonAs"])
    other = DocpipeConfig(di_methods=["AddTransientAs"])

    assert parse_options(first) == parse_options(second) == parse_options(short)
    assert parse_options(first) != parse_options(other)


def test_run_writes_the_same_cache_key_for_another_reason(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    cache_dir = tmp_path / "cache"

    def stored(reason: str) -> str:
        settings = DocpipeConfig(di_methods=[NamedDecision(name="AddSingletonAs", reason=reason)])
        run(root, settings, cache_dir=cache_dir)
        with closing(sqlite3.connect(cache_dir / "parse.sqlite")) as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = 'options'").fetchone()
        return str(row[0])

    # Совпавшая запись `meta` — ровно условие, при котором кэш не очищается.
    expected = parse_options(DocpipeConfig(di_methods=["AddSingletonAs"]))
    assert stored("обёртка") == stored("совсем другая причина") == expected
    assert "AddSingletonAs" in expected


# --------------------------------------------------------------------------------------
# Тест-сторож: сырые поля читает только `config.py`
# --------------------------------------------------------------------------------------

# Поля со второй формой. Имена `enrolled` и `exclude` заняты и у других моделей
# (`Module.enrolled`, `Ruleset.exclude`), поэтому у них учитывается получатель:
# переменная настройки называется `config` или `settings` (в том числе
# `inputs.settings`, `self.config`). Остальные имена в `docpipe/` больше
# ничему не принадлежат и запрещены на любом получателе.
GUARDED = frozenset({"enrolled", "not_enrolled", "exclude", "di_methods", "dispatch_interfaces"})
SHARED_NAMES = frozenset({"enrolled", "exclude"})
CONFIG_RECEIVERS = ("config", "settings")
# `configcheck.py` назван в плане как допустимый: отчёт проверки может
# показывать записи с причинами. Сейчас он читает свойства, как все.
OWNERS = frozenset({Path("docpipe/config.py"), Path("docpipe/configcheck.py")})


def _receiver(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _raw_reads(source: str, path: str) -> list[str]:
    """Чтения сырых полей настройки со второй формой: `файл:строка: выражение`."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            receiver = _receiver(node.value)
            web_roots = node.attr == "roots" and receiver == "web"
            guarded = node.attr in GUARDED and (
                node.attr not in SHARED_NAMES or receiver.endswith(CONFIG_RECEIVERS)
            )
            if web_roots or guarded:
                found.append(f"{path}:{node.lineno}: {ast.unparse(node)}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in GUARDED
        ):
            found.append(f"{path}:{node.lineno}: {ast.unparse(node)}")
    return sorted(found)


def test_no_raw_reads_outside_config() -> None:
    offenders = [
        offender
        for path in sorted(Path("docpipe").rglob("*.py"))
        if path not in OWNERS
        for offender in _raw_reads(path.read_text(encoding="utf-8"), path.as_posix())
    ]

    assert offenders == [], (
        "поле настройки со второй формой прочитано мимо нормализованного свойства"
        f" (`enrolled_globs`, `exclude_patterns`, `web.root_paths` и соседи): {offenders}"
    )


@pytest.mark.parametrize(
    "line",
    [
        "x = settings.enrolled",
        "x = config.exclude",
        "x = inputs.settings.di_methods",
        "x = self.config.not_enrolled",
        "x = anything.dispatch_interfaces",
        "x = config.web.roots",
        "x = getattr(settings, 'exclude')",
    ],
)
def test_guard_catches_a_raw_read(line: str) -> None:
    """Сторож не пустой: временно вставленное `settings.enrolled` он находит."""
    assert _raw_reads(line, "x.py") != []


@pytest.mark.parametrize(
    "line",
    [
        "x = module.enrolled",
        "x = ruleset.exclude.rules",
        "x = args.exclude",
        "x = settings.enrolled_globs",
        "x = config.web.root_paths",
        "x = meta.roots",
    ],
)
def test_guard_leaves_other_models_alone(line: str) -> None:
    assert _raw_reads(line, "x.py") == []
