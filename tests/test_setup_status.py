"""Необъяснённое в области: `setup status` (S24).

Одна команда отвечает, что в области ещё без решения и что сломано, —
проверяемое состояние Р-6. Критерии приёмки — на `SampleSolution` (символы,
область, владение, шаг 2) и `SeamWorkspace` (шов: без правил и с правилами
S19–S21 в копии).

Правила классификации — путём от корня этого репозитория, а не ключом
`rules` из `docpipe.yaml` фикстуры шва (`tests/test_seam_fixture.py`
о первой ступени `resolve_input` в git worktree). Кэш выключен везде:
иначе он лёг бы в `.docpipe/` внутри фикстуры.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig, load_config
from docpipe.discovery import discover
from docpipe.emit import exclude_globs
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.explain import BUILTIN_FILE, DEFAULT_FILE, explain_path
from docpipe.setup.link import NOTE_NO_GAIN, link_clusters
from docpipe.setup.status import (
    CODES,
    FINDING_CODES,
    Finding,
    SetupStatus,
    build_status,
    decision_coverage,
    decision_id,
    format_status,
    load_baseline,
    status_json,
)
from tests.test_setup_link import S19_WEB
from tests.test_setup_review import (
    WIDGETS_FILE,
    WIDGETS_REWRITE,
    WIDGETS_UNRESOLVABLE,
    add_widgets,
)

runner = CliRunner()

FIXTURES: Final = Path(__file__).parent / "fixtures"
SAMPLE: Final = FIXTURES / "SampleSolution"
SEAM: Final = FIXTURES / "SeamWorkspace"
WEB: Final = FIXTURES / "WebWorkspace"
RULES: Final = Path("rules/rules.yaml")
TEMPLATES: Final = Path("templates")

PRICING_CSPROJ: Final = "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"
COMMON_CSPROJ: Final = "src/Sample.Common/Sample.Common.csproj"

# Явная область `SampleSolution`: без неё «всё решено» не бывает (S33,
# `scope.not_configured` на умолчании `enrolled`).
ENROLLED_SRC: Final[list[Any]] = [{"glob": "src/**", "reason": "продукт команды"}]

# Правило отсева на `Program` — решение «не документируем» с причиной.
PROGRAM_RULE: Final[dict[str, Any]] = {
    "id": "entry.program",
    "reason": "точка входа: конфигурация хоста, а не контракт",
    "when": {"name_regex": ["^Program$"]},
}

# Правила S20 и запись `url_rewrite` для копии шва: после них в коде фикстуры
# нет ни одного вызова без эндпоинта и ни одного невосстановленного без решения.
LINK_SECTION: Final[dict[str, Any]] = {
    "external_targets": [
        {"host": "ext.example.org", "reason": "лента партнёра, не наш бэк"},
        {"route": "api/apps/archived", "reason": "архив отдаёт внешний сервис"},
        {"route": "api/apps/search*", "reason": "поиск отдаёт внешний сервис"},
    ],
    "unresolvable": [
        {"path": "**/links.service.ts", "reason": "гипермедиа: адрес из ответа"},
        {"path": "**/editor.component.ts", "reason": "адрес задаёт родитель через @Input"},
        {"path": "**/apps.service.ts", "reason": "база из environment, сборкой"},
    ],
}
REWRITE: Final[dict[str, str]] = {
    "module": "seam-web",
    "reason": "setup link: ни одна пара strip/add не связывает больше — префикс не нужен",
}


def _status(root: Path, settings: DocpipeConfig, **kwargs: Any) -> SetupStatus:
    return build_status(SetupContext(root, settings, use_cache=False), **kwargs)


def _finding(report: SetupStatus, code: str) -> Finding | None:
    return next((item for item in report.findings if item.code == code), None)


def _codes(report: SetupStatus) -> set[str]:
    return {item.code for item in report.findings if item.count}


def _rules_with(tmp_path: Path, *exclusions: dict[str, Any]) -> Path:
    """Копия набора правил репозитория с дополнительными правилами отсева `dotnet`."""
    raw = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    raw["dotnet"]["exclude"].setdefault("rules", []).extend(exclusions)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def _ownership(tmp_path: Path, glob: str = "**") -> Path:
    """Правила владения с одной командой на всё под `glob`."""
    path = tmp_path / "ownership.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "ownership_version": "1",
                "teams": [{"id": "core", "title": "Ядро"}],
                "rules": [
                    {"id": "core.all", "team": "core", "priority": 0, "when": {"path_glob": [glob]}}
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def _seam_settings(**web: Any) -> DocpipeConfig:
    settings = load_config(SEAM / "docpipe.yaml")
    return settings.model_copy(
        update={
            "rules": str(RULES),
            "web": settings.web.model_copy(update={"rules": str(RULES), **web}),
        }
    )


def _copy_seam(root: Path, *, rules: bool, link: bool) -> Path:
    """Копия шва: правила S19 (`rules`), секция `link` S20 и `url_rewrite` (`link`)."""
    shutil.copytree(SEAM, root, ignore=shutil.ignore_patterns(".docpipe"))
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(RULES.resolve())
    if rules:
        raw["web"].update(S19_WEB)
    if link:
        raw["web"]["url_rewrite"] = [REWRITE]
        raw["link"] = LINK_SECTION
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return root


def _context(root: Path) -> SetupContext:
    return SetupContext.build(root, root / "docpipe.yaml", use_cache=False)


# --------------------------------------------------------------------------------------
# Коды находок
# --------------------------------------------------------------------------------------


def test_finding_codes_are_unique_and_defects_have_no_decision_home() -> None:
    codes = [item.code for item in FINDING_CODES]
    assert len(codes) == len(set(codes)) == len(CODES)
    for item in FINDING_CODES:
        assert item.category in ("decision", "defect")
        assert item.title and item.decision_home
        assert (item.decision_home == "—") == (item.category == "defect"), item.code
    # Сначала решения, потом дефекты: порядок отчёта — порядок таблицы.
    categories = [item.category for item in FINDING_CODES]
    assert categories == sorted(categories, key=lambda category: category == "defect")


def test_codes_of_the_plan_are_all_there() -> None:
    """Таблица спецификации S24 целиком — каталог вопросов (S28) читает эти коды."""
    planned = {
        "scope.not_configured",  # S33: умолчание области — тоже место без решения
        "scope.module_undecided",
        "scope.front_undecided",
        "dotnet.undecided",
        "web.undecided",
        "link.calls_unresolved",
        "link.calls_invisible",
        "link.calls_without_endpoint",
        "link.endpoints_without_caller",
        "link.almost",
        "link.module_without_rewrite",
        "link.registry_unresolved",
        "pages.route_unresolved",
        "pages.unanchorable",
        "pages.layout",
        "pages.stale_overrides",
        "docs.orphan",
        "owners.unowned",
        "owners.not_configured",
        "parse.errors",
        "config.problems",
        "docs.broken",
        "docs.shadowed",
        "load.errors",
        "docs.unavailable",
    }
    assert planned <= CODES.keys()
    # Сверх таблицы — дефект, который `web link` называет единственным дефектом шва.
    assert CODES.keys() - planned == {"link.duplicate_endpoints"}


# --------------------------------------------------------------------------------------
# SampleSolution: символы, область, владение, шаг 2
# --------------------------------------------------------------------------------------


def test_sample_defaults_give_program_undecided_clustered_by_module() -> None:
    """Критерий приёмки: с умолчаниями — `dotnet.undecided` = 1 (`Program`), кластер по модулю."""
    report = _status(SAMPLE, DocpipeConfig())
    finding = _finding(report, "dotnet.undecided")
    assert finding is not None
    assert (finding.count, finding.category, finding.previous) == (1, "decision", None)
    [module, *_] = finding.clusters
    assert (module.slice, module.key, module.count) == ("module", PRICING_CSPROJ, 1)
    assert module.examples == ["Sample.Pricing.Api.Program"]
    assert finding.decision_home == CODES["dotnet.undecided"].decision_home
    # Фронта нет — шва нет: эндпоинты контроллеров «без вызывающего» не находка.
    assert not {code for code in _codes(report) if code.startswith("link.")}
    assert report.defects == 0


def test_exclusion_with_reason_closes_the_finding_and_has_coverage(tmp_path: Path) -> None:
    """Критерий приёмки: правило отсева на `Program` — находки нет, у правила охват 1.

    «Всё решено» включает область: без явного `enrolled` осталась бы
    `scope.not_configured` (S33), поэтому область задана явно.
    """
    rules = _rules_with(tmp_path, PROGRAM_RULE)
    settings = DocpipeConfig(
        rules=str(rules), ownership=str(_ownership(tmp_path)), enrolled=ENROLLED_SRC
    )
    report = _status(SAMPLE, settings)

    assert report.findings == []
    assert (report.unexplained, report.defects) == (0, 0)
    [coverage] = [item for item in report.coverage if item.value == "entry.program"]
    assert (coverage.key, coverage.count, coverage.reason) == (
        "dotnet.exclude",
        1,
        PROGRAM_RULE["reason"],
    )
    assert coverage.id == decision_id(rules.as_posix(), "dotnet.exclude", "entry.program")


def test_fail_on_unexplained_is_zero_when_everything_is_decided(tmp_path: Path) -> None:
    """Критерий приёмки: `--fail-on-unexplained` — код 0, когда находок нет; 1 — когда есть."""
    rules = _rules_with(tmp_path, PROGRAM_RULE)
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "rules": str(rules),
                "ownership": str(_ownership(tmp_path)),
                "templates": str(TEMPLATES.resolve()),
                "enrolled": ENROLLED_SRC,
            }
        ),
        encoding="utf-8",
    )
    args = ["setup", "status", "--root", str(SAMPLE), "--config", str(config), "--no-cache"]
    result = runner.invoke(app, [*args, "--fail-on-unexplained"])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("В области: 0 находок без решения, 0 дефектов")

    undecided = runner.invoke(
        app, ["setup", "status", "--root", str(SAMPLE), "--no-cache", "--fail-on-unexplained"]
    )
    assert undecided.exit_code == 1
    # Без флага код 0 при любых находках: отчёт построен.
    plain = runner.invoke(app, ["setup", "status", "--root", str(SAMPLE), "--no-cache"])
    assert plain.exit_code == 0


def test_without_ownership_the_finding_is_not_configured() -> None:
    """Критерий приёмки: без `ownership` — `owners.not_configured`, по узлу на документ."""
    report = _status(SAMPLE, DocpipeConfig())
    finding = _finding(report, "owners.not_configured")
    assert finding is not None
    assert finding.count == 6  # узлов с документом на SampleSolution
    assert {cluster.key for cluster in finding.clusters} == {"Sample.Common", "Sample.Pricing.Api"}
    assert _finding(report, "owners.unowned") is None


def test_unowned_documents_are_named_by_module(tmp_path: Path) -> None:
    """Правило только на сервисы — остальные узлы без владельца, правило с охватом 1."""
    ownership = _ownership(tmp_path, "src/Sample.Pricing.Api/Services/**")
    report = _status(SAMPLE, DocpipeConfig(ownership=str(ownership)))
    finding = _finding(report, "owners.unowned")
    assert finding is not None and finding.count == 5
    assert _finding(report, "owners.not_configured") is None
    [rule] = [item for item in report.coverage if item.key == "rules"]
    assert (rule.file, rule.value, rule.count) == (ownership.as_posix(), "core.all", 1)


def test_without_templates_the_plan_is_a_defect_not_a_failure(tmp_path: Path) -> None:
    """Критерий приёмки: без скелетов — `docs.unavailable`, команда не падает."""
    missing = tmp_path / "no-templates"
    report = _status(SAMPLE, DocpipeConfig(templates=str(missing)))
    finding = _finding(report, "docs.unavailable")
    assert finding is not None and finding.category == "defect"
    assert {cluster.key for cluster in finding.clusters} == {"dotnet", "web"}
    assert all(str(missing) in cluster.examples[0] for cluster in finding.clusters)
    # Остальные находки на месте: они агенту нужны и тогда. Причину называет
    # и `config check`: вход `templates` не найден.
    assert _finding(report, "dotnet.undecided") is not None
    problems = _finding(report, "config.problems")
    assert problems is not None and [cluster.key for cluster in problems.clusters] == [
        "input-missing"
    ]
    assert finding.count == 2 and report.defects == 3

    config = tmp_path / "docpipe.yaml"
    config.write_text(yaml.safe_dump({"templates": str(missing)}), encoding="utf-8")
    args = ["setup", "status", "--root", str(SAMPLE), "--config", str(config), "--no-cache"]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, [*args, "--fail-on-unexplained"]).exit_code == 1


def test_short_name_found_in_the_product_is_a_config_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S36: `templates/` продукта (на abp — шаблоны стартовых решений) выиграл
    первую ступень у скелетов рядом с `docpipe.yaml`. Находка — та же
    `config.problems`, сборка из `check_config` без правки, кластер — код.

    Правила — абсолютным путём: команды зовутся из корня продукта, а там
    `rules/rules.yaml` этого репозитория нет.
    """
    repo = tmp_path / "repo"
    (repo / "templates").mkdir(parents=True)
    shutil.copytree(TEMPLATES, repo / "cfg" / "templates")
    rules = str(RULES.resolve())
    config = Path("cfg/docpipe.yaml")
    (repo / config).write_text(
        yaml.safe_dump({"templates": "templates", "rules": rules, "web": {"rules": rules}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    report = build_status(SetupContext.build(SAMPLE, config, use_cache=False))

    problems = _finding(report, "config.problems")
    assert problems is not None and problems.category == "defect"
    [cluster] = problems.clusters
    assert cluster.key == "input-shadowed"
    assert "cfg/templates" in cluster.examples[0]


def test_module_undecided_only_with_explicit_enrolled() -> None:
    """Ловушка: при умолчании `["**"]` находки нет; явный `enrolled` — модуль без решения."""
    assert _finding(_status(SAMPLE, DocpipeConfig()), "scope.module_undecided") is None

    report = _status(SAMPLE, DocpipeConfig(enrolled=["src/Sample.Common/**"]))
    finding = _finding(report, "scope.module_undecided")
    assert finding is not None and finding.count == 1
    [cluster] = finding.clusters
    assert (cluster.slice, cluster.key, cluster.examples) == ("directory", "src", [PRICING_CSPROJ])
    [enrolled] = [item for item in report.coverage if item.key == "enrolled"]
    assert (enrolled.value, enrolled.count) == ("src/Sample.Common/**", 1)


def test_scope_not_configured_until_enrolled_is_explicit() -> None:
    """S33: умолчание области — находка `scope.not_configured`; явный `enrolled` — нет.

    Вопрос «Область» онбординга строится из неё: до S33 он задавался без кода
    на всех четырёх репозиториях прогона S31. Фронта у `SampleSolution` нет —
    среза `front` нет, а не «фронт без решения».
    """
    report = _status(SAMPLE, DocpipeConfig())
    finding = _finding(report, "scope.not_configured")
    assert finding is not None and finding.category == "decision"
    assert finding.count == 2
    assert [(c.slice, c.key, c.count, c.examples) for c in finding.clusters] == [
        ("directory", "src", 2, [COMMON_CSPROJ, PRICING_CSPROJ])
    ]
    assert finding.count <= report.unexplained
    # Явность — по `model_fields_set`, а не по значению: `["**"]` — тоже ответ.
    for enrolled in (ENROLLED_SRC, ["**"]):
        explicit = _status(SAMPLE, DocpipeConfig.model_validate({"enrolled": enrolled}))
        assert _finding(explicit, "scope.not_configured") is None, enrolled
        assert _finding(explicit, "scope.module_undecided") is None, enrolled

    # Модуль под `not_enrolled` решён: ответ «не берём» уменьшает число.
    dropped = DocpipeConfig.model_validate(
        {"not_enrolled": [{"glob": "src/Sample.Pricing.Api/**", "reason": "чужой сервис"}]}
    )
    partial = _finding(_status(SAMPLE, dropped), "scope.not_configured")
    assert partial is not None and partial.count == 1
    assert partial.clusters[0].examples == [COMMON_CSPROJ]


def test_scope_not_configured_front_slice(tmp_path: Path) -> None:
    """Срез `front` — пока `web.roots` не задан явно; `[]` — ответ «фронта нет».

    Копия шва с явным `enrolled` и без `web.roots`: шаг `web` по умолчанию
    обходит весь корень и берёт `frontend` молча. С `web.roots: [frontend]`
    находки нет; с `web.roots: []` её тоже нет, а фронт разведки становится
    `scope.front_undecided` — вопрос о нём по её разделу.
    """
    root = _copy_seam(tmp_path / "seam", rules=False, link=False)
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["enrolled"] = [{"glob": "backend/**", "reason": "бэк команды"}]

    def report(**web: Any) -> SetupStatus:
        changed = {
            **raw,
            "web": {key: value for key, value in raw["web"].items() if key != "roots"},
        }
        changed["web"].update(web)
        config.write_text(yaml.safe_dump(changed, allow_unicode=True), encoding="utf-8")
        return build_status(_context(root))

    finding = _finding(report(), "scope.not_configured")
    assert finding is not None
    clusters = [(c.slice, c.key, c.count) for c in finding.clusters]
    assert clusters == [("front", "frontend", 1)]
    assert finding.count == 1
    [example] = finding.clusters[0].examples
    assert example.startswith("frontend/angular.json  ")

    assert _finding(report(roots=["frontend"]), "scope.not_configured") is None

    none = report(roots=[])
    assert _finding(none, "scope.not_configured") is None
    front = _finding(none, "scope.front_undecided")
    assert front is not None
    assert [(c.key, c.examples) for c in front.clusters] == [
        ("frontend", ["frontend/angular.json"])
    ]


def test_not_enrolled_is_out_of_scope_and_not_a_finding() -> None:
    settings = DocpipeConfig.model_validate(
        {
            "enrolled": ["src/Sample.Common/**"],
            "not_enrolled": [
                {"glob": "src/Sample.Pricing.Api/**", "reason": "сервис другой команды"}
            ],
        }
    )
    report = _status(SAMPLE, settings)
    assert _finding(report, "scope.module_undecided") is None
    # `Program` вне области; интерфейс провайдера без реализации в области
    # больше не «интерфейс с реализацией» — решение о нём нужно снова.
    undecided = _finding(report, "dotnet.undecided")
    assert undecided is not None
    assert undecided.clusters[0].examples == ["Sample.Common.Abstractions.IPricingProvider"]
    assert report.out_of_scope.modules == 1
    assert report.out_of_scope.symbols == 8  # все символы `Sample.Pricing.Api`
    assert report.out_of_scope.examples == [PRICING_CSPROJ]
    [dropped] = [item for item in report.coverage if item.key == "not_enrolled"]
    assert (dropped.count, dropped.reason) == (1, "сервис другой команды")


def test_documents_orphan_broken_and_shadowed(tmp_path: Path) -> None:
    """Сирота — документ без узла в **обоих** планах; невидимый файл — свой дефект."""
    root = tmp_path / "sample"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".docpipe"))
    docs = root / "docs" / "modules"
    docs.mkdir(parents=True)
    (docs / "orphan.md").write_text(
        "---\ndocpipe:\n  schema: materialize/1\n  node_id: type:nowhere#Gone`0\n---\n# Gone\n",
        encoding="utf-8",
    )
    (docs / "broken.md").write_text("---\ndocpipe: [\n", encoding="utf-8")
    shadowed = docs / "controllers" / "Sample.Pricing.Api" / "pricing-controller.md"
    shadowed.parent.mkdir(parents=True)
    shadowed.write_text("Текст без front matter: обход документов его не видит.\n", "utf-8")

    report = _status(root, DocpipeConfig())
    orphan = _finding(report, "docs.orphan")
    broken = _finding(report, "docs.broken")
    hidden = _finding(report, "docs.shadowed")
    assert orphan is not None and orphan.clusters[0].examples == ["docs/modules/orphan.md"]
    assert broken is not None and broken.clusters[0].examples == ["docs/modules/broken.md"]
    assert hidden is not None and hidden.clusters[0].examples == [
        "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"
    ]
    assert (orphan.category, broken.category, hidden.category) == ("decision", "defect", "defect")


def test_a_failed_run_is_a_load_error_and_the_rest_is_reported(tmp_path: Path) -> None:
    """Названный `web.pages` без файла роняет шаг `web` — дефект, шаг 1 отвечает как прежде."""
    settings = DocpipeConfig.model_validate({"web": {"pages": str(tmp_path / "pages.yaml")}})
    report = _status(SAMPLE, settings)
    finding = _finding(report, "load.errors")
    assert finding is not None and [cluster.key for cluster in finding.clusters] == ["web"]
    assert _finding(report, "dotnet.undecided") is not None
    # Решения прогона, который не собрался, в охват не идут: ноль у них — неизвестность.
    assert not [item for item in report.coverage if item.key.startswith("web.")]


def test_parse_errors_are_a_decision(tmp_path: Path) -> None:
    root = tmp_path / "wild"
    shutil.copytree(FIXTURES / "WildSolution", root, ignore=shutil.ignore_patterns(".docpipe"))
    report = _status(root, DocpipeConfig())
    finding = _finding(report, "parse.errors")
    assert finding is not None and finding.category == "decision"
    assert any(
        example.endswith("ConditionalModule.cs")
        for cluster in finding.clusters
        for example in cluster.examples
    )


# --------------------------------------------------------------------------------------
# Фронт: область и страницы
# --------------------------------------------------------------------------------------


def _front(root: Path, name: str) -> None:
    (root / name / "src").mkdir(parents=True)
    project = {name: {"root": "", "sourceRoot": "src", "projectType": "application"}}
    (root / name / "angular.json").write_text(json.dumps({"projects": project}), "utf-8")
    (root / name / "package.json").write_text('{"dependencies": {"@angular/core": "17"}}', "utf-8")
    (root / name / "src" / "x.ts").write_text("export class X {}\n", "utf-8")


def test_front_outside_web_roots_is_undecided_until_excluded(tmp_path: Path) -> None:
    _front(tmp_path, "app")
    _front(tmp_path, "legacy")
    report = _status(tmp_path, DocpipeConfig.model_validate({"web": {"roots": ["app"]}}))
    finding = _finding(report, "scope.front_undecided")
    assert finding is not None and finding.count == 1
    assert [(cluster.key, cluster.examples) for cluster in finding.clusters] == [
        ("legacy", ["legacy/angular.json"])
    ]
    assert report.out_of_scope.fronts == 0

    settings = DocpipeConfig.model_validate(
        {"web": {"roots": ["app"]}, "exclude": [{"glob": "legacy/**", "reason": "старый фронт"}]}
    )
    decided = _status(tmp_path, settings)
    assert _finding(decided, "scope.front_undecided") is None
    assert (decided.out_of_scope.fronts, decided.out_of_scope.examples) == (1, ["legacy"])
    [pattern] = [item for item in decided.coverage if item.key == "exclude"]
    assert (pattern.value, pattern.count) == ("legacy/**", 3)  # angular.json, package.json, x.ts


def test_default_web_roots_take_every_front(tmp_path: Path) -> None:
    _front(tmp_path, "app")
    _front(tmp_path, "legacy")
    assert _finding(_status(tmp_path, DocpipeConfig()), "scope.front_undecided") is None


def test_pages_notes_become_findings() -> None:
    """`WebWorkspace`: layout на пустом маршруте и страница с несобранной второй записью."""
    report = _status(WEB, DocpipeConfig())
    layout = _finding(report, "pages.layout")
    partial = _finding(report, "pages.route_unresolved")
    assert layout is not None and partial is not None
    shown = [example for cluster in layout.clusters for example in cluster.examples]
    assert "ShellComponent: /" in shown
    assert [example for cluster in partial.clusters for example in cluster.examples] == [
        "ListComponent: /models, ?/models"
    ]
    assert _finding(report, "pages.unanchorable") is None


# --------------------------------------------------------------------------------------
# SeamWorkspace: шов без правил и с правилами
# --------------------------------------------------------------------------------------


def test_seam_without_rules_names_the_seam_findings() -> None:
    """Критерий приёмки: без правил шва — без эндпоинта, невосстановленные, модуль без записи."""
    report = build_status(
        SetupContext(SEAM, _seam_settings(), SEAM / "docpipe.yaml", use_cache=False)
    )
    counts = {item.code: item.count for item in report.findings}
    assert counts["link.calls_without_endpoint"] == 3
    assert counts["link.calls_unresolved"] == 9
    assert counts["link.module_without_rewrite"] == 1
    assert counts["link.calls_invisible"] == 3
    assert counts["link.endpoints_without_caller"] == 13

    unresolved = _finding(report, "link.calls_unresolved")
    assert unresolved is not None
    # Кластеры — те же, что у `setup link --category calls_unresolved` (S21).
    assert unresolved.clusters[0].slice == "reason"
    assert unresolved.clusters[0].key == "значение переменной — параметр функции"
    invisible = _finding(report, "link.calls_invisible")
    assert invisible is not None
    assert {cluster.key for cluster in invisible.clusters} == {
        "HTTP.getVersioned",
        "HTTP.requestVersioned",
        "rest.request",
    }


def test_seam_rules_close_the_seam_findings(tmp_path: Path) -> None:
    """Критерий приёмки: правила S19–S20 и `url_rewrite` из подсказки S21 — находок шва нет.

    Подсказка S21 на фикстуре — «префикс не нужен» (`NOTE_NO_GAIN`): эндпоинты
    и фронт говорят `api/…` одинаково. Запись из неё — пустая: «проверено,
    преобразования нет», и она снимает `link.module_without_rewrite`.
    """
    hint = link_clusters(_context(_copy_seam(tmp_path / "hint", rules=True, link=False)))
    assert [cluster.rewrite_note for cluster in hint.clusters] == [NOTE_NO_GAIN]

    root = _copy_seam(tmp_path / "ws", rules=True, link=True)
    report = build_status(_context(root))
    codes = _codes(report)
    for closed in (
        "link.calls_without_endpoint",
        "link.calls_unresolved",
        "link.module_without_rewrite",
        "link.calls_invisible",
    ):
        assert closed not in codes, closed

    by_key = {(item.key, item.value): item.count for item in report.coverage}
    assert by_key[("link.external_targets", "ext.example.org")] == 1
    assert by_key[("link.unresolvable", "**/links.service.ts")] == 1
    assert by_key[("web.url_rewrite", "seam-web")] > 0
    assert by_key[("web.url_builders", "apiUrl.buildUrl")] > 0


def test_module_with_only_unresolved_calls_needs_its_rewrite_and_covers_it(
    tmp_path: Path,
) -> None:
    """Охват `url_rewrite` и находка `link.module_without_rewrite` — об одних вызовах (S35).

    Модуль `widgets`, чей единственный вызов невосстановим и объявлен
    `link.unresolvable`: без записи — находка (вызов у модуля есть), с пустой
    записью — находки нет, а у записи охват 1, а не 0.
    """
    root = _copy_seam(tmp_path / "ws", rules=True, link=True)
    add_widgets(root)
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["link"]["unresolvable"].append(WIDGETS_UNRESOLVABLE)
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    without = build_status(_context(root))
    finding = _finding(without, "link.module_without_rewrite")
    assert finding is not None and finding.count == 1
    [cluster] = finding.clusters
    assert (cluster.key, cluster.count) == ("widgets", 1)
    assert cluster.examples[0].startswith(f"{WIDGETS_FILE}:")

    raw["web"]["url_rewrite"].append(WIDGETS_REWRITE)
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    report = build_status(_context(root))
    assert "link.module_without_rewrite" not in _codes(report)
    by_key = {(item.key, item.value): item.count for item in report.coverage}
    assert by_key[("web.url_rewrite", "widgets")] == 1


def test_coverage_matches_setup_explain_on_the_whole_repository(tmp_path: Path) -> None:
    """Охват и `setup explain .` — одно знание о решениях, посчитанное дважды.

    Сторож от расхождения двух подсчётов: «к чему запись применилась» у ревью
    (S25) и «какая запись это решила» у `explain` обязаны совпасть. Решения,
    которых человек не принимал (встроенный отсев, умолчание), — только у `explain`.
    """
    root = _copy_seam(tmp_path / "ws", rules=True, link=True)
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["ownership"] = str(_ownership(tmp_path, "frontend/**"))
    raw["exclude"] = [{"glob": "**/environment.ts", "reason": "значения сборки"}, "nothing/**"]
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    context = _context(root)

    explained = explain_path(context, ".", limit=0)
    expected = {
        (ref.file, ref.key, ref.value): ref.count
        for ref in explained.decisions
        if ref.file not in (BUILTIN_FILE, DEFAULT_FILE)
    }
    covered = {
        (item.file, item.key, item.value): item.count
        for item in decision_coverage(context)
        if item.count
    }
    assert covered == expected
    keys = {key for _, key, _ in covered}
    assert {"exclude", "rules", "web.http_wrappers", "link.external_targets"} <= keys


def test_coverage_keeps_decisions_that_decided_nothing() -> None:
    settings = DocpipeConfig(exclude=["nothing/**"])
    report = _status(SAMPLE, settings)
    [pattern] = [item for item in report.coverage if item.key == "exclude"]
    assert (pattern.value, pattern.count, pattern.reason) == ("nothing/**", 0, "")
    assert pattern.id in report.without_reason


def test_coverage_by_file_is_in_memory_only(tmp_path: Path) -> None:
    """Разбивка «файл → сколько решено» — у `decision_coverage`, в отчёте только сумма."""
    rules = _rules_with(tmp_path, PROGRAM_RULE)
    context = SetupContext(SAMPLE, DocpipeConfig(rules=str(rules)), use_cache=False)
    [detail] = [item for item in decision_coverage(context) if item.value == "entry.program"]
    assert (detail.count, detail.files) == (1, {"src/Sample.Pricing.Api/Program.cs": 1})
    report = build_status(context)
    assert "files" not in report.model_dump()["coverage"][0]


def test_builtin_exclusion_counts_files_in_pruned_directories() -> None:
    """`excluded_by` — и под отсечённым каталогом: обход заходит туда только ради счёта."""
    found = discover(SAMPLE, exclude_globs(DocpipeConfig()), count_excluded=True)
    generated = "src/Sample.Pricing.Api/obj/Debug/net8.0/Sample.Generated.g.cs"
    assert found.excluded == {generated: ("**/*.g.cs", "**/obj/**")}
    assert found.excluded_by == {"**/*.g.cs": 1, "**/obj/**": 1}
    # Без флага не считали — `None`, а не «не отсекли ничего».
    assert discover(SAMPLE, exclude_globs(DocpipeConfig())).excluded_by is None


def test_pruned_and_walked_count_the_same() -> None:
    """Отсечение каталога — оптимизация и для счёта: те же файлы, что при обходе внутрь."""
    pruned = discover(SAMPLE, ["**/obj/**"], count_excluded=True)
    walked = discover(SAMPLE, ["**/obj/*", "**/obj/*/*", "**/obj/*/*/*"], count_excluded=True)
    assert pruned.excluded is not None and walked.excluded is not None
    assert set(pruned.excluded) == set(walked.excluded)
    assert pruned.cs_files == walked.cs_files


def test_counting_excluded_does_not_change_what_is_found() -> None:
    plain = discover(WEB, exclude_globs(DocpipeConfig()))
    counted = discover(WEB, exclude_globs(DocpipeConfig()), count_excluded=True)
    assert counted.ts_files == plain.ts_files
    assert counted.excluded_by is not None and counted.excluded_by.get("**/node_modules/**")


# --------------------------------------------------------------------------------------
# Сравнение прогонов, детерминизм, команда
# --------------------------------------------------------------------------------------


def test_out_then_baseline_gives_previous_equal_to_count(tmp_path: Path) -> None:
    """Критерий приёмки: `--out A`, затем `--baseline A` — `previous` равен `count`."""
    out = tmp_path / "runs" / "a.json"
    args = ["setup", "status", "--root", str(SAMPLE), "--no-cache"]
    first = runner.invoke(app, [*args, "--out", str(out)])
    assert first.exit_code == 0, first.output
    assert out.is_file()

    second = runner.invoke(app, [*args, "--baseline", str(out), "--format", "json"])
    assert second.exit_code == 0, second.output
    report = SetupStatus.model_validate_json(second.stdout)
    assert report.findings and report.coverage
    assert all(item.previous == item.count for item in report.findings)
    assert all(item.previous == item.count for item in report.coverage)
    assert "(без изменений)" in runner.invoke(app, [*args, "--baseline", str(out)]).stdout


def test_baseline_keeps_a_closed_finding_with_zero(tmp_path: Path) -> None:
    """Находка, закрытая с прошлого прогона, остаётся строкой с нулём — иначе разницы не видно."""
    before = _status(SAMPLE, DocpipeConfig())
    rules = _rules_with(tmp_path, PROGRAM_RULE)
    after = _status(SAMPLE, DocpipeConfig(rules=str(rules)), baseline=before)
    closed = _finding(after, "dotnet.undecided")
    assert closed is not None
    assert (closed.count, closed.previous, closed.clusters) == (0, 1, [])
    assert "(было 1, -1)" in format_status(after)
    # Новое решение в базе не было — `previous` у его охвата неизвестен.
    [rule] = [item for item in after.coverage if item.value == "entry.program"]
    assert rule.previous is None


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    """Критерий приёмки: два прогона — байт в байт, и функцией, и командой."""
    root = _copy_seam(tmp_path / "ws", rules=True, link=False)
    first, second = (status_json(build_status(_context(root))) for _ in range(2))
    assert first == second

    args = ["setup", "status", "--root", str(root), "--config", str(root / "docpipe.yaml")]
    outputs = [runner.invoke(app, [*args, "--no-cache", "--format", "json"]) for _ in range(2)]
    assert outputs[0].exit_code == 0, outputs[0].output
    assert outputs[0].stdout == outputs[1].stdout
    assert outputs[0].stdout.rstrip("\n") == first.rstrip("\n")
    texts = [runner.invoke(app, [*args, "--no-cache"]).stdout for _ in range(2)]
    assert texts[0] == texts[1]


def test_limit_cuts_clusters_but_keeps_totals() -> None:
    report = build_status(
        SetupContext(SEAM, _seam_settings(), SEAM / "docpipe.yaml", use_cache=False), limit=1
    )
    finding = _finding(report, "link.endpoints_without_caller")
    assert finding is not None
    assert len(finding.clusters) == 1 and finding.clusters_total > 1


def test_text_starts_with_the_summary_line() -> None:
    text = format_status(_status(SAMPLE, DocpipeConfig()))
    first = text.splitlines()[0]
    # 1 `dotnet.undecided` + 6 `owners.not_configured` + 2 `scope.not_configured` (S33).
    assert first == (
        "В области: 9 находок без решения, 0 дефектов; вне области: модулей 0, фронтов 0."
    )
    assert "символ .NET без решения (dotnet.undecided): 1" in text
    assert "(scope.not_configured): 2" in text


def test_command_json_equals_the_function() -> None:
    result = runner.invoke(
        app, ["setup", "status", "--root", str(SAMPLE), "--format", "json", "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    expected = status_json(_status(SAMPLE, DocpipeConfig()))
    assert result.stdout.rstrip("\n") == expected.rstrip("\n")


def test_baseline_that_is_not_a_report_is_refused(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.json"
    garbage.write_text('{"schema_version": "9.9"}', encoding="utf-8")
    with pytest.raises(InputError, match="база сравнения"):
        load_baseline(garbage)
    with pytest.raises(InputError):
        load_baseline(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "extra",
    [
        ["--format", "jsno"],
        ["--limit", "-1"],
        ["--baseline", "/nonexistent/baseline.json"],
        ["--config", "/nonexistent/docpipe.yaml"],
    ],
)
def test_bad_input_is_code_2(extra: list[str]) -> None:
    result = runner.invoke(app, ["setup", "status", "--root", str(SAMPLE), "--no-cache", *extra])
    assert result.exit_code == 2, result.output


def test_command_does_not_write_into_the_fixture(tmp_path: Path) -> None:
    """С `--no-cache` и без `--out` команда не пишет ничего — ни кэша, ни отчёта."""
    root = tmp_path / "sample"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".docpipe"))
    before = sorted(path.relative_to(root) for path in root.rglob("*"))
    assert runner.invoke(app, ["setup", "status", "--root", str(root), "--no-cache"]).exit_code == 0
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before
