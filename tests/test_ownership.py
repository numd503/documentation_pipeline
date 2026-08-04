"""Владение: правила, приоритеты, диагностика (M06).

Проверяется на искусственном манифесте, воспроизводящем ситуацию, ради которой
владение и заводится: шэренный проект `Cf.Shared`, внутри которого лежит код
трёх команд.
"""

from pathlib import Path

import pytest

from docpipe.materialize.ownership import (
    Ownership,
    explain,
    lint,
    load_ownership,
    owner_of,
)
from docpipe.model import Attribute, DocNode, SourceSpan, Symbol

EXAMPLE = Path("ownership.example.yaml")


def _node(
    fqn: str,
    path: str,
    *,
    module: str = "Cf.Shared",
    csproj: str = "src/Cf.Shared/Cf.Shared.csproj",
    kind: str = "service",
    extra: list[str] | None = None,
    bases: list[str] | None = None,
    closure: list[str] | None = None,
    attributes: list[str] | None = None,
) -> DocNode:
    name = fqn.rsplit(".", 1)[-1]
    sources = [SourceSpan(path=p, start=1, end=9) for p in [path, *(extra or [])]]
    return DocNode(
        id=f"type:{csproj}#{fqn}`0",
        kind=kind,
        template="service",
        title=name,
        doc_path=f"docs/modules/{module}/services/{name.lower()}.md",
        parent=f"module:{csproj}",
        module=module,
        domain=module,
        symbol=Symbol(
            fqn=fqn,
            name=name,
            type_kind="class",
            namespace=fqn.rsplit(".", 1)[0],
            module=module,
            sources=sources,
            base_types=bases or [],
            # Замыкание в манифесте содержит и прямые базы: правило `inherits`
            # обязано ловить их тоже, иначе оно оказалось бы уже `base_type`.
            base_type_closure=closure if closure is not None else list(bases or []),
            attributes=[Attribute(name=item) for item in attributes or []],
        ),
        signature_hash="sha256:0",
        impl_hash="sha256:0",
    )


NODES = [
    _node("Cf.Shared.Pricing.CurveStore", "src/Cf.Shared/Pricing/CurveStore.cs"),
    _node("Cf.Shared.Risk.Exposure", "src/Cf.Shared/Risk/Exposure.cs"),
    _node("Cf.Shared.Common.Clock", "src/Cf.Shared/Common/Clock.cs"),
    _node(
        "Cf.Shared.Pricing.Serialization.CurveWriter",
        "src/Cf.Shared/Pricing/Serialization/CurveWriter.cs",
    ),
    _node(
        "Cf.Pricing.Engine.StressTestRunner",
        "src/Cf.Pricing.Engine/StressTestRunner.cs",
        module="Cf.Pricing.Engine",
        csproj="src/Cf.Pricing.Engine/Cf.Pricing.Engine.csproj",
    ),
    # Лежит в каталоге платформы, но по базовому типу принадлежит рискам:
    # граница, которую нельзя провести ни путём, ни namespace.
    _node(
        "Cf.Shared.Common.VarEngine",
        "src/Cf.Shared/Common/VarEngine.cs",
        bases=["Cf.Risk.Engine.RiskEngineBase"],
    ),
]


@pytest.fixture
def ownership() -> Ownership:
    return load_ownership(EXAMPLE)


def _inline(body: str, tmp: Path) -> Ownership:
    path = tmp / "ownership.yaml"
    path.write_text(f'version: "1"\n{body}', encoding="utf-8")
    return load_ownership(path)


def _team(fqn: str, ownership: Ownership) -> str | None:
    node = next(n for n in NODES if n.symbol and n.symbol.fqn == fqn)
    return owner_of(node, ownership).team


# --------------------------------------------------------------------------------------
# Слои приоритетов
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fqn", "team"),
    [
        ("Cf.Shared.Pricing.CurveStore", "pricing"),
        ("Cf.Shared.Risk.Exposure", "risk"),
        ("Cf.Shared.Common.Clock", "platform"),
    ],
)
def test_shared_project_is_split_by_folder(fqn: str, team: str, ownership: Ownership) -> None:
    """Ради этого владение и заводится: один `.csproj`, код трёх команд."""
    assert _team(fqn, ownership) == team


def test_base_type_beats_folder(ownership: Ownership) -> None:
    """Слой 80 в заготовке: тип лежит в каталоге платформы, но наследует
    `RiskEngineBase`. Ни путём, ни namespace такая граница не выражается."""
    assert _team("Cf.Shared.Common.VarEngine", ownership) == "risk"


def test_namespace_beats_folder(ownership: Ownership) -> None:
    """Приоритет 80 бьёт 50: сериализация внутри каталога ценообразования
    принадлежит платформе."""
    assert _team("Cf.Shared.Pricing.Serialization.CurveWriter", ownership) == "platform"


def test_pointwise_rule_beats_module(ownership: Ownership) -> None:
    """Приоритет 100 бьёт 10: один тип передан другой команде."""
    assert _team("Cf.Pricing.Engine.StressTestRunner", ownership) == "risk"


def test_rule_order_in_yaml_changes_nothing(ownership: Ownership) -> None:
    """Порядок строк в файле источником решения быть не может."""
    reversed_rules = Ownership(
        version=ownership.version,
        ownership_version=ownership.ownership_version,
        teams=ownership.teams,
        rules=list(reversed(ownership.rules)),
    )

    assert [owner_of(node, reversed_rules).team for node in NODES] == [
        owner_of(node, ownership).team for node in NODES
    ]


def test_unmatched_node_has_no_owner(ownership: Ownership) -> None:
    node = _node(
        "Other.Thing",
        "src/Other/Thing.cs",
        module="Other",
        csproj="src/Other/Other.csproj",
    )

    assert owner_of(node, ownership).team is None


def test_matched_keeps_all_rules_for_audit(ownership: Ownership) -> None:
    node = next(n for n in NODES if n.title == "CurveWriter")

    decision = owner_of(node, ownership)

    assert [rule.id for rule in decision.matched] == [
        "platform.shared-serialization",
        "pricing.shared-folders",
        "platform.modules",
    ]
    assert decision.winner is not None and decision.winner.id == "platform.shared-serialization"


# --------------------------------------------------------------------------------------
# Предикат по путям
# --------------------------------------------------------------------------------------


def test_path_glob_uses_matches_glob_not_fnmatch(tmp_path: Path) -> None:
    """`**/Pricing/**` обязан поймать файл в корне модуля: `fnmatch`
    не понимает `**` как «ноль или больше сегментов»."""
    ownership = _inline(
        """
teams: [{id: t, title: T}]
rules:
  - {id: r, team: t, priority: 10, when: {path_glob: ["**/Pricing/**"]}}
""",
        tmp_path,
    )
    node = _node("N.X", "Pricing/CurveStore.cs")

    assert owner_of(node, ownership).team == "t"


def test_any_source_is_enough(ownership: Ownership) -> None:
    """У `partial`-класса файлы бывают в каталогах разных команд. Правило
    «все источники» оставило бы такой тип ничьим — это выглядело бы как дефект
    инструмента, поэтому достаточно одного совпадения."""
    node = _node(
        "Cf.Shared.Mixed.Thing",
        "src/Cf.Shared/Common/Thing.cs",
        extra=["src/Cf.Shared/Risk/Thing.Extra.cs"],
    )

    assert owner_of(node, ownership).team in {"risk", "platform"}


# --------------------------------------------------------------------------------------
# Предикаты по контракту типа
# --------------------------------------------------------------------------------------


BY_BASE = """
teams: [{id: ml, title: ML}, {id: platform, title: P}]
rules:
  - {id: platform.all, team: platform, priority: 10, when: {module_glob: ["**"]}}
  - {id: ml.controllers, team: ml, priority: 50, when: {inherits: ["MlApiControllerBase"]}}
"""


def test_inherits_follows_the_whole_closure(tmp_path: Path) -> None:
    """Ради этого предикат и заведён: команда опознаёт свои типы по базовому
    классу, а прямой базой у них стоит собственный промежуточный."""
    ownership = _inline(BY_BASE, tmp_path)
    node = _node(
        "Ml.Api.ScoringController",
        "src/Ml.Api/ScoringController.cs",
        bases=["Ml.Api.ScoringControllerBase"],
        closure=["Ml.Api.ScoringControllerBase", "Sbt.Ml.Web.MlApiControllerBase"],
    )

    assert owner_of(node, ownership).team == "ml"


def test_base_type_is_not_a_synonym_of_inherits(tmp_path: Path) -> None:
    """Два предиката, а не один: `base_type` смотрит только на прямые базы.
    Если бы он тоже шёл по замыканию, разница между ними была бы необъяснима."""
    ownership = _inline(
        """
teams: [{id: ml, title: ML}]
rules:
  - {id: ml.direct, team: ml, priority: 10, when: {base_type: ["MlApiControllerBase"]}}
""",
        tmp_path,
    )
    through_middle = _node(
        "Ml.Api.ScoringController",
        "src/Ml.Api/ScoringController.cs",
        bases=["Ml.Api.ScoringControllerBase"],
        closure=["Ml.Api.ScoringControllerBase", "Sbt.Ml.Web.MlApiControllerBase"],
    )
    direct = _node(
        "Ml.Api.LimitsController",
        "src/Ml.Api/LimitsController.cs",
        bases=["Sbt.Ml.Web.MlApiControllerBase"],
    )

    assert owner_of(through_middle, ownership).team is None
    assert owner_of(direct, ownership).team == "ml"


def test_condition_from_the_ruleset_works_verbatim(tmp_path: Path) -> None:
    """Условие скопировано из `rules/dotnet.yaml` (правило `controller.aspnet`)
    без единой правки. Ради этого трактовка имён и вынесена в общую функцию:
    иначе одна и та же строка означала бы в двух файлах разное."""
    ownership = _inline(
        """
teams: [{id: ml, title: ML}]
rules:
  - id: ml.controllers
    team: ml
    priority: 100
    when:
      any:
        - attribute: ["ApiController"]
        - base_type: ["ControllerBase", "Controller"]
        - inherits: ["ControllerBase", "Controller"]
""",
        tmp_path,
    )
    by_attribute = _node("Ml.Api.A", "src/Ml.Api/A.cs", attributes=["ApiController"])
    by_closure = _node(
        "Ml.Api.B",
        "src/Ml.Api/B.cs",
        bases=["Ml.Api.BaseApiController"],
        closure=["Ml.Api.BaseApiController", "Microsoft.AspNetCore.Mvc.ControllerBase"],
    )

    assert owner_of(by_attribute, ownership).team == "ml"
    assert owner_of(by_closure, ownership).team == "ml"


def test_short_name_matches_qualified_base(tmp_path: Path) -> None:
    """`base_type_candidates` из `classify.py`: в правиле пишут короткое имя,
    в замыкании лежит полное."""
    ownership = _inline(
        """
teams: [{id: ml, title: ML}]
rules:
  - {id: ml.base, team: ml, priority: 10, when: {inherits: ["MlApiControllerBase"]}}
""",
        tmp_path,
    )
    node = _node("Ml.Api.C", "src/Ml.Api/C.cs", bases=["Sbt.Ml.Web.MlApiControllerBase"])

    assert owner_of(node, ownership).team == "ml"


def test_broken_closure_keeps_only_the_direct_base(tmp_path: Path) -> None:
    """Модуль базы исключён в `docpipe.yaml` — рвётся ТРАНЗИТИВНАЯ часть
    замыкания. Воспроизведено на `SampleSolution`: у `PricingController`
    остаётся `["BaseApiController"]`, а `ControllerBase` из её объявления
    пропадает. Правило по прямой базе работает, по базе через уровень — нет,
    и это молча."""
    ownership = _inline(
        """
teams: [{id: web, title: W}]
rules:
  - {id: web.direct, team: web, priority: 10, when: {inherits: ["BaseApiController"]}}
  - {id: web.grandparent, team: web, priority: 10, when: {inherits: ["ControllerBase"]}}
""",
        tmp_path,
    )
    node = _node(
        "Ml.Api.PricingController",
        "src/Ml.Api/PricingController.cs",
        bases=["BaseApiController"],
        closure=["BaseApiController"],
    )

    matched = {rule.id for rule in owner_of(node, ownership).matched}
    assert matched == {"web.direct"}


def test_type_predicates_are_false_without_symbol(tmp_path: Path) -> None:
    """Узел без символа не обязан ронять прогон: остальные предикаты владения
    ведут себя на нём так же — пустое значение, а не исключение."""
    ownership = _inline(
        """
teams: [{id: ml, title: ML}]
rules:
  - id: ml.any
    team: ml
    priority: 10
    when:
      any:
        - attribute: ["ApiController"]
        - base_type: ["ControllerBase"]
        - inherits: ["ControllerBase"]
""",
        tmp_path,
    )
    node = DocNode(
        id="module:src/Ml.Api/Ml.Api.csproj",
        kind="module",
        template="module",
        title="Ml.Api",
        doc_path="docs/modules/Ml.Api/index.md",
        module="Ml.Api",
        domain="ml",
        symbol=None,
        signature_hash="sha256:0",
    )

    assert owner_of(node, ownership).team is None


# --------------------------------------------------------------------------------------
# Ничьи
# --------------------------------------------------------------------------------------


TIE = """
teams: [{id: alpha, title: A}, {id: zeta, title: Z}]
rules:
  - {id: zeta.rule, team: zeta, priority: 50, when: {path_glob: ["src/**"]}}
  - {id: alpha.rule, team: alpha, priority: 50, when: {path_glob: ["src/**"]}}
"""


def test_tie_goes_to_smaller_id_and_is_reported(tmp_path: Path) -> None:
    ownership = _inline(TIE, tmp_path)
    node = _node("N.X", "src/X.cs")

    decision = owner_of(node, ownership)
    _, warnings = lint([node], ownership)

    assert decision.team == "alpha"
    assert decision.tie is True
    assert any("Ничьи по приоритету" in warning for warning in warnings)


# --------------------------------------------------------------------------------------
# Диагностика
# --------------------------------------------------------------------------------------


def test_lint_finds_dead_rule_unowned_nodes_and_idle_team(tmp_path: Path) -> None:
    ownership = _inline(
        """
teams: [{id: used, title: U}, {id: idle, title: I}]
rules:
  - {id: live, team: used, priority: 10, when: {path_glob: ["src/Live/**"]}}
  - {id: dead, team: used, priority: 10, when: {path_glob: ["src/Renamed/**"]}}
""",
        tmp_path,
    )
    nodes = [_node("N.A", "src/Live/A.cs"), _node("N.B", "src/Other/B.cs")]

    findings, _ = lint(nodes, ownership)
    text = "\n".join(findings)

    assert "не совпавшие ни с одним узлом: dead" in text
    assert "Узлов без владельца: 1 из 2" in text
    assert "не досталось ни одного узла: idle" in text


def test_lint_slices_show_where_to_write_rules(tmp_path: Path) -> None:
    """Один счётчик бесполезен: именно срезы показывают, что дописать."""
    ownership = _inline(
        """
teams: [{id: t, title: T}]
rules:
  - {id: r, team: t, priority: 10, when: {path_glob: ["src/Known/**"]}}
""",
        tmp_path,
    )
    nodes = [_node("N.A", "src/Unknown/A.cs"), _node("N.B", "src/Unknown/B.cs")]

    findings, _ = lint(nodes, ownership)
    text = "\n".join(findings)

    assert "модули:" in text
    assert "каталоги внутри модуля:" in text
    assert "src/Unknown" in text


def test_lint_is_quiet_on_a_healthy_set(ownership: Ownership) -> None:
    findings, warnings = lint(NODES, ownership)

    assert not any("без владельца" in finding for finding in findings)
    assert warnings == []


def test_lint_reports_types_split_across_folders(ownership: Ownership) -> None:
    node = _node(
        "Cf.Shared.Pricing.Split",
        "src/Cf.Shared/Pricing/Split.cs",
        extra=["src/Cf.Shared/Risk/Split.Extra.cs"],
    )

    _, warnings = lint([node], ownership)

    assert any("в разных каталогах" in warning for warning in warnings)


# --------------------------------------------------------------------------------------
# Отказы при загрузке
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "teams: [{id: a, title: A}]\nrules:\n"
            "  - {id: r, team: nope, priority: 1, when: {module: [x]}}\n",
            "которой нет в `teams`",
        ),
        (
            "teams: [{id: a, title: A}, {id: a, title: B}]\n",
            "повтор id команды",
        ),
        (
            "teams: [{id: a, title: A}]\nrules:\n"
            "  - {id: r, team: a, priority: 1, when: {module: [x]}}\n"
            "  - {id: r, team: a, priority: 2, when: {module: [y]}}\n",
            "повтор id правила",
        ),
        (
            "teams: [{id: a, title: A}]\nrules:\n"
            "  - {id: r, team: a, priority: 1, when: {namespace_prefx: [x]}}\n",
            "неизвестный предикат",
        ),
        (
            "teams: [{id: a, title: A}]\nrules:\n"
            "  - {id: r, team: a, priority: 1, when: {namespace_regex: ['(']}}\n",
            "неверное регулярное выражение",
        ),
        (
            "teams: [{id: a, title: A}]\nrules:\n  - {id: r, team: a, priority: 1}\n",
            "без полей",
        ),
    ],
)
def test_load_rejects(tmp_path: Path, body: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        _inline(body, tmp_path)


def test_unknown_predicate_message_matches_classify(tmp_path: Path) -> None:
    """Общий движок — значит, и сообщение общее: настройщик правил и настройщик
    границ команд часто один человек."""
    with pytest.raises(ValueError) as exc:
        _inline(
            "teams: [{id: a, title: A}]\nrules:\n"
            "  - {id: r, team: a, priority: 1, when: {module_glb: [x]}}\n",
            tmp_path,
        )

    assert "неизвестный предикат `module_glb`; известны:" in str(exc.value)
    assert "path_glob" in str(exc.value)


# --------------------------------------------------------------------------------------
# Объяснение
# --------------------------------------------------------------------------------------


def test_explain_shows_every_matched_rule(ownership: Ownership) -> None:
    node = next(n for n in NODES if n.title == "CurveWriter")

    text = explain(node, ownership)

    assert "platform.shared-serialization" in text
    assert "pricing.shared-folders" in text
    assert "← победитель" in text
    assert "владелец:  platform" in text


def test_explain_says_when_there_is_no_owner(ownership: Ownership) -> None:
    node = _node("Other.Thing", "src/Other/Thing.cs", module="Other", csproj="src/Other/O.csproj")

    text = explain(node, ownership)

    assert "совпавшие правила:" in text
    assert "владелец:  не задан" in text
