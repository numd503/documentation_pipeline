"""Сравнение манифестов — вход для шага 3 (T19)."""

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.diff import NodeChange, diff_manifests, format_changes
from docpipe.emit import scan, write_manifest
from docpipe.model import Manifest

runner = CliRunner()

WORKFLOW = "src/Sample.Pricing.Api/Workflows/ValuationWorkflow.cs"
SERVICE = "src/Sample.Pricing.Api/Services/PricingService.cs"


def _copy(sample_solution: Path, tmp_path: Path) -> Path:
    root = tmp_path / "Solution"
    shutil.copytree(sample_solution, root)
    return root


def _changes(root: Path, before: Manifest) -> list[NodeChange]:
    after, _ = scan(root)
    return diff_manifests(before, after)


# --------------------------------------------------------------------------------------
# Критерии приёмки
# --------------------------------------------------------------------------------------


def test_manifest_against_itself_is_empty(sample_solution: Path) -> None:
    manifest, _ = scan(sample_solution)
    assert diff_manifests(manifest, manifest) == []


def test_deleted_file_gives_one_removal(sample_solution: Path, tmp_path: Path) -> None:
    root = _copy(sample_solution, tmp_path)
    before, _ = scan(root)
    (root / WORKFLOW).unlink()

    changes = _changes(root, before)
    assert [c.change for c in changes] == ["removed"]
    assert "ValuationWorkflow" in changes[0].node_id


def test_rename_gives_added_and_removed(sample_solution: Path, tmp_path: Path) -> None:
    """Переименование — не «изменение», а исчезновение и появление.

    Идентификатор узла построен от FQN, и связать два имени между собой
    здесь нечем: это работа шага 3, если он захочет её делать.
    """
    root = _copy(sample_solution, tmp_path)
    before, _ = scan(root)

    target = root / WORKFLOW
    target.write_text(
        target.read_text(encoding="utf-8").replace("ValuationWorkflow", "PricingWorkflow"),
        encoding="utf-8",
    )

    changes = _changes(root, before)
    assert sorted(c.change for c in changes) == ["added", "removed"]
    assert any("PricingWorkflow" in c.node_id and c.change == "added" for c in changes)
    assert any("ValuationWorkflow" in c.node_id and c.change == "removed" for c in changes)


def test_new_member_gives_signature_changed(sample_solution: Path, tmp_path: Path) -> None:
    root = _copy(sample_solution, tmp_path)
    before, _ = scan(root)

    target = root / SERVICE
    target.write_text(
        target.read_text(encoding="utf-8").rstrip()[:-1] + "\n    public int Added() => 1;\n}\n",
        encoding="utf-8",
    )

    changes = _changes(root, before)
    assert [c.change for c in changes] == ["signature_changed"]
    assert "PricingService" in changes[0].node_id


# --------------------------------------------------------------------------------------
# Виды изменений
# --------------------------------------------------------------------------------------


def test_changed_rules_give_reclassification(sample_solution: Path, tmp_path: Path) -> None:
    """Переклассификация возникает не от правки кода, а от правки правил.

    Это основной сценарий настройки: добавили правило — половина дерева
    сменила вид, и по диффу видно, что именно.
    """
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "ruleset_version: t\nexclude: {}\nrules:\n"
        "  - {id: r, kind: repository, template: repository, priority: 1,"
        " when: {name_suffix: ['Service']}}\n",
        encoding="utf-8",
    )

    before, _ = scan(sample_solution)
    after, _ = scan(sample_solution, ruleset=load_ruleset(rules))

    reclassified = [c for c in diff_manifests(before, after) if c.change == "reclassified"]
    assert reclassified
    assert all(c.details["to"] == "repository" for c in reclassified)
    # Смена вида меняет и doc_path, но сообщается только более существенное.
    assert not any(c.change == "moved" for c in diff_manifests(before, after))


def test_moved_when_only_path_changes() -> None:
    """`moved` без смены вида: узел тот же, файл лежит в другом месте."""
    from docpipe.model import DocNode, ParserVersions

    def node(doc_path: str) -> DocNode:
        return DocNode(
            id="type:a#N.C`0",
            kind="service",
            template="service",
            title="C",
            doc_path=doc_path,
            module="M",
            domain="d",
            signature_hash="sha256:same",
        )

    versions = ParserVersions(tree_sitter="1", grammar_c_sharp="1")
    old = Manifest(ruleset_version="v", parser=versions, nodes=[node("docs/a/services/c.md")])
    new = Manifest(ruleset_version="v", parser=versions, nodes=[node("docs/b/services/c.md")])

    changes = diff_manifests(old, new)
    assert [c.change for c in changes] == ["moved"]
    assert changes[0].details == {"from": "docs/a/services/c.md", "to": "docs/b/services/c.md"}


def test_only_one_change_per_node() -> None:
    """У узла может измениться всё сразу — сообщается самое существенное."""
    from docpipe.model import DocNode, ParserVersions

    versions = ParserVersions(tree_sitter="1", grammar_c_sharp="1")
    old = Manifest(
        ruleset_version="v",
        parser=versions,
        nodes=[
            DocNode(
                id="type:a#N.C`0",
                kind="service",
                template="service",
                title="C",
                doc_path="docs/services/c.md",
                module="M",
                domain="d",
                signature_hash="sha256:one",
            )
        ],
    )
    new = Manifest(
        ruleset_version="v",
        parser=versions,
        nodes=[
            DocNode(
                id="type:a#N.C`0",
                kind="repository",
                template="repository",
                title="C",
                doc_path="docs/repositories/c.md",
                module="M",
                domain="d",
                signature_hash="sha256:two",
            )
        ],
    )

    changes = diff_manifests(old, new)
    assert len(changes) == 1
    assert changes[0].change == "reclassified"


def test_changes_are_sorted(sample_solution: Path, tmp_path: Path) -> None:
    root = _copy(sample_solution, tmp_path)
    before, _ = scan(root)
    (root / WORKFLOW).unlink()
    (root / "src/Sample.Pricing.Api/Providers/CurveProvider.cs").unlink()

    changes = _changes(root, before)
    assert [(c.node_id, c.change) for c in changes] == sorted(
        (c.node_id, c.change) for c in changes
    )


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_diff_command_text(sample_solution: Path, tmp_path: Path) -> None:
    root = _copy(sample_solution, tmp_path)
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    manifest, _ = scan(root)
    write_manifest(manifest, first)
    (root / WORKFLOW).unlink()
    manifest, _ = scan(root)
    write_manifest(manifest, second)

    result = runner.invoke(app, ["diff", str(first), str(second)])
    assert result.exit_code == 0
    assert "removed" in result.output
    assert "Всего изменений: 1" in result.output


def test_diff_command_json(sample_solution: Path, tmp_path: Path) -> None:
    root = _copy(sample_solution, tmp_path)
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    manifest, _ = scan(root)
    write_manifest(manifest, first)
    (root / WORKFLOW).unlink()
    manifest, _ = scan(root)
    write_manifest(manifest, second)

    result = runner.invoke(app, ["diff", str(first), str(second), "--format", "json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert [item["change"] for item in payload] == ["removed"]


def test_diff_of_identical_manifests_says_so(sample_solution: Path, tmp_path: Path) -> None:
    """Пустой результат — это строка, а не пустой вывод: иначе непонятно, отработало ли."""
    out = tmp_path / "a.json"
    manifest, _ = scan(sample_solution)
    write_manifest(manifest, out)

    result = runner.invoke(app, ["diff", str(out), str(out)])
    assert result.exit_code == 0
    assert "Изменений нет" in result.output


def test_diff_exit_code_is_zero_even_with_changes(sample_solution: Path, tmp_path: Path) -> None:
    """Наличие изменений — не ошибка: команда для конвейера, а не для гейта."""
    root = _copy(sample_solution, tmp_path)
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    manifest, _ = scan(root)
    write_manifest(manifest, first)
    (root / WORKFLOW).unlink()
    manifest, _ = scan(root)
    write_manifest(manifest, second)

    assert runner.invoke(app, ["diff", str(first), str(second)]).exit_code == 0


def test_bad_format_is_rejected(sample_solution: Path, tmp_path: Path) -> None:
    out = tmp_path / "a.json"
    manifest, _ = scan(sample_solution)
    write_manifest(manifest, out)

    result = runner.invoke(app, ["diff", str(out), str(out), "--format", "yaml"])
    assert result.exit_code == 2


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(app, ["diff", str(tmp_path / "no.json"), str(tmp_path / "no.json")])
    assert result.exit_code == 2
    assert "Не удалось прочитать манифест" in result.output


def test_format_changes_of_empty_list() -> None:
    assert format_changes([]) == "Изменений нет."
