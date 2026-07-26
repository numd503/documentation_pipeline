"""Сквозной прогон: исходники -> манифест и сидкар (T16)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.emit import map_files_to_modules, parse_files, run_meta_path, scan
from docpipe.model import Manifest, RunMeta

GOLDEN = Path("tests/golden/doc-tree.json")
runner = CliRunner()


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_scan_writes_both_files(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "artifacts/doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"]
    )

    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert run_meta_path(out).is_file()
    assert run_meta_path(out).name == "doc-tree.run.json"


def test_manifest_is_valid(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"])

    manifest = Manifest.model_validate_json(out.read_text(encoding="utf-8"))
    assert len(manifest.nodes) == 6
    assert len(manifest.modules) == 2
    assert manifest.schema_version == "1.0"
    assert manifest.partial is None


def test_run_meta_is_valid(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"])

    meta = RunMeta.model_validate_json(run_meta_path(out).read_text(encoding="utf-8"))
    assert meta.stats["nodes"] == 6
    assert meta.stats["symbols"] == 10
    assert meta.stats["excluded"] == 1
    # Два интерфейса ушли в `interface_covered`: у обоих есть документируемая
    # реализация, и в `unclassified` им не место — см. T20.
    assert meta.stats["interface_covered"] == 2
    assert meta.stats["unclassified"] == 1
    assert meta.parse_error_files == []


def test_scan_creates_missing_directories(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "deep/nested/doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"]
    )
    assert result.exit_code == 0
    assert out.is_file()


# --------------------------------------------------------------------------------------
# Ничего недетерминированного в манифесте
# --------------------------------------------------------------------------------------


def test_manifest_contains_nothing_from_the_run(sample_solution: Path) -> None:
    """Время, хост и длительность обязаны остаться в сидкаре.

    Проверка именно по значениям из сидкара, а не по «нет текущего года»:
    `ruleset_version` сам выглядит как дата (`2026-07-26.1`), и наивная
    проверка падала бы на нём.
    """
    manifest, meta = scan(sample_solution)
    text = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False)

    assert meta.generated_at not in text
    assert meta.host not in text
    assert str(meta.duration_seconds) not in text


def test_manifest_has_no_absolute_paths(sample_solution: Path) -> None:
    manifest, _ = scan(sample_solution)
    text = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False)

    assert str(sample_solution.resolve()) not in text
    for node in manifest.nodes:
        assert node.symbol is not None
        for source in node.symbol.sources:
            assert not source.path.startswith("/")


def test_two_runs_give_identical_manifests(sample_solution: Path, tmp_path: Path) -> None:
    """Главное свойство шага: два прогона — один и тот же байт-в-байт файл."""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for out in (first, second):
        runner.invoke(
            app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"]
        )

    assert first.read_bytes() == second.read_bytes()
    # Недетерминированное живёт в сидкаре — там оно есть, и это нормально.
    # Сравнивать сидкары между собой бессмысленно: на быстрой фикстуре оба
    # прогона укладываются в одну секунду и дают одинаковый `generated_at`.
    assert "generated_at" in run_meta_path(first).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Золотой файл
# --------------------------------------------------------------------------------------


def test_matches_golden_manifest(sample_solution: Path, tmp_path: Path) -> None:
    """Зафиксированный результат: любое изменение вывода видно в диффе.

    Файл обновляется осознанно, а не по факту падения теста. Если он разошёлся,
    сначала нужно понять, изменение это или регрессия, — в том числе апгрейд
    грамматики, версии которой лежат прямо в манифесте.
    """
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"])

    assert out.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")


def test_golden_is_valid() -> None:
    manifest = Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))
    assert {node.title for node in manifest.nodes} == {
        "PricingController",
        "BaseApiController",
        "RiskComputeService",
        "ValuationWorkflow",
        "CurveProvider",
        "PricingService",
    }


# --------------------------------------------------------------------------------------
# Кэш и параллелизм
# --------------------------------------------------------------------------------------


def test_cache_does_not_change_the_result(sample_solution: Path, tmp_path: Path) -> None:
    """Кэш — оптимизация. Он обязан быть незаметен в выводе."""
    cache_dir = tmp_path / "cache"
    cold, _ = scan(sample_solution, cache_dir=cache_dir)
    warm, _ = scan(sample_solution, cache_dir=cache_dir)

    assert cold == warm
    assert cold == scan(sample_solution)[0]


def test_parallel_parsing_gives_the_same_result(sample_solution: Path) -> None:
    """При `--jobs > 1` порядок завершения задач случаен, поэтому важна пересортировка."""
    assert scan(sample_solution, jobs=1)[0] == scan(sample_solution, jobs=3)[0]


def test_parse_files_are_sorted_by_path(sample_solution: Path) -> None:
    relatives = [
        "src/Sample.Pricing.Api/Program.cs",
        "src/Sample.Common/Web/BaseApiController.cs",
        "src/Sample.Pricing.Api/Models/PriceDto.cs",
    ]
    results = parse_files(sample_solution, relatives, None)
    assert [r.path for r in results] == sorted(relatives)


# --------------------------------------------------------------------------------------
# Привязка файлов к модулям
# --------------------------------------------------------------------------------------


def test_files_map_to_the_nearest_project() -> None:
    mapping = map_files_to_modules(
        ["src/A/One.cs", "src/A/Nested/Two.cs", "src/B/Three.cs"],
        ["src/A/A.csproj", "src/B/B.csproj"],
    )
    assert mapping == {
        "src/A/One.cs": "src/A/A.csproj",
        "src/A/Nested/Two.cs": "src/A/A.csproj",
        "src/B/Three.cs": "src/B/B.csproj",
    }


def test_file_outside_any_project_is_dropped() -> None:
    """Общий код, подключённый через `<Compile Include>`, лежит вне проектов (T05b)."""
    mapping = map_files_to_modules(["shared/Guard.cs", "src/A/One.cs"], ["src/A/A.csproj"])
    assert mapping == {"src/A/One.cs": "src/A/A.csproj"}


def test_nested_project_wins_over_outer() -> None:
    mapping = map_files_to_modules(
        ["src/Outer/Inner/File.cs"], ["src/Outer/Outer.csproj", "src/Outer/Inner/Inner.csproj"]
    )
    assert mapping == {"src/Outer/Inner/File.cs": "src/Outer/Inner/Inner.csproj"}


# --------------------------------------------------------------------------------------
# Сообщение о сломанных файлах
# --------------------------------------------------------------------------------------


def test_broken_file_is_reported(wild_solution: Path, tmp_path: Path) -> None:
    """`ConditionalModule.cs` разбирается с ошибками и не даёт ни одного типа.

    Единственный внешний признак того, что тип уничтожен директивой внутри
    выражения, — поэтому список идёт в сидкар и в вывод команды.
    """
    out = tmp_path / "doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(wild_solution), "--out", str(out), "--no-cache"]
    )

    meta = RunMeta.model_validate_json(run_meta_path(out).read_text(encoding="utf-8"))
    assert meta.parse_error_files == ["src/Wild.Api/Modules/ConditionalModule.cs"]
    assert "не дали ни одного типа" in result.output


# --------------------------------------------------------------------------------------
# Ошибки пользователя — сообщением, а не traceback
# --------------------------------------------------------------------------------------


def test_missing_root_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["scan", "--root", str(tmp_path / "nope"), "--out", str(tmp_path / "o.json")]
    )
    assert result.exit_code == 2
    assert "каталог не найден" in result.output


def test_missing_rules_file_is_reported(sample_solution: Path, tmp_path: Path) -> None:
    """Опечатка в пути — это ошибка пользователя, а не сбой программы."""
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--rules",
            str(tmp_path / "nope.yaml"),
            "--out",
            str(tmp_path / "o.json"),
        ],
    )
    assert result.exit_code == 2
    assert "Ошибка конфигурации" in result.output


def test_broken_rules_file_is_reported(sample_solution: Path, tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "ruleset_version: t\nrules:\n  - {id: r, kind: k, template: t, priority: 1,"
        " when: {attribut: [X]}}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--rules",
            str(rules),
            "--out",
            str(tmp_path / "o.json"),
        ],
    )
    assert result.exit_code == 2
    assert "неизвестный предикат" in result.output
