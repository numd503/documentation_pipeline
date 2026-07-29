"""Кросс-ссылки: резолв FQN в узлы документации (M05).

`dependencies[].target` — это FQN, а `id` узла — модуль плюс FQN плюс арность.
FQN не уникален (на ABP 255 коллизий на 9075 объявлений), поэтому резолв даёт
список, а не узел, и неоднозначность показывается, а не разрешается молча:
выбор одного из кандидатов — это ложь в документации, которая обнаружится нескоро.
"""

from pathlib import Path

import pytest

from docpipe.materialize.build import build_context, build_generated_block, resolve_link
from docpipe.materialize.template import load_templates
from docpipe.model import (
    Dependency,
    DocNode,
    Manifest,
    Module,
    ParserVersions,
    Relation,
    SourceSpan,
    Symbol,
)

GOLDEN = Path("tests/golden/doc-tree.json")


@pytest.fixture
def golden() -> Manifest:
    return Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# На золотом манифесте
# --------------------------------------------------------------------------------------


def test_interface_resolves_through_its_implementation(golden: Manifest) -> None:
    """В наборе правил по умолчанию интерфейсы не документируются, а зависимости
    почти всегда указывают именно на интерфейс. Без этого шага таблица
    зависимостей у каждого сервиса состояла бы из строк «вне дерева»."""
    context = build_context(golden, load_templates(Path("templates")))
    node = next(n for n in golden.nodes if n.title == "PricingController")

    block = build_generated_block(node, context)

    assert "[PricingService](../services/pricing-service.md) — реализация интерфейса" in block


def test_no_link_contains_a_backslash(golden: Manifest) -> None:
    """`posixpath.relpath`, а не `os.path.relpath`: последний на Windows даёт
    `..\\services\\…`, и дерево разошлось бы с собранным в CI."""
    context = build_context(golden, load_templates(Path("templates")))

    for node in golden.nodes:
        assert "\\" not in build_generated_block(node, context)


def test_reversed_manifest_gives_the_same_result(golden: Manifest) -> None:
    templates = load_templates(Path("templates"))
    forward = build_context(golden, templates)
    backward = build_context(
        golden.model_copy(update={"nodes": list(reversed(golden.nodes))}), templates
    )
    node = next(n for n in golden.nodes if n.title == "PricingController")

    assert build_generated_block(node, forward) == build_generated_block(node, backward)


# --------------------------------------------------------------------------------------
# Неоднозначность: один FQN в двух модулях
# --------------------------------------------------------------------------------------


def _node(module: str, fqn: str, *, deps: list[str] | None = None, refs: bool = False) -> DocNode:
    name = fqn.rsplit(".", 1)[-1]
    return DocNode(
        id=f"type:src/{module}/{module}.csproj#{fqn}`0",
        kind="service",
        template="service",
        title=f"{name}@{module}" if not refs else name,
        doc_path=f"docs/modules/{module}/services/{name.lower()}-{module.lower()}.md",
        parent=f"module:src/{module}/{module}.csproj",
        module=module,
        domain=module,
        symbol=Symbol(
            fqn=fqn,
            name=name,
            type_kind="class",
            namespace=fqn.rsplit(".", 1)[0],
            module=module,
            sources=[SourceSpan(path=f"src/{module}/{name}.cs", start=1, end=5)],
        ),
        dependencies=[
            Dependency(target=t, via="constructor", confidence="high") for t in deps or []
        ],
        signature_hash="sha256:0",
        impl_hash="sha256:0",
    )


def _manifest(nodes: list[DocNode], references: dict[str, list[str]] | None = None) -> Manifest:
    names = sorted({node.module for node in nodes})
    return Manifest(
        ruleset_version="test",
        parser=ParserVersions(tree_sitter="0.26.0", grammar_c_sharp="0.23.5"),
        modules=[
            Module(
                id=f"module:src/{name}/{name}.csproj",
                name=name,
                csproj=f"src/{name}/{name}.csproj",
                project_references=[
                    f"src/{other}/{other}.csproj" for other in (references or {}).get(name, [])
                ],
                domain=name,
                enrolled=True,
            )
            for name in names
        ],
        nodes=nodes,
    )


def test_same_module_candidate_wins() -> None:
    caller = _node("A", "N.Caller", deps=["N.Target"])
    manifest = _manifest([caller, _node("A", "N.Target"), _node("B", "N.Target")])
    context = build_context(manifest, {})

    links = resolve_link("N.Target", caller, context)

    assert [link.node.module for link in links] == ["A"]


def test_referenced_module_wins_when_own_module_has_none() -> None:
    """Сужение по `project_references` — один шаг, не транзитивное замыкание."""
    caller = _node("A", "N.Caller", deps=["N.Target"])
    manifest = _manifest(
        [caller, _node("B", "N.Target"), _node("C", "N.Target")], references={"A": ["B"]}
    )
    context = build_context(manifest, {})

    links = resolve_link("N.Target", caller, context)

    assert [link.node.module for link in links] == ["B"]


def test_ambiguity_is_shown_not_resolved() -> None:
    """Выбор одного из кандидатов — ложь в документации, которая обнаружится нескоро."""
    caller = _node("A", "N.Caller", deps=["N.Target"])
    manifest = _manifest([caller, _node("B", "N.Target"), _node("C", "N.Target")])
    context = build_context(manifest, {})

    links = resolve_link("N.Target", caller, context)
    block = build_generated_block(caller, context)

    assert [link.node.doc_path for link in links] == sorted(link.node.doc_path for link in links)
    assert [link.node.module for link in links] == ["B", "C"]
    assert "(B)" in block and "(C)" in block


def test_unknown_target_is_outside_the_tree() -> None:
    caller = _node("A", "N.Caller", deps=["N.Missing"])
    context = build_context(_manifest([caller]), {})

    assert resolve_link("N.Missing", caller, context) == []
    assert "вне дерева документации" in build_generated_block(caller, context)


def test_self_reference_is_dropped() -> None:
    """Тип, зависящий от себя через DI-регистрацию, дал бы ссылку
    на собственный файл."""
    caller = _node("A", "N.Caller", deps=["N.Caller"])
    context = build_context(_manifest([caller]), {})

    assert resolve_link("N.Caller", caller, context) == []


def test_relation_is_linked_too() -> None:
    caller = _node("A", "N.Caller")
    caller = caller.model_copy(
        update={"related": [Relation(target="N.Target", relation="implements")]}
    )
    manifest = _manifest([caller, _node("B", "N.Target")])
    context = build_context(manifest, {})

    block = build_generated_block(caller, context)
    tail = block[block.index("### Связи") :]

    assert "implements" in tail
    assert "target-b.md" in tail
