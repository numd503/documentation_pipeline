"""Бизнес-слой и фронт: якорь `page` (F15).

Стрелка та же, что во всей конструкции: бизнес-документ ссылается на **экран**,
а не на компонент. `/models/loader/quiz` знает пользователь, на него ведёт
закладка, его же пишет аналитик. `LoaderQuizComponent` не знает никто снаружи,
и переименование класса не должно ломать документ.
"""

from pathlib import Path

import pytest

from docpipe.business.fingerprint import business_hash
from docpipe.business.model import Anchor
from docpipe.business.resolve import ResolveContext, build_context, resolve
from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.web.tree import run as run_web

RULES = Path("rules/rules.yaml")


@pytest.fixture
def web(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


@pytest.fixture
def ctx(web: Manifest) -> ResolveContext:
    empty = Manifest(ruleset_version="x", parser=web.parser)
    return build_context([], empty, web=web)


def _page(ref: str) -> Anchor:
    return Anchor(kind="page", ref=ref)


# --------------------------------------------------------------------------------------
# Разрешение якоря
# --------------------------------------------------------------------------------------


def test_page_anchor_resolves_to_a_web_node(ctx: ResolveContext) -> None:
    resolution = resolve(_page("/models/loader/quiz"), ctx)

    assert resolution.resolved
    assert [target.doc_path for target in resolution.targets] == [
        "docs/modules/pages/tr-p/quiz-component.md"
    ]


def test_route_is_normalised_by_the_shared_function(ctx: ResolveContext) -> None:
    """Своя копия нормализации разошлась бы с обеими техническими сторонами.

    `/models/:id` в документе и `models/{}` в манифесте — один и тот же экран.
    """
    assert resolve(_page("/models/:id"), ctx).resolved
    assert resolve(_page("models/{}"), ctx).resolved
    assert resolve(_page("/MODELS/:id/"), ctx).resolved


def test_missing_page_is_unresolved_and_says_what_was_tried(ctx: ResolveContext) -> None:
    resolution = resolve(_page("/nowhere"), ctx)

    assert not resolution.resolved
    assert resolution.tried == ["реестр", "страница"]


def test_literal_rung_is_not_tried_for_a_page(ctx: ResolveContext) -> None:
    """Совпадение маршрута строкой где-нибудь в коде доказательством не является.

    Страница либо объявлена в манифесте фронта, либо её нет.
    """
    assert "литерал" not in resolve(_page("/nowhere"), ctx).tried


def test_unresolved_route_does_not_become_an_anchor(ctx: ResolveContext) -> None:
    """Ветку, чей путь собрать не удалось, на якорь не поставить.

    Иначе все такие ветки склеились бы в один ключ — путь у них общий, родительский.
    """
    unresolved = [
        node for node in ctx.pages_by_route.get("models", []) if node.title == "ListComponent"
    ]
    assert len(unresolved) == 1  # только собранный маршрут, не вторая ветка


def test_repository_without_a_front_end_has_no_pages() -> None:
    """Пустой словарь страниц — законное состояние, а не отказ."""
    from docpipe.model import ParserVersions

    empty = Manifest(ruleset_version="x", parser=ParserVersions(tree_sitter="0"))
    assert build_context([], empty).pages_by_route == {}


# --------------------------------------------------------------------------------------
# Путь «страница → список»
# --------------------------------------------------------------------------------------


def test_page_facts_carry_the_registry_lists(web_workspace: Path) -> None:
    """Обращение к реестру связывает бизнес-документ со списком напрямую.

    Считается по графу вызовов — тому же, по которому собирается документ
    страницы: вторая реализация обхода разошлась бы с ним, и хэш менялся бы
    от того, чего в документе не видно.
    """
    from docpipe.config import WebConfig

    config = DocpipeConfig(
        web=WebConfig.model_validate(
            {
                "registry_calls": [
                    {
                        "route": "api/items",
                        "discriminator": {"in": "query", "name": "listInnerName"},
                        "kind": "list",
                    }
                ]
            }
        )
    )
    manifest = run_web(web_workspace, config, load_ruleset(RULES, "web")).manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)
    resolution = resolve(_page("/models/loader/quiz"), build_context([], empty, web=manifest))

    assert resolution.facts == {
        "route": "models/loader/quiz",
        "lists": ["dictionaries"],
        # Эндпоинты — HTTP-контракт, переживающий переименование чего угодно.
        "endpoints": ["GET api/items", "POST integration/log/auditj"],
    }


def test_page_without_registry_calls_carries_only_its_route(ctx: ResolveContext) -> None:
    """`route` в фактах есть всегда: у неразрешённого якоря фактов нет вовсе,
    и именно этим исчезновение страницы становится видно в хэше."""
    assert resolve(_page("/forecast/daily"), ctx).facts == {"route": "forecast/daily"}


# --------------------------------------------------------------------------------------
# business_hash: что создаёт работу, а что нет
# --------------------------------------------------------------------------------------


def test_renaming_the_component_does_not_change_the_hash(
    web_workspace: Path, tmp_path: Path
) -> None:
    """Переименование класса бизнес-смысла не меняет — значит, и работы не создаёт."""
    import shutil

    copy = tmp_path / "workspace"
    shutil.copytree(web_workspace, copy)

    def hash_of(root: Path) -> str:
        manifest = run_web(root, DocpipeConfig(), load_ruleset(RULES, "web")).manifest
        empty = Manifest(ruleset_version="x", parser=manifest.parser)
        context = build_context([], empty, web=manifest)
        return business_hash([resolve(_page("/models/loader/quiz"), context)])

    before = hash_of(copy)

    for relative in (
        "src/app/routes/models/quiz/quiz.component.ts",
        "src/app/routesPath/models.ts",
    ):
        path = copy / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace("QuizComponent", "LoaderQuizScreen"), "utf-8"
        )

    assert hash_of(copy) == before


def test_changing_the_route_changes_the_hash(web_workspace: Path, tmp_path: Path) -> None:
    """Смена маршрута ломает закладку пользователя — это и есть смена смысла."""
    import shutil

    copy = tmp_path / "workspace"
    shutil.copytree(web_workspace, copy)

    def hash_of(root: Path) -> str:
        manifest = run_web(root, DocpipeConfig(), load_ruleset(RULES, "web")).manifest
        empty = Manifest(ruleset_version="x", parser=manifest.parser)
        context = build_context([], empty, web=manifest)
        return business_hash([resolve(_page("/models/loader/quiz"), context)])

    before = hash_of(copy)

    routes = copy / "src/app/routesPath/models.ts"
    routes.write_text(
        routes.read_text(encoding="utf-8").replace("'loader/quiz'", "'loader/exam'"), "utf-8"
    )

    assert hash_of(copy) != before


# --------------------------------------------------------------------------------------
# Покрытие страниц
# --------------------------------------------------------------------------------------


def test_uncovered_pages_are_counted_and_do_not_fail_the_run(ctx: ResolveContext) -> None:
    """Состояние работы, а не дефект: линт, красный с первого дня, выключат."""
    from docpipe.business.catalog import Catalog
    from docpipe.business.lint import INFORMATIONAL, lint

    report = lint(Catalog(docs=[]), [], ctx, "business")
    uncovered = [finding for finding in report.findings if finding.check == "pages-uncovered"]

    assert len(uncovered) == len(ctx.pages_by_route)
    assert "pages-uncovered" in INFORMATIONAL


def test_page_facts_carry_the_state_by_its_declared_name(web_workspace: Path) -> None:
    """`innerDebt`, а не `DebtState`: имя класса в бизнес-факты не попадает.

    Состояние — часть смысла экрана, и без него документ страницы описывал бы
    только походы за данными. Но берётся `name` из декоратора: переименование
    класса смысла не меняет, а имя стейта — контракт, по которому его селектят.
    """
    manifest = run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)
    facts = resolve(_page("/models"), build_context([], empty, web=manifest)).facts

    assert facts["states"] == ["innerDebt", "innerDebtAudit"]
    assert not any("DebtState" in str(value) for value in facts.values())


def test_renaming_a_state_class_does_not_change_the_hash(
    web_workspace: Path, tmp_path: Path
) -> None:
    """Тот же довод, что и у компонента: рефакторинг работы не создаёт."""
    first = _facts_of(web_workspace, "/models")

    copied = _copy_workspace(web_workspace, tmp_path)
    path = copied / "src/app/inner-debt/state/debt.state.ts"
    path.write_text(
        path.read_text(encoding="utf-8").replace("DebtState", "InnerDebtsState"),
        encoding="utf-8",
    )
    for neighbour in ("routes/models/list/list.component.ts",):
        target = copied / "src/app" / neighbour
        target.write_text(
            target.read_text(encoding="utf-8").replace("DebtState", "InnerDebtsState"),
            encoding="utf-8",
        )

    assert _facts_of(copied, "/models") == first


def test_renaming_the_state_itself_changes_the_hash(web_workspace: Path, tmp_path: Path) -> None:
    """А вот `name: 'innerDebt'` — контракт, и его смена работу создаёт."""
    copied = _copy_workspace(web_workspace, tmp_path)
    path = copied / "src/app/inner-debt/state/debt.state.ts"
    path.write_text(
        path.read_text(encoding="utf-8").replace("name: 'innerDebt'", "name: 'debts'"),
        encoding="utf-8",
    )

    assert _facts_of(copied, "/models") != _facts_of(web_workspace, "/models")


def _facts_of(workspace: Path, route: str) -> dict:
    manifest = run_web(workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest
    empty = Manifest(ruleset_version="x", parser=manifest.parser)
    return resolve(_page(route), build_context([], empty, web=manifest)).facts


def _copy_workspace(source: Path, tmp_path: Path) -> Path:
    import shutil

    target = tmp_path / "copy"
    shutil.copytree(source, target)
    return target
