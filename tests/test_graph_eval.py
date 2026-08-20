"""Оценочный набор (G18).

Набор меряет связку «сторонний разбор + наш слой» как одно целое:
пользователю всё равно, чьё ребро соврало. Тесты держат два свойства —
вопрос без ожидания не принимается, а прогон на фикстурах повторяем.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.graph import GraphEdge, GraphIndex, GraphMeta, GraphNode, compute, write_index
from docpipe.graph.evaluate import format_score, load, run
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
