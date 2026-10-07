"""Закоммиченные схемы `schema/*.json` совпадают с генерацией из моделей (S06).

Схема — производная от моделей, но лежит в git отдельным файлом, и правка
модели без `docpipe schema` его не трогает. Так `doc-tree.schema.json` однажды
отстал на шесть полей, и никто не заметил: схему читают снаружи, а тесты
смотрели только на модели.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import SCHEMA_MODELS, app
from docpipe.hashing import stable_json_dumps

runner = CliRunner()


@pytest.mark.parametrize("name", sorted(SCHEMA_MODELS))
def test_committed_schema_matches_the_model(name: str) -> None:
    model, committed = SCHEMA_MODELS[name]

    generated = stable_json_dumps(model.model_json_schema())

    assert Path(committed).read_text(encoding="utf-8") == generated, (
        f"{committed} отстал от модели: uv run docpipe schema --model {name}"
    )


@pytest.mark.parametrize("name", sorted(SCHEMA_MODELS))
def test_command_writes_what_the_test_compares(name: str, tmp_path: Path) -> None:
    """Сверка выше осмысленна, только если команда пишет ровно эту строку:
    иначе тест зеленел бы при схеме, которую `docpipe schema` не воспроизводит."""
    model, _ = SCHEMA_MODELS[name]
    out = tmp_path / "schema.json"

    result = runner.invoke(app, ["schema", "--model", name, "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == stable_json_dumps(model.model_json_schema())


def test_every_committed_schema_has_a_model() -> None:
    """Схема без модели не сверяется ничем и отстаёт первой."""
    committed = {Path(path).as_posix() for _, path in SCHEMA_MODELS.values()}
    on_disk = {path.as_posix() for path in sorted(Path("schema").glob("*.json"))}

    assert on_disk == committed
