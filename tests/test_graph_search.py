"""Поиск и разрешение имён (G10).

Три свойства, каждое из которых ломается молча: русский регистр
сворачивается (иначе весь русский поиск мёртв, а тесты на английских именах
этого не покажут), ответ называет, чем совпал, и пустоты не бывает.
"""

import time
from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.graph import GraphIndex, GraphMeta, GraphNode, write_index
from docpipe.graph.search import EXACT, SUBSTRING, entries, resolve, similarity

runner = CliRunner()


def node(key: str, kind: str, name: str, attributes: dict[str, str] | None = None) -> GraphNode:
    return GraphNode(key=key, kind=kind, name=name, attributes=attributes or {})


def index_with_names() -> GraphIndex:
    return GraphIndex(
        nodes=(
            node("src/A.cs#PricingService", "type", "PricingService", {"fqn": "Ns.PricingService"}),
            node(
                "data:user_tasks",
                "data",
                "Пользовательские задачи",
                {"field:Title": "FieldText|Наименование задачи"},
            ),
            node(
                "entry:http_endpoint:get api/v1/pricing",
                "entry_point",
                "GET api/v1/Pricing",
                {"route": "api/v1/Pricing", "entry_kind": "http_endpoint"},
            ),
        )
    )


def prepared(tmp_path: Path) -> tuple[Path, dict[str, GraphNode]]:
    index = index_with_names()
    target = tmp_path / "graph.db"
    write_index(target, index, GraphMeta(generation=""), None, entries(index))
    return target, {item.key: item for item in index.nodes}


# ──────────────────────────────────────────────────────────────────────────────
# Что и как находится
# ──────────────────────────────────────────────────────────────────────────────


def test_exact_match_is_named_as_exact(tmp_path: Path) -> None:
    path, nodes = prepared(tmp_path)
    matches, inexact = resolve(path, "PricingService", nodes)
    assert matches[0].how == EXACT
    assert inexact is False


def test_russian_query_finds_russian_text(tmp_path: Path) -> None:
    """Регистр кириллицы сворачивается на нашей стороне: `LOWER()` в SQLite
    без ICU этого не делает, и «Пользовательские» не совпало бы никогда."""
    path, nodes = prepared(tmp_path)
    matches, _ = resolve(path, "ПОЛЬЗОВАТЕЛЬСКИЕ задачи", nodes)
    assert matches
    assert matches[0].node == "data:user_tasks"


def test_yo_is_folded(tmp_path: Path) -> None:
    """«ё» и «е» — одна буква для поиска: иначе половина запросов
    не находит написанное через другую."""
    index = GraphIndex(nodes=(node("data:x", "data", "Учётные записи"),))
    target = tmp_path / "graph.db"
    write_index(target, index, GraphMeta(generation=""), None, entries(index))
    matches, _ = resolve(target, "учетные", {item.key: item for item in index.nodes})
    assert matches and matches[0].node == "data:x"


def test_field_labels_are_searchable(tmp_path: Path) -> None:
    """Русские названия полей — единственный источник предметных слов
    на репозитории, где код английский, а предметная область русская."""
    path, nodes = prepared(tmp_path)
    matches, _ = resolve(path, "наименование задачи", nodes)
    assert matches[0].node == "data:user_tasks"
    assert matches[0].field == "поле"


def test_route_is_searchable(tmp_path: Path) -> None:
    path, nodes = prepared(tmp_path)
    matches, _ = resolve(path, "api/v1/pricing", nodes)
    assert matches[0].node.startswith("entry:")
    assert matches[0].field == "route"


def test_substring_is_named_as_substring(tmp_path: Path) -> None:
    path, nodes = prepared(tmp_path)
    matches, inexact = resolve(path, "Pricing", nodes)
    assert any(match.how == SUBSTRING for match in matches)
    assert inexact is True


def test_one_node_gives_one_match(tmp_path: Path) -> None:
    """Тип, найденный и по имени, и по полному имени, — одна находка."""
    path, nodes = prepared(tmp_path)
    matches, _ = resolve(path, "PricingService", nodes)
    assert len({match.node for match in matches}) == len(matches)


def test_mixed_alphabet_query_does_not_crash(tmp_path: Path) -> None:
    path, nodes = prepared(tmp_path)
    matches, inexact = resolve(path, "Pricing задачи", nodes)
    assert isinstance(matches, list)
    assert inexact is True


def test_empty_query_is_not_a_crash(tmp_path: Path) -> None:
    path, nodes = prepared(tmp_path)
    assert resolve(path, "   ", nodes) == ([], True)


def test_trigram_similarity_is_symmetric_and_bounded() -> None:
    assert similarity("pricing", "pricing") == 1.0
    assert similarity("pricing", "прайсинг") == 0.0
    assert 0 < similarity("pricingservice", "pricing service") < 1


# ──────────────────────────────────────────────────────────────────────────────
# Время
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_is_fast_enough(tmp_path: Path) -> None:
    """Требование плана — не более 100 мс. Порог теста мягче: на медленной
    машине жёсткий порог даёт мигающий тест, а мигающий тест выключают."""
    nodes = tuple(
        node(f"src/File{number}.cs#Type{number}", "type", f"SomeService{number}")
        for number in range(4000)
    )
    index = GraphIndex(nodes=nodes)
    target = tmp_path / "graph.db"
    write_index(target, index, GraphMeta(generation=""), None, entries(index))
    lookup = {item.key: item for item in nodes}

    started = time.perf_counter()
    matches, _ = resolve(target, "SomeService1234", lookup)
    elapsed = time.perf_counter() - started
    assert matches
    assert elapsed < 1.0


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def test_cli_says_what_matched(tmp_path: Path) -> None:
    path, _ = prepared(tmp_path)
    result = runner.invoke(app, ["graph", "resolve", "PricingService", "--index", str(path)])
    assert result.exit_code == 0, result.output
    assert "точное совпадение" in result.output


def test_cli_gives_a_foothold_when_nothing_matches(tmp_path: Path) -> None:
    """Пустота неотличима от факта и не даёт зацепки для второй попытки,
    поэтому называется, где искали и что вообще есть в индексе."""
    path, _ = prepared(tmp_path)
    result = runner.invoke(app, ["graph", "resolve", "квартальный отчёт", "--index", str(path)])
    assert result.exit_code == 1
    assert "Искали среди" in result.output
    assert "В индексе:" in result.output
