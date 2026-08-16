"""Команда `docs status` — вход агента шага 3 (M09).

Команда информационная: ни один тест здесь не проверяет запись, зато один
проверяет её отсутствие. Информационная команда, которая что-то меняет,
перестаёт гоняться в CI.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app

MANIFEST = Path("tests/golden/doc-tree.json")
CONTROLLER = "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"
runner = CliRunner()


def _materialize(root: Path, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["materialize", str(manifest), "--root", str(root)])


def _status(root: Path, *extra: str, manifest: Path = MANIFEST):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["docs", "status", str(manifest), "--root", str(root), *extra])


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
# Свежее дерево
# --------------------------------------------------------------------------------------


def test_fresh_tree_is_all_empty_and_needs_writing(tmp_path: Path) -> None:
    _materialize(tmp_path)

    result = _status(tmp_path)

    assert result.exit_code == 0
    assert "empty            6" in result.stdout
    assert result.stdout.count("write   empty") == 6


def test_status_writes_nothing(tmp_path: Path) -> None:
    """Соблазн «заодно починить front matter» превращает информационную
    команду в изменяющую."""
    _materialize(tmp_path)
    before = {path: path.read_bytes() for path in sorted((tmp_path / "docs").rglob("*.md"))}

    _status(tmp_path)

    assert {path: path.read_bytes() for path in before} == before


# --------------------------------------------------------------------------------------
# Формат json
# --------------------------------------------------------------------------------------


def test_json_shape(tmp_path: Path) -> None:
    _materialize(tmp_path)

    payload = json.loads(_status(tmp_path, "--format", "json").stdout)

    assert payload["total"] == 6
    assert payload["counts"] == {"empty": 6}
    assert payload["manifest_partial"] is False
    assert [doc["doc_path"] for doc in payload["documents"]] == sorted(
        doc["doc_path"] for doc in payload["documents"]
    )

    first = payload["documents"][0]
    assert set(first) == {
        "action",
        "file_action",
        "reason",
        "changes",
        "doc_path",
        "node_id",
        "status",
        "kind",
        "template_ref",
        "example_ref",
        "team",
        "empty_sections",
        "orphan_sections",
        "sources",
        "broken_links",
    }
    assert first["sources"][0]["path"].endswith(".cs")


def test_json_is_byte_stable(tmp_path: Path) -> None:
    """Иначе агент увидит изменение там, где его нет."""
    _materialize(tmp_path)

    assert (
        _status(tmp_path, "--format", "json").stdout == _status(tmp_path, "--format", "json").stdout
    )


def test_example_ref_is_null_where_there_is_no_example(tmp_path: Path) -> None:
    _materialize(tmp_path)

    payload = json.loads(_status(tmp_path, "--format", "json").stdout)
    ignite = next(doc for doc in payload["documents"] if doc["kind"] == "ignite_service")

    assert ignite["template_ref"] == "templates/ignite-service.md"
    assert ignite["example_ref"] is None


# --------------------------------------------------------------------------------------
# Фильтры
# --------------------------------------------------------------------------------------


def test_positional_path_narrows_to_a_directory(tmp_path: Path) -> None:
    """При раскладке `kind-first` каталог верхнего уровня — вид сущности.

    Выбрать префиксом модуль целиком больше нельзя: его документы разложены
    по каталогам видов. Это единственная плата за раскладку, и здесь она
    зафиксирована как поведение, а не обойдена.
    """
    _materialize(tmp_path)

    result = _status(tmp_path, "docs/modules/controllers", "--format", "json")
    payload = json.loads(result.stdout)

    assert payload["total"] == 2
    assert all(
        doc["doc_path"].startswith("docs/modules/controllers/") for doc in payload["documents"]
    )


def test_positional_path_narrows_to_one_module_inside_a_kind(tmp_path: Path) -> None:
    """Модуль выбирается префиксом внутри вида: `{вид}/{модуль}`."""
    _materialize(tmp_path)

    payload = json.loads(
        _status(tmp_path, "docs/modules/controllers/Sample.Common", "--format", "json").stdout
    )

    assert payload["total"] == 1
    assert payload["documents"][0]["doc_path"].startswith("docs/modules/controllers/Sample.Common/")


def test_positional_path_accepts_a_file(tmp_path: Path) -> None:
    _materialize(tmp_path)

    payload = json.loads(_status(tmp_path, CONTROLLER, "--format", "json").stdout)

    assert payload["total"] == 1


def test_action_filter(tmp_path: Path) -> None:
    _materialize(tmp_path)

    payload = json.loads(_status(tmp_path, "--action", "review", "--format", "json").stdout)

    assert payload["total"] == 0


def test_partially_filled_document_still_needs_writing(tmp_path: Path) -> None:
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER, "purpose", "Написано.")

    payload = json.loads(_status(tmp_path, CONTROLLER, "--format", "json").stdout)
    doc = payload["documents"][0]

    assert doc["action"] == "write"
    assert doc["status"] == "empty"
    assert "purpose" not in doc["empty_sections"]
    assert doc["empty_sections"] == ["api", "behaviour", "collaboration", "notes"]


# --------------------------------------------------------------------------------------
# Коды возврата
# --------------------------------------------------------------------------------------


def test_fail_on_matches(tmp_path: Path) -> None:
    _materialize(tmp_path)

    assert _status(tmp_path, "--fail-on", "empty").exit_code == 1
    assert _status(tmp_path, "--fail-on", "stale").exit_code == 0


def test_fail_on_typo_is_a_user_error(tmp_path: Path) -> None:
    """Опечатка `--fail-on statle` иначе дала бы вечно зелёную проверку в CI."""
    _materialize(tmp_path)

    result = _status(tmp_path, "--fail-on", "statle")

    assert result.exit_code == 2
    assert "неизвестные значения statle" in result.stderr
    assert "empty" in result.stderr


def test_action_typo_is_a_user_error(tmp_path: Path) -> None:
    _materialize(tmp_path)

    result = _status(tmp_path, "--action", "wrte")

    assert result.exit_code == 2
    assert "неизвестные значения wrte" in result.stderr


# --------------------------------------------------------------------------------------
# Битые ссылки в авторских секциях
# --------------------------------------------------------------------------------------


def test_broken_link_inside_an_authored_section(tmp_path: Path) -> None:
    """Ссылки в генерируемых блоках чинятся сами — блоки пересобираются.
    Поставленные агентом внутри своих секций не чинятся, поэтому о них сообщают."""
    _materialize(tmp_path)
    _fill(tmp_path, CONTROLLER, "purpose", "См. [соседний](../services/нет-такого.md).")

    payload = json.loads(_status(tmp_path, CONTROLLER, "--format", "json").stdout)

    assert payload["documents"][0]["broken_links"] == ["../services/нет-такого.md"]


def test_working_link_is_not_reported(tmp_path: Path) -> None:
    _materialize(tmp_path)
    link = "../../services/Sample.Pricing.Api/pricing-service.md"
    _fill(tmp_path, CONTROLLER, "purpose", f"См. [сервис]({link}).")

    payload = json.loads(_status(tmp_path, CONTROLLER, "--format", "json").stdout)

    assert payload["documents"][0]["broken_links"] == []


def test_external_links_are_not_checked(tmp_path: Path) -> None:
    """Внешний адрес и якорь не про файловую систему."""
    _materialize(tmp_path)
    _fill(
        tmp_path,
        CONTROLLER,
        "purpose",
        "[внешняя](https://example.com/x) и [якорь](#назначение).",
    )

    payload = json.loads(_status(tmp_path, CONTROLLER, "--format", "json").stdout)

    assert payload["documents"][0]["broken_links"] == []


def test_generated_block_links_are_not_checked(tmp_path: Path) -> None:
    """В генерируемом блоке ссылки на исходники ведут наружу дерева документации
    и в тестовом окружении не существуют — сообщать о них нечего."""
    _materialize(tmp_path)

    payload = json.loads(_status(tmp_path, "--format", "json").stdout)

    assert all(doc["broken_links"] == [] for doc in payload["documents"])


# --------------------------------------------------------------------------------------
# Частичный манифест
# --------------------------------------------------------------------------------------


def test_partial_manifest_is_flagged(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["partial"] = {"scope": ["src/Sample.Pricing.Api"], "outside_from_cache": True}
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _materialize(tmp_path, partial)
    text = _status(tmp_path, manifest=partial)
    machine = json.loads(_status(tmp_path, "--format", "json", manifest=partial).stdout)

    assert "манифест частичный" in text.stdout
    assert machine["manifest_partial"] is True


# --------------------------------------------------------------------------------------
# Полный цикл статусов
# --------------------------------------------------------------------------------------


def test_current_is_only_in_the_counter(tmp_path: Path) -> None:
    """На АС CF подробный список по всем документам — тысячи строк, и в них
    теряется то немногое, ради чего команду и звали."""
    _materialize(tmp_path)
    node = json.loads(MANIFEST.read_text(encoding="utf-8"))["nodes"]
    controller = next(n for n in node if n["doc_path"] == CONTROLLER)
    for name in ("purpose", "api", "behaviour", "collaboration", "notes"):
        _fill(tmp_path, CONTROLLER, name, "Текст.")

    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "docpipe_state:\n  accepted: null\n  review: null\n",
            "docpipe_state:\n"
            "  accepted:\n"
            f"    signature_hash: {controller['signature_hash']}\n"
            f"    impl_hash: {controller['impl_hash']}\n"
            f"    kind: {controller['kind']}\n"
            "    members: []\n"
            "  review: null\n",
        ),
        encoding="utf-8",
    )

    result = _status(tmp_path)

    assert "current          1" in result.stdout
    assert CONTROLLER not in result.stdout


# --------------------------------------------------------------------------------------
# Проверка `--team`: раньше её делал только `materialize`
# --------------------------------------------------------------------------------------


def test_unknown_team_is_rejected_not_silently_empty(tmp_path: Path) -> None:
    """Опечатка в `--team` обязана называться опечаткой.

    Без проверки выборка сужалась до пустой, и команда отвечала «документов
    нет» — то есть неотличимо от честного «у этой команды документов нет».
    """
    _materialize(tmp_path)
    result = _status(tmp_path, "--team", "no-such-team")

    assert result.exit_code == 2
    assert "Неизвестные команды" in result.stderr


def test_unknown_team_is_rejected_by_worklist(tmp_path: Path) -> None:
    _materialize(tmp_path)
    result = runner.invoke(
        app,
        [
            "worklist",
            str(MANIFEST),
            "--root",
            str(tmp_path),
            "--out",
            str(tmp_path / "queue.json"),
            "--team",
            "no-such-team",
        ],
    )

    assert result.exit_code == 2
    assert not (tmp_path / "queue.json").exists()


# --------------------------------------------------------------------------------------
# Действие с файлом
# --------------------------------------------------------------------------------------
#
# `status` описывает содержимое документа, `file_action` — что прогон сделает
# с файлом. Раньше наружу выходило только первое, и вопрос «что именно
# перепишут» ответа не имел ни в одном отчёте.


def _with_domain(tmp_path: Path, domain: str) -> Path:
    """Тот же манифест с другим доменом: поле проекции, документ не меняется."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        node["domain"] = domain
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return changed


def test_file_action_is_shown_as_a_column(tmp_path: Path) -> None:
    _materialize(tmp_path)

    out = _status(tmp_path).stdout

    assert "unchanged" in out
    assert f"empty       unchanged  {CONTROLLER}" in out


def test_file_action_filters_the_selection(tmp_path: Path) -> None:
    _materialize(tmp_path)
    changed = _with_domain(tmp_path, "Другой домен")

    updated = _status(tmp_path, "--file-action", "update", manifest=changed)
    untouched = _status(tmp_path, "--file-action", "unchanged", manifest=changed)

    assert updated.exit_code == 0
    assert updated.stdout.count("update") >= 6
    assert "всего            6" in updated.stdout
    assert "всего            0" in untouched.stdout


def test_unknown_file_action_is_rejected(tmp_path: Path) -> None:
    """Опечатка в значении иначе дала бы пустую выборку, неотличимую от честной."""
    result = _status(tmp_path, "--file-action", "updated")

    assert result.exit_code == 2
    assert "неизвестные значения updated" in result.stderr


def test_current_document_stays_visible_when_it_is_rewritten(tmp_path: Path) -> None:
    """Документ в порядке, а файл перепишут — раньше об этом не было ни строки.

    Смена домена меняет проекцию: статус остаётся `current`, `file_action`
    становится `update`, и подробный список показывал только первое.
    """
    _materialize(tmp_path)
    for name in ("purpose", "api", "behaviour", "collaboration", "notes"):
        _fill(tmp_path, CONTROLLER, name, "Написано человеком.")
    runner.invoke(app, ["docs", "accept", str(MANIFEST), CONTROLLER, "--root", str(tmp_path)])

    # Принятый и не переписываемый документ в подробностях не показывается:
    # ради этого `current` из них и исключён.
    assert CONTROLLER not in _status(tmp_path).stdout

    changed = _with_domain(tmp_path, "Другой домен")
    loud = _status(tmp_path, manifest=changed).stdout

    assert f"current     update     {CONTROLLER}" in loud
