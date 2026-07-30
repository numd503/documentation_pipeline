"""Общее для тестов бизнес-слоя: синтетический манифест и правка реестров.

Манифест собирается вручную, а не разбором фикстур C#: связь «якорь → факты»
языконезависима, и тянуть ради неё разбор .NET значило бы привязать
бизнес-слой к нему через тесты. По той же причине помощники лежат отдельно
от `conftest.py`, который весь про .NET.
"""

import shutil
from pathlib import Path

from docpipe.business import build_context
from docpipe.business.resolve import ResolveContext
from docpipe.model import DocNode, Manifest, Member, ParserVersions, Relation, SourceSpan, Symbol
from docpipe.registry import load_registries, read_registry, resolve_anchors

REGISTRIES_ROOT = Path("tests/fixtures/registries")
BUSINESS_ROOT = Path("tests/fixtures")


def member(name: str, kind: str = "method", *, public: bool = True) -> Member:
    return Member(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        signature=f"{'public' if public else 'private'} void {name}()",
        modifiers=["public"] if public else ["private"],
        line=1,
        end_line=1,
    )


def node(
    fqn: str,
    *,
    implements: str | None = None,
    params: list[str] | None = None,
    members: list[Member] | None = None,
    kind: str = "service",
) -> DocNode:
    name = fqn.rsplit(".", 1)[-1]
    namespace = fqn.rsplit(".", 1)[0]
    return DocNode(
        id=f"type:src/App/App.csproj#{fqn}`{len(params or [])}",
        kind=kind,
        template=kind,
        title=name,
        doc_path=f"docs/modules/App/{kind}s/{name.lower()}.md",
        module="App",
        domain="app",
        signature_hash="sha256:0",
        symbol=Symbol(
            fqn=fqn,
            name=name,
            type_kind="class",
            namespace=namespace,
            module="App",
            type_parameters=params or [],
            members=members or [],
            sources=[SourceSpan(path=f"src/App/{name}.cs", start=1, end=10)],
        ),
        related=([Relation(target=implements, relation="implements")] if implements else []),
    )


# `Calc` и `Recalc` — публичные методы grid-сервиса: единственные имена
# C#-элементов, входящие в `business_hash`. `Prepare` приватный и не входит.
def nodes() -> list[DocNode]:
    return [
        node(
            "Sbt.Cashflow.Grid.Services.CalcResult.CalcResult",
            members=[member("Calc"), member("Recalc"), member("Prepare", public=False)],
        ),
        node("Sbt.Cashflow.Grid.Services.CalcService.DXExcelCalcService"),
        node(
            "Sbt.CMS.Cashflow.Infrastructure.FinancialReportJob",
            implements="Sbt.CMS.Cashflow.Infrastructure.QuartzJobInterfaces.IFinancialReportJob",
        ),
        node("Sbt.Sample.Steps.CollectStep"),
        node("Sbt.Sample.Steps.ScoreStep"),
        node("Sbt.Sample.Steps.MapStep", params=["TIn", "TOut"]),
        node("Sbt.Sample.Models.SampleAggregate"),
        node("Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver"),
    ]


def manifest(items: list[DocNode] | None = None, ruleset_version: str = "test") -> Manifest:
    return Manifest(
        ruleset_version=ruleset_version,
        parser=ParserVersions(tree_sitter="0.25.0", grammar_c_sharp="0.23.0"),
        nodes=nodes() if items is None else items,
    )


def context(tree: Path = REGISTRIES_ROOT, resolved: Manifest | None = None) -> ResolveContext:
    """Инвентаризация плюс манифест, свёрнутые в индексы."""
    resolved = resolved or manifest()
    specs = load_registries(tree / "registries.yaml")
    anchors = resolve_anchors([read_registry(spec, tree) for spec in specs], resolved)
    return build_context(anchors, resolved, root=tree)


def registries_copy(tmp_path: Path) -> Path:
    """Копия дерева реестров, которую тест правит, не трогая фикстуру."""
    destination = tmp_path / "registries"
    shutil.copytree(REGISTRIES_ROOT, destination)
    return destination


def edit(path: Path, old: str, new: str) -> None:
    """Правка байтами: у `Items-Workflows.xml` есть BOM, и снимать его нельзя.

    Заменяется только первое вхождение. `</Fields>` и `<Fields>` встречаются
    в `Structure.xml` у каждого списка, и замена всех вхождений правила бы
    заодно соседний список — тест продолжал бы проходить, проверяя не то,
    что написано в его названии.
    """
    data = path.read_bytes()
    assert old.encode("utf-8") in data, f"нечего заменять в {path.name}: {old!r}"
    path.write_bytes(data.replace(old.encode("utf-8"), new.encode("utf-8"), 1))
