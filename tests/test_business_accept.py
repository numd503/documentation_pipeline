"""Состояние бизнес-документа и приёмка (B10).

Цикл замыкается здесь: пока приёмки не было, документ нельзя отличить
от написанного наугад. Проверяется главным образом то, **что именно** приёмка
фиксирует и на какие изменения кода она обязана среагировать, а на какие нет.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.business.status import changes
from docpipe.cli import app
from tests.business_support import combined_tree, edit, manifest

runner = CliRunner()
BUSINESS = "business"
SCORING = "business/processes/valuation/twinml-scoring.md"
WORKFLOW = "deployment/Data/Items/Workflows/Sample.v2.json"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = combined_tree(tmp_path)
    (root / "doc-tree.json").write_text(manifest().model_dump_json(), encoding="utf-8")

    # Единственная пустая секция фикстуры дописывается: пустой документ
    # не принимается, а проверять надо приёмку, а не отказ от неё.
    path = root / SCORING
    path.write_text(
        path.read_text(encoding="utf-8").replace("<!-- Пусто — нормально. -->", "Замечаний нет."),
        encoding="utf-8",
    )
    return root


def call(tree: Path, command: str, *args: str) -> object:
    return runner.invoke(
        app,
        [
            "business",
            command,
            str(tree / "doc-tree.json"),
            *args,
            "--registries",
            str(tree / "registries.yaml"),
            "--root",
            str(tree),
            "--business-root",
            BUSINESS,
        ],
    )


def status_of(tree: Path, doc_path: str = SCORING) -> dict[str, str]:
    result = call(tree, "status", "--format", "json")
    items = json.loads(result.stdout)
    return next(item for item in items if item["doc_path"] == doc_path)


# --------------------------------------------------------------------------------------
# Приёмка
# --------------------------------------------------------------------------------------


def test_accept_is_idempotent(tree: Path) -> None:
    """Времени в состоянии нет, поэтому второй вызов подряд не меняет файла.

    Со временем каждая приёмка давала бы дифф на пустом месте, и `git status`
    после прогона перестал бы что-либо означать.
    """
    call(tree, "build")
    assert call(tree, "accept", str(tree / SCORING)).exit_code == 0

    before = (tree / SCORING).read_bytes()
    assert call(tree, "accept", str(tree / SCORING)).exit_code == 0

    assert (tree / SCORING).read_bytes() == before


def test_accept_makes_the_document_current(tree: Path) -> None:
    call(tree, "build")
    assert status_of(tree)["status"] == "undeclared"

    call(tree, "accept", "--all")

    assert status_of(tree)["status"] == "current"
    assert status_of(tree)["action"] == "skip"


def test_accept_refuses_an_empty_document(tree: Path) -> None:
    """Приёмка означает «текст сверен с реализацией», а сверять нечего."""
    call(tree, "build")
    result = call(tree, "accept", "--all")

    assert result.exit_code == 1
    assert "limits-load.md" in result.output
    assert status_of(tree, "business/processes/valuation/limits-load.md")["status"] == "empty"


def test_force_accepts_an_empty_document(tree: Path) -> None:
    """Оставлено для документов, описанных целиком в чужой системе:
    у `bp.valuation.limits-load` заполнен `external_ref`."""
    call(tree, "build")
    result = call(tree, "accept", "--all", "--force")

    assert result.exit_code == 0
    assert status_of(tree, "business/processes/valuation/limits-load.md")["status"] == "empty"


def test_accept_dry_run_writes_nothing(tree: Path) -> None:
    call(tree, "build")
    before = (tree / SCORING).read_bytes()
    result = call(tree, "accept", "--all", "--dry-run")

    assert result.exit_code == 1  # два пустых документа всё равно отказ
    assert (tree / SCORING).read_bytes() == before


def test_accept_without_targets_is_a_user_error(tree: Path) -> None:
    result = call(tree, "accept")

    assert result.exit_code == 2
    assert "Нечего принимать" in result.output


def test_accept_takes_the_hash_from_fresh_resolution(tree: Path) -> None:
    """Front matter — зеркало и мог отстать. Приняв его значение, документ
    навсегда остался бы `current`, будучи `drifted`."""
    call(tree, "build")
    edit(tree / WORKFLOW, '"NextStepId": "ThresholdStep"', '"NextStepId": "ScoreStep"')

    # Документ не пересобирали: в нём лежит старая проекция. Приёмка обязана
    # взять факты из реестра, а не из документа.
    call(tree, "accept", str(tree / SCORING))

    assert status_of(tree)["status"] == "current"


# --------------------------------------------------------------------------------------
# На что приёмка реагирует, а на что нет
# --------------------------------------------------------------------------------------


def test_added_step_gives_review_with_its_name(tree: Path) -> None:
    call(tree, "build")
    call(tree, "accept", "--all")

    edit(
        tree / WORKFLOW,
        '{\n      "Id": "ScoreStep",',
        '{\n      "Id": "NotifyStep",\n'
        '      "StepType": "Sbt.Sample.Steps.NotifyStep, Sbt.Sample"\n'
        '    },\n    {\n      "Id": "ScoreStep",',
    )
    item = status_of(tree)

    assert item["status"] == "drifted"
    assert item["action"] == "review"
    assert "добавлен шаг NotifyStep" in item["reason"]


def test_repeated_accept_clears_the_drift(tree: Path) -> None:
    call(tree, "build")
    call(tree, "accept", "--all")
    edit(tree / WORKFLOW, '"NextStepId": "ThresholdStep"', '"NextStepId": "ScoreStep"')
    assert status_of(tree)["status"] == "drifted"

    call(tree, "accept", str(tree / SCORING))

    assert status_of(tree)["status"] == "current"


def test_renamed_step_class_stays_skip(tree: Path) -> None:
    """Переименование класса шага бизнес-смысла не меняет — и работы
    не создаёт. Ровно то требование, ради которого затеян `business_hash`."""
    call(tree, "build")
    call(tree, "accept", "--all")

    edit(tree / WORKFLOW, "Sbt.Sample.Steps.ScoreStep", "Sbt.Sample.Steps.ScoringStep")

    assert status_of(tree)["action"] == "skip"


def test_unresolved_anchor_is_broken_not_drifted(tree: Path) -> None:
    """Пропавшая точка входа — не «состав изменился», а «не нашли».
    Разные причины требуют разных действий, и смешивать их нельзя."""
    call(tree, "build")
    call(tree, "accept", "--all")

    edit(
        tree / "deployment/Data/Items/Items-Workflows.xml",
        "Data\\Items\\Workflows\\Sample.v2.json",
        "Data\\Items\\Workflows\\Gone.json",
    )
    item = status_of(tree)

    assert item["status"] == "broken"
    assert "точка входа не найдена" in item["reason"]


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def test_status_json_is_byte_identical_across_calls(tree: Path) -> None:
    call(tree, "build")
    first = call(tree, "status", "--format", "json")
    second = call(tree, "status", "--format", "json")

    assert first.stdout == second.stdout  # type: ignore[attr-defined]


def test_status_filters_by_action_and_team(tree: Path) -> None:
    call(tree, "build")

    assert "twinml-scoring" in call(tree, "status", "--action", "review").stdout  # type: ignore[attr-defined]
    assert "twinml-scoring" not in call(tree, "status", "--action", "write").stdout  # type: ignore[attr-defined]
    assert "Документов: 0" in call(tree, "status", "--team", "Нетакой").stdout  # type: ignore[attr-defined]


def test_status_rejects_a_typo_in_fail_on(tree: Path) -> None:
    result = call(tree, "status", "--fail-on", "drifed")

    assert result.exit_code == 2
    assert "Неизвестный статус" in result.output


def test_status_fail_on_returns_one(tree: Path) -> None:
    call(tree, "build")

    assert call(tree, "status", "--fail-on", "undeclared").exit_code == 1
    assert call(tree, "status", "--fail-on", "current").exit_code == 0


def test_status_selects_by_path(tree: Path) -> None:
    call(tree, "build")
    result = call(tree, "status", str(tree / SCORING))

    assert "Документов: 1" in result.stdout  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------
# Разница фактов
# --------------------------------------------------------------------------------------


def test_changes_names_what_happened() -> None:
    """Два хэша ничего не сообщают человеку, открывшему отчёт."""
    was = {
        "entry": [
            {
                "kind": "workflow",
                "scope": "",
                "ref": "W",
                "version": "1",
                "facts": {"steps": [{"id": "A", "next": "B"}, {"id": "B", "next": ""}]},
            }
        ]
    }
    now = {
        "entry": [
            {
                "kind": "workflow",
                "scope": "",
                "ref": "W",
                "version": "1",
                "facts": {
                    "steps": [
                        {"id": "A", "next": "C"},
                        {"id": "B", "next": ""},
                        {"id": "C", "next": "B"},
                    ]
                },
            }
        ]
    }

    assert changes(was, now) == ["добавлен шаг C", "шаг A: переход был 'B', стал 'C'"]


def test_changes_names_gendered_nouns_correctly() -> None:
    """«добавлен поле» в отчёте выглядит как дефект инструмента, а не как
    описание изменения."""
    was = {"entry": [{"kind": "table", "scope": "", "ref": "L", "version": "", "facts": {}}]}
    now = {
        "entry": [
            {
                "kind": "table",
                "scope": "",
                "ref": "L",
                "version": "",
                "facts": {"fields": [["F", "FieldText", "C", "", "", ""]]},
            }
        ]
    }

    assert changes(was, now) == ["добавлено поле F"]
