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
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe import cli
from docpipe.config import load_config
from docpipe.emit import DEFAULT_EXCLUDE, exclude_globs
from docpipe.graph import build, logical_hash, project, read_index, read_meta
from docpipe.graph import engine as engine_module
from docpipe.graph.engine import (
    EXPECTED_VERSION,
    PROJECT_CONFIG,
    SKIPPED_DIRECTORIES,
    Engine,
    EngineError,
    engine_environment,
)
from docpipe.hashing import content_hash

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


@engine_required
def test_checksum_mismatch_warns_but_does_not_refuse() -> None:
    """Расхождение чек-суммы — предупреждение, а не отказ.

    Чек-сумма различает **сборки**, а не версии: бинарь не воспроизводим
    бит в бит, и на закрытом контуре стои́т своя сборка того же 0.6.0 — это
    измерено. Отказ по ней останавливал бы работу там, где всё в порядке,
    а инструмент, который нельзя запустить на целевой машине, там и не нужен.

    Проверяется на настоящем бинаре с заведомо чужой закреплённой суммой:
    так же, как это выглядит на контуре.
    """
    engine = Engine(
        binary=ENGINE_PATH,
        cache_dir=Path("/tmp/нет-такого-кэша"),
        expected_sha256="sha256:00",
    )
    identity = engine.check()

    assert identity.version == EXPECTED_VERSION
    assert not identity.matches_pin
    assert identity.warning is not None
    assert "ожидалась" in identity.warning
    assert "получена" in identity.warning
    # Фактическая, а не закреплённая: паспорт индекса берёт её отсюда.
    assert identity.checksum != identity.expected


def test_wrong_version_still_refuses(tmp_path: Path) -> None:
    """Версия осталась отказом, и это то, ради чего снят отказ по сумме.

    Сняв блокирующей и её, мы получили бы движок, не проверяемый ничем:
    правило «не запускать то, что нашлось» держится теперь здесь одно.
    """
    fake = tmp_path / "движок"
    fake.write_text("#!/bin/sh\necho 'codebase-memory-mcp 0.10.8'\n", encoding="utf-8")
    fake.chmod(0o755)
    wrong = Engine(binary=fake, cache_dir=tmp_path / "cache", expected_sha256="")

    with pytest.raises(EngineError) as error:
        wrong.check()
    assert "0.10.8" in str(error.value)
    assert EXPECTED_VERSION in str(error.value)


# ──────────────────────────────────────────────────────────────────────────────
# Контракт: что движок отвечает на наших фикстурах
# ──────────────────────────────────────────────────────────────────────────────


@engine_required
def test_version_and_checksum_match_the_pin(engine: Engine) -> None:
    """Версия, на которой ведётся разработка, — та, что прошла в контур.

    Чек-сумма здесь тоже обязана сойтись: это машина разработки, и на ней
    закреплённое значение описывает реальный файл. Расходится оно законно
    только на другой сборке — там и предупреждение.
    """
    identity = engine.check()
    assert identity.version == EXPECTED_VERSION
    assert identity.matches_pin
    assert identity.warning is None


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


# ──────────────────────────────────────────────────────────────────────────────
# Маршруты фронта у разбора: перекрёстная проверка и отсев мусора
# ──────────────────────────────────────────────────────────────────────────────


def test_route_name_is_split_into_method_and_path() -> None:
    """Имя узла маршрута собрано с префиксом: `__route__ANY__/api/x`."""
    from docpipe.graph.engine import _split_route

    assert _split_route("__route__GET__/api/items") == ("GET", "/api/items")
    assert _split_route("__route__ANY__/api/content/:app/query") == (
        "ANY",
        "/api/content/:app/query",
    )
    assert _split_route("api/items") == ("", "api/items")


def test_regex_literals_are_not_routes() -> None:
    """Половина найденных «маршрутов» — литералы регулярок из минифицированного
    JS: `/&/g`, `/---/g`. По форме от пути неотличимы, маршрутом не являются,
    и отсев их считается — молча выброшенное ребро через месяц неотличимо
    от потерянного.
    """
    from docpipe.graph.engine import _REGEX_LITERAL

    assert _REGEX_LITERAL.match("/&/g")
    assert _REGEX_LITERAL.match("/---/g")
    assert _REGEX_LITERAL.match("/%20/g")
    assert not _REGEX_LITERAL.match("/api/items")
    assert not _REGEX_LITERAL.match("/api/content/:app/query")


# ──────────────────────────────────────────────────────────────────────────────
# Python: узлы и рёбра приходят тем же мостом
# ──────────────────────────────────────────────────────────────────────────────


@engine_required
def test_python_code_needs_no_parser_of_our_own(engine: Engine, tmp_path: Path) -> None:
    """Узлы и рёбра `calls` внутри Python приходят из стороннего разбора через
    тот же мост: отдельного разбора Python в `docpipe` не появляется (G16 п. 2).

    Проверяется на настоящем питоновском коде, а не на фикстуре из одной
    строки: важно, что рёбра действительно строятся, а не что запрос
    отработал без ошибки.
    """
    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "handlers.py").write_text(
        "def compute(payload):\n    return payload\n\n\n"
        "class ForecastHandler:\n    def run(self, payload):\n        return compute(payload)\n",
        encoding="utf-8",
    )
    result = build(engine, tmp_path)
    languages = {node.lang for node in result.index.nodes}
    assert "python" in languages
    assert any(edge.kind == "calls" for edge in result.index.edges)


@engine_required
def test_registry_written_in_python_becomes_a_root(engine: Engine, tmp_path: Path) -> None:
    """Реестр в виде исполняемого кода даёт точки входа, связанные с классами
    (G16 п. 3). Разбирается он статически — модуль не импортируется.
    """
    from docpipe.arch import ArchRegistry, run_adapter
    from docpipe.graph.entrypoints import from_registry, link

    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "handlers.py").write_text(
        "class ForecastHandler:\n    def run(self):\n        return 1\n", encoding="utf-8"
    )
    (tmp_path / "srv" / "registry.py").write_text(
        "from srv.handlers import ForecastHandler\n\nSERVICES = {'forecast': ForecastHandler}\n",
        encoding="utf-8",
    )

    produced = run_adapter(
        "python_code",
        {"path": "srv/registry.py", "variable": "SERVICES", "entry_kind": "service"},
        tmp_path,
        lambda value: Path(value),
    )
    registry = ArchRegistry(version="1", records=tuple(produced.records))
    result = build(engine, tmp_path)
    entries = from_registry(registry)
    edges, report = link(entries, result.index.nodes, None)

    assert [node.attributes["entry_kind"] for node in entries] == ["service"]
    assert edges, "точка входа из реестра не связалась с классом"
    assert report.linked


@engine_required
def test_engine_skips_whole_directories_without_saying_so(tmp_path: Path) -> None:
    """Контрактный тест: список пропускаемых каталогов — измеренный факт, а не догадка.

    Если следующая версия разборщика перестанет их пропускать (или начнёт
    пропускать другие), это обязано упасть здесь, а не проявиться пропавшим
    интеграционным кодом на боевом репозитории.
    """
    for directory in ("tools", "scripts", "build", "vendor", "bin", "src", "clients"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "sample.py").write_text(
            f"class Sample_{directory}:\n    def run(self):\n        return 1\n",
            encoding="utf-8",
        )

    engine = Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "cache")
    run = engine.index(tmp_path)
    rows = engine.query(run.project, "MATCH (n:File) RETURN n.qualified_name, n.file_path")
    indexed = {row[-1].split("/")[0] for row in rows if row and row[-1]}

    assert indexed == {"src", "clients"}, indexed
    for directory in SKIPPED_DIRECTORIES:
        assert directory not in indexed


# ──────────────────────────────────────────────────────────────────────────────
# Отсев `graph build` — тот же, что у `scan` (S08 плана настройки, строка 5)
# ──────────────────────────────────────────────────────────────────────────────

# Встроенный отсев `scan` на каждом его шаблоне: что движок пропускает сам
# и что нет. `bin/` лежит в обоих списках: его пропуск зависит от режима.
_SCAN_DROPS = {
    "src/App/Foo.g.cs": "FooGenerated",
    "src/App/obj/InObj.cs": "InObj",
    "src/App/bin/InBin.cs": "InBin",
    "web/node_modules/lib/InNodeModules.ts": "InNodeModules",
    "web/dist/InDist.ts": "InDist",
}


def _repo_with_scan_drops(root: Path) -> Path:
    (root / "src" / "App").mkdir(parents=True)
    (root / "src" / "App" / "Foo.cs").write_text(
        "namespace App { public class Foo { public int Run() { return 1; } } }\n",
        encoding="utf-8",
    )
    for relative, name in _SCAN_DROPS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"namespace App {{ public class {name} {{ public int Run() {{ return 1; }} }} }}\n"
            if relative.endswith(".cs")
            else f"export class {name} {{ run() {{ return 1; }} }}\n"
        )
        path.write_text(body, encoding="utf-8")
    return root


def _indexed_files(engine: Engine, root: Path) -> set[str]:
    run = engine.index(root)
    rows = engine.query(run.project, "MATCH (n:File) RETURN n.qualified_name, n.file_path")
    return {row[-1] for row in rows if row and row[-1]}


@engine_required
def test_engine_parses_what_scan_drops(tmp_path: Path) -> None:
    """Контрактный тест: встроенный отсев `scan` движок делает не весь.

    `obj/`, `node_modules/`, `dist/` он пропускает сам в любом режиме, а
    `*.g.cs` разбирает всегда, `bin/` — в режиме `full`. Пока `graph build`
    отдавал мосту один пользовательский `exclude`, сгенерированные классы
    попадали в граф, хотя в манифесте их нет. Если следующая версия начнёт
    пропускать их сама, это упадёт здесь — и отсев в мосте станет избыточным,
    но не вредным.
    """
    repo = _repo_with_scan_drops(tmp_path / "repo")

    fast = _indexed_files(Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "fast"), repo)
    full = _indexed_files(
        Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "full", mode="full"), repo
    )

    assert fast == {"src/App/Foo.cs", "src/App/Foo.g.cs"}, fast
    assert full == {"src/App/Foo.cs", "src/App/Foo.g.cs", "src/App/bin/InBin.cs"}, full


def test_graph_build_hands_the_bridge_the_scan_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без движка: предикат, который `graph build` отдаёт мосту, — `exclude_globs`.

    Встроенный список складывается с пользовательским, как у `scan`, а не
    замещается им: иначе человек, дописавший `exclude: ["vendor/**"]`,
    получал бы в графе `*.g.cs`, которых нет в манифесте.
    """
    seen: dict[str, Callable[[str], bool]] = {}

    def fake_build(
        _engine: Engine, _root: Path, *, is_excluded: Callable[[str], bool], **_: object
    ):
        seen["excluded"] = is_excluded
        raise EngineError("стоп: дальше движок не нужен")

    monkeypatch.setattr(cli, "build_graph", fake_build)
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        'exclude: ["vendor/**"]\ngraph:\n  engine_path: "/нет/движка"\n', encoding="utf-8"
    )

    result = CliRunner().invoke(
        cli.app, ["graph", "build", "--root", str(tmp_path), "--config", str(config)]
    )

    assert result.exit_code == 2, result.output
    excluded = seen["excluded"]
    assert all(excluded(path) for path in _SCAN_DROPS), _SCAN_DROPS
    assert excluded("vendor/Lib.cs")
    assert not excluded("src/App/Foo.cs")
    assert set(exclude_globs(load_config(config))) >= {*DEFAULT_EXCLUDE, "vendor/**"}


@engine_required
def test_generated_file_does_not_reach_the_index(tmp_path: Path) -> None:
    """Сквозной прогон: `Foo.g.cs` в индекс не попадает, и отсев виден в отчёте."""
    repo = _repo_with_scan_drops(tmp_path / "repo")
    out = tmp_path / "graph.db"
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        "graph:\n"
        f'  engine_path: "{ENGINE_PATH}"\n'
        f'  out: "{out}"\n'
        f'  cache_dir: "{tmp_path / "engine-cache"}"\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli.app, ["graph", "build", "--root", str(repo), "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    files = {node.file for node in read_index(out).nodes}
    assert "src/App/Foo.cs" in files
    assert not any(file.endswith(".g.cs") for file in files), files
    assert read_meta(out).report.get("узлов разбора отсеяно: отсев файлового множества", 0) > 0


# ──────────────────────────────────────────────────────────────────────────────
# Окружение движка: что пользователь не должен менять в разборе
# ──────────────────────────────────────────────────────────────────────────────


def test_engine_environment_drops_what_changes_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переменные движка и путь к его пользовательскому конфигу — скрытый вход.

    `CBM_SEMANTIC_ENABLED` в профиле одного человека включила бы у него
    семантические рёбра, а `XDG_CONFIG_HOME` привёл бы движок к его личному
    сопоставлению расширений. Индекс бы различался, а по индексу этого не видно.
    """
    monkeypatch.setenv("CBM_SEMANTIC_ENABLED", "1")
    monkeypatch.setenv("CBM_SEMANTIC_THRESHOLD", "0.1")
    monkeypatch.setenv("CBM_CACHE_DIR", "/home/someone/.cache/codebase-memory-mcp")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/someone/.config")
    cache = tmp_path / "cache"

    env = engine_environment(cache)

    assert sorted(key for key in env if key.startswith("CBM_")) == ["CBM_CACHE_DIR"]
    assert env["CBM_CACHE_DIR"] == str(cache)
    assert Path(env["XDG_CONFIG_HOME"]).is_relative_to(cache)
    assert not Path(env["XDG_CONFIG_HOME"]).exists()
    # Остальное окружение проходит как есть: PATH нужен движку для git.
    assert env["PATH"] == os.environ["PATH"]


def _repo_with_foreign_extension(root: Path) -> Path:
    """Репозиторий с файлом, который движок разбирает только по чужому конфигу."""
    (root / "src").mkdir(parents=True)
    (root / "src" / "hidden.foo").write_text(
        "class Hidden:\n    def run(self):\n        return 1\n", encoding="utf-8"
    )
    (root / "src" / "seen.py").write_text("class Seen:\n    pass\n", encoding="utf-8")
    return root


_FOO_IS_PYTHON = '{"extra_extensions": {".foo": "python"}}\n'


def _classes(engine: Engine, project: str) -> set[str]:
    return {row[0] for row in engine.query(project, "MATCH (n:Class) RETURN n.name")}


@engine_required
def test_user_engine_config_does_not_reach_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Контрактный тест: движок читает пользовательский конфиг, мост — глушит.

    Первая половина фиксирует поведение чужого кода: если следующая версия
    перестанет читать `~/.config/codebase-memory-mcp/config.json`, это упадёт
    здесь, и глушение можно будет пересмотреть. Вторая — что мост его глушит.
    """
    repo = _repo_with_foreign_extension(tmp_path / "repo")
    user_config = tmp_path / "xdg"
    (user_config / "codebase-memory-mcp").mkdir(parents=True)
    (user_config / "codebase-memory-mcp" / "config.json").write_text(
        _FOO_IS_PYTHON, encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_config))

    # Без глушения: окружение пользователя как есть.
    with monkeypatch.context() as patched:
        patched.setattr(
            engine_module,
            "engine_environment",
            lambda cache: {**os.environ, "CBM_CACHE_DIR": str(cache)},
        )
        raw = Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "raw-cache")
        assert _classes(raw, raw.index(repo).project) == {"Seen", "Hidden"}

    bridged = Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "cache")
    assert _classes(bridged, bridged.index(repo).project) == {"Seen"}


@engine_required
def test_project_engine_config_is_kept_and_recorded(tmp_path: Path) -> None:
    """Конфиг движка в корне репозитория — часть репозитория, и он не глушится.

    Решать за автора репозитория нельзя, но и молча принять нельзя: разбор
    с ним идёт не по умолчанию. Поэтому предупреждение сейчас и сумма
    в паспорте — для того, кто читает индекс позже.
    """
    plain = _repo_with_foreign_extension(tmp_path / "plain")
    warnings: list[str] = []
    result = build(
        Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "c1"), plain, warn=warnings.append
    )
    assert result.meta.engine_project_config == ""
    assert not any(PROJECT_CONFIG in message for message in warnings)

    configured = _repo_with_foreign_extension(tmp_path / "configured")
    (configured / PROJECT_CONFIG).write_text(_FOO_IS_PYTHON, encoding="utf-8")
    engine = Engine(binary=ENGINE_PATH, cache_dir=tmp_path / "c2")
    result = build(engine, configured, warn=warnings.append)

    assert _classes(engine, result.run.project) == {"Seen", "Hidden"}
    assert result.meta.engine_project_config == content_hash(
        (configured / PROJECT_CONFIG).read_bytes()
    )
    assert any(PROJECT_CONFIG in message for message in warnings)
