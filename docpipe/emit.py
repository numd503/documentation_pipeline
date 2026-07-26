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
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path

from docpipe import __version__
from docpipe.cache import ParseCache
from docpipe.classify import Ruleset, classify, is_excluded, load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.discovery import discover
from docpipe.dotnet.csproj import parse_csproj, resolve_references
from docpipe.dotnet.endpoints import extract_endpoints
from docpipe.dotnet.parser import parse_source
from docpipe.dotnet.resolve import build_symbol_index, compute_closures
from docpipe.hashing import content_hash, stable_json_dumps
from docpipe.model import (
    DocNode,
    FileParseResult,
    Manifest,
    Module,
    ParserVersions,
    RunMeta,
    Symbol,
)
from docpipe.tree import build_nodes

DEFAULT_EXCLUDE = ["**/obj/**", "**/bin/**", "**/*.g.cs"]


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


def scan(
    root: Path,
    config: DocpipeConfig | None = None,
    ruleset: Ruleset | None = None,
    cache_dir: Path | None = None,
    jobs: int = 1,
) -> tuple[Manifest, RunMeta]:
    """Полный прогон: исходники -> манифест и метаданные прогона."""
    started = time.perf_counter()
    config = config or DocpipeConfig()
    ruleset = ruleset or load_ruleset(Path(config.rules))
    versions = parser_versions()

    found = discover(root, DEFAULT_EXCLUDE)
    modules: list[Module] = resolve_references(
        [parse_csproj(root / relative, root) for relative in found.csproj_files]
    )

    cache = ParseCache(cache_dir / "parse.sqlite", versions) if cache_dir else None
    try:
        results = parse_files(root, found.cs_files, cache, jobs)
        if cache is not None:
            cache.prune(set(found.cs_files))
    finally:
        if cache is not None:
            cache.close()

    file_to_module = map_files_to_modules(found.cs_files, found.csproj_files)
    index = compute_closures(build_symbol_index(results, file_to_module))

    configured, nodes = build_nodes(
        index,
        modules,
        ruleset,
        config,
        [registration for result in results for registration in result.di_registrations],
        {key: extract_endpoints(symbol) for key, symbol in index.items()},
    )

    manifest = Manifest(
        ruleset_version=ruleset.ruleset_version,
        parser=versions,
        modules=configured,
        nodes=nodes,
    )

    # Файл с ошибками разбора и без единого объявления — признак того, что тип
    # уничтожен директивой препроцессора внутри выражения. Снаружи это ничем
    # больше не видно, поэтому список идёт в сидкар, а не просто счётчик.
    broken = sorted(
        result.path for result in results if result.parse_errors and not result.declarations
    )

    meta = RunMeta(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        host=socket.gethostname(),
        duration_seconds=round(time.perf_counter() - started, 3),
        docpipe_version=__version__,
        stats=_stats(results, index, nodes, configured, ruleset),
        parse_error_files=broken,
    )
    return manifest, meta


def _stats(
    results: list[FileParseResult],
    index: dict[str, Symbol],
    nodes: list[DocNode],
    modules: list[Module],
    ruleset: Ruleset,
) -> dict[str, int]:
    excluded = sum(1 for symbol in index.values() if is_excluded(symbol, ruleset))
    classified = sum(
        1
        for symbol in index.values()
        if not is_excluded(symbol, ruleset) and classify(symbol, ruleset)
    )
    return {
        "files": len(results),
        "parse_errors": sum(result.parse_errors for result in results),
        "declarations": sum(len(result.declarations) for result in results),
        "symbols": len(index),
        "excluded": excluded,
        "classified": classified,
        "unclassified": len(index) - excluded - classified,
        "modules": len(modules),
        "modules_enrolled": sum(1 for module in modules if module.enrolled),
        "nodes": len(nodes),
    }
