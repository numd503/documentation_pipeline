"""Владение на узлах фронта (F16).

Проверяется не то, что механизм работает вообще, а какие его предикаты
на фронте **вырождаются**: набор правил, написанный по .NET-привычке, даст
ноль совпадений и будет выглядеть сломанным инструментом.
"""

from pathlib import Path

import pytest
import yaml

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.materialize.ownership import Ownership, lint, load_ownership, owner_of
from docpipe.model import DocNode, Manifest
from docpipe.web.tree import run as run_web

RULES = Path("rules/web.yaml")


@pytest.fixture
def manifest(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES)).manifest


def _ownership(tmp_path: Path, rules: list[dict[str, object]]) -> Ownership:
    path = tmp_path / "ownership.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "ownership_version": "test",
                "teams": [{"id": "ml", "title": "ML"}, {"id": "platform", "title": "Платформа"}],
                "rules": rules,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return load_ownership(path)


def _node(manifest: Manifest, title: str) -> DocNode:
    return next(node for node in manifest.nodes if node.title == title)


# --------------------------------------------------------------------------------------
# Предикаты, которые работают как есть
# --------------------------------------------------------------------------------------


def test_path_glob_owns_a_directory_of_the_front_end(manifest: Manifest, tmp_path: Path) -> None:
    ownership = _ownership(
        tmp_path,
        [
            {
                "id": "ml.inner-debt",
                "team": "ml",
                "priority": 50,
                "when": {"path_glob": ["src/app/inner-debt/**"]},
            }
        ],
    )

    assert owner_of(_node(manifest, "DebtState"), ownership).team == "ml"
    assert owner_of(_node(manifest, "AuditService"), ownership).team is None


def test_module_glob_matches_the_boundary_directory(manifest: Manifest, tmp_path: Path) -> None:
    """`module_glob` сравнивает ПУТЬ модуля, а не имя, и на фронте это каталог-граница.

    На .NET там лежит путь `.csproj`, на фронте — `src` либо
    `nx-app/apps/widget`. Правило `module_glob: ["widget"]`, написанное
    по имени проекта, даст ноль совпадений: имя проверяет предикат `module`.
    """
    by_path = _ownership(
        tmp_path,
        [
            {
                "id": "widget",
                "team": "platform",
                "priority": 50,
                "when": {"module_glob": ["nx-app/apps/widget"]},
            }
        ],
    )
    assert owner_of(_node(manifest, "WidgetService"), by_path).team == "platform"
    assert owner_of(_node(manifest, "AuditService"), by_path).team is None

    by_name = _ownership(
        tmp_path,
        [{"id": "widget", "team": "platform", "priority": 50, "when": {"module": ["widget"]}}],
    )
    assert owner_of(_node(manifest, "WidgetService"), by_name).team == "platform"


def test_kind_owns_all_pages(manifest: Manifest, tmp_path: Path) -> None:
    """`kind` работает и здесь: виды фронта — такие же значения, как у .NET."""
    ownership = _ownership(
        tmp_path,
        [{"id": "pages", "team": "ml", "priority": 50, "when": {"kind": ["page"]}}],
    )

    assert owner_of(_node(manifest, "QuizComponent"), ownership).team == "ml"
    assert owner_of(_node(manifest, "ItemsService"), ownership).team is None


def test_attribute_owns_by_decorator(manifest: Manifest, tmp_path: Path) -> None:
    """Декоратор попадает в `attributes` символа, и предикат работает как с атрибутом C#."""
    ownership = _ownership(
        tmp_path,
        [{"id": "states", "team": "ml", "priority": 50, "when": {"attribute": ["State"]}}],
    )

    assert owner_of(_node(manifest, "DebtState"), ownership).team == "ml"


# --------------------------------------------------------------------------------------
# Предикаты, которые на фронте вырождаются
# --------------------------------------------------------------------------------------


def test_namespace_prefix_is_a_directory_not_a_namespace(
    manifest: Manifest, tmp_path: Path
) -> None:
    """В TypeScript пространств имён нет: `namespace` узла — каталог файла.

    Правило `namespace_prefix: ["Sbt.Cashflow"]`, перенесённое с .NET, даст
    ноль совпадений и будет выглядеть сломанным инструментом.
    """
    dotted = _ownership(
        tmp_path,
        [{"id": "dotted", "team": "ml", "priority": 50, "when": {"namespace_prefix": ["src.app"]}}],
    )
    assert owner_of(_node(manifest, "DebtState"), dotted).team is None

    sloped = _ownership(
        tmp_path,
        [{"id": "sloped", "team": "ml", "priority": 50, "when": {"namespace_prefix": ["src/app"]}}],
    )
    assert owner_of(_node(manifest, "DebtState"), sloped).team == "ml"


def test_inherits_is_degenerate_on_the_front_end(manifest: Manifest, tmp_path: Path) -> None:
    """Наследование в Angular редкость: на фикстуре замыкание есть у трёх узлов из 27.

    Правило по контракту типа здесь не столько не работает, сколько описывает
    почти пустое множество, — и это документируется, а не чинится.
    """
    with_closure = [
        node for node in manifest.nodes if node.symbol and node.symbol.base_type_closure
    ]
    assert len(with_closure) <= 3

    ownership = _ownership(
        tmp_path,
        [
            {
                "id": "inherited",
                "team": "ml",
                "priority": 50,
                "when": {"inherits": ["BaseApiService"]},
            }
        ],
    )
    owned = [node for node in manifest.nodes if owner_of(node, ownership).team]
    assert [node.title for node in owned] == ["InnerDebtService"]


# --------------------------------------------------------------------------------------
# Линт
# --------------------------------------------------------------------------------------


def test_lint_gives_no_false_findings_on_a_covered_tree(manifest: Manifest, tmp_path: Path) -> None:
    """Набор, покрывающий оба модуля, не должен давать ни находок, ни мёртвых правил."""
    ownership = _ownership(
        tmp_path,
        [
            {"id": "ml.all", "team": "ml", "priority": 10, "when": {"module_glob": ["src"]}},
            {
                "id": "platform.widget",
                "team": "platform",
                "priority": 10,
                "when": {"module_glob": ["nx-app/**"]},
            },
        ],
    )

    findings, _ = lint(manifest.nodes, ownership)
    assert findings == []


def test_lint_names_a_dead_rule(manifest: Manifest, tmp_path: Path) -> None:
    """Мёртвое правило — самый частый дефект сопровождения, и на фронте тоже."""
    ownership = _ownership(
        tmp_path,
        [
            {
                "id": "ml.all",
                "team": "ml",
                "priority": 10,
                "when": {"module_glob": ["src", "nx-app/**"]},
            },
            {
                "id": "ml.dotnet-habit",
                "team": "ml",
                "priority": 50,
                "when": {"namespace_prefix": ["Sbt.Cashflow.ML"]},
            },
        ],
    )

    findings, _ = lint(manifest.nodes, ownership)
    assert any("ml.dotnet-habit" in finding for finding in findings)
