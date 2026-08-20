"""Точка входа командной строки."""

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import BaseModel

from docpipe import __version__
from docpipe.arch import (
    AdapterSpec,
    ArchRegistry,
    Collected,
    check_document,
    collect,
    dump_registry,
    format_statuses,
    load_arch_registry,
    read_document,
    source_statuses,
    statuses_json,
)
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
from docpipe.config import DocpipeConfig, candidate_inputs, load_config, resolve_input
from docpipe.diff import diff_manifests, format_changes
from docpipe.discovery import is_excluded
from docpipe.documents import accepted_block, write_atomic
from docpipe.emit import run as run_scan
from docpipe.emit import run_meta_path, write_manifest, write_run_meta
from docpipe.explain import ANY, format_selection, select, selection_json
from docpipe.graph import build as build_graph
from docpipe.graph import read_index, read_meta, read_reach, write_index
from docpipe.graph.coverage import coverage as coverage_of
from docpipe.graph.coverage import format_coverage
from docpipe.graph.engine import Engine, EngineError
from docpipe.graph.evaluate import check_requests, format_requests, format_score, merged_requests
from docpipe.graph.evaluate import load as load_questions
from docpipe.graph.evaluate import run as run_questions
from docpipe.graph.match import RootMismatchError
from docpipe.graph.mcp import Server as McpServer
from docpipe.graph.mcp import serve as serve_mcp
from docpipe.graph.reach import DEFAULT_FANOUT_THRESHOLD
from docpipe.graph.reach import path as graph_path
from docpipe.graph.report import health as health_report
from docpipe.graph.report import render as render_report
from docpipe.graph.search import resolve as resolve_names
from docpipe.hashing import content_hash, stable_json_dumps
from docpipe.materialize.apply import apply_plan, format_result
from docpipe.materialize.build import BuildContext, build_context
from docpipe.materialize.explain import format_explain_document, zone_diff
from docpipe.materialize.ownership import (
    Ownership,
    explain,
    lint,
    load_ownership,
    owner_of,
)
from docpipe.materialize.plan import (
    DEFAULT_DOCS_SCAN_EXCLUDE,
    ExistingDoc,
    MaterializePlan,
    PlannedDoc,
    PlanOptions,
    build_plan,
    check_links,
    expected_root,
    scan_docs,
    shadowed_docs,
    with_links,
)
from docpipe.materialize.status import (
    AGENT_ACTIONS,
    FILE_ACTIONS,
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
from docpipe.model import DocNode, Manifest, RunMeta
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
from docpipe.web.overrides import Overrides, load_overrides
from docpipe.web.pages import DEFAULT_DEPTH
from docpipe.web.pages import FORMATS as PAGE_FORMATS
from docpipe.web.pages import build_report as build_pages_report
from docpipe.web.pages import format_report as format_pages_report
from docpipe.web.pages import report_csv as pages_csv
from docpipe.web.pages import report_json as pages_json
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
    "arch": (ArchRegistry, "schema/arch-registry.schema.json"),
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
        ruleset = load_ruleset(rules or resolve_input(settings.rules, config), "dotnet")
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


def _load_page_overrides(
    pages: Path | None, settings: DocpipeConfig, config: Path | None
) -> Overrides:
    """Ручной состав страниц. Файла нет — пустые правила, а не отказ.

    Репозиторий, где обход находит страницы сам, ничего не дописывает руками,
    и требовать от него файл значило бы делать настройку обязательной там,
    где она не нужна. Но **названный** файл обязан существовать: молча
    проигнорировать `--pages` значит потерять решения человека.
    """
    if pages is not None:
        return load_overrides(pages)
    if not settings.web.pages:
        return Overrides()
    path = resolve_input(settings.web.pages, config)
    return load_overrides(path) if path.is_file() else Overrides()


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
            help=(
                "undecided, not_documented, documented, not_enrolled, "
                "interface_covered, page_covered или any."
            ),
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
    lang: Annotated[
        str,
        typer.Option("--lang", help="cs (шаг 1) либо ts (шаг `web`). По умолчанию cs."),
    ] = "cs",
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

    if lang not in ("cs", "ts"):
        raise typer.BadParameter("известны cs и ts", param_hint="--lang")

    # Секция правил и есть язык: набор .NET, прочитанный шагом `web`, отсеял бы
    # весь фронт целиком (`require_public` на TypeScript), и это молчаливая
    # ловушка, а не ошибка загрузки.
    section = "dotnet" if lang == "cs" else "web"
    try:
        settings = load_config(config)
        settings_rules = settings.rules if lang == "cs" else settings.web.rules
        ruleset = load_ruleset(rules or resolve_input(settings_rules, config), section)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    cache_dir = None if no_cache else root / settings.cache_dir
    if lang == "cs":
        scanned = run_scan(root, settings, ruleset, cache_dir, jobs)
        index, manifest = scanned.index, scanned.manifest
        configured = {module.project_file for module in manifest.modules if module.enrolled}
    else:
        web = run_web_scan(root, settings, ruleset, cache_dir)
        index, manifest = web.index, web.manifest
        configured = {
            module.id.removeprefix("module:") for module in manifest.modules if module.enrolled
        }

    selection = select(
        index,
        manifest.nodes,
        ruleset,
        configured,
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
    pages: Annotated[
        Path | None,
        typer.Option("--pages", help="Ручной состав страниц. Без флага — `web.pages`."),
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
    fail_on_stale_overrides: Annotated[
        bool,
        typer.Option(
            "--fail-on-stale-overrides",
            help="Код 1, если правило из `pages.yaml` ни на что не легло. Для CI.",
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
        ruleset = load_ruleset(rules or resolve_input(settings.web.rules, config), "web")
        overrides = _load_page_overrides(pages, settings, config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    cache_dir = None if no_cache else root / settings.cache_dir
    destination = out or Path(settings.web.out)

    try:
        result = run_web_scan(root, settings, ruleset, cache_dir, overrides)
    except ValueError as exc:
        # Неоднозначное правило снятия. Выбор наугад здесь означал бы, что
        # инструмент сам решает, какую страницу убрать из документации.
        typer.echo(f"Ошибка в ручном составе страниц: {exc}", err=True)
        raise typer.Exit(code=2) from exc

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
        f"Шаблонов прочитано: {stats.get('templates', 0)}; из разметки зовут "
        f"чужие узлы {stats.get('template_usages', 0)} раз, свои члены "
        f"{stats.get('template_own_members', 0)} раз."
    )
    typer.echo(
        f"Страниц: {stats['routes']}, из них маршрут не собран у {stats['routes_unresolved']}."
    )
    if result.overrides.added or result.overrides.removed:
        typer.echo(
            f"Ручной состав: добавлено {len(result.overrides.added)}, "
            f"снято {len(result.overrides.removed)}."
        )
    for rule in result.overrides.stale:
        # Печатается всегда: правило, переставшее совпадать, иначе исчезает
        # вместе со страницей и не оставляет следа ни в одном отчёте.
        typer.echo(f"Внимание: {rule.describe()}", err=True)
    if result.meta.parse_error_files:
        typer.echo(
            f"Внимание: {len(result.meta.parse_error_files)} файлов разобраны с ошибками "
            "и не дали ни одного объявления — см. parse_error_files в сидкаре."
        )
    _check_undecided(statistics, fail_on_undecided)
    if fail_on_stale_overrides and result.overrides.stale:
        typer.echo(
            f"Отказ: правил в ручном составе страниц, не легших ни на что: "
            f"{len(result.overrides.stale)}.",
            err=True,
        )
        raise typer.Exit(code=1)


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


@web_app.command("pages")
def web_pages(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага `web`.")],
    output_format: Annotated[
        str, typer.Option("--format", help=f"Один из: {', '.join(PAGE_FORMATS)}.")
    ] = "text",
    depth: Annotated[
        int,
        typer.Option("--depth", help="Глубина обхода зависимостей при поиске вызовов страницы."),
    ] = DEFAULT_DEPTH,
    route: Annotated[
        str, typer.Option("--route", help="Только страницы, в маршруте которых есть подстрока.")
    ] = "",
    module: Annotated[
        str, typer.Option("--module", help="Только страницы этого модуля (подстрока имени).")
    ] = "",
    not_pages: Annotated[
        bool,
        typer.Option("--not-pages", help="Дописать компоненты, страницами не ставшие, и почему."),
    ] = False,
) -> None:
    """Показать страницы фронта и почему каждая из них страница.

    Вид `page` — единственный, который не выдаётся правилом: правило говорит
    «компонент», а страницей его делает повышение по таблице роутов. Поэтому
    ни `scan --stats`, ни `symbols` этот вид объяснить не могут, и цифру
    «страниц N» до этой команды нечем было проверить.

    Вызовы страницы **вычисляются обходом** зависимостей, а не читаются полем:
    `web_calls` лежат на узле сервиса, где вызов и записан. Отсюда `--depth`
    и указание посредника у каждого вызова.
    """
    if output_format not in PAGE_FORMATS:
        typer.echo(
            f"Неизвестный формат: {output_format}. Известные: {', '.join(PAGE_FORMATS)}.",
            err=True,
        )
        raise typer.Exit(code=2)
    if depth < 0:
        raise typer.BadParameter("глубина не может быть отрицательной", param_hint="--depth")

    try:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    report = build_pages_report(manifest, depth=depth, route=route, module=module)

    if output_format == "json":
        typer.echo(pages_json(report))
        return
    if output_format == "csv":
        typer.echo(pages_csv(report), nl=False)
        return

    typer.echo(format_pages_report(report, show_not_pages=not_pages))

    # Пустой список у манифеста с узлами — это не «страниц нет», а «таблица
    # роутов не собралась»: она опознаётся по аннотации типа (`: Routes`),
    # и фронт на `RouterModule.forRoot([...])` с массивом на месте не даст
    # ни одной записи, не дав и ни одной ошибки разбора.
    if not report.counts["pages"] and manifest.nodes:
        typer.echo(
            "\nНи одной страницы. Таблица роутов опознаётся по аннотации типа "
            "(`const x: Routes = [...]`); массив, объявленный прямо в "
            "`RouterModule.forRoot([...])` или без аннотации, таблицей не считается.",
            err=True,
        )


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
        typer.Option("--action", help="Только эти решения агента: write, review, skip."),
    ] = None,
    file_action: Annotated[
        list[str] | None,
        typer.Option(
            "--file-action",
            help="Только эти действия с файлом: create, update, unchanged, relocate, refuse.",
        ),
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
        ("--file-action", file_action, FILE_ACTIONS),
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

    selected = filter_documents(plan.documents, paths or [], action or [], file_action or [])
    typer.echo(
        format_status_json(plan, selected)
        if output_format == "json"
        else format_status(plan, selected)
    )

    if plan.errors or (fail_on and {doc.status for doc in selected} & set(fail_on)):
        raise typer.Exit(code=1)


@docs_app.command("explain")
def docs_explain(
    manifest_path: Annotated[Path, typer.Argument(help="Манифест шага 1.")],
    doc_path: Annotated[
        Path, typer.Argument(help="Путь документа: репо-относительный или обычный.")
    ],
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория.")] = Path("."),
    config: Annotated[Path | None, typer.Option("--config", help="docpipe.yaml.")] = None,
    templates_dir: Annotated[
        Path | None, typer.Option("--templates", help="Каталог шаблонов.")
    ] = None,
    ownership_file: Annotated[
        Path | None, typer.Option("--ownership", help="Правила владения.")
    ] = None,
    show_diff: Annotated[
        bool, typer.Option("--diff", help="Показать полный unified diff, а не только зоны.")
    ] = False,
) -> None:
    """Почему с этим документом сделают именно это. Ничего не пишет.

    Отчёт по дереву группирует по статусу и на три вопроса не отвечает: какой
    фильтр обхода отбросил лежащий на диске файл, чем собранный текст отличается
    от него и не задевает ли перезапись авторские секции. Здесь — по шагам
    и про один документ.
    """
    loaded = _prepare(manifest_path, root, config, templates_dir, ownership_file)

    # Путь принимается в любом виде, лишь бы указывал на то же место: репо-
    # относительный из отчёта, обычный из оболочки с дополнением по Tab,
    # абсолютный. Требовать один вид значило бы заставлять человека
    # преобразовывать путь руками ровно тогда, когда он и так что-то ищет.
    target = doc_path.as_posix()
    try:
        target = doc_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        target = target[2:] if target.startswith("./") else target

    plan = with_links(loaded.plan, check_links(loaded.existing, root))
    if plan.errors:
        typer.echo(format_status(plan, []))
        raise typer.Exit(code=1)

    doc = next((item for item in plan.documents if item.doc_path == target), None)
    globs = DEFAULT_DOCS_SCAN_EXCLUDE + list(loaded.settings.docs_scan_exclude)
    typer.echo(format_explain_document(target, doc, root, globs, show_diff))

    if doc is None:
        raise typer.Exit(code=1)

    # Затронутая авторская секция — не «изменение», а нарушение инварианта
    # шага 2, поэтому код возврата: такую находку надо ловить проверкой,
    # а не глазами в выводе.
    if doc.content is not None and (root / target).is_file():
        raw = (root / target).read_bytes().decode("utf-8-sig")
        before = raw.replace("\r\n", "\n").replace("\r", "\n")
        if zone_diff(before, doc.content).touches_authored:
            raise typer.Exit(code=1)


def _load_ownership_quietly(settings: DocpipeConfig, config: Path | None) -> Ownership | None:
    """Правила владения для селектора `only.team` на шаге 2.

    Нечитаемые правила здесь не роняют прогон и не печатают ничего: шаг 2
    материализует техническую документацию, и отказывать ему из-за файла,
    нужного одному селектору бизнес-слоя, — наказание не за то. Скажет об этом
    `business lint`, где это и есть предмет разговора.
    """
    if not settings.ownership:
        return None
    try:
        return load_ownership(resolve_input(settings.ownership, config))
    except (OSError, ValueError):
        return None


def _with_business_links(
    context: BuildContext,
    manifest: Manifest,
    root: Path,
    settings: DocpipeConfig,
    config: Path | None,
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
        anchors, _ = read_anchors(manifest, resolve_input(settings.registries, config), root)
        catalog = load_catalog(root, settings.business_root)
        ownership = _load_ownership_quietly(settings, config)
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
    templates_path = templates_dir or resolve_input(settings.templates, config)
    ownership_path = ownership_file or (
        resolve_input(settings.ownership, config) if settings.ownership else None
    )

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
        config,
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
            web_modules_root=settings.web_modules_root,
            teams=teams,
            accept=accept,
            force=force,
            docs_scan_exclude=tuple(settings.docs_scan_exclude),
            # Проверка по диску, а не по обходу: узел без файла и узел, чей файл
            # обход не увидел, — разные состояния, и различить их можно только так.
            shadowed=tuple(shadowed_docs(root, manifest, existing)),
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
        # Префикс очереди берётся по манифесту: у фронта своя ветка дерева,
        # и корень бэкенда сообщил бы внешнему исполнителю путь, которого
        # в очереди нет ни у одного документа.
        modules_root=expected_root(manifest, settings.modules_root, settings.web_modules_root),
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

    claiming = {node.doc_path: node for node in manifest.nodes}
    if to_path not in claiming:
        problems.append(f"{to_path}: ни один узел манифеста не претендует на этот путь")

    # Узел, поглощённый страницей, своего документа не получает вовсе, поэтому
    # переносить на его путь бессмысленно: файл тут же снова стал бы сиротой.
    # Слить текст в раздел страницы автоматически нельзя — это авторский текст,
    # и место ему выбирает человек, а не совпадение имён.
    absorbed = _absorbing_page(manifest, from_path, existing)
    if absorbed:
        problems.append(
            f"{from_path}: узел описывается внутри страницы {absorbed.title} "
            f"({absorbed.doc_path}). Перенесите текст в её разделы «Состояние» "
            "и «Логика» руками, затем удалите файл."
        )

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


def _absorbing_page(manifest: Manifest, path: str, existing: list[ExistingDoc]) -> DocNode | None:
    """Страница, внутри которой описан узел этого документа. Иначе `None`."""
    doc = next((item for item in existing if item.path == path), None)
    node = next(
        (item for item in manifest.nodes if item.id == (doc.node_id if doc else None)), None
    )
    if node is None or not node.absorbed_by:
        return None
    return next((item for item in manifest.nodes if item.id == node.absorbed_by), None)


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
    path = ownership_file or (
        resolve_input(settings.ownership, config) if settings.ownership else None
    )

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


arch_app = typer.Typer(
    help="Нормализованный реестр архитектурных элементов: точки входа, данные, швы, слои.",
    no_args_is_help=True,
)
app.add_typer(arch_app, name="arch")


def _arch_path(arch: Path | None, settings: DocpipeConfig, config: Path | None) -> Path:
    """Путь к реестру: флаг важнее конфигурации, но конфигурация читается.

    Реестр — вход инструмента, поэтому путь разрешается двумя ступенями
    (`resolve_input`): сначала от текущего каталога, потом от каталога
    `docpipe.yaml`. Конфигурация лежит не в корне, и путь внутри неё пишут
    относительно неё же.
    """
    if arch is not None:
        return arch
    if settings.arch:
        return resolve_input(settings.arch, config)
    typer.echo(
        "Реестр не задан: укажите путь аргументом или ключом `arch` в docpipe.yaml",
        err=True,
    )
    raise typer.Exit(code=2)


@arch_app.command("validate")
def arch_validate(
    arch: Annotated[
        Path | None, typer.Argument(help="Файл реестра. Без аргумента — ключ `arch`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    draft: Annotated[
        bool,
        typer.Option(
            "--draft",
            help="Проверять черновик скилла: разрешён провенанс `skill_proposed`.",
        ),
    ] = False,
) -> None:
    """Проверить реестр целиком и назвать все находки сразу.

    Все, а не первую: файл заполняет человек, и второй заход ради второй
    ошибки — способ отучить его заполнять файл.
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    path = _arch_path(arch, settings, config)
    if not path.exists():
        typer.echo(f"Реестр не найден: {path}", err=True)
        raise typer.Exit(code=2)

    try:
        raw = read_document(path)
    except (OSError, ValueError) as exc:
        typer.echo(f"Не удалось прочитать реестр: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    registry, problems = check_document(raw, draft=draft)
    if registry is None:
        typer.echo(f"{path}: реестр не прошёл проверку", err=True)
        for problem in problems:
            typer.echo(f"  {problem.where}: {problem.message}", err=True)
        raise typer.Exit(code=1)

    kinds = ("entry_point", "data", "seam", "layer")
    counts = {kind: len(registry.of_kind(kind)) for kind in kinds}
    listed = ", ".join(f"{kind}: {count}" for kind, count in counts.items())
    if not registry.records:
        typer.echo(
            f"{path}: реестр пуст и это валидное состояние — "
            "на репозитории без реестров граф строится и без него."
        )
        return
    typer.echo(f"{path}: реестр в порядке, версия {registry.version}. Записей — {listed}.")


def _adapter_specs(settings: DocpipeConfig) -> list[AdapterSpec]:
    return [
        AdapterSpec(id=item.id, adapter=item.adapter, options=dict(item.options))
        for item in settings.arch_adapters
    ]


def _collect_records(
    arch: Path | None, settings: DocpipeConfig, config: Path | None, root: Path
) -> Collected:
    """Снимок плюс адаптеры. Путь к реестру необязателен: реестра может не быть."""
    path: Path | None
    if arch is not None:
        path = arch
    elif settings.arch:
        path = resolve_input(settings.arch, config)
    else:
        path = None
    return collect(
        path,
        _adapter_specs(settings),
        root,
        resolve=lambda value: resolve_input(value, config),
    )


@arch_app.command("records")
def arch_records(
    arch: Annotated[
        Path | None, typer.Argument(help="Файл реестра. Без аргумента — ключ `arch`.")
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: пути источников от него.")
    ] = Path("."),
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Машиночитаемый вывод вместо текста.")
    ] = False,
) -> None:
    """Показать все записи: и снимок, и то, что читают адаптеры.

    Отдельная команда, а не флаг `status`, потому что вопросы разные:
    `status` спрашивает «не отстал ли снимок», `records` — «что вообще
    входит в реестр на этом репозитории».
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        collected = _collect_records(arch, settings, config, root)
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    registry = collected.registry
    if as_json:
        payload = {
            "from_file": collected.from_file,
            "from_adapters": [
                {"id": name, "records": count} for name, count in collected.from_adapters
            ],
            "shadowed_by_file": list(collected.shadowed_by_file),
            "duplicates": list(collected.duplicates),
            "errors": list(collected.errors),
            "records": [record.model_dump(mode="json") for record in registry.records],
        }
        typer.echo(stable_json_dumps(payload), nl=False)
        return

    kinds = ("entry_point", "data", "seam", "layer")
    listed = ", ".join(f"{kind}: {len(registry.of_kind(kind))}" for kind in kinds)
    typer.echo(f"Записей всего: {len(registry.records)} — {listed}")
    typer.echo(f"  из снимка: {collected.from_file}")
    for name, count in collected.from_adapters:
        typer.echo(f"  адаптер {name}: {count}")
    if not collected.from_adapters:
        typer.echo("  адаптеров не подключено — реестр держится снимком")
    if collected.shadowed_by_file:
        typer.echo(
            f"\nЗапись есть и в снимке, и у адаптера ({len(collected.shadowed_by_file)}); "
            "оставлена запись снимка — она прошла через человека:"
        )
        for line in collected.shadowed_by_file[:10]:
            typer.echo(f"  {line}")
    if collected.duplicates:
        typer.echo(
            f"\nАдаптер вернул запись, ключ которой уже занят ({len(collected.duplicates)}); "
            "источник объявляет её дважды или ключу не хватает различителя:"
        )
        for line in collected.duplicates[:10]:
            typer.echo(f"  {line}")
    if collected.errors:
        typer.echo(f"\nНаходки чтения ({len(collected.errors)}):")
        for line in collected.errors[:20]:
            typer.echo(f"  {line}")


@arch_app.command("snapshot")
def arch_snapshot(
    out: Annotated[Path, typer.Option("--out", help="Куда записать снимок.")],
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: пути источников от него.")
    ] = Path("."),
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    adapter: Annotated[
        list[str] | None,
        typer.Option("--adapter", help="Снять только этот адаптер. Можно повторять."),
    ] = None,
) -> None:
    """Снять снимок с адаптеров: живое чтение превращается в файл.

    Снимок пишется с хэшами источников, поэтому `arch status` начинает
    ловить его устаревание сразу, без дописывания сорока строк руками.
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    specs = _adapter_specs(settings)
    if adapter:
        wanted = set(adapter)
        specs = [spec for spec in specs if spec.id in wanted]
        unknown = wanted - {spec.id for spec in specs}
        if unknown:
            typer.echo(f"Адаптеры не найдены в конфигурации: {sorted(unknown)}", err=True)
            raise typer.Exit(code=2)
    if not specs:
        typer.echo("Адаптеров не подключено: снимать нечего", err=True)
        raise typer.Exit(code=2)

    try:
        collected = collect(None, specs, root, resolve=lambda value: resolve_input(value, config))
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    header = (
        "# Снимок, снятый адаптерами: "
        + ", ".join(spec.id for spec in specs)
        + "\n# Правки руками переживут только до следующего `docpipe arch snapshot`.\n"
        + "# Проверить: docpipe arch validate; не отстал ли: docpipe arch status.\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_registry(collected.registry, header), encoding="utf-8")
    typer.echo(f"Снимок записан: {out} — записей {len(collected.registry.records)}")
    for line in collected.errors[:20]:
        typer.echo(f"  {line}")


@arch_app.command("status")
def arch_status(
    arch: Annotated[
        Path | None, typer.Argument(help="Файл реестра. Без аргумента — ключ `arch`.")
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: пути источников от него.")
    ] = Path("."),
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Машиночитаемый вывод вместо текста.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Печатать все записи, а не первые пять в категории.")
    ] = False,
    fail_on_stale: Annotated[
        bool,
        typer.Option(
            "--fail-on-stale",
            help="Вернуть ненулевой код, если снимок отстал от источника.",
        ),
    ] = False,
) -> None:
    """Показать записи, у которых снимок разошёлся с источником.

    Красным по умолчанию отчёт не бывает: устаревший снимок — состояние
    работы, а не дефект, и линт, красный с первого дня, выключают на второй.
    Порог задаёт вызывающий флагом.
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    path = _arch_path(arch, settings, config)
    try:
        registry = load_arch_registry(path)
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    statuses = source_statuses(registry, root)
    if as_json:
        typer.echo(stable_json_dumps(statuses_json(statuses)), nl=False)
    else:
        typer.echo(format_statuses(statuses, verbose=verbose), nl=False)

    stale = [item for item in statuses if item.state in ("stale", "source_missing")]
    if stale and fail_on_stale:
        raise typer.Exit(code=1)


graph_app = typer.Typer(
    help="Индекс связей: точки входа, вызовы, данные и швы.",
    no_args_is_help=True,
)
app.add_typer(graph_app, name="graph")


@graph_app.command("build")
def graph_build(
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория с исходниками.")],
    manifest_path: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            help="Манифест шага 1: даёт узлам модуль, FQN и связь с документом.",
        ),
    ] = None,
    web_manifest_path: Annotated[
        Path | None,
        typer.Option(
            "--web-manifest",
            help="Манифест шага `web`: страницы, цепочка фронта и швы с бэкендом.",
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Куда записать индекс. Без флага — `graph.out`."),
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
) -> None:
    """Собрать индекс связей: разбор, чтение, проекция, запись.

    Один вход — один выход: хэш логического содержимого совпадает между двумя
    прогонами на одном входе. Времени в индексе нет вовсе, поколение выводится
    из содержимого.
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if not settings.graph.engine_path:
        typer.echo(
            "Не задан путь к разборщику: ключ `graph.engine_path` в docpipe.yaml. "
            "Умолчания у него нет намеренно — запускать то, что нашлось в PATH, "
            "значит получить числа от другой версии и не заметить этого.",
            err=True,
        )
        raise typer.Exit(code=2)

    ruleset_excludes = list(settings.exclude)
    engine = Engine(
        binary=Path(settings.graph.engine_path).expanduser(),
        cache_dir=Path(settings.graph.cache_dir),
        mode=settings.graph.mode,
        expected_sha256=settings.graph.engine_sha256 or Engine.expected_sha256,
    )

    def excluded(path: str) -> bool:
        return is_excluded(path, ruleset_excludes)

    arch_registry = None
    if settings.arch or settings.arch_adapters:
        try:
            arch_registry = _collect_records(None, settings, config, root).registry
        except (OSError, ValueError) as exc:
            typer.echo(f"Не удалось прочитать реестр: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    manifest = None
    if manifest_path is not None:
        try:
            manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            typer.echo(f"Не удалось прочитать манифест: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    web_manifest = None
    if web_manifest_path is not None:
        try:
            web_manifest = Manifest.model_validate_json(
                web_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"Не удалось прочитать манифест фронта: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    typer.echo(f"Разбор репозитория {root}…")
    try:
        result = build_graph(
            engine,
            root,
            is_excluded=excluded,
            manifest=manifest,
            arch=arch_registry,
            web=web_manifest,
            progress=lambda message: typer.echo(f"  … {message}"),
        )
    except EngineError as exc:
        typer.echo(f"Разбор не состоялся: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except RootMismatchError as exc:
        typer.echo(f"Манифест и разбор сняты с разных корней: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    target = out or Path(settings.graph.out)
    meta = write_index(target, result.index, result.meta, result.reachability, result.searchable)

    typer.echo(
        f"Индекс записан: {target}\n"
        f"  узлов: {meta.counts.get('nodes', 0)}, рёбер: {meta.counts.get('edges', 0)}\n"
        f"  у разборщика было: узлов {meta.counts.get('engine_nodes', 0)}, "
        f"рёбер {meta.counts.get('engine_edges', 0)}\n"
        f"  корней: {meta.counts.get('roots', 0)}\n"
        f"  поколение: {meta.generation}"
    )
    if meta.report:
        typer.echo("Что не вошло в индекс:")
        for category, number in sorted(meta.report.items()):
            typer.echo(f"  {category}: {number}")

    if result.entry_points is not None and not result.entry_points.registry_present:
        typer.echo(
            "\nДекларативных источников точек входа нет: корни собраны только "
            "из кода. Это законное состояние — на репозитории, где точки входа "
            "объявлены в коде, реестр не нужен."
        )
    if result.entry_points is not None and result.entry_points.unlinked_examples:
        typer.echo("\nКорни без узла кода (состояние работы, не дефект):")
        for example in result.entry_points.unlinked_examples[:5]:
            typer.echo(f"  {example}")

    if result.match is not None:
        typer.echo("\nСопоставление с манифестом:")
        for category, number in result.match.as_counts().items():
            typer.echo(f"  {category}: {number}")
        for category, examples in result.match.examples.items():
            if examples:
                typer.echo(f"  {category} — примеры:")
                for example in examples[:5]:
                    typer.echo(f"    {example}")
        typer.echo(
            "  «есть в графе — нет в манифесте» велико по построению: манифест — "
            "дерево документации, а не полный список объявлений. Смотреть надо "
            "на обратное число."
        )


@graph_app.command("entrypoints")
def graph_entrypoints(
    index: Annotated[
        Path | None, typer.Argument(help="Файл индекса. Без аргумента — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    kind: Annotated[str | None, typer.Option("--kind", help="Только корни этого вида.")] = None,
    unlinked: Annotated[
        bool, typer.Option("--unlinked", help="Только корни, не связанные с кодом.")
    ] = False,
) -> None:
    """Показать корни графа: вид, источник и связан ли корень с кодом.

    Пустой вывод здесь запрещён так же, как в поиске: «корней нет» говорится
    словами и со списком того, где искали.
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    path = index or Path(settings.graph.out)
    if not path.is_file():
        typer.echo(
            f"Индекса нет: {path}. Соберите: docpipe graph build --root <репозиторий>",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        loaded = read_index(path)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    linked = {edge.source for edge in loaded.edges if edge.kind == "dispatches"}
    roots = [node for node in loaded.nodes if node.kind == "entry_point"]
    if kind:
        roots = [node for node in roots if node.attributes.get("entry_kind") == kind]
    if unlinked:
        roots = [node for node in roots if node.key not in linked]

    if not roots:
        typer.echo(
            "Корней не найдено. Искали в реестре (ключ `arch`, адаптеры "
            "`arch_adapters`) и в манифесте (эндпоинты контроллеров). "
            "Если реестра нет — это законное состояние; если он есть, "
            "проверьте `docpipe arch records`."
        )
        return

    by_source = {"registry": "реестр", "manifest": "код"}
    for node in sorted(roots, key=lambda item: item.key):
        mark = "связан" if node.key in linked else "НЕ СВЯЗАН"
        entry_kind = node.attributes.get("entry_kind", "—")
        where = node.attributes.get("source_record") or node.attributes.get("member") or ""
        typer.echo(
            f"{entry_kind:<16} {node.name:<44} {by_source.get(node.source, node.source):<8} "
            f"{mark:<10} {node.file}{(' → ' + where) if where else ''}"
        )
    typer.echo(
        f"\nВсего корней: {len(roots)}, из них связано с кодом: "
        f"{sum(1 for node in roots if node.key in linked)}"
    )


def _open_index(index: Path | None, config: Path | None) -> tuple[Path, DocpipeConfig]:
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    path = index or Path(settings.graph.out)
    if not path.is_file():
        typer.echo(
            f"Индекса нет: {path}. Соберите: docpipe graph build --root <репозиторий>",
            err=True,
        )
        raise typer.Exit(code=2)
    return path, settings


@graph_app.command("report")
def graph_report(
    index: Annotated[
        Path | None, typer.Argument(help="Файл индекса. Без аргумента — `graph.out`.")
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Куда записать markdown. Без флага — на экран.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    threshold: Annotated[
        int,
        typer.Option("--fanout", help="Порог веерности: выше него узел считается общим."),
    ] = DEFAULT_FANOUT_THRESHOLD,
) -> None:
    """Таблица точек входа: что достигается, какие таблицы, где обрывается.

    Одна команда, а не сумма трёх, которые надо догадаться позвать.
    Формат детерминированный: отчёт кладут в ревью и сравнивают между
    прогонами.
    """
    path, _ = _open_index(index, config)
    try:
        loaded = read_index(path)
        meta = read_meta(path)
        reachability = read_reach(path)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    text = render_report(loaded, meta, reachability, threshold)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Отчёт записан: {out}")
        return
    typer.echo(text)


@graph_app.command("health")
def graph_health(
    index: Annotated[
        Path | None, typer.Argument(help="Файл индекса. Без аргумента — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
) -> None:
    """Отчёт о неполноте: что не разрешилось и в каком количестве.

    Красным по умолчанию отчёт не бывает: линт, красный с первого дня,
    выключают на второй.
    """
    path, _ = _open_index(index, config)
    try:
        typer.echo(health_report(read_meta(path)), nl=False)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@graph_app.command("reaches")
def graph_reaches(
    root: Annotated[str, typer.Argument(help="Ключ или имя точки входа.")],
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    kind: Annotated[
        str | None, typer.Option("--kind", help="Только узлы этого вида: member, type, data.")
    ] = None,
) -> None:
    """Что достигает эта точка входа: код, таблицы, швы."""
    path, _ = _open_index(index, config)
    loaded = read_index(path)
    reachability = read_reach(path)

    nodes = {node.key: node for node in loaded.nodes}
    candidates = [
        node
        for node in loaded.nodes
        if node.kind == "entry_point" and (node.key == root or root.lower() in node.name.lower())
    ]
    if not candidates:
        # Пустой ответ запрещён: называется, что искали и что рядом.
        known = sorted(node.name for node in loaded.nodes if node.kind == "entry_point")[:10]
        typer.echo(f"Точка входа {root!r} не найдена. Известные корни (первые): {known}")
        raise typer.Exit(code=1)
    if len(candidates) > 1:
        typer.echo(f"Под {root!r} подходит несколько корней — уточните:")
        for node in candidates[:10]:
            typer.echo(f"  {node.key}  {node.name}")
        raise typer.Exit(code=1)

    found = candidates[0]
    reached = [nodes[key] for key in reachability.reached_by(found.key) if key in nodes]
    if kind:
        reached = [node for node in reached if node.kind == kind]
    typer.echo(f"{found.name} ({found.attributes.get('entry_kind', '—')})")
    for group in ("data", "member", "type", "seam"):
        selected = sorted(node.name or node.key for node in reached if node.kind == group)
        if selected:
            typer.echo(f"  {group}: {len(selected)}")
            for name in selected[:15]:
                typer.echo(f"    {name}")
    if not reached:
        typer.echo(
            "  ничего не достигается: у корня нет узла кода либо из него не видно ни одного вызова"
        )


@graph_app.command("affects")
def graph_affects(
    node: Annotated[str, typer.Argument(help="Ключ узла или часть его имени.")],
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    threshold: Annotated[
        int, typer.Option("--fanout", help="Порог веерности: выше него анализ не сужает.")
    ] = DEFAULT_FANOUT_THRESHOLD,
) -> None:
    """Какие точки входа затронет изменение этого узла.

    Узел выше порога веерности — это не «затронуто триста процессов»,
    а «общий компонент, анализ не сужает». Инструмент, который всегда
    что-то отвечает, теряет доверие целиком.
    """
    path, _ = _open_index(index, config)
    loaded = read_index(path)
    reachability = read_reach(path)
    nodes = {item.key: item for item in loaded.nodes}

    matches = [
        item
        for item in loaded.nodes
        if item.key == node or node.lower() in (item.name or "").lower()
    ]
    if not matches:
        typer.echo(f"Узел {node!r} не найден. Проверьте `docpipe graph entrypoints` и имена узлов.")
        raise typer.Exit(code=1)

    for item in sorted(matches, key=lambda value: value.key)[:5]:
        fanout = reachability.fanout(item.key)
        typer.echo(f"{item.key}")
        if fanout > threshold:
            typer.echo(
                f"  ОБЩИЙ КОМПОНЕНТ: достижим от {fanout} корней при пороге {threshold}. "
                "Анализ влияния здесь не сужает — список точек входа не печатается "
                "намеренно."
            )
            continue
        roots = [nodes[key].name for key in reachability.roots_of(item.key) if key in nodes]
        if not roots:
            typer.echo("  не достижим ни от одной точки входа")
            continue
        typer.echo(f"  точек входа: {len(roots)}")
        for name in sorted(roots)[:20]:
            typer.echo(f"    {name}")


@graph_app.command("path")
def graph_path_command(
    source: Annotated[str, typer.Argument(help="Откуда: ключ узла.")],
    target: Annotated[str, typer.Argument(help="Куда: ключ узла.")],
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    depth: Annotated[int, typer.Option("--depth", help="Предел глубины обхода.")] = 12,
) -> None:
    """Как связаны две сущности. Единственная команда, которая обходит граф."""
    path, _ = _open_index(index, config)
    loaded = read_index(path)
    found = graph_path(loaded, source, target, depth)
    if not found:
        typer.echo(
            f"Пути от {source} до {target} не нашлось при глубине {depth}. "
            "Это может значить и «связи нет», и «цепочка длиннее предела» — "
            "попробуйте --depth."
        )
        raise typer.Exit(code=1)
    typer.echo(source)
    for edge in found:
        typer.echo(f"  --{edge.kind}({edge.via})--> {edge.target}")


@graph_app.command("resolve")
def graph_resolve(
    query: Annotated[str, typer.Argument(help="Свободный текст: имя, маршрут, таблица.")],
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Сколько кандидатов показать.")] = 10,
) -> None:
    """Что это такое, если известно только приблизительное имя.

    Ответ называет, **чем совпало**: без этого вызывающий не знает, стоит ли
    переформулировать запрос и как именно. Пустого ответа не бывает —
    при отсутствии точного совпадения печатаются ближайшие по триграммам.
    """
    path, _ = _open_index(index, config)
    loaded = read_index(path)
    nodes = {node.key: node for node in loaded.nodes}
    matches, inexact = resolve_names(path, query, nodes, limit)

    if not matches:
        # Пустота — худший из возможных ответов: она неотличима от факта
        # и не даёт зацепки для второй попытки. Поэтому говорится, где
        # искали и что вообще есть в индексе.
        counts: dict[str, int] = {}
        for node in loaded.nodes:
            counts[node.kind] = counts.get(node.kind, 0) + 1
        inventory = ", ".join(f"{kind} — {number}" for kind, number in sorted(counts.items()))
        typer.echo(
            f"По запросу {query!r} не нашлось ничего, даже похожего.\n"
            "Искали среди имён узлов, ключей, маршрутов, названий полей "
            "и заголовков документов.\n"
            f"В индексе: {inventory}."
        )
        examples = sorted(
            node.name for node in loaded.nodes if node.kind == "entry_point" and node.name
        )[:5]
        if examples:
            typer.echo(f"Точки входа, например: {', '.join(examples)}")
        raise typer.Exit(code=1)
    if inexact:
        typer.echo("Точного совпадения нет; ниже — ближайшее по тому, чем совпало.")
    for match in matches:
        module = f"  [{match.module}]" if match.module else ""
        typer.echo(
            f"{match.kind:<12} {match.name:<40} {match.how} по полю «{match.field}»"
            f"{module}\n    {match.node}"
        )


@graph_app.command("serve")
def graph_serve(
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: нужен формам без графа.")
    ] = Path("."),
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
) -> None:
    """Запустить MCP-сервер на stdio: формы вопроса становятся инструментами агента.

    Сервер не собирает граф сам: отсутствие индекса — внятная ошибка с командой
    сборки. Отсутствие реестра ошибкой не является — `overview`, `why` и `card`
    работают и без него.
    """
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    server = McpServer(index or Path(settings.graph.out), root)
    serve_mcp(server)


@graph_app.command("eval")
def graph_eval(
    questions: Annotated[Path, typer.Argument(help="Файл оценочного набора.")],
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    fail_under: Annotated[
        float,
        typer.Option("--fail-under", help="Вернуть ненулевой код, если полнота ниже."),
    ] = 0.0,
) -> None:
    """Прогнать оценочный набор: полнота и точность ответов.

    Числа идут в журнал при каждом релизе — в том числе при смене версии
    разборщика: обновление бинаря без прогона набора запрещено, это
    и есть регрессионный тест на чужой код.
    """
    path, _ = _open_index(index, config)
    try:
        set_of_questions = load_questions(questions)
    except (OSError, ValueError) as exc:
        typer.echo(f"Набор не прочитан: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    score = run_questions(
        set_of_questions, path, read_index(path), read_meta(path), read_reach(path)
    )
    typer.echo(format_score(score), nl=False)
    if score.recall < fail_under:
        typer.echo(f"Полнота {score.recall} ниже порога {fail_under}", err=True)
        raise typer.Exit(code=1)


@graph_app.command("coverage")
def graph_coverage(
    index: Annotated[
        Path | None, typer.Argument(help="Файл индекса. Без аргумента — `graph.out`.")
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Корень репозитория: от него ищется каталог.")
    ] = Path("."),
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    fail_under: Annotated[
        float,
        typer.Option("--fail-under", help="Ненулевой код, если описано меньше этой доли."),
    ] = 0.0,
) -> None:
    """Какие точки входа описаны бизнес-документами, а какие нет.

    Непокрытые точки входа — состояние работы, а не дефект: отчёт печатается
    всегда и кода возврата не меняет, пока порог не задан явно. Линт, красный
    с первого дня, выключают на второй.
    """
    path, settings = _open_index(index, config)
    catalog_root = root / settings.business_root
    if not catalog_root.is_dir():
        typer.echo(
            f"Каталога бизнес-документов нет: {catalog_root}. "
            "Ключ `business_root` в docpipe.yaml задаёт, где его искать.",
            err=True,
        )
        raise typer.Exit(code=2)

    catalog = load_catalog(root, settings.business_root)
    report = coverage_of(read_index(path).nodes, catalog)
    typer.echo(format_coverage(report), nl=False)
    share = report.covered / report.entry_points if report.entry_points else 1.0
    if share < fail_under:
        typer.echo(f"Описано {share:.0%}, порог {fail_under:.0%}", err=True)
        raise typer.Exit(code=1)


@graph_app.command("pr-check")
def graph_pr_check(
    root: Annotated[Path, typer.Option("--root", help="Корень репозитория с историей git.")],
    index: Annotated[
        Path | None, typer.Option("--index", help="Файл индекса. Без флага — `graph.out`.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
    count: Annotated[int, typer.Option("--count", help="Сколько влитых правок взять.")] = 10,
    scan: Annotated[
        int, typer.Option("--scan", help="Сколько последних правок просмотреть в поиске кода.")
    ] = 100,
    fail_on_missed: Annotated[
        bool,
        typer.Option("--fail-on-missed", help="Ненулевой код, если точка входа пропущена."),
    ] = False,
) -> None:
    """Что предсказал `affects` на уже влитых правках.

    Настоящая истина — «что реально затрагивалось в ревью» — есть только там,
    где есть ревью. Проверяемая её часть есть везде: если правка меняла файл
    точки входа, `affects` обязан эту точку входа назвать. Пропуск здесь —
    ложное отрицание, самый опасный вид ошибки, потому что он тихий.
    """
    path, _ = _open_index(index, config)
    requests = merged_requests(root, count, scan)
    if not requests:
        typer.echo(
            f"В {root} не нашлось истории git — брать нечего. "
            "Проверка работает на репозитории с историей, а не на выгрузке.",
            err=True,
        )
        raise typer.Exit(code=2)

    outcomes = check_requests(requests, read_index(path), read_meta(path), read_reach(path))
    typer.echo(format_requests(outcomes), nl=False)
    if fail_on_missed and any(outcome.missed for outcome in outcomes):
        raise typer.Exit(code=1)


@graph_app.command("info")
def graph_info(
    index: Annotated[
        Path | None,
        typer.Argument(help="Файл индекса. Без аргумента — `graph.out`."),
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Файл конфигурации docpipe.yaml.")
    ] = None,
) -> None:
    """Показать паспорт индекса: чем собран, из чего и что не вошло."""
    try:
        settings = load_config(config)
    except (OSError, ValueError) as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    path = index or Path(settings.graph.out)
    if not path.is_file():
        typer.echo(
            f"Индекса нет: {path}. Соберите: docpipe graph build --root <репозиторий>",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        meta = read_meta(path)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Индекс: {path}")
    typer.echo(f"  схема: {meta.schema_version}, поколение: {meta.generation}")
    typer.echo(f"  репозиторий: {meta.repo}, разборщик: {meta.engine_version}")
    for name, number in sorted(meta.counts.items()):
        typer.echo(f"  {name}: {number}")
    if meta.report:
        typer.echo("  что не вошло:")
        for category, number in sorted(meta.report.items()):
            typer.echo(f"    {category}: {number}")


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


def _registries_path(registries: Path | None, settings: DocpipeConfig, config: Path | None) -> Path:
    """Путь к описанию реестров: флаг важнее конфигурации, но конфигурация читается.

    Раньше команды `anchors` ключ `registries` не читали вовсе — у `list` не было
    даже `--config`. Один и тот же ключ работал в `business *` и молчал здесь,
    так что настроенный репозиторий всё равно требовал флага руками.
    """
    if registries is not None:
        return registries
    if settings.registries:
        return resolve_input(settings.registries, config)
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
        manifest_path, _registries_path(registries, load_config(config), config), root
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
    anchors, _ = _load_anchors(manifest_path, _registries_path(registries, settings, config), root)
    found = [anchor for anchor in anchors if ref in (anchor.ref, anchor.display)]

    if not found:
        typer.echo(f"Якорь не найден: {ref}", err=True)
        raise typer.Exit(code=1)

    ownership_path = ownership_file or (
        resolve_input(settings.ownership, config) if settings.ownership else None
    )
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


def _business_template(directories: list[Path], kind: str) -> str:
    """Скелет по первому кандидату, где он есть.

    Кандидатов несколько, потому что путь в `docpipe.yaml` пишут и от текущего
    каталога, и от самой конфигурации. Перечислять их в отказе обязательно:
    «не найден» с одним путём заставляет гадать, искался ли второй, и это ровно
    тот случай, когда виновата раскладка поставки, а не команда.
    """
    tried = [directory / f"{kind}.md" for directory in directories]
    for path in tried:
        try:
            return path.read_bytes().decode("utf-8-sig")
        except OSError:
            continue

    typer.echo(
        f"Скелет не найден: {', '.join(str(path) for path in tried)}."
        " Каталог берётся из ключа `templates` плюс `business`"
        " — от текущего каталога, затем от каталога docpipe.yaml;"
        " задать явно — флагом --templates",
        err=True,
    )
    raise typer.Exit(code=2)


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
        [templates_dir]
        if templates_dir
        else [
            directory / BUSINESS_TEMPLATES
            for directory in candidate_inputs(settings.templates, config)
        ],
        kind,
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
            state=accepted_block(business_accepted_state(doc, loaded.ctx)),
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
    web_manifest: Path | None = None,
) -> BusinessInputs:
    """Каталог, инвентаризация, контекст разрешения и владение.

    Один сборщик на все команды бизнес-слоя. Разойдись они — `build` собирал бы
    документ по одному набору реестров, а `status` сравнивал бы с другим,
    и документ навсегда остался бы «изменившимся».
    """
    settings = load_config(config)
    anchors, registry_errors = _load_anchors(
        manifest_path, _registries_path(registries_file, settings, config), root
    )
    manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    # Манифест фронта необязателен: репозиторий без фронта его не собирает,
    # и якорей `page` в таком каталоге нет. Путь по умолчанию — `web.out`
    # из конфигурации; файла нет — страниц просто ноль, а не отказ.
    #
    # Второй ступени поиска здесь НЕТ намеренно, хотя это чтение. `web.out` —
    # цель записи `web scan`, и писатель второй ступени получить не может:
    # угадывать, куда писать, инструмент не должен. Дай её одному читателю —
    # и один ключ разошёлся бы на две раскладки: `web scan` пишет в один файл,
    # `business build` читает другой.
    web_path = web_manifest or Path(settings.web.out)
    web = (
        Manifest.model_validate_json(web_path.read_text(encoding="utf-8"))
        if web_path.is_file()
        else None
    )

    # Зато молчать об этом нельзя. «Страниц ноль» — законный исход, и по нему
    # не отличить репозиторий без фронта от прогона из другого каталога.
    # Единственный различимый признак — манифест лежит рядом с конфигурацией:
    # значит, фронт разбирали, а команду позвали не оттуда.
    if web is None and web_manifest is None:
        beside = next(
            (path for path in candidate_inputs(settings.web.out, config)[1:] if path.is_file()),
            None,
        )
        if beside is not None:
            typer.echo(
                f"Манифест фронта не прочитан: {web_path} — страниц будет ноль."
                f" Рядом с docpipe.yaml он есть ({beside}), но ключ `web.out` —"
                " цель записи `web scan` и считается от текущего каталога:"
                " позовите команду оттуда же или задайте --web-manifest.",
                err=True,
            )

    ownership_path = ownership_file or (
        resolve_input(settings.ownership, config) if settings.ownership else None
    )
    try:
        ownership = load_ownership(ownership_path) if ownership_path else None
    except (OSError, ValueError) as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=2) from exc

    where = business_root or settings.business_root
    return BusinessInputs(
        catalog=load_catalog(root, where),
        anchors=anchors,
        ctx=build_resolve_context(anchors, manifest, root=root, ownership=ownership, web=web),
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
    web_manifest: Annotated[
        Path | None,
        typer.Option("--web-manifest", help="Манифест шага `web`. Без него — `web.out`."),
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
        manifest_path, registries_file, root, config, business_root, ownership_file, web_manifest
    )
    report = lint_catalog(loaded.catalog, loaded.anchors, loaded.ctx, loaded.root, loaded.ownership)

    typer.echo(format_lint_report(report, selected, inventory=scope == "all"))
    for error in loaded.registry_errors:
        typer.echo(f"Замечание при чтении реестров: {error}", err=True)

    if report.failing(selected):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
