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
