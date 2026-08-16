"""Документ страницы как агрегат (P09).

Единица документации — экран, а не класс. Сервис, до которого дотягивается
только эта страница, описывается здесь же; общий — остаётся отдельным
документом и попадает сюда ссылкой: копия текста разошлась бы с оригиналом
на первой правке.

Отсюда же два хэша: правка поглощённого сервиса обязана помечать документ
страницы устаревшим — своего документа у сервиса больше нет, и заметить
изменение больше негде.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import DocNode, Manifest, SourceSpan, Symbol, Usage
from docpipe.web.absorb import absorb
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")


@pytest.fixture
def documents(web_workspace: Path, tmp_path: Path) -> Path:
    manifest = tmp_path / "web.json"
    docs = tmp_path / "docs-root"
    docs.mkdir()

    scan = runner.invoke(app, ["web", "scan", "--root", str(web_workspace), "--out", str(manifest)])
    assert scan.exit_code == 0, scan.output

    result = runner.invoke(app, ["materialize", str(manifest), "--root", str(docs)])
    assert result.exit_code == 0, result.output
    return docs


def _page(documents: Path, name: str) -> str:
    return (documents / f"docs/modules/pages/tr-p/{name}.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Разделы
# --------------------------------------------------------------------------------------


def test_absorbed_node_has_no_document_of_its_own(documents: Path) -> None:
    """Второй файл про то же разошёлся бы с разделом страницы на первой правке."""
    assert not (documents / "docs/modules/api-services/tr-p/items-service.md").exists()
    assert (documents / "docs/modules/pages/tr-p/quiz-component.md").exists()


def test_page_document_names_its_composition(documents: Path) -> None:
    text = _page(documents, "list-component")

    assert "### Состав документа" in text
    assert "`InnerDebtService` (api-service) — своего документа нет, описывается здесь" in text


def test_shared_node_is_a_link_not_a_copy(documents: Path) -> None:
    """То же правило, по которому бизнес-слой ссылается на технический."""
    text = _page(documents, "list-component")

    assert "Общие узлы, у которых свой документ" in text
    assert "audit-service.md" in text


def test_state_section_names_the_state_by_its_decorator(documents: Path) -> None:
    """`innerDebt`, а не `DebtState`: переименование класса смысла не меняет."""
    text = _page(documents, "list-component")

    assert "### Состояние" in text
    assert "`innerDebt`" in text


def test_data_section_shows_the_path_of_every_endpoint(documents: Path) -> None:
    """Список эндпоинтов — вывод по графу, и путь обязан быть виден."""
    text = _page(documents, "list-component")

    assert "### Данные" in text
    assert "`InnerDebtService.byClient`" in text
    assert "`[Inner Debt] Load`" in text


def test_child_pages_are_linked(documents: Path) -> None:
    text = _page(documents, "shell-component")

    assert "### Дочерние страницы" in text
    assert "list-component.md" in text


def test_author_sections_are_present_and_empty(documents: Path) -> None:
    """Подсказки — только HTML-комментарии: иначе раздел считается написанным."""
    text = _page(documents, "quiz-component")

    assert "docpipe:section:start state" in text
    assert "docpipe:section:start logic" in text


def test_service_document_has_no_page_sections(documents: Path) -> None:
    """У сервиса нет ни состава, ни дочерних страниц, и пустые разделы были бы шумом."""
    text = (documents / "docs/modules/api-services/tr-p/audit-service.md").read_text(
        encoding="utf-8"
    )

    assert "### Состав документа" not in text
    assert "### Дочерние страницы" not in text


# --------------------------------------------------------------------------------------
# Хэши агрегата
# --------------------------------------------------------------------------------------


def _pair(inner_impl: str, inner_id: str = "type:src#svc") -> DocNode:
    page = DocNode(
        id="type:src#page",
        kind="page",
        template="page",
        title="Page",
        doc_path="docs/p.md",
        module="src",
        domain="src",
        signature_hash="sha256:page",
        impl_hash="sha256:page-impl",
        uses=[Usage(target="src.Svc", member="load")],
    )
    service = DocNode(
        id=inner_id,
        kind="service",
        template="service",
        title="Svc",
        doc_path="docs/s.md",
        module="src",
        domain="src",
        signature_hash="sha256:svc",
        impl_hash=inner_impl,
        symbol=Symbol(
            fqn="src.Svc",
            name="Svc",
            namespace="src",
            module="src",
            type_kind="class",
            sources=[SourceSpan(path="svc.ts", start=1, end=5)],
        ),
    )
    return absorb([page, service])[0]


def test_editing_an_absorbed_node_makes_the_page_stale() -> None:
    """Иначе раздел «Логика» остаётся ложью, и заметить это негде."""
    assert _pair("sha256:before").impl_hash != _pair("sha256:after").impl_hash


def test_composition_change_shows_in_the_signature_hash() -> None:
    """Узел ушёл или пришёл — документ устарел, даже если код не менялся."""
    assert (
        _pair("sha256:same", "type:src#svc").signature_hash
        != _pair("sha256:same", "type:src#other").signature_hash
    )


def test_page_without_absorbed_nodes_keeps_its_hashes() -> None:
    """Агрегат не должен менять хэши там, где агрегата нет."""
    page = DocNode(
        id="type:src#alone",
        kind="page",
        template="page",
        title="Alone",
        doc_path="docs/a.md",
        module="src",
        domain="src",
        signature_hash="sha256:s",
        impl_hash="sha256:i",
    )
    result = absorb([page])[0]

    assert (result.signature_hash, result.impl_hash) == ("sha256:s", "sha256:i")


def test_hashes_are_stable_between_runs(web_workspace: Path) -> None:
    first = run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest
    second = run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest

    assert _hashes(first) == _hashes(second)


def _hashes(manifest: Manifest) -> dict[str, tuple[str, str]]:
    return {node.id: (node.signature_hash, node.impl_hash) for node in manifest.nodes}
