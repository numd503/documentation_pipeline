"""Точка входа командной строки."""

from pathlib import Path
from typing import Annotated

import typer

from docpipe import __version__
from docpipe.classify import load_ruleset
from docpipe.config import load_config
from docpipe.diff import diff_manifests, format_changes
from docpipe.emit import run as run_scan
from docpipe.emit import run_meta_path, write_manifest, write_run_meta
from docpipe.hashing import stable_json_dumps
from docpipe.model import Manifest, RunMeta
from docpipe.stats import (
    format_breakdown,
    format_stats,
    stats_from_manifest,
    validate_manifest,
)

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
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Куда записать манифест. Без флага — `out` из конфигурации."),
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Не использовать кэш разобранных файлов.")
    ] = False,
    jobs: Annotated[int, typer.Option("--jobs", help="Число процессов для разбора.")] = 1,
    scope: Annotated[
        list[str] | None,
        typer.Option("--scope", help="Перестроить только эти каталоги. Можно повторять."),
    ] = None,
    from_manifest: Annotated[
        Path | None,
        typer.Option("--from-manifest", help="Манифест, из которого брать узлы вне скоупа."),
    ] = None,
    show_stats: Annotated[
        bool,
        typer.Option("--stats", help="Показать счётчики и подсказки по правилам, не писать файлы."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Показать дифф против существующего --out, не писать."),
    ] = False,
) -> None:
    """Построить дерево документации по исходникам .NET.

    Пишет два файла: детерминированный манифест и сидкар `<out>.run.json`
    с метаданными прогона. Всё недетерминированное — только в сидкаре.
    """
    if not root.is_dir():
        raise typer.BadParameter(f"каталог не найден: {root}", param_hint="--root")

    # Скоуп-прогон переносит узлы вне скоупа из предыдущего манифеста. Без него
    # получился бы не частичный, а урезанный манифест — и молча.
    if scope and from_manifest is None:
        typer.echo("--scope требует --from-manifest", err=True)
        raise typer.Exit(code=2)

    # Ошибки конфигурации — это опечатка пользователя, а не сбой программы.
    # Traceback на полстраницы вместо строчки «файл не найден» ровно в тот
    # момент, когда человек первый раз пробует команду руками, — плохой обмен.
    try:
        settings = load_config(config)
        ruleset = load_ruleset(rules or Path(settings.rules))
        previous = (
            Manifest.model_validate_json(from_manifest.read_text(encoding="utf-8"))
            if from_manifest
            else None
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    cache_dir = None if no_cache else root / settings.cache_dir

    # Поле `out` в конфигурации до этого не читалось: путь всегда брался из флага,
    # у которого было значение по умолчанию. Конфигурация с прописанным `out`
    # выглядела применённой, а манифест уезжал в `artifacts/` рядом с текущим
    # каталогом. Флаг по-прежнему важнее — но только когда он действительно задан.
    destination = out or Path(settings.out)

    result = run_scan(root, settings, ruleset, cache_dir, jobs, scope or None, previous)
    manifest, meta = result.manifest, result.meta

    # `--stats` и `--dry-run` ничего не пишут: их зовут в цикле настройки правил,
    # где перезаписывать манифест на каждой итерации незачем.
    if show_stats:
        typer.echo(format_stats(result.stats))
        typer.echo(format_breakdown(result.stats))
        return

    if dry_run:
        existing = (
            Manifest.model_validate_json(destination.read_text(encoding="utf-8"))
            if destination.is_file()
            else Manifest(ruleset_version=manifest.ruleset_version, parser=manifest.parser)
        )
        typer.echo(format_changes(diff_manifests(existing, manifest)))
        return

    write_manifest(manifest, destination)
    write_run_meta(meta, destination)

    typer.echo(
        f"Модулей: {len(manifest.modules)}, узлов: {len(manifest.nodes)}. "
        f"Записано: {destination} и {run_meta_path(destination)}"
    )
    if manifest.partial is not None:
        typer.echo(
            f"Частичный прогон по {', '.join(manifest.partial.scope)}: "
            f"вне скоупа данные взяты из кэша и предыдущего манифеста."
        )
    if meta.stats.get("missing_from_cache"):
        typer.echo(
            f"Внимание: {meta.stats['missing_from_cache']} файлов вне скоупа "
            "отсутствуют в кэше — граф наследования может быть неполным.",
            err=True,
        )
    if meta.parse_error_files:
        typer.echo(
            f"Внимание: {len(meta.parse_error_files)} файлов разобраны с ошибками "
            "и не дали ни одного типа — см. parse_error_files в сидкаре."
        )


@app.command()
def diff(
    old: Annotated[Path, typer.Argument(help="Манифест «до».")],
    new: Annotated[Path, typer.Argument(help="Манифест «после».")],
    output_format: Annotated[
        str, typer.Option("--format", help="Формат вывода: text или json.")
    ] = "text",
) -> None:
    """Показать, что изменилось между двумя манифестами.

    Наличие изменений — не ошибка, поэтому код возврата всегда 0: команда
    предназначена для конвейера, где по её выводу решают, что перегенерировать.
    """
    if output_format not in ("text", "json"):
        raise typer.BadParameter("допустимо text или json", param_hint="--format")

    try:
        before = Manifest.model_validate_json(old.read_text(encoding="utf-8"))
        after = Manifest.model_validate_json(new.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    changes = diff_manifests(before, after)
    if output_format == "json":
        typer.echo(stable_json_dumps([change.model_dump(mode="json") for change in changes]))
    else:
        typer.echo(format_changes(changes))


@app.command()
def validate(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест для проверки.")],
) -> None:
    """Проверить манифест: схему и инварианты, которые она не покрывает.

    Каждый инвариант однажды нарушался на реальном коде и приводил к молчаливой
    потере документов, поэтому проверка отдельная, а не «оно же по схеме валидно».
    """
    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Манифест не прошёл проверку схемой: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Список файлов со сломанным разбором живёт в сидкаре: в манифесте ему
    # не место, он про прогон, а не про результат.
    sidecar = run_meta_path(manifest_path)
    parse_error_files: list[str] = []
    if sidecar.is_file():
        parse_error_files = RunMeta.model_validate_json(
            sidecar.read_text(encoding="utf-8")
        ).parse_error_files

    errors, warnings = validate_manifest(manifest, parse_error_files)

    for warning in warnings:
        typer.echo(f"Предупреждение: {warning}", err=True)
    for error in errors:
        typer.echo(f"Ошибка: {error}", err=True)

    if errors:
        raise typer.Exit(code=1)
    typer.echo(f"Манифест корректен: {len(manifest.modules)} модулей, {len(manifest.nodes)} узлов.")


@app.command()
def stats(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест.")],
) -> None:
    """Показать состав готового манифеста.

    `unclassified` здесь быть не может: в манифест попадают только узлы.
    Чтобы увидеть непокрытое, нужен `scan --stats` — он держит индекс символов.
    """
    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_stats(stats_from_manifest(manifest), total_label="total nodes"))


if __name__ == "__main__":
    app()
