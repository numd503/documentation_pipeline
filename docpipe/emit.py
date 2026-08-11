"""Сквозной прогон и запись артефактов.

Здесь собирается вся цепочка: обход, разбор через кэш, индекс символов,
классификация, дерево. Результат — два файла: детерминированный манифест
и сидкар с метаданными прогона.

Разделение на два файла — не косметика. Пока время, хост и длительность лежат
отдельно, проверка воспроизводимости сводится к побайтовому сравнению манифеста,
без логики «сравнить, игнорируя такие-то поля».
"""

import socket
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path

from docpipe import __version__
from docpipe.cache import ParseCache
from docpipe.classify import Ruleset, load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.discovery import discover, in_scope, normalize_scope
from docpipe.dotnet.csproj import parse_csproj, resolve_references
from docpipe.dotnet.endpoints import extract_endpoints
from docpipe.dotnet.parser import parse_source
from docpipe.dotnet.resolve import build_symbol_index, compute_closures
from docpipe.hashing import content_hash, stable_json_dumps
from docpipe.merge import merge_manifests, node_in_scope
from docpipe.model import (
    DocNode,
    FileParseResult,
    Manifest,
    Module,
    ParserVersions,
    RunMeta,
    Symbol,
)
from docpipe.stats import Stats, collect_stats, kind_counts
from docpipe.tree import build_nodes

DEFAULT_EXCLUDE = [
    "**/obj/**",
    "**/bin/**",
    "**/*.g.cs",
    # Выходные и вендоренные каталоги фронта. `node_modules` — десятки тысяч
    # файлов, и это единственная причина, по которой обход фронта вообще
    # укладывается в разумное время; остальные три хранят копии исходников,
    # из-за которых один и тот же символ объявился бы дважды.
    "**/node_modules/**",
    "**/dist/**",
    "**/.angular/**",
    "**/coverage/**",
]


def exclude_globs(config: DocpipeConfig) -> list[str]:
    """Шаблоны обхода: встроенные плюс заданные в конфигурации.

    Складываются, а не замещаются. Замещение означало бы, что человек, дописавший
    `exclude: ["docs/**"]`, молча начинает документировать `obj/` и `bin/` —
    ошибка, которая выглядит как успех и обнаруживается только по раздутому
    манифесту.

    Порядок для результата безразличен (проверка — «совпал хотя бы один»),
    но список всё равно отсортирован: он попадает человеку на глаза в диагностике.
    """
    return sorted({*DEFAULT_EXCLUDE, *config.exclude})


def parser_versions() -> ParserVersions:
    """Версии грамматики. Попадают в манифест: их апгрейд может законно изменить вывод."""
    return ParserVersions(
        tree_sitter=package_version("tree-sitter"),
        grammar_c_sharp=package_version("tree-sitter-c-sharp"),
    )


def run_meta_path(out: Path) -> Path:
    """`artifacts/doc-tree.json` -> `artifacts/doc-tree.run.json`."""
    return out.with_suffix(".run.json")


def write_manifest(manifest: Manifest, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(stable_json_dumps(manifest.model_dump(mode="json")), encoding="utf-8")


def write_run_meta(meta: RunMeta, out: Path) -> None:
    path = run_meta_path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(meta.model_dump(mode="json")), encoding="utf-8")


# --------------------------------------------------------------------------------------
# Привязка файлов к модулям
# --------------------------------------------------------------------------------------


def map_files_to_modules(cs_files: list[str], csproj_files: list[str]) -> dict[str, str]:
    """Файл -> `.csproj` ближайшего вверх по дереву проекта.

    Файлы, не попавшие ни в один проект, в результат не входят: документировать
    код вне проектов некуда. Такое встречается — общий код, подключённый через
    `<Compile Include>`, физически лежит вне каталогов проектов (см. T05b).
    """
    directories = {str(Path(c).parent): c for c in csproj_files}

    mapping: dict[str, str] = {}
    for relative in cs_files:
        current = Path(relative).parent
        while True:
            if str(current) in directories:
                mapping[relative] = directories[str(current)]
                break
            if current == Path("."):
                break
            current = current.parent
    return mapping


# --------------------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------------------


def _parse_one(item: tuple[str, bytes]) -> FileParseResult:
    """Разбор в отдельном процессе. Верхнеуровневая функция — иначе не сериализуется."""
    path, source = item
    return parse_source(source, path)


def parse_files(
    root: Path,
    relatives: list[str],
    cache: ParseCache | None,
    jobs: int = 1,
) -> list[FileParseResult]:
    """Разобрать файлы, переиспользуя кэш. Результат отсортирован по пути.

    Сортировка обязательна: при `--jobs > 1` порядок завершения задач случаен,
    а от порядка `results` зависит слияние `partial`-объявлений на T10.
    """
    results: list[FileParseResult] = []
    pending: list[tuple[str, bytes]] = []

    for relative in relatives:
        source = (root / relative).read_bytes()
        cached = cache.get(relative, content_hash(source)) if cache else None
        if cached is not None:
            results.append(cached)
        else:
            pending.append((relative, source))

    if pending and jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            parsed = list(pool.map(_parse_one, pending))
    else:
        parsed = [_parse_one(item) for item in pending]

    for result in parsed:
        if cache is not None:
            cache.put(result)
    results.extend(parsed)

    results.sort(key=lambda result: result.path)
    return results


# --------------------------------------------------------------------------------------
# Сквозной прогон
# --------------------------------------------------------------------------------------


def _outside_scope_results(
    cache: ParseCache | None, scope: list[tuple[str, ...]] | None
) -> tuple[list[FileParseResult], list[str]]:
    """Разбор файлов вне скоупа — целиком из кэша, без сверки хэша.

    Сверять не с чем: файл не читался, его содержимое неизвестно. Именно
    поэтому такой манифест помечается `partial` — данные вне скоупа могли
    устареть.

    Без этого шага индекс символов был бы неполным, и тип из скоупа, который
    наследуется от базового класса вне скоупа, потерял бы классификацию.

    Отбор идёт **по принадлежности пути скоупу**, а не по списку найденных
    файлов. Разница видна на удалении: файл внутри скоупа обходом уже не
    находится, но в кэше ещё лежит, и правило «чего не нашли, то возьмём
    из кэша» воскресило бы удалённый тип.
    """
    if cache is None:
        return [], []

    restored: list[FileParseResult] = []
    missing: list[str] = []
    for path in cache.all_paths():
        if in_scope(path, scope):
            continue
        cached = cache.get_any(path)
        if cached is None:
            missing.append(path)
        else:
            restored.append(cached)
    return restored, missing


@dataclass(frozen=True)
class ScanResult:
    """Всё, что даёт прогон. `stats` не входит в сидкар: срезы там не нужны.

    `index` — полный индекс символов, включая неклассифицированные и вне области.
    В манифест они не попадают по определению, поэтому выборка символов
    (`docpipe symbols`) без него невозможна: спрашивать «что осталось без решения»
    у файла, куда попадает только решённое, бессмысленно.
    """

    manifest: Manifest
    meta: RunMeta
    stats: Stats
    index: dict[str, Symbol]


def scan(
    root: Path,
    config: DocpipeConfig | None = None,
    ruleset: Ruleset | None = None,
    cache_dir: Path | None = None,
    jobs: int = 1,
    scope: list[str] | None = None,
    previous: Manifest | None = None,
) -> tuple[Manifest, RunMeta]:
    """Прогон: манифест и метаданные. Обёртка над `run` для частого случая."""
    result = run(root, config, ruleset, cache_dir, jobs, scope, previous)
    return result.manifest, result.meta


def run(
    root: Path,
    config: DocpipeConfig | None = None,
    ruleset: Ruleset | None = None,
    cache_dir: Path | None = None,
    jobs: int = 1,
    scope: list[str] | None = None,
    previous: Manifest | None = None,
) -> ScanResult:
    """Прогон: исходники -> манифест, метаданные прогона и статистика.

    С `scope` обход и разбор ограничены указанными каталогами, символы вне
    скоупа берутся из кэша, а узлы вне скоупа переносятся из `previous`.
    """
    started = time.perf_counter()
    config = config or DocpipeConfig()
    ruleset = ruleset or load_ruleset(Path(config.rules))
    versions = parser_versions()

    found = discover(root, exclude_globs(config), scope, config.roots)
    modules: list[Module] = resolve_references(
        [parse_csproj(root / relative, root) for relative in found.csproj_files]
    )

    cache = ParseCache(cache_dir / "parse.sqlite", versions) if cache_dir else None
    outside: list[FileParseResult] = []
    missing: list[str] = []
    try:
        results = parse_files(root, found.cs_files, cache, jobs)
        if scope is None:
            if cache is not None:
                cache.prune(set(found.cs_files))
        else:
            # Чистить кэш в скоуп-режиме нельзя: файлов вне скоупа мы не видели,
            # и `prune` снёс бы ровно то, ради чего кэш здесь и нужен.
            outside, missing = _outside_scope_results(cache, normalize_scope(scope))
    finally:
        if cache is not None:
            cache.close()

    # Модули вне скоупа известны только из предыдущего манифеста — обход туда
    # не заходил. Без них файлы вне скоупа не привязались бы к проектам.
    known_csproj = sorted(
        {
            *found.csproj_files,
            *(module.project_file for module in (previous.modules if previous else [])),
        }
    )
    all_results = sorted(results + outside, key=lambda result: result.path)
    file_to_module = map_files_to_modules([r.path for r in all_results], known_csproj)
    index = compute_closures(build_symbol_index(all_results, file_to_module))

    configured, nodes = build_nodes(
        index,
        modules,
        ruleset,
        config,
        [registration for result in all_results for registration in result.di_registrations],
        {key: extract_endpoints(symbol) for key, symbol in index.items()},
    )

    manifest = Manifest(
        ruleset_version=ruleset.ruleset_version,
        parser=versions,
        modules=configured,
        nodes=nodes,
    )

    if scope is not None:
        normalized = normalize_scope(scope)
        manifest = merge_manifests(
            previous or Manifest(ruleset_version=ruleset.ruleset_version, parser=versions),
            manifest.model_copy(
                update={"nodes": [n for n in nodes if node_in_scope(n, normalized)]}
            ),
            scope,
        )

    # Файл с ошибками разбора и без единого объявления — признак того, что тип
    # уничтожен директивой препроцессора внутри выражения. Снаружи это ничем
    # больше не видно, поэтому список идёт в сидкар, а не просто счётчик.
    broken = sorted(
        result.path for result in results if result.parse_errors and not result.declarations
    )

    statistics = collect_stats(
        index,
        manifest.nodes,
        ruleset,
        {module.project_file for module in configured if module.enrolled},
    )
    meta = RunMeta(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        host=socket.gethostname(),
        duration_seconds=round(time.perf_counter() - started, 3),
        docpipe_version=__version__,
        stats=_counters(all_results, statistics, manifest.nodes, configured)
        | (
            {"restored_from_cache": len(outside), "missing_from_cache": len(missing)}
            if scope
            else {}
        ),
        parse_error_files=broken,
    )
    return ScanResult(manifest=manifest, meta=meta, stats=statistics, index=index)


def _counters(
    results: list[FileParseResult],
    statistics: Stats,
    nodes: list[DocNode],
    modules: list[Module],
) -> dict[str, int]:
    """Плоские счётчики для сидкара. Срезы туда не идут: они для человека, не для файла.

    `documented` дублирует сумму по видам намеренно: сидкар читают глазами и
    из скриптов, и складывать виды вручную, чтобы сверить с `undecided`, значит
    каждый раз заново решать, какие ключи считаются видами, а какие служебные.
    """
    kinds = kind_counts(statistics)
    return {
        "files": len(results),
        "parse_errors": sum(result.parse_errors for result in results),
        "declarations": sum(len(result.declarations) for result in results),
        "symbols": statistics.total,
        "modules": len(modules),
        "modules_enrolled": sum(1 for module in modules if module.enrolled),
        "nodes": len(nodes),
        "documented": sum(kinds.values()),
        **statistics.counts,
    }
