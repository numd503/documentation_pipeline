"""Команда `docpipe worklist` — очередь для внешнего исполнителя шага 3 (M13).

Отличие от `docs status` проверяется прямо: сводка обязана описывать дерево
целиком даже тогда, когда очередь сужена. Ради этого команда и заведена.
"""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig, load_config
from docpipe.hashing import content_hash
from docpipe.tree import doc_path_for

MANIFEST = Path("tests/golden/doc-tree.json")
CONTROLLER = "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"
_SECTION_END = re.compile(r"<!-- docpipe:section:end (\S+) -->")
runner = CliRunner()


def _materialize(root: Path):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["materialize", str(MANIFEST), "--root", str(root)])


def _worklist(root: Path, out: Path, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app, ["worklist", str(MANIFEST), "--root", str(root), "--out", str(out), *extra]
    )


def _read(out: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(out.read_text(encoding="utf-8"))


def _fill(root: Path, doc_path: str, name: str, text: str) -> None:
    path = root / doc_path
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"<!-- docpipe:section:end {name} -->",
            f"{text}\n<!-- docpipe:section:end {name} -->",
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------------------
# Файл и его конверт
# --------------------------------------------------------------------------------------


def test_writes_the_file_after_materialize(tmp_path: Path) -> None:
    _materialize(tmp_path)
    out = tmp_path / "artifacts" / "doc-worklist.json"

    result = _worklist(tmp_path, out)

    assert result.exit_code == 0
    queue = _read(out)
    assert queue["schema_version"] == "1.1"
    assert queue["totals"] == {"documents": 6, "selected": 6, "truncated": False}
    assert {entry["action"] for entry in queue["documents"]} == {"write"}
    # Документы созданы: статус `empty`, а не `missing`, — материализовать нечего.
    assert queue["needs_materialize"] is False


def test_missing_documents_ask_for_materialize(tmp_path: Path) -> None:
    """Исполнитель, открывший `doc_path` со статусом `missing`, файла не найдёт
    и решит, что очередь битая. Флаг существует, чтобы этого не случилось."""
    out = tmp_path / "wl.json"

    result = _worklist(tmp_path, out)

    assert result.exit_code == 0
    queue = _read(out)
    assert queue["needs_materialize"] is True
    assert {entry["status"] for entry in queue["documents"]} == {"missing"}
    assert "materialize" in result.stdout


def test_envelope_points_at_the_manifest(tmp_path: Path) -> None:
    """`manifest_sha256` — единственное, чем чужой процесс проверяет, что читает
    очередь от того самого дерева."""
    _materialize(tmp_path)
    out = tmp_path / "wl.json"

    _worklist(tmp_path, out)

    queue = _read(out)
    assert queue["manifest_sha256"] == content_hash(MANIFEST.read_bytes())
    assert queue["docs_root"] == "docs"
    assert queue["modules_root"] == "docs/modules"
    assert queue["ruleset_version"]


def test_no_time_in_the_file(tmp_path: Path) -> None:
    """Время сделало бы файл меняющимся на каждом прогоне: его нельзя было бы
    ни сравнить между прогонами, ни закоммитить без шума."""
    _materialize(tmp_path)
    out = tmp_path / "wl.json"

    _worklist(tmp_path, out)

    text = out.read_text(encoding="utf-8")
    assert "generated_at" not in text
    assert "timestamp" not in text


def test_second_run_is_byte_identical(tmp_path: Path) -> None:
    _materialize(tmp_path)
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    _worklist(tmp_path, first)
    _worklist(tmp_path, second)

    assert first.read_bytes() == second.read_bytes()


# --------------------------------------------------------------------------------------
# Сводка не сужается фильтрами
# --------------------------------------------------------------------------------------


def test_counts_cover_the_whole_tree_when_the_queue_is_narrowed(tmp_path: Path) -> None:
    """То, ради чего команда отдельная. У `docs status` фильтр сужает и счётчики,
    поэтому сводка и очередь в один её вывод не помещаются."""
    _materialize(tmp_path)
    for section in ("purpose", "api", "behaviour", "collaboration", "notes"):
        _fill(tmp_path, CONTROLLER, section, "Текст.")
    runner.invoke(app, ["docs", "accept", str(MANIFEST), CONTROLLER, "--root", str(tmp_path)])
    out = tmp_path / "wl.json"

    _worklist(tmp_path, out)

    queue = _read(out)
    assert queue["counts"] == {"empty": 5, "current": 1}
    assert queue["totals"]["documents"] == 6
    assert queue["totals"]["selected"] == 5


def test_accepted_tree_gives_an_empty_queue_and_still_writes(tmp_path: Path) -> None:
    """Пустая очередь означает «работы нет», отсутствие файла — «прогон
    не состоялся». Путать эти два состояния внешнему модулю нельзя."""
    _materialize(tmp_path)
    for path in sorted((tmp_path / "docs").rglob("*.md")):
        relative = str(path.relative_to(tmp_path))
        for name in _SECTION_END.findall(path.read_text(encoding="utf-8")):
            _fill(tmp_path, relative, name, "Текст.")
    accepted = runner.invoke(
        app, ["docs", "accept", str(MANIFEST), "--all", "--root", str(tmp_path)]
    )
    assert accepted.exit_code == 0, accepted.stdout
    out = tmp_path / "wl.json"

    result = _worklist(tmp_path, out)

    assert result.exit_code == 0
    queue = _read(out)
    assert queue["documents"] == []
    assert queue["totals"]["selected"] == 0
    assert queue["totals"]["documents"] == 6


# --------------------------------------------------------------------------------------
# Отбор, порядок и порция
# --------------------------------------------------------------------------------------


def test_skip_is_not_in_the_queue_by_default(tmp_path: Path) -> None:
    _materialize(tmp_path)
    for section in ("purpose", "api", "behaviour", "collaboration", "notes"):
        _fill(tmp_path, CONTROLLER, section, "Текст.")
    runner.invoke(app, ["docs", "accept", str(MANIFEST), CONTROLLER, "--root", str(tmp_path)])
    out = tmp_path / "wl.json"

    _worklist(tmp_path, out)

    assert CONTROLLER not in {entry["doc_path"] for entry in _read(out)["documents"]}


def test_limit_takes_the_most_important_first(tmp_path: Path) -> None:
    """Без приоритета статуса `--limit` резал бы очередь по алфавиту и отдавал
    исполнителю `Api/…` вместо документа, которого вообще нет."""
    _materialize(tmp_path)
    # Один документ удалён: он получит `missing`, остальные останутся `empty`.
    (tmp_path / CONTROLLER).unlink()
    out = tmp_path / "wl.json"

    _worklist(tmp_path, out, "--limit", "1")

    queue = _read(out)
    assert queue["totals"] == {"documents": 6, "selected": 1, "truncated": True}
    assert queue["documents"][0]["doc_path"] == CONTROLLER
    assert queue["documents"][0]["status"] == "missing"


def test_bad_action_and_limit_are_user_errors(tmp_path: Path) -> None:
    out = tmp_path / "wl.json"

    assert _worklist(tmp_path, out, "--action", "writte").exit_code == 2
    assert _worklist(tmp_path, out, "--limit", "0").exit_code == 2
    assert not out.exists()


# --------------------------------------------------------------------------------------
# Запись документа общая с `docs status`
# --------------------------------------------------------------------------------------


def test_entry_matches_docs_status(tmp_path: Path) -> None:
    """Копия записи разошлась бы с оригиналом на первом новом поле, и одна
    ситуация получила бы в двух отчётах разные причины."""
    _materialize(tmp_path)
    out = tmp_path / "wl.json"
    _worklist(tmp_path, out)

    status = runner.invoke(
        app,
        ["docs", "status", str(MANIFEST), "--root", str(tmp_path), "--format", "json"],
    )
    by_path = {entry["doc_path"]: entry for entry in json.loads(status.stdout)["documents"]}

    for entry in _read(out)["documents"]:
        assert entry == by_path[entry["doc_path"]]


# --------------------------------------------------------------------------------------
# Отказ
# --------------------------------------------------------------------------------------


def test_blocking_errors_keep_the_previous_file(tmp_path: Path) -> None:
    """Прежняя очередь достовернее полупустой новой, а о коде возврата чужой
    процесс может и не узнать."""
    _materialize(tmp_path)
    out = tmp_path / "wl.json"
    _worklist(tmp_path, out)
    before = out.read_bytes()

    # Два узла на один путь — блокирующая ошибка плана.
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["nodes"][1]["doc_path"] = manifest["nodes"][0]["doc_path"]
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")

    result = runner.invoke(
        app, ["worklist", str(broken), "--root", str(tmp_path), "--out", str(out)]
    )

    assert result.exit_code == 1
    assert out.read_bytes() == before


# --------------------------------------------------------------------------------------
# Схема
# --------------------------------------------------------------------------------------


def test_schema_describes_the_worklist(tmp_path: Path) -> None:
    out = tmp_path / "worklist.schema.json"

    result = runner.invoke(app, ["schema", "--model", "worklist", "--out", str(out)])

    assert result.exit_code == 0
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["title"] == "Worklist"
    assert "documents" in schema["properties"]
    assert runner.invoke(app, ["schema", "--model", "нет-такой"]).exit_code == 2


# --------------------------------------------------------------------------------------
# Настройка путей в docpipe.yaml
# --------------------------------------------------------------------------------------


def test_worklist_path_comes_from_config(tmp_path: Path) -> None:
    _materialize(tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text(f'worklist: "{tmp_path / "queue.json"}"\n', encoding="utf-8")

    result = runner.invoke(
        app, ["worklist", str(MANIFEST), "--root", str(tmp_path), "--config", str(config)]
    )

    assert result.exit_code == 0
    assert (tmp_path / "queue.json").is_file()


def test_modules_dir_is_configurable() -> None:
    """`docs/modules` перестал быть литералом: обе части пути приходят
    из конфигурации."""
    settings = DocpipeConfig(docs_root="documentation", modules_dir="tech")

    assert settings.modules_root == "documentation/tech"
    assert (
        doc_path_for(
            "Sample.Api", "controller", "PricingController", "kind-first", "documentation/tech"
        )
        == "documentation/tech/controllers/Sample.Api/pricing-controller.md"
    )


def test_modules_dir_may_be_empty() -> None:
    """Документы кладутся прямо в корень дерева, без промежуточного каталога."""
    settings = DocpipeConfig(modules_dir="")

    assert settings.modules_root == "docs"
    assert (
        doc_path_for("Sample.Api", "service", "PricingService", "module-first", "docs")
        == "docs/Sample.Api/services/pricing-service.md"
    )


@pytest.mark.parametrize("value", ["/var/docs", "../docs", "docs\\modules"])
def test_paths_outside_the_repository_are_rejected(value: str) -> None:
    """Значение уходит в `doc_path` каждого узла, а тот обязан быть
    репо-относительным и POSIX — иначе манифест непереносим между машинами."""
    with pytest.raises(ValueError):
        DocpipeConfig(docs_root=value)


def test_scan_and_materialize_agree_on_the_configured_tree(tmp_path: Path) -> None:
    """Главное свойство пары ключей: то, что пишет `materialize`, лежит там,
    где ищет `docs status`. Иначе документ навсегда остаётся `missing`."""
    config = tmp_path / "docpipe.yaml"
    config.write_text('docs_root: "documentation"\nmodules_dir: "tech"\n', encoding="utf-8")
    settings = load_config(config)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for node in manifest["nodes"]:
        node["doc_path"] = node["doc_path"].replace("docs/modules/", f"{settings.modules_root}/")
    retargeted = tmp_path / "dt.json"
    retargeted.write_text(json.dumps(manifest), encoding="utf-8")

    runner.invoke(
        app, ["materialize", str(retargeted), "--root", str(tmp_path), "--config", str(config)]
    )
    out = tmp_path / "wl.json"
    result = runner.invoke(
        app,
        [
            "worklist",
            str(retargeted),
            "--root",
            str(tmp_path),
            "--config",
            str(config),
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0
    queue = _read(out)
    assert queue["modules_root"] == "documentation/tech"
    # Документы найдены на новом месте: ни один не `missing`.
    assert queue["needs_materialize"] is False
    assert all(entry["doc_path"].startswith("documentation/tech/") for entry in queue["documents"])


def test_entry_carries_the_file_action(tmp_path: Path) -> None:
    """Очередь и `docs status --format json` собираются одной функцией, поэтому
    поле обязано быть в обеих. `extra="forbid"` у записи это и стережёт."""
    _materialize(tmp_path)
    out = tmp_path / "artifacts" / "doc-worklist.json"

    _worklist(tmp_path, out)
    queue = _read(out)

    assert {doc["file_action"] for doc in queue["documents"]} == {"unchanged"}
