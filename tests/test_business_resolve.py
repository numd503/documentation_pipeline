"""Разрешение якорей: три ступени и их границы (B05).

Ступени идут по порядку, первая сработавшая выигрывает. Здесь проверяются
и сами ступени, и их намеренные границы: пустой литеральный поиск для
диспетчеризуемых по данным видов, отказ выбирать версию workflow за автора
и молчание `type` в фактах.
"""

from pathlib import Path

import pytest

from docpipe.business import load_catalog, resolve
from docpipe.business.model import Anchor
from docpipe.business.resolve import DATA_DISPATCHED, ResolveContext
from docpipe.model import DocNode
from tests.business_support import BUSINESS_ROOT, context, node


@pytest.fixture
def ctx() -> ResolveContext:
    return context()


# --------------------------------------------------------------------------------------
# Ступень 1: реестр
# --------------------------------------------------------------------------------------


def test_workflow_resolves_to_steps(ctx: ResolveContext) -> None:
    resolution = resolve(Anchor(kind="workflow", ref="SampleWorkflow", version="2"), ctx)

    assert resolution.confidence == "registry"
    assert resolution.facts["id"] == "SampleWorkflow"
    assert [step["id"] for step in resolution.facts["steps"]] == [
        "CollectStep",
        "ScoreStep",
        "ThresholdStep",
    ]


def test_workflow_steps_are_sorted_not_declared(ctx: ResolveContext) -> None:
    """Порядок в JSON источником порядка не является: связь шагов несёт
    `NextStepId`, а не позиция в массиве."""
    resolution = resolve(Anchor(kind="workflow", ref="SampleWorkflow", version="2"), ctx)
    steps = {step["id"]: step["next"] for step in resolution.facts["steps"]}

    assert steps == {"CollectStep": "ThresholdStep", "ThresholdStep": "ScoreStep", "ScoreStep": ""}


def test_workflow_without_version_is_ambiguous(ctx: ResolveContext) -> None:
    """Выбирать версию самим нельзя: состав шагов у версий разный, и молчаливый
    выбор зафиксировал бы в хэше произвольную."""
    resolution = resolve(Anchor(kind="workflow", ref="SampleWorkflow"), ctx)

    assert resolution.confidence == "unresolved"
    assert resolution.candidates == ["SampleWorkflow@1", "SampleWorkflow@2"]
    assert resolution.facts == {}


def test_job_resolves_to_schedule(ctx: ResolveContext) -> None:
    resolution = resolve(Anchor(kind="job", ref="PM: Load limits"), ctx)

    assert resolution.confidence == "registry"
    assert resolution.facts == {
        "title": "PM: Load limits",
        "interval_seconds": 86400,
        "first_time": "2020-01-01",
    }


def test_job_state_flags_are_not_facts(ctx: ResolveContext) -> None:
    """`JOBDISABLED` — состояние боевой БД, которого инструмент не видит.

    Прочитанный флаг однажды прочтут как факт, поэтому его нет ни в реестре,
    ни в фактах.
    """
    resolution = resolve(Anchor(kind="job", ref="PM: Load limits"), ctx)

    assert "disabled" not in resolution.facts
    assert "JOBDISABLED" not in str(resolution.facts)


def test_grid_service_resolves_to_public_methods(ctx: ResolveContext) -> None:
    """Методы вызываются по имени через прокси — они и есть контракт.
    Имя класса контрактом не является и в факты не входит."""
    resolution = resolve(Anchor(kind="grid_service", ref="ICalcResult"), ctx)

    assert resolution.facts == {"name": "ICalcResult", "methods": ["Calc", "Recalc"]}
    assert "CalcResult" not in resolution.facts["methods"]


def test_list_event_collects_assemblies_of_all_handlers(ctx: ResolveContext) -> None:
    """На паре «UserTasks + ItemAdded» два обработчика из разных сборок, и в
    факты идут ОБЕ. Сборки отсортированы, а в XML они лежат в обратном
    порядке — значит проверяется сортировка, а не порядок записей."""
    resolution = resolve(Anchor(kind="list_event", ref="ItemAdded", scope="UserTasks"), ctx)

    assert resolution.facts == {
        "list": "UserTasks",
        "event": "ItemAdded",
        "assemblies": [
            "Sbt.Cashflow.ML.EventReceivers",
            "Sbt.Cashflow.Reports.EventReceivers",
        ],
    }


def test_two_handlers_on_one_pair_are_not_ambiguity(ctx: ResolveContext) -> None:
    """Несколько записей на якорь — неоднозначность для всех видов, кроме
    обработчиков события: платформа вызывает всех подписчиков, поэтому пара
    «список + EventType» — одна точка входа на всех, и весь набор и есть факт.

    Разделить их якорем нельзя и не нужно: класс подписчика в якорь не входит,
    иначе его переименование ломало бы бизнес-документ.
    """
    resolution = resolve(Anchor(kind="list_event", ref="ItemAdded", scope="UserTasks"), ctx)

    assert resolution.confidence == "registry"
    assert not resolution.candidates
    assert sorted(target.fqn for target in resolution.targets) == [
        "Sbt.Cashflow.ML.EventReceivers.UserTasksAddedTriggerSampleWorkflowEventReceiver",
        "Sbt.Cashflow.Reports.EventReceivers.UserTasksAddedAuditEventReceiver",
    ]


def test_table_resolves_to_field_composition(ctx: ResolveContext) -> None:
    """Вид поля читается по префиксу `Field*`: `FieldSomethingNew` обязан
    попасть в состав, иначе белый список видов молча терял бы поля."""
    resolution = resolve(Anchor(kind="table", ref="UserTasks"), ctx)
    names = [field[0] for field in resolution.facts["fields"]]

    assert names == [
        "UserTasksExperimental",
        "UserTasksTaskStartTime",
        "UserTasksTitle",
        "UserTasksType",
    ]
    # Связь между сущностями объявлена, а не выведена: `ListSource` входит
    # в состав, поэтому её пропажа меняет хэш.
    lookup = next(field for field in resolution.facts["fields"] if field[0] == "UserTasksType")
    assert lookup[1] == "FieldLookup"
    assert lookup[5] == "UserTaskTypes"


def test_registry_kinds_differ_from_anchor_kinds(ctx: ResolveContext) -> None:
    """Аналитик пишет `table` и `kafka` — слова предметной области; реестр
    объявляет `list` и `kafka_topic` — слова платформы. Мост между словарями
    один и лежит в `REGISTRY_KIND`."""
    assert resolve(Anchor(kind="table", ref="UserTasks"), ctx).confidence == "registry"
    assert resolve(Anchor(kind="list", ref="UserTasks"), ctx).confidence == "unresolved"


# --------------------------------------------------------------------------------------
# Ступень 2: литерал
# --------------------------------------------------------------------------------------


def test_literal_rung_is_empty_for_data_dispatched_anchors(ctx: ResolveContext) -> None:
    """Для workflow, job, grid_service и list_event пустой литеральный поиск
    не является ошибкой: диспетчеризация идёт по данным.

    Литерала конкретного workflow в C# не существует структурно. Реализация,
    считающая это дефектом, дала бы вечно красный линт по всем 45 workflow,
    после чего его выключили бы целиком.
    """
    for kind in ("workflow", "job", "grid_service", "list_event"):
        assert kind in DATA_DISPATCHED

    # Якоря, которых нет ни в одном реестре: ступень 1 не сработала,
    # ступень 2 для них не запускается вовсе.
    for kind in sorted(DATA_DISPATCHED):
        resolution = resolve(Anchor(kind=kind, ref="Нет такого", scope="UserTasks"), ctx)
        assert resolution.confidence == "unresolved"
        assert resolution.tried == ["реестр"]


def test_literal_rung_finds_string_in_manifest_sources(tmp_path: Path) -> None:
    """Поиск идёт по `symbol.sources[].path` из манифеста, а не обходом ФС:
    иначе в результаты попал бы вывод сборки и каталог самого инструмента."""
    (tmp_path / "src" / "App").mkdir(parents=True)
    (tmp_path / "src" / "App" / "Publisher.cs").write_text(
        'class Publisher { const string Topic = "pricing.eod.requested"; }',
        encoding="utf-8",
    )
    # Файл вне манифеста: содержит ту же строку, но найден быть не должен.
    (tmp_path / "src" / "App" / "Ignored.cs").write_text("pricing.eod.requested", encoding="utf-8")

    publisher: DocNode = node("App.Publisher")
    ctx = ResolveContext(
        source_paths=["src/App/Publisher.cs"],
        nodes_by_id={publisher.id: publisher},
        root=tmp_path,
    )
    resolution = resolve(Anchor(kind="kafka", ref="pricing.eod.requested"), ctx)

    assert resolution.confidence == "literal"
    assert resolution.sources == ["src/App/Publisher.cs:1"]
    assert resolution.facts == {}


# --------------------------------------------------------------------------------------
# Ступень 3: индекс символов
# --------------------------------------------------------------------------------------


def test_type_anchor_resolves_through_symbol_rung(ctx: ResolveContext) -> None:
    resolution = resolve(Anchor(kind="type", ref="Sbt.Sample.Models.SampleAggregate"), ctx)

    assert resolution.confidence == "symbol"
    assert resolution.targets[0].doc_path.endswith("sampleaggregate.md")


def test_type_anchor_contributes_no_facts(ctx: ResolveContext) -> None:
    """Форма типа в манифесте не хранится, а вычисленная по членам была бы
    пустой у каждого `record` с позиционными параметрами. Пустая форма у всех
    записей сразу — худший исход: хэши совпали бы, и изменение контракта
    прошло бы незамеченным. Поэтому `type` разрешается, но фактов не вносит.
    """
    resolution = resolve(Anchor(kind="type", ref="Sbt.Sample.Models.SampleAggregate"), ctx)

    assert resolution.facts == {}


def test_unknown_type_is_not_declared_dead(ctx: ResolveContext) -> None:
    """«Не найден среди узлов документации» — не то же самое, что «типа нет»:
    узлами становятся только enrolled и классифицированные типы."""
    resolution = resolve(Anchor(kind="type", ref="App.NoSuchType"), ctx)

    assert resolution.confidence == "unresolved"
    assert resolution.tried == ["реестр", "литерал", "индекс символов"]


# --------------------------------------------------------------------------------------
# Граница зоны ответственности
# --------------------------------------------------------------------------------------


def test_unverified_anchor_is_not_resolved_at_all(ctx: ResolveContext) -> None:
    """`verify: false` — объявленная чужая зона, а не неудача поиска.
    Разрешать её значило бы получить находку линта на чужом коде."""
    anchor = Anchor(kind="kafka", ref="pricing.eod.requested", verify=False)
    resolution = resolve(anchor, ctx)

    assert resolution.confidence == "unresolved"
    assert resolution.tried == []
    assert resolution.facts == {}


def test_catalog_anchors_resolve_against_fixture_registries(ctx: ResolveContext) -> None:
    """Сквозная проверка: якоря фикстурного каталога разрешаются реестрами."""
    catalog = load_catalog(BUSINESS_ROOT, "business")
    doc = catalog.by_id()["bp.valuation.twinml-scoring"]

    assert [resolve(anchor, ctx).confidence for anchor in doc.entry] == ["registry", "registry"]
    assert resolve(doc.upstream[0], ctx).confidence == "unresolved"


# --------------------------------------------------------------------------------------
# Детерминизм
# --------------------------------------------------------------------------------------


def test_resolution_is_stable_across_calls(ctx: ResolveContext) -> None:
    anchor = Anchor(kind="workflow", ref="SampleWorkflow", version="2")

    assert resolve(anchor, ctx).model_dump_json() == resolve(anchor, ctx).model_dump_json()
