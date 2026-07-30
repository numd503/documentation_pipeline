"""Счётчики, `--dry-run` и `validate` (T20)."""

import shutil
from pathlib import Path

from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.emit import run, scan, write_manifest, write_run_meta
from docpipe.model import DocNode, Manifest, Module, ParserVersions
from docpipe.stats import (
    collect_stats,
    format_breakdown,
    format_decisions,
    format_kinds,
    format_report,
    format_skipped,
    plural,
    stats_from_manifest,
    validate_manifest,
)

runner = CliRunner()
RULES = Path("rules/dotnet.yaml")
VERSIONS = ParserVersions(tree_sitter="1", grammar_c_sharp="1")


def _node(node_id: str, doc_path: str, kind: str = "service") -> DocNode:
    return DocNode(
        id=node_id,
        kind=kind,
        template=kind,
        title="C",
        doc_path=doc_path,
        module="M",
        domain="d",
        signature_hash="sha256:x",
    )


# --------------------------------------------------------------------------------------
# Счётчики на фикстуре
# --------------------------------------------------------------------------------------


def test_counts_on_fixture(sample_solution: Path) -> None:
    result = run(sample_solution)

    assert result.stats.total == 10
    assert result.stats.counts == {
        "controller": 2,
        "ignite_service": 1,
        "provider": 1,
        "service": 1,
        "workflow": 1,
        "interface_covered": 2,
        "undecided": 1,
        "not_documented": 1,
    }


def test_every_excluded_symbol_names_its_decision(sample_solution: Path) -> None:
    """Отсев без причины — это снова безымянное число, от которого и уходим."""
    stats = run(sample_solution).stats

    assert stats.skipped == [
        (
            "data.contracts",
            "Контракт передачи данных: смысл в вызывающем коде, а не в самом типе",
            1,
        )
    ]
    assert sum(count for _, _, count in stats.skipped) == stats.counts["not_documented"]


def test_skipped_is_ordered_by_count_then_id(tmp_path: Path, sample_solution: Path) -> None:
    """Порядок задан явно: таблица идёт в журнал, и он не должен зависеть от YAML."""
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "ruleset_version: t\nexclude:\n  rules:\n"
        "    - {id: zzz.classes, reason: r1, priority: 5, when: {type_kind: ['class']}}\n"
        "    - {id: aaa.interfaces, reason: r2, priority: 5, when: {type_kind: ['interface']}}\n"
        "rules: []\n",
        encoding="utf-8",
    )
    stats = run(sample_solution, ruleset=load_ruleset(rules)).stats

    ids = [rule_id for rule_id, _, _ in stats.skipped]
    counts = [count for _, _, count in stats.skipped]
    assert counts == sorted(counts, reverse=True)
    assert ids == ["zzz.classes", "aaa.interfaces"]  # 8 классов, 2 интерфейса


def test_interface_with_documented_implementation_is_not_undecided(
    sample_solution: Path,
) -> None:
    """`IPricingService` реализован документируемым `PricingService`.

    Это не «правила не справились», а осознанное решение документировать
    реализацию — принятое инструментом, а не человеком. Смешивать такие
    интерфейсы с типами, про которые решения нет, нельзя: `undecided`
    существует ровно для настройки правил, и мусор в нём обесценивает счётчик.
    В eShopOnWeb таких 9 из 199, в ABP — 254 из 5267.
    """
    stats = run(sample_solution).stats

    assert stats.counts["interface_covered"] == 2
    assert stats.counts["undecided"] == 1  # остаётся только Program


def test_totals_add_up(sample_solution: Path) -> None:
    """Каждый символ учтён ровно один раз — иначе счётчикам нельзя верить."""
    stats = run(sample_solution).stats
    assert sum(stats.counts.values()) == stats.total


def test_breakdown_points_at_what_is_missing(sample_solution: Path) -> None:
    stats = run(sample_solution).stats

    assert "модули" in stats.breakdown
    assert stats.breakdown["модули"] == [("Sample.Pricing.Api", 1)]
    # Пустые срезы не показываются: у единственного непокрытого типа
    # нет ни атрибутов, ни базовых типов.
    assert "атрибуты" not in stats.breakdown


def test_breakdown_is_empty_when_everything_is_covered(
    sample_solution: Path, tmp_path: Path
) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "ruleset_version: t\nexclude: {}\nrules:\n"
        "  - {id: all, kind: service, template: service, priority: 1,"
        " when: {type_kind: ['class', 'record', 'interface', 'enum', 'struct']}}\n",
        encoding="utf-8",
    )
    stats = run(sample_solution, ruleset=load_ruleset(rules)).stats

    assert stats.breakdown == {}
    assert "undecided" not in stats.counts


# --------------------------------------------------------------------------------------
# Формат вывода
# --------------------------------------------------------------------------------------


def test_decisions_block_ends_with_the_undecided_line(sample_solution: Path) -> None:
    """Значима последняя строка: всё выше неё решено, работа — в ней."""
    lines = format_decisions(run(sample_solution).stats).splitlines()

    assert lines[0] == "Решения по 10 символам:"
    assert set(lines[-2].strip()) == {"-"}
    assert lines[-1].strip().startswith("РЕШЕНИЕ НЕ ПРИНЯТО")
    assert lines[-1].split()[3] == "1"


def test_decisions_block_reports_completion_when_nothing_is_left(
    sample_solution: Path, tmp_path: Path
) -> None:
    """Ноль нерешённого — не пустая строка, а видимое «настройка закончена».

    Строка остаётся на месте и при нуле: исчезнув, она заставила бы искать,
    пропала она потому, что всё решено, или потому, что отчёт сломался.
    """
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "ruleset_version: t\nexclude: {}\nrules:\n"
        "  - {id: all, kind: service, template: service, priority: 1,"
        " when: {type_kind: ['class', 'record', 'interface', 'enum', 'struct']}}\n",
        encoding="utf-8",
    )
    block = format_decisions(run(sample_solution, ruleset=load_ruleset(rules)).stats)

    assert "решены все символы" in block
    assert "РЕШЕНИЕ НЕ ПРИНЯТО" not in block


def test_decisions_block_hides_empty_categories(sample_solution: Path) -> None:
    """Категория без символов уводит взгляд от той, в которой они есть."""
    block = format_decisions(run(sample_solution).stats)

    assert "интерфейс с реализацией" in block
    assert "вне области" not in block  # enrolled по умолчанию покрывает всё


def test_kind_table_layout(sample_solution: Path) -> None:
    lines = format_kinds(run(sample_solution).stats).splitlines()

    assert lines[0].startswith("kind")
    assert lines[0].rstrip().endswith("count")
    assert set(lines[1]) <= {"-", " "}
    # Только виды и только по алфавиту: служебные категории — в блоке решений,
    # и дублировать их значило бы предлагать сверять две таблицы об одном.
    kinds = [line.split()[0] for line in lines[2:]]
    assert kinds == sorted(kinds)
    assert kinds == ["controller", "ignite_service", "provider", "service", "workflow"]
    assert "total" not in lines[-1]


def test_kind_table_columns_fit_the_longest_label(sample_solution: Path) -> None:
    """Метка не должна слипаться с числом или съезжать в колонку чисел."""
    lines = format_kinds(run(sample_solution).stats).splitlines()
    widths = {len(line) for line in lines}

    assert len(widths) == 1, "все строки таблицы одной ширины"
    row = next(line for line in lines if line.startswith("ignite_service"))
    assert row.split()[-1] == "1"
    assert row.startswith("ignite_service ")


def test_report_puts_decisions_before_details(sample_solution: Path) -> None:
    """Порядок блоков несёт смысл: состояние, потом детали, потом что осталось."""
    report = format_report(run(sample_solution).stats)
    positions = [
        report.index("Решения по"),
        report.index("kind "),
        report.index("Не документируем"),
        report.index("Решение не принято — за что зацепиться"),
    ]

    assert positions == sorted(positions)


def test_stats_from_manifest_has_no_decisions(sample_solution: Path) -> None:
    """В манифесте только узлы, поэтому нерешённое там посчитать не из чего."""
    manifest, _ = scan(sample_solution)
    stats = stats_from_manifest(manifest)

    assert stats.total == len(manifest.nodes)
    assert "undecided" not in stats.counts
    assert "total nodes" in format_kinds(stats, total_label="total nodes")


def test_empty_blocks_format_to_nothing() -> None:
    empty = collect_stats({}, [], load_ruleset(RULES))

    assert format_breakdown(empty) == ""
    assert format_skipped(empty) == ""
    assert format_kinds(empty) == ""


def test_breakdown_marks_what_it_cut_off(sample_solution: Path) -> None:
    """Обрезанный срез обязан сказать, что он обрезан.

    На репозитории с сотнями проектов топ-15 выглядит полным списком, и половина
    работы остаётся невидимой. Проверяется на `--top 0`: срез есть, показано ноль.
    """
    stats = run(sample_solution, DocpipeConfig(enrolled=["**"])).stats
    cut = format_breakdown(stats, top=0)

    assert "модули:" in cut
    assert "и ещё 1 строка" in cut
    assert "Sample.Pricing.Api" not in cut


def test_breakdown_is_not_truncated_in_the_data(sample_solution: Path) -> None:
    """Обрезка — дело вывода: `--top` иначе пришлось бы протаскивать до `run()`."""
    stats = run(sample_solution).stats
    assert stats.breakdown["модули"] == [("Sample.Pricing.Api", 1)]


def test_top_flag_changes_how_much_is_shown(sample_solution: Path) -> None:
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--stats", "--top", "0", "--no-cache"]
    )

    assert result.exit_code == 0
    assert "и ещё" in result.output


def test_plural_agrees_with_the_number() -> None:
    """«1 правил» в отчёте выглядит дефектом инструмента."""
    forms = [plural(n, "правило", "правила", "правил") for n in (1, 2, 5, 11, 21, 112)]
    assert forms == ["1 правило", "2 правила", "5 правил", "11 правил", "21 правило", "112 правил"]


# --------------------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------------------


def test_valid_manifest_passes(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    manifest, meta = scan(sample_solution)
    write_manifest(manifest, out)
    write_run_meta(meta, out)

    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 0
    assert "корректен" in result.output


def test_broken_json_fails(tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    out.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    assert "схемой" in result.output


def test_duplicate_doc_path_fails() -> None:
    """Два узла, пишущие в один файл, — потеря документа на шаге 2."""
    manifest = Manifest(
        ruleset_version="v",
        parser=VERSIONS,
        nodes=[_node("type:a", "docs/x.md"), _node("type:b", "docs/x.md")],
    )
    errors, _ = validate_manifest(manifest)
    assert any("doc_path" in error for error in errors)


def test_duplicate_node_id_fails() -> None:
    """Совпадение id означает, что ключ построен неверно (на ABP так было 39 раз)."""
    manifest = Manifest(
        ruleset_version="v",
        parser=VERSIONS,
        nodes=[_node("type:a", "docs/x.md"), _node("type:a", "docs/y.md")],
    )
    errors, _ = validate_manifest(manifest)
    assert any("id узлов" in error for error in errors)


def test_duplicate_module_id_fails() -> None:
    module = Module(id="module:a", name="A", csproj="a/A.csproj", domain="d", enrolled=True)
    manifest = Manifest(ruleset_version="v", parser=VERSIONS, modules=[module, module])

    errors, _ = validate_manifest(manifest)
    assert any("id модулей" in error for error in errors)


def test_parse_error_files_fail_validation(wild_solution: Path, tmp_path: Path) -> None:
    """Файл с ошибками разбора и без объявлений — единственный признак того,
    что директива препроцессора уничтожила тип целиком."""
    out = tmp_path / "doc-tree.json"
    manifest, meta = scan(wild_solution)
    write_manifest(manifest, out)
    write_run_meta(meta, out)

    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    assert "ConditionalModule.cs" in result.output


def test_validation_without_sidecar_still_works(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    manifest, _ = scan(sample_solution)
    write_manifest(manifest, out)

    assert runner.invoke(app, ["validate", str(out)]).exit_code == 0


def test_multi_source_without_partial_is_a_warning(sample_solution: Path) -> None:
    """Не ошибка: такие файлы просто никогда не компилируются вместе."""
    manifest, _ = scan(sample_solution)
    node = next(n for n in manifest.nodes if n.title == "PricingService")
    assert node.symbol is not None

    broken = node.model_copy(
        update={"symbol": node.symbol.model_copy(update={"modifiers": ["public"]})}
    )
    errors, warnings = validate_manifest(manifest.model_copy(update={"nodes": [broken]}))

    assert errors == []
    assert warnings and "partial" in warnings[0]


# --------------------------------------------------------------------------------------
# Команды scan --stats и --dry-run
# --------------------------------------------------------------------------------------


def test_stats_flag_prints_and_writes_nothing(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--stats", "--no-cache"]
    )

    assert result.exit_code == 0
    assert "Решения по 10 символам" in result.output
    assert "РЕШЕНИЕ НЕ ПРИНЯТО" in result.output
    assert not out.exists()
    assert not out.with_suffix(".run.json").exists()


def test_fail_on_undecided_returns_one(sample_solution: Path, tmp_path: Path) -> None:
    """Ради этого кода возврата всё и переделано.

    Пока «не решено» было счётчиком «неклассифицировано», он был большим всегда,
    и новый тип в репозитории в нём не выделялся. Доведённое до нуля «не решено»
    превращает такой тип в упавшую сборку.
    """
    out = tmp_path / "doc-tree.json"
    args = ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"]

    failed = runner.invoke(app, [*args, "--stats", "--fail-on-undecided"])
    assert failed.exit_code == 1
    assert "Решение не принято по 1 символу" in failed.output

    silent = runner.invoke(app, [*args, "--stats"])
    assert silent.exit_code == 0


def test_fail_on_undecided_passes_when_everything_is_decided(
    sample_solution: Path, tmp_path: Path
) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "ruleset_version: t\nexclude:\n  rules:\n"
        "    - {id: all, reason: «фикстура целиком», when:"
        " {type_kind: ['class', 'record', 'interface', 'enum', 'struct']}}\n"
        "rules: []\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--out",
            str(tmp_path / "doc-tree.json"),
            "--rules",
            str(rules),
            "--no-cache",
            "--fail-on-undecided",
        ],
    )

    assert result.exit_code == 0, result.output


def test_dry_run_on_unchanged_sources(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"])
    before = out.read_bytes()

    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--dry-run", "--no-cache"]
    )

    assert result.exit_code == 0
    assert "Изменений нет" in result.output
    assert out.read_bytes() == before  # файл не перезаписан


def test_dry_run_shows_changes(sample_solution: Path, tmp_path: Path) -> None:
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root)
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(root), "--out", str(out), "--no-cache"])

    (root / "src/Sample.Pricing.Api/Workflows/ValuationWorkflow.cs").unlink()
    result = runner.invoke(
        app, ["scan", "--root", str(root), "--out", str(out), "--dry-run", "--no-cache"]
    )

    assert "removed" in result.output
    assert "ValuationWorkflow" in result.output


def test_dry_run_without_existing_manifest_shows_everything_as_added(
    sample_solution: Path, tmp_path: Path
) -> None:
    out = tmp_path / "doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--dry-run", "--no-cache"]
    )

    added = [line for line in result.output.splitlines() if line.startswith("added")]
    assert len(added) == 6
    assert not out.exists()


def test_stats_command_on_manifest(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"])

    result = runner.invoke(app, ["stats", str(out)])
    assert result.exit_code == 0
    assert "total nodes" in result.output
    assert "controller" in result.output


def test_stats_command_on_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["stats", str(tmp_path / "no.json")])
    assert result.exit_code == 2


def test_not_enrolled_symbols_are_counted_separately(sample_solution: Path, tmp_path: Path) -> None:
    """Символы неenrolled модулей — не «нерешённые».

    Решение по ним принято, просто в другом файле: `enrolled` в `docpipe.yaml`.
    Правила к ним и не применялись, а в `undecided` они дают ложный сигнал
    «допишите правил». На semantic-kernel это 1597 символов из 1258 «непокрытых» —
    то есть счётчик состоял из них целиком и настраивать по нему было нельзя.
    """
    config = DocpipeConfig(enrolled=["src/Sample.Pricing.Api/**"])
    stats = run(sample_solution, config).stats

    assert stats.counts["not_enrolled"] == 2  # два типа Sample.Common
    assert "BaseApiController" not in str(stats.breakdown)
    assert sum(stats.counts.values()) == stats.total


def test_breakdown_ignores_not_enrolled(sample_solution: Path) -> None:
    """Срезы подсказывают правила, поэтому неenrolled в них не место."""
    stats = run(sample_solution, DocpipeConfig(enrolled=["src/Sample.Common/**"])).stats
    assert all("Pricing" not in name for rows in stats.breakdown.values() for name, _ in rows)
