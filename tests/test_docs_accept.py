"""Приёмка, ручной перенос и владельцы (M10).

Приёмка замыкает цикл: она единственная пишет принятое состояние, и она же
единственное место, где документ объявляется соответствующим коду.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app

MANIFEST = Path("tests/golden/doc-tree.json")
CONTROLLER = "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md"
SERVICE = "docs/modules/Sample.Pricing.Api/services/pricing-service.md"
runner = CliRunner()


def _materialize(root: Path, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["materialize", str(manifest), "--root", str(root)])


def _accept(root: Path, *extra: str, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["docs", "accept", str(manifest), "--root", str(root), *extra])


def _status(root: Path, *extra: str, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app,
        ["docs", "status", str(manifest), "--root", str(root), "--format", "json", *extra],
    )


def _fill(root: Path, doc_path: str) -> None:
    path = root / doc_path
    text = path.read_text(encoding="utf-8")
    for name in ("purpose", "api", "behaviour", "collaboration", "notes"):
        text = text.replace(
            f"<!-- docpipe:section:end {name} -->",
            f"Авторский текст.\n<!-- docpipe:section:end {name} -->",
        )
    path.write_text(text, encoding="utf-8")


def _mutate(tmp_path: Path, **fields: str) -> Path:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["doc_path"] == CONTROLLER:
            node.update(fields)
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _doc(root: Path, doc_path: str, manifest: Path = MANIFEST) -> dict:  # type: ignore[type-arg]
    payload = json.loads(_status(root, doc_path, manifest=manifest).stdout)
    return payload["documents"][0]


# --------------------------------------------------------------------------------------
# Цикл приёмки
# --------------------------------------------------------------------------------------


def test_accept_makes_the_document_current(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)

    assert _doc(tmp_path, CONTROLLER)["action"] == "review"

    result = _accept(tmp_path, CONTROLLER)

    assert result.exit_code == 0
    assert _doc(tmp_path, CONTROLLER)["action"] == "skip"
    assert _doc(tmp_path, CONTROLLER)["status"] == "current"


def test_accepted_state_records_public_members_only(tmp_path: Path) -> None:
    """`_pricing` — приватное поле, в контракт не входит."""
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    _accept(tmp_path, CONTROLLER)

    text = (tmp_path / CONTROLLER).read_text(encoding="utf-8")

    assert "- GetAsync" in text
    assert "- PricingController" in text
    assert "- RecalculateAsync" in text
    assert "_pricing" not in text.split("docpipe_state:")[1]


def test_accept_is_idempotent(tmp_path: Path) -> None:
    """Времени в состоянии нет, поэтому два прогона подряд не меняют файл."""
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    _accept(tmp_path, CONTROLLER)
    before = (tmp_path / CONTROLLER).read_bytes()

    _accept(tmp_path, CONTROLLER)

    assert (tmp_path / CONTROLLER).read_bytes() == before


def test_accept_takes_hashes_from_the_manifest(tmp_path: Path) -> None:
    """Front matter — зеркало и мог отстать: документ не материализовали после
    последнего `scan`. Взяв значение оттуда, приёмка зафиксировала бы устаревший
    хэш, и документ навсегда остался бы `current`, будучи `stale`.
    """
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)

    # Зеркало во front matter намеренно расходится с манифестом.
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "signature_hash: sha256:0e29455f", "signature_hash: sha256:устарело"
        ),
        encoding="utf-8",
    )

    _accept(tmp_path, CONTROLLER)
    text = (tmp_path / CONTROLLER).read_text(encoding="utf-8")

    assert "устарело" not in text
    assert _doc(tmp_path, CONTROLLER)["status"] == "current"


# --------------------------------------------------------------------------------------
# Отказы
# --------------------------------------------------------------------------------------


def test_accept_refuses_an_empty_document(tmp_path: Path) -> None:
    _materialize(tmp_path)
    before = (tmp_path / CONTROLLER).read_bytes()

    result = _accept(tmp_path, CONTROLLER)

    assert result.exit_code == 1
    assert "нужен --force" in result.stderr
    assert (tmp_path / CONTROLLER).read_bytes() == before


def test_force_accepts_an_empty_document(tmp_path: Path) -> None:
    _materialize(tmp_path)

    result = _accept(tmp_path, CONTROLLER, "--force")

    assert result.exit_code == 0
    assert _doc(tmp_path, CONTROLLER)["status"] == "empty"
    assert "kind: controller" in (tmp_path / CONTROLLER).read_text(encoding="utf-8")


def test_accept_refuses_a_broken_document(tmp_path: Path) -> None:
    _materialize(tmp_path)
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace("<!-- docpipe:section:end notes -->", ""),
        encoding="utf-8",
    )
    before = path.read_bytes()

    result = _accept(tmp_path, CONTROLLER, "--force")

    assert result.exit_code == 1
    assert path.read_bytes() == before


def test_accept_requires_a_selector(tmp_path: Path) -> None:
    """Без отбора команда приняла бы всё дерево — слишком дорогая опечатка."""
    _materialize(tmp_path)

    result = _accept(tmp_path)

    assert result.exit_code == 2
    assert "хотя бы один отбор" in result.stderr


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    before = (tmp_path / CONTROLLER).read_bytes()

    result = _accept(tmp_path, CONTROLLER, "--dry-run")

    assert result.exit_code == 0
    assert (tmp_path / CONTROLLER).read_bytes() == before


# --------------------------------------------------------------------------------------
# Что снимает приёмку
# --------------------------------------------------------------------------------------


def test_contract_change_makes_it_stale_and_lists_members(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    _accept(tmp_path, CONTROLLER)

    changed = _mutate(tmp_path, signature_hash="sha256:changed")
    doc = _doc(tmp_path, CONTROLLER, manifest=changed)

    assert doc["action"] == "write"
    assert doc["status"] == "stale"
    assert "контракт изменился" in doc["reason"]

    assert _accept(tmp_path, CONTROLLER, manifest=changed).exit_code == 0
    assert _doc(tmp_path, CONTROLLER, manifest=changed)["status"] == "current"


def test_implementation_change_alone_asks_for_review(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    _accept(tmp_path, CONTROLLER)

    changed = _mutate(tmp_path, impl_hash="sha256:changed")
    doc = _doc(tmp_path, CONTROLLER, manifest=changed)

    assert doc["action"] == "review"
    assert doc["status"] == "drifted"
    assert "реализация изменилась" in doc["reason"]


def test_kind_change_asks_for_review(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    _accept(tmp_path, CONTROLLER)

    changed = _mutate(tmp_path, kind="service")
    doc = _doc(tmp_path, CONTROLLER, manifest=changed)

    assert doc["action"] == "review"
    assert "вид сущности: controller → service" in doc["reason"]


# --------------------------------------------------------------------------------------
# Ручной перенос
# --------------------------------------------------------------------------------------


def _adopt(root: Path, source: str, target: str, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app,
        [
            "docs",
            "adopt",
            str(MANIFEST),
            "--root",
            str(root),
            "--from",
            source,
            "--to",
            target,
            *extra,
        ],
    )


SPARE = "docs/modules/Sample.Pricing.Api/controllers/spare.md"


def _spare(root: Path, node_id: str = "type:src/X/X.csproj#X.Gone`0") -> str:
    """Документ исчезнувшего узла — то, ради чего `adopt` и нужен."""
    text = (root / CONTROLLER).read_text(encoding="utf-8")
    marker = "  node_id: "
    start = text.index(marker) + len(marker)
    end = text.index("\n", start)
    (root / SPARE).write_text(text[:start] + node_id + text[end:], encoding="utf-8")
    return SPARE


def test_adopt_moves_and_keeps_the_text(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER)
    spare = _spare(tmp_path)
    (tmp_path / SERVICE).unlink()

    result = _adopt(tmp_path, spare, SERVICE)
    text = (tmp_path / SERVICE).read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert not (tmp_path / spare).exists()
    assert "Авторский текст." in text
    assert f"doc_path: {SERVICE}" in text
    assert "kind: service" in text


def test_adopt_refuses_an_occupied_target(tmp_path: Path) -> None:
    _materialize(tmp_path)
    spare = _spare(tmp_path)

    result = _adopt(tmp_path, spare, SERVICE)

    assert result.exit_code == 1
    assert "цель занята" in result.stderr
    assert (tmp_path / spare).exists()


def test_adopt_refuses_a_duplicate_node_id(tmp_path: Path) -> None:
    """Тот же узел уже описан другим файлом: перенос создал бы вторую копию."""
    _materialize(tmp_path)
    (tmp_path / SPARE).write_bytes((tmp_path / CONTROLLER).read_bytes())
    (tmp_path / SERVICE).unlink()

    result = _adopt(tmp_path, SPARE, SERVICE)

    assert result.exit_code == 1
    assert "уже описан" in result.stderr
    assert (tmp_path / SPARE).exists()


def test_adopt_refuses_a_target_no_node_claims(tmp_path: Path) -> None:
    _materialize(tmp_path)

    result = _adopt(tmp_path, CONTROLLER, "docs/modules/Sample.Pricing.Api/services/нет.md")

    assert result.exit_code == 1
    assert "ни один узел" in result.stderr
    assert (tmp_path / CONTROLLER).exists()


def test_adopt_dry_run_does_not_move(tmp_path: Path) -> None:
    _materialize(tmp_path)
    spare = _spare(tmp_path)
    (tmp_path / SERVICE).unlink()

    result = _adopt(tmp_path, spare, SERVICE, "--dry-run")

    assert result.exit_code == 0
    assert (tmp_path / spare).exists()
    assert not (tmp_path / SERVICE).exists()


# --------------------------------------------------------------------------------------
# Владельцы
# --------------------------------------------------------------------------------------


OWNERSHIP = (
    'version: "1"\n'
    "teams: [{id: pricing, title: P}, {id: idle, title: I}]\n"
    "rules:\n"
    "  - {id: p, team: pricing, priority: 10, when: {kind: [controller]}}\n"
    "  - {id: dead, team: pricing, priority: 10, when: {module: [Нетакого]}}\n"
)


def _owners(root: Path, path: Path, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["docs", "owners", str(MANIFEST), "--ownership", str(path), *extra])


@pytest.fixture
def ownership_file(tmp_path: Path) -> Path:
    path = tmp_path / "ownership.yaml"
    path.write_text(OWNERSHIP, encoding="utf-8")
    return path


def test_owners_counts_by_team(tmp_path: Path, ownership_file: Path) -> None:
    result = _owners(tmp_path, ownership_file)

    assert result.exit_code == 0
    assert "pricing" in result.stdout
    assert "(не задан)" in result.stdout


def test_owners_lint_finds_problems(tmp_path: Path, ownership_file: Path) -> None:
    result = _owners(tmp_path, ownership_file, "--lint")

    assert result.exit_code == 1
    assert "не совпавшие ни с одним узлом: dead" in result.stdout
    assert "Узлов без владельца" in result.stdout
    assert "не досталось ни одного узла: idle" in result.stdout


def test_owners_explain(tmp_path: Path, ownership_file: Path) -> None:
    result = _owners(tmp_path, ownership_file, "--explain", CONTROLLER)

    assert result.exit_code == 0
    assert "← победитель" in result.stdout
    assert "владелец:  pricing" in result.stdout


def test_owners_explain_unknown_node(tmp_path: Path, ownership_file: Path) -> None:
    result = _owners(tmp_path, ownership_file, "--explain", "нет/такого.md")

    assert result.exit_code == 1


def test_owners_without_rules_is_a_user_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["docs", "owners", str(MANIFEST)])

    assert result.exit_code == 2
    assert "Правила владения не заданы" in result.stderr
