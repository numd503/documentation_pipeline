"""План шага 2: статусы, решения, сироты, переносы (M07).

План — чистая функция. Ни один тест здесь ничего не записывает через `apply`:
всё, что нужно проверить, видно в самом плане, и это ровно то свойство, ради
которого он отделён от записи.
"""

import os
import stat
from pathlib import Path

import pytest

from docpipe.materialize.build import build_context
from docpipe.materialize.plan import (
    ExistingDoc,
    PlanOptions,
    build_plan,
    layout_drift,
    match_relocations,
    scan_docs,
    shadowed_docs,
)
from docpipe.materialize.template import DEFAULT_TEMPLATE, load_templates
from docpipe.model import Manifest

GOLDEN = Path("tests/golden/doc-tree.json")
EXAMPLES = frozenset({"controller", "service", "provider", "workflow"})


@pytest.fixture
def manifest() -> Manifest:
    return Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def templates():  # type: ignore[no-untyped-def]
    return load_templates(Path("templates"))


@pytest.fixture
def context(manifest: Manifest, templates):  # type: ignore[no-untyped-def]
    return build_context(manifest, templates, EXAMPLES)


def _plan(manifest, templates, context, root: Path, **kwargs):  # type: ignore[no-untyped-def]
    existing = scan_docs(root, "docs")
    return build_plan(manifest, existing, templates, context, options=PlanOptions(**kwargs))


def _apply(plan, root: Path) -> None:  # type: ignore[no-untyped-def]
    """Минимальная запись — только чтобы было что читать следующим планом.
    Настоящая запись со всеми гарантиями — задача M08."""
    for doc in plan.documents:
        if doc.content is None:
            continue
        path = root / doc.doc_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc.content, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------
# Пустое дерево и повторный прогон
# --------------------------------------------------------------------------------------


def test_empty_tree_gives_six_creates(manifest, templates, context, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    plan = _plan(manifest, templates, context, tmp_path)

    assert plan.errors == []
    assert len(plan.documents) == 6
    assert {doc.file_action for doc in plan.documents} == {"create"}
    assert {doc.status for doc in plan.documents} == {"missing"}
    assert {doc.agent_action for doc in plan.documents} == {"write"}


def test_second_plan_is_unchanged(manifest, templates, context, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)

    plan = _plan(manifest, templates, context, tmp_path)

    assert {doc.file_action for doc in plan.documents} == {"unchanged"}
    assert all(doc.content is None for doc in plan.documents)


def test_fresh_documents_are_empty_and_need_writing(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)

    plan = _plan(manifest, templates, context, tmp_path)

    assert {doc.status for doc in plan.documents} == {"empty"}
    assert {doc.agent_action for doc in plan.documents} == {"write"}


# --------------------------------------------------------------------------------------
# Жизненный цикл одного документа
# --------------------------------------------------------------------------------------


def _fill(root: Path, doc_path: str) -> None:
    path = root / doc_path
    text = path.read_text(encoding="utf-8")
    for name in ("purpose", "api", "behaviour", "collaboration", "notes"):
        text = text.replace(
            f"<!-- docpipe:section:end {name} -->",
            f"Написанный человеком текст.\n<!-- docpipe:section:end {name} -->",
        )
    path.write_text(text, encoding="utf-8")


def _accept(root: Path, doc_path: str, node) -> None:  # type: ignore[no-untyped-def]
    path = root / doc_path
    members = sorted({member.name for member in node.symbol.members})
    text = path.read_text(encoding="utf-8").replace(
        "docpipe_state:\n  accepted: null\n  review: null\n",
        "docpipe_state:\n"
        "  accepted:\n"
        f"    signature_hash: {node.signature_hash}\n"
        f"    impl_hash: {node.impl_hash}\n"
        f"    kind: {node.kind}\n"
        f"    members: [{', '.join(members)}]\n"
        "  review: null\n",
    )
    path.write_text(text, encoding="utf-8")


CONTROLLER = "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"


def _one(plan, doc_path: str):  # type: ignore[no-untyped-def]
    return next(doc for doc in plan.documents if doc.doc_path == doc_path)


def test_lifecycle_undeclared_then_current(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    _fill(tmp_path, CONTROLLER)

    plan = _plan(manifest, templates, context, tmp_path)
    assert _one(plan, CONTROLLER).status == "undeclared"
    assert _one(plan, CONTROLLER).agent_action == "review"

    node = next(n for n in manifest.nodes if n.doc_path == CONTROLLER)
    _accept(tmp_path, CONTROLLER, node)

    plan = _plan(manifest, templates, context, tmp_path)
    assert _one(plan, CONTROLLER).status == "current"
    assert _one(plan, CONTROLLER).agent_action == "skip"


def test_signature_change_makes_it_stale(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    _fill(tmp_path, CONTROLLER)
    node = next(n for n in manifest.nodes if n.doc_path == CONTROLLER)
    _accept(tmp_path, CONTROLLER, node)

    changed = manifest.model_copy(
        update={
            "nodes": [
                n.model_copy(update={"signature_hash": "sha256:changed"})
                if n.doc_path == CONTROLLER
                else n
                for n in manifest.nodes
            ]
        }
    )
    plan = _plan(changed, templates, build_context(changed, templates, EXAMPLES), tmp_path)

    assert _one(plan, CONTROLLER).status == "stale"
    assert _one(plan, CONTROLLER).agent_action == "write"
    assert "контракт изменился" in _one(plan, CONTROLLER).reason


def test_impl_change_alone_makes_it_drifted(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Реализация изменилась, контракт тот же — это `review`, а не `write`."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    _fill(tmp_path, CONTROLLER)
    node = next(n for n in manifest.nodes if n.doc_path == CONTROLLER)
    _accept(tmp_path, CONTROLLER, node)

    changed = manifest.model_copy(
        update={
            "nodes": [
                n.model_copy(update={"impl_hash": "sha256:changed"})
                if n.doc_path == CONTROLLER
                else n
                for n in manifest.nodes
            ]
        }
    )
    plan = _plan(changed, templates, build_context(changed, templates, EXAMPLES), tmp_path)

    assert _one(plan, CONTROLLER).status == "drifted"
    assert _one(plan, CONTROLLER).agent_action == "review"
    assert "реализация изменилась" in _one(plan, CONTROLLER).reason


# --------------------------------------------------------------------------------------
# Сохранность и слияние
# --------------------------------------------------------------------------------------


def test_authored_text_survives_a_new_template_section(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Секции шаблона, которых в файле нет, дописываются в КОНЕЦ: вставка
    по порядку требует угадать позицию относительно обвязки, которую человек
    мог переписать."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    _fill(tmp_path, CONTROLLER)

    path = tmp_path / CONTROLLER
    text = path.read_text(encoding="utf-8")
    start = text.index("<!-- docpipe:section:start notes -->")
    end = text.index("<!-- docpipe:section:end notes -->") + len(
        "<!-- docpipe:section:end notes -->\n"
    )
    path.write_text(text[:start] + text[end:], encoding="utf-8")

    plan = _plan(manifest, templates, context, tmp_path)
    doc = _one(plan, CONTROLLER)

    assert doc.file_action == "update"
    assert doc.content is not None
    assert doc.content.count("Написанный человеком текст.") == 4
    assert doc.content.rstrip().endswith("<!-- docpipe:section:end notes -->")
    assert "notes" in doc.empty_sections


def test_orphan_sections_are_reported_not_removed(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)

    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8") + "\n<!-- docpipe:section:start responsibilities -->\n"
        "Осталось от прошлого шаблона.\n"
        "<!-- docpipe:section:end responsibilities -->\n",
        encoding="utf-8",
    )

    doc = _one(_plan(manifest, templates, context, tmp_path), CONTROLLER)

    assert doc.orphan_sections == ["responsibilities"]
    assert doc.content is not None
    assert "Осталось от прошлого шаблона." in doc.content


def test_foreign_front_matter_keys_survive(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)

    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "docpipe_state:", "zeta: 1\nalpha: 2\ndocpipe_state:", 1
        ),
        encoding="utf-8",
    )

    doc = _one(_plan(manifest, templates, context, tmp_path), CONTROLLER)

    assert doc.content is not None
    assert doc.content.index("alpha:") < doc.content.index("zeta:")


# --------------------------------------------------------------------------------------
# Границы дерева
# --------------------------------------------------------------------------------------


def test_broken_document_is_refused_not_a_blocking_error(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Отказ по одному файлу, а не блокирующая ошибка всего прогона."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace("<!-- docpipe:section:end notes -->", ""),
        encoding="utf-8",
    )

    plan = _plan(manifest, templates, context, tmp_path)
    broken = (
        _one(plan, CONTROLLER)
        if False
        else next(doc for doc in plan.documents if doc.status == "broken")
    )

    assert plan.errors == []
    assert broken.file_action == "refuse"
    assert broken.error is not None


def test_foreign_markdown_never_becomes_an_orphan(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Воспроизводит раскладку АС CF: инструмент лежит внутри `docs/`."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)

    (tmp_path / "docs/index.md").write_text("# Написано руками\n", encoding="utf-8")
    venv = tmp_path / "docs/ml/docspipe/.venv/lib/python3.12/site-packages/pkg"
    venv.mkdir(parents=True)
    (venv / "README.md").write_text("---\ntitle: чужое\n---\n", encoding="utf-8")

    plan = _plan(manifest, templates, context, tmp_path)

    assert not any(doc.status == "orphan" for doc in plan.documents)
    assert len(plan.documents) == 6


def test_business_document_is_not_ours(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Формат зон и ключ `docpipe` у слоёв общие — различает их только `schema`.

    Без проверки схемы весь бизнес-каталог стал бы сиротами шага 2.
    """
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    (tmp_path / "docs/business").mkdir(parents=True)
    (tmp_path / "docs/business/p.md").write_text(
        "---\ndocpipe:\n  schema: business/1\n  id: bp.a.b\n  node_id: whatever\n---\n",
        encoding="utf-8",
    )

    plan = _plan(manifest, templates, context, tmp_path)

    assert not any(doc.status == "orphan" for doc in plan.documents)


def test_orphan_is_reported_with_its_own_team(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """У сироты узла нет, поэтому команда берётся из её собственного front
    matter; иначе она была бы молча приписана текущей."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)

    # Документ исчезнувшего узла: лежит не на пути ни одного узла и ни с чем
    # не сопоставляется — ни по источникам, ни по impl_hash.
    gone = tmp_path / "docs/modules/controllers/Sample.Pricing.Api/gone.md"
    gone.write_text(
        "---\n"
        "docpipe:\n"
        "  schema: materialize/1\n"
        "  node_id: type:src/X/X.csproj#X.Gone`0\n"
        "  impl_hash: sha256:zzz\n"
        "  team: risk\n"
        "  sources:\n"
        "  - {path: src/X/Gone.cs, start: 1, end: 2}\n"
        "docpipe_state:\n"
        "  accepted: null\n"
        "---\n",
        encoding="utf-8",
    )

    plan = _plan(manifest, templates, context, tmp_path)
    orphan = next(doc for doc in plan.documents if doc.status == "orphan")

    assert orphan.team == "risk"
    assert orphan.file_action == "unchanged"


def test_team_filter_narrows_writes_but_not_comparison(  # type: ignore[no-untyped-def]
    manifest, templates, tmp_path: Path
) -> None:
    """Фильтр сужает множество того, что пишется; множество, с которым
    сравнивают, — никогда. Иначе `--team pricing` объявил бы сиротами
    документацию всех остальных команд."""
    from docpipe.materialize.ownership import load_ownership

    ownership_file = tmp_path / "ownership.yaml"
    ownership_file.write_text(
        'version: "1"\n'
        "teams: [{id: pricing, title: P}, {id: risk, title: R}]\n"
        "rules:\n"
        "  - {id: p, team: pricing, priority: 10, when: {kind: [controller]}}\n"
        "  - {id: r, team: risk, priority: 10,\n"
        "     when: {kind: [service, provider, workflow, ignite_service]}}\n",
        encoding="utf-8",
    )
    ownership = load_ownership(ownership_file)
    context = build_context(manifest, templates, EXAMPLES)

    full = build_plan(manifest, [], templates, context, ownership)
    narrowed = build_plan(
        manifest, [], templates, context, ownership, PlanOptions(teams=("pricing",))
    )

    assert len(full.documents) == 6
    assert len(narrowed.documents) == 2
    assert {doc.team for doc in narrowed.documents} == {"pricing"}
    assert not any(doc.status == "orphan" for doc in narrowed.documents)


# --------------------------------------------------------------------------------------
# Блокирующие ошибки
# --------------------------------------------------------------------------------------


def test_two_nodes_on_one_path_block_everything(manifest, templates, context) -> None:  # type: ignore[no-untyped-def]
    """Без проверки второй узел молча затрёт первый, и потеряется не пустой
    скелет, а написанный документ."""
    first = manifest.nodes[0]
    clash = manifest.nodes[1].model_copy(update={"doc_path": first.doc_path})
    broken = manifest.model_copy(update={"nodes": [first, clash]})

    plan = build_plan(broken, [], templates, context)

    assert plan.documents == []
    assert any("на один путь претендуют" in error for error in plan.errors)


def test_case_only_difference_blocks(manifest, templates, context) -> None:  # type: ignore[no-untyped-def]
    """На macOS и Windows файловая система регистронезависима, а имя модуля
    в `doc_path` не слагифицируется. `docpipe validate` этого не ловит."""
    first = manifest.nodes[0]
    clash = manifest.nodes[1].model_copy(update={"doc_path": first.doc_path.upper()})
    broken = manifest.model_copy(update={"nodes": [first, clash]})

    plan = build_plan(broken, [], templates, context)

    assert any("различаются только регистром" in error for error in plan.errors)


def test_missing_template_blocks_only_without_the_default(manifest, templates, context) -> None:  # type: ignore[no-untyped-def]
    """Отказ остаётся там, где подставить нечего, и только там.

    Без базового скелета молчаливой подстановки «ничего» быть не должно:
    прогон отменяется целиком, как и раньше.
    """
    broken = manifest.model_copy(
        update={"nodes": [manifest.nodes[0].model_copy(update={"template": "нет-такого"})]}
    )
    without_default = {name: tpl for name, tpl in templates.items() if name != DEFAULT_TEMPLATE}

    plan = build_plan(broken, [], without_default, context)

    assert any("нет шаблонов: нет-такого" in error for error in plan.errors)
    assert not plan.documents


def test_unknown_template_falls_back_to_the_default(manifest, templates, context) -> None:  # type: ignore[no-untyped-def]
    """Узел документируется базовым скелетом, а не роняет весь прогон.

    Первое же своё правило классификации на чужом репозитории приносит новый
    `template`; отказ здесь означал бы, что дерева документации нет вовсе.
    """
    node = manifest.nodes[0].model_copy(update={"template": "нет-такого"})
    changed = manifest.model_copy(update={"nodes": [node, *manifest.nodes[1:]]})

    plan = build_plan(changed, [], templates, context)

    assert not plan.errors
    assert len(plan.documents) == len(manifest.nodes)
    assert plan.substituted == {"нет-такого": 1}


def test_substitution_is_reported_by_size_then_name(manifest, templates, context) -> None:  # type: ignore[no-untyped-def]
    """Подстановка обязана быть видимой числом: опечатку в `template` ловил отказ.

    Порядок — явный ключ: цифры идут в отчёт, а отчёты сравнивают между прогонами.
    """
    nodes = [
        node.model_copy(update={"template": "яяя" if index else "ааа"})
        for index, node in enumerate(manifest.nodes)
    ]

    plan = build_plan(manifest.model_copy(update={"nodes": nodes}), [], templates, context)

    assert list(plan.substituted) == ["яяя", "ааа"]
    assert plan.substituted["яяя"] == len(nodes) - 1


def test_default_document_points_at_the_applied_template(manifest, templates, context) -> None:  # type: ignore[no-untyped-def]
    """Иначе агент шага 3 пойдёт читать файл, которого нет."""
    node = manifest.nodes[0].model_copy(update={"template": "нет-такого"})
    changed = manifest.model_copy(update={"nodes": [node]})

    plan = build_plan(changed, [], templates, context)
    text = plan.documents[0].content or ""

    assert f"template_ref: templates/{DEFAULT_TEMPLATE}.md" in text
    assert "template: нет-такого" in text  # запрошенный вид не подменяется
    assert "example_ref: null" in text


def test_two_files_with_one_node_id_block(manifest, templates, context, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    original = (tmp_path / CONTROLLER).read_text(encoding="utf-8")
    (tmp_path / "docs/copy.md").write_text(original, encoding="utf-8")

    plan = build_plan(manifest, scan_docs(tmp_path, "docs"), templates, context)

    assert any("один узел в нескольких файлах" in error for error in plan.errors)


# --------------------------------------------------------------------------------------
# Автоперенос
# --------------------------------------------------------------------------------------


def _doc(path: str, node_id: str, *, sources: list[str] = (), impl: str = "") -> ExistingDoc:  # type: ignore[assignment]
    from docpipe.documents.zones import parse_document

    lines = [
        "---",
        "docpipe:",
        "  schema: materialize/1",
        f"  node_id: {node_id}",
        f"  impl_hash: {impl or 'sha256:x'}",
        "  sources:",
    ]
    lines += [f"  - {{path: {source}, start: 1, end: 2}}" for source in sources]
    lines += ["docpipe_state:", "  accepted: null", "---", ""]
    text = "\n".join(lines)
    return ExistingDoc(path=path, text=text, parsed=parse_document(text))


def test_reclassification_is_an_exact_relocation(manifest) -> None:  # type: ignore[no-untyped-def]
    """Правило переклассифицировало тип: `doc_path` изменился, `node_id` — нет."""
    node = manifest.nodes[0]
    orphan = _doc("docs/modules/X/services/old.md", node.id)

    matched, notes = match_relocations([node], [orphan])

    assert matched[node.id][1] == "exact"
    assert notes == []


def test_rename_with_the_same_file_is_high_confidence(manifest) -> None:  # type: ignore[no-untyped-def]
    node = next(n for n in manifest.nodes if n.symbol and n.symbol.sources)
    orphan = _doc(
        "docs/modules/X/services/old.md",
        "type:другой#Старый`0",
        sources=[node.symbol.sources[0].path],
    )

    matched, _ = match_relocations([node], [orphan])

    assert matched[node.id][1] == "high"


def test_two_candidates_stay_unmoved(manifest) -> None:  # type: ignore[no-untyped-def]
    """Требование «пара единственная в обе стороны» снимает главный риск —
    два типа, обменявшихся именами."""
    node = next(n for n in manifest.nodes if n.symbol and n.symbol.sources)
    path = node.symbol.sources[0].path
    orphans = [
        _doc("docs/a.md", "type:a#A`0", sources=[path]),
        _doc("docs/b.md", "type:b#B`0", sources=[path]),
    ]

    matched, notes = match_relocations([node], orphans)

    assert matched == {}
    assert any("кандидатов на перенос несколько" in note for note in notes)


def test_no_signal_stays_orphan(manifest) -> None:  # type: ignore[no-untyped-def]
    node = manifest.nodes[0]
    orphan = _doc("docs/a.md", "type:a#A`0", sources=["src/Совсем/Другое.cs"], impl="sha256:zzz")

    matched, notes = match_relocations([node], [orphan])

    assert matched == {}
    assert notes == []


def test_relocated_document_keeps_its_text(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    _fill(tmp_path, CONTROLLER)
    _accept(tmp_path, CONTROLLER, next(n for n in manifest.nodes if n.doc_path == CONTROLLER))

    moved = manifest.model_copy(
        update={
            "nodes": [
                n.model_copy(
                    update={
                        "doc_path": "docs/modules/services/Sample.Pricing.Api/pricing-controller.md"
                    }
                )
                if n.doc_path == CONTROLLER
                else n
                for n in manifest.nodes
            ]
        }
    )
    plan = _plan(moved, templates, build_context(moved, templates, EXAMPLES), tmp_path)
    doc = next(d for d in plan.documents if d.relocate_from)

    assert doc.confidence == "exact"
    assert doc.relocate_from == CONTROLLER
    assert doc.file_action == "relocate"
    assert doc.status == "relocated"
    assert doc.content is not None and "Написанный человеком текст." in doc.content
    assert not any(d.status == "orphan" for d in plan.documents)


# --------------------------------------------------------------------------------------
# Раскладка манифеста против текущей конфигурации
# --------------------------------------------------------------------------------------


def test_layout_drift_is_silent_when_prefix_matches(manifest: Manifest) -> None:
    assert layout_drift(manifest, "docs/modules") is None


def test_layout_drift_not_checked_without_modules_root(manifest: Manifest) -> None:
    """Пустая строка — «не проверять»: план строят и там, где конфигурации нет."""
    assert layout_drift(manifest, "") is None


def test_layout_drift_names_the_expected_prefix_and_an_example(manifest: Manifest) -> None:
    message = layout_drift(manifest, "docs/ml/tech-docs")
    assert message is not None
    assert "docs/ml/tech-docs/" in message
    assert "docs/modules/" in message
    assert "scan" in message


def test_layout_drift_blocks_the_plan(manifest, templates, context, tmp_path: Path) -> None:
    """Расхождение — блокирующая ошибка, а не замечание в отчёте.

    Иначе `materialize` пишет документы по путям манифеста, `worklist` кладёт
    в очередь `modules_root` из конфигурации, и внешний исполнитель получает
    префикс, которого в очереди нет. Раньше это проходило молча.
    """
    plan = _plan(manifest, templates, context, tmp_path, modules_root="docs/ml/tech-docs")
    assert plan.errors
    assert plan.documents == []

    ok = _plan(manifest, templates, context, tmp_path, modules_root="docs/modules")
    assert ok.errors == []
    assert ok.documents


# --------------------------------------------------------------------------------------
# Документ, которого обход не увидел
# --------------------------------------------------------------------------------------
#
# Все случаи здесь про одно и то же: файл на `doc_path` лежит, а `scan_docs`
# его не вернул. Узел при этом получал `missing`, `missing` означает `create`,
# и написанный документ переписывался пустым скелетом без единого сообщения.


def _first(manifest: Manifest) -> str:
    return sorted(node.doc_path for node in manifest.nodes)[0]


def test_bom_does_not_hide_a_document(manifest, templates, context, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Документ, пересохранённый Блокнотом, остаётся своим.

    `read_document` читает `utf-8-sig` и это проверено отдельно, но отсев
    по первым байтам стоял ДО него и до разбора такой файл не доезжал.
    """
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    target = _first(manifest)
    path = tmp_path / target
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    existing = scan_docs(tmp_path, "docs")

    assert target in {doc.path for doc in existing}
    assert shadowed_docs(tmp_path, manifest, existing) == []


def test_unreadable_document_is_broken_not_missing(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Нечитаемый файл — отказ с причиной, а не «файла нет».

    Молчаливый пропуск здесь означал бы перезапись документа, прочитать
    который не удалось.
    """
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    target = _first(manifest)
    path = tmp_path / target
    os.chmod(path, 0)
    try:
        existing = scan_docs(tmp_path, "docs")
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    doc = next(item for item in existing if item.path == target)
    assert doc.error is not None
    assert "не читается" in doc.error


def test_document_without_schema_is_refused_not_recreated(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Отбор по `docpipe.schema` нужен (иначе бизнес-каталог станет сиротами),
    но выпавший из-за него файл обязан быть назван, а не затёрт."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    target = _first(manifest)
    path = tmp_path / target
    path.write_text(
        path.read_text(encoding="utf-8").replace("  schema: materialize/1\n", ""),
        encoding="utf-8",
    )

    existing = scan_docs(tmp_path, "docs")
    shadowed = shadowed_docs(tmp_path, manifest, existing)
    plan = build_plan(
        manifest, existing, templates, context, options=PlanOptions(shadowed=tuple(shadowed))
    )

    assert shadowed == [target]
    doc = next(item for item in plan.documents if item.doc_path == target)
    assert (doc.file_action, doc.status, doc.agent_action) == ("refuse", "broken", "review")
    # Содержимого нет намеренно: `--force` перезаписывает только то, для чего
    # план его собрал, а что лежит в этом файле — инструменту неизвестно.
    assert doc.content is None


def test_symlinked_directory_is_caught_by_the_probe(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """`rglob` в каталоги за симлинком не заходит, и обход документ не вернёт.
    Проверка по диску видит его всё равно — потому она и по диску."""
    _apply(_plan(manifest, templates, context, tmp_path), tmp_path)
    target = _first(manifest)
    original = (tmp_path / target).parent
    moved = tmp_path / "elsewhere"
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=True)

    existing = scan_docs(tmp_path, "docs")

    assert (tmp_path / target).is_file()
    assert target not in {doc.path for doc in existing}
    assert shadowed_docs(tmp_path, manifest, existing) == [target]


def test_docs_scan_exclude_over_the_tree_blocks_the_run(  # type: ignore[no-untyped-def]
    manifest, templates, context, tmp_path: Path
) -> None:
    """Шаблон обхода, накрывший само дерево документов, — блокирующая ошибка.

    Пара `docs_root` + `modules_dir` держит инвариант «пишем туда, где ищем»
    структурно, а `docs_scan_exclude` обходит его с другой стороны: прогон
    остаётся вечно `missing` и переписывает всё дерево каждый раз.
    """
    plan = build_plan(
        manifest,
        [],
        templates,
        context,
        options=PlanOptions(docs_scan_exclude=("docs/modules/**",)),
    )

    assert plan.documents == []
    assert any("docs_scan_exclude" in error for error in plan.errors)
    assert any("docs/modules/**" in error for error in plan.errors)
