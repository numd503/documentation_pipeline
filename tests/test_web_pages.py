"""Список страниц и обоснование каждой (F19).

Проверяется не «команда отработала», а то, ради чего она заведена: по выводу
должно быть **проверяемо**, почему класс стал страницей. Вид `page` —
единственный, который выдаётся не правилом, а повышением по таблице роутов,
и до этой команды цифру «страниц N» нечем было сверить с кодом.

Отдельная группа — про наблюдения отчёта. Каждое из них подсказка, и ни одно
не меняет `kind` в манифесте: угаданная страница ломает якорь бизнес-документа,
угаданная не-страница молча выкидывает экран из документации.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.web.pages import (
    NOTE_EMPTY_ROUTE,
    NOTE_NO_CALLS,
    NOTE_NO_FEATURES,
    NOTE_UNANCHORABLE,
    Page,
    build_report,
    format_report,
    report_csv,
    report_json,
)
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")


@pytest.fixture
def manifest(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


def _page(manifest: Manifest, title: str, depth: int = 2) -> Page:
    report = build_report(manifest, depth=depth)
    found = [page for page in report.pages if page.title == title]
    assert len(found) == 1, f"{title}: найдено {len(found)}"
    return found[0]


# --------------------------------------------------------------------------------------
# Что собралось и почему
# --------------------------------------------------------------------------------------


def test_pages_are_the_promoted_components(manifest: Manifest) -> None:
    report = build_report(manifest)

    assert report.counts["pages"] == 5
    assert [page.title for page in report.pages] == [
        "ShellComponent",
        "ForecastComponent",
        "ListComponent",
        "QuizComponent",
        "DetailComponent",
    ]


def test_route_names_the_file_and_the_table_it_came_from(manifest: Manifest) -> None:
    """Главное свойство отчёта: маршрут можно проверить, открыв названный файл.

    Дерево собирается межфайлово, и по узлу не видно, какой из массивов
    `Routes` привёл сюда: `/models/loader/quiz` объявлен не в `app.routes.ts`,
    через который до него доходит обход, а в `routesPath/models.ts`.
    """
    quiz = _page(manifest, "QuizComponent")

    assert [item.path for item in quiz.routes] == ["models/loader/quiz"]
    assert quiz.routes[0].source == "src/app/routesPath/models.ts"
    assert quiz.routes[0].table == "modelsPath"


def test_rule_said_component_and_the_report_says_so(manifest: Manifest) -> None:
    """`matched_rules` при повышении не меняется: там записано, что сработало."""
    quiz = _page(manifest, "QuizComponent")

    assert quiz.matched_rules == ["web.component"]
    assert "component" in format_report(build_report(manifest))


def test_calls_are_found_through_dependencies_with_the_step_named(manifest: Manifest) -> None:
    """Вызовы лежат на узле сервиса, а не страницы, — значит шаг обязан быть виден."""
    detail = _page(manifest, "DetailComponent")

    assert detail.calls, "вызовы страницы собираются обходом зависимостей"
    assert all(call.through == "ModelService" for call in detail.calls)
    assert all(call.depth == 1 for call in detail.calls)
    assert ("GET", "api/ml/structure/{}") in {
        (call.http_method, call.route) for call in detail.calls
    }


def test_depth_zero_leaves_only_calls_of_the_component_itself(manifest: Manifest) -> None:
    """Глубина — не украшение вывода: на нуле у страницы остаются свои вызовы.

    В Angular компонент почти никогда не зовёт `http` сам, поэтому ноль здесь
    и означает пустой список. Если он однажды перестанет означать это, тест
    покажет, что вызовы поехали не на тот узел.
    """
    assert _page(manifest, "DetailComponent", depth=0).calls == []
    assert _page(manifest, "DetailComponent", depth=1).calls != []


def test_components_without_a_route_are_listed_with_the_reason(manifest: Manifest) -> None:
    """Обратная сторона: «почему этого экрана нет среди страниц»."""
    report = build_report(manifest)

    assert [item.title for item in report.not_pages] == [
        "LegacyBannerComponent",
        "WidgetComponent",
    ]
    assert all("маршрута нет" in item.reason for item in report.not_pages)


# --------------------------------------------------------------------------------------
# Наблюдения отчёта — подсказки, а не смена вида
# --------------------------------------------------------------------------------------


def test_layout_is_marked_but_stays_a_page(manifest: Manifest) -> None:
    """`ShellComponent` — обёртка с `<router-outlet>`: маршрут пустой, функционала нет.

    Отчёт обязан это назвать и обязан **не** менять вид: решение о том, что
    считать страницей, принимает человек правилами и конфигурацией.
    """
    shell = _page(manifest, "ShellComponent")

    assert NOTE_EMPTY_ROUTE in shell.notes
    assert NOTE_NO_FEATURES in shell.notes
    assert {node.kind for node in manifest.nodes if node.title == "ShellComponent"} == {"page"}


def test_page_with_features_but_no_calls_names_the_ngxs_gap(manifest: Manifest) -> None:
    """«Вызовов ноль» и «страница пустая» — разные утверждения.

    Экшен NGXS создаётся в теле метода, а не внедряется конструктором, поэтому
    обход по зависимостям похода за данными не видит. Отчёт, оставивший ноль
    без объяснения, приведёт к решению «документировать нечего».

    Зависимости снимаются с готового узла: страница с членами и без единой
    разрешённой зависимости — это и есть форма, в которой компонент ходит
    за данными только через `dispatch`.
    """
    node = next(item for item in manifest.nodes if item.title == "DetailComponent")
    without_deps = node.model_copy(update={"dependencies": []})
    trimmed = manifest.model_copy(
        update={"nodes": [without_deps if item.id == node.id else item for item in manifest.nodes]}
    )

    page = _page(trimmed, "DetailComponent")

    assert page.members == 3
    assert NOTE_NO_CALLS in page.notes
    assert NOTE_NO_FEATURES not in page.notes


def test_page_whose_routes_are_all_unresolved_is_named(manifest: Manifest) -> None:
    """Такая страница есть, а якоря у неё нет: `pages_by_route` её пропускает.

    До этого отчёта о ней не сообщал никто: вид `page` она получила,
    а в покрытие страниц не попала.
    """
    node = next(node for node in manifest.nodes if node.title == "ListComponent")
    only_unresolved = node.model_copy(
        update={"routes": [entry for entry in node.routes if entry.route_unresolved]}
    )
    trimmed = manifest.model_copy(
        update={
            "nodes": [only_unresolved if item.id == node.id else item for item in manifest.nodes]
        }
    )

    report = build_report(trimmed)
    page = next(item for item in report.pages if item.title == "ListComponent")

    assert NOTE_UNANCHORABLE in page.notes
    assert report.counts["unanchorable"] == 1


# --------------------------------------------------------------------------------------
# Сужение и счётчики
# --------------------------------------------------------------------------------------


def test_counters_are_computed_over_the_whole_tree(manifest: Manifest) -> None:
    """Сужение отвечает на «покажи эти», а не на «сколько их всего».

    То же правило, что у очереди шага 3: сводка, посчитанная по срезу,
    объявила бы, что страниц одна.
    """
    report = build_report(manifest, route="quiz")

    assert [page.title for page in report.pages] == ["QuizComponent"]
    assert report.counts["pages"] == 5


def test_module_filter_narrows_both_halves(manifest: Manifest) -> None:
    report = build_report(manifest, module="widget")

    assert report.pages == []
    assert [item.title for item in report.not_pages] == ["WidgetComponent"]


# --------------------------------------------------------------------------------------
# Детерминизм и CLI
# --------------------------------------------------------------------------------------


def test_output_is_deterministic(manifest: Manifest) -> None:
    """Отчёт сравнивают между прогонами — значит он обязан совпадать байт в байт."""
    first, second = build_report(manifest), build_report(manifest)

    assert report_json(first) == report_json(second)
    assert report_csv(first) == report_csv(second)
    assert format_report(first) == format_report(second)


def test_csv_has_a_row_per_page_and_unix_line_endings(manifest: Manifest) -> None:
    text = report_csv(build_report(manifest))

    assert "\r\n" not in text
    assert len(text.strip().splitlines()) == 1 + 5


def test_cli_prints_pages_and_the_reason(web_workspace: Path, tmp_path: Path) -> None:
    destination = tmp_path / "web.json"
    scanned = runner.invoke(
        app, ["web", "scan", "--root", str(web_workspace), "--out", str(destination)]
    )
    assert scanned.exit_code == 0, scanned.output

    result = runner.invoke(app, ["web", "pages", str(destination), "--not-pages"])

    assert result.exit_code == 0, result.output
    assert "/models/loader/quiz" in result.output
    assert "src/app/routesPath/models.ts" in result.output
    assert "LegacyBannerComponent" in result.output


def test_cli_rejects_an_unknown_format(tmp_path: Path, manifest: Manifest) -> None:
    destination = tmp_path / "web.json"
    destination.write_text(manifest.model_dump_json(), encoding="utf-8")

    result = runner.invoke(app, ["web", "pages", str(destination), "--format", "yaml"])

    assert result.exit_code == 2
    assert "yaml" in result.output


def test_cli_explains_an_empty_list(tmp_path: Path, manifest: Manifest) -> None:
    """Ноль страниц при непустом манифесте — почти всегда неопознанная таблица.

    Форма `RouterModule.forRoot([...])` не даёт ни одной записи и ни одной
    ошибки разбора, и без подсказки это выглядит как «фронт без страниц».
    """
    without_pages = manifest.model_copy(
        update={"nodes": [node for node in manifest.nodes if node.kind != "page"]}
    )
    destination = tmp_path / "web.json"
    destination.write_text(without_pages.model_dump_json(), encoding="utf-8")

    result = runner.invoke(app, ["web", "pages", str(destination)])

    assert result.exit_code == 0
    assert "RouterModule.forRoot" in result.output
