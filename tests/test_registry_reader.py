"""Проверка декларативного читателя реестров (B01).

Фикстуры в `tests/fixtures/registries/` воспроизводят конструкции боевых файлов
АС CF, а не только их структуру: вложенный `<add>` в интерцепторах, посторонний
справочник рядом с JOBS, BOM, разделители Windows в пути, поле неизвестного вида.
Упрощение фикстуры оставит тесты зелёными и бессмысленными.
"""

from pathlib import Path

import pytest

from docpipe.registry import (
    ChildSpec,
    FollowChildSpec,
    FollowSpec,
    RegistrySpec,
    load_registries,
    read_registry,
)

ROOT = Path("tests/fixtures/registries")
ITEMS = "deployment/Data/Items"


@pytest.fixture
def root() -> Path:
    return ROOT


# --------------------------------------------------------------------------------------
# Описания реестров: те же, что в плане, но по фикстурам
# --------------------------------------------------------------------------------------


def services_spec() -> RegistrySpec:
    return RegistrySpec(
        id="grid-services",
        kind="grid_service",
        format="xml",
        path="services.config",
        item_xpath="./dotNetServices/add",
        fields={"ref": "@name", "impl_fqn": "@class", "assembly": "@assembly", "team": "@team"},
    )


def jobs_spec(paths: list[str] | None = None) -> RegistrySpec:
    return RegistrySpec(
        id="jobs",
        kind="job",
        format="xml",
        paths=paths or [f"{ITEMS}/Items_Basic.xml"],
        item_xpath="./Lists/List[@InnerName='JOBS']/Items/Item[@KeyField='JOBTITLE']",
        fields={
            "ref": "@KeyValue",
            "contract_fqn": "@JOBCLASS",
            "assembly": "@JOBASSEMBLY",
            "disabled": "@JOBDISABLED",
            "schedule_raw": "@JOBSCHEDULE",
        },
    )


def workflows_spec(base: str = "deployment") -> RegistrySpec:
    return RegistrySpec(
        id="workflows",
        kind="workflow",
        format="xml",
        paths=[f"{ITEMS}/Items-Workflows.xml"],
        item_xpath="./Lists/List[@InnerName='Workflows']/Items/Item[@KeyField='WorkflowTitle']",
        fields={
            "title": "@KeyValue",
            "is_active": "@IsActive",
            "definition": "./File[@FieldInnerName='WorkflowDefinition']/@Path",
        },
        follow=FollowSpec(
            field="definition",
            base=base,
            fields={"ref": "Id", "version": "Version", "data_type": "DataType"},
            children=FollowChildSpec(
                kind="workflow_step",
                items_key="Steps",
                fields={"ref": "Id", "impl_type": "StepType", "next": "NextStepId"},
            ),
        ),
    )


def structure_spec() -> RegistrySpec:
    return RegistrySpec(
        id="structure",
        kind="list",
        format="xml",
        path="Structure.xml",
        item_xpath="./Lists/List",
        fields={"ref": "@InnerName", "table": "@TableName", "display_name": "@DisplayName"},
        children=[
            ChildSpec(
                kind="list_field",
                item_xpath="./Fields/*",
                fields={
                    "ref": "@InnerName",
                    "column": "@ColumnName",
                    "display_name": "@DisplayName",
                    "required": "@Required",
                    "lookup": "@ListSource",
                },
            ),
            ChildSpec(
                kind="list_event",
                item_xpath="./EventReceivers/EventReceiver",
                fields={"ref": "@EventType", "impl_fqn": "@Class", "assembly": "@Assembly"},
            ),
        ],
    )


# --------------------------------------------------------------------------------------
# Grid-сервисы: вложенный <add> и независимость якоря от класса
# --------------------------------------------------------------------------------------


def test_interceptors_are_not_services(root: Path) -> None:
    """Вложенный <add> внутри <interceptors> не попадает в реестр.

    Обход по `.//add` подобрал бы его: у интерцептора нет @name, поэтому запись
    либо упала бы, либо стала фантомным сервисом.
    """
    result = read_registry(services_spec(), root)

    assert [item.ref for item in result.items] == [
        "CalcDxExcel",
        "IBlockFilesService",
        "ICalcResult",
    ]
    assert result.errors == []
    assert all("IgniteServiceUserPropagator" not in item.ref for item in result.items)


def test_service_anchor_is_not_derived_from_class(root: Path) -> None:
    """Инвариант фикстуры: имя сервиса не выводится ни из класса, ни из интерфейса."""
    result = read_registry(services_spec(), root)
    by_ref = {item.ref: item for item in result.items}

    assert by_ref["CalcDxExcel"].fields["impl_fqn"].endswith("DXExcelCalcService")
    assert "CalcDxExcel" not in by_ref["CalcDxExcel"].fields["impl_fqn"]
    assert by_ref["ICalcResult"].fields["team"] == "Core"
    assert by_ref["IBlockFilesService"].fields["team"] == "TVM"


# --------------------------------------------------------------------------------------
# Джобы: посторонние списки и устойчивость к битому файлу
# --------------------------------------------------------------------------------------


def test_foreign_lists_are_not_jobs(root: Path) -> None:
    """`.//Item` втянул бы справочник подразделений; якорь на List это отсекает."""
    result = read_registry(jobs_spec(), root)

    assert [item.ref for item in result.items] == ["Financial reports job", "PM: Load limits"]
    assert result.errors == []
    assert all(item.ref not in ("Казначейство", "Риски") for item in result.items)


def test_escaped_schedule_is_kept_raw(root: Path) -> None:
    """Разэкранированием занимается B02; читатель обязан отдать значение целиком."""
    result = read_registry(jobs_spec(), root)
    raw = result.items[0].fields["schedule_raw"]

    assert raw.startswith("<JobSchedule")
    assert 'Interval="60"' in raw


def test_broken_file_does_not_stop_the_run(root: Path) -> None:
    """Один битый файл даёт строку в errors; остальные читаются."""
    result = read_registry(jobs_spec([f"{ITEMS}/Items*.xml"]), root)

    assert [item.ref for item in result.items] == ["Financial reports job", "PM: Load limits"]
    assert any("Items_Broken.xml" in error for error in result.errors)


def test_file_without_matching_items_is_not_an_error(root: Path) -> None:
    """Items-Workflows.xml попадает под глоб джобов и списка JOBS не содержит.

    Ноль записей в отдельном файле — норма, а не повод сообщать: иначе каждый
    прогон печатал бы шум по всем файлам поставки.
    """
    result = read_registry(jobs_spec([f"{ITEMS}/Items*.xml"]), root)

    assert not any("Items-Workflows.xml" in error for error in result.errors)


# --------------------------------------------------------------------------------------
# Переход по ссылке
# --------------------------------------------------------------------------------------


def test_attribute_of_child_element_is_extracted(root: Path) -> None:
    """ElementTree не выбирает атрибуты: `путь/@attr` обязан работать сам."""
    result = read_registry(workflows_spec(), root)
    definitions = sorted(item.fields["definition"] for item in result.items)

    assert definitions == [
        r"Data\Items\Workflows\Sample.v1.json",
        r"Data\Items\Workflows\Sample.v2.json",
    ]


def test_follow_reads_definition_and_steps(root: Path) -> None:
    """Якорь приходит из JSON, заголовок — из XML, шаги — вложенными записями."""
    result = read_registry(workflows_spec(), root)
    versions = {item.fields["version"]: item for item in result.items}

    assert sorted(versions) == ["1", "2"]
    assert versions["2"].ref == "SampleWorkflow"
    assert versions["2"].fields["title"] == "Онлайн УФН (версия 2)"
    assert versions["2"].fields["data_type"].startswith("Sbt.Sample.Models.SampleAggregate")
    assert [step.ref for step in versions["2"].children] == [
        "CollectStep",
        "ThresholdStep",
        "ScoreStep",
    ]
    assert versions["2"].children[0].fields["next"] == "ThresholdStep"
    assert versions["2"].children[-1].kind == "workflow_step"


def test_children_keep_declaration_order(root: Path) -> None:
    """Порядок шагов — данные (цепочка NextStepId), а не следствие обхода.

    Сортировка по `ref` дала бы Collect, Score, Threshold и порвала бы цепочку.
    """
    result = read_registry(workflows_spec(), root)
    steps = next(item for item in result.items if item.fields["version"] == "2").children

    assert [step.ref for step in steps] != sorted(step.ref for step in steps)


def test_follow_base_is_not_the_registry_directory(root: Path) -> None:
    """База отсчёта задаётся явно.

    Склейка пути из реестра с каталогом самого реестра даёт
    `deployment/Data/Items/Data/Items/Workflows/...` — путь, который выглядит
    настоящим и указывает в никуда.
    """
    result = read_registry(workflows_spec(base=f"{ITEMS}"), root)

    assert result.items == []
    assert sum("файл описания не найден" in error for error in result.errors) == 3
    assert any(f"{ITEMS}/Data/Items/Workflows/Sample.v1.json" in error for error in result.errors)


def test_missing_definition_is_reported_not_fatal(root: Path) -> None:
    """Оборванная ссылка не роняет чтение остальных записей."""
    result = read_registry(workflows_spec(), root)

    assert len(result.items) == 2
    assert any("Missing.v1.json" in error for error in result.errors)


# --------------------------------------------------------------------------------------
# BOM
# --------------------------------------------------------------------------------------


def test_fixture_still_has_bom() -> None:
    """Инвариант фикстуры: без BOM проверка чтения байтами ничего не проверяет."""
    data = (ROOT / ITEMS / "Items-Workflows.xml").read_bytes()

    assert data.startswith(b"\xef\xbb\xbf")


def test_bom_file_is_read(root: Path) -> None:
    result = read_registry(workflows_spec(), root)

    assert len(result.items) == 2


# --------------------------------------------------------------------------------------
# Вложенные записи структуры
# --------------------------------------------------------------------------------------


def test_fields_are_read_by_prefix_not_by_whitelist(root: Path) -> None:
    """Перечень видов полей заведомо неполон: FieldSomethingNew обязан прочитаться."""
    result = read_registry(structure_spec(), root)
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    fields = [child for child in tasks.children if child.kind == "list_field"]

    assert [child.ref for child in fields] == [
        "UserTasksTitle",
        "UserTasksType",
        "UserTasksTaskStartTime",
        "UserTasksExperimental",
    ]
    assert tasks.fields["display_name"] == "Задачи пользователей"
    assert tasks.fields["table"] == "USER_TASKS"


def test_lookup_carries_declared_relation(root: Path) -> None:
    """Связь между сущностями объявлена, а не выведена из кода."""
    result = read_registry(structure_spec(), root)
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    lookup = next(child for child in tasks.children if child.ref == "UserTasksType")

    assert lookup.fields["lookup"] == "UserTaskTypes"


def test_forms_are_not_mistaken_for_fields(root: Path) -> None:
    result = read_registry(structure_spec(), root)
    refs = {child.ref for item in result.items for child in item.children}

    assert "DisplayUserTask" not in refs
    assert "AllUserTasks" not in refs


def test_event_receivers_are_read_per_event_type(root: Path) -> None:
    """Якорь обработчика — пара «список + EventType», а не класс."""
    result = read_registry(structure_spec(), root)
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    events = [child for child in tasks.children if child.kind == "list_event"]

    assert [child.ref for child in events] == [
        "ItemAdding",
        "ItemUpdating",
        "ItemAdded",
        "ItemAdded",
    ]
    assert {child.fields["assembly"] for child in events} == {
        "Sbt.Cashflow.Reports.EventReceivers",
        "Sbt.Cashflow.ML.EventReceivers",
    }


def test_repeated_event_type_gives_two_records(root: Path) -> None:
    """Два обработчика на одной паре «список + EventType» — не дубль и не
    ошибка чтения: платформа вызывает всех подписчиков, поэтому обе записи
    обязаны дойти до разрешения. Реализация, схлопывающая их по `ref`,
    молча потеряла бы половину участников события."""
    result = read_registry(structure_spec(), root)
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    added = [c for c in tasks.children if c.kind == "list_event" and c.ref == "ItemAdded"]

    assert len(added) == 2
    assert {child.fields["impl_fqn"] for child in added} == {
        "Sbt.Cashflow.Reports.EventReceivers.UserTasksAddedAuditEventReceiver",
        "Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver",
    }


# --------------------------------------------------------------------------------------
# Детерминизм
# --------------------------------------------------------------------------------------


def test_items_are_sorted_by_ref(root: Path) -> None:
    result = read_registry(structure_spec(), root)

    assert [item.ref for item in result.items] == ["UserTaskTypes", "UserTasks"]


def test_traversal_order_does_not_affect_output(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перестановка порядка обхода ФС не меняет вывод ни на байт."""
    baseline = read_registry(jobs_spec([f"{ITEMS}/Items*.xml"]), root)
    original = Path.glob

    def reversed_glob(self: Path, pattern: str, **kwargs: object) -> object:
        return iter(sorted(original(self, pattern), reverse=True))

    monkeypatch.setattr(Path, "glob", reversed_glob)

    assert read_registry(jobs_spec([f"{ITEMS}/Items*.xml"]), root) == baseline


def test_pattern_order_does_not_affect_output(root: Path) -> None:
    forward = read_registry(
        jobs_spec([f"{ITEMS}/Items_Basic.xml", f"{ITEMS}/Items-Workflows.xml"]), root
    )
    backward = read_registry(
        jobs_spec([f"{ITEMS}/Items-Workflows.xml", f"{ITEMS}/Items_Basic.xml"]), root
    )

    assert forward.items == backward.items


# --------------------------------------------------------------------------------------
# Пустой реестр и записи без якоря
# --------------------------------------------------------------------------------------


def test_empty_registry_is_reported(root: Path) -> None:
    """Реестр без записей неотличим от «таких точек входа нет» — надо сообщать."""
    spec = jobs_spec().model_copy(
        update={"item_xpath": "./Lists/List[@InnerName='NOSUCH']/Items/Item"}
    )
    result = read_registry(spec, root)

    assert result.items == []
    assert any("не найдено ни одной записи" in error for error in result.errors)


def test_item_without_ref_is_reported(root: Path) -> None:
    """Запись без якоря пропускается с сообщением, а не молча."""
    spec = jobs_spec().model_copy(update={"fields": {"ref": "@NOSUCHATTR"}})
    result = read_registry(spec, root)

    assert result.items == []
    assert sum("без поля `ref`" in error for error in result.errors) == 2


def test_missing_pattern_is_reported(root: Path) -> None:
    result = read_registry(jobs_spec(["deployment/nowhere/*.xml"]), root)

    assert any("файлов не найдено" in error for error in result.errors)


def test_inline_registry(root: Path) -> None:
    """Заглушка для Kafka: перечень ведётся руками, пока источник не найден."""
    spec = RegistrySpec(
        id="kafka-topics",
        kind="kafka_topic",
        format="inline",
        items=[{"ref": "pricing.eod.requested", "owner": "интеграция"}],
    )
    result = read_registry(spec, root)

    assert [item.ref for item in result.items] == ["pricing.eod.requested"]
    assert result.items[0].fields == {"owner": "интеграция"}


# --------------------------------------------------------------------------------------
# Загрузка описания
# --------------------------------------------------------------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "registries.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = """
version: "1"
registries_version: "2026-07-29.1"
registries:
  - id: jobs
    kind: job
    format: xml
    path: a.xml
    item_xpath: "./Item"
    fields: {ref: "@KeyValue"}
"""


def test_load_valid(tmp_path: Path) -> None:
    specs = load_registries(_write(tmp_path, VALID))

    assert [spec.id for spec in specs] == ["jobs"]
    assert specs[0].patterns() == ["a.xml"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("registries: []", "непустым списком"),
        ("registriez: []", "неизвестные ключи верхнего уровня"),
        (
            VALID + "  - {id: jobs, kind: job, format: xml, path: b.xml, item_xpath: './I',"
            " fields: {ref: '@x'}}",
            "повтор id",
        ),
        ("registries:\n  - {kind: job, format: xml}", "без полей"),
        (
            "registries:\n  - {id: a, kind: job, format: xml, item_xpath: './I',"
            " fields: {ref: '@x'}}",
            "нужен `path` или `paths`",
        ),
        (
            "registries:\n  - {id: a, kind: job, format: xml, path: a.xml, fields: {ref: '@x'}}",
            "нужен `item_xpath`",
        ),
        (
            "registries:\n  - {id: a, kind: job, format: xml, path: a.xml,"
            " item_xpath: './I', fields: {name: '@x'}}",
            "нет `ref`",
        ),
        (
            "registries:\n  - {id: a, kind: job, format: xml, path: a.xml,"
            " item_xpath: './I', fields: {ref: '@x'}, itemxpath: './I'}",
            "неизвестные ключи",
        ),
        ("registries:\n  - {id: a, kind: k, format: inline}", "требует список `items`"),
        ("registries:\n  - {id: a, kind: k, format: inline, items: [{x: 1}]}", "ключом `ref`"),
    ],
)
def test_load_rejects(tmp_path: Path, text: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        load_registries(_write(tmp_path, text))


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Файл реестров не найден"):
        load_registries(tmp_path / "nope.yaml")
