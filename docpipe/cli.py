"""Точка входа командной строки."""

from pathlib import Path
from typing import Annotated

import typer

from docpipe import __version__
from docpipe.hashing import stable_json_dumps
from docpipe.model import Manifest

app = typer.Typer(
    name="docpipe",
    help="Построение структуры документации по исходному коду .NET.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Построение структуры документации по исходному коду .NET.

    Callback нужен, чтобы typer не схлопывал единственную команду в корневую:
    без него `docpipe version` разбирается как вызов корня с лишним аргументом.
    """


@app.command()
def version() -> None:
    """Показать версию docpipe."""
    typer.echo(__version__)


@app.command()
def schema(
    out: Annotated[
        Path,
        typer.Option("--out", help="Куда записать JSON Schema манифеста."),
    ] = Path("schema/doc-tree.schema.json"),
) -> None:
    """Сгенерировать JSON Schema манифеста из моделей.

    Схема — производная от `model.py`, а не отдельно поддерживаемый файл.
    Расхождение между ними невозможно по построению.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(stable_json_dumps(Manifest.model_json_schema()), encoding="utf-8")
    typer.echo(f"Схема записана: {out}")


if __name__ == "__main__":
    app()
