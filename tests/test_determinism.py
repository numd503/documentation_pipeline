"""Детерминизм как проверяемое свойство (T17).

Требование из `purpose.md` — «шаг должен быть детерминированным» — здесь
превращается в набор тестов. Проверяется не «похожесть» результатов, а
побайтовое равенство: манифест не содержит ни одного недетерминированного
поля, поэтому сравнение может быть буквальным.

Все тесты работают на временной копии фикстуры; исходная не изменяется.
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from docpipe import discovery
from docpipe.emit import scan, write_manifest
from docpipe.model import Manifest, ParserVersions

CACHE = "cache"


def _copy(source: Path, tmp_path: Path, name: str = "Solution") -> Path:
    destination = tmp_path / name
    shutil.copytree(source, destination)
    return destination


def _bytes(root: Path, tmp_path: Path, name: str, **kwargs: object) -> bytes:
    """Манифест как байты — так же, как его увидит downstream."""
    out = tmp_path / f"{name}.json"
    manifest, _ = scan(root, **kwargs)  # type: ignore[arg-type]
    write_manifest(manifest, out)
    return out.read_bytes()


# --------------------------------------------------------------------------------------
# 1. Двойной прогон
# --------------------------------------------------------------------------------------


def test_two_runs_are_byte_identical(sample_solution: Path, tmp_path: Path) -> None:
    assert _bytes(sample_solution, tmp_path, "first") == _bytes(sample_solution, tmp_path, "second")


# --------------------------------------------------------------------------------------
# 2. Независимость от порядка обхода ФС
# --------------------------------------------------------------------------------------


def test_reversed_filesystem_order_changes_nothing(
    sample_solution: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок, в котором ФС отдаёт имена, не должен влиять ни на что.

    Он и правда различается между файловыми системами и машинами, поэтому
    любое место, где порядок обхода просочился бы в вывод, ломало бы
    воспроизводимость незаметно — на другой машине.
    """
    expected = _bytes(sample_solution, tmp_path, "straight")

    real_walk = os.walk

    def reversed_walk(
        *args: object, **kwargs: object
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        for dirpath, dirnames, filenames in real_walk(*args, **kwargs):  # type: ignore[arg-type]
            dirnames.reverse()
            yield dirpath, dirnames, list(reversed(filenames))

    monkeypatch.setattr(discovery.os, "walk", reversed_walk)
    assert _bytes(sample_solution, tmp_path, "reversed") == expected


# --------------------------------------------------------------------------------------
# 3. Инвариант инкремента: full == incremental
# --------------------------------------------------------------------------------------


def test_incremental_equals_full(sample_solution: Path, tmp_path: Path) -> None:
    """Кэш — оптимизация, а не другой режим работы.

    Проверяется цепочкой: холодный прогон, тёплый прогон, изменение файла
    с тёплым кэшем и то же изменение с нуля. Совпасть обязаны обе пары.
    """
    root = _copy(sample_solution, tmp_path)
    cache_dir = tmp_path / CACHE

    cold = _bytes(root, tmp_path, "cold", cache_dir=cache_dir)
    warm = _bytes(root, tmp_path, "warm", cache_dir=cache_dir)
    assert cold == warm

    target = root / "src/Sample.Pricing.Api/Services/PricingService.cs"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.rstrip()[:-1] + "\n    public decimal Markup() => 1.05m;\n}\n", encoding="utf-8"
    )

    incremental = _bytes(root, tmp_path, "incremental", cache_dir=cache_dir)
    full = _bytes(root, tmp_path, "full")

    assert incremental == full
    assert incremental != cold  # изменение действительно дошло до манифеста


def test_deleted_file_disappears_from_the_manifest(sample_solution: Path, tmp_path: Path) -> None:
    """Кэш не должен воскрешать удалённый файл: за это отвечает `prune`."""
    root = _copy(sample_solution, tmp_path)
    cache_dir = tmp_path / CACHE

    before = _bytes(root, tmp_path, "before", cache_dir=cache_dir)
    (root / "src/Sample.Pricing.Api/Workflows/ValuationWorkflow.cs").unlink()

    incremental = _bytes(root, tmp_path, "after", cache_dir=cache_dir)
    full = _bytes(root, tmp_path, "after-full")

    assert incremental == full
    # Проверяется исчезновение УЗЛА, а не строки: `Program.cs` никуда не делся
    # и по-прежнему содержит `services.AddTransient<ValuationWorkflow>()`.
    # Регистрация на тип, которого больше нет, — законный факт файла
    # и отдельная находка связывания (G04), а не остаток кэша.
    assert b'"title": "ValuationWorkflow"' not in incremental
    assert b'"title": "ValuationWorkflow"' in before


# --------------------------------------------------------------------------------------
# 4. Инвалидация по версии грамматики
# --------------------------------------------------------------------------------------


def test_new_parser_version_invalidates_cache_without_changing_output(
    sample_solution: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Апгрейд грамматики обязан очистить кэш, а не подсунуть старый разбор.

    Содержимое файлов не менялось, значит хэши совпадают, и без сверки версий
    кэш вернул бы разбор, сделанный прежней грамматикой. Сам вывод при этом
    меняется ровно в одном месте — в поле `parser`, и это видно в диффе.
    """
    root = _copy(sample_solution, tmp_path)
    cache_dir = tmp_path / CACHE

    original, _ = scan(root, cache_dir=cache_dir)

    monkeypatch.setattr(
        "docpipe.emit.parser_versions",
        lambda: ParserVersions(tree_sitter="99.0.0", grammar_c_sharp="99.0.0"),
    )
    upgraded, _ = scan(root, cache_dir=cache_dir)

    assert upgraded.parser.tree_sitter == "99.0.0"
    assert upgraded.modules == original.modules
    assert upgraded.nodes == original.nodes


# --------------------------------------------------------------------------------------
# 5. Перенос репозитория
# --------------------------------------------------------------------------------------


def test_manifest_survives_relocation(sample_solution: Path, tmp_path: Path) -> None:
    """Копия репозитория в другом каталоге обязана дать тот же манифест.

    Прямое следствие правила «пути только репо-относительные». Если бы
    в манифест просочился абсолютный путь, документация ломалась бы при
    любом различии в расположении рабочих копий у разработчика и в CI.
    """
    here = _copy(sample_solution, tmp_path, "here")
    elsewhere = _copy(sample_solution, tmp_path, "some/deeper/there")

    assert _bytes(here, tmp_path, "here") == _bytes(elsewhere, tmp_path, "there")


def test_cache_from_another_location_is_reusable(sample_solution: Path, tmp_path: Path) -> None:
    """Кэш ключуется путями внутри репозитория, а не снаружи.

    Значит, кэш, наполненный из одной рабочей копии, годится для другой:
    попадание определяется содержимым файла и его репо-относительным путём.
    """
    cache_dir = tmp_path / CACHE
    here = _copy(sample_solution, tmp_path, "here")
    elsewhere = _copy(sample_solution, tmp_path, "there")

    first = _bytes(here, tmp_path, "here", cache_dir=cache_dir)
    second = _bytes(elsewhere, tmp_path, "there", cache_dir=cache_dir)

    assert first == second


# --------------------------------------------------------------------------------------
# Свойства манифеста
# --------------------------------------------------------------------------------------


def test_every_list_in_the_manifest_is_sorted(sample_solution: Path) -> None:
    """Явная сортировка — не стиль, а условие воспроизводимости."""
    manifest, _ = scan(sample_solution)

    assert [m.id for m in manifest.modules] == sorted(m.id for m in manifest.modules)
    assert [n.id for n in manifest.nodes] == sorted(n.id for n in manifest.nodes)

    for module in manifest.modules:
        assert module.target_frameworks == sorted(module.target_frameworks)
        assert module.project_references == sorted(module.project_references)
        assert module.package_references == sorted(module.package_references)

    for node in manifest.nodes:
        assert node.matched_rules == sorted(node.matched_rules)
        assert node.symbol is not None
        assert node.symbol.modifiers == sorted(node.symbol.modifiers)
        assert node.symbol.base_type_closure == sorted(node.symbol.base_type_closure)
        assert [s.path for s in node.symbol.sources] == sorted(s.path for s in node.symbol.sources)


def test_manifest_roundtrips_through_json(sample_solution: Path, tmp_path: Path) -> None:
    """Запись и чтение обязаны быть обратимы: манифест — контракт с шагом 2."""
    manifest, _ = scan(sample_solution)
    out = tmp_path / "doc-tree.json"
    write_manifest(manifest, out)

    assert Manifest.model_validate_json(out.read_text(encoding="utf-8")) == manifest
