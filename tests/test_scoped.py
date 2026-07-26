"""Скоуп-режим: частичное обновление, не ломающее корректность (T18)."""

import shutil
from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.discovery import normalize_scope
from docpipe.emit import scan
from docpipe.merge import merge_manifests, node_in_scope
from docpipe.model import Manifest

runner = CliRunner()

PRICING = "src/Sample.Pricing.Api"
COMMON = "src/Sample.Common"


def _prepare(sample_solution: Path, tmp_path: Path) -> tuple[Path, Path, Manifest]:
    """Копия решения, тёплый кэш и полный манифест — общая подготовка."""
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root)
    cache_dir = tmp_path / "cache"

    full, _ = scan(root, cache_dir=cache_dir)
    return root, cache_dir, full


def _scoped(root: Path, cache_dir: Path, previous: Manifest, *scope: str) -> Manifest:
    manifest, _ = scan(root, cache_dir=cache_dir, scope=list(scope), previous=previous)
    return manifest


# --------------------------------------------------------------------------------------
# Главный инвариант
# --------------------------------------------------------------------------------------


def test_scoped_run_equals_full_run(sample_solution: Path, tmp_path: Path) -> None:
    """То, ради чего построена вся конструкция с индексом символов.

    Скоуп-прогон парсит только один модуль, но обязан дать те же узлы,
    что и полный прогон. Если этот тест падает — значит, кэш не используется
    для резолва, и обходить это нельзя.
    """
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    scoped = _scoped(root, cache_dir, full, PRICING)

    assert scoped.nodes == full.nodes
    assert scoped.modules == full.modules


def test_scoped_run_by_the_other_module_also_equals_full(
    sample_solution: Path, tmp_path: Path
) -> None:
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    assert _scoped(root, cache_dir, full, COMMON).nodes == full.nodes


def test_inheritance_survives_across_unparsed_module(sample_solution: Path, tmp_path: Path) -> None:
    """`PricingController` наследуется от `BaseApiController` из `Sample.Common`.

    При скоупе по `Sample.Pricing.Api` этот модуль не парсится вовсе — его
    символы приходят из кэша. Классификация обязана сохраниться: правило
    `controller.aspnet` смотрит на `ControllerBase`, до которого два шага
    наследования через чужой модуль.
    """
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    scoped = _scoped(root, cache_dir, full, PRICING)

    controller = next(node for node in scoped.nodes if node.title == "PricingController")
    assert controller.kind == "controller"
    assert controller.symbol is not None
    assert controller.symbol.base_type_closure == [
        "ControllerBase",
        "Sample.Common.Web.BaseApiController",
    ]


def test_several_scopes(sample_solution: Path, tmp_path: Path) -> None:
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    assert _scoped(root, cache_dir, full, PRICING, COMMON).nodes == full.nodes


# --------------------------------------------------------------------------------------
# Что скоуп-режим видит и чего не видит
# --------------------------------------------------------------------------------------


def test_change_inside_scope_is_picked_up(sample_solution: Path, tmp_path: Path) -> None:
    root, cache_dir, full = _prepare(sample_solution, tmp_path)

    target = root / PRICING / "Services/PricingService.cs"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.rstrip()[:-1] + "\n    public decimal Markup() => 1.05m;\n}\n", encoding="utf-8"
    )

    scoped = _scoped(root, cache_dir, full, PRICING)
    before = next(n for n in full.nodes if n.title == "PricingService")
    after = next(n for n in scoped.nodes if n.title == "PricingService")

    assert after.signature_hash != before.signature_hash


def test_change_outside_scope_is_not_seen(sample_solution: Path, tmp_path: Path) -> None:
    """Честная граница режима, а не дефект.

    Изменение вне скоупа скоуп-прогон не увидит: файл не читался, его разбор
    взят из кэша. Ровно поэтому манифест помечается `partial`, а источником
    истины в CI остаётся полный прогон.
    """
    root, cache_dir, full = _prepare(sample_solution, tmp_path)

    target = root / COMMON / "Web/BaseApiController.cs"
    text = target.read_text(encoding="utf-8")
    target.write_text(text.rstrip()[:-1] + "\n    public void Added() { }\n}\n", encoding="utf-8")

    scoped = _scoped(root, cache_dir, full, PRICING)
    full_again, _ = scan(root)

    unchanged = next(n for n in scoped.nodes if n.title == "BaseApiController")
    changed = next(n for n in full_again.nodes if n.title == "BaseApiController")

    assert unchanged.signature_hash != changed.signature_hash
    assert unchanged == next(n for n in full.nodes if n.title == "BaseApiController")


def test_removed_type_disappears_from_merged_manifest(
    sample_solution: Path, tmp_path: Path
) -> None:
    """Старый узел в скоупе отбрасывается до добавления новых.

    Иначе удалённый тип остался бы в дереве навсегда: скоуп-прогон его
    не породит, а из предыдущего манифеста никто не уберёт.
    """
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    (root / PRICING / "Workflows/ValuationWorkflow.cs").unlink()

    scoped = _scoped(root, cache_dir, full, PRICING)

    assert "ValuationWorkflow" in {node.title for node in full.nodes}
    assert "ValuationWorkflow" not in {node.title for node in scoped.nodes}


def test_partial_is_recorded(sample_solution: Path, tmp_path: Path) -> None:
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    scoped = _scoped(root, cache_dir, full, PRICING)

    assert full.partial is None
    assert scoped.partial is not None
    assert scoped.partial.scope == [PRICING]
    assert scoped.partial.outside_from_cache is True


def test_stats_report_what_came_from_cache(sample_solution: Path, tmp_path: Path) -> None:
    root, cache_dir, full = _prepare(sample_solution, tmp_path)
    _, meta = scan(root, cache_dir=cache_dir, scope=[PRICING], previous=full)

    assert meta.stats["restored_from_cache"] == 2  # два файла Sample.Common
    assert meta.stats["missing_from_cache"] == 0


def test_cold_cache_breaks_classification_outside_scope(
    sample_solution: Path, tmp_path: Path
) -> None:
    """Без кэша символов вне скоупа взять неоткуда, и последствия жёстче ожидаемых.

    `PricingController` наследуется от `BaseApiController`, а правило смотрит
    на `ControllerBase` — на два шага дальше. Без `Sample.Common` в индексе
    цепочка обрывается на первом звене, ни одно правило не совпадает,
    и контроллер **исчезает из документации** вовсе. Не «теряет часть данных»,
    а перестаёт существовать.

    Отсюда требование: скоуп-режим работает только поверх тёплого кэша,
    и когда файлов в нём нет, команда обязана предупреждать.
    """
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root)
    full, _ = scan(root)

    scoped, meta = scan(root, cache_dir=tmp_path / "empty", scope=[PRICING], previous=full)

    assert "PricingController" in {node.title for node in full.nodes}
    # Узел из предыдущего манифеста при слиянии выброшен как «в скоупе»,
    # а новый не породился — тип пропал.
    assert "PricingController" not in {node.title for node in scoped.nodes}
    assert meta.stats["restored_from_cache"] == 0


# --------------------------------------------------------------------------------------
# merge_manifests
# --------------------------------------------------------------------------------------


def test_node_in_scope_by_any_source(sample_solution: Path, tmp_path: Path) -> None:
    """`partial class` с половинами по разные стороны границы считается в скоупе.

    Иначе половина изменений потерялась бы: тип перестроен, но узел взят старый.
    """
    _, _, full = _prepare(sample_solution, tmp_path)
    service = next(node for node in full.nodes if node.title == "PricingService")

    assert service.symbol is not None
    assert len(service.symbol.sources) == 2
    assert node_in_scope(service, normalize_scope([PRICING]))


def test_merge_keeps_outside_nodes_and_replaces_inside(
    sample_solution: Path, tmp_path: Path
) -> None:
    _, _, full = _prepare(sample_solution, tmp_path)
    empty = Manifest(ruleset_version=full.ruleset_version, parser=full.parser)

    merged = merge_manifests(full, empty, [PRICING])
    titles = {node.title for node in merged.nodes}

    assert titles == {"BaseApiController"}  # всё из Pricing.Api выброшено
    assert merged.modules == full.modules  # модули сохранены


def test_merge_sorts_and_marks_partial(sample_solution: Path, tmp_path: Path) -> None:
    _, _, full = _prepare(sample_solution, tmp_path)
    merged = merge_manifests(full, full, [COMMON, PRICING])

    assert [n.id for n in merged.nodes] == sorted(n.id for n in merged.nodes)
    assert merged.partial is not None
    assert merged.partial.scope == sorted([COMMON, PRICING])


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_scope_without_manifest_is_rejected(sample_solution: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(sample_solution),
            "--out",
            str(tmp_path / "o.json"),
            "--scope",
            PRICING,
        ],
    )
    assert result.exit_code == 2
    assert "--scope требует --from-manifest" in result.output


def test_scoped_command_end_to_end(sample_solution: Path, tmp_path: Path) -> None:
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root)
    full_path = tmp_path / "full.json"
    scoped_path = tmp_path / "scoped.json"

    runner.invoke(app, ["scan", "--root", str(root), "--out", str(full_path)])
    result = runner.invoke(
        app,
        [
            "scan",
            "--root",
            str(root),
            "--out",
            str(scoped_path),
            "--scope",
            PRICING,
            "--from-manifest",
            str(full_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Частичный прогон" in result.output

    full = Manifest.model_validate_json(full_path.read_text(encoding="utf-8"))
    scoped = Manifest.model_validate_json(scoped_path.read_text(encoding="utf-8"))
    assert scoped.nodes == full.nodes
