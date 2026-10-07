"""Прогоны без молчания (S03).

Шесть мест, где прогон проходил и давал правдоподобный неполный результат:
смена `doc_layout` без пересборки манифеста, упавший адаптер реестра под
`graph build`, владение и реестры бизнес-ссылок шага 2, неполнота скоуп-прогона
под `--stats`, протухшие правила `pages.yaml` под `web scan --stats`. Каждый
тест здесь проверяет, что прогон говорит об этом вслух.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.arch import collect_configured, load_arch_registry, registry_for_build
from docpipe.cli import app
from docpipe.config import ArchAdapterConfig, DocpipeConfig
from docpipe.materialize.build import build_context
from docpipe.materialize.ownership import load_ownership
from docpipe.materialize.plan import PlanOptions, build_plan, layout_mismatch
from docpipe.materialize.template import load_templates
from docpipe.model import Manifest
from tests.business_support import business_doc, combined_tree, edit
from tests.business_support import manifest as business_manifest

runner = CliRunner()
GOLDEN = Path("tests/golden/doc-tree.json")
ARCH_FIXTURE = Path("tests/fixtures/arch/sample-arch.yaml")
MODULE_FIRST_CONTROLLER = "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md"


def _golden() -> Manifest:
    return Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


def _to_module_first(path: str) -> str:
    """`docs/modules/<вид>s/<модуль>/x.md` → `docs/modules/<модуль>/<вид>s/x.md`."""
    docs, modules, kinds, module, name = path.split("/")
    return f"{docs}/{modules}/{module}/{kinds}/{name}"


def _module_first(manifest: Manifest, only: str | None = None) -> Manifest:
    """Тот же манифест, собранный при `doc_layout: module-first`.

    `only` — перевести один узел: так выглядит манифест скоуп-прогона после
    смены ключа, где узлы вне скоупа перенесены из прежнего манифеста как есть.
    """
    return manifest.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"doc_path": _to_module_first(node.doc_path)})
                if only is None or node.title == only
                else node
                for node in manifest.nodes
            ]
        }
    )


def _write(manifest: Manifest, path: Path) -> Path:
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# 1. Раскладка манифеста против `doc_layout`
# --------------------------------------------------------------------------------------


def test_layout_mismatch_names_both_layouts_and_the_fix() -> None:
    """Отказ называет, с какой раскладкой собран манифест, что говорит
    конфигурация и как починить — двумя способами, оба за человеком."""
    message = layout_mismatch(_module_first(_golden()), "docs/modules", "kind-first")

    assert message is not None
    assert "манифест собран с раскладкой `module-first`" in message
    assert "конфигурация говорит `kind-first`" in message
    assert "`docpipe scan`" in message
    assert "doc_layout: module-first" in message
    assert "разложено узлов: 6 из 6" in message
    # Пример — первый путь по алфавиту: сообщение воспроизводится байт в байт.
    assert "docs/modules/Sample.Common/controllers/base-api-controller.md" in message


def test_layout_mismatch_is_silent_when_the_layouts_agree() -> None:
    assert layout_mismatch(_golden(), "docs/modules", "kind-first") is None
    assert layout_mismatch(_module_first(_golden()), "docs/modules", "module-first") is None


def test_layout_mismatch_works_in_both_directions() -> None:
    message = layout_mismatch(_golden(), "docs/modules", "module-first")

    assert message is not None
    assert "манифест собран с раскладкой `kind-first`" in message


def test_collision_suffix_does_not_count_as_a_layout() -> None:
    """Суффикс коллизии меняет имя файла, а не каталог: сверка по каталогам
    обязана его не замечать, иначе каждое разведённое имя давало бы отказ."""
    manifest = _golden()
    suffixed = manifest.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"doc_path": node.doc_path.replace(".md", "-1a2b3c4d.md")})
                for node in manifest.nodes
            ]
        }
    )

    assert layout_mismatch(suffixed, "docs/modules", "kind-first") is None
    assert layout_mismatch(_module_first(suffixed), "docs/modules", "kind-first") is not None


def test_nodes_named_like_their_kind_are_not_compared() -> None:
    """Модуль `services` и вид `service`: обе раскладки дают один каталог,
    и такой узел ничего не говорит о том, какой из них собран манифест."""
    manifest = _golden()
    ambiguous = manifest.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={
                        "module": "services",
                        "doc_path": "docs/modules/services/services/pricing-service.md",
                    }
                )
                for node in manifest.nodes
                if node.kind == "service"
            ]
        }
    )

    assert layout_mismatch(ambiguous, "docs/modules", "kind-first") is None
    assert layout_mismatch(ambiguous, "docs/modules", "module-first") is None


def test_one_node_in_the_other_layout_is_enough() -> None:
    """Скоуп-прогон после смены ключа даёт смешанный манифест: узлы вне скоупа
    переносятся из прежнего как есть. Отказ обязан сработать и на нём."""
    message = layout_mismatch(
        _module_first(_golden(), only="PricingController"), "docs/modules", "kind-first"
    )

    assert message is not None
    assert "разложено узлов: 1 из 6" in message
    assert "без `--scope`" in message


def test_frontend_manifest_is_sent_to_web_scan() -> None:
    """У фронта своя команда сборки: отсылать к `docpipe scan` значит отправить
    пересобирать не тот манифест."""
    manifest = _module_first(_golden())
    web = manifest.model_copy(
        update={
            "modules": [module.model_copy(update={"lang": "ts"}) for module in manifest.modules]
        }
    )

    message = layout_mismatch(web, "docs/modules", "kind-first")

    assert message is not None
    assert "`docpipe web scan`" in message


def test_plan_is_blocked_only_when_the_layout_is_known() -> None:
    """`doc_layout=None` — «не проверять», как пустой `modules_root`: план строят
    и там, где конфигурации нет (тесты, `adopt`)."""
    manifest = _module_first(_golden())
    templates = load_templates(Path("templates"))
    context = build_context(manifest, templates)

    blocked = build_plan(
        manifest,
        [],
        templates,
        context,
        options=PlanOptions(modules_root="docs/modules", doc_layout="kind-first"),
    )
    unchecked = build_plan(
        manifest, [], templates, context, options=PlanOptions(modules_root="docs/modules")
    )

    assert any("`module-first`" in error for error in blocked.errors)
    assert unchecked.errors == []


@pytest.mark.parametrize(
    "command",
    [
        ["materialize"],
        ["docs", "status"],
        ["worklist"],
    ],
)
def test_step2_commands_refuse_a_manifest_of_the_other_layout(
    command: list[str], tmp_path: Path
) -> None:
    """Критерий приёмки: манифест `module-first` при умолчании `kind-first`."""
    manifest = _write(_module_first(_golden()), tmp_path / "dt.json")
    extra = ["--out", str(tmp_path / "wl.json")] if command == ["worklist"] else []

    result = runner.invoke(app, [*command, str(manifest), "--root", str(tmp_path), *extra])

    assert result.exit_code == 1, result.output
    assert "docpipe scan" in result.output
    assert "module-first" in result.output
    assert not (tmp_path / "docs").exists()
    assert not (tmp_path / "wl.json").exists()


def test_step2_accepts_a_manifest_of_the_configured_layout(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text("doc_layout: module-first\n", encoding="utf-8")
    manifest = _write(_module_first(_golden()), tmp_path / "dt.json")

    result = runner.invoke(
        app, ["materialize", str(manifest), "--root", str(tmp_path), "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / MODULE_FIRST_CONTROLLER).is_file()


def test_plan_does_not_load_dotnet() -> None:
    """Формулу раскладки план берёт у шага 1 (`docpipe.tree.doc_path_for`),
    а `materialize/*` не вправе зависеть от разбора .NET.

    Тест границы в `test_materialize_cli.py` проверяет только прямые импорты
    пакета. Этот — транзитивные: потянет `docpipe.tree` однажды `docpipe.dotnet`,
    и функцию придётся вынести в отдельный модуль, а не скопировать.
    """
    probe = (
        "import sys, docpipe.materialize.plan; "
        "print(sorted(m for m in sys.modules if m.startswith('docpipe.dotnet')))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert loaded.stdout.strip() == "[]"


# --------------------------------------------------------------------------------------
# 2. Реестр для `graph build`
# --------------------------------------------------------------------------------------


def _broken_adapter() -> ArchAdapterConfig:
    # У адаптера `registries` обязательный параметр `spec`: без него он падает
    # исключением, которое `collect` складывает в `errors`.
    return ArchAdapterConfig(id="реестры", adapter="registries", options={})


def test_registry_for_build_is_empty_without_sources(tmp_path: Path) -> None:
    assert registry_for_build(DocpipeConfig(), None, tmp_path).records == ()


def test_registry_for_build_reads_the_named_snapshot(tmp_path: Path) -> None:
    settings = DocpipeConfig(arch=ARCH_FIXTURE.as_posix())

    registry = registry_for_build(settings, None, tmp_path)

    assert registry.records
    assert registry.records == load_arch_registry(ARCH_FIXTURE).records


def test_named_snapshot_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """Названный файл назвал человек: пустой реестр вместо него — тот же ответ
    «точек входа из реестра нет», что и при честно пустом."""
    config = tmp_path / "conf" / "docpipe.yaml"
    settings = DocpipeConfig(arch="arch-registry.yaml")

    with pytest.raises(ValueError, match="не найден") as caught:
        registry_for_build(settings, config, tmp_path)

    assert "arch-registry.yaml" in str(caught.value)
    assert str(config.parent / "arch-registry.yaml") in str(caught.value)


def test_adapter_error_is_refused(tmp_path: Path) -> None:
    settings = DocpipeConfig(arch_adapters=[_broken_adapter()])

    with pytest.raises(ValueError, match="реестры: ") as caught:
        registry_for_build(settings, None, tmp_path)

    assert "spec" in str(caught.value)


def test_all_problems_come_in_one_message(tmp_path: Path) -> None:
    """Чинить за один прогон: и отсутствующий снимок, и упавший адаптер."""
    settings = DocpipeConfig(arch="нет-такого.yaml", arch_adapters=[_broken_adapter()])

    with pytest.raises(ValueError, match=r"с ошибками \(2\)"):
        registry_for_build(settings, None, tmp_path)


def test_arch_records_keeps_showing_errors_instead_of_failing(tmp_path: Path) -> None:
    """Политику отказа задаёт вызывающий: `arch records` показывает находки
    человеку и падать из-за них не должен — иначе их негде будет увидеть."""
    settings = DocpipeConfig(arch_adapters=[_broken_adapter()])

    collected = collect_configured(settings, None, tmp_path)

    assert collected.errors
    assert collected.registry.records == ()


def test_graph_build_refuses_a_broken_registry(tmp_path: Path) -> None:
    """Реестр собирается до запуска разборщика, поэтому отказ виден и без него."""
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        f"arch: нет-такого.yaml\ngraph:\n  engine_path: {tmp_path / 'нет-разборщика'}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["graph", "build", "--root", str(tmp_path), "--config", str(config)]
    )

    assert result.exit_code == 2, result.output
    assert "не найден" in result.output
    assert "Разбор репозитория" not in result.output


# --------------------------------------------------------------------------------------
# 3. Бизнес-ссылки шага 2: владение и ошибки реестров
# --------------------------------------------------------------------------------------

OWNERSHIP = """
version: "1"
ownership_version: "test"
teams:
  - id: ML
    title: ML
  - id: Core
    title: Core
rules:
  - id: receivers
    team: {team}
    priority: 10
    when:
      namespace_prefix: ["Sbt.Cashflow.ML"]
"""
RECEIVER = "docs/modules/App/services/usertasksaddedtriggersampleworkfloweventreceiver.md"


@pytest.fixture
def business_tree(tmp_path: Path) -> Path:
    """Реестры, бизнес-каталог и синтетический манифест под одним корнем.

    Якорь события сужен до команды ML: селектор `only.team` работает только
    по правилам владения, и по нему видно, какие правила дошли до бизнес-ссылок.
    Манифест разложен по `module-first`, отсюда ключ в конфигурации.
    """
    root = combined_tree(tmp_path)
    _write(business_manifest(), root / "doc-tree.json")
    edit(
        root / "business/processes/valuation/twinml-scoring.md",
        "    scope: UserTasks\n",
        "    scope: UserTasks\n    only:\n      team: ML\n",
    )
    for team in ("ML", "Core"):
        (root / f"{team.lower()}.yaml").write_text(
            OWNERSHIP.replace("{team}", team), encoding="utf-8"
        )
    # Ошибка смысла, а не синтаксиса: команда, которой нет в `teams`.
    (root / "broken.yaml").write_text(OWNERSHIP.replace("{team}", "Nobody"), encoding="utf-8")
    (root / "docpipe.yaml").write_text(
        f"registries: {root / 'registries.yaml'}\nbusiness_root: business\n"
        "doc_layout: module-first\nownership: broken.yaml\n",
        encoding="utf-8",
    )
    return root


def _materialize(root: Path, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app,
        [
            "materialize",
            str(root / "doc-tree.json"),
            "--root",
            str(root),
            "--templates",
            "templates",
            "--config",
            str(root / "docpipe.yaml"),
            *extra,
        ],
    )


def _business_context(root: Path) -> str:
    text = (root / RECEIVER).read_text(encoding="utf-8")
    return text.split("Бизнес-контекст", 1)[1].split("<!-- docpipe:generated:end -->", 1)[0]


def test_business_links_use_the_ownership_flag(business_tree: Path) -> None:
    """Бизнес-ссылки строятся от того же владения, что и план.

    Своё чтение брало ключ `ownership` (здесь он битый) мимо `--ownership`
    и глотало ошибку: `only.team` не сужал ничего, ссылки не было при любом
    флаге, а план тем временем раскладывал документы по командам из флага.
    """
    ml = _materialize(business_tree, "--ownership", str(business_tree / "ml.yaml"))
    assert ml.exit_code == 0, ml.output
    assert "Онлайн УФН" in _business_context(business_tree)

    core = _materialize(business_tree, "--ownership", str(business_tree / "core.yaml"))
    assert core.exit_code == 0, core.output
    assert "Онлайн УФН" not in _business_context(business_tree)


def test_broken_ownership_flag_fails_as_the_plan_does(business_tree: Path) -> None:
    broken = business_tree / "broken.yaml"
    with pytest.raises(ValueError) as caught:
        load_ownership(broken)

    result = _materialize(business_tree, "--ownership", str(broken))

    assert result.exit_code == 2
    assert str(caught.value) in result.stderr
    assert not (business_tree / RECEIVER).exists()


def test_registry_and_catalog_errors_are_warnings(business_tree: Path) -> None:
    """Ошибки реестров и каталога — в stderr с префиксом, код возврата прежний.

    Отказ всего `materialize` из-за реестра вне цели был бы хуже молчания,
    но и молчать нельзя: выброшенная запись реестра выглядит как «у этого
    класса нет бизнес-контекста».
    """
    misplaced = business_tree / "business/processes/valuation/misplaced.md"
    misplaced.write_text(
        business_doc({"id": "bp.valuation.elsewhere", "kind": "process", "title": "Не там"}),
        encoding="utf-8",
    )

    result = _materialize(business_tree, "--ownership", str(business_tree / "ml.yaml"))

    assert result.exit_code == 0, result.output
    assert "бизнес-ссылки: deployment/Data/Items/Items_Broken.xml" in result.stderr
    assert "бизнес-ссылки: business/processes/valuation/misplaced.md" in result.stderr
    # Предупреждения не мешают собрать раздел из того, что прочиталось.
    assert "Онлайн УФН" in _business_context(business_tree)


# --------------------------------------------------------------------------------------
# 4. Неполнота скоуп-прогона под `--stats` и `--dry-run`
# --------------------------------------------------------------------------------------

PRICING = "src/Sample.Pricing.Api"


@pytest.fixture
def cold_scope(sample_solution: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Полный манифест есть, кэша нет: скоуп-прогону вне скоупа взять неоткуда.

    `.docpipe/` фикстуры не копируется: это кэш ручных прогонов из корня
    репозитория, git его не видит, и копия делала бы кэш тёплым на машине
    разработчика и холодным на свежем клоне — тест проверял бы разное.
    """
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root, ignore=shutil.ignore_patterns(".docpipe"))
    full = tmp_path / "full.json"
    written = runner.invoke(app, ["scan", "--root", str(root), "--out", str(full), "--no-cache"])
    assert written.exit_code == 0, written.output
    return root, full


@pytest.mark.parametrize("mode", ["--stats", "--dry-run"])
def test_scoped_stats_and_dry_run_warn_about_the_cold_cache(
    cold_scope: tuple[Path, Path], tmp_path: Path, mode: str
) -> None:
    root, full = cold_scope
    out = tmp_path / "scoped.json"

    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(root),
            "--scope",
            PRICING,
            "--from-manifest",
            str(full),
            "--out",
            str(out),
            mode,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "отсутствуют в кэше" in result.stderr
    assert "Частичный прогон" in result.stderr
    assert not out.exists()


def test_parse_errors_are_named_without_a_sidecar(wild_solution: Path, tmp_path: Path) -> None:
    """Без записи сидкара нет, а прежний описывает прошлый прогон: файлы с
    уничтоженными типами называются прямо в предупреждении."""
    result = runner.invoke(app, ["scan", "--root", str(wild_solution), "--stats", "--no-cache"])

    assert result.exit_code == 0, result.output
    assert "ConditionalModule.cs" in result.stderr
    assert "сидкаре" not in result.stderr


# --------------------------------------------------------------------------------------
# 5. Протухшие правила `pages.yaml` под `web scan --stats`
# --------------------------------------------------------------------------------------


def test_web_stats_reports_stale_rules_and_fails_when_asked(
    web_workspace: Path, tmp_path: Path
) -> None:
    pages = tmp_path / "pages.yaml"
    pages.write_text(
        'version: "1"\npages:\n  add:\n    - route: "/x"\n'
        '      component: "src/app/gone.Missing"\n      reason: "протухло"\n',
        encoding="utf-8",
    )
    arguments = ["web", "scan", "--root", str(web_workspace), "--pages", str(pages), "--stats"]

    soft = runner.invoke(app, [*arguments, "--no-cache"])
    hard = runner.invoke(app, [*arguments, "--no-cache", "--fail-on-stale-overrides"])

    assert soft.exit_code == 0, soft.output
    assert "не легло" in soft.stderr
    assert hard.exit_code == 1
    assert "Отказ: правил в ручном составе страниц" in hard.stderr


def test_web_stats_without_stale_rules_does_not_fail(web_workspace: Path) -> None:
    result = runner.invoke(
        app,
        ["web", "scan", "--root", str(web_workspace), "--stats", "--no-cache"]
        + ["--fail-on-stale-overrides"],
    )

    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------
# Сквозное: исправленный манифест действительно переносит документы
# --------------------------------------------------------------------------------------


def test_rescan_after_layout_change_relocates_instead_of_refusing(tmp_path: Path) -> None:
    """Штатная процедура смены раскладки остаётся выполнимой: сверяется манифест
    с конфигурацией, а не документы на диске с конфигурацией."""
    config = tmp_path / "module-first.yaml"
    config.write_text("doc_layout: module-first\n", encoding="utf-8")
    old = _write(_module_first(_golden()), tmp_path / "old.json")
    first = runner.invoke(
        app, ["materialize", str(old), "--root", str(tmp_path), "--config", str(config)]
    )
    assert first.exit_code == 0, first.output

    # Ключ вернули к умолчанию и пересобрали манифест: документы ещё лежат
    # по `module-first`, манифест — уже по `kind-first`.
    moved = runner.invoke(app, ["materialize", str(GOLDEN), "--root", str(tmp_path)])

    assert moved.exit_code == 0, moved.output
    assert not (tmp_path / MODULE_FIRST_CONTROLLER).exists()
    assert (tmp_path / "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md").exists()
