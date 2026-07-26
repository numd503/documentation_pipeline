"""Точка входа командной строки."""

from pathlib import Path
from typing import Annotated

import typer

from docpipe import __version__
from docpipe.classify import load_ruleset
from docpipe.config import load_config
from docpipe.emit import run_meta_path, write_manifest, write_run_meta
from docpipe.emit import scan as run_scan
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


@app.command()
def scan(
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория с исходниками.")],
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    rules: Annotated[
        Path | None, typer.Option("--rules", help="Набор правил классификации.")
    ] = None,
    out: Annotated[Path, typer.Option("--out", help="Куда записать манифест.")] = Path(
        "artifacts/doc-tree.json"
    ),
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Не использовать кэш разобранных файлов.")
    ] = False,
    jobs: Annotated[int, typer.Option("--jobs", help="Число процессов для разбора.")] = 1,
) -> None:
    """Построить дерево документации по исходникам .NET.

    Пишет два файла: детерминированный манифест и сидкар `<out>.run.json`
    с метаданными прогона. Всё недетерминированное — только в сидкаре.
    """
    if not root.is_dir():
        raise typer.BadParameter(f"каталог не найден: {root}", param_hint="--root")

    # Ошибки конфигурации — это опечатка пользователя, а не сбой программы.
    # Traceback на полстраницы вместо строчки «файл не найден» ровно в тот
    # момент, когда человек первый раз пробует команду руками, — плохой обмен.
    try:
        settings = load_config(config)
        ruleset = load_ruleset(rules or Path(settings.rules))
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    cache_dir = None if no_cache else root / settings.cache_dir

    manifest, meta = run_scan(root, settings, ruleset, cache_dir, jobs)
    write_manifest(manifest, out)
    write_run_meta(meta, out)

    typer.echo(
        f"Модулей: {len(manifest.modules)}, узлов: {len(manifest.nodes)}. "
        f"Записано: {out} и {run_meta_path(out)}"
    )
    if meta.parse_error_files:
        typer.echo(
            f"Внимание: {len(meta.parse_error_files)} файлов разобраны с ошибками "
            "и не дали ни одного типа — см. parse_error_files в сидкаре."
        )


if __name__ == "__main__":
    app()
