"""Генерируемый блок бизнес-документа (B08).

Блок отвечает на вопрос «чем этот процесс является в коде» без единой
написанной руками ссылки. Главный его раздел — «Участники»: граница
ответственности внутри чужого процесса **вычисляется** по реализациям шагов,
а не ведётся списком, который пришлось бы сопровождать.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.business import load_catalog, resolve_all
from docpipe.business.build import backlinks, cell, generated_block, relative
from docpipe.business.model import Anchor, BusinessDoc
from docpipe.business.resolve import ResolveContext
from docpipe.cli import app
from docpipe.materialize.ownership import Ownership, load_ownership
from tests.business_support import combined_tree, context, edit, manifest

BUSINESS = "business"

OWNERSHIP = """
version: "1"
ownership_version: "test"
teams:
  - id: ML
    title: ML
  - id: Core
    title: Core
rules:
  - id: ml.collect
    team: ML
    priority: 10
    when:
      fqn_prefix: ["Sbt.Sample.Steps.CollectStep"]
  - id: core.steps
    team: Core
    priority: 5
    when:
      namespace_prefix: ["Sbt.Sample.Steps"]
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = combined_tree(tmp_path)
    (root / "doc-tree.json").write_text(manifest().model_dump_json(), encoding="utf-8")
    return root


@pytest.fixture
def ownership(tree: Path) -> Ownership:
    path = tree / "ownership.yaml"
    path.write_text(OWNERSHIP, encoding="utf-8")
    return load_ownership(path)


def scoring(tree: Path) -> BusinessDoc:
    return load_catalog(tree, BUSINESS).by_id()["bp.valuation.twinml-scoring"]


def block(tree: Path, ownership: Ownership | None = None, ctx: ResolveContext | None = None) -> str:
    doc = scoring(tree)
    resolve_ctx = ctx or context(tree)
    return generated_block(doc, resolve_all(doc.anchors, resolve_ctx), resolve_ctx, ownership)


# --------------------------------------------------------------------------------------
# Состав и порядок
# --------------------------------------------------------------------------------------


def test_sections_go_in_a_fixed_order(tree: Path) -> None:
    """Порядок несёт смысл: от того, чем процесс запускается, к тому, что
    в нём чужое."""
    text = block(tree)
    titles = [line for line in text.splitlines() if line.startswith("### ")]

    assert titles == [
        "### Точки входа",
        "### Реализация",
        "### Участники",
        "### Данные",
        "### Вне зоны ответственности",
    ]


def test_empty_section_says_so(tree: Path) -> None:
    """Раздел без данных печатается со словом «Нет.».

    Пропущенный раздел читается как «инструмент не умеет», а «Нет.» — как факт,
    и это разные сообщения.
    """
    doc = BusinessDoc(
        schema="business/1",
        id="bp.valuation.bare",
        kind="process",
        title="Пустой",
        doc_path=f"{BUSINESS}/processes/valuation/bare.md",
    )
    ctx = context(tree)
    text = generated_block(doc, resolve_all(doc.anchors, ctx), ctx)

    assert text.count("Нет.") == 5


def test_header_warns_about_the_live_database(tree: Path) -> None:
    """Инструмент видит репозиторий, а не боевую БД, и обязан это оговаривать
    безусловно — для любого значения из реестра."""
    assert "не боевую БД" in block(tree)


# --------------------------------------------------------------------------------------
# Участники
# --------------------------------------------------------------------------------------


def test_step_breakdown_by_team(tree: Path, ownership: Ownership) -> None:
    """Ради этого раздела всё и затевается: команда описывает свою часть
    и видит, чья остальная."""
    text = block(tree, ownership)

    assert "`Core` — шагов 2: `ScoreStep`, `ThresholdStep`" in text
    assert "`ML` (наши) — шагов 1: `CollectStep`" in text


def test_step_without_ownership_is_named_not_owned(tree: Path) -> None:
    """Без правил владения шаги не пропадают из отчёта: пустой раздел
    выглядел бы как «шагов нет»."""
    text = block(tree)

    assert "`(не задан)` — шагов 3" in text


def test_participants_do_not_leak_step_class_names(tree: Path, ownership: Ownership) -> None:
    """`StepType` нужен, чтобы определить команду, но в текст не попадает:
    переименование класса шага бизнес-смысла не меняет."""
    text = block(tree, ownership)

    assert "Sbt.Sample.Steps" not in text.split("### Участники")[1].split("### Данные")[0]


# --------------------------------------------------------------------------------------
# Ссылки и таблицы
# --------------------------------------------------------------------------------------


def test_no_link_contains_a_backslash(tree: Path) -> None:
    """`posixpath.relpath`, а не `os.path.relpath`: иначе дерево документации
    оказывалось бы разным в зависимости от машины, на которой его собрали."""
    assert "\\" not in block(tree)


def test_relative_link_goes_from_document_to_document() -> None:
    assert (
        relative("business/processes/valuation/x.md", "docs/modules/App/services/y.md")
        == "../../../docs/modules/App/services/y.md"
    )


def test_cell_escapes_the_pipe() -> None:
    """Вертикальная черта внутри значения разорвала бы таблицу."""
    assert cell("a|b") == r"`a\|b`"
    assert cell("") == "—"


def test_table_survives_colons_and_cyrillic(tree: Path) -> None:
    """`JOBTITLE` содержит двоеточия и пробелы, заголовок workflow — кириллицу."""
    doc = load_catalog(tree, BUSINESS).by_id()["bp.valuation.limits-load"]
    ctx = context(tree)
    text = generated_block(doc, resolve_all(doc.anchors, ctx), ctx)

    rows = [line for line in text.splitlines() if line.startswith("| `job`")]
    assert len(rows) == 1
    assert "`PM: Load limits`" in rows[0]
    assert rows[0].count("|") == 4


# --------------------------------------------------------------------------------------
# Детерминизм
# --------------------------------------------------------------------------------------


def test_all_handlers_of_one_event_are_printed(tree: Path) -> None:
    """У пары «UserTasks + ItemAdded» два подписчика, и в «Реализации» обязаны
    стоять оба.

    Печать одного — худший вид ошибки: раздел выглядит заполненным, а половина
    участников события из документа пропала, и заметить это нечем.
    """
    text = block(tree)
    entries, implementation = text.split("### Реализация")[0], text.split("### Реализация")[1]
    rows = [line for line in implementation.splitlines() if "UserTasks/ItemAdded" in line]

    # В «Точках входа» пара — одна строка: точка входа одна, подписчиков много.
    assert sum("UserTasks/ItemAdded" in line for line in entries.splitlines()) == 1
    assert len(rows) == 2
    assert any("UserTasksAddedTriggerSampleWorkflowEventReceiver" in row for row in rows)
    assert any("UserTasksAddedAuditEventReceiver" in row for row in rows)


def test_two_calls_give_the_same_text(tree: Path, ownership: Ownership) -> None:
    assert block(tree, ownership) == block(tree, ownership)


def test_reversed_registry_order_gives_the_same_text(tree: Path) -> None:
    """Перестановка записей в реестре — форматирование XML, а не изменение."""
    before = block(tree)

    structure = tree / "Structure.xml"
    added = (
        '<EventReceiver Class="Sbt.Cashflow.ML.EventReceivers.'
        'UserTasksAddedTriggerSampleWorkflowEventReceiver"\n'
        + " " * 31
        + 'Assembly="Sbt.Cashflow.ML.EventReceivers"\n'
        + " " * 31
        + 'EventType="ItemAdded" />'
    )
    edit(structure, added + "\n            </EventReceivers>", "</EventReceivers>")
    edit(structure, "<EventReceivers>", "<EventReceivers>\n                " + added)

    assert block(tree) == before


# --------------------------------------------------------------------------------------
# Обратный индекс
# --------------------------------------------------------------------------------------


def test_backlinks_map_nodes_to_business_documents(tree: Path) -> None:
    """Индекс строится один раз по каталогу и передаётся шагу 2 как данные:
    `docpipe/materialize/**` не должен импортировать `docpipe/business/**`."""
    index = backlinks(load_catalog(tree, BUSINESS), context(tree))
    receiver = next(key for key in index if "EventReceiver" in key)

    assert ("Онлайн УФН", "business/processes/valuation/twinml-scoring.md") in index[receiver]


def test_backlinks_are_sorted_and_unique(tree: Path) -> None:
    index = backlinks(load_catalog(tree, BUSINESS), context(tree))

    assert list(index) == sorted(index)
    for pairs in index.values():
        assert pairs == sorted(set(pairs))


def test_unverified_anchor_appears_as_out_of_scope(tree: Path) -> None:
    """`upstream` показывается с владельцем и пометкой «не проверяется»:
    это объявленная чужая зона, а не то, что инструмент не сумел найти."""
    text = block(tree)
    outside = text.split("### Вне зоны ответственности")[1]

    assert "pricing.eod.requested" in outside
    assert "команда интеграции" in outside
    assert "не проверяется" in outside


def test_anchor_display_is_never_parsed_back(tree: Path) -> None:
    """Строка показа существует только для человека: `JOBTITLE` содержит
    пробелы и двоеточия, и любой её парсер был бы источником багов."""
    anchor = Anchor(kind="job", ref="PM: Load limits")

    assert anchor.display == "PM: Load limits"
    assert Anchor(kind="workflow", ref="W", version="2").display == "W@2"


# --------------------------------------------------------------------------------------
# Сквозная проверка: шаг 2 получает бизнес-контекст (B09)
# --------------------------------------------------------------------------------------


def test_materialize_adds_business_context_when_catalog_is_configured(tree: Path) -> None:
    """Раздел появляется в техническом документе, когда каталог задан
    в конфигурации, и не появляется, когда не задан."""
    runner = CliRunner()
    (tree / "docpipe.yaml").write_text(
        f"registries: {tree / 'registries.yaml'}\nbusiness_root: {BUSINESS}\n",
        encoding="utf-8",
    )
    args = [
        "materialize",
        str(tree / "doc-tree.json"),
        "--root",
        str(tree),
        "--templates",
        "templates",
    ]

    assert runner.invoke(app, args).exit_code == 0
    receiver = (
        tree / "docs/modules/App/services/usertasksaddedtriggersampleworkfloweventreceiver.md"
    )
    assert "### Бизнес-контекст" not in receiver.read_text(encoding="utf-8")

    assert runner.invoke(app, [*args, "--config", str(tree / "docpipe.yaml")]).exit_code == 0
    text = receiver.read_text(encoding="utf-8")

    assert "### Бизнес-контекст" in text
    assert "Онлайн УФН" in text
    assert "\\" not in text
