"""Вызов → член, в котором он записан (P01).

Различие, ради которого поле заведено: «страница зовёт метод, который ходит
вот сюда» против «страница внедрила сервис, у которого есть такой метод».
На боевом модуле это 51 вызов против единиц, и без атрибуции документ
страницы собирался бы вокруг чужого списка.

Разбора здесь нет: у вызова есть файл и строка, у члена — `line` и `end_line`.
Второй проход по дереву ради той же величины дал бы второй источник истины.
"""

from pathlib import Path

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest, Member, SourceSpan, Symbol
from docpipe.web.members import member_ranges
from docpipe.web.tree import run as run_web

RULES = Path("rules/rules.yaml")


@pytest.fixture
def manifest(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


def _calls(manifest: Manifest, title: str) -> dict[int, str]:
    node = next(node for node in manifest.nodes if node.title == title)
    return {call.line: call.member for call in node.web_calls}


def _symbol(members: list[Member]) -> Symbol:
    return Symbol(
        fqn="a.A",
        name="A",
        namespace="a",
        module="src",
        type_kind="class",
        members=members,
        sources=[SourceSpan(path="a.ts", start=1, end=50)],
    )


def _member(name: str, line: int, end_line: int) -> Member:
    return Member(name=name, kind="method", signature=f"{name}()", line=line, end_line=end_line)


# --------------------------------------------------------------------------------------
# На фикстуре
# --------------------------------------------------------------------------------------


def test_every_call_of_a_service_knows_its_method(manifest: Manifest) -> None:
    assert _calls(manifest, "ModelService") == {
        16: "list",
        20: "byId",
        24: "forUpdate",
        29: "saveAlternative",
        33: "removeAlternative",
    }


def test_call_inside_a_local_function_belongs_to_the_method(manifest: Manifest) -> None:
    """Диапазон метода накрывает локальную стрелку целиком.

    Победить обязан самый узкий накрывающий член — сам метод. Перебор
    в порядке объявления вернул бы первый попавшийся накрывающий, и вызов
    уехал бы в чужой метод.
    """
    assert _calls(manifest, "AuditService")[31] == "retry"


def test_one_line_field_arrow_is_a_member_too(manifest: Manifest) -> None:
    """`line == end_line`: сравнение границ нестрогое с обеих сторон."""
    assert _calls(manifest, "AuditService")[37] == "ping"


def test_call_outside_any_member_has_no_member(manifest: Manifest) -> None:
    """Фабрика уровня модуля. Пустая строка — состояние, а не неизвестность."""
    ranges = member_ranges([node.symbol for node in manifest.nodes if node.symbol])

    assert ranges.of("src/app/shared/services/audit.service.ts", 42) == ""


# --------------------------------------------------------------------------------------
# Правило выбора
# --------------------------------------------------------------------------------------


def test_the_narrowest_covering_member_wins() -> None:
    ranges = member_ranges([_symbol([_member("outer", 1, 40), _member("inner", 10, 12)])])

    assert ranges.of("a.ts", 11) == "inner"
    assert ranges.of("a.ts", 5) == "outer"


def test_equal_ranges_resolve_by_name_not_by_order() -> None:
    """Два члена с одинаковым диапазоном — ошибка разбора, но не недетерминизм."""
    straight = member_ranges([_symbol([_member("b", 3, 5), _member("a", 3, 5)])])
    reversed_ = member_ranges([_symbol([_member("a", 3, 5), _member("b", 3, 5)])])

    assert straight.of("a.ts", 4) == reversed_.of("a.ts", 4) == "a"


def test_lines_outside_every_member_give_nothing() -> None:
    ranges = member_ranges([_symbol([_member("only", 10, 12)])])

    assert ranges.of("a.ts", 9) == ""
    assert ranges.of("a.ts", 13) == ""
    assert ranges.of("other.ts", 11) == ""


def test_template_lines_do_not_shift_members(manifest: Manifest) -> None:
    """Ключ — файл объявления, то есть первый источник.

    Шаблон `.html` дописывается в `sources` шагом `web` рядом с `.ts`,
    и его строки к членам отношения не имеют: индекс по всем источникам
    отнёс бы вызов к члену с тем же номером строки в другом файле.
    """
    node = next(node for node in manifest.nodes if node.title == "ListComponent")
    assert node.symbol is not None
    paths = [source.path for source in node.symbol.sources]

    assert paths[0].endswith(".ts") and any(path.endswith(".html") for path in paths)
    assert member_ranges([node.symbol]).by_file.keys() == {paths[0]}
