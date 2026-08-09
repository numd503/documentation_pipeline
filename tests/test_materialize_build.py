"""Front matter и генерируемый блок (M05).

Front matter сверяется с эталоном **байт в байт**: это проекция, по которой
принимают решения скрипты и агент, и любое расхождение в порядке ключей или
в квотировании — изменение формата, а не косметика.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from docpipe.materialize.build import (
    build_context,
    build_front_matter,
    build_generated_block,
    dump_front_matter,
)
from docpipe.materialize.template import load_templates
from docpipe.model import (
    Dependency,
    DocNode,
    Endpoint,
    Manifest,
    Module,
    ParserVersions,
    SourceSpan,
    Symbol,
)

GOLDEN = Path("tests/golden/doc-tree.json")
EXAMPLES = frozenset({"controller", "service", "provider", "workflow"})

# ruff: noqa: E501 — эталон сверяется байт в байт, переносить строки нельзя
EXPECTED_FRONT_MATTER = """---
docpipe:
  schema: materialize/1
  node_id: type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj#Sample.Pricing.Api.Controllers.PricingController`0
  doc_path: docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md
  title: PricingController
  fqn: Sample.Pricing.Api.Controllers.PricingController
  kind: controller
  template: controller
  template_ref: templates/controller.md
  example_ref: templates/examples/controller.md
  module: Sample.Pricing.Api
  module_csproj: src/Sample.Pricing.Api/Sample.Pricing.Api.csproj
  domain: Sample.Pricing.Api
  team: null
  signature_hash: sha256:0e29455f07023ae28072c3bef5b59c2725eead683869ad1b48c23170251613b4
  impl_hash: sha256:74ba5471180f9a88bdbc3e969e8ac5b8a802e1741f504714bbfbafaaf19e50c2
  ruleset_version: 2026-07-30.1
  sources:
  - path: src/Sample.Pricing.Api/Controllers/PricingController.cs
    start: 8
    end: 26
docpipe_state:
  accepted: null
  review: null
---
"""


@pytest.fixture
def manifest() -> Manifest:
    return Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def context(manifest: Manifest):  # type: ignore[no-untyped-def]
    return build_context(manifest, load_templates(Path("templates")), EXAMPLES)


def _node(manifest: Manifest, title: str) -> DocNode:
    return next(node for node in manifest.nodes if node.title == title)


# --------------------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------------------


def test_front_matter_matches_reference(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    text = dump_front_matter(build_front_matter(node, context, None), None)

    assert text == EXPECTED_FRONT_MATTER


def test_backtick_and_hash_survive_the_dumper(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """`node_id` содержит `#` и обратную кавычку. Решение «когда квотировать»
    знает дампер; ручная сборка f-строками — дефект."""
    node = _node(manifest, "PricingController")

    text = dump_front_matter(build_front_matter(node, context, None), None)

    assert "node_id: type:src/" in text
    assert "PricingController`0" in text


def test_partial_keeps_all_sources_in_manifest_order(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingService")

    front_matter = build_front_matter(node, context, None)

    assert [source.path for source in front_matter.sources] == [
        "src/Sample.Pricing.Api/Services/PricingService.Calculations.cs",
        "src/Sample.Pricing.Api/Services/PricingService.cs",
    ]


def test_missing_example_gives_null(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Для Ignite образцов нет намеренно, и врать про них нельзя."""
    node = _node(manifest, "RiskComputeService")

    front_matter = build_front_matter(node, context, None)

    assert front_matter.example_ref is None
    assert front_matter.template_ref == "templates/ignite-service.md"


def test_team_is_always_present(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Отсутствие владельца — видимый факт, а не отсутствие строки."""
    node = _node(manifest, "PricingController")

    assert "team: null" in dump_front_matter(build_front_matter(node, context, None), None)
    assert "team: pricing" in dump_front_matter(build_front_matter(node, context, "pricing"), None)


def test_preserved_keys_go_sorted_after_state(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Их порядок — тот, что человек мог поменять руками; без сортировки
    перестановка двух строк вызывала бы перезапись документа."""
    node = _node(manifest, "PricingController")

    text = dump_front_matter(build_front_matter(node, context, None), None, {"zeta": 1, "alpha": 2})

    assert text.index("docpipe_state:") < text.index("alpha:") < text.index("zeta:")


def test_cyrillic_is_not_escaped(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Аналог `ensure_ascii=False` в `hashing.py`."""
    node = _node(manifest, "PricingController").model_copy(update={"domain": "Ценообразование"})

    text = dump_front_matter(build_front_matter(node, context, "Ценообразование"), None)

    assert "domain: Ценообразование" in text
    assert "\\u04" not in text


def test_long_value_is_not_wrapped(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """PyYAML разрывает значение С ПРОБЕЛАМИ длиннее 80 символов на две строки.

    Без `width=10**9` текст файла стал бы зависеть от версии библиотеки.
    """
    long_title = (
        "Очень длинное название сущности, в котором есть пробелы и оно длиннее восьмидесяти"
    )
    node = _node(manifest, "PricingController").model_copy(update={"title": long_title})

    text = dump_front_matter(build_front_matter(node, context, None), None)

    assert f"title: {long_title}\n" in text


def test_state_is_preserved_as_given(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")
    state = {"accepted": {"signature_hash": "sha256:x"}, "review": None}

    text = dump_front_matter(build_front_matter(node, context, None), state)

    assert "signature_hash: sha256:x" in text


# --------------------------------------------------------------------------------------
# Генерируемый блок
# --------------------------------------------------------------------------------------


def test_generated_block_is_deterministic(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    assert build_generated_block(node, context) == build_generated_block(node, context)


def test_all_sections_are_present_even_when_empty(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Пропуск раздела означал бы, что появление первой зависимости меняет
    и структурные строки документа."""
    node = _node(manifest, "ValuationWorkflow")

    block = build_generated_block(node, context)

    for heading in ("Исходники", "HTTP-эндпоинты", "Зависимости", "Связи", "XML-doc из кода"):
        assert f"### {heading}" in block
    assert block.count("Нет.") >= 3


def test_header_names_shape_module_domain_and_owner(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    header = build_generated_block(node, context, team="pricing").splitlines()[0]

    assert "public sealed class" in header
    assert "модуль `Sample.Pricing.Api`" in header
    assert "владелец `pricing`" in header


def test_owner_absence_is_visible(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    assert "владелец не задан" in build_generated_block(node, context)


def test_sources_are_linked_relative_to_the_document(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    block = build_generated_block(node, context)

    assert "(../../../../src/Sample.Pricing.Api/Controllers/PricingController.cs)" in block
    assert "строки 8–26" in block


def test_endpoints_table(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    block = build_generated_block(node, context)

    assert "| `POST` | `api/v1/Pricing` | `RecalculateAsync` | 24 |" in block
    assert "| `GET` | `api/v1/Pricing/{id:guid}` | `GetAsync` | 18 |" in block


def test_xml_doc_goes_as_a_quote(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")

    assert "> Handles pricing requests." in build_generated_block(node, context)


# --------------------------------------------------------------------------------------
# Экранирование
# --------------------------------------------------------------------------------------


def _artificial() -> tuple[Manifest, DocNode]:
    symbol = Symbol(
        fqn="A.B.Weird",
        name="Weird",
        type_kind="class",
        namespace="A.B",
        module="M",
        modifiers=["public"],
        sources=[SourceSpan(path="src/M/Weird.cs", start=1, end=9)],
        xml_doc="Первая строка.\nВторая | со трубой.",
    )
    node = DocNode(
        id="type:src/M/M.csproj#A.B.Weird`0",
        kind="service",
        template="service",
        title="Weird",
        doc_path="docs/modules/M/services/weird.md",
        parent="module:src/M/M.csproj",
        module="M",
        domain="M",
        symbol=symbol,
        endpoints=[Endpoint(http_method="GET", route="api/v1/a|b", member="Get", line=3)],
        dependencies=[Dependency(target="Some|Type", via="di", confidence="low")],
        signature_hash="sha256:0",
        impl_hash="sha256:0",
    )
    manifest = Manifest(
        ruleset_version="test",
        parser=ParserVersions(tree_sitter="0.26.0", grammar_c_sharp="0.23.5"),
        modules=[
            Module(
                id="module:src/M/M.csproj",
                name="M",
                project_file="src/M/M.csproj",
                lang="cs",
                domain="M",
                enrolled=True,
            )
        ],
        nodes=[node],
    )
    return manifest, node


def test_pipe_in_values_does_not_break_tables() -> None:
    """`|` внутри значения режет ячейку и ломает таблицу целиком — в том числе
    внутри обратных кавычек, где интуиция подсказывает обратное."""
    manifest, node = _artificial()
    context = build_context(manifest, {}, EXAMPLES)

    block = build_generated_block(node, context)

    assert "`api/v1/a\\|b`" in block
    assert "`Some\\|Type`" in block

    # Внутри одной таблицы у всех строк одинаковое число НЕэкранированных `|`.
    # Значение с трубой иначе добавляет колонку, и таблица рассыпается.
    for section in block.split("### ")[1:]:
        rows = [line for line in section.splitlines() if line.startswith("| ")]
        widths = {row.count("|") - row.count("\\|") for row in rows}

        assert len(widths) <= 1, section


def test_multiline_xml_doc_never_lands_in_a_table() -> None:
    """Перевод строки внутри ячейки ломает таблицу."""
    manifest, node = _artificial()
    context = build_context(manifest, {}, EXAMPLES)

    block = build_generated_block(node, context)
    tail = block[block.index("### XML-doc") :]

    assert "> Первая строка." in tail
    assert "> Вторая | со трубой." in tail


def test_unresolvable_dependency_is_marked_not_hidden() -> None:
    """При `confidence: low` в `target` лежит сырое имя типа. Оно не резолвится
    никогда, и это норма — проверять `confidence` не нужно."""
    manifest, node = _artificial()
    context = build_context(manifest, {}, EXAMPLES)

    assert "вне дерева документации" in build_generated_block(node, context)


# --------------------------------------------------------------------------------------
# Бизнес-контекст (B09)
# --------------------------------------------------------------------------------------


def test_business_section_is_absent_without_a_catalog(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Без бизнес-каталога шаг 2 работает ровно как прежде.

    Проверяется отсутствием заголовка, а не только совпадением золотого дерева:
    дерево сравнивается для шести узлов, а свойство должно держаться для любого.
    """
    text = build_generated_block(_node(manifest, "PricingController"), context)

    assert "### Бизнес-контекст" not in text


def test_business_section_links_to_the_process(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    """Обратный индекс приходит в контекст как данные: `docpipe/materialize`
    не знает ни про каталог, ни про якоря."""
    node = _node(manifest, "PricingController")
    with_links = replace(
        context,
        business_root="business",
        business_links={node.id: [("Онлайн УФН", "business/processes/valuation/twinml.md")]},
    )

    text = build_generated_block(node, with_links)

    assert "### Бизнес-контекст" in text
    assert "[Онлайн УФН](../../../../business/processes/valuation/twinml.md)" in text


def test_business_section_says_nothing_when_node_is_not_referenced(
    manifest: Manifest, context
) -> None:  # type: ignore[no-untyped-def]
    """Каталог задан, но узел в нём не упомянут — это факт, а не отсутствие
    возможности. Пропущенный раздел читался бы как «инструмент не умеет»."""
    node = _node(manifest, "PricingController")
    with_links = replace(context, business_root="business", business_links={})

    text = build_generated_block(node, with_links)

    assert "### Бизнес-контекст\n\nНет." in text


def test_business_links_contain_no_backslash(manifest: Manifest, context) -> None:  # type: ignore[no-untyped-def]
    node = _node(manifest, "PricingController")
    with_links = replace(
        context,
        business_root="business",
        business_links={node.id: [("Процесс", "business/processes/x/y.md")]},
    )

    assert "\\" not in build_generated_block(node, with_links)
