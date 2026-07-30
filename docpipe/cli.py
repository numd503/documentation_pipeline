"""Точка входа командной строки."""

from collections import Counter
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
from docpipe.materialize.apply import apply_plan, format_result
from docpipe.materialize.build import build_context
from docpipe.materialize.ownership import (
    Ownership,
    explain,
    lint,
    load_ownership,
    owner_of,
)
from docpipe.materialize.plan import (
    ExistingDoc,
    MaterializePlan,
    PlannedDoc,
    PlanOptions,
    build_plan,
    check_links,
    scan_docs,
    with_links,
)
from docpipe.materialize.status import (
    AGENT_ACTIONS,
    STATUSES,
    filter_documents,
    format_status,
    format_status_json,
)
from docpipe.materialize.template import load_templates
from docpipe.model import Manifest, RunMeta
from docpipe.registry import load_registries, read_registry
from docpipe.registry.anchors import (
    ResolvedAnchor,
    counts,
    filter_anchors,
    format_anchors,
    format_explain,
    resolve_anchors,
)
from docpipe.stats import (
    UNDECIDED,
    Stats,
    format_kinds,
    format_report,
    plural,
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
    fail_on_undecided: Annotated[
        bool,
        typer.Option(
            "--fail-on-undecided",
            help="Код 1, если про какой-то символ решение не принято. Для CI.",
        ),
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
        typer.echo(format_report(result.stats))
        _check_undecided(result.stats, fail_on_undecided)
        return

    if dry_run:
        existing = (
            Manifest.model_validate_json(destination.read_text(encoding="utf-8"))
            if destination.is_file()
            else Manifest(ruleset_version=manifest.ruleset_version, parser=manifest.parser)
        )
        typer.echo(format_changes(diff_manifests(existing, manifest)))
        _check_undecided(result.stats, fail_on_undecided)
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
    _check_undecided(result.stats, fail_on_undecided)


def _check_undecided(stats: Stats, fail: bool) -> None:
    """Отказ, если про какой-то символ решение не принято.

    Смысл флага — в том, что проверка становится возможной вообще. Пока
    «не решено» было счётчиком «неклассифицировано», он был большим всегда,
    и появление в репозитории нового типа в нём не выделялось. Доведённое
    до нуля «не решено» превращает такой тип в упавшую сборку.
    """
    undecided = stats.counts.get(UNDECIDED, 0)
    if not fail or not undecided:
        return

    typer.echo(
        f"Решение не принято по {plural(undecided, 'символу', 'символам', 'символам')}. "
        "Каждый обязан быть либо классифицирован правилом, либо отсеян правилом "
        "exclude с указанной причиной. Что именно осталось — покажет `scan --stats`.",
        err=True,
    )
    raise typer.Exit(code=1)


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

    Состояния решений здесь быть не может: в манифест попадают только узлы,
    то есть то, про что решено «документируем». Чтобы увидеть нерешённое, нужен
    `scan --stats` — он держит индекс символов целиком.
    """
    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_kinds(stats_from_manifest(manifest), total_label="total nodes"))


@app.command()
def materialize(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория с документацией.")
    ] = Path("."),
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    templates_dir: Annotated[
        Path | None, typer.Option("--templates", help="Каталог шаблонов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    team: Annotated[
        list[str] | None, typer.Option("--team", help="Только узлы этих команд; можно повторять.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Показать план, ничего не писать.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Пересоздать испорченные документы.")
    ] = False,
) -> None:
    """Создать или обновить документы по манифесту.

    Две фазы строго: план → проверка → запись. При блокирующей ошибке
    не записывается ничего: половина обновлённого дерева хуже необновлённого,
    потому что о ней никто не узнает.
    """
    settings = load_config(config)
    templates_path = templates_dir or Path(settings.templates)
    ownership_path = ownership_file or (Path(settings.ownership) if settings.ownership else None)

    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        templates = load_templates(templates_path)
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=2) from exc

    if team:
        known = {item.id for item in ownership.teams} if ownership else set()
        unknown = sorted(set(team) - known)
        if unknown:
            listing = ", ".join(sorted(known)) or "(правила владения не заданы)"
            typer.echo(f"Неизвестные команды: {', '.join(unknown)}; известны: {listing}", err=True)
            raise typer.Exit(code=2)

    examples = frozenset(path.stem for path in (templates_path / "examples").glob("*.md"))
    context = build_context(manifest, templates, examples, templates_path.as_posix())
    existing = scan_docs(root, settings.docs_root, settings.docs_scan_exclude)
    plan = build_plan(
        manifest,
        existing,
        templates,
        context,
        ownership,
        PlanOptions(docs_root=settings.docs_root, teams=tuple(team or ()), force=force),
    )

    result = apply_plan(plan, root, dry_run=dry_run, force=force)
    typer.echo(format_result(plan, result, dry_run))

    if plan.errors or result.errors or result.refused:
        raise typer.Exit(code=1)


docs_app = typer.Typer(
    help="Состояние дерева документации.",
    no_args_is_help=True,
)
app.add_typer(docs_app, name="docs")


@docs_app.command("status")
def docs_status(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="Файлы или каталоги. Без них — всё дерево."),
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    templates_dir: Annotated[
        Path | None, typer.Option("--templates", help="Каталог шаблонов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    team: Annotated[
        list[str] | None, typer.Option("--team", help="Только эти команды; можно повторять.")
    ] = None,
    action: Annotated[
        list[str] | None,
        typer.Option("--action", help="Только эти решения: write, review, skip."),
    ] = None,
    fail_on: Annotated[
        list[str] | None,
        typer.Option("--fail-on", help="Код 1 при встрече статуса; можно повторять."),
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="text или json.")] = "text",
) -> None:
    """Что делать с каждым документом. Ничего не пишет.

    Соблазн «заодно починить front matter» превращает информационную команду
    в изменяющую, и её перестают гонять в CI.
    """
    for name, values, allowed in (
        ("--action", action, AGENT_ACTIONS),
        ("--fail-on", fail_on, STATUSES),
    ):
        unknown = sorted(set(values or ()) - allowed)
        if unknown:
            # Опечатка `--fail-on statle` иначе дала бы вечно зелёную проверку.
            typer.echo(
                f"{name}: неизвестные значения {', '.join(unknown)};"
                f" известны: {', '.join(sorted(allowed))}",
                err=True,
            )
            raise typer.Exit(code=2)

    settings = load_config(config)
    templates_path = templates_dir or Path(settings.templates)
    ownership_path = ownership_file or (Path(settings.ownership) if settings.ownership else None)

    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        templates = load_templates(templates_path)
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=2) from exc

    examples = frozenset(path.stem for path in (templates_path / "examples").glob("*.md"))
    context = build_context(manifest, templates, examples, templates_path.as_posix())
    existing = scan_docs(root, settings.docs_root, settings.docs_scan_exclude)
    plan = with_links(
        build_plan(
            manifest,
            existing,
            templates,
            context,
            ownership,
            PlanOptions(docs_root=settings.docs_root, teams=tuple(team or ())),
        ),
        check_links(existing, root),
    )

    selected = filter_documents(plan.documents, paths or [], action or [])
    typer.echo(
        format_status_json(plan, selected)
        if output_format == "json"
        else format_status(plan, selected)
    )

    if plan.errors or (fail_on and {doc.status for doc in selected} & set(fail_on)):
        raise typer.Exit(code=1)


def _prepare(
    manifest_path: Path,
    root: Path,
    config: Path | None,
    templates_dir: Path | None,
    ownership_file: Path | None,
    teams: tuple[str, ...] = (),
    accept: tuple[str, ...] = (),
) -> tuple[Manifest, Ownership | None, list[ExistingDoc], MaterializePlan]:
    """Общая подготовка команд шага 2: манифест, шаблоны, владение, план."""
    settings = load_config(config)
    templates_path = templates_dir or Path(settings.templates)
    ownership_path = ownership_file or (Path(settings.ownership) if settings.ownership else None)

    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        templates = load_templates(templates_path)
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=2) from exc

    examples = frozenset(path.stem for path in (templates_path / "examples").glob("*.md"))
    context = build_context(manifest, templates, examples, templates_path.as_posix())
    existing = scan_docs(root, settings.docs_root, settings.docs_scan_exclude)
    plan = build_plan(
        manifest,
        existing,
        templates,
        context,
        ownership,
        PlanOptions(docs_root=settings.docs_root, teams=teams, accept=accept),
    )
    return manifest, ownership, existing, plan


@docs_app.command("accept")
def docs_accept(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    paths: Annotated[list[Path] | None, typer.Argument(help="Файлы или каталоги.")] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    templates_dir: Annotated[
        Path | None, typer.Option("--templates", help="Каталог шаблонов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    node: Annotated[
        list[str] | None, typer.Option("--node", help="Идентификатор узла; можно повторять.")
    ] = None,
    team: Annotated[list[str] | None, typer.Option("--team", help="Только эти команды.")] = None,
    accept_all: Annotated[bool, typer.Option("--all", help="Все документы дерева.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Принять и пустые документы.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Показать, не писать.")] = False,
) -> None:
    """Зафиксировать соответствие документа коду.

    Единственный писатель принятого состояния. Хэши берутся из манифеста,
    а не из front matter документа: тот — зеркало и мог отстать.
    """
    if not (paths or node or team or accept_all):
        typer.echo("Нужен хотя бы один отбор: пути, --node, --team или --all", err=True)
        raise typer.Exit(code=2)

    _, _, _, plan = _prepare(
        manifest_path, root, config, templates_dir, ownership_file, tuple(team or ())
    )
    if plan.errors:
        typer.echo(format_status(plan, plan.documents))
        raise typer.Exit(code=1)

    selected = filter_documents(plan.documents, paths or [], [])
    if node:
        selected = [doc for doc in selected if doc.node_id in set(node)]

    def _refusal(doc: PlannedDoc) -> str | None:
        if doc.status == "broken":
            return "структура документа испорчена"
        if doc.node_id is None:
            return "узла для документа нет"
        if doc.status == "empty" and not force:
            return "документ не наполнен, нужен --force"
        return None

    refused = [
        f"{doc.doc_path}: {reason}" for doc in selected if (reason := _refusal(doc)) is not None
    ]
    if refused:
        typer.echo("\n".join(["Приёмка отклонена:", *(f"  {line}" for line in refused)]), err=True)
        raise typer.Exit(code=1)

    targets = tuple(doc.doc_path for doc in selected)
    if not targets:
        typer.echo("Под отбор не попал ни один документ.")
        return

    _, _, _, accepted = _prepare(
        manifest_path,
        root,
        config,
        templates_dir,
        ownership_file,
        tuple(team or ()),
        accept=targets,
    )
    chosen = [doc for doc in accepted.documents if doc.doc_path in set(targets)]
    result = apply_plan(
        MaterializePlan(documents=chosen, manifest_partial=accepted.manifest_partial),
        root,
        dry_run=dry_run,
    )

    typer.echo(f"Принято: {len(targets)}{' (ничего не записано)' if dry_run else ''}")
    if result.errors:
        typer.echo("\n".join(["Ошибки записи:", *(f"  {e}" for e in result.errors)]), err=True)
        raise typer.Exit(code=1)


@docs_app.command("adopt")
def docs_adopt(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    from_path: Annotated[str, typer.Option("--from", help="Откуда переносить.")],
    to_path: Annotated[str, typer.Option("--to", help="Куда переносить.")],
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    templates_dir: Annotated[
        Path | None, typer.Option("--templates", help="Каталог шаблонов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Показать, не писать.")] = False,
) -> None:
    """Перенести документ вручную.

    Нужен там, где автоперенос отказался: кандидатов оказалось несколько,
    и выбор за человеком. Отметку о пересмотре ставить не требуется — документ
    писался для другого типа, и обычная логика статусов пометит его сама.
    """
    manifest, _, existing, plan = _prepare(
        manifest_path, root, config, templates_dir, ownership_file
    )
    source = root / from_path
    target = root / to_path

    problems: list[str] = []
    if not source.is_file():
        problems.append(f"{from_path}: файла нет")
    if target.exists():
        problems.append(f"{to_path}: цель занята")
    if to_path not in {node.doc_path for node in manifest.nodes}:
        problems.append(f"{to_path}: ни один узел манифеста не претендует на этот путь")

    moving = next((doc for doc in existing if doc.path == from_path), None)
    if moving is not None and moving.node_id:
        others = [
            doc.path for doc in existing if doc.node_id == moving.node_id and doc.path != from_path
        ]
        if others:
            problems.append(
                f"{from_path}: узел {moving.node_id} уже описан в {', '.join(sorted(others))}"
            )

    if problems:
        typer.echo("\n".join(["Перенос отклонён:", *(f"  {p}" for p in problems)]), err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo(f"Было бы перенесено: {from_path} → {to_path}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(target)
    except OSError as exc:
        typer.echo(f"Перенос не удался: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Пересборка обычным прогоном: документ на новом месте получает свой
    # front matter, а авторский текст остаётся дословно.
    _, _, _, rebuilt = _prepare(manifest_path, root, config, templates_dir, ownership_file)
    apply_plan(rebuilt, root)
    typer.echo(f"Перенесено: {from_path} → {to_path}")


@docs_app.command("owners")
def docs_owners(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    explain_node: Annotated[
        str | None, typer.Option("--explain", help="Идентификатор узла или путь документа.")
    ] = None,
    run_lint: Annotated[bool, typer.Option("--lint", help="Диагностика набора правил.")] = False,
) -> None:
    """Кто владеет документами и что не так с правилами владения."""
    settings = load_config(config)
    path = ownership_file or (Path(settings.ownership) if settings.ownership else None)
    if path is None:
        typer.echo("Правила владения не заданы: --ownership или ключ `ownership`", err=True)
        raise typer.Exit(code=2)

    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        ownership = load_ownership(path)
    except (OSError, ValueError) as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=2) from exc

    if explain_node:
        node = next(
            (n for n in manifest.nodes if explain_node in (n.id, n.doc_path)),
            None,
        )
        if node is None:
            typer.echo(f"Узел не найден: {explain_node}", err=True)
            raise typer.Exit(code=1)
        typer.echo(explain(node, ownership))
        return

    if run_lint:
        findings, warnings = lint(manifest.nodes, ownership)
        for line in findings:
            typer.echo(line)
        for line in warnings:
            typer.echo(line, err=True)
        if findings or warnings:
            raise typer.Exit(code=1)
        typer.echo("Правила владения в порядке.")
        return

    counted = Counter(owner_of(node, ownership).team or "(не задан)" for node in manifest.nodes)
    width = max(len(name) for name in counted)
    typer.echo("\n".join(f"{name:<{width}}  {count:>5}" for name, count in sorted(counted.items())))


anchors_app = typer.Typer(
    help="Точки входа, объявленные в реестрах платформы.",
    no_args_is_help=True,
)
app.add_typer(anchors_app, name="anchors")


def _load_anchors(
    manifest_path: Path, registries: Path, root: Path
) -> tuple[list[ResolvedAnchor], list[str]]:
    """Прочитать манифест и реестры. Ошибки чтения — код 2, замечания — в отчёт."""
    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        specs = load_registries(registries)
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать описание реестров: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    results = [read_registry(spec, root) for spec in specs]
    errors = [error for result in results for error in result.errors]
    return resolve_anchors(results, manifest), errors


@anchors_app.command("list")
def anchors_list(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    registries: Annotated[Path, typer.Option("--registries", help="Описание реестров.")],
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: пути реестров от него.")
    ] = Path("."),
    kind: Annotated[
        list[str] | None, typer.Option("--kind", help="Только эти виды; можно повторять.")
    ] = None,
    team: Annotated[
        list[str] | None, typer.Option("--team", help="Только эти команды; можно повторять.")
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="text или json.")] = "text",
) -> None:
    """Инвентаризация точек входа.

    Неразрешённые ссылки на типы — находка, а не ошибка: код возврата остаётся
    нулевым. Узлами документации становятся только enrolled и классифицированные
    типы, поэтому «не найден среди узлов» и «типа нет» — разные вещи, и различить
    их по манифесту нельзя.
    """
    anchors, errors = _load_anchors(manifest_path, registries, root)
    selected = filter_anchors(anchors, kind or [], team or [])

    if output_format == "json":
        typer.echo(
            stable_json_dumps(
                {
                    "counts": counts(selected),
                    "anchors": [anchor.model_dump(mode="json") for anchor in selected],
                    "registry_errors": errors,
                    "total": len(selected),
                }
            )
        )
    else:
        typer.echo(format_anchors(selected, errors))


@anchors_app.command("explain")
def anchors_explain(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    ref: Annotated[str, typer.Argument(help="Якорь: `ref` или строка показа.")],
    registries: Annotated[Path, typer.Option("--registries", help="Описание реестров.")],
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
) -> None:
    """Показать всё, что известно про один якорь.

    Совпадение ищется и по `ref`, и по строке показа. Строка показа при этом
    **не разбирается** на части: `JOBTITLE` содержит пробелы и двоеточия,
    а заголовок workflow — кириллицу.
    """
    anchors, _ = _load_anchors(manifest_path, registries, root)
    found = [anchor for anchor in anchors if ref in (anchor.ref, anchor.display)]

    if not found:
        typer.echo(f"Якорь не найден: {ref}", err=True)
        raise typer.Exit(code=1)

    typer.echo("\n\n".join(format_explain(anchor) for anchor in found))


if __name__ == "__main__":
    app()
