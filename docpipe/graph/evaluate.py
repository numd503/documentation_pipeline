"""Оценочный набор: измерение качества ответов (G18).

На продукте, чья ценность — «ответ правильный», это не роскошь: без набора
регресс качества не виден ничем, а доказывать пользу нечем. Набор меряет
**связку** «сторонний разбор + наш слой» как одно целое: пользователю
всё равно, чьё ребро соврало.

**Вопрос записывается вместе с ожидаемым ответом, а не вместо него.**
Ожидание — это список того, что в ответе обязано быть (`expect`), и того,
чего в нём быть не должно (`refute`). Второе не формальность: ответ,
в который затесался чужой сервис, выглядит полнее правильного.

Набор пополняется каждый раз, когда найден неверный ответ, — иначе та же
ошибка вернётся.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from docpipe.graph.api import affects, card, reaches, resolve
from docpipe.graph.api import path as path_form
from docpipe.graph.model import GraphIndex, GraphMeta
from docpipe.graph.reach import Reachability

FORMS: Final[tuple[str, ...]] = ("resolve", "card", "reaches", "affects", "path")


@dataclass(frozen=True)
class Question:
    """Один вопрос набора."""

    id: str
    form: str
    args: dict[str, Any]
    expect: tuple[str, ...] = ()
    refute: tuple[str, ...] = ()
    why: str = ""


@dataclass(frozen=True)
class Outcome:
    question: Question
    found: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.missing and not self.forbidden


@dataclass(frozen=True)
class Score:
    total: int = 0
    passed: int = 0
    outcomes: tuple[Outcome, ...] = ()
    # Полнота и точность считаются по ожиданиям, а не по вопросам: вопрос,
    # у которого нашлось три ожидания из четырёх, отвечен наполовину,
    # и округлять это до «не отвечен» значит терять различие.
    recall: float = 0.0
    precision: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)


def load(path: Path) -> list[Question]:
    """Прочитать набор. Вопрос без ожидания не принимается.

    Причина та же, по которой в правилах отсева обязателен `reason`:
    вопрос без ожидаемого ответа ничего не измеряет, а в отчёте выглядит
    как измерение.
    """
    raw = yaml.safe_load(path.read_bytes().decode("utf-8-sig")) or {}
    questions: list[Question] = []
    for index, item in enumerate(raw.get("questions", [])):
        identity = str(item.get("id") or f"вопрос-{index}")
        form = str(item.get("form", ""))
        if form not in FORMS:
            raise ValueError(f"{identity}: форма {form!r} неизвестна; известны {list(FORMS)}")
        expect = tuple(str(value) for value in item.get("expect", []))
        refute = tuple(str(value) for value in item.get("refute", []))
        if not expect and not refute:
            raise ValueError(f"{identity}: нет ни `expect`, ни `refute` — вопрос ничего не мерит")
        questions.append(
            Question(
                id=identity,
                form=form,
                args=dict(item.get("args", {})),
                expect=expect,
                refute=refute,
                why=str(item.get("why", "")),
            )
        )
    return questions


def _answer(
    question: Question,
    index_path: Path,
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
) -> str:
    """Ответ формы, приведённый к тексту: ожидания сверяются вхождением.

    Вхождение подстроки, а не сравнение структур: набор пишет человек,
    и требовать от него точного вида ответа значит сделать набор
    неподдерживаемым после первой же правки формата.
    """
    if question.form == "resolve":
        answer = resolve(index_path, index, str(question.args.get("query", "")))
    elif question.form == "card":
        answer = card(index, meta, reachability, str(question.args.get("node", "")))
    elif question.form == "reaches":
        answer = reaches(index, meta, reachability, str(question.args.get("root", "")))
    elif question.form == "affects":
        answer = affects(
            index, meta, reachability, [str(value) for value in question.args.get("keys", [])]
        )
    else:
        answer = path_form(
            index,
            str(question.args.get("source", "")),
            str(question.args.get("target", "")),
            int(question.args.get("depth", 12)),
        )
    return json.dumps(answer, ensure_ascii=False)


def run(
    questions: list[Question],
    index_path: Path,
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
) -> Score:
    """Прогнать набор и посчитать полноту с точностью."""
    outcomes: list[Outcome] = []
    expected_total = 0
    expected_found = 0
    forbidden_total = 0
    forbidden_seen = 0

    for question in questions:
        text = _answer(question, index_path, index, meta, reachability)
        found = tuple(value for value in question.expect if value in text)
        missing = tuple(value for value in question.expect if value not in text)
        forbidden = tuple(value for value in question.refute if value in text)
        expected_total += len(question.expect)
        expected_found += len(found)
        forbidden_total += len(question.refute)
        forbidden_seen += len(forbidden)
        outcomes.append(
            Outcome(question=question, found=found, missing=missing, forbidden=forbidden)
        )

    passed = sum(1 for outcome in outcomes if outcome.passed)
    return Score(
        total=len(questions),
        passed=passed,
        outcomes=tuple(outcomes),
        recall=round(expected_found / expected_total, 3) if expected_total else 1.0,
        precision=(round(1 - forbidden_seen / forbidden_total, 3) if forbidden_total else 1.0),
        counts={
            "ожиданий всего": expected_total,
            "ожиданий подтверждено": expected_found,
            "запретов всего": forbidden_total,
            "запретов нарушено": forbidden_seen,
        },
    )


def format_score(score: Score) -> str:
    """Отчёт для человека и для журнала."""
    lines = [
        f"Вопросов: {score.total}, отвечено полностью: {score.passed}",
        f"Полнота: {score.recall}, точность: {score.precision}",
        "",
    ]
    for outcome in score.outcomes:
        mark = "OK " if outcome.passed else "НЕТ"
        lines.append(f"{mark} {outcome.question.id}  ({outcome.question.form})")
        if outcome.question.why:
            lines.append(f"      {outcome.question.why}")
        for value in outcome.missing:
            lines.append(f"      не нашлось: {value}")
        for value in outcome.forbidden:
            lines.append(f"      лишнее в ответе: {value}")
    lines.append("")
    for name, number in score.counts.items():
        lines.append(f"  {name}: {number}")
    lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class PullRequest:
    """Влитая правка: что в ней менялось."""

    commit: str
    subject: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullRequestOutcome:
    request: PullRequest
    predicted: tuple[str, ...] = ()
    obvious: tuple[str, ...] = ()
    missed: tuple[str, ...] = ()
    downstream: int = 0
    shared: int = 0


# Расширения, по которым правка считается правкой кода. Слияние, тронувшее
# только документацию и настройки сборки, об `affects` не говорит ничего:
# оно даст ноль предсказаний и ноль обязательных находок, а в отчёте будет
# выглядеть как проверенный случай.
CODE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".cs", ".ts", ".js", ".py", ".sql", ".html", ".razor", ".cshtml"}
)


def merged_requests(root: Path, count: int, scan: int = 0) -> list[PullRequest]:
    """Последние влитые правки, тронувшие код, и их файлы.

    Берутся коммиты слияния: они и есть влитые PR. Если истории слияний нет
    (репозиторий с линейной историей), берутся обычные коммиты — вопрос
    «что предсказал `affects` на реальной правке» от этого не меняется.

    Просматривается `scan` последних, а возвращается `count` из них: подряд
    идущие слияния часто оказываются правками документации, и набор из десяти
    таких — это десять строк «предсказано 0, обязано 0», по которым о качестве
    ответа не сказано ничего.
    """
    import subprocess

    def log(extra: list[str]) -> list[str]:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "-C",
                str(root),
                "log",
                f"-{max(scan, count)}",
                "--format=%H%x02%s",
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout.splitlines() if proc.returncode == 0 else []

    lines = log(["--merges"]) or log([])
    requests: list[PullRequest] = []
    for line in lines:
        if len(requests) >= count:
            break
        commit, _, subject = line.partition("\x02")
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotePath=false",
                "-C",
                str(root),
                "show",
                "--name-only",
                "--format=",
                "-m",
                "--first-parent",
                commit,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        files = tuple(sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()}))
        if not any(Path(file).suffix in CODE_SUFFIXES for file in files):
            continue
        requests.append(PullRequest(commit=commit[:12], subject=subject, files=files))
    return requests


def check_requests(
    requests: list[PullRequest],
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
) -> list[PullRequestOutcome]:
    """Прогнать `affects` по влитым правкам.

    **Что здесь считается истиной.** Настоящая истина — «что реально
    затрагивалось в ревью», и её у открытого репозитория нет. Но есть
    проверяемая её часть: если правка меняла файл точки входа, то `affects`
    по этой правке обязан эту точку входа назвать. Пропуск здесь — ложное
    отрицание, а это самый опасный вид ошибки: он тихий.

    Всё, что `affects` назвал сверх этого, — предсказание вниз по графу;
    оно считается отдельным числом и истиной не объявляется.
    """
    # Файл точки входа — это файл **кода**, с которым она связана, а не поле
    # `file` её узла: у корня, пришедшего из манифеста, там лежит путь
    # документа (`docs/modules/…/x.md`). Ground truth, собранный по нему,
    # был бы пуст всегда и молча — проверка показывала бы «пропусков нет»
    # ровно потому, что проверять оказалось нечего.
    nodes = {node.key: node for node in index.nodes}
    roots = {node.key for node in index.nodes if node.kind == "entry_point"}
    roots_by_file: dict[str, set[str]] = {}
    for key in roots:
        node = nodes[key]
        if node.file and not node.file.endswith(".md"):
            roots_by_file.setdefault(node.file, set()).add(key)
    for edge in index.edges:
        if edge.source in roots:
            target = nodes.get(edge.target)
            if target is not None and target.file:
                roots_by_file.setdefault(target.file, set()).add(edge.source)

    outcomes: list[PullRequestOutcome] = []
    for request in requests:
        # Предел страницы снят намеренно: усечённый список, прочитанный
        # как полный, дал бы «пропущено» там, где точка входа просто
        # не поместилась в страницу.
        answer = affects(index, meta, reachability, list(request.files), limit=1_000_000)
        predicted = {str(item["node"]) for item in answer.get("entry_points", {}).get("items", [])}
        obvious = {key for file in request.files for key in roots_by_file.get(file, set())}
        outcomes.append(
            PullRequestOutcome(
                request=request,
                predicted=tuple(sorted(predicted)),
                obvious=tuple(sorted(obvious)),
                missed=tuple(sorted(obvious - predicted)),
                downstream=len(predicted - obvious),
                shared=len(answer.get("shared_components", [])),
            )
        )
    return outcomes


def format_requests(outcomes: list[PullRequestOutcome]) -> str:
    """Отчёт по влитым правкам."""
    checked = [outcome for outcome in outcomes if outcome.obvious]
    missed = sum(len(outcome.missed) for outcome in checked)
    expected = sum(len(outcome.obvious) for outcome in checked)
    lines = [
        f"Правок просмотрено: {len(outcomes)}, из них меняли файл точки входа: {len(checked)}",
        f"Точек входа, которые обязаны были найтись: {expected}, пропущено: {missed}",
        "",
    ]
    for outcome in outcomes:
        mark = "OK " if not outcome.missed else "НЕТ"
        lines.append(
            f"{mark} {outcome.request.commit}  файлов {len(outcome.request.files)}, "
            f"предсказано точек входа {len(outcome.predicted)} "
            f"(из них вниз по графу {outcome.downstream}), "
            f"общих компонентов {outcome.shared}"
        )
        lines.append(f"      {outcome.request.subject[:100]}")
        for key in outcome.missed:
            lines.append(f"      ПРОПУЩЕНА: {key}")
    lines.append("")
    lines.append(
        "Истина здесь — проверяемая её часть: правка меняла файл точки входа, "
        "значит `affects` обязан был её назвать. Всё, что названо сверх, — "
        "предсказание вниз по графу, и истиной оно не объявляется."
    )
    lines.append("")
    return "\n".join(lines)
