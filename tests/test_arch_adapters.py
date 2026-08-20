"""Адаптеры реестров (R04).

Два адаптера и одно правило, которое их обоих удерживает честными: снимок
и адаптер на одном источнике обязаны совпадать запись в запись. Без этого
теста два пути записи разойдутся — и разойдутся молча, потому что каждый
по отдельности выглядит правильным.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.arch import (
    ARCH_VERSION,
    AdapterSpec,
    check_document,
    collect,
    dump_registry,
    load_arch_registry,
    run_adapter,
)
from docpipe.arch.model import DataRecord, EntryPointRecord
from docpipe.cli import app

runner = CliRunner()
FIXTURES = Path("tests/fixtures/registries")
SPEC = FIXTURES / "registries.yaml"

runner_env = {"COLUMNS": "200"}


def registries_records() -> tuple:
    result = run_adapter("registries", {"spec": str(SPEC)}, FIXTURES, lambda value: Path(value))
    return result.records, result.errors


# ──────────────────────────────────────────────────────────────────────────────
# Подключение по имени
# ──────────────────────────────────────────────────────────────────────────────


def test_unknown_adapter_lists_known_ones() -> None:
    """Тихо пропустить адаптер нельзя: реестр без части записей внешне
    неотличим от репозитория, где этих точек входа нет вовсе."""
    with pytest.raises(ValueError, match="registries"):
        run_adapter("никакой", {}, FIXTURES)


def test_unknown_option_is_refused() -> None:
    """Опечатка в имени параметра — отказ, а не молчание."""
    with pytest.raises(ValueError, match="неизвестные параметры"):
        run_adapter("registries", {"spec": str(SPEC), "kind": "job"}, FIXTURES)


def test_missing_required_option_is_refused() -> None:
    with pytest.raises(ValueError, match="spec"):
        run_adapter("registries", {}, FIXTURES)


def test_core_does_not_know_any_concrete_registry() -> None:
    """Ядро о существовании конкретного реестра не знает (R04 п. 1, Р11).

    Проверка структурная: пути, теги и имена атрибутов живут в конфигурации,
    и `grep` по коду адаптеров их не находит. Нарушают это правило не злым
    умыслом, а по одной уступке за раз.
    """
    for path in Path("docpipe/arch").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for marker in ("cashflow", "structure.xml", "jobtitle", "sbt.", "tornado"):
            assert marker not in text, f"{path}: встречается «{marker}»"


# ──────────────────────────────────────────────────────────────────────────────
# Адаптер декларативных реестров
# ──────────────────────────────────────────────────────────────────────────────


def test_registries_adapter_produces_valid_records() -> None:
    """Адаптер выдаёт записи того же формата и проходит ту же валидацию.

    Второй путь записи — это вторая реализация формата, и она разойдётся
    с первой; поэтому вывод адаптера прогоняется через тот же загрузчик.
    """
    records, _ = registries_records()
    assert records
    document = yaml.safe_load(dump_registry_of(records))
    registry, problems = check_document(document)
    assert registry is not None, problems
    assert len(registry.records) == len(records)


def dump_registry_of(records: tuple) -> str:
    from docpipe.arch.model import ArchRegistry

    return dump_registry(ArchRegistry(version=ARCH_VERSION, records=records))


def test_version_is_part_of_the_workflow_key() -> None:
    """Один и тот же процесс живёт в нескольких версиях сразу.

    Ключ без версии склеил бы их, и вторая пропала бы как дубль — то есть
    инструмент уверенно отвечал бы про не ту версию процесса.
    """
    records, _ = registries_records()
    workflows = {
        record.key for record in records if getattr(record, "entry_kind", "") == "workflow"
    }
    assert any("@" in key for key in workflows), workflows


def test_event_handler_carries_list_and_event() -> None:
    """«Список + EventType» — якорь уровня контракта: одно только имя события
    не адрес, `ItemAdded` есть у десятка списков."""
    records, _ = registries_records()
    handlers = [
        record
        for record in records
        if isinstance(record, EntryPointRecord) and record.entry_kind == "event_handler"
    ]
    keys = {record.key for record in handlers}
    assert "UserTasks:ItemAdded" in keys


def test_two_handlers_on_one_anchor_are_one_record_with_two_implementations() -> None:
    """На паре «список + EventType» сидят два обработчика — аудит и запуск
    процесса, — и оба срабатывают. Одна строка `impl` потеряла бы второй молча.
    """
    records, _ = registries_records()
    handler = next(
        record
        for record in records
        if isinstance(record, EntryPointRecord) and record.key == "UserTasks:ItemAdded"
    )
    assert len(handler.impl) == 2
    assert handler.impl == tuple(sorted(handler.impl))
    # Атрибуты, разошедшиеся между реализациями, выброшены: записать одно
    # из двух значений `assembly` значит соврать про вторую реализацию.
    assert "assembly" not in handler.attributes


def test_event_handler_touches_its_list() -> None:
    """Связь «обработчик → таблица своего списка» известна из декларации,
    и проходить за ней через код не нужно."""
    records, _ = registries_records()
    handler = next(
        record
        for record in records
        if isinstance(record, EntryPointRecord) and record.key == "UserTasks:ItemAdded"
    )
    assert handler.touches == ("UserTasks",)


def test_list_becomes_data_node_with_fields_and_references() -> None:
    records, _ = registries_records()
    lists = [record for record in records if isinstance(record, DataRecord)]
    tasks = next(record for record in lists if record.key == "UserTasks")
    assert tasks.table == "USER_TASKS"
    assert tasks.normalized_key == "user_tasks"
    assert tasks.name == "Задачи пользователей"
    kinds = {field.kind for field in tasks.fields}
    # Вид поля читается как есть: белый список молча потерял бы поле
    # неизвестного вида, воспроизведённое в фикстуре ради этого случая.
    assert "FieldSomethingNew" in kinds
    assert "UserTaskTypes" in tasks.references


def test_broken_source_is_a_finding_not_a_failure() -> None:
    """Один битый файл не роняет прогон: реестров десятки, и правились они
    годами разными командами."""
    records, errors = registries_records()
    assert records
    assert any("не разбирается" in error for error in errors)


def test_unmapped_registry_kind_is_named(tmp_path: Path) -> None:
    """Вид записи, которому не назначен вид графа, называется вслух.

    Молча пропущенная запись через месяц неотличима от записи, которой
    в реестре нет.
    """
    spec = tmp_path / "registries.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "registries_version": "1",
                "registries": [
                    {
                        "id": "странные",
                        "kind": "невиданный_вид",
                        "format": "inline",
                        "items": [{"ref": "что-то"}],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    result = run_adapter("registries", {"spec": str(spec)}, tmp_path, lambda value: Path(value))
    assert result.records == ()
    assert any("невиданный_вид" in error for error in result.errors)


def test_kinds_override_from_configuration(tmp_path: Path) -> None:
    """Новый вид реестра — строка в конфигурации, а не правка кода."""
    spec = tmp_path / "registries.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "version": "1",
                "registries_version": "1",
                "registries": [
                    {
                        "id": "очереди",
                        "kind": "наши_очереди",
                        "format": "inline",
                        "items": [{"ref": "orders.created"}],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    result = run_adapter(
        "registries",
        {"spec": str(spec), "kinds": {"наши_очереди": "seam:queue"}},
        tmp_path,
        lambda value: Path(value),
    )
    assert [record.kind for record in result.records] == ["seam"]
    assert result.records[0].normalized_key == "orders.created"


# ──────────────────────────────────────────────────────────────────────────────
# Снимок и адаптер совпадают запись в запись
# ──────────────────────────────────────────────────────────────────────────────


def test_snapshot_equals_adapter_record_for_record(tmp_path: Path) -> None:
    """R04 п. 3 — тест, который единственный удерживает два пути честными.

    Снимок снимается с адаптера, читается обратно загрузчиком и сравнивается
    с тем, что выдал адаптер. Расхождение здесь означает, что формат
    существует в двух версиях, и вторая уже разошлась с первой.
    """
    records, _ = registries_records()
    snapshot = tmp_path / "arch.yaml"
    snapshot.write_text(dump_registry_of(records), encoding="utf-8")
    loaded = load_arch_registry(snapshot)
    assert len(loaded.records) == len(records)
    for after, before in zip(loaded.records, records, strict=True):
        assert after == before


def test_snapshot_carries_hashes_and_starts_checking_at_once(tmp_path: Path) -> None:
    """Снимок с адаптера пишется с хэшами: иначе человеку пришлось бы
    дописывать сорок строк руками, а это ровно та работа, из-за которой
    проверку перестают делать."""
    records, _ = registries_records()
    snapshot = tmp_path / "arch.yaml"
    snapshot.write_text(dump_registry_of(records), encoding="utf-8")
    from docpipe.arch import source_statuses

    states = {item.state for item in source_statuses(load_arch_registry(snapshot), FIXTURES)}
    assert states == {"current"}


# ──────────────────────────────────────────────────────────────────────────────
# Адаптер реестра, записанного кодом
# ──────────────────────────────────────────────────────────────────────────────


REGISTRY_MODULE = '''
"""Модуль, который нельзя импортировать: он падает при импорте."""

import модуля_которого_нет  # noqa

SERVICES = {
    "forecast": ForecastHandler,
    "structure": handlers.StructureHandler,
}

ROUTES = [
    ("/models/train", TrainHandler),
    ("/models/score", ScoreHandler()),
]


def wire(app):
    register("audit", AuditHandler)
'''


def test_code_registry_is_parsed_without_import(tmp_path: Path) -> None:
    """Реестр в виде исполняемого кода разбирается статически (R04 п. 5).

    Модуль фикстуры падает при импорте намеренно: импорт чужого файла ради
    «посмотреть, что зарегистрировалось» — это выполнение произвольного кода
    репозитория при разведке.
    """
    (tmp_path / "services.py").write_text(REGISTRY_MODULE, encoding="utf-8")
    result = run_adapter(
        "python_code", {"path": "services.py", "call": "register"}, tmp_path, lambda v: Path(v)
    )
    keys = {record.key for record in result.records}
    assert keys == {"forecast", "structure", "/models/train", "/models/score", "audit"}
    impls = {record.key: record.impl for record in result.records}
    assert impls["structure"] == ("handlers.StructureHandler",)
    # Вызов в таблице регистрации — тот же класс, а не другой объект.
    assert impls["/models/score"] == ("ScoreHandler",)


def test_code_registry_can_be_narrowed_to_one_variable(tmp_path: Path) -> None:
    (tmp_path / "services.py").write_text(REGISTRY_MODULE, encoding="utf-8")
    result = run_adapter(
        "python_code", {"path": "services.py", "variable": "SERVICES"}, tmp_path, lambda v: Path(v)
    )
    assert {record.key for record in result.records} == {"forecast", "structure"}


def test_code_registry_says_when_it_found_nothing(tmp_path: Path) -> None:
    """Ноль записей произносится вслух: пустой результат внешне неотличим
    от «в файле нет регистраций», а причина обычно другая."""
    (tmp_path / "services.py").write_text("x = 1\n", encoding="utf-8")
    result = run_adapter("python_code", {"path": "services.py"}, tmp_path, lambda v: Path(v))
    assert result.records == ()
    assert any("не нашлось" in error for error in result.errors)


def test_code_registry_survives_syntax_error(tmp_path: Path) -> None:
    (tmp_path / "services.py").write_text("def (:\n", encoding="utf-8")
    result = run_adapter("python_code", {"path": "services.py"}, tmp_path, lambda v: Path(v))
    assert result.records == ()
    assert any("не разбирается" in error for error in result.errors)


# ──────────────────────────────────────────────────────────────────────────────
# Сбор: снимок плюс адаптеры
# ──────────────────────────────────────────────────────────────────────────────


def test_snapshot_wins_over_adapter(tmp_path: Path) -> None:
    """Выигрывает подтверждённое человеком (Р10), и факт совпадения печатается:
    молча выброшенная запись через месяц неотличима от потерянной."""
    (tmp_path / "services.py").write_text(REGISTRY_MODULE, encoding="utf-8")
    snapshot = tmp_path / "arch.yaml"
    snapshot.write_text(
        yaml.safe_dump(
            {
                "version": ARCH_VERSION,
                "records": [
                    {
                        "kind": "entry_point",
                        "key": "forecast",
                        "entry_kind": "service",
                        "name": "Прогноз, проверено человеком",
                        "source": {"file": "services.py", "record": "SERVICES['forecast']"},
                        "provenance": "manual",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    collected = collect(
        snapshot,
        [AdapterSpec(id="код", adapter="python_code", options={"path": "services.py"})],
        tmp_path,
        lambda value: Path(value),
    )
    forecast = collected.registry.find("entry_point", "forecast")
    assert forecast is not None
    assert forecast.provenance == "manual"
    assert collected.shadowed_by_file == ["код: entry_point forecast"] or list(
        collected.shadowed_by_file
    ) == ["код: entry_point forecast"]


def test_no_adapters_is_a_working_state(tmp_path: Path) -> None:
    """Отсутствие адаптера ничего не блокирует: снимок остаётся способом,
    а на репозитории, который видишь впервые, он единственный (R04 п. 4)."""
    snapshot = tmp_path / "arch.yaml"
    snapshot.write_text(
        yaml.safe_dump({"version": ARCH_VERSION, "records": []}, allow_unicode=True),
        encoding="utf-8",
    )
    collected = collect(snapshot, [], tmp_path)
    assert collected.registry.records == ()
    assert collected.from_adapters == ()
    assert collected.errors == ()


def test_adapter_failure_does_not_lose_the_snapshot(tmp_path: Path) -> None:
    """Сломанный адаптер — находка, а не потеря реестра."""
    snapshot = tmp_path / "arch.yaml"
    snapshot.write_text(
        yaml.safe_dump(
            {
                "version": ARCH_VERSION,
                "records": [
                    {
                        "kind": "seam",
                        "key": "api/x",
                        "seam_kind": "http_route",
                        "source": {"file": "a.ts"},
                        "provenance": "manual",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    collected = collect(
        snapshot,
        [AdapterSpec(id="сломан", adapter="registries", options={})],
        tmp_path,
        lambda value: Path(value),
    )
    assert len(collected.registry.records) == 1
    assert any("сломан" in error for error in collected.errors)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def test_cli_records_and_snapshot(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "arch_adapters": [
                    {"id": "платформа", "adapter": "registries", "options": {"spec": str(SPEC)}}
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    listed = runner.invoke(
        app, ["arch", "records", "--config", str(config), "--root", str(FIXTURES)]
    )
    assert listed.exit_code == 0, listed.output
    assert "адаптер платформа" in listed.output

    out = tmp_path / "arch.yaml"
    taken = runner.invoke(
        app,
        ["arch", "snapshot", "--config", str(config), "--root", str(FIXTURES), "--out", str(out)],
    )
    assert taken.exit_code == 0, taken.output
    assert load_arch_registry(out).records


def test_cli_snapshot_without_adapters_refuses(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text("", encoding="utf-8")
    result = runner.invoke(
        app, ["arch", "snapshot", "--config", str(config), "--out", str(tmp_path / "a.yaml")]
    )
    assert result.exit_code == 2
    assert "снимать нечего" in result.output
