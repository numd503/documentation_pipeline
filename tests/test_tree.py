"""Проверка сборки дерева документации (T15)."""

import shutil
from collections import defaultdict
from pathlib import Path

import pytest

from docpipe.config import DocpipeConfig
from docpipe.model import Dependency, DocNode
from docpipe.tree import doc_path_for, parameter_types
from tests.conftest import build_tree, sectioned


def _nodes(root: Path, **kwargs: object) -> dict[str, DocNode]:
    _, nodes = build_tree(root, **kwargs)  # type: ignore[arg-type]
    return {node.title: node for node in nodes}


# --------------------------------------------------------------------------------------
# Критерии приёмки на фикстуре
# --------------------------------------------------------------------------------------


def test_exactly_six_nodes(sample_solution: Path) -> None:
    assert set(_nodes(sample_solution)) == {
        "PricingController",
        "BaseApiController",
        "RiskComputeService",
        "ValuationWorkflow",
        "CurveProvider",
        "PricingService",
    }


def test_doc_path(sample_solution: Path) -> None:
    node = _nodes(sample_solution)["PricingController"]
    assert node.doc_path == "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"


def test_doc_path_module_first_layout(sample_solution: Path) -> None:
    """Вторая раскладка — та же тройка сегментов, переставленная."""
    nodes = _nodes(sample_solution, config=DocpipeConfig(doc_layout="module-first"))
    assert (
        nodes["PricingController"].doc_path
        == "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md"
    )


def test_layout_groups_kinds_together(sample_solution: Path) -> None:
    """Ради чего раскладка и менялась: все сущности вида — в одном каталоге.

    В `module-first` тот же набор узлов даёт столько каталогов верхнего уровня,
    сколько модулей, и обойти «все контроллеры» одним каталогом нельзя.
    """
    other = _nodes(sample_solution, config=DocpipeConfig(doc_layout="module-first"))
    kind_first = {node.doc_path.split("/")[2] for node in _nodes(sample_solution).values()}
    module_first = {node.doc_path.split("/")[2] for node in other.values()}
    assert kind_first == {"controllers", "ignite_services", "providers", "services", "workflows"}
    assert module_first == {"Sample.Common", "Sample.Pricing.Api"}


def test_layout_does_not_change_collisions() -> None:
    """Смена раскладки не создаёт и не убирает ни одного конфликта путей.

    Свойство, ради которого раскладку и можно менять безболезненно: путь
    в каждой из них — инъекция от тройки (модуль, вид, slug), поэтому два узла
    спорят за файл в одной ровно тогда, когда спорят и в другой. Проверяется
    на наборе, где конфликты есть **на самом деле**: на фикстурах узлов слишком
    мало, чтобы хоть один случился, и тест на них проходил бы вхолостую.

    Опасный случай — последний: модуль назван как вид сущности. Уровни в пути
    не смешиваются, поэтому и он ничего не склеивает.
    """
    triples = [
        ("Sample.Pricing.Api", "controller", "PricingController"),
        ("Sample.Pricing.Api", "controller", "pricing_controller"),  # тот же slug
        ("Sample.Common", "controller", "PricingController"),  # тот же вид и slug
        ("Sample.Pricing.Api", "service", "PricingController"),  # тот же модуль и slug
        ("Sample.Pricing.Api", "service", "PricingService"),
        ("services", "controller", "PricingService"),  # модуль назван как вид
        ("services", "service", "PricingService"),
    ]

    def groups(layout: str) -> list[list[int]]:
        by_path: defaultdict[str, list[int]] = defaultdict(list)
        for position, (module, kind, name) in enumerate(triples):
            path = doc_path_for(module, kind, name, layout, "docs/modules")  # type: ignore[arg-type]
            by_path[path].append(position)
        return sorted(by_path.values())

    assert groups("kind-first") == groups("module-first")
    # И конфликт в наборе действительно есть — иначе сравнение выше пусто.
    assert [group for group in groups("kind-first") if len(group) > 1] == [[0, 1]]


def test_layout_keeps_node_identity(sample_solution: Path) -> None:
    """Раскладка меняет только путь: `id` узла от неё не зависит."""
    kind_first = _nodes(sample_solution)
    module_first = _nodes(sample_solution, config=DocpipeConfig(doc_layout="module-first"))

    assert {title: node.id for title, node in kind_first.items()} == {
        title: node.id for title, node in module_first.items()
    }
    assert {title: node.signature_hash for title, node in kind_first.items()} == {
        title: node.signature_hash for title, node in module_first.items()
    }


def test_node_belongs_to_its_own_module(sample_solution: Path) -> None:
    """Базовый контроллер лежит в другом модуле, чем наследник."""
    nodes = _nodes(sample_solution)
    assert nodes["BaseApiController"].module == "Sample.Common"
    assert nodes["PricingController"].module == "Sample.Pricing.Api"


def test_constructor_dependency(sample_solution: Path) -> None:
    node = _nodes(sample_solution)["PricingController"]
    assert (
        Dependency(
            target="Sample.Pricing.Api.Services.IPricingService",
            via="constructor",
            confidence="high",
        )
        in node.dependencies
    )


def test_signature_hash_ignores_formatting(sample_solution: Path, tmp_path: Path) -> None:
    """Пустая строка перед классом не должна менять хэш.

    Иначе переформатирование файла заставит агента шага 3 перегенерировать
    документ впустую, и вся экономия от инкрементальности пропадёт.
    """
    copy = tmp_path / "Solution"
    shutil.copytree(sample_solution, copy)
    target = copy / "src/Sample.Pricing.Api/Controllers/PricingController.cs"

    before = _nodes(copy)["PricingController"].signature_hash
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("[Route(", "\n[Route("), encoding="utf-8")
    after = _nodes(copy)["PricingController"].signature_hash

    assert before == after


def test_signature_hash_changes_with_a_new_member(sample_solution: Path, tmp_path: Path) -> None:
    """Контроль к предыдущему тесту: содержательное изменение хэш менять обязано."""
    copy = tmp_path / "Solution"
    shutil.copytree(sample_solution, copy)
    target = copy / "src/Sample.Pricing.Api/Controllers/PricingController.cs"

    before = _nodes(copy)["PricingController"].signature_hash
    text = target.read_text(encoding="utf-8")
    target.write_text(text.rstrip()[:-1] + "\n    public void Added() { }\n}\n", encoding="utf-8")

    assert _nodes(copy)["PricingController"].signature_hash != before


# --------------------------------------------------------------------------------------
# Коллизии путей
# --------------------------------------------------------------------------------------


def _rules_for_interfaces(tmp_path: Path) -> Path:
    """Набор, классифицирующий интерфейсы: штатный их не берёт вовсе."""
    path = tmp_path / "rules.yaml"
    path.write_text(
        sectioned(
            {
                "ruleset_version": "test",
                "exclude": {},
                "rules": [
                    {
                        "id": "any.type",
                        "kind": "service",
                        "template": "service",
                        "priority": 1,
                        "when": {"type_kind": ["class", "record", "interface"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_same_name_in_one_module_gets_hash_suffix(tmp_path: Path) -> None:
    """Два класса с одинаковым именем в одном модуле претендуют на один файл."""
    root = tmp_path / "Solution"
    (root / "src/App").mkdir(parents=True)
    (root / "src/App/App.csproj").write_text("<Project />", encoding="utf-8")
    (root / "src/App/One.cs").write_text(
        "namespace A;\npublic class Handler { }\n", encoding="utf-8"
    )
    (root / "src/App/Two.cs").write_text(
        "namespace B;\npublic class Handler { }\n", encoding="utf-8"
    )

    nodes = build_tree(root, rules=_rules_for_interfaces(tmp_path))[1]
    paths = [node.doc_path for node in nodes]

    assert len(paths) == 2
    assert len(set(paths)) == 2
    assert all(not path.endswith("/handler.md") for path in paths), paths


def test_generic_arity_gives_three_nodes(wild_solution: Path, tmp_path: Path) -> None:
    """Три `ICrudAppService` с арностями 1, 2, 3 — один FQN на всех.

    Реализация с ключом по одному FQN даст здесь один узел вместо трёх,
    и два документа исчезнут молча. В ABP таких групп 112.
    """
    _, nodes = build_tree(wild_solution, rules=_rules_for_interfaces(tmp_path))
    crud = [node for node in nodes if node.title == "ICrudAppService"]

    assert len(crud) == 3
    assert len({node.id for node in crud}) == 3
    assert len({node.doc_path for node in crud}) == 3


def test_suffix_is_built_from_id_not_fqn(wild_solution: Path, tmp_path: Path) -> None:
    """У трёх `ICrudAppService` FQN одинаков — суффикс от FQN не развёл бы их."""
    _, nodes = build_tree(wild_solution, rules=_rules_for_interfaces(tmp_path))
    crud = [node for node in nodes if node.title == "ICrudAppService"]

    assert len({node.symbol.fqn for node in crud if node.symbol}) == 1
    assert len({node.doc_path for node in crud}) == 3


# --------------------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------------------


def test_domain_from_config(sample_solution: Path) -> None:
    config = DocpipeConfig(domains={"src/Sample.Pricing.Api/**": "pricing"})
    modules, nodes = build_tree(sample_solution, config=config)

    by_name = {module.name: module for module in modules}
    assert by_name["Sample.Pricing.Api"].domain == "pricing"
    # Без совпадения доменом становится имя модуля.
    assert by_name["Sample.Common"].domain == "Sample.Common"
    assert {node.domain for node in nodes if node.module == "Sample.Pricing.Api"} == {"pricing"}


def test_domain_globs_are_checked_in_key_order(sample_solution: Path) -> None:
    """Побеждает первый в лексикографическом порядке ключей, а не в порядке записи."""
    config = DocpipeConfig(domains={"src/**": "zzz-wide", "**/Sample.Pricing.Api/**": "aaa-narrow"})
    modules, _ = build_tree(sample_solution, config=config)

    by_name = {module.name: module for module in modules}
    assert by_name["Sample.Pricing.Api"].domain == "aaa-narrow"


def test_non_enrolled_module_produces_no_nodes(sample_solution: Path) -> None:
    """Неenrolled модуль всё равно парсится — его символы нужны графу наследования."""
    config = DocpipeConfig(enrolled=["src/Sample.Pricing.Api/**"])
    modules, nodes = build_tree(sample_solution, config=config)

    assert {node.module for node in nodes} == {"Sample.Pricing.Api"}
    assert not next(m for m in modules if m.name == "Sample.Common").enrolled
    # Наследование через неenrolled модуль сохранилось.
    controller = next(node for node in nodes if node.title == "PricingController")
    assert controller.symbol is not None
    assert "ControllerBase" in controller.symbol.base_type_closure


# --------------------------------------------------------------------------------------
# Связи, зависимости и эндпоинты
# --------------------------------------------------------------------------------------


def test_implements_relation(sample_solution: Path) -> None:
    node = _nodes(sample_solution)["PricingService"]
    assert [(r.target, r.relation) for r in node.related] == [
        ("Sample.Pricing.Api.Services.IPricingService", "implements")
    ]


def test_di_dependency_points_at_the_service(sample_solution: Path) -> None:
    node = _nodes(sample_solution)["CurveProvider"]
    assert (
        Dependency(
            target="Sample.Common.Abstractions.IPricingProvider", via="di", confidence="high"
        )
        in node.dependencies
    )


def test_self_registration_gives_no_loop(sample_solution: Path) -> None:
    """`AddTransient<ValuationWorkflow>()` — регистрация типа на самого себя.

    Петля в графе зависимостей — артефакт формы записи, а не факт о коде.
    """
    node = _nodes(sample_solution)["ValuationWorkflow"]
    assert all(
        dependency.target != "Sample.Pricing.Api.Workflows.ValuationWorkflow"
        for dependency in node.dependencies
    )


def test_endpoints_land_on_the_node(sample_solution: Path) -> None:
    node = _nodes(sample_solution)["PricingController"]
    assert [(e.http_method, e.route) for e in node.endpoints] == [
        ("POST", "api/v1/Pricing"),
        ("GET", "api/v1/Pricing/{id:guid}"),
    ]
    assert _nodes(sample_solution)["PricingService"].endpoints == []


def test_external_constructor_parameter_gets_low_confidence(sample_solution: Path) -> None:
    """Тип, которого нет в индексе, резолвнуть нечем — это видно по `confidence`."""
    node = _nodes(sample_solution)["PricingService"]
    external = [d for d in node.dependencies if d.confidence == "low"]
    assert all(d.via == "constructor" for d in external)


# --------------------------------------------------------------------------------------
# parameter_types
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ("public C(IFoo foo)", ["IFoo"]),
        ("public C()", []),
        ("public C(IFoo foo, IBar bar)", ["IFoo", "IBar"]),
        # Запятая внутри дженерика не разделяет параметры.
        ("public C(IReadOnlyDictionary<string, int> map)", ["IReadOnlyDictionary<string, int>"]),
        # Атрибут параметра, модификаторы и значение по умолчанию отбрасываются.
        ("public C([FromServices] IFoo foo)", ["IFoo"]),
        ("public C(ref IFoo foo, out IBar bar)", ["IFoo", "IBar"]),
        ("public C(params string[] args)", ["string[]"]),
        ("public C(int retries = 3)", ["int"]),
        ("public C(CancellationToken ct = default)", ["CancellationToken"]),
        # Кортеж — тоже один параметр.
        ("public C((int, string) pair)", ["(int, string)"]),
        ("public C(IFoo foo, CancellationToken ct)", ["IFoo", "CancellationToken"]),
    ],
)
def test_parameter_types(signature: str, expected: list[str]) -> None:
    assert parameter_types(signature) == expected


def test_parameter_types_without_parentheses() -> None:
    """Свойство или поле сигнатуры со скобками не имеет — падать нельзя."""
    assert parameter_types("public int Value") == []


# --------------------------------------------------------------------------------------
# Свойства результата
# --------------------------------------------------------------------------------------


def test_nodes_and_modules_are_sorted_by_id(sample_solution: Path) -> None:
    modules, nodes = build_tree(sample_solution)
    assert [n.id for n in nodes] == sorted(n.id for n in nodes)
    assert [m.id for m in modules] == sorted(m.id for m in modules)


def test_node_id_matches_symbol_key_scheme(sample_solution: Path) -> None:
    """Схема id обязана совпадать с ключом символа, иначе они не сопоставятся."""
    node = _nodes(sample_solution)["PricingController"]
    assert node.id == (
        "type:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"
        "#Sample.Pricing.Api.Controllers.PricingController`0"
    )
    assert node.parent == "module:src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"


def test_matched_rules_are_kept_for_audit(sample_solution: Path) -> None:
    node = _nodes(sample_solution)["RiskComputeService"]
    assert node.matched_rules == ["ignite.service", "service"]
    assert node.kind == "ignite_service"


def test_build_is_deterministic(sample_solution: Path) -> None:
    assert build_tree(sample_solution) == build_tree(sample_solution)


def test_parameter_types_stop_at_constructor_initializer() -> None:
    """Сигнатура конструктора включает `: base(...)` — резать нужно по парной скобке.

    По последней скобке в строке получился бы «параметр» `IOptions o) : base(o`.
    На ABP так ломался каждый пятый конструктор: 217 рёбер из 1016.
    """
    assert parameter_types("public C(IOptions o) : base(o)") == ["IOptions"]
    assert parameter_types("public C(IFoo a, IBar b) : this(a)") == ["IFoo", "IBar"]
