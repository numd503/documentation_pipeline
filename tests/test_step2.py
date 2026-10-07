"""Вход шага 2 библиотекой и JSON у команд шага 2 и `arch validate` (S06).

Вход (`docpipe.step2`) зовут без CLI сервер настройки и сводка состояния,
поэтому здесь проверяется, что он не печатает, не выходит из процесса и даёт
тот же план, что команда. Отчёты JSON проверяются против текста той же
команды: две формы одного ответа, разошедшиеся на находке, хуже одной.

Манифест — золотой, `tests/golden/doc-tree.json`: это манифест `SampleSolution`,
на нём же стоят тесты `materialize` и `docs status`.
"""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.materialize.apply import apply_plan, materialize_report
from docpipe.materialize.explain import explain_report
from docpipe.materialize.ownership import (
    OwnershipLint,
    format_lint,
    lint_findings,
    load_ownership,
)
from docpipe.materialize.plan import MaterializePlan, PlannedDoc
from docpipe.materialize.status import status_report
from docpipe.model import SourceSpan
from docpipe.step2 import BUSINESS_LINKS_PREFIX, Step2Error, load_manifest, prepare

MANIFEST = Path("tests/golden/doc-tree.json")
CONTROLLER = "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"
SERVICE = "docs/modules/services/Sample.Pricing.Api/pricing-service.md"
ARCH_EXAMPLE = Path("arch-registry.example.yaml")
runner = CliRunner()

OWNERSHIP = (
    'version: "1"\n'
    "teams: [{id: pricing, title: P}, {id: idle, title: I}]\n"
    "rules:\n"
    "  - {id: p, team: pricing, priority: 10, when: {kind: [controller]}}\n"
    "  - {id: dead, team: pricing, priority: 10, when: {module: [Нетакого]}}\n"
)


def _invoke(*args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, list(args))


def _materialize(root: Path, *extra: str, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return _invoke("materialize", str(manifest), "--root", str(root), *extra)


def _break(root: Path, doc_path: str) -> None:
    """Порвать секцию: документ становится `broken`, файл — `refuse`."""
    path = root / doc_path
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("<!-- docpipe:section:end notes -->", ""), encoding="utf-8")


def _unknown_template(tmp_path: Path) -> Path:
    """Золотой манифест, где у одного узла вид без своего скелета."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["nodes"][0]["template"] = "нет-такого"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# Вход шага 2 без CLI
# --------------------------------------------------------------------------------------


def test_prepare_gives_the_plan_of_materialize_dry_run(tmp_path: Path) -> None:
    """Один и тот же план у библиотеки и у команды — затем вход и вынесен.

    Сравнивается весь отчёт, а не число документов: копия сборки, отставшая
    на один ключ, совпала бы по числу и разошлась бы по `file_action`.
    """
    _materialize(tmp_path)
    _break(tmp_path, CONTROLLER)

    loaded = prepare(load_manifest(MANIFEST), tmp_path, DocpipeConfig(), None)
    expected = materialize_report(
        loaded.plan, apply_plan(loaded.plan, tmp_path, dry_run=True), dry_run=True
    )
    result = _materialize(tmp_path, "--dry-run", "--format", "json")

    assert json.loads(result.stdout) == expected.model_dump(mode="json")
    assert {doc.file_action for doc in expected.documents} == {"refuse", "unchanged"}


def test_prepare_neither_prints_nor_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Предупреждения бизнес-ссылок — в `warnings`, а не в stderr.

    Библиотека, печатающая в stderr, засорила бы ответ сервера настройки:
    у MCP поверх stdio другого канала для разговора нет.
    """
    settings = DocpipeConfig(registries=str(tmp_path / "нет-такого.yaml"))

    loaded = prepare(load_manifest(MANIFEST), tmp_path, settings, None)

    assert len(loaded.warnings) == 1
    assert loaded.warnings[0].startswith(f"{BUSINESS_LINKS_PREFIX}каталог не прочитан")
    assert capsys.readouterr() == ("", "")
    # Бизнес-слой прогон не роняет: план собран целиком.
    assert len(loaded.plan.documents) == 6


def test_cli_prints_the_warnings_of_prepare(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text(f"registries: {tmp_path / 'нет-такого.yaml'}\n", encoding="utf-8")

    result = _materialize(tmp_path, "--dry-run", "--config", str(config))

    assert result.exit_code == 0
    assert f"{BUSINESS_LINKS_PREFIX}каталог не прочитан" in result.stderr


def test_input_errors_are_step2_errors_with_code_2(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)

    with pytest.raises(Step2Error) as missing:
        load_manifest(tmp_path / "нет.json")
    with pytest.raises(Step2Error) as templates:
        prepare(manifest, tmp_path, DocpipeConfig(), None, templates_dir=tmp_path / "нет")
    with pytest.raises(Step2Error) as team:
        prepare(manifest, tmp_path, DocpipeConfig(), None, teams=("нет-такой",))

    assert (missing.value.code, templates.value.code, team.value.code) == (2, 2, 2)
    assert "Неизвестные команды: нет-такой" in team.value.message


def test_links_flag_attaches_broken_links(tmp_path: Path) -> None:
    """`links=True` — то, что раньше `docs status`, `explain` и `worklist`
    делали каждая сама, вызывая `with_links` после `_prepare`."""
    _materialize(tmp_path)
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "<!-- docpipe:section:end purpose -->",
            "[куда-то](нет-такого.md)\n<!-- docpipe:section:end purpose -->",
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(MANIFEST)

    def broken(links: bool) -> list[str]:
        plan = prepare(manifest, tmp_path, DocpipeConfig(), None, links=links).plan
        return next(doc for doc in plan.documents if doc.doc_path == CONTROLLER).broken_links

    assert broken(links=False) == []
    assert broken(links=True) == ["нет-такого.md"]


def test_cli_keeps_no_copy_of_the_assembly() -> None:
    """Сборка плана живёт в одном месте. Вторая копия в CLI — ровно та ловушка,
    из-за которой вход и выносился: три копии `_prepare` однажды разошлись."""
    source = Path("docpipe/cli.py").read_text(encoding="utf-8")

    for call in ("build_plan(", "scan_docs(", "shadowed_docs(", "load_templates(", "with_links("):
        assert call not in source, call


# --------------------------------------------------------------------------------------
# Битая конфигурация — код 2 и сообщение, а не трассировка
# --------------------------------------------------------------------------------------


STEP2_COMMANDS = [
    ("materialize", str(MANIFEST)),
    ("docs", "status", str(MANIFEST)),
    ("docs", "explain", str(MANIFEST), CONTROLLER),
    ("worklist", str(MANIFEST)),
    ("docs", "owners", str(MANIFEST)),
]


@pytest.mark.parametrize("command", STEP2_COMMANDS, ids=lambda c: " ".join(c[:2]))
def test_broken_config_is_code_2_and_one_line(command: tuple[str, ...], tmp_path: Path) -> None:
    """Раньше `load_config` стоял вне `try`: трассировка и код 1, который
    `docs status --fail-on` в CI читал бы как «документ устарел»."""
    config = tmp_path / "docpipe.yaml"
    config.write_text("enrolled:\n", encoding="utf-8")

    result = _invoke(*command, "--config", str(config))

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("Ошибка конфигурации:")
    assert "`enrolled:` без элементов" in lines[0]


@pytest.mark.parametrize("command", STEP2_COMMANDS, ids=lambda c: " ".join(c[:2]))
def test_unknown_config_key_is_code_2(command: tuple[str, ...], tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text("docs_rot: docs\n", encoding="utf-8")

    result = _invoke(*command, "--config", str(config))

    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("Ошибка конфигурации:")
    assert "docs_rot" in result.stderr


# --------------------------------------------------------------------------------------
# docs status --format json: конверт
# --------------------------------------------------------------------------------------


def test_status_envelope_carries_notes_substituted_and_document_errors(tmp_path: Path) -> None:
    """Текст печатал это всегда, JSON — терял: агент видел `broken`, но не причину."""
    manifest = _unknown_template(tmp_path)
    root = tmp_path / "repo"
    _materialize(root, manifest=manifest)
    _break(root, CONTROLLER)

    result = _invoke("docs", "status", str(manifest), "--root", str(root), "--format", "json")
    payload = json.loads(result.stdout)

    assert payload["schema_version"] == "1.0"
    assert payload["notes"] == []
    assert payload["substituted"] == [{"template": "нет-такого", "count": 1}]
    assert [item["doc_path"] for item in payload["document_errors"]] == [CONTROLLER]
    assert payload["document_errors"][0]["error"]
    # Записи документов не тронуты: новое — только в конверте.
    assert "error" not in payload["documents"][0]


def test_status_report_takes_notes_from_the_plan() -> None:
    doc = PlannedDoc(
        doc_path="docs/b.md",
        node_id=None,
        file_action="refuse",
        status="broken",
        agent_action="review",
        error="не закрыта секция notes",
    )
    plan = MaterializePlan(
        documents=[doc],
        notes=["кандидатов на перенос два: решите вручную"],
        substituted={"b": 1, "a": 1, "c": 5},
    )

    report = status_report(plan, plan.documents)

    assert report.notes == ["кандидатов на перенос два: решите вручную"]
    assert [(item.template, item.count) for item in report.substituted] == [
        ("c", 5),
        ("a", 1),
        ("b", 1),
    ]
    assert [(item.doc_path, item.error) for item in report.document_errors] == [
        ("docs/b.md", "не закрыта секция notes")
    ]


def test_status_json_is_byte_stable(tmp_path: Path) -> None:
    _materialize(tmp_path)
    args = ("docs", "status", str(MANIFEST), "--root", str(tmp_path), "--format", "json")

    assert _invoke(*args).stdout == _invoke(*args).stdout


# --------------------------------------------------------------------------------------
# materialize --format json
# --------------------------------------------------------------------------------------


def test_materialize_json_on_a_fresh_tree(tmp_path: Path) -> None:
    result = _materialize(tmp_path, "--dry-run", "--format", "json")
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["schema_version"] == "1.0"
    assert payload["dry_run"] is True
    # Все пять действий, с нулями: «ни одного update» — ноль, а не пропавший ключ.
    assert payload["counts"] == {
        "create": 6,
        "refuse": 0,
        "relocate": 0,
        "unchanged": 0,
        "update": 0,
    }
    assert set(payload["documents"][0]) == {
        "doc_path",
        "node_id",
        "file_action",
        "status",
        "relocate_from",
        "confidence",
    }
    assert [doc["doc_path"] for doc in payload["documents"]] == sorted(
        doc["doc_path"] for doc in payload["documents"]
    )
    assert not (tmp_path / "docs").exists()


def test_materialize_json_keeps_the_exit_code(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _break(tmp_path, CONTROLLER)

    result = _materialize(tmp_path, "--dry-run", "--format", "json")
    payload = json.loads(result.stdout)

    assert result.exit_code == 1
    assert payload["counts"]["refuse"] == 1


def test_materialize_json_without_dry_run_describes_what_was_done(tmp_path: Path) -> None:
    result = _materialize(tmp_path, "--format", "json")
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is False
    assert payload["counts"]["create"] == 6
    assert (tmp_path / CONTROLLER).is_file()


def test_materialize_rejects_an_unknown_format(tmp_path: Path) -> None:
    result = _materialize(tmp_path, "--format", "jsno")

    assert result.exit_code == 2
    assert not (tmp_path / "docs").exists()


# --------------------------------------------------------------------------------------
# docs explain --format json
# --------------------------------------------------------------------------------------


def _explain(root: Path, path: str, *extra: str, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return _invoke("docs", "explain", str(manifest), path, "--root", str(root), *extra)


def test_explain_json_names_the_zones_of_an_update(tmp_path: Path) -> None:
    _materialize(tmp_path)
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        node["domain"] = "Другой домен"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = _explain(tmp_path, CONTROLLER, "--format", "json", manifest=changed)
    report = json.loads(result.stdout)

    assert result.exit_code == 0
    assert report["schema_version"] == "1.0"
    assert report["exists"] is True
    assert report["scan_verdict"] is None
    assert report["plan"]["file_action"] == "update"
    assert report["plan"]["status"] == "empty"
    assert report["zone_diff"]["front_matter"] == [
        "docpipe.domain: Sample.Pricing.Api → Другой домен"
    ]
    assert report["zone_diff"]["generated_changed"] is True
    assert report["zone_diff"]["touches_authored"] is False


def test_explain_json_names_the_filter_that_dropped_the_file(tmp_path: Path) -> None:
    _materialize(tmp_path)
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace("  schema: materialize/1\n", ""),
        encoding="utf-8",
    )

    report = json.loads(_explain(tmp_path, CONTROLLER, "--format", "json").stdout)

    assert report["plan"]["file_action"] == "refuse"
    assert report["plan"]["error"]
    assert report["scan_verdict"] == "`docpipe.schema` = нет, а нужен префикс `materialize/`"


def test_explain_json_for_an_unknown_path_keeps_code_1(tmp_path: Path) -> None:
    _materialize(tmp_path)

    result = _explain(tmp_path, "docs/нет-такого.md", "--format", "json")
    report = json.loads(result.stdout)

    assert result.exit_code == 1
    assert report["plan"] is None
    assert report["exists"] is False


def test_explain_report_marks_a_touched_authored_section(tmp_path: Path) -> None:
    """Код 1 у `docs explain` берётся из этого поля — в обоих форматах."""
    path = tmp_path / "docs" / "x.md"
    path.parent.mkdir(parents=True)
    before = (
        "---\ndocpipe:\n  schema: materialize/1\n---\n"
        "<!-- docpipe:section:start purpose -->\nавторский текст\n"
        "<!-- docpipe:section:end purpose -->\n"
    )
    path.write_text(before, encoding="utf-8")
    doc = PlannedDoc(
        doc_path="docs/x.md",
        node_id="type:x",
        file_action="update",
        status="current",
        agent_action="skip",
        content=before.replace("авторский текст", "затёрли"),
    )

    report = explain_report("docs/x.md", doc, tmp_path, [])

    assert report.zone_diff is not None
    assert report.zone_diff.sections_changed == ["purpose"]
    assert report.zone_diff.touches_authored is True


# --------------------------------------------------------------------------------------
# docs owners --lint --format json
# --------------------------------------------------------------------------------------


def _owners(path: Path, *extra: str):  # type: ignore[no-untyped-def]
    return _invoke("docs", "owners", str(MANIFEST), "--ownership", str(path), "--lint", *extra)


def test_owners_lint_json_has_the_findings_of_the_text(tmp_path: Path) -> None:
    """Один набор находок: текст собирается из той же структуры, что уходит в JSON."""
    ownership = tmp_path / "ownership.yaml"
    ownership.write_text(OWNERSHIP, encoding="utf-8")

    text = _owners(ownership)
    structured = _owners(ownership, "--format", "json")
    report = OwnershipLint.model_validate_json(structured.stdout)

    assert structured.exit_code == text.exit_code == 1
    assert format_lint(report)[0] == text.stdout.strip("\n").splitlines()
    assert [(item.code, item.subject) for item in report.findings if item.count == 0] == [
        ("dead-rule", "dead"),
        ("idle-team", "idle"),
    ]
    summary = next(item for item in report.findings if item.code == "unowned-nodes")
    assert (summary.count, report.nodes) == (4, 6)


def test_owners_lint_warnings_come_structured(tmp_path: Path) -> None:
    """Ничья по приоритету и тип в нескольких каталогах — предупреждения
    с числом, ради которого заведены: правил в ничьей, каталогов у типа."""
    ownership = tmp_path / "ownership.yaml"
    ownership.write_text(
        "teams: [{id: a, title: A}, {id: b, title: B}]\n"
        "rules:\n"
        "  - {id: a.all, team: a, priority: 10, when: {module: [Sample.Pricing.Api]}}\n"
        "  - {id: b.all, team: b, priority: 10, when: {module: [Sample.Pricing.Api]}}\n"
        "  - {id: a.common, team: a, priority: 10, when: {module: [Sample.Common]}}\n",
        encoding="utf-8",
    )
    manifest = load_manifest(MANIFEST)
    nodes = []
    for node in manifest.nodes:
        if node.doc_path == SERVICE and node.symbol is not None:
            moved = SourceSpan(
                path="src/Sample.Pricing.Api/Other/PricingService.Extra.cs", start=1, end=9
            )
            symbol = node.symbol.model_copy(update={"sources": [*node.symbol.sources, moved]})
            node = node.model_copy(update={"symbol": symbol})
        nodes.append(node)

    report = lint_findings(nodes, load_ownership(ownership))
    findings, warnings = format_lint(report)

    ties = [item for item in report.warnings if item.code == "priority-tie"]
    split = [item for item in report.warnings if item.code == "split-type"]
    assert len(ties) == 5
    assert {item.count for item in ties} == {2}
    assert [(item.subject, item.count) for item in split] == [(SERVICE, 2)]
    assert warnings[0] == "Ничьи по приоритету у 5 узлов, победил меньший id:"
    assert f"    {SERVICE}" in warnings
    # Команда, проигравшая все ничьи меньшему id, остаётся без узлов — это
    # уже находка, а не предупреждение.
    assert findings == ["Команды, которым не досталось ни одного узла: b"]


def test_owners_json_needs_lint(tmp_path: Path) -> None:
    ownership = tmp_path / "ownership.yaml"
    ownership.write_text(OWNERSHIP, encoding="utf-8")

    result = _invoke(
        "docs", "owners", str(MANIFEST), "--ownership", str(ownership), "--format", "json"
    )

    assert result.exit_code == 2


# --------------------------------------------------------------------------------------
# arch validate --format json
# --------------------------------------------------------------------------------------


def test_arch_validate_json_on_a_valid_registry() -> None:
    result = _invoke("arch", "validate", str(ARCH_EXAMPLE), "--format", "json")
    report = json.loads(result.stdout)
    text = _invoke("arch", "validate", str(ARCH_EXAMPLE)).stdout

    assert result.exit_code == 0
    assert report["schema_version"] == "1.0"
    assert report["valid"] is True
    assert report["problems"] == []
    assert report["path"] == ARCH_EXAMPLE.as_posix()
    # Те же числа, что в строке текста.
    for kind, count in report["counts"].items():
        assert f"{kind}: {count}" in text


def test_arch_validate_json_lists_all_problems(tmp_path: Path) -> None:
    path = tmp_path / "arch.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "records": [
                    {"kind": "nope", "key": "a"},
                    {"kind": "entry_point", "key": "b"},
                ],
                "extra": True,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = _invoke("arch", "validate", str(path), "--format", "json")
    report = json.loads(result.stdout)
    text = _invoke("arch", "validate", str(path))

    assert result.exit_code == text.exit_code == 1
    assert report["valid"] is False
    assert report["counts"] == {}
    assert len(report["problems"]) >= 3
    for problem in report["problems"]:
        assert f"  {problem['where']}: {problem['message']}" in text.stderr


def test_arch_validate_json_keeps_input_errors_as_code_2(tmp_path: Path) -> None:
    result = _invoke("arch", "validate", str(tmp_path / "нет.yaml"), "--format", "json")

    assert result.exit_code == 2
    assert result.stdout == ""


# --------------------------------------------------------------------------------------
# worklist не меняется
# --------------------------------------------------------------------------------------


def test_worklist_is_byte_stable_between_runs(tmp_path: Path) -> None:
    """Очередь собирается из записей `document_json`; конверт `docs status`
    вырос, а записи и очередь — нет."""
    _materialize(tmp_path)
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    for out in (first, second):
        result = _invoke("worklist", str(MANIFEST), "--root", str(tmp_path), "--out", str(out))
        assert result.exit_code == 0, result.output

    assert first.read_bytes() == second.read_bytes()
    queue = json.loads(first.read_text(encoding="utf-8"))
    assert queue["schema_version"] == "1.1"
    assert "notes" not in queue
