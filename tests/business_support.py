"""Общее для тестов бизнес-слоя: синтетический манифест и правка реестров.

Манифест собирается вручную, а не разбором фикстур C#: связь «якорь → факты»
языконезависима, и тянуть ради неё разбор .NET значило бы привязать
бизнес-слой к нему через тесты. По той же причине помощники лежат отдельно
от `conftest.py`, который весь про .NET.
"""

import shutil
from pathlib import Path

import yaml

from docpipe.business import build_context, doc_path_for
from docpipe.business.resolve import ResolveContext
from docpipe.materialize.ownership import Ownership, OwnershipRule, Team
from docpipe.model import DocNode, Manifest, Member, ParserVersions, Relation, SourceSpan, Symbol
from docpipe.registry import load_registries, read_registry, resolve_anchors
from docpipe.registry.anchors import ResolvedAnchor

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
        node("Sbt.Cashflow.Reports.EventReceivers.UserTasksAddedAuditEventReceiver"),
    ]


def manifest(items: list[DocNode] | None = None, ruleset_version: str = "test") -> Manifest:
    return Manifest(
        ruleset_version=ruleset_version,
        parser=ParserVersions(tree_sitter="0.25.0", grammar_c_sharp="0.23.0"),
        nodes=nodes() if items is None else items,
    )


def registry_anchors(
    tree: Path = REGISTRIES_ROOT, resolved: Manifest | None = None
) -> list[ResolvedAnchor]:
    """Инвентаризация точек входа по фикстурным реестрам."""
    specs = load_registries(tree / "registries.yaml")
    return resolve_anchors([read_registry(spec, tree) for spec in specs], resolved or manifest())


def owners(rules: dict[str, list[str]]) -> Ownership:
    """Правила владения по префиксу FQN: `{команда: [префиксы]}`.

    Собирается объектами, а не через файл: предмет проверки — селектор
    `only.team`, а не разбор YAML, который проверяется своими тестами.
    """
    return Ownership(
        version="1",
        ownership_version="test",
        teams=[Team(id=team, title=team.upper()) for team in sorted(rules)],
        rules=[
            OwnershipRule(id=f"{team}.rule", team=team, priority=10, when={"fqn_prefix": prefixes})
            for team, prefixes in sorted(rules.items())
        ],
    )


def context(
    tree: Path = REGISTRIES_ROOT,
    resolved: Manifest | None = None,
    ownership: Ownership | None = None,
) -> ResolveContext:
    """Инвентаризация плюс манифест, свёрнутые в индексы."""
    resolved = resolved or manifest()
    return build_context(registry_anchors(tree, resolved), resolved, root=tree, ownership=ownership)


def registries_copy(tmp_path: Path) -> Path:
    """Копия дерева реестров, которую тест правит, не трогая фикстуру."""
    destination = tmp_path / "registries"
    shutil.copytree(REGISTRIES_ROOT, destination)
    return destination


def combined_tree(tmp_path: Path) -> Path:
    """Корень, где реестры и бизнес-каталог лежат под одним `--root`.

    В фикстурах они разнесены (`tests/fixtures/registries` и
    `tests/fixtures/business`), потому что реестры описывают пути от своего
    каталога. Командам же `--root` один на всё, и проверять их надо в такой
    же раскладке, а не в удобной тестам.
    """
    shutil.copytree(REGISTRIES_ROOT, tmp_path, dirs_exist_ok=True)
    shutil.copytree(BUSINESS_ROOT / "business", tmp_path / "business")
    return tmp_path


def business_doc(block: dict[str, object], *, filled: bool = False) -> str:
    """Текст бизнес-документа с одной секцией.

    Подсказка в пустой секции — HTML-комментарий: обычный текст сделал бы
    `is_section_empty` ложным, и каждый новый документ выглядел бы написанным.
    """
    front = yaml.safe_dump({"schema": "business/1", **block}, allow_unicode=True, sort_keys=False)
    body = "Написано." if filled else "<!-- Подсказка. -->"
    return (
        "---\n"
        + "docpipe:\n"
        + "".join(f"  {line}\n" for line in front.strip().splitlines())
        + "docpipe_state:\n  accepted: null\n  review: null\n"
        + "---\n\n"
        + f"# {block.get('title', 'Документ')}\n\n"
        + "<!-- docpipe:generated:start -->\n<!-- docpipe:generated:end -->\n\n"
        + "## Зачем процесс существует\n\n"
        + f"<!-- docpipe:section:start purpose -->\n{body}\n"
        + "<!-- docpipe:section:end purpose -->\n"
    )


def write_doc(root: Path, business_root: str, block: dict[str, object], **kwargs: bool) -> Path:
    """Положить документ по пути, выведенному из идентификатора."""
    path = root / doc_path_for(str(block["id"]), business_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(business_doc(block, **kwargs), encoding="utf-8")
    return path


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
