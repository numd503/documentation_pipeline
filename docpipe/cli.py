"""Точка входа командной строки."""

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import BaseModel

from docpipe import __version__
from docpipe.business import Catalog, doc_path_for, load_catalog, resolve_all
from docpipe.business import build_context as build_resolve_context
from docpipe.business.build import backlinks, compose, entry_snippet, link_warnings
from docpipe.business.catalog import ID_RE
from docpipe.business.lint import CHECKS as LINT_CHECKS
from docpipe.business.lint import format_report as format_lint_report
from docpipe.business.lint import lint as lint_catalog
from docpipe.business.model import KIND_BY_PREFIX
from docpipe.business.resolve import ResolveContext, holder_team
from docpipe.business.status import ACTIONS as BUSINESS_ACTIONS
from docpipe.business.status import STATUSES as BUSINESS_STATUSES
from docpipe.business.status import DocumentStatus as BusinessStatus
from docpipe.business.status import accepted_state as business_accepted_state
from docpipe.business.status import format_statuses as format_business_statuses
from docpipe.business.status import statuses as business_statuses
from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig, load_config
from docpipe.diff import diff_manifests, format_changes
from docpipe.emit import run as run_scan
from docpipe.emit import run_meta_path, write_manifest, write_run_meta
from docpipe.explain import ANY, format_selection, select, selection_json
from docpipe.hashing import content_hash, stable_json_dumps
from docpipe.materialize.apply import apply_plan, format_result, write_atomic
from docpipe.materialize.build import BuildContext, build_context
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
from docpipe.materialize.worklist import (
    DEFAULT_ACTIONS,
    Worklist,
    build_worklist,
    select_documents,
)
from docpipe.model import Manifest, RunMeta
from docpipe.registry import load_registries, read_registry
from docpipe.registry.anchors import (
    ResolvedAnchor,
    counts,
    filter_anchors,
    find_by_implementation,
    format_anchors,
    format_explain,
    format_which,
    resolve_anchors,
    similar_names,
)
from docpipe.stats import (
    STATE_TITLES,
    TOP,
    UNDECIDED,
    Stats,
    collect_stats,
    format_kinds,
    format_report,
    plural,
    stats_from_manifest,
    validate_manifest,
)
from docpipe.web.link import CATEGORIES as LINK_CATEGORIES
from docpipe.web.link import build_report as build_link_report
from docpipe.web.link import format_report as format_link_report
from docpipe.web.tree import run as run_web_scan

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


SCHEMA_MODELS: Final[dict[str, tuple[type[BaseModel], str]]] = {
    "doc-tree": (Manifest, "schema/doc-tree.schema.json"),
    "worklist": (Worklist, "schema/doc-worklist.schema.json"),
}


@app.command()
def schema(
    model: Annotated[
        str, typer.Option("--model", help="Что описывать: doc-tree или worklist.")
    ] = "doc-tree",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Куда записать JSON Schema; без флага — путь по модели."),
    ] = None,
) -> None:
    """Сгенерировать JSON Schema из моделей.

    Схема — производная от `model.py` и `materialize/worklist.py`, а не отдельно
    поддерживаемый файл. Расхождение между ними невозможно по построению.
    """
    if model not in SCHEMA_MODELS:
        known = ", ".join(sorted(SCHEMA_MODELS))
        typer.echo(f"--model: неизвестное значение {model}; известны: {known}", err=True)
        raise typer.Exit(code=2)

    described, default_out = SCHEMA_MODELS[model]
    target = out or Path(default_out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stable_json_dumps(described.model_json_schema()), encoding="utf-8")
    typer.echo(f"Схема записана: {target}")


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
    top: Annotated[
        int,
        typer.Option("--top", help="Сколько строк показывать в каждом срезе --stats."),
    ] = TOP,
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
        typer.echo(format_report(result.stats, top))
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
def symbols(
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория с исходниками.")],
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    rules: Annotated[
        Path | None, typer.Option("--rules", help="Набор правил классификации.")
    ] = None,
    state: Annotated[
        str,
        typer.Option(
            "--state",
            help="undecided, not_documented, documented, not_enrolled, interface_covered или any.",
        ),
    ] = UNDECIDED,
    module: Annotated[
        str, typer.Option("--module", help="Подстрока пути .csproj; с `*` — глоб, как в enrolled.")
    ] = "",
    namespace: Annotated[str, typer.Option("--namespace", help="Начало namespace.")] = "",
    rule: Annotated[
        str, typer.Option("--rule", help="Только символы, к которым причастно это правило.")
    ] = "",
    kind: Annotated[str, typer.Option("--kind", help="Только этот вид сущности.")] = "",
    limit: Annotated[int, typer.Option("--limit", help="Показать не больше N; 0 — все.")] = 0,
    jobs: Annotated[int, typer.Option("--jobs", help="Число процессов для разбора.")] = 1,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Не использовать кэш разобранных файлов.")
    ] = False,
    output_format: Annotated[str, typer.Option("--format", help="text или json.")] = "text",
) -> None:
    """Показать сами символы, а не счётчики: что именно осталось без решения.

    Инструмент отладки набора правил на конкретном проекте. `--stats` отвечает
    «сколько», эта команда — «что именно и по чему для него писать предикат»:
    печатает замыкание наследования, атрибуты, публичные члены и путь.

    Ничего не пишет. Прогон идёт через кэш, поэтому повторный вызов на том же
    репозитории обходится дёшево — так и рассчитано, её зовут в цикле.
    """
    if not root.is_dir():
        raise typer.BadParameter(f"каталог не найден: {root}", param_hint="--root")

    known = {*STATE_TITLES, ANY}
    if state not in known:
        raise typer.BadParameter(
            f"неизвестное состояние {state!r}; известны: {', '.join(sorted(known))}",
            param_hint="--state",
        )

    try:
        settings = load_config(config)
        ruleset = load_ruleset(rules or Path(settings.rules))
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    result = run_scan(
        root, settings, ruleset, None if no_cache else root / settings.cache_dir, jobs
    )
    selection = select(
        result.index,
        result.manifest.nodes,
        ruleset,
        {module.project_file for module in result.manifest.modules if module.enrolled},
        state=state,
        module=module,
        namespace=namespace,
        rule=rule,
        kind=kind,
        limit=limit,
    )

    typer.echo(
        selection_json(selection) if output_format == "json" else format_selection(selection)
    )


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
    loaded = _prepare(
        manifest_path,
        root,
        config,
        templates_dir,
        ownership_file,
        teams=tuple(team or ()),
        force=force,
    )
    plan = loaded.plan

    result = apply_plan(plan, root, dry_run=dry_run, force=force)
    typer.echo(format_result(plan, result, dry_run))

    if plan.errors or result.errors or result.refused:
        raise typer.Exit(code=1)


web_app = typer.Typer(
    help="Шаг `web`: фронтенд на Angular.",
    no_args_is_help=True,
)
app.add_typer(web_app, name="web")


@web_app.command("scan")
def web_scan(
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория с исходниками.")],
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    rules: Annotated[
        Path | None, typer.Option("--rules", help="Набор правил классификации фронта.")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", help="Куда записать манифест. Без флага — `web.out` из конфигурации."
        ),
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Не использовать кэш разобранных файлов.")
    ] = False,
    show_stats: Annotated[
        bool,
        typer.Option("--stats", help="Показать счётчики и подсказки по правилам, не писать файлы."),
    ] = False,
    fail_on_undecided: Annotated[
        bool,
        typer.Option(
            "--fail-on-undecided",
            help="Код 1, если про какой-то символ фронта решение не принято. Для CI.",
        ),
    ] = False,
    top: Annotated[
        int,
        typer.Option("--top", help="Сколько строк показывать в каждом срезе --stats."),
    ] = TOP,
) -> None:
    """Построить дерево документации по исходникам фронтенда.

    Пишет два файла: детерминированный манифест и сидкар `<out>.run.json`.
    Манифест той же схемы, что у шага 1, — `materialize`, `docs status`
    и бизнес-слой работают от него, не зная про язык.
    """
    if not root.is_dir():
        raise typer.BadParameter(f"каталог не найден: {root}", param_hint="--root")

    try:
        settings = load_config(config)
        ruleset = load_ruleset(rules or Path(settings.web.rules))
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    cache_dir = None if no_cache else root / settings.cache_dir
    destination = out or Path(settings.web.out)

    result = run_web_scan(root, settings, ruleset, cache_dir)

    # Счётчик «решение не принято» считается по СВОЕМУ набору и своим флагом.
    # Общий флаг с шагом 1 означал бы, что настройка фронта роняет CI бэкенда:
    # на старте нерешённых много, красный CI выключат на второй день, и вместе
    # с ним пропадут проверки, которые уже работают.
    statistics = collect_stats(
        result.index,
        result.manifest.nodes,
        ruleset,
        {
            module.id.removeprefix("module:")
            for module in result.manifest.modules
            if module.enrolled
        },
    )
    if show_stats:
        typer.echo(format_report(statistics, top))
        _check_undecided(statistics, fail_on_undecided)
        return

    write_manifest(result.manifest, destination)
    write_run_meta(result.meta, destination)

    stats = result.meta.stats
    typer.echo(
        f"Модулей: {len(result.manifest.modules)}, узлов: {len(result.manifest.nodes)}. "
        f"Записано: {destination} и {run_meta_path(destination)}"
    )
    typer.echo(
        f"Вызовов: восстановлено {stats['calls_resolved']}, "
        f"не восстановлено {stats['calls_unresolved']}; "
        f"обращений к реестру без различителя {stats['registry_unresolved']}."
    )
    typer.echo(
        f"Страниц: {stats['routes']}, из них маршрут не собран у {stats['routes_unresolved']}."
    )
    if result.meta.parse_error_files:
        typer.echo(
            f"Внимание: {len(result.meta.parse_error_files)} файлов разобраны с ошибками "
            "и не дали ни одного объявления — см. parse_error_files в сидкаре."
        )
    _check_undecided(statistics, fail_on_undecided)


@web_app.command("link")
def web_link(
    backend: Annotated[Path, typer.Argument(help="Манифест шага 1 (.NET).")],
    frontend: Annotated[Path, typer.Argument(help="Манифест шага `web`.")],
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Куда записать отчёт. Без флага — `web.link_out`."),
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="text либо json.")] = "text",
    fail_on: Annotated[
        list[str] | None,
        typer.Option(
            "--fail-on", help=f"Категории, роняющие прогон: {', '.join(LINK_CATEGORIES)}."
        ),
    ] = None,
) -> None:
    """Свести два манифеста: кто зовёт какой эндпоинт и чего не хватает.

    Пять категорий, и только последняя — дефект. Остальные печатаются всегда
    и кода возврата не меняют, пока не названы в `--fail-on`: линт, красный
    с первого дня, выключат на второй, и вместе с ним пропадут работающие
    проверки.
    """
    try:
        settings = load_config(config)
        first = Manifest.model_validate_json(backend.read_text(encoding="utf-8"))
        second = Manifest.model_validate_json(frontend.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка чтения манифеста: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    unknown = sorted(set(fail_on or []) - set(LINK_CATEGORIES))
    if unknown:
        typer.echo(
            f"Неизвестные категории в --fail-on: {', '.join(unknown)}. "
            f"Известные: {', '.join(LINK_CATEGORIES)}.",
            err=True,
        )
        raise typer.Exit(code=2)

    report = build_link_report(first, second, {rule.module for rule in settings.web.url_rewrite})
    destination = out or Path(settings.web.link_out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(stable_json_dumps(report.model_dump(mode="json")), encoding="utf-8")

    if output_format == "json":
        typer.echo(stable_json_dumps(report.model_dump(mode="json")))
    else:
        typer.echo(format_link_report(report))
        typer.echo(f"\nЗаписано: {destination}")

    triggered = sorted(name for name in (fail_on or []) if report.counts.get(name))
    if triggered:
        typer.echo(
            "Отказ по категориям: "
            + ", ".join(f"{name} ({report.counts[name]})" for name in triggered),
            err=True,
        )
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

    loaded = _prepare(
        manifest_path, root, config, templates_dir, ownership_file, teams=tuple(team or ())
    )
    plan = with_links(loaded.plan, check_links(loaded.existing, root))

    selected = filter_documents(plan.documents, paths or [], action or [])
    typer.echo(
        format_status_json(plan, selected)
        if output_format == "json"
        else format_status(plan, selected)
    )

    if plan.errors or (fail_on and {doc.status for doc in selected} & set(fail_on)):
        raise typer.Exit(code=1)


def _load_ownership_quietly(settings: DocpipeConfig) -> Ownership | None:
    """Правила владения для селектора `only.team` на шаге 2.

    Нечитаемые правила здесь не роняют прогон и не печатают ничего: шаг 2
    материализует техническую документацию, и отказывать ему из-за файла,
    нужного одному селектору бизнес-слоя, — наказание не за то. Скажет об этом
    `business lint`, где это и есть предмет разговора.
    """
    if not settings.ownership:
        return None
    try:
        return load_ownership(Path(settings.ownership))
    except (OSError, ValueError):
        return None


def _with_business_links(
    context: BuildContext, manifest: Manifest, root: Path, settings: DocpipeConfig
) -> BuildContext:
    """Досыпать в контекст шага 2 обратный индекс бизнес-каталога.

    Индекс строится здесь, а не внутри `materialize`: пакет шага 2 не импортирует
    бизнес-слой и получает готовые данные. Без заданных `registries` шаг 2
    работает ровно как прежде — раздела «Бизнес-контекст» не появляется вовсе.

    Неготовность бизнес-слоя прогон шага 2 не роняет: реестры могут быть
    описаны раньше, чем появится первый бизнес-документ, и отказ материализовать
    техническую документацию из-за этого был бы наказанием не за то.
    """
    if not settings.registries:
        return context

    try:
        anchors, _ = read_anchors(manifest, Path(settings.registries), root)
        catalog = load_catalog(root, settings.business_root)
        ownership = _load_ownership_quietly(settings)
        links = backlinks(
            catalog, build_resolve_context(anchors, manifest, root=root, ownership=ownership)
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Бизнес-каталог не прочитан, раздел не собран: {exc}", err=True)
        return context

    return replace(context, business_root=settings.business_root, business_links=links)


def _check_teams(teams: tuple[str, ...], ownership: Ownership | None) -> None:
    """Отвергнуть неизвестные команды.

    Без проверки опечатка в `--team` сужает выборку до пустой, и команда
    рапортует «документов нет» — неотличимо от честного «у этой команды
    документов нет». Раньше это ловил только `materialize`; `docs status`
    и `worklist` с тем же флагом молчали.
    """
    if not teams:
        return
    known = {item.id for item in ownership.teams} if ownership else set()
    unknown = sorted(set(teams) - known)
    if unknown:
        listing = ", ".join(sorted(known)) or "(правила владения не заданы)"
        typer.echo(f"Неизвестные команды: {', '.join(unknown)}; известны: {listing}", err=True)
        raise typer.Exit(code=2)


@dataclass(frozen=True)
class Step2Inputs:
    """Всё, что команды шага 2 читают из конфигурации и с диска.

    Возвращается целиком, а не тройкой-четвёркой: раньше `materialize`,
    `docs status` и `_prepare` собирали одно и то же тремя копиями одного
    блока, и ключ конфигурации, добавленный в одну, до остальных не доезжал.
    Писателем документов при этом был `materialize` — та копия, что отстала бы
    незаметнее всех.
    """

    settings: DocpipeConfig
    manifest: Manifest
    ownership: Ownership | None
    existing: list[ExistingDoc]
    plan: MaterializePlan


def _prepare(
    manifest_path: Path,
    root: Path,
    config: Path | None,
    templates_dir: Path | None,
    ownership_file: Path | None,
    teams: tuple[str, ...] = (),
    accept: tuple[str, ...] = (),
    force: bool = False,
) -> Step2Inputs:
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

    _check_teams(teams, ownership)

    examples = frozenset(path.stem for path in (templates_path / "examples").glob("*.md"))
    context = _with_business_links(
        build_context(manifest, templates, examples, templates_path.as_posix()),
        manifest,
        root,
        settings,
    )
    existing = scan_docs(root, settings.docs_root, settings.docs_scan_exclude)
    plan = build_plan(
        manifest,
        existing,
        templates,
        context,
        ownership,
        PlanOptions(
            docs_root=settings.docs_root,
            modules_root=settings.modules_root,
            teams=teams,
            accept=accept,
            force=force,
        ),
    )
    return Step2Inputs(
        settings=settings, manifest=manifest, ownership=ownership, existing=existing, plan=plan
    )


@app.command()
def worklist(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
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
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Куда писать очередь; без флага — `worklist` из конфигурации."),
    ] = None,
    action: Annotated[
        list[str] | None,
        typer.Option("--action", help="Состав очереди; по умолчанию write и review."),
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Первые N записей в порядке приоритета.")
    ] = None,
) -> None:
    """Записать очередь документов для внешнего исполнителя шага 3.

    В дерево документации не пишет ничего: `materialize` остаётся единственным
    писателем документов, иначе появилась бы вторая реализация слияния зон.
    """
    unknown = sorted(set(action or ()) - AGENT_ACTIONS)
    if unknown:
        typer.echo(
            f"--action: неизвестные значения {', '.join(unknown)};"
            f" известны: {', '.join(sorted(AGENT_ACTIONS))}",
            err=True,
        )
        raise typer.Exit(code=2)

    if limit is not None and limit < 1:
        typer.echo("--limit: ожидается положительное число", err=True)
        raise typer.Exit(code=2)

    # План строится по ВСЕМУ дереву, без `--team`: сводка обязана описывать
    # дерево целиком, а сужает `--team` только очередь. Иначе прогон одной
    # команды объявил бы, что документов в репозитории двадцать.
    loaded = _prepare(manifest_path, root, config, templates_dir, ownership_file)
    settings, manifest = loaded.settings, loaded.manifest

    # План сузить нельзя, а имена команд проверить обязаны: `--team` здесь режет
    # только очередь, и опечатка иначе дала бы пустую очередь при целом дереве.
    _check_teams(tuple(team or ()), loaded.ownership)
    target = out or Path(settings.worklist)
    plan = with_links(loaded.plan, check_links(loaded.existing, root))

    if plan.errors:
        # Файл не переписывается: прежняя очередь достовернее полупустой новой,
        # а чужой процесс о коде возврата может и не узнать.
        typer.echo("\n".join(["Блокирующие ошибки, очередь не записана:", *plan.errors]), err=True)
        raise typer.Exit(code=1)

    selected, truncated = select_documents(
        plan.documents,
        actions=tuple(action) if action else DEFAULT_ACTIONS,
        teams=tuple(team or ()),
        limit=limit,
    )
    queue = build_worklist(
        plan,
        selected,
        docs_root=settings.docs_root,
        modules_root=settings.modules_root,
        ruleset_version=manifest.ruleset_version,
        manifest_sha256=content_hash(manifest_path.read_bytes()),
        truncated=truncated,
    )

    write_atomic(target, stable_json_dumps(queue.model_dump(mode="json")))
    typer.echo(
        f"Очередь записана: {target}"
        f" (документов: {queue.totals.selected} из {queue.totals.documents})"
    )
    if queue.needs_materialize:
        typer.echo("В очереди есть документы, которых на диске ещё нет: нужен `materialize`.")


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

    plan = _prepare(
        manifest_path, root, config, templates_dir, ownership_file, tuple(team or ())
    ).plan
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

    accepted = _prepare(
        manifest_path,
        root,
        config,
        templates_dir,
        ownership_file,
        tuple(team or ()),
        accept=targets,
    ).plan
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
    loaded = _prepare(manifest_path, root, config, templates_dir, ownership_file)
    manifest, existing = loaded.manifest, loaded.existing
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
    rebuilt = _prepare(manifest_path, root, config, templates_dir, ownership_file).plan
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

    # Сообщения здесь длиннее обычного намеренно. Владение — необязательная
    # настройка, файла с ним в репозитории нет, и «файл не найден» без объяснения
    # выглядит как поломка команды, а не как «его надо создать». Этот отказ —
    # первое, обо что спотыкаются, начиная настройку.
    if path is None:
        typer.echo(
            "Правила владения не заданы: --ownership FILE или ключ `ownership`"
            " в docpipe.yaml (тогда нужен --config).\n"
            "Файл заводится копией примера: cp ownership.example.yaml ownership.yaml\n"
            "Владение необязательно: без него у всех документов `team: null`,"
            " и это не мешает ни материализации, ни статусам.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест {manifest_path}: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        ownership = load_ownership(path)
    except FileNotFoundError as exc:
        typer.echo(
            f"Файл правил владения не найден: {path}.\n"
            "В репозитории его нет — он заводится копией примера:"
            " cp ownership.example.yaml ownership.yaml",
            err=True,
        )
        raise typer.Exit(code=2) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"Правила владения не читаются ({path}): {exc}", err=True)
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


def read_anchors(
    manifest: Manifest, registries: Path, root: Path
) -> tuple[list[ResolvedAnchor], list[str]]:
    """Реестры → разрешённые якоря. Единственная сборка этой цепочки.

    Политику отказа задаёт вызывающий, а не эта функция: `anchors` и `business`
    падают на нечитаемых реестрах, а шаг 2 продолжает без бизнес-раздела —
    реестры могут быть описаны раньше первого бизнес-документа, и ронять из-за
    этого материализацию технической документации было бы наказанием не за то.
    Раньше расхождение политик тянуло за собой три копии самой цепочки.
    """
    results = [read_registry(spec, root) for spec in load_registries(registries)]
    errors = [error for result in results for error in result.errors]
    return resolve_anchors(results, manifest), errors


def _load_anchors(
    manifest_path: Path, registries: Path, root: Path
) -> tuple[list[ResolvedAnchor], list[str]]:
    """То же с политикой отказа команд `anchors` и `business`: ошибка чтения — код 2."""
    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        return read_anchors(manifest, registries, root)
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать описание реестров: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _registries_path(registries: Path | None, settings: DocpipeConfig) -> Path:
    """Путь к описанию реестров: флаг важнее конфигурации, но конфигурация читается.

    Раньше команды `anchors` ключ `registries` не читали вовсе — у `list` не было
    даже `--config`. Один и тот же ключ работал в `business *` и молчал здесь,
    так что настроенный репозиторий всё равно требовал флага руками.
    """
    if registries is not None:
        return registries
    if settings.registries:
        return Path(settings.registries)
    typer.echo("Реестры не заданы: --registries или ключ `registries`", err=True)
    raise typer.Exit(code=2)


@anchors_app.command("list")
def anchors_list(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    registries: Annotated[
        Path | None, typer.Option("--registries", help="Описание реестров.")
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: пути реестров от него.")
    ] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
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
    anchors, errors = _load_anchors(
        manifest_path, _registries_path(registries, load_config(config)), root
    )
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
    registries: Annotated[
        Path | None, typer.Option("--registries", help="Описание реестров.")
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения: показать команды.")
    ] = None,
) -> None:
    """Показать всё, что известно про один якорь.

    Совпадение ищется и по `ref`, и по строке показа. Строка показа при этом
    **не разбирается** на части: `JOBTITLE` содержит пробелы и двоеточия,
    а заголовок workflow — кириллицу.
    """
    settings = load_config(config)
    anchors, _ = _load_anchors(manifest_path, _registries_path(registries, settings), root)
    found = [anchor for anchor in anchors if ref in (anchor.ref, anchor.display)]

    if not found:
        typer.echo(f"Якорь не найден: {ref}", err=True)
        raise typer.Exit(code=1)

    ownership_path = ownership_file or (Path(settings.ownership) if settings.ownership else None)
    try:
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        typer.echo(f"Правила владения не прочитаны, команды не показаны: {exc}", err=True)
        ownership = None

    manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    ctx = build_resolve_context(anchors, manifest, root=root, ownership=ownership)

    # Записей на якоре бывает несколько: голый `ItemAdded` совпадает с событием
    # у каждого списка, а пара «список + EventType» — с каждым подписчиком.
    # Во втором случае сузить якорь нужно, и подсказка про `only` печатается
    # только там, где выбор действительно есть.
    keys = Counter((anchor.kind, anchor.scope or "", anchor.ref) for anchor in anchors)

    if len(found) > 1:
        typer.echo(f"Совпало записей: {len(found)}. Уточнить можно парой `Область/Якорь`.\n")

    typer.echo(
        "\n\n".join(
            format_explain(
                anchor,
                team=holder_team(anchor, ctx),
                shared=keys[(anchor.kind, anchor.scope or "", anchor.ref)] > 1,
            )
            for anchor in found
        )
    )


@anchors_app.command("which")
def anchors_which(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    query: Annotated[str, typer.Argument(help="FQN, `doc_path` или простое имя типа.")],
    registries: Annotated[Path, typer.Option("--registries", help="Описание реестров.")],
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    output_format: Annotated[str, typer.Option("--format", help="text или json.")] = "text",
) -> None:
    """Какими якорями вызывается этот тип.

    Обратное к `anchors list`: тот идёт от реестра к коду, а аналитик знает
    свой класс и не знает, какой строкой его вызывают. Печатается готовый
    кусок `entry` — строка показа якоря собрана для человека и обратно
    не разбирается, поэтому собирать её глазами в поля никто не обязан.

    Ищутся и вложенные записи: команда чаще владеет шагом workflow, чем
    процессом целиком, а шаги на верхний уровень не поднимаются.
    """
    if output_format not in ("text", "json"):
        typer.echo(f"Неизвестный формат: {output_format}. Известны: text, json", err=True)
        raise typer.Exit(code=2)

    anchors, errors = _load_anchors(manifest_path, registries, root)
    found = find_by_implementation(anchors, query)

    if output_format == "json":
        typer.echo(
            stable_json_dumps(
                {
                    "query": query,
                    "matches": [
                        {
                            **match.model_dump(mode="json"),
                            "entry_snippet": entry_snippet(match),
                        }
                        for match in found
                    ],
                }
            )
        )
    else:
        typer.echo(
            format_which(
                found,
                query,
                [entry_snippet(m) for m in found],
                similar_names(anchors, query) if not found else None,
            )
        )

    for error in errors:
        typer.echo(f"Замечание при чтении реестров: {error}", err=True)

    # Отсутствие якорей — не ошибка пользователя и не поломка: у большей части
    # кода точки входа нет вовсе. Код 1 здесь означал бы, что «не вызывается
    # ниоткуда» надо чинить, а это нормальное состояние для почти всего дерева.
    if not found:
        raise typer.Exit(code=0)


business_app = typer.Typer(
    help="Бизнес-каталог: процессы, сущности и их связь с кодом.",
    no_args_is_help=True,
)
app.add_typer(business_app, name="business")

BUSINESS_TEMPLATES = "business"


def _business_template(directory: Path, kind: str) -> str:
    path = directory / f"{kind}.md"
    try:
        return path.read_bytes().decode("utf-8-sig")
    except OSError as exc:
        typer.echo(
            f"Скелет не найден: {path}."
            " Каталог берётся из ключа `templates` плюс `business`;"
            " задать явно — флагом --templates",
            err=True,
        )
        raise typer.Exit(code=2) from exc


@business_app.command("new")
def business_new(
    doc_id: Annotated[str, typer.Argument(help="Идентификатор: bp.<домен>.<имя>.")],
    title: Annotated[str, typer.Option("--title", help="Заголовок документа.")],
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    business_root: Annotated[
        str | None, typer.Option("--business-root", help="Каталог бизнес-документов.")
    ] = None,
    templates_dir: Annotated[
        Path | None, typer.Option("--templates", help="Каталог скелетов бизнес-документов.")
    ] = None,
) -> None:
    """Создать скелет бизнес-документа.

    Вид документа берётся из префикса идентификатора и параметром не задаётся:
    второй источник истины про вид разошёлся бы с первым, и документ уехал бы
    не в тот каталог.
    """
    settings = load_config(config)
    if not ID_RE.match(doc_id):
        typer.echo(
            f"Идентификатор {doc_id!r} не по образцу `(bp|be|cap).<домен>.<имя>`"
            " строчными латинскими",
            err=True,
        )
        raise typer.Exit(code=2)

    where = business_root or settings.business_root
    kind = KIND_BY_PREFIX[doc_id.split(".", 1)[0]]
    path = root / doc_path_for(doc_id, where)

    # Повторный вызов — отказ, а не перезапись: документ уже могли наполнить,
    # и «создать заново» здесь означало бы стереть чужую работу.
    if path.exists():
        typer.echo(f"Документ уже существует: {path}", err=True)
        raise typer.Exit(code=1)

    # Скелеты бизнес-слоя лежат подкаталогом внутри каталога скелетов шага 2,
    # поэтому путь выводится из ключа `templates`, а не задан константой.
    # Константа `templates/business` относительно текущего каталога работала бы
    # только в репозитории разработки: в установке инструмент лежит не в корне
    # сканируемого репозитория, и по умолчанию искалось бы несуществующее.
    template = _business_template(
        templates_dir or Path(settings.templates) / BUSINESS_TEMPLATES, kind
    )
    head = (
        "---\ndocpipe:\n"
        f"  schema: business/1\n  id: {doc_id}\n  kind: {kind}\n"
        f"  title: {title}\n  status: draft\n  entry: []\n"
        "docpipe_state:\n  accepted: null\n  review: null\n---\n\n"
    )
    write_atomic(path, head + template.replace("{{ title }}", title))
    typer.echo(f"Создано: {path}")


@business_app.command("build")
def business_build(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    registries_file: Annotated[
        Path | None, typer.Option("--registries", help="Описание реестров.")
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    business_root: Annotated[
        str | None, typer.Option("--business-root", help="Каталог бизнес-документов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Ничего не писать.")] = False,
) -> None:
    """Пересобрать генерируемые блоки бизнес-документов.

    Идемпотентно: сравнение идёт по тексту, и документ без изменений
    не открывается на запись вовсе. Иначе на репозитории с `core.autocrlf=true`
    каждый документ оказывался бы изменённым при каждом прогоне.
    """
    loaded = _business_context(
        manifest_path, registries_file, root, config, business_root, ownership_file
    )

    written: list[str] = []
    unchanged: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []

    for doc in loaded.catalog.docs:
        path = root / doc.doc_path
        try:
            existing = path.read_bytes().decode("utf-8-sig")
        except OSError as exc:
            errors.append(f"{doc.doc_path}: {exc}")
            continue

        warnings += link_warnings(doc, resolve_all(doc.anchors, loaded.ctx))

        text = compose(doc, loaded.ctx, existing, loaded.ownership)
        if text == existing:
            unchanged.append(doc.doc_path)
            continue
        if not dry_run:
            write_atomic(path, text)
        written.append(doc.doc_path)

    verb = "Было бы обновлено" if dry_run else "Обновлено"
    typer.echo(f"{verb}: {len(written)}, без изменений: {len(unchanged)}")
    for line in sorted(written):
        typer.echo(f"  {line}")
    for line in sorted(loaded.catalog.errors) + sorted(errors):
        typer.echo(f"  {line}", err=True)

    # Предупреждения не роняют прогон: документ собран, и текст в нём остаётся
    # полезным. Но сказать о них обязательно — «Реализация: Нет.» неотличима
    # от поломки связности, пока не названа причина.
    for line in sorted(warnings):
        typer.echo(f"Внимание: {line}", err=True)

    if errors:
        raise typer.Exit(code=1)


@business_app.command("status")
def business_status(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    paths: Annotated[list[Path] | None, typer.Argument(help="Файлы или каталоги.")] = None,
    registries_file: Annotated[
        Path | None, typer.Option("--registries", help="Описание реестров.")
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    business_root: Annotated[
        str | None, typer.Option("--business-root", help="Каталог бизнес-документов.")
    ] = None,
    action: Annotated[
        str | None, typer.Option("--action", help="Только эти решения: write, review, skip.")
    ] = None,
    team: Annotated[
        str | None, typer.Option("--team", help="Только документы этой команды.")
    ] = None,
    fail_on: Annotated[
        list[str] | None, typer.Option("--fail-on", help="Код 1 при этих статусах.")
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="text или json.")] = "text",
) -> None:
    """Что делать с каждым бизнес-документом. Ничего не пишет."""
    loaded = _business_context(manifest_path, registries_file, root, config, business_root, None)
    selected = list(fail_on or [])

    unknown = sorted(set(selected) - set(BUSINESS_STATUSES))
    if unknown:
        listing = ", ".join(BUSINESS_STATUSES)
        typer.echo(f"Неизвестный статус: {', '.join(unknown)}. Известны: {listing}", err=True)
        raise typer.Exit(code=2)

    if action and action not in BUSINESS_ACTIONS:
        listing = ", ".join(BUSINESS_ACTIONS)
        typer.echo(f"Неизвестное решение: {action}. Известны: {listing}", err=True)
        raise typer.Exit(code=2)

    items = business_statuses(loaded.catalog, root, loaded.ctx)
    items = _business_filter(items, paths, root, action, team)

    if output_format == "json":
        typer.echo(stable_json_dumps([item.model_dump(mode="json") for item in items]))
    else:
        typer.echo(format_business_statuses(items))

    if selected and any(item.status in set(selected) for item in items):
        raise typer.Exit(code=1)


def _business_filter(
    items: list[BusinessStatus],
    paths: list[Path] | None,
    root: Path,
    action: str | None,
    team: str | None,
) -> list[BusinessStatus]:
    """Сузить выборку. Множество, с которым сравнивают, при этом не сужается:
    статусы уже посчитаны по всему каталогу."""
    selected = items
    if paths:
        wanted = [path.resolve() for path in paths]
        selected = [
            item
            for item in selected
            if any(
                (root / item.doc_path).resolve() == path
                or path in (root / item.doc_path).resolve().parents
                for path in wanted
            )
        ]
    if action:
        selected = [item for item in selected if item.action == action]
    if team:
        selected = [item for item in selected if item.team == team]
    return selected


@business_app.command("accept")
def business_accept(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    paths: Annotated[list[Path] | None, typer.Argument(help="Файлы или каталоги.")] = None,
    registries_file: Annotated[
        Path | None, typer.Option("--registries", help="Описание реестров.")
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    business_root: Annotated[
        str | None, typer.Option("--business-root", help="Каталог бизнес-документов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    accept_all: Annotated[bool, typer.Option("--all", help="Принять весь каталог.")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Принять и ненаполненный документ.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Ничего не писать.")] = False,
) -> None:
    """Зафиксировать соответствие документа реализации.

    Хэш и факты берутся из свежего разрешения, а не из документа: front matter —
    зеркало и мог отстать. Приняв его значение, документ навсегда остался бы
    `current`, будучи `drifted`.
    """
    if not paths and not accept_all:
        typer.echo("Нечего принимать: укажите пути или --all", err=True)
        raise typer.Exit(code=2)

    loaded = _business_context(
        manifest_path, registries_file, root, config, business_root, ownership_file
    )
    items = _business_filter(
        business_statuses(loaded.catalog, root, loaded.ctx), paths, root, None, None
    )
    by_id = loaded.catalog.by_id()

    accepted: list[str] = []
    refused: list[str] = []

    for item in items:
        # Ненаполненный документ не принимается: приёмка означает «текст сверен
        # с реализацией», а сверять нечего. `--force` оставлен для документов,
        # которые описаны целиком в чужой системе (`external_ref`).
        if item.status in ("empty", "broken") and not force:
            refused.append(f"{item.doc_path}: {item.status} — {item.reason}")
            continue

        doc = by_id[item.doc_id]
        path = root / doc.doc_path
        text = compose(
            doc,
            loaded.ctx,
            path.read_bytes().decode("utf-8-sig"),
            loaded.ownership,
            state={"accepted": business_accepted_state(doc, loaded.ctx), "review": None},
        )
        if not dry_run:
            write_atomic(path, text)
        accepted.append(doc.doc_path)

    verb = "Было бы принято" if dry_run else "Принято"
    typer.echo(f"{verb}: {len(accepted)}, отказов: {len(refused)}")
    for line in sorted(accepted):
        typer.echo(f"  {line}")
    for line in sorted(refused):
        typer.echo(f"  {line}", err=True)

    if refused:
        raise typer.Exit(code=1)


@dataclass(frozen=True)
class BusinessInputs:
    """Всё, что нужно любой команде бизнес-слоя, прочитанное один раз."""

    catalog: Catalog
    anchors: list[ResolvedAnchor]
    ctx: ResolveContext
    ownership: Ownership | None
    root: str
    registry_errors: list[str]


def _business_context(
    manifest_path: Path,
    registries_file: Path | None,
    root: Path,
    config: Path | None,
    business_root: str | None,
    ownership_file: Path | None,
) -> BusinessInputs:
    """Каталог, инвентаризация, контекст разрешения и владение.

    Один сборщик на все команды бизнес-слоя. Разойдись они — `build` собирал бы
    документ по одному набору реестров, а `status` сравнивал бы с другим,
    и документ навсегда остался бы «изменившимся».
    """
    settings = load_config(config)
    anchors, registry_errors = _load_anchors(
        manifest_path, _registries_path(registries_file, settings), root
    )
    manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    ownership_path = ownership_file or (Path(settings.ownership) if settings.ownership else None)
    try:
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=2) from exc

    where = business_root or settings.business_root
    return BusinessInputs(
        catalog=load_catalog(root, where),
        anchors=anchors,
        ctx=build_resolve_context(anchors, manifest, root=root, ownership=ownership),
        ownership=ownership,
        root=where,
        registry_errors=registry_errors,
    )


@business_app.command("lint")
def business_lint(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    registries_file: Annotated[
        Path | None, typer.Option("--registries", help="Описание реестров.")
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    business_root: Annotated[
        str | None, typer.Option("--business-root", help="Каталог бизнес-документов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    fail_on: Annotated[
        list[str] | None,
        typer.Option("--fail-on", help=f"Ронять на этих проверках: {', '.join(LINT_CHECKS)}."),
    ] = None,
    scope: Annotated[
        str, typer.Option("--scope", help="all или catalog: с инвентарём или без.")
    ] = "all",
) -> None:
    """Проверить каталог: что сломано и сколько ещё писать.

    Код 1 дают дефекты каталога — то, что автор документа может починить сам.
    Непокрытые точки входа и записи реестров, не найденные среди узлов
    документации, кода возврата не меняют: это состояние репозитория, а не
    дефект. Требование покрытия 100 % не ставится — линт, красный с первого
    дня, выключат на второй.
    """
    if scope not in ("all", "catalog"):
        typer.echo(f"Неизвестный --scope: {scope}. Известны: all, catalog", err=True)
        raise typer.Exit(code=2)

    selected = list(fail_on or [])

    # Значение вне перечня — ошибка, а не пустой фильтр: опечатка
    # `--fail-on unresolvd` иначе дала бы вечно зелёную проверку.
    unknown = sorted(set(selected) - set(LINT_CHECKS))
    if unknown:
        typer.echo(
            f"Неизвестная проверка: {', '.join(unknown)}. Известны: {', '.join(LINT_CHECKS)}",
            err=True,
        )
        raise typer.Exit(code=2)

    loaded = _business_context(
        manifest_path, registries_file, root, config, business_root, ownership_file
    )
    report = lint_catalog(loaded.catalog, loaded.anchors, loaded.ctx, loaded.root, loaded.ownership)

    typer.echo(format_lint_report(report, selected, inventory=scope == "all"))
    for error in loaded.registry_errors:
        typer.echo(f"Замечание при чтении реестров: {error}", err=True)

    if report.failing(selected):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
