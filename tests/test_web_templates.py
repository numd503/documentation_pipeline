"""Разбор Angular-шаблонов (G14).

Закрывается известная дыра: `(click)="save()"` и `service.list() | async`
живут в `.html`, и разбор `.ts` их не видит вовсе. Для страницы это
существенно — сервис, к которому обращаются прямо из разметки, иначе
не связан со страницей ничем.
"""

from pathlib import Path

import pytest

from docpipe.model import Attribute, Manifest, Member, SourceSpan, Symbol
from docpipe.web.templates import TEMPLATE_MEMBER, calls, expressions, template_of


def component(
    *,
    template: str = "",
    template_url: str = "",
    members: tuple[str, ...] = (),
    path: str = "src/app/x.component.ts",
) -> Symbol:
    named = {"selector": "app-x"}
    if template:
        named["template"] = template
    if template_url:
        named["templateUrl"] = template_url
    return Symbol(
        fqn="src/app/x.component.XComponent",
        name="XComponent",
        type_kind="class",
        namespace="",
        module="app",
        attributes=[Attribute(name="Component", named_args=named)],
        members=[
            Member(name=item, kind="method", signature=f"{item}()", line=1, end_line=2)
            for item in members
        ],
        sources=[SourceSpan(path=path, start=1, end=20)],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Откуда берётся шаблон
# ──────────────────────────────────────────────────────────────────────────────


def test_inline_and_external_templates_are_read_the_same(tmp_path: Path) -> None:
    """Разница только в том, откуда взят текст: компонент с инлайновым
    шаблоном ничем не хуже."""
    inline = template_of(tmp_path, component(template="<b>{{ total() }}</b>"))
    assert inline is not None and inline.inline is True

    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src/app/x.component.html").write_text("<b>{{ total() }}</b>", encoding="utf-8")
    external = template_of(tmp_path, component(template_url="./x.component.html"))
    assert external is not None and external.inline is False
    assert external.text == inline.text


def test_missing_template_file_does_not_break_the_component(tmp_path: Path) -> None:
    """Ошибка разбора шаблона не роняет разбор компонента: шаблон может быть
    удалён или переименован, а компонент никуда не делся."""
    assert template_of(tmp_path, component(template_url="./нет-такого.html")) is None


def test_component_without_a_template_is_not_a_problem(tmp_path: Path) -> None:
    assert template_of(tmp_path, component()) is None


# ──────────────────────────────────────────────────────────────────────────────
# Что находится в разметке
# ──────────────────────────────────────────────────────────────────────────────


def test_expressions_come_from_interpolations_and_bindings() -> None:
    text = """
    <button (click)="save()">x</button>
    <span>{{ total() }}</span>
    <em *ngIf="visible">y</em>
    <input [value]="form.value" />
    """
    found = expressions(text)
    assert "save()" in found
    assert "total()" in found
    assert "visible" in found
    assert "form.value" in found


def test_only_declared_names_become_calls() -> None:
    """Сверка с объявленными именами — единственная защита от ложных рёбер:
    в выражениях живут локальные переменные `*ngFor` и имена директив."""
    text = '<li *ngFor="let model of models">{{ model.title }}</li><b (click)="save()">x</b>'
    found = calls(text, members={"models", "save"}, receivers=set())
    assert {(item.member, item.own) for item in found} == {("models", True), ("save", True)}
    # `model` — локальная переменная директивы, а не член: ребром не стала.
    assert all(item.member != "title" for item in found)


def test_service_called_from_markup_is_found() -> None:
    """Обращение к внедрённому сервису прямо из разметки: в `.ts` такого
    вызова нет ни одного."""
    text = '<ul><li *ngFor="let item of audit.list() | async">{{ item }}</li></ul>'
    found = calls(text, members=set(), receivers={"audit"})
    assert [(item.receiver, item.member, item.own) for item in found] == [("audit", "list", False)]


def test_this_prefix_is_understood() -> None:
    found = calls('<b (click)="this.save()">x</b>', members={"save"}, receivers=set())
    assert [item.member for item in found] == ["save"]


# ──────────────────────────────────────────────────────────────────────────────
# Что попадает в манифест
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def manifest(tmp_path_factory: pytest.TempPathFactory) -> Manifest:
    from typer.testing import CliRunner

    from docpipe.cli import app

    out = tmp_path_factory.mktemp("web") / "doc-tree.web.json"
    result = CliRunner().invoke(
        app, ["web", "scan", "--root", "tests/fixtures/WebWorkspace", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    return Manifest.model_validate_json(out.read_text(encoding="utf-8"))


def test_service_called_from_the_template_becomes_an_edge(manifest: Manifest) -> None:
    """Ребро от компонента к сервису, которого в `.ts` нет: страница
    обращается к нему прямо из разметки."""
    page = next(node for node in manifest.nodes if "list.component" in node.id)
    from_template = [use for use in page.uses if use.via == TEMPLATE_MEMBER]
    assert [(use.target.rpartition(".")[2], use.member) for use in from_template] == [
        ("AuditService", "list")
    ]


def test_own_members_are_counted_not_edged(manifest: Manifest) -> None:
    """`Usage` — это обращение к члену ДРУГОГО узла. Самоссылка сломала бы
    и модель, и список зависимостей страницы, поэтому свои члены только
    считаются: их число отвечает на вопрос «сколько методов зовут только
    из разметки»."""
    page = next(node for node in manifest.nodes if "list.component" in node.id)
    assert all(use.target != page.symbol.fqn for use in page.uses if page.symbol)
