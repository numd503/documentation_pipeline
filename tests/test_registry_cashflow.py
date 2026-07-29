"""Четыре реестра АС CF и разбор их специфики (B02).

Реестры описаны данными в `registries.example.yaml`; здесь проверяется, что
описание действительно читает боевые конструкции, и что поставляемый пример
не разошёлся с тем, что проверено.
"""

from pathlib import Path
from typing import Any

import pytest

from docpipe.registry import RegistrySpec, load_registries, read_registry
from docpipe.registry.parse import parse_schedule, split_type_name

ROOT = Path("tests/fixtures/registries")
EXAMPLE = Path("registries.example.yaml")


@pytest.fixture
def specs() -> dict[str, RegistrySpec]:
    return {spec.id: spec for spec in load_registries(ROOT / "registries.yaml")}


def _read(specs: dict[str, RegistrySpec], name: str) -> Any:
    return read_registry(specs[name], ROOT)


# --------------------------------------------------------------------------------------
# Пример и фикстура не должны разъехаться
# --------------------------------------------------------------------------------------


def _shape(spec: RegistrySpec) -> dict[str, Any]:
    """Всё, кроме путей: они и только они отличают пример от фикстуры."""
    shape = spec.model_dump(exclude={"path", "paths", "follow"})
    shape["follow"] = None if spec.follow is None else spec.follow.model_dump(exclude={"base"})
    return shape


def test_example_matches_fixture(specs: dict[str, RegistrySpec]) -> None:
    """Иначе пример и проверенное описание разойдутся, и заметит это только
    настройщик на боевом репозитории — там, где отладка дороже всего."""
    example = {spec.id: spec for spec in load_registries(EXAMPLE)}

    assert sorted(example) == sorted(specs)
    for name, spec in example.items():
        assert _shape(spec) == _shape(specs[name]), name


def test_example_declares_four_registries_and_a_stub() -> None:
    example = {spec.id: spec.kind for spec in load_registries(EXAMPLE)}

    assert example == {
        "grid-services": "grid_service",
        "jobs": "job",
        "workflows": "workflow",
        "structure": "list",
        "kafka-topics": "kafka_topic",
    }


# --------------------------------------------------------------------------------------
# Четыре реестра на фикстурах
# --------------------------------------------------------------------------------------


def test_grid_services(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "grid-services")

    assert len(result.items) == 3
    assert result.errors == []
    assert {item.fields["team"] for item in result.items} == {"Core", "TVM"}


def test_jobs(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "jobs")
    by_ref = {item.ref: item for item in result.items}

    assert sorted(by_ref) == ["Financial reports job", "PM: Load limits"]
    assert by_ref["PM: Load limits"].fields["override"] == "false"
    assert by_ref["Financial reports job"].fields["override"] == "true"
    assert by_ref["PM: Load limits"].fields["contract_fqn"].endswith("ILoadLimitsJob")


def test_workflows(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "workflows")

    assert len(result.items) == 2
    assert {item.ref for item in result.items} == {"SampleWorkflow"}
    assert sorted(item.fields["version"] for item in result.items) == ["1", "2"]
    assert all(item.fields["is_active"] == "true" for item in result.items)


def test_structure(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "structure")
    tasks = next(item for item in result.items if item.ref == "UserTasks")

    assert [item.ref for item in result.items] == ["UserTaskTypes", "UserTasks"]
    assert sum(child.kind == "list_field" for child in tasks.children) == 4
    assert sum(child.kind == "list_event" for child in tasks.children) == 3


def test_kafka_stub_is_empty_but_valid(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "kafka-topics")

    assert result.items == []
    assert result.errors == []


# --------------------------------------------------------------------------------------
# Специфика: вид поля лежит в имени элемента
# --------------------------------------------------------------------------------------


def test_field_kind_comes_from_tag(specs: dict[str, RegistrySpec]) -> None:
    """Вид поля входит в состав бизнес-сущности, а атрибутом он не записан."""
    result = _read(specs, "structure")
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    kinds = {
        child.ref: child.fields["field_type"]
        for child in tasks.children
        if child.kind == "list_field"
    }

    assert kinds["UserTasksTitle"] == "FieldText"
    assert kinds["UserTasksType"] == "FieldLookup"
    assert kinds["UserTasksTaskStartTime"] == "FieldDateTime"


def test_unknown_field_kind_is_read(specs: dict[str, RegistrySpec]) -> None:
    """Перечень видов заведомо неполон: белый список молча потерял бы поле."""
    result = _read(specs, "structure")
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    experimental = next(c for c in tasks.children if c.ref == "UserTasksExperimental")

    assert experimental.fields["field_type"] == "FieldSomethingNew"


def test_lookup_is_declared_relation(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "structure")
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    lookup = next(c for c in tasks.children if c.ref == "UserTasksType")

    assert lookup.fields["lookup"] == "UserTaskTypes"
    assert any(item.ref == "UserTaskTypes" for item in result.items)


def test_event_receiver_gives_list_event_class_and_assembly(
    specs: dict[str, RegistrySpec],
) -> None:
    result = _read(specs, "structure")
    tasks = next(item for item in result.items if item.ref == "UserTasks")
    added = next(c for c in tasks.children if c.kind == "list_event" and c.ref == "ItemAdded")

    assert tasks.ref == "UserTasks"
    assert added.fields["impl_fqn"].endswith("UserTasksAddedTriggerSampleWorkflowEventReceiver")
    assert added.fields["assembly"] == "Sbt.Cashflow.ML.EventReceivers"


# --------------------------------------------------------------------------------------
# Расписание: экранированный XML внутри атрибута
# --------------------------------------------------------------------------------------


def test_schedule_from_fixture(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "jobs")
    by_ref = {item.ref: item for item in result.items}

    reports = parse_schedule(by_ref["Financial reports job"].fields["schedule_raw"])
    limits = parse_schedule(by_ref["PM: Load limits"].fields["schedule_raw"])

    assert reports == parse_schedule('<JobSchedule Interval="60" FirstTime="2017-01-01" />')
    assert reports is not None and reports.interval_seconds == 60
    assert reports.first_time == "2017-01-01"
    assert limits is not None and limits.interval_seconds == 86400


@pytest.mark.parametrize(
    ("raw", "interval", "first_time"),
    [
        ('<JobSchedule Interval="60" FirstTime="2017-01-01" />', 60, "2017-01-01"),
        ('<JobSchedule Interval="86400" />', 86400, None),
        ("<JobSchedule />", None, None),
        ('<JobSchedule Interval="каждый час" />', None, None),
    ],
)
def test_parse_schedule(raw: str, interval: int | None, first_time: str | None) -> None:
    schedule = parse_schedule(raw)

    assert schedule is not None
    assert schedule.interval_seconds == interval
    assert schedule.first_time == first_time


@pytest.mark.parametrize("raw", ["", "60", "<JobSchedule"])
def test_parse_schedule_rejects_garbage(raw: str) -> None:
    """Нечитаемое расписание не роняет инвентаризацию: признак неудачи — None."""
    assert parse_schedule(raw) is None


# --------------------------------------------------------------------------------------
# Assembly-qualified имена типов
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "fqn", "assembly"),
    [
        ("Sbt.Sample.Steps.CollectStep, Sbt.Sample", "Sbt.Sample.Steps.CollectStep", "Sbt.Sample"),
        ("Sbt.Sample.Models.Aggregate", "Sbt.Sample.Models.Aggregate", None),
        (
            "Foo, Asm, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null",
            "Foo",
            "Asm",
        ),
        (
            "Sbt.Steps.MapStep`2[[System.String, mscorlib],[System.Int32, mscorlib]], Sbt.Sample",
            "Sbt.Steps.MapStep`2[[System.String, mscorlib],[System.Int32, mscorlib]]",
            "Sbt.Sample",
        ),
    ],
)
def test_split_type_name(raw: str, fqn: str, assembly: str | None) -> None:
    assert split_type_name(raw) == (fqn, assembly)


def test_naive_split_would_truncate_generics() -> None:
    """Ради этого случая разбор и написан: `split(",")[0]` даёт обрубок,
    который не найдётся ни в одном индексе символов и выглядит как отсутствующий тип."""
    raw = "Sbt.Steps.MapStep`2[[System.String, mscorlib],[System.Int32, mscorlib]], Sbt.Sample"

    assert raw.split(",")[0] == "Sbt.Steps.MapStep`2[[System.String"
    assert split_type_name(raw)[0] != raw.split(",")[0]


def test_split_type_name_on_fixture_steps(specs: dict[str, RegistrySpec]) -> None:
    result = _read(specs, "workflows")
    v2 = next(item for item in result.items if item.fields["version"] == "2")
    threshold = next(step for step in v2.children if step.ref == "ThresholdStep")

    fqn, assembly = split_type_name(threshold.fields["impl_type"])

    assert fqn.startswith("Sbt.Sample.Steps.MapStep`2[[")
    assert assembly == "Sbt.Sample"


def test_data_type_is_assembly_qualified(specs: dict[str, RegistrySpec]) -> None:
    """Агрегат состояния процесса — кандидат в бизнес-сущность, и он тоже
    записан парой «FQN, сборка»."""
    result = _read(specs, "workflows")
    fqn, assembly = split_type_name(result.items[0].fields["data_type"])

    assert fqn == "Sbt.Sample.Models.SampleAggregate"
    assert assembly == "Sbt.Sample"
