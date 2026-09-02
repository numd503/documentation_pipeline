"""Шаг 2 для фронта: документы фронта той же машиной, что и .NET (F14).

Проверяется главным образом то, что различает шаблон и стили: переписанный
экран обязан помечать документ устаревшим, а правка `.scss` — нет.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.documents.zones import assemble, is_section_empty, parse_document
from docpipe.materialize.template import load_templates
from docpipe.model import Manifest
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")
WEB_TEMPLATES = ("page", "component", "api-service", "state")


@pytest.fixture
def workspace(web_workspace: Path, tmp_path: Path) -> Path:
    """Копия фикстуры: тесты правят исходники, а фикстура обязана пережить прогон."""
    target = tmp_path / "WebWorkspace"
    shutil.copytree(web_workspace, target)
    return target


def _manifest(root: Path) -> Manifest:
    return run_web(root, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


def _node(manifest: Manifest, title: str) -> object:
    return next(node for node in manifest.nodes if node.title == title)


def _impl_hash(root: Path, title: str) -> str:
    node = _node(_manifest(root), title)
    return node.impl_hash  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------
# Шаблон компонента входит в impl_hash, стили — нет
# --------------------------------------------------------------------------------------


def test_template_is_a_source_of_the_component(workspace: Path) -> None:
    """Путь шаблона попадает в узел: агент шага 3 читает список источников."""
    node = _node(_manifest(workspace), "ListComponent")
    paths = [source.path for source in node.symbol.sources]  # type: ignore[attr-defined]

    assert paths == [
        "src/app/routes/models/list/list.component.ts",
        "src/app/routes/models/list/list.component.html",
    ]


def test_editing_the_template_changes_impl_hash(workspace: Path) -> None:
    """Переписанный экран обязан помечать документ устаревшим."""
    before = _impl_hash(workspace, "ListComponent")

    template = workspace / "src/app/routes/models/list/list.component.html"
    template.write_text(template.read_text(encoding="utf-8") + "\n<p>ещё блок</p>\n", "utf-8")

    assert _impl_hash(workspace, "ListComponent") != before


def test_editing_the_styles_does_not_change_impl_hash(workspace: Path) -> None:
    """Правка `.scss` смысла документа не меняет."""
    before = _impl_hash(workspace, "ListComponent")

    styles = workspace / "src/app/routes/models/list/list.component.scss"
    styles.write_text(styles.read_text(encoding="utf-8") + "\n.x { color: red; }\n", "utf-8")

    assert _impl_hash(workspace, "ListComponent") == before


def test_component_without_an_external_template_still_builds(workspace: Path) -> None:
    """Шаблон строкой — обычное дело; `impl_hash` обязан собраться, а не упасть."""
    node = _node(_manifest(workspace), "ShellComponent")
    assert [source.path for source in node.symbol.sources] == [  # type: ignore[attr-defined]
        "src/app/routes/shell.component.ts"
    ]


def test_missing_template_file_does_not_break_the_run(workspace: Path) -> None:
    """`templateUrl` может указывать на файл, которого нет: отказ тут не поможет."""
    (workspace / "src/app/routes/models/list/list.component.html").unlink()
    assert _node(_manifest(workspace), "ListComponent") is not None


# --------------------------------------------------------------------------------------
# Материализация
# --------------------------------------------------------------------------------------


@pytest.fixture
def materialized(workspace: Path, tmp_path: Path) -> tuple[Path, Path]:
    manifest_path, docs = tmp_path / "web.json", tmp_path / "docs-root"
    runner.invoke(
        app,
        ["web", "scan", "--root", str(workspace), "--out", str(manifest_path), "--no-cache"],
    )
    result = runner.invoke(app, ["materialize", str(manifest_path), "--root", str(docs)])
    assert result.exit_code == 0, result.output
    return manifest_path, docs


def test_materialize_creates_documents(materialized: tuple[Path, Path]) -> None:
    manifest_path, docs = materialized
    status = runner.invoke(app, ["docs", "status", str(manifest_path), "--root", str(docs)])

    assert status.exit_code == 0, status.output
    assert "write" in status.output
    assert "missing" not in status.output


def test_page_document_carries_its_route(materialized: tuple[Path, Path]) -> None:
    """Маршрут — якорь страницы, и он обязан быть виден в документе."""
    _, docs = materialized
    text = (docs / "docs/modules/pages/tr-p/quiz-component.md").read_text(encoding="utf-8")

    assert "### Маршрут страницы" in text
    assert "/models/loader/quiz" in text


def test_shared_service_keeps_its_own_document(materialized: tuple[Path, Path]) -> None:
    """Свой документ остаётся у сервиса, которого зовут ДВЕ страницы.

    `AuditService` зовут `ListComponent` и `QuizComponent`; вложить его в одну
    значило бы завести две копии одного текста. А `ItemsService`, который зовёт
    одна страница, своего файла больше не получает — он описан внутри неё.
    """
    _, docs = materialized
    text = (docs / "docs/modules/api-services/tr-p/audit-service.md").read_text(encoding="utf-8")

    assert "### Вызовы к бэкенду" in text
    assert "integration/log/auditj" in text
    assert not (docs / "docs/modules/api-services/tr-p/items-service.md").exists()


def test_unresolved_route_is_printed_in_words(materialized: tuple[Path, Path]) -> None:
    """Пропуск неотличим от «страницы нет», и покрытие занизилось бы молча."""
    _, docs = materialized
    text = (docs / "docs/modules/pages/tr-p/list-component.md").read_text(encoding="utf-8")

    assert "маршрут собрать не удалось" in text


def test_dotnet_document_has_no_web_sections(tmp_path: Path) -> None:
    """У контроллера нет маршрута страницы, и пустой раздел был бы шумом.

    Симметрии здесь нет и в предметной области: разделы печатаются у узлов
    модулей с `lang: ts` и только у них.
    """
    docs = tmp_path / "dotnet-docs"
    runner.invoke(app, ["materialize", "tests/golden/doc-tree.json", "--root", str(docs)])
    text = (docs / "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md").read_text(
        encoding="utf-8"
    )

    assert "### Маршрут страницы" not in text
    assert "### HTTP-эндпоинты" in text


# --------------------------------------------------------------------------------------
# Скелеты
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", WEB_TEMPLATES)
def test_web_template_round_trips(name: str) -> None:
    """`assemble(parse_document(t)) == t` байт в байт.

    Инвариант, на котором стоит всё разделение зон: затирается только то,
    что принадлежит инструменту.
    """
    text = Path(f"templates/{name}.md").read_text(encoding="utf-8")
    assert assemble(parse_document(text)) == text


@pytest.mark.parametrize("name", WEB_TEMPLATES)
def test_web_template_hints_are_html_comments(name: str) -> None:
    """`is_section_empty` — самая дорогая функция шага 2.

    Ошибка в сторону «не пусто» заставит агента счесть всё дерево написанным.
    """
    document = parse_document(load_templates(Path("templates"))[name].text)
    sections = [item for item in document.segments if item.kind == "section"]

    assert sections
    for section in sections:
        assert is_section_empty(section.body), section.name
