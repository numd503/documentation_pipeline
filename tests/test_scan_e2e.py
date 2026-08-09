"""Сквозной прогон: исходники -> манифест и сидкар (T16)."""

import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig
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
    assert manifest.schema_version == "2.0"
    assert manifest.partial is None


def test_run_meta_is_valid(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"])

    meta = RunMeta.model_validate_json(run_meta_path(out).read_text(encoding="utf-8"))
    assert meta.stats["nodes"] == 6
    assert meta.stats["symbols"] == 10
    assert meta.stats["documented"] == 6
    assert meta.stats["not_documented"] == 1
    # Два интерфейса ушли в `interface_covered`: у обоих есть документируемая
    # реализация, и в `undecided` им не место — см. T20 и T23.
    assert meta.stats["interface_covered"] == 2
    assert meta.stats["undecided"] == 1
    assert meta.parse_error_files == []


def test_scan_creates_missing_directories(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "deep/nested/doc-tree.json"
    result = runner.invoke(
        app, ["scan", "--root", str(sample_solution), "--out", str(out), "--no-cache"]
    )
    assert result.exit_code == 0
    assert out.is_file()


# --------------------------------------------------------------------------------------
# Инструмент внутри сканируемого репозитория
# --------------------------------------------------------------------------------------


def _repo_with_tooling_inside(sample_solution: Path, tmp_path: Path) -> Path:
    """Копия решения, внутри которой лежит каталог с посторонним .NET-кодом.

    Воспроизводит раскладку АС CF: `docs/ml/docspipe` с самим инструментом
    (а в нём — фикстуры на C#) внутри сканируемого репозитория.

    Каталог назван `fixtures`, а не `tests/fixtures`, намеренно: сегмент `tests`
    отсеялся бы правилом `**/tests/**` из `rules/dotnet.yaml`, и узла не было бы
    даже без исключения. Модулем посторонний проект стал бы всё равно, а его
    символы всё равно попали бы в индекс — то есть проверка исключения
    маскировалась бы отсевом на другом уровне.
    """
    root = tmp_path / "repo"
    shutil.copytree(sample_solution, root)
    alien = root / "docs/ml/docspipe/fixtures/Alien"
    alien.mkdir(parents=True)
    (alien / "Alien.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    (alien / "AlienService.cs").write_text(
        "namespace Alien; public class AlienService { }", encoding="utf-8"
    )
    return root


def test_tooling_directory_pollutes_manifest_without_exclude(
    sample_solution: Path, tmp_path: Path
) -> None:
    """Контрольная проверка: без исключения посторонний код попадает в документацию.

    Без неё тест ниже проходил бы и в том случае, если исключать нечего.
    """
    manifest, _ = scan(_repo_with_tooling_inside(sample_solution, tmp_path))
    assert len(manifest.modules) == 3
    assert "AlienService" in {node.title for node in manifest.nodes}


def test_exclude_keeps_tooling_directory_out_of_the_manifest(
    sample_solution: Path, tmp_path: Path
) -> None:
    root = _repo_with_tooling_inside(sample_solution, tmp_path)
    manifest, _ = scan(root, DocpipeConfig(exclude=["docs/**"]))

    assert len(manifest.modules) == 2
    assert "AlienService" not in {node.title for node in manifest.nodes}
    # Результат обязан совпасть с прогоном по решению без постороннего каталога.
    assert manifest == scan(sample_solution)[0]


def test_out_from_config_is_used_when_flag_is_absent(sample_solution: Path, tmp_path: Path) -> None:
    """Поле `out` в конфигурации должно работать: иначе манифест уезжает мимо.

    Флаг по-прежнему важнее — но раньше у него было значение по умолчанию,
    и конфигурация не влияла ни на что, оставаясь при этом на вид применённой.
    """
    config = tmp_path / "docpipe.yaml"
    from_config = tmp_path / "from-config/doc-tree.json"
    from_flag = tmp_path / "from-flag/doc-tree.json"
    config.write_text(
        yaml.safe_dump({"rules": "rules/dotnet.yaml", "out": str(from_config)}), encoding="utf-8"
    )

    assert (
        runner.invoke(
            app,
            ["scan", "--root", str(sample_solution), "--config", str(config), "--no-cache"],
        ).exit_code
        == 0
    )
    assert from_config.is_file()

    flags = ["--config", str(config), "--out", str(from_flag), "--no-cache"]
    runner.invoke(app, ["scan", "--root", str(sample_solution), *flags])
    assert from_flag.is_file()


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


def test_golden_nodes_have_impl_hash() -> None:
    """Все шесть узлов эталона несут `impl_hash` (M02).

    Пустое значение прошло бы схему и молча лишило шаг 2 сигнала «реализация
    изменилась при том же контракте».
    """
    manifest = Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))

    assert len(manifest.nodes) == 6
    assert all(node.impl_hash.startswith("sha256:") for node in manifest.nodes)
    assert len({node.impl_hash for node in manifest.nodes}) == 6
