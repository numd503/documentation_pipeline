"""Принадлежность узла странице (P07).

Правило одно: узел поглощается страницей, только если достижим **единственной**
страницей. Порога нет и быть не может: «общий» — это «появился второй
потребитель», а не «потребителей больше N». Замер на боевом модуле объясняет
почему: 43 эндпоинта из 61 достижимы ровно с четырёх страниц, и попытка
приписать сервис «главной» странице продублировала бы его в четырёх документах.
"""

from pathlib import Path

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.model import DocNode, Manifest, SourceSpan, Symbol, Usage
from docpipe.web.absorb import absorb
from docpipe.web.tree import run as run_web

RULES = Path("rules/rules.yaml")


@pytest.fixture
def manifest(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


def _owner(manifest: Manifest, title: str) -> str:
    node = next(item for item in manifest.nodes if item.title == title)
    if not node.absorbed_by:
        return ""
    return next(item.title for item in manifest.nodes if item.id == node.absorbed_by)


def _node(name: str, kind: str = "service", uses: list[tuple[str, str]] | None = None) -> DocNode:
    return DocNode(
        id=f"type:src#{name}",
        kind=kind,
        template="service",
        title=name,
        doc_path=f"docs/{name}.md",
        module="src",
        domain="src",
        signature_hash="sha256:0",
        symbol=Symbol(
            fqn=name,
            name=name,
            namespace="src",
            module="src",
            type_kind="class",
            sources=[SourceSpan(path=f"{name}.ts", start=1, end=10)],
        ),
        uses=[Usage(target=target, member=member) for target, member in (uses or [])],
    )


# --------------------------------------------------------------------------------------
# На фикстуре
# --------------------------------------------------------------------------------------


def test_service_called_by_one_page_is_absorbed(manifest: Manifest) -> None:
    assert _owner(manifest, "ModelService") == "DetailComponent"
    assert _owner(manifest, "ItemsService") == "QuizComponent"


def test_service_called_by_two_pages_stays_on_its_own(manifest: Manifest) -> None:
    """`AuditService` зовут `ListComponent` и `QuizComponent`.

    Приписать его одной значило бы завести две копии одного текста, и они
    разошлись бы на первой правке.
    """
    assert _owner(manifest, "AuditService") == ""


def test_state_is_absorbed_through_the_dispatch_chain(manifest: Manifest) -> None:
    """Достижимость считается по вызовам, а `Store` — внешний тип.

    Без цепочки NGXS стейт не был бы достижим ни от одной страницы и остался
    бы самостоятельным документом при единственном потребителе.
    """
    assert _owner(manifest, "DebtState") == "ListComponent"
    assert _owner(manifest, "InnerDebtService") == "ListComponent"


def test_pages_are_never_absorbed(manifest: Manifest) -> None:
    """Вложить экран в экран значило бы потерять его якорь."""
    assert all(not node.absorbed_by for node in manifest.nodes if node.kind == "page")


def test_node_nobody_calls_stays_on_its_own(manifest: Manifest) -> None:
    assert _owner(manifest, "UrlDecoratorService") == ""


# --------------------------------------------------------------------------------------
# Правило
# --------------------------------------------------------------------------------------


def test_transitive_reach_absorbs_the_whole_chain() -> None:
    """Утилита, которую зовёт сервис страницы, принадлежит той же странице.

    Глубина не ограничена намеренно: иначе принадлежность зависела бы
    от длины цепочки, а не от того, кто ею пользуется.
    """
    nodes = absorb(
        [
            _node("Page", kind="page", uses=[("Service", "load")]),
            _node("Service", uses=[("Util", "format")]),
            _node("Util"),
        ]
    )
    owners = {node.title: node.absorbed_by for node in nodes}

    assert owners["Service"] == "type:src#Page"
    assert owners["Util"] == "type:src#Page"


def test_second_page_makes_the_node_shared() -> None:
    nodes = absorb(
        [
            _node("First", kind="page", uses=[("Shared", "load")]),
            _node("Second", kind="page", uses=[("Shared", "load")]),
            _node("Shared"),
        ]
    )

    assert {node.title: node.absorbed_by for node in nodes}["Shared"] == ""


def test_cycle_does_not_hang() -> None:
    nodes = absorb(
        [
            _node("Page", kind="page", uses=[("A", "x")]),
            _node("A", uses=[("B", "y")]),
            _node("B", uses=[("A", "x")]),
        ]
    )

    assert {node.title: node.absorbed_by for node in nodes}["B"] == "type:src#Page"


def test_result_does_not_depend_on_node_order() -> None:
    nodes = [
        _node("Page", kind="page", uses=[("A", "x")]),
        _node("A"),
        _node("Other", kind="page", uses=[("B", "y")]),
        _node("B"),
    ]
    straight = {node.title: node.absorbed_by for node in absorb(nodes)}
    reversed_ = {node.title: node.absorbed_by for node in absorb(list(reversed(nodes)))}

    assert straight == reversed_
