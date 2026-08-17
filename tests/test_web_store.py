"""Цепочка NGXS: диспатч → обработчик → сервис (P03).

Названный пробел F14. Без него у страницы, которая ходит за данными через
стор, нет ни одного эндпоинта — при том, что в боевом модуле это основная
форма похода за данными, и «вызовов ноль» читалось бы как «документировать
нечего».

Связывание идёт по имени класса экшена, разрешённому таблицей импортов:
у члена стейта уже записан атрибут `Action` с этим именем. Строка `type`
нужна отдельно — она переживает переименование класса и потому едет в ребро.
"""

from pathlib import Path

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest, Usage
from docpipe.web.parser import parse_tree
from docpipe.web.store import action_types, extract_dispatches, extract_selects
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

RULES = Path("rules/rules.yaml")


@pytest.fixture
def scanned(web_workspace: Path) -> WebScanResult:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web"))


@pytest.fixture
def manifest(scanned: WebScanResult) -> Manifest:
    return scanned.manifest


def _uses(manifest: Manifest, title: str) -> list[Usage]:
    return next(node for node in manifest.nodes if node.title == title).uses


def _short(usage: Usage) -> tuple[str, str, str]:
    return (usage.target.rpartition(".")[2], usage.member, usage.action)


# --------------------------------------------------------------------------------------
# Цепочка на фикстуре
# --------------------------------------------------------------------------------------


def test_dispatch_links_the_page_to_the_handler(manifest: Manifest) -> None:
    """Ребро ведёт в **член** стейта, обрабатывающий экшен, а не в стейт целиком."""
    assert ("DebtState", "load", "[Inner Debt] Load") in [
        _short(usage) for usage in _uses(manifest, "ListComponent")
    ]


def test_one_action_handled_by_two_states_gives_two_edges(manifest: Manifest) -> None:
    """В NGXS это законно: сохранение пишет данные в одном стейте, журнал в другом.

    Реализация «первый попавшийся обработчик» потеряла бы половину состояния
    страницы молча.
    """
    save = [
        item for item in map(_short, _uses(manifest, "ListComponent")) if item[2].endswith("Save")
    ]

    assert sorted(save) == [
        ("AuditState", "write", "[Inner Debt] Save"),
        ("DebtState", "save", "[Inner Debt] Save"),
    ]


def test_both_selection_forms_give_the_same_edge(manifest: Manifest) -> None:
    """`@Select(DebtState.items)` и `store.selectSnapshot(DebtState.items)`.

    Обе означают одно: страница смотрит в этот стейт. Различает их только
    член источника, из которого читают.
    """
    selects = {
        (usage.member, usage.via)
        for usage in _uses(manifest, "ListComponent")
        if usage.target.endswith("DebtState") and not usage.action
    }

    assert selects == {("items", "items$"), ("items", "total")}


def test_action_without_a_handler_is_counted_not_dropped(scanned: WebScanResult) -> None:
    """Обработчик может жить в подписке или в эффекте вне стейта.

    Это состояние работы, а не ошибка разбора, поэтому число, а не отказ.
    """
    assert scanned.meta.stats["actions_without_handler"] == 1
    assert scanned.meta.stats["dispatches"] == 2
    assert scanned.meta.stats["selects"] == 2


def test_state_reaches_the_service_by_the_same_graph(manifest: Manifest) -> None:
    """Второе звено цепочки — обычное ребро P02, а не особый вид связи."""
    assert [_short(usage) for usage in _uses(manifest, "DebtState")] == [
        ("InnerDebtService", "byClient", ""),
        ("InnerDebtService", "insert", ""),
    ]


# --------------------------------------------------------------------------------------
# Формы записи
# --------------------------------------------------------------------------------------


def _dispatches(source: str) -> list[str]:
    tree = parse_tree(source.encode("utf-8"))
    return [item.action for item in extract_dispatches(tree.root_node, "a.ts")]


def test_array_in_dispatch_gives_every_action() -> None:
    """Так пишут пакетную загрузку экрана; «первый аргумент» дал бы ноль."""
    assert _dispatches("class C { m() { this.store.dispatch([new A(), new B()]); } }") == ["A", "B"]
    assert _dispatches("class C { m() { this.store.dispatch(new A()); } }") == ["A"]


def test_dispatch_of_a_variable_is_not_an_action_name() -> None:
    """`dispatch(action)` — имя класса неизвестно, и выдумывать его нечем."""
    assert _dispatches("class C { m(action: unknown) { this.store.dispatch(action); } }") == []


def test_selection_forms() -> None:
    tree = parse_tree(
        b"class C { @Select(S.items) a$; m() { this.store.selectSnapshot(S.total); } }"
    )
    found = extract_selects(tree.root_node, "a.ts")

    assert [(item.owner, item.member) for item in found] == [("S", "items"), ("S", "total")]


def test_action_type_literal_is_taken_from_its_class() -> None:
    tree = parse_tree(b"export class LoadX { static readonly type = '[X] Load'; }")

    assert action_types(tree.root_node) == {"LoadX": "[X] Load"}


def test_action_without_a_type_field_has_no_literal() -> None:
    """Тогда в ребро едет имя класса: это хуже, но честно."""
    tree = parse_tree(b"export class LoadX {}")

    assert action_types(tree.root_node) == {}


# --------------------------------------------------------------------------------------
# `via_action` у вызова
# --------------------------------------------------------------------------------------


def test_call_written_inside_a_handler_carries_the_action_type(tmp_path: Path) -> None:
    """Поле `WebCall.via_action` — про вызов, записанный В обработчике.

    Путь «страница → экшен → стейт → сервис» им не описывается, и это
    не упрощение: один и тот же метод сервиса зовут и обработчик, и компонент
    напрямую. Пометив вызов, инструмент утверждал бы, что до него всегда
    доходят через стор; поэтому путь несёт ребро (`Usage.action`), а вызов —
    только собственное происхождение.
    """
    # Модуль фронта опознаётся по `package.json`: без него в дереве нет
    # ни одного узла, и тест зеленел бы, ничего не проверив.
    (tmp_path / "package.json").write_text('{"name": "mini"}\n', encoding="utf-8")
    (tmp_path / "actions.ts").write_text(
        "export class LoadX { static readonly type = '[X] Load'; }\n", encoding="utf-8"
    )
    (tmp_path / "x.state.ts").write_text(
        "import { HttpClient } from '@angular/common/http';\n"
        "import { Action, State } from '@ngxs/store';\n"
        "import { LoadX } from './actions';\n"
        "@State({ name: 'x' })\n"
        "export class XState {\n"
        "  constructor(private http: HttpClient) {}\n"
        "  @Action(LoadX)\n"
        "  load() {\n"
        "    return this.http.get('api/x/list');\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    manifest = run_web(tmp_path, DocpipeConfig(), load_ruleset(RULES, "web")).manifest
    state = next(node for node in manifest.nodes if node.title == "XState")

    assert [(call.member, call.via_action) for call in state.web_calls] == [("load", "[X] Load")]
