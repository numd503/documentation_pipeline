"""Счётчики, `--dry-run` и `validate` (T20)."""

import shutil
from pathlib import Path

from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.emit import run, scan, write_manifest, write_run_meta
from docpipe.model import DocNode, Manifest, Module, ParserVersions
from docpipe.stats import (
    collect_stats,
    format_breakdown,
    format_stats,
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
        "unclassified": 1,
        "excluded": 1,
    }


def test_interface_with_documented_implementation_is_not_unclassified(
    sample_solution: Path,
) -> None:
    """`IPricingService` реализован документируемым `PricingService`.

    Это не «правила не справились», а осознанное решение документировать
    реализацию. Смешивать такие интерфейсы с типами, про которые правила
    действительно ничего не знают, нельзя: `unclassified` существует ровно
    для настройки правил, и мусор в нём обесценивает счётчик. В eShopOnWeb
    таких 9 из 199, в ABP — 254 из 5267.
    """
    stats = run(sample_solution).stats

    assert stats.counts["interface_covered"] == 2
    assert stats.counts["unclassified"] == 1  # остаётся только Program


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
    assert "unclassified" not in stats.counts


# --------------------------------------------------------------------------------------
# Формат вывода
# --------------------------------------------------------------------------------------


def test_table_layout(sample_solution: Path) -> None:
    lines = format_stats(run(sample_solution).stats).splitlines()

    assert lines[0].startswith("kind")
    assert lines[0].rstrip().endswith("count")
    assert set(lines[1]) <= {"-", " "}
    assert lines[-1].startswith("total symbols")
    # Виды по алфавиту, служебные категории — после них.
    kinds = [line.split()[0] for line in lines[2:-2]]
    assert kinds[:5] == sorted(kinds[:5])
    assert kinds[-3:] == ["interface_covered", "unclassified", "excluded"]


def test_columns_fit_the_longest_label(sample_solution: Path) -> None:
    """`interface_covered` — 17 символов, колонка обязана расшириться под него.

    Иначе метка съезжает в колонку чисел, и таблица перестаёт читаться.
    """
    lines = format_stats(run(sample_solution).stats).splitlines()
    widths = {len(line) for line in lines}

    assert len(widths) == 1, "все строки таблицы одной ширины"
    covered = next(line for line in lines if line.startswith("interface_covered"))
    assert covered.split()[-1] == "2"
    assert covered.startswith("interface_covered ")  # метка не слиплась с числом


def test_stats_from_manifest_has_no_unclassified(sample_solution: Path) -> None:
    """В манифесте только узлы, поэтому непокрытое там посчитать не из чего."""
    manifest, _ = scan(sample_solution)
    stats = stats_from_manifest(manifest)

    assert stats.total == len(manifest.nodes)
    assert "unclassified" not in stats.counts
    assert "total nodes" in format_stats(stats, total_label="total nodes")


def test_empty_breakdown_formats_to_nothing() -> None:
    assert format_breakdown(collect_stats({}, [], load_ruleset(RULES))) == ""


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
    assert "total symbols" in result.output
    assert "unclassified" in result.output
    assert not out.exists()
    assert not out.with_suffix(".run.json").exists()


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
