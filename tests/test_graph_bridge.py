"""Контрактные тесты моста к движку разбора (G01 п. 5).

Эти тесты зовут настоящий бинарь и потому пропускаются там, где его нет.
Пропуск честный: без движка проверять нечего, а выдумывать его ответы —
значит проверять свои фантазии.

Смысл контрактных тестов один: **зафиксировать поведение чужого кода
на наших фикстурах**. Смена версии бинаря, меняющая эти ответы, роняет
тесты — это и есть детектор дрейфа недокументированной схемы (риск Р-5).
Сюда же идут самые злые фикстуры шага 1: как движок ведёт себя на ловушках,
которые уже стоили нам захода, фиксируется, а не предполагается.
"""

import os
from pathlib import Path

import pytest

from docpipe.graph import build, logical_hash, project
from docpipe.graph.engine import EXPECTED_VERSION, Engine, EngineError

ENGINE_PATH = Path(
    os.environ.get("DOCPIPE_ENGINE_PATH", "~/.local/bin/codebase-memory-mcp")
).expanduser()

engine_required = pytest.mark.skipif(
    not ENGINE_PATH.is_file(),
    reason=(
        f"движок разбора не найден: {ENGINE_PATH}. Путь задаётся переменной DOCPIPE_ENGINE_PATH"
    ),
)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "engine-cache")


# ──────────────────────────────────────────────────────────────────────────────
# Отказы до запуска
# ──────────────────────────────────────────────────────────────────────────────


def test_missing_binary_is_a_readable_refusal(tmp_path: Path) -> None:
    """Отказ движка — внятная ошибка с диагностикой, а не трейс (G01 п. 9)."""
    absent = Engine(binary=tmp_path / "нет-такого", cache_dir=tmp_path / "cache")
    with pytest.raises(EngineError, match="не найден"):
        absent.check()


def test_checksum_mismatch_refuses_before_launch(tmp_path: Path) -> None:
    """Чек-сумма проверяется ДО запуска, и расхождение — отказ, а не
    предупреждение (G01 п. 4).

    Запустить то, что нашлось, и разбираться потом — значит получить числа
    от другой версии движка и не иметь способа это заметить.
    """
    fake = tmp_path / "движок"
    fake.write_text("не тот бинарь", encoding="utf-8")
    wrong = Engine(binary=fake, cache_dir=tmp_path / "cache", expected_sha256="sha256:00")
    with pytest.raises(EngineError) as error:
        wrong.check()
    assert "ожидалась" in str(error.value)
    assert "получена" in str(error.value)


# ──────────────────────────────────────────────────────────────────────────────
# Контракт: что движок отвечает на наших фикстурах
# ──────────────────────────────────────────────────────────────────────────────


@engine_required
def test_version_and_checksum_match_the_pin(engine: Engine) -> None:
    """Версия, на которой ведётся разработка, — та, что прошла в контур."""
    assert engine.check() == EXPECTED_VERSION


@engine_required
def test_sample_solution_projection_is_fixed(engine: Engine) -> None:
    """Число узлов и рёбер на канонической фикстуре зафиксировано.

    Не ради самого числа: смена версии бинаря, меняющая ответ, обязана
    уронить тест, а не проехать незамеченной.
    """
    result = build(engine, Path("tests/fixtures/SampleSolution"))
    assert result.meta.counts["nodes"] == 24
    assert result.meta.counts["edges"] == 5
    kinds = {node.kind for node in result.index.nodes}
    assert kinds == {"type", "member"}


@engine_required
def test_conditional_module_is_invisible_to_both_parsers(engine: Engine) -> None:
    """`#if` внутри выражения теряет тип и у движка тоже.

    Наш разбор на этой конструкции теряет объявление целиком — это записанная
    ловушка шага 1. Здесь зафиксировано, что движок ведёт себя так же:
    значит, сопоставление манифеста с графом на ней не разойдётся,
    а число потерянного обязан считать отчёт о неполноте.
    """
    result = build(engine, Path("tests/fixtures/WildSolution"))
    files = {node.file for node in result.index.nodes}
    assert not any("ConditionalModule" in file for file in files)


@engine_required
def test_frontend_workspace_projects_nothing(engine: Engine) -> None:
    """На фикстуре фронта индекс пуст, и это правило, а не совпадение:
    рёбра TypeScript приходят из нашего разбора, а не отсюда."""
    result = build(engine, Path("tests/fixtures/WebWorkspace"))
    assert result.index.nodes == ()
    assert result.index.edges == ()
    assert result.meta.report["узлов фронта: источник не разбор, а наш web"] > 0


@engine_required
def test_two_runs_give_the_same_logical_hash(engine: Engine, tmp_path: Path) -> None:
    """Один вход — один выход (G01 п. 2 и п. 3).

    Прогоны идут с чистого кэша: инкрементальность движка — чужой
    непроверенный код, и прогон по несвежему кэшу против прогона с нуля —
    два разных входа, которые выглядят одним.
    """
    first = build(engine, Path("tests/fixtures/SampleSolution"))
    second = Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "другой-кэш")
    again = build(second, Path("tests/fixtures/SampleSolution"))
    assert logical_hash(first.index) == logical_hash(again.index)


@engine_required
def test_reading_covers_every_declared_edge_of_our_kinds(engine: Engine) -> None:
    """Полнота чтения проверяется числом, а не доверием.

    У 0.6.0 шаблон ребра без меток молча возвращает ноль строк, поэтому
    единственный способ узнать, что прочитано всё, — сверить прочитанное
    со счётчиком схемы. Разница законна: у ребра может быть конец вне наших
    меток. Незаконно — не знать этой разницы.
    """
    engine.check()
    run = engine.index(Path("tests/fixtures/SampleSolution"))
    graph = engine.read(run.project)
    for kind, declared in graph.declared_edges.items():
        read = graph.read_edges.get(kind, 0)
        assert read <= declared
    assert sum(graph.read_edges.values()) > 0


@engine_required
def test_exclusions_reach_the_engine_output(engine: Engine) -> None:
    """Исключения обхода применяются и к выходу движка.

    Сгенерированные дубли иначе зальют сопоставление коллизиями полных имён,
    которых в исходниках нет.
    """
    engine.check()
    run = engine.index(Path("tests/fixtures/SampleSolution"))
    everything = engine.read(run.project)
    narrowed = engine.read(run.project, is_excluded=lambda path: path.endswith(".cs"))
    assert len(narrowed.nodes) < len(everything.nodes)
    assert narrowed.filtered_nodes.get("отсев файлового множества", 0) > 0


@engine_required
def test_project_name_is_stripped_from_keys(engine: Engine) -> None:
    """Имя проекта движок выводит из абсолютного пути — в наши ключи
    оно не попадает. Иначе индекс зависел бы от того, где лежит чекаут."""
    result = build(engine, Path("tests/fixtures/SampleSolution"))
    for node in result.index.nodes:
        assert "home-" not in node.key
        assert node.key.startswith("src/") or node.key.startswith("tests/")


@engine_required
def test_engine_failure_on_missing_repository_is_readable(engine: Engine, tmp_path: Path) -> None:
    engine.check()
    with pytest.raises(EngineError):
        engine.index(tmp_path / "нет-такого-каталога")
        engine.read("несуществующий-проект")


@engine_required
def test_projection_is_pure(engine: Engine) -> None:
    """Проекция — чистая функция от прочитанного графа: одна и та же
    выборка даёт один и тот же индекс."""
    engine.check()
    run = engine.index(Path("tests/fixtures/SampleSolution"))
    graph = engine.read(run.project)
    first, _ = project(graph, "0.6.0")
    second, _ = project(graph, "0.6.0")
    assert logical_hash(first) == logical_hash(second)
