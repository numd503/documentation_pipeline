"""Нормализованный реестр архитектурных элементов (R03).

Формат — контракт между разведкой и графом, и держат его три свойства:
запись без источника не принимается, ключи нормализуются одним правилом
на весь проект, а предложение скилла не может попасть в реестр, минуя
человека. Каждое проверяется здесь, потому что каждое ломается молча.
"""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.arch import (
    ARCH_VERSION,
    ArchRegistry,
    check_document,
    format_statuses,
    load_arch_registry,
    load_optional,
    source_statuses,
    statuses_json,
)
from docpipe.cli import app
from docpipe.route import normalize_route

runner = CliRunner()
EXAMPLE = Path("arch-registry.example.yaml")


def write(path: Path, document: dict) -> Path:
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def record(**overrides) -> dict:
    base = {
        "kind": "entry_point",
        "key": "nightly-reindex",
        "entry_kind": "job",
        "source": {"file": "config/jobs.xml", "record": "Job[@name='nightly-reindex']"},
        "provenance": "manual",
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────────
# Загрузка и отказы
# ──────────────────────────────────────────────────────────────────────────────


def test_empty_registry_is_valid(tmp_path: Path) -> None:
    """Пустой реестр — валидное состояние, а не ошибка.

    На репозитории без реестров граф строится и без него, и «реестров здесь
    нет» — успешный исход разведки, а не пустой (R03 п. 4).
    """
    path = write(tmp_path / "arch.yaml", {"version": ARCH_VERSION, "records": []})
    registry = load_arch_registry(path)
    assert registry.records == ()
    assert load_optional(tmp_path / "нет-такого.yaml").records == ()


def test_record_without_source_is_refused(tmp_path: Path) -> None:
    """Запись без источника не принимается на уровне загрузки (R03 п. 2).

    Причина та же, по которой в правилах отсева обязателен `reason`:
    безымянная запись через месяц неотличима от выдумки.
    """
    entry = record()
    del entry["source"]
    _, problems = check_document({"version": ARCH_VERSION, "records": [entry]})
    assert any("source" in problem.message for problem in problems)


def test_source_without_file_is_refused() -> None:
    entry = record(source={"record": "Job[1]"})
    _, problems = check_document({"version": ARCH_VERSION, "records": [entry]})
    assert any("file" in problem.message for problem in problems)


def test_version_mismatch_names_what_to_do() -> None:
    """Несовпадение версии — отказ с командой миграции, а не молчаливое чтение."""
    _, problems = check_document({"version": "0", "records": []})
    assert problems
    message = problems[0].message
    assert ARCH_VERSION in message
    assert "arch-registry.md" in message


def test_unknown_top_level_key_is_refused() -> None:
    _, problems = check_document({"version": ARCH_VERSION, "registries": []})
    assert any("неизвестные ключи" in problem.message for problem in problems)


def test_unknown_kind_lists_known_kinds() -> None:
    _, problems = check_document({"version": ARCH_VERSION, "records": [record(kind="точка")]})
    assert any("entry_point" in problem.message for problem in problems)


def test_all_problems_are_reported_at_once() -> None:
    """Все находки, а не первая: файл заполняет человек, и второй заход ради
    второй ошибки — способ отучить его заполнять файл."""
    _, problems = check_document(
        {
            "version": ARCH_VERSION,
            "records": [record(kind="точка"), record(key="x", entry_kind="неизвестно")],
        }
    )
    assert len(problems) >= 2


def test_bom_does_not_break_loading(tmp_path: Path) -> None:
    """BOM снимается при чтении: редакторы Windows его ставят, а
    `read_text(encoding="utf-8")` его не снимает — ловушка, уже стоившая
    захода на реестрах платформы."""
    path = tmp_path / "arch.yaml"
    body = yaml.safe_dump({"version": ARCH_VERSION, "records": [record()]}, allow_unicode=True)
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert len(load_arch_registry(path).records) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Граница Р10: предложение скилла не входит в реестр само
# ──────────────────────────────────────────────────────────────────────────────


def test_skill_proposal_is_refused_in_registry() -> None:
    """`skill_proposed` в реестре запрещён, в черновике разрешён.

    Это структурная реализация Р10: недетерминированное входит не в пайплайн,
    а в файл, который человек прочитал. Проверка данными, а не памятью, —
    единственный способ заметить, что подтверждение выпало из цикла (Р-11).
    """
    document = {"version": ARCH_VERSION, "records": [record(provenance="skill_proposed")]}
    registry, problems = check_document(document)
    assert registry is None
    assert any("skill_proposed" in problem.message for problem in problems)

    draft, draft_problems = check_document(document, draft=True)
    assert draft is not None and not draft_problems


# ──────────────────────────────────────────────────────────────────────────────
# Ключи: одно правило на весь проект
# ──────────────────────────────────────────────────────────────────────────────


def test_data_key_normalization_removes_brackets_and_case() -> None:
    registry = ArchRegistry.model_validate(
        {
            "version": ARCH_VERSION,
            "records": [
                {
                    "kind": "data",
                    "key": "[dbo].[USER_TASKS]",
                    "source": {"file": "db.xml"},
                    "provenance": "manual",
                }
            ],
        }
    )
    assert registry.records[0].normalized_key == "dbo.user_tasks"
    assert registry.find("data", "DBO.User_Tasks") is not None


def test_seam_key_uses_the_same_route_normalization() -> None:
    """У шва и у маршрута нормализация одна и та же функция.

    Отдельная нормализация здесь разошлась бы с графовой, и часть записей
    не сошлась бы при том, что обе стороны выглядят правильно (R03 п. 7).
    """
    raw = "https://host/API/Items/{id}?listInnerName=x"
    registry = ArchRegistry.model_validate(
        {
            "version": ARCH_VERSION,
            "records": [
                {
                    "kind": "seam",
                    "key": "items",
                    "seam_kind": "http_route",
                    "literal": raw,
                    "source": {"file": "front/api.ts"},
                    "provenance": "manual",
                }
            ],
        }
    )
    assert registry.records[0].normalized_key == normalize_route(raw)


def test_http_entry_point_key_includes_method() -> None:
    """`GET api/items` и `POST api/items` — разные точки входа."""
    registry = ArchRegistry.model_validate(
        {
            "version": ARCH_VERSION,
            "records": [
                {
                    "kind": "entry_point",
                    "key": "items-list",
                    "entry_kind": "http_endpoint",
                    "http_method": "get",
                    "route": "/api/Items",
                    "source": {"file": "Controllers/Items.cs"},
                    "provenance": "manual",
                }
            ],
        }
    )
    assert registry.records[0].normalized_key == "GET api/items"


def test_duplicate_keys_after_normalization_are_refused() -> None:
    """Один ключ — одна запись, и совпадение считается ПОСЛЕ нормализации."""
    _, problems = check_document(
        {
            "version": ARCH_VERSION,
            "records": [
                {
                    "kind": "data",
                    "key": "[dbo].[FOO]",
                    "source": {"file": "a.xml"},
                    "provenance": "manual",
                },
                {
                    "kind": "data",
                    "key": "dbo.foo",
                    "source": {"file": "b.xml"},
                    "provenance": "manual",
                },
            ],
        }
    )
    assert any("после нормализации" in problem.message for problem in problems)


def test_same_key_in_different_kinds_is_allowed() -> None:
    """Шов `api/x` и точка входа `api/x` — разные вещи и спорить за ключ
    не обязаны."""
    registry, problems = check_document(
        {
            "version": ARCH_VERSION,
            "records": [
                {
                    "kind": "seam",
                    "key": "api/x",
                    "seam_kind": "http_route",
                    "source": {"file": "a.ts"},
                    "provenance": "manual",
                },
                {
                    "kind": "entry_point",
                    "key": "api/x",
                    "entry_kind": "http_endpoint",
                    "route": "api/x",
                    "source": {"file": "b.cs"},
                    "provenance": "manual",
                },
            ],
        }
    )
    assert registry is not None and not problems


# ──────────────────────────────────────────────────────────────────────────────
# Состояние снимков
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def repo_with_registry(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "jobs.xml").write_text("<Jobs/>", encoding="utf-8")
    return root, tmp_path / "arch.yaml"


def test_status_states(repo_with_registry: tuple[Path, Path]) -> None:
    """Пять состояний, и `provenance: adapter` среди них не выделен: всё,
    что лежит в файле, — снимок, и стареет одинаково."""
    root, arch = repo_with_registry
    from docpipe.hashing import content_hash

    digest = content_hash((root / "config" / "jobs.xml").read_bytes())
    write(
        arch,
        {
            "version": ARCH_VERSION,
            "records": [
                record(key="a", source={"file": "config/jobs.xml", "hash": digest}),
                record(key="b", source={"file": "config/jobs.xml", "hash": "sha256:0"}),
                record(key="c", source={"file": "config/jobs.xml"}),
                record(
                    key="d",
                    source={"file": "config/jobs.xml", "hash": digest},
                    provenance="adapter",
                ),
                record(key="e", source={"file": "config/ушёл.xml", "hash": digest}),
            ],
        },
    )
    states = {item.key: item.state for item in source_statuses(load_arch_registry(arch), root)}
    assert states == {
        "a": "current",
        "b": "stale",
        "c": "no_hash",
        "d": "current",
        "e": "source_missing",
    }


def test_missing_source_is_caught_without_a_hash(repo_with_registry: tuple[Path, Path]) -> None:
    """Файл, переименованный или удалённый, хэшем не ловится — его ловит
    проверка существования.

    Это прямо записанная в плане дыра (Р-12): «хэш источника ловит только
    случай „источник менялся“». Существование проверяется отдельно и раньше,
    поэтому запись, указывающая в никуда, видна даже без снимка.
    """
    root, arch = repo_with_registry
    write(
        arch,
        {
            "version": ARCH_VERSION,
            "records": [record(source={"file": "config/переименовали.xml"})],
        },
    )
    statuses = source_statuses(load_arch_registry(arch), root)
    assert [item.state for item in statuses] == ["source_missing"]


def test_status_report_shows_hash_to_paste(repo_with_registry: tuple[Path, Path]) -> None:
    """Отчёт печатает актуальный хэш строкой, готовой к вставке.

    Без этого человек, увидевший «снимок отстал», идёт считать sha256 руками,
    — и это ровно та мелкая работа, из-за которой проверку перестают делать.
    """
    root, arch = repo_with_registry
    write(arch, {"version": ARCH_VERSION, "records": [record(source={"file": "config/jobs.xml"})]})
    report = format_statuses(source_statuses(load_arch_registry(arch), root))
    assert "вписать в `hash:`" in report
    assert "sha256:" in report


def test_status_order_does_not_depend_on_file_order(repo_with_registry: tuple[Path, Path]) -> None:
    root, arch = repo_with_registry
    entries = [
        record(key="ccc", source={"file": "config/jobs.xml"}),
        record(key="aaa", source={"file": "config/jobs.xml"}),
        record(key="bbb", source={"file": "config/jobs.xml"}),
    ]
    write(arch, {"version": ARCH_VERSION, "records": entries})
    first = statuses_json(source_statuses(load_arch_registry(arch), root))
    write(arch, {"version": ARCH_VERSION, "records": list(reversed(entries))})
    second = statuses_json(source_statuses(load_arch_registry(arch), root))
    assert first == second


def test_empty_registry_status_says_so(tmp_path: Path) -> None:
    """«Записей нет» и «проверка не отработала» в отчёте обязаны различаться."""
    assert "Реестр пуст" in format_statuses([])


# ──────────────────────────────────────────────────────────────────────────────
# CLI и пример
# ──────────────────────────────────────────────────────────────────────────────


def test_example_registry_is_valid() -> None:
    """Пример из репозитория обязан проходить проверку: иначе первое, что
    делает человек по инструкции, заканчивается ошибкой."""
    result = runner.invoke(app, ["arch", "validate", str(EXAMPLE)])
    assert result.exit_code == 0, result.output


def test_example_registry_has_no_target_system_traces() -> None:
    """Формат заполняется на открытом репозитории и без единого упоминания
    целевой системы (R03 п. 6, проверка Р11)."""
    text = EXAMPLE.read_text(encoding="utf-8").lower()
    for marker in ("cashflow", "sbt.", "jobtitle", "structure.xml", "ignite"):
        assert marker not in text


def test_cli_validate_lists_every_problem(tmp_path: Path) -> None:
    path = write(
        tmp_path / "arch.yaml",
        {"version": "0", "records": [record(kind="точка"), record(entry_kind="нет")]},
    )
    result = runner.invoke(app, ["arch", "validate", str(path)])
    assert result.exit_code == 1
    assert result.output.count("\n") >= 3


def test_cli_status_json(repo_with_registry: tuple[Path, Path]) -> None:
    root, arch = repo_with_registry
    write(arch, {"version": ARCH_VERSION, "records": [record(source={"file": "config/jobs.xml"})]})
    result = runner.invoke(app, ["arch", "status", str(arch), "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == 1
    assert payload["by_state"]["no_hash"] == 1


def test_cli_status_fails_only_when_asked(repo_with_registry: tuple[Path, Path]) -> None:
    """Красным по умолчанию отчёт не бывает: линт, красный с первого дня,
    выключают на второй."""
    root, arch = repo_with_registry
    write(
        arch,
        {
            "version": ARCH_VERSION,
            "records": [record(source={"file": "config/jobs.xml", "hash": "sha256:0"})],
        },
    )
    quiet = runner.invoke(app, ["arch", "status", str(arch), "--root", str(root)])
    assert quiet.exit_code == 0
    strict = runner.invoke(
        app, ["arch", "status", str(arch), "--root", str(root), "--fail-on-stale"]
    )
    assert strict.exit_code == 1


def test_cli_reads_path_from_config(tmp_path: Path) -> None:
    """Ключ `arch` в конфигурации читается, и путь внутри неё разрешается
    относительно неё же — как `rules` и `templates`."""
    (tmp_path / "docs").mkdir()
    config = tmp_path / "docs" / "docpipe.yaml"
    config.write_text("arch: arch-registry.yaml\n", encoding="utf-8")
    write(tmp_path / "docs" / "arch-registry.yaml", {"version": ARCH_VERSION, "records": []})
    result = runner.invoke(app, ["arch", "validate", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "пуст" in result.output


def test_schema_is_generated_from_models(tmp_path: Path) -> None:
    """Схема — производная от моделей, а не отдельно поддерживаемый файл."""
    out = tmp_path / "arch.schema.json"
    result = runner.invoke(app, ["schema", "--model", "arch", "--out", str(out)])
    assert result.exit_code == 0, result.output
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert "EntryPointRecord" in json.dumps(schema, ensure_ascii=False)
