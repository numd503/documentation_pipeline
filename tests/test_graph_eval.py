"""Оценочный набор (G18).

Набор меряет связку «сторонний разбор + наш слой» как одно целое:
пользователю всё равно, чьё ребро соврало. Тесты держат два свойства —
вопрос без ожидания не принимается, а прогон на фикстурах повторяем.
"""

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.graph import GraphEdge, GraphIndex, GraphMeta, GraphNode, compute, write_index
from docpipe.graph.evaluate import (
    check_requests,
    format_requests,
    format_score,
    load,
    merged_requests,
    run,
)
from docpipe.graph.search import entries

runner = CliRunner()
SETS = sorted(Path("evals").glob("*.yaml"))


def index() -> GraphIndex:
    return GraphIndex(
        nodes=(
            GraphNode(
                key="entry:job:ночная",
                kind="entry_point",
                name="Ночная переоценка",
                source="registry",
                attributes={"entry_kind": "job"},
            ),
            GraphNode(key="src/A.cs#A", kind="type", name="Runner", file="src/A.cs"),
        ),
        edges=(
            GraphEdge(
                kind="dispatches", source="entry:job:ночная", target="src/A.cs#A", via="тест"
            ),
        ),
    )


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[Path, GraphIndex, GraphMeta, object]:
    built = index()
    reachability = compute(built)
    target = tmp_path / "graph.db"
    meta = write_index(
        target,
        built,
        GraphMeta(generation="", repo="демо", roots=reachability.roots),
        reachability,
        entries(built),
    )
    return target, built, meta, reachability


def write_set(path: Path, questions: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"questions": questions}, allow_unicode=True), encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────────────────
# Формат набора
# ──────────────────────────────────────────────────────────────────────────────


def test_question_without_expectation_is_refused(tmp_path: Path) -> None:
    """Вопрос без ожидаемого ответа ничего не мерит, а в отчёте выглядит
    как измерение."""
    path = write_set(tmp_path / "set.yaml", [{"id": "пустой", "form": "resolve", "args": {}}])
    with pytest.raises(ValueError, match="ничего не мерит"):
        load(path)


def test_unknown_form_is_refused(tmp_path: Path) -> None:
    path = write_set(tmp_path / "set.yaml", [{"id": "x", "form": "выдумка", "expect": ["что-то"]}])
    with pytest.raises(ValueError, match="форма"):
        load(path)


def test_every_shipped_set_loads() -> None:
    """Наборы в репозитории обязаны грузиться: набор, который не читается,
    не прогоняют, а значит и качество не меряют."""
    assert SETS, "в репозитории нет ни одного оценочного набора"
    for path in SETS:
        assert load(path)


def test_every_question_says_why_it_is_asked() -> None:
    """Вопрос без объяснения через месяц неотличим от случайного, и первый же
    его провал спишут на «набор устарел»."""
    for path in SETS:
        for question in load(path):
            assert question.why, f"{path}: {question.id} без объяснения"


# ──────────────────────────────────────────────────────────────────────────────
# Счёт
# ──────────────────────────────────────────────────────────────────────────────


def test_missing_expectation_lowers_recall(tmp_path: Path, prepared: tuple) -> None:
    target, built, meta, reachability = prepared
    path = write_set(
        tmp_path / "set.yaml",
        [
            {
                "id": "половина",
                "form": "reaches",
                "args": {"root": "entry:job:ночная"},
                "expect": ["src/A.cs#A", "чего-нет-в-ответе"],
                "why": "проверка счёта",
            }
        ],
    )
    score = run(load(path), target, built, meta, reachability)
    assert score.recall == 0.5
    assert score.passed == 0


def test_forbidden_value_lowers_precision(tmp_path: Path, prepared: tuple) -> None:
    """Ответ, в который затесался чужой узел, выглядит полнее правильного —
    поэтому запреты считаются отдельно."""
    target, built, meta, reachability = prepared
    path = write_set(
        tmp_path / "set.yaml",
        [
            {
                "id": "лишнее",
                "form": "reaches",
                "args": {"root": "entry:job:ночная"},
                "refute": ["src/A.cs#A"],
                "why": "проверка счёта",
            }
        ],
    )
    score = run(load(path), target, built, meta, reachability)
    assert score.precision == 0.0
    assert score.outcomes[0].forbidden == ("src/A.cs#A",)


def test_report_names_what_did_not_match(tmp_path: Path, prepared: tuple) -> None:
    target, built, meta, reachability = prepared
    path = write_set(
        tmp_path / "set.yaml",
        [
            {
                "id": "проваленный",
                "form": "resolve",
                "args": {"query": "ночная"},
                "expect": ["чего-нет"],
                "why": "нужен текст находки",
            }
        ],
    )
    text = format_score(run(load(path), target, built, meta, reachability))
    assert "не нашлось: чего-нет" in text
    assert "нужен текст находки" in text


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def test_cli_can_fail_under_a_threshold(tmp_path: Path, prepared: tuple) -> None:
    """Порог задаёт вызывающий: набор, красный по умолчанию, выключат."""
    target, *_ = prepared
    path = write_set(
        tmp_path / "set.yaml",
        [
            {
                "id": "проваленный",
                "form": "resolve",
                "args": {"query": "ночная"},
                "expect": ["чего-нет"],
                "why": "порог",
            }
        ],
    )
    quiet = runner.invoke(app, ["graph", "eval", str(path), "--index", str(target)])
    assert quiet.exit_code == 0
    strict = runner.invoke(
        app, ["graph", "eval", str(path), "--index", str(target), "--fail-under", "0.5"]
    )
    assert strict.exit_code == 1


# ------------------------------------------------------------------------------------------
# Проверка на влитых правках (G18 п. 4)
# ------------------------------------------------------------------------------------------


def _repo_with_history(root: Path) -> None:
    """Крохотный репозиторий с историей: две правки, вторая трогает точку входа."""
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(root),
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
                "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
                "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00",
            },
        )

    root.mkdir(parents=True, exist_ok=True)
    git("init", "-q")
    (root / "readme.md").write_text("нет кода\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "документация")
    (root / "Api.cs").write_text("class Api {}\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "правка кода")


def test_requests_without_code_are_not_counted_as_checked(tmp_path: Path) -> None:
    """Правка, не тронувшая код, в набор не попадает.

    Иначе набор из десяти правок документации даёт десять строк
    «предсказано 0, обязано 0» и выглядит проверкой, которой не было.
    """
    root = tmp_path / "repo"
    _repo_with_history(root)
    requests = merged_requests(root, count=10, scan=50)
    assert [request.subject for request in requests] == ["правка кода"]


def test_missing_entry_point_is_reported(tmp_path: Path) -> None:
    """Точка входа, чей файл менялся, обязана найтись — иначе это пропуск."""
    root = tmp_path / "repo"
    _repo_with_history(root)

    nodes = (
        GraphNode(key="Api.cs#Api", kind="type", name="Api", file="Api.cs"),
        GraphNode(key="entry:http:get api", kind="entry_point", name="GET api", file="doc.md"),
    )
    # Корень связан с узлом кода — по этой связи и собирается истина.
    edges = (
        GraphEdge(
            kind="dispatches",
            source="entry:http:get api",
            target="Api.cs#Api",
            via="entry:manifest",
            confidence=1.0,
        ),
    )
    index = GraphIndex(nodes=nodes, edges=edges)
    meta = GraphMeta(generation="x", roots=("entry:http:get api",))

    class _Empty:
        """Достижимость, которая не находит ничего: имитация пропуска."""

        def fanout(self, key: str) -> int:
            return 0

        def roots_of(self, key: str) -> set[str]:
            return set()

    outcomes = check_requests(merged_requests(root, 10, 50), index, meta, _Empty())  # type: ignore[arg-type]
    assert [outcome.missed for outcome in outcomes] == [("entry:http:get api",)]
    assert "пропущено: 1" in format_requests(outcomes)


def test_entry_point_file_is_the_code_file_not_the_document(tmp_path: Path) -> None:
    """Истина собирается по файлу кода, а не по `file` узла корня.

    У корня из манифеста там лежит путь документа: ground truth по нему
    пуст всегда, и проверка молча объявляет «пропусков нет».
    """
    root = tmp_path / "repo"
    _repo_with_history(root)
    nodes = (
        GraphNode(key="Api.cs#Api", kind="type", name="Api", file="Api.cs"),
        GraphNode(
            key="entry:http:get api",
            kind="entry_point",
            name="GET api",
            file="docs/modules/api.md",
        ),
    )
    edges = (
        GraphEdge(
            kind="dispatches",
            source="entry:http:get api",
            target="Api.cs#Api",
            via="entry:manifest",
            confidence=1.0,
        ),
    )
    index = GraphIndex(nodes=nodes, edges=edges)
    meta = GraphMeta(generation="x", roots=("entry:http:get api",))
    reachability = compute(index)
    outcomes = check_requests(merged_requests(root, 10, 50), index, meta, reachability)
    assert [outcome.obvious for outcome in outcomes] == [("entry:http:get api",)]
