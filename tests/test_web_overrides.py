"""Ручной состав страниц: `pages.yaml` (P06).

Состав документации — решение человека. Две половины задачи одинаково важны:
правило должно применяться так, будто страницу нашёл обход, и оно обязано
**громко устаревать**. Компонент переименовали, правило перестало совпадать,
страница тихо исчезла из документации — в отчёте просто на строку меньше,
и об этом никто не узнает.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.web.overrides import AddPage, Overrides, RemovePage, load_overrides
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")

SHELL = "src/app/routes/shell.component.ShellComponent"
BANNER = "src/app/legacy/legacy.module.LegacyBannerComponent"
LIST = "src/app/routes/models/list/list.component.ListComponent"


def _scan(workspace: Path, overrides: Overrides) -> WebScanResult:
    return run_web(workspace, DocpipeConfig(), load_ruleset(RULES, "web"), None, overrides)


def _kind(manifest: Manifest, title: str) -> str:
    return next(node.kind for node in manifest.nodes if node.title == title)


# --------------------------------------------------------------------------------------
# Добавление
# --------------------------------------------------------------------------------------


def test_added_page_is_indistinguishable_from_a_found_one(web_workspace: Path) -> None:
    """Ручная страница — запись маршрута из другого источника, а не второй механизм.

    Отличать её должно ровно одно поле — источник записи; всё остальное
    (вид, маршрут, документ, якорь) обязано работать как у найденной обходом.
    """
    result = _scan(
        web_workspace,
        Overrides(add=[AddPage(route="/legacy/banner", component=BANNER, reason="печать")]),
    )
    node = next(item for item in result.manifest.nodes if item.title == "LegacyBannerComponent")

    assert node.kind == "page"
    assert [(entry.path, entry.source, entry.table) for entry in node.routes] == [
        ("legacy/banner", "pages.yaml", "manual")
    ]
    assert result.overrides.added == [BANNER]


def test_route_of_an_added_page_is_normalized(web_workspace: Path) -> None:
    """Тем же `normalize_route`, что и обе технические стороны.

    Своя копия разошлась бы молча, и якорь `/Models/:id` перестал бы совпадать
    с узлом `models/{}`.
    """
    result = _scan(
        web_workspace,
        Overrides(add=[AddPage(route="/Legacy/:id/", component=BANNER, reason="печать")]),
    )
    node = next(item for item in result.manifest.nodes if item.title == "LegacyBannerComponent")

    assert [entry.path for entry in node.routes] == ["legacy/{}"]


def test_add_for_an_unknown_component_is_a_finding(web_workspace: Path) -> None:
    """Иначе переименование класса тихо уносит страницу из документации."""
    result = _scan(
        web_workspace,
        Overrides(add=[AddPage(route="/x", component="src/app/gone.Missing", reason="печать")]),
    )

    assert [(item.kind, item.key) for item in result.overrides.stale] == [
        ("add-missed", "src/app/gone.Missing")
    ]


def test_add_that_became_redundant_is_a_finding_too(web_workspace: Path) -> None:
    """Маршрут стал находиться сам — правило больше не нужно, и это надо сказать."""
    result = _scan(
        web_workspace,
        Overrides(add=[AddPage(route="/models", component=LIST, reason="было не видно")]),
    )

    assert [item.kind for item in result.overrides.stale] == ["add-redundant"]


# --------------------------------------------------------------------------------------
# Снятие
# --------------------------------------------------------------------------------------


def test_removed_page_stays_a_component(web_workspace: Path) -> None:
    """Снятие отменяет повышение, а не удаляет узел: класс документируется как компонент."""
    result = _scan(
        web_workspace, Overrides(remove=[RemovePage(route="/", reason="layout без экрана")])
    )

    assert _kind(result.manifest, "ShellComponent") == "component"
    assert result.overrides.removed == [SHELL]


def test_removed_page_keeps_its_route_as_a_fact(web_workspace: Path) -> None:
    """Маршрут в таблице есть — это факт кода, и стирать его нельзя.

    Он же единственный признак, по которому отчёт отличает снятие руками
    от «маршрута нет вовсе».
    """
    result = _scan(web_workspace, Overrides(remove=[RemovePage(component=SHELL, reason="layout")]))
    node = next(item for item in result.manifest.nodes if item.title == "ShellComponent")

    assert [entry.path for entry in node.routes] == [""]


def test_removed_page_disappears_from_the_business_coverage(web_workspace: Path) -> None:
    """Иначе снятый экран продолжает требовать бизнес-документ."""
    from docpipe.business.resolve import build_context

    result = _scan(web_workspace, Overrides(remove=[RemovePage(component=SHELL, reason="layout")]))
    # Технический манифест здесь не важен: индекс страниц строится по web-манифесту.
    context = build_context([], result.manifest, web=result.manifest)

    assert "" not in context.pages_by_route


def test_ambiguous_removal_by_route_is_refused(web_workspace: Path) -> None:
    """Компонент на двух маршрутах снимается целиком — значит намеренно.

    В боевом модуле так стои́т `StructuringCreatePage`: `create` и `edit` —
    один класс. Снятие по одному маршруту убрало бы и второй.
    """
    result = _scan(
        web_workspace,
        Overrides(add=[AddPage(route="/models/copy", component=LIST, reason="вторая точка")]),
    )
    assert result.overrides.added == [LIST]

    with pytest.raises(ValueError, match="неоднозначно"):
        _scan(
            web_workspace,
            Overrides(
                add=[AddPage(route="/models/copy", component=LIST, reason="вторая точка")],
                remove=[RemovePage(route="/models/copy", reason="лишняя")],
            ),
        )


def test_removal_wins_over_addition(web_workspace: Path) -> None:
    """Порядок фиксирован: автоматика -> add -> remove."""
    result = _scan(
        web_workspace,
        Overrides(
            add=[AddPage(route="/legacy/banner", component=BANNER, reason="печать")],
            remove=[RemovePage(component=BANNER, reason="передумали")],
        ),
    )

    assert _kind(result.manifest, "LegacyBannerComponent") == "component"


def test_rule_order_does_not_change_the_result(web_workspace: Path) -> None:
    straight = _scan(
        web_workspace,
        Overrides(
            add=[
                AddPage(route="/a", component=BANNER, reason="раз"),
                AddPage(route="/b", component="src/app/gone.Missing", reason="два"),
            ]
        ),
    )
    reversed_ = _scan(
        web_workspace,
        Overrides(
            add=[
                AddPage(route="/b", component="src/app/gone.Missing", reason="два"),
                AddPage(route="/a", component=BANNER, reason="раз"),
            ]
        ),
    )

    assert straight.manifest.model_dump_json() == reversed_.manifest.model_dump_json()


# --------------------------------------------------------------------------------------
# Загрузка файла
# --------------------------------------------------------------------------------------


def test_rule_without_a_reason_is_rejected(tmp_path: Path) -> None:
    """«Сняли 12 страниц» без причин — снова безымянное число."""
    path = tmp_path / "pages.yaml"
    path.write_text(
        'version: "1"\npages:\n  add:\n    - route: "/x"\n      component: "a.B"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_overrides(path)


def test_removal_needs_exactly_one_key(tmp_path: Path) -> None:
    path = tmp_path / "pages.yaml"
    path.write_text(
        'version: "1"\npages:\n  remove:\n    - route: "/x"\n      component: "a.B"\n'
        '      reason: "оба ключа"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ровно одно"):
        load_overrides(path)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    """Опечатка в имени ключа иначе выглядит как правило, которое не сработало."""
    path = tmp_path / "pages.yaml"
    path.write_text(
        'version: "1"\npages:\n  add:\n    - route: "/x"\n      componen: "a.B"\n'
        '      reason: "опечатка"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_overrides(path)


def test_example_file_loads(tmp_path: Path) -> None:
    """Пример в репозитории обязан быть рабочим файлом, а не текстом про файл."""
    overrides = load_overrides(Path("pages.example.yaml"))

    assert len(overrides.add) == 2
    assert len(overrides.remove) == 2
    assert all(rule.reason for rule in overrides.add)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_cli_reports_stale_rules_and_fails_only_when_asked(
    web_workspace: Path, tmp_path: Path
) -> None:
    """Находка печатается всегда, код возврата меняет только флаг.

    Линт, красный с первого дня, выключат на второй, и вместе с ним пропадут
    работающие проверки.
    """
    pages = tmp_path / "pages.yaml"
    pages.write_text(
        'version: "1"\npages:\n  add:\n    - route: "/x"\n'
        '      component: "src/app/gone.Missing"\n      reason: "протухло"\n',
        encoding="utf-8",
    )
    arguments = [
        "web",
        "scan",
        "--root",
        str(web_workspace),
        "--pages",
        str(pages),
        "--out",
        str(tmp_path / "w.json"),
    ]

    soft = runner.invoke(app, arguments)
    assert soft.exit_code == 0
    assert "не легло" in soft.output

    hard = runner.invoke(app, [*arguments, "--fail-on-stale-overrides"])
    assert hard.exit_code == 1


def test_cli_refuses_an_ambiguous_removal(web_workspace: Path, tmp_path: Path) -> None:
    pages = tmp_path / "pages.yaml"
    pages.write_text(
        'version: "1"\npages:\n  add:\n    - route: "/models/copy"\n'
        f'      component: "{LIST}"\n      reason: "вторая точка"\n'
        '  remove:\n    - route: "/models/copy"\n      reason: "лишняя"\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(web_workspace),
            "--pages",
            str(pages),
            "--out",
            str(tmp_path / "w.json"),
        ],
    )

    assert result.exit_code == 2
    assert "неоднозначно" in result.output
