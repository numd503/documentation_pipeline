"""«Что решено об этом коде»: `setup explain` (S23).

Вход «расширение области»: человек называет код, агент показывает, входит ли
он в область и какие записи настройки с какими причинами о нём решили.
Критерии приёмки — на `SampleSolution` (область, отсев, правила) и
`SeamWorkspace` (вызовы фронта и правила шва).

Правила передаются путём от корня этого репозитория, а не ключом `rules`
из `docpipe.yaml` фикстуры шва: тот записан от каталога конфигурации,
и в git worktree первая ступень `resolve_input` нашла бы правила основного
клона (`tests/test_seam_fixture.py`). Кэш выключен: иначе он лёг бы
в `.docpipe/` внутри фикстуры.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import (
    DocpipeConfig,
    Enrolled,
    ExcludeEntry,
    NamedDecision,
    NotEnrolled,
    UrlRewrite,
    load_config,
)
from docpipe.setup.candidates import CandidateInputs, candidates
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.explain import (
    BUILTIN_FILE,
    DEFAULT_FILE,
    DecisionRef,
    PathExplain,
    explain_json,
    explain_path,
    format_explain,
    normalize_target,
)
from docpipe.setup.status import decision_coverage
from tests.test_setup_review import (
    WIDGETS_DIR,
    WIDGETS_REWRITE,
    WIDGETS_UNRESOLVABLE,
    add_widgets,
)

runner = CliRunner()

FIXTURES: Final = Path(__file__).parent / "fixtures"
SAMPLE: Final = FIXTURES / "SampleSolution"
SEAM: Final = FIXTURES / "SeamWorkspace"
RULES: Final = Path("rules/rules.yaml")

PRICING: Final = "src/Sample.Pricing.Api"
PRICING_CSPROJ: Final = f"{PRICING}/Sample.Pricing.Api.csproj"
SERVICES: Final = f"{PRICING}/Services"
APPS_SERVICE: Final = "frontend/src/app/services/apps.service.ts"
APPS_PAGE: Final = "frontend/src/app/pages/apps-page.component.AppsPageComponent"


def _explain(
    settings: DocpipeConfig, target: str, root: Path = SAMPLE, **kwargs: Any
) -> PathExplain:
    return explain_path(SetupContext(root, settings, use_cache=False), target, **kwargs)


def _decision(report: PathExplain, key: str, value: str) -> DecisionRef:
    [found] = [ref for ref in report.decisions if (ref.key, ref.value) == (key, value)]
    return found


def _codes(report: PathExplain) -> list[str]:
    return [note.code for note in report.notes]


def _seam(**web: Any) -> DocpipeConfig:
    """Настройка фикстуры шва с правилами этого репозитория и дополнениями секции `web`."""
    settings = load_config(SEAM / "docpipe.yaml")
    return settings.model_copy(
        update={
            "rules": str(RULES),
            "web": settings.web.model_copy(update={"rules": str(RULES), **web}),
        }
    )


# --------------------------------------------------------------------------------------
# Отсев: отсечённое обходом не доходит ни до чего
# --------------------------------------------------------------------------------------


def test_builtin_exclusion_names_the_pattern_and_leaves_the_rest_empty() -> None:
    """Критерий приёмки: `obj` — встроенным отсевом, остальное пусто.

    Пустые разделы здесь — ответ, а не «ничего нет», и об этом говорит заметка.
    Встроенный отсев подписан своим «файлом»: причины у него нет, и выглядеть
    решением человека он не должен.
    """
    context = SetupContext(SAMPLE, DocpipeConfig(), use_cache=False)
    report = explain_path(context, f"{PRICING}/obj")

    assert report.matched_files == 1
    assert report.excluded_by is not None
    assert (report.excluded_by.file, report.excluded_by.value) == (BUILTIN_FILE, "**/obj/**")
    assert report.excluded_by.reason == ""
    for empty in (
        report.roots,
        report.web_roots,
        report.modules,
        report.symbols,
        report.symbol_rows,
        report.pages,
        report.page_overrides,
        report.calls,
        report.endpoints,
        report.documents,
        report.owners,
    ):
        assert not empty
    assert _codes(report) == ["path.excluded"]
    # В отсечённое обход не заходит, и прогонов для ответа не нужно вовсе.
    assert "scan" not in vars(context) and "web" not in vars(context)


def test_both_matching_builtin_patterns_are_listed() -> None:
    """`Sample.Generated.g.cs` под `obj/` совпал с двумя шаблонами — показаны оба.

    Правка одного из двух файл не вернёт, и агенту это надо видеть.
    """
    report = _explain(DocpipeConfig(), f"{PRICING}/obj")
    assert {(ref.file, ref.value, ref.count) for ref in report.decisions} == {
        (BUILTIN_FILE, "**/obj/**", 1),
        (BUILTIN_FILE, "**/*.g.cs", 1),
    }


def test_configured_exclusion_carries_its_reason() -> None:
    settings = DocpipeConfig(
        exclude=[ExcludeEntry(glob=f"{SERVICES}/**", reason="переписывается, ждём новую версию")]
    )
    context = SetupContext(SAMPLE, settings, use_cache=False)
    report = explain_path(context, SERVICES)

    assert report.excluded_by == DecisionRef(
        file="docpipe.yaml",
        key="exclude",
        value=f"{SERVICES}/**",
        reason="переписывается, ждём новую версию",
        effect="обход не читает файл",
        count=3,
    )
    assert report.symbols == {} and report.modules == []
    assert "отсечено шаблоном «src/Sample.Pricing.Api/Services/**»" in report.notes[0].message
    assert "scan" not in vars(context)


def test_partly_excluded_directory_lists_the_pattern_without_excluded_by() -> None:
    """В каталоге модуля отсечён только `obj/` — шаблон в списке, а `excluded_by` пуст."""
    report = _explain(DocpipeConfig(), PRICING)

    assert report.excluded_by is None
    assert _decision(report, "exclude", "**/obj/**").count == 1
    assert report.symbols["documented"] == 5


# --------------------------------------------------------------------------------------
# Область модуля и правила классификации
# --------------------------------------------------------------------------------------


def test_services_directory_names_the_module_and_the_winning_rule() -> None:
    """Критерий приёмки: модуль `enrolled`, символы с решениями, победитель вида `service`."""
    report = _explain(DocpipeConfig(), SERVICES)

    assert report.matched_files == 3
    assert report.roots == ["."]
    [module] = report.modules
    assert (module.module, module.project_file, module.scope) == (
        "Sample.Pricing.Api",
        PRICING_CSPROJ,
        "enrolled",
    )
    # Умолчание `enrolled` — не решение человека, и подписано оно так же.
    assert module.by is not None
    assert (module.by.file, module.by.key, module.by.value) == (DEFAULT_FILE, "enrolled", "**")

    assert report.symbols == {"documented": 1, "interface_covered": 1}
    assert [(row.name, row.state, row.winner_rule) for row in report.symbol_rows] == [
        ("IPricingService", "interface_covered", None),
        ("PricingService", "documented", "service"),
    ]
    assert _decision(report, "dotnet.rules", "service") == DecisionRef(
        file=RULES.as_posix(),
        key="dotnet.rules",
        value="service",
        reason="",
        effect="вид service",
        count=1,
    )
    [document] = report.documents
    assert (document.status, document.file_action) == ("missing", "create")
    assert document.doc_path.endswith("/pricing-service.md")
    assert report.documents_total == 1


def test_not_enrolled_module_names_the_entry_and_its_reason() -> None:
    """Критерий приёмки: `not_enrolled` с причиной — `scope: not_enrolled`, `by.reason`."""
    reason = "сервис выводится из эксплуатации, документировать не будем"
    settings = DocpipeConfig(not_enrolled=[NotEnrolled(glob=f"{PRICING}/**", reason=reason)])
    report = _explain(settings, SERVICES)

    [module] = report.modules
    assert module.scope == "not_enrolled"
    assert module.by is not None
    assert (module.by.key, module.by.value, module.by.reason) == (
        "not_enrolled",
        f"{PRICING}/**",
        reason,
    )
    assert module.by in report.decisions
    assert report.symbols == {"not_enrolled": 2}
    # Символы вне области правилами не решаются, документов у них нет.
    assert not any(ref.key == "dotnet.rules" for ref in report.decisions)
    assert report.documents == []


def test_module_outside_explicit_enrolled_is_undecided() -> None:
    report = _explain(DocpipeConfig(enrolled=["src/Sample.Common/**"]), SERVICES)
    [module] = report.modules
    assert (module.scope, module.by) == ("undecided", None)


def test_explicit_enrolled_entry_is_named_with_the_file_of_the_settings() -> None:
    settings = DocpipeConfig(enrolled=[Enrolled(glob=f"{PRICING}/**", reason="наш сервис")])
    [module] = _explain(settings, SERVICES).modules
    assert module.by is not None
    assert (module.by.file, module.by.value, module.by.reason) == (
        "docpipe.yaml",
        f"{PRICING}/**",
        "наш сервис",
    )


def test_excluded_symbol_names_the_exclusion_rule_and_its_reason() -> None:
    report = _explain(DocpipeConfig(), f"{PRICING}/Models")
    ref = _decision(report, "dotnet.exclude", "data.contracts")
    assert ref.reason and ref.effect == "не документируем"


def test_file_and_glob_targets_select_like_symbols_path() -> None:
    """Один ответ на «попадает ли файл под цель» с `symbols --path`."""
    one_part = _explain(DocpipeConfig(), f"{SERVICES}/PricingService.Calculations.cs")
    assert [row.name for row in one_part.symbol_rows] == ["PricingService"]
    assert one_part.matched_files == 1

    globbed = _explain(DocpipeConfig(), "**/Services/I*.cs")
    assert [row.name for row in globbed.symbol_rows] == ["IPricingService"]


def test_code_outside_roots_is_said_out_loud() -> None:
    """Вне `roots` обход не читает файлы, и сказать это обязан ответ, а не пустой раздел."""
    context = SetupContext(SAMPLE, DocpipeConfig(roots=["src/Sample.Common"]), use_cache=False)
    report = explain_path(context, SERVICES)

    assert report.matched_files == 3
    assert report.roots == [] and report.modules == [] and report.symbols == {}
    assert _codes(report) == ["path.outside_roots"]
    assert "scan" not in vars(context)


def test_empty_target_is_said_out_loud() -> None:
    report = _explain(DocpipeConfig(), "src/NoSuchProject")
    assert report.matched_files == 0
    assert _codes(report) == ["path.empty"]


def test_whole_repository_and_explicit_roots_are_decisions() -> None:
    """`.` — весь репозиторий; явно заданные `roots` — решение с охватом."""
    report = _explain(DocpipeConfig(roots=["src"]), ".")
    assert report.target == ""
    assert report.roots == ["src"]
    ref = _decision(report, "roots", "src")
    # Минус отсечённый `obj/…/*.g.cs` и `SampleSolution.sln` в корне — он вне `roots`.
    assert ref.count == report.matched_files - 2
    assert _codes(report) == ["owners.not_configured", "path.outside_roots"]
    assert [module.module for module in report.modules] == ["Sample.Common", "Sample.Pricing.Api"]


# --------------------------------------------------------------------------------------
# Только нужные прогоны
# --------------------------------------------------------------------------------------


def test_dotnet_target_does_not_run_the_web_step() -> None:
    """Шаг `web` не нужен коду .NET — и его отказ из-за `pages.yaml` такой ответ не роняет."""
    context = SetupContext(SAMPLE, DocpipeConfig(), use_cache=False)
    explain_path(context, SERVICES)
    assert "scan" in vars(context) and "web" not in vars(context)


def test_candidates_and_explain_share_one_scan() -> None:
    """Прогоны собирает один контекст: вторая команда на нём шаг 1 не повторяет."""
    assert CandidateInputs is SetupContext
    context = SetupContext(SAMPLE, DocpipeConfig(), use_cache=False)
    candidates("di-methods", context)
    scan = context.scan
    explain_path(context, SERVICES)
    assert context.scan is scan


# --------------------------------------------------------------------------------------
# DI, диспетчеризация, владение, шаг 2
# --------------------------------------------------------------------------------------

_PROJECT = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup /></Project>'
_PROGRAM = """
namespace App;
public static class Startup
{
    public static void Configure(IServiceCollection services)
    {
        services.AddSingletonAs<Clock>();
        services.AddSingletonAs<Cache>();
    }
}
public class Clock {}
public class Cache {}
"""
_HANDLERS = """
namespace App.Orders;
public class CreateOrder {}
public class CreateOrderHandler : IRequestHandler<CreateOrder>
{
    public void Handle(CreateOrder request) {}
}
"""


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src/App/Orders").mkdir(parents=True)
    (root / "src/App/App.csproj").write_text(_PROJECT, encoding="utf-8")
    (root / "src/App/Startup.cs").write_text(_PROGRAM, encoding="utf-8")
    (root / "src/App/Orders/Handlers.cs").write_text(_HANDLERS, encoding="utf-8")
    return root


def test_di_methods_and_dispatch_interfaces_are_named_where_they_decide(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    settings = DocpipeConfig(
        di_methods=[NamedDecision(name="AddSingletonAs", reason="обёртка Squidex.Hosting")],
        dispatch_interfaces=[NamedDecision(name="IRequestHandler", reason="MediatR")],
    )

    startup = _explain(settings, "src/App/Startup.cs", root)
    wrapper = _decision(startup, "di_methods", "AddSingletonAs")
    assert (wrapper.reason, wrapper.count) == ("обёртка Squidex.Hosting", 2)
    assert not any(ref.key == "dispatch_interfaces" for ref in startup.decisions)

    orders = _explain(settings, "src/App/Orders", root)
    handler = _decision(orders, "dispatch_interfaces", "IRequestHandler")
    assert (handler.reason, handler.count) == ("MediatR", 1)
    assert not any(ref.key == "di_methods" for ref in orders.decisions)


def test_owners_and_the_winning_ownership_rule(tmp_path: Path) -> None:
    ownership = tmp_path / "ownership.yaml"
    ownership.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "ownership_version": "1",
                "teams": [{"id": "pricing", "title": "Ценообразование"}],
                "rules": [
                    {
                        "id": "pricing.services",
                        "team": "pricing",
                        "priority": 10,
                        "when": {"path_glob": [f"{SERVICES}/**"]},
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    report = _explain(DocpipeConfig(ownership=str(ownership)), PRICING)

    # Узлов с документом пять, у сервиса владелец есть, у остальных — нет.
    assert report.owners == {"": 4, "pricing": 1}
    rule = _decision(report, "rules", "pricing.services")
    assert (rule.file, rule.effect, rule.count) == (
        ownership.as_posix(),
        "команда pricing",
        1,
    )
    assert "owners.not_configured" not in _codes(report)


def test_without_ownership_the_report_says_so() -> None:
    report = _explain(DocpipeConfig(), SERVICES)
    assert report.owners == {}
    assert "owners.not_configured" in _codes(report)


def test_step2_that_does_not_build_is_a_note_not_a_failure(tmp_path: Path) -> None:
    """Нет скелетов — шаг 2 не собрался; остальное в ответе есть, команда не падает."""
    report = _explain(DocpipeConfig(templates=str(tmp_path / "no-templates")), SERVICES)
    assert report.documents == [] and report.documents_total == 0
    assert "documents.unavailable" in _codes(report)
    assert report.symbols == {"documented": 1, "interface_covered": 1}


# --------------------------------------------------------------------------------------
# Фронт и шов
# --------------------------------------------------------------------------------------


def test_front_file_gives_calls_by_category_and_the_missing_rewrite() -> None:
    """Критерий приёмки: файл фронта — вызовы по категориям и правила шва.

    Без записи `url_rewrite` модуля правила нет — и об этом заметка: пустая
    запись и её отсутствие различаются (`web/link.py`, `_unconfigured`).
    """
    context = SetupContext(SEAM, _seam(), use_cache=False)
    report = explain_path(context, APPS_SERVICE)

    assert report.web_roots == ["frontend"] and report.roots == []
    [module] = report.modules
    assert (module.module, module.lang, module.scope) == ("seam-web", "ts", "enrolled")
    assert module.by is not None and (module.by.key, module.by.value) == ("web.roots", "frontend")

    assert report.calls == {"registry_unresolved": 0, "resolved": 2, "unresolved": 3}
    assert report.unresolved_reasons == [
        ("значение переменной — вызов `apiUrl.buildUrl(…)`", 2),
        ("база в начале конкатенации не восстановлена", 1),
    ]
    assert "link.module_without_rewrite" in _codes(report)
    assert not any(ref.key == "web.url_rewrite" for ref in report.decisions)
    # Сервис поглощён страницей: его описывает документ страницы.
    assert [page.title for page in report.pages] == ["AppsPageComponent"]
    assert "scan" not in vars(context)


def test_seam_rules_that_touch_the_file_are_decisions() -> None:
    rewrite = UrlRewrite(module="seam-web", reason="proxy.conf: pathRewrite нет")
    registry = {
        "route": "api/apps/archived",
        "discriminator": {"in": "query", "name": "kind"},
        "reason": "реестр приложений",
    }
    settings = load_config(SEAM / "docpipe.yaml")
    settings = DocpipeConfig.model_validate(
        {
            "roots": settings.roots,
            "rules": str(RULES),
            "web": {
                "roots": settings.web.root_paths,
                "rules": str(RULES),
                "url_rewrite": [rewrite.model_dump()],
                "registry_calls": [registry],
            },
        }
    )
    report = _explain(settings, APPS_SERVICE, SEAM)

    ref = _decision(report, "web.url_rewrite", "seam-web")
    # Два восстановленных и три невосстановленных вызова файла: запись модуля
    # решает о каждом его вызове, как у охвата `setup status` (S35).
    assert (ref.reason, ref.count) == ("proxy.conf: pathRewrite нет", 5)
    assert ref.effect == "strip_prefix '', add_prefix ''"
    registry_ref = _decision(report, "web.registry_calls", "api/apps/archived")
    assert (registry_ref.reason, registry_ref.effect, registry_ref.count) == (
        "реестр приложений",
        "различитель query.kind",
        1,
    )
    assert "link.module_without_rewrite" not in _codes(report)


def test_rewrite_of_a_module_with_only_unresolved_calls_counts_like_status(
    tmp_path: Path,
) -> None:
    """Запись `url_rewrite` модуля, все вызовы которого невосстановлены: счёт — как у охвата.

    `setup status` (`_cover_calls`) и `setup explain` (`_calls`) считают охват
    записи в двух местах; правка одного назвала бы у одной записи два числа
    (S35, ловушка «охват `url_rewrite` считается в двух местах»).
    """
    root = tmp_path / "ws"
    shutil.copytree(SEAM, root, ignore=shutil.ignore_patterns(".docpipe"))
    add_widgets(root)
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(RULES.resolve())
    raw["web"]["url_rewrite"] = [WIDGETS_REWRITE]
    raw["link"] = {"unresolvable": [WIDGETS_UNRESOLVABLE]}
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    context = SetupContext.build(root, config, use_cache=False)

    report = explain_path(context, WIDGETS_DIR)
    assert report.calls["resolved"] == 0 and report.calls["unresolved"] == 1
    ref = _decision(report, "web.url_rewrite", "widgets")
    [covered] = [
        item
        for item in decision_coverage(context)
        if (item.key, item.value) == ("web.url_rewrite", "widgets")
    ]
    assert ref.count == covered.count == 1
    assert "link.module_without_rewrite" not in _codes(report)


def test_pages_yaml_entries_that_touch_the_code(tmp_path: Path) -> None:
    pages = tmp_path / "pages.yaml"
    pages.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "pages": {
                    "remove": [{"component": APPS_PAGE, "reason": "экран-обёртка"}],
                    "features": [
                        {
                            "name": "apps",
                            "path": "frontend/src/app/services",
                            "title": "Приложения",
                            "reason": "раздел без своего маршрута",
                        }
                    ],
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    settings = _seam(pages=str(pages))

    page = _explain(settings, "frontend/src/app/pages", SEAM)
    removal = _decision(page, "remove", APPS_PAGE)
    assert (removal.file, removal.reason, removal.count) == (pages.as_posix(), "экран-обёртка", 1)
    assert page.page_overrides == [removal]

    services = _explain(settings, APPS_SERVICE, SEAM)
    feature = _decision(services, "features", "apps")
    assert feature.reason == "раздел без своего маршрута"
    assert feature.count == 2  # `AppsService` и `AppDto`
    assert [ref.key for ref in services.page_overrides] == ["features"]
    assert [item.title for item in services.pages] == ["Приложения"]


def test_backend_controllers_give_endpoints() -> None:
    report = _explain(_seam(), "backend/Seam.Api/Controllers", SEAM)
    # S17: 10 + 1 → 14 + 0. Префикс базы наследуется, `[Route]` без глагола
    # даёт `*`, `AcceptVerbs` — два эндпоинта, а `GET` заказов получил маршрут
    # базы вместо пустого (числа — `test_seam_fixture.test_endpoints`).
    assert report.endpoints == {"routed": 14, "unrouted": 0}
    assert report.calls == {}


# --------------------------------------------------------------------------------------
# Вход, страница, детерминизм
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["/etc", "../outside", "src\\App", "C:/x"])
def test_target_outside_the_root_is_refused(target: str) -> None:
    with pytest.raises(InputError):
        normalize_target(target)


def test_target_is_normalized() -> None:
    assert normalize_target("./src/Sample.Pricing.Api/Services/") == SERVICES
    assert normalize_target(".") == ""


def test_limit_cuts_rows_but_keeps_totals() -> None:
    report = _explain(DocpipeConfig(), PRICING, limit=1)
    assert len(report.symbol_rows) == 1 and sum(report.symbols.values()) == 8
    assert len(report.documents) == 1 and report.documents_total == 5
    assert "показано 1 из 8" in format_explain(report)

    with pytest.raises(InputError):
        _explain(DocpipeConfig(), PRICING, limit=-1)


def test_two_runs_are_byte_identical() -> None:
    first = explain_json(_explain(_seam(), ".", SEAM, limit=0))
    second = explain_json(_explain(_seam(), ".", SEAM, limit=0))
    assert first == second


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["setup", "explain", *args, "--no-cache"])


def test_command_prints_the_same_json_as_the_function() -> None:
    result = _invoke(SERVICES, "--root", str(SAMPLE), "--format", "json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1.0"
    assert result.output == explain_json(_explain(DocpipeConfig(), SERVICES))


def test_command_text_says_what_cut_the_code() -> None:
    result = _invoke(f"{PRICING}/obj", "--root", str(SAMPLE))
    assert result.exit_code == 0, result.output
    assert "Отсечено обходом: exclude: **/obj/** (встроенный отсев)." in result.output
    assert not result.output.endswith("\n\n")


def test_command_reads_the_settings_file(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "rules": str(RULES.resolve()),
                "not_enrolled": [{"glob": f"{PRICING}/**", "reason": "выводится"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    result = _invoke(SERVICES, "--root", str(SAMPLE), "--config", str(config), "--format", "json")
    assert result.exit_code == 0, result.output
    [module] = json.loads(result.output)["modules"]
    assert module["scope"] == "not_enrolled"
    assert module["by"]["file"] == config.as_posix()
    assert module["by"]["reason"] == "выводится"


@pytest.mark.parametrize(
    "args",
    [
        ["/abs", "--root", str(SAMPLE)],
        [SERVICES, "--root", str(SAMPLE), "--format", "jsno"],
        [SERVICES, "--root", str(SAMPLE), "--limit", "-1"],
        [SERVICES, "--root", str(SAMPLE), "--config", "no-such-docpipe.yaml"],
    ],
)
def test_bad_input_is_code_2(args: list[str]) -> None:
    assert _invoke(*args).exit_code == 2
