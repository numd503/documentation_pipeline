"""Автоперенос документов при переезде узла (M08).

Переезд — это когда изменился **код** и пересчитался `doc_path`, а файл с текстом
остался на старом месте. Файл никто не переносил.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from docpipe.cli import app

GOLDEN_MANIFEST = Path("tests/golden/doc-tree.json")
CONTROLLER = "docs/modules/Sample.Pricing.Api/controllers/pricing-controller.md"
runner = CliRunner()


def _run(root: Path, manifest: Path = GOLDEN_MANIFEST, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["materialize", str(manifest), "--root", str(root), *extra])


def _fill(root: Path, doc_path: str) -> None:
    path = root / doc_path
    text = path.read_text(encoding="utf-8")
    for name in ("purpose", "api", "behaviour", "collaboration", "notes"):
        text = text.replace(
            f"<!-- docpipe:section:end {name} -->",
            f"Авторский текст {name}.\n<!-- docpipe:section:end {name} -->",
        )
    path.write_text(text, encoding="utf-8")


def _manifest(tmp_path: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    payload = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    mutate(payload)
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


NEW_PATH = "docs/modules/Sample.Pricing.Api/services/pricing-controller.md"


def _reclassified(payload: dict) -> None:  # type: ignore[type-arg]
    """Правило переклассифицировало тип: `doc_path` изменился, `node_id` — нет."""
    for node in payload["nodes"]:
        if node["doc_path"] == CONTROLLER:
            node["doc_path"] = NEW_PATH
            node["kind"] = "service"
            node["template"] = "service"


def test_reclassification_moves_the_document(tmp_path: Path) -> None:
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)

    result = _run(tmp_path, _manifest(tmp_path, _reclassified))
    text = (tmp_path / NEW_PATH).read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert not (tmp_path / CONTROLLER).exists()
    assert "Авторский текст purpose." in text


def test_relocated_document_is_marked_for_review(tmp_path: Path) -> None:
    """Отметка снимается приёмкой; до неё документ виден в отчётах."""
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)

    _run(tmp_path, _manifest(tmp_path, _reclassified))
    text = (tmp_path / NEW_PATH).read_text(encoding="utf-8")

    assert "reason: relocated" in text
    assert f"from: {CONTROLLER}" in text


def test_relocated_document_gets_correct_front_matter(tmp_path: Path) -> None:
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)

    _run(tmp_path, _manifest(tmp_path, _reclassified))
    text = (tmp_path / NEW_PATH).read_text(encoding="utf-8")

    assert f"doc_path: {NEW_PATH}" in text
    assert "kind: service" in text
    assert "template_ref: templates/service.md" in text


def test_sections_of_the_new_template_are_appended(tmp_path: Path) -> None:
    """Смена шаблона `controller` → `service` меняет состав секций.

    Секции старого шаблона не удаляются: в них авторский текст. Секции нового
    дописываются в конец, и про осиротевшие сообщается в генерируемом блоке.
    """
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)

    _run(tmp_path, _manifest(tmp_path, _reclassified))
    text = (tmp_path / NEW_PATH).read_text(encoding="utf-8")

    assert "Авторский текст api." in text
    assert "docpipe:section:start responsibilities" in text
    assert "Секции не из текущего шаблона `service`" in text
    assert "`api`" in text


def test_second_run_after_relocation_is_stable(tmp_path: Path) -> None:
    """Идемпотентность формулируется как сходимость: после переноса повторный
    прогон не должен ничего менять."""
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)
    changed = _manifest(tmp_path, _reclassified)
    _run(tmp_path, changed)
    before = (tmp_path / NEW_PATH).read_bytes()

    result = _run(tmp_path, changed)

    assert result.exit_code == 0
    assert (tmp_path / NEW_PATH).read_bytes() == before


def test_rename_with_the_same_file_moves_with_high_confidence(tmp_path: Path) -> None:
    """Тип переименован: и `doc_path`, и `node_id` другие, но файл `.cs` тот же."""
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)

    def rename(payload: dict) -> None:  # type: ignore[type-arg]
        for node in payload["nodes"]:
            if node["doc_path"] == CONTROLLER:
                node["doc_path"] = "docs/modules/Sample.Pricing.Api/controllers/pricing-api.md"
                node["id"] = node["id"].replace("PricingController", "PricingApi")
                node["title"] = "PricingApi"
                node["symbol"]["fqn"] = node["symbol"]["fqn"].replace(
                    "PricingController", "PricingApi"
                )

    result = _run(tmp_path, _manifest(tmp_path, rename))
    moved = tmp_path / "docs/modules/Sample.Pricing.Api/controllers/pricing-api.md"

    assert result.exit_code == 0
    assert not (tmp_path / CONTROLLER).exists()
    assert "Авторский текст purpose." in moved.read_text(encoding="utf-8")


def test_ambiguous_pair_is_not_moved(tmp_path: Path) -> None:
    """Требование «пара единственная в обе стороны» снимает главный риск —
    два типа, обменявшихся именами."""
    _run(tmp_path)
    copy = tmp_path / "docs/modules/Sample.Pricing.Api/controllers/copy.md"
    original = (tmp_path / CONTROLLER).read_text(encoding="utf-8")
    copy.write_text(original.replace("PricingController`0", "PricingCopy`0"), encoding="utf-8")

    def rename(payload: dict) -> None:  # type: ignore[type-arg]
        for node in payload["nodes"]:
            if node["doc_path"] == CONTROLLER:
                node["doc_path"] = "docs/modules/Sample.Pricing.Api/controllers/pricing-api.md"
                node["id"] = node["id"].replace("PricingController", "PricingApi")

    result = _run(tmp_path, _manifest(tmp_path, rename))

    # Не сбой прогона: документ просто остался на месте, о чём и сказано.
    # Дальше он виден как сирота и переносится командой `docs adopt`.
    assert result.exit_code == 0
    assert "кандидатов на перенос несколько" in result.stdout
    assert (tmp_path / CONTROLLER).exists()
    assert copy.exists()


def test_dry_run_does_not_move(tmp_path: Path) -> None:
    _run(tmp_path)
    _fill(tmp_path, CONTROLLER)

    result = _run(tmp_path, _manifest(tmp_path, _reclassified), "--dry-run")

    assert result.exit_code == 0
    assert (tmp_path / CONTROLLER).exists()
    assert not (tmp_path / NEW_PATH).exists()
