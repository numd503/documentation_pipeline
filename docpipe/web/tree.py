"""Сквозной прогон шага `web`: исходники фронта -> манифест той же схемы.

Манифест отдельный (`doc-tree.web.json`), схема общая. `materialize`,
`docs status` и бизнес-слой работают от `Manifest` и про язык не знают,
поэтому отдельный файл не меняет ни байта .NET-манифеста и не удваивает
время `scan`.

Модули здесь ключуются **каталогом-границей**, а не файлом объявления
(см. `modules.py`), поэтому собственный сборщик узлов, а не `tree.build_nodes`:
там ключом служит путь `.csproj`, и один многопроектный `angular.json`
склеил бы несколько модулей в один.
"""

import posixpath
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path

from docpipe import __version__
from docpipe.cache import ParseCache
from docpipe.classify import Ruleset, classify, load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.discovery import discover
from docpipe.emit import exclude_globs
from docpipe.hashing import content_hash, stable_hash
from docpipe.model import (
    Dependency,
    DocNode,
    FileParseResult,
    Manifest,
    Module,
    ParserVersions,
    RouteEntry,
    RunMeta,
    SourceSpan,
    Symbol,
    WebCall,
)
from docpipe.route import RewriteRule
from docpipe.tree import doc_path_for, signature_hash
from docpipe.web.calls import CallScan, RawCall, RegistryCall, build_calls, extract_calls
from docpipe.web.modules import (
    WebModule,
    discover_modules,
    load_aliases,
    map_files_to_modules,
)
from docpipe.web.parser import parse_source, parse_tree
from docpipe.web.resolve import (
    ResolveContext,
    TsConfig,
    build_context,
    build_symbol_index,
    compute_closures,
)
from docpipe.web.routes import RouteScan, build_routes


def parser_versions() -> ParserVersions:
    """Версии, попадающие и в манифест, и в ключ кэша.

    Грамматика C# здесь `None` намеренно: манифест фронта про неё ничего
    не знает, а её версия в нём была бы шумом. Заодно это разводит кэши двух
    шагов: общий файл кэша они бы инвалидировали друг другу на каждом прогоне.
    """
    return ParserVersions(
        tree_sitter=package_version("tree-sitter"),
        grammar_typescript=package_version("tree-sitter-typescript"),
    )


@dataclass(frozen=True)
class WebScanResult:
    """Всё, что даёт прогон шага `web`."""

    manifest: Manifest
    meta: RunMeta
    index: dict[str, Symbol]
    calls: CallScan
    routes: RouteScan


@dataclass(frozen=True)
class _Parsed:
    """Разбор одного файла: символы и факты о вызовах из одного дерева."""

    result: FileParseResult
    calls: list[RawCall]


def _parse_files(root: Path, relatives: list[str], cache: ParseCache | None) -> list[_Parsed]:
    """Разобрать файлы, переиспользуя кэш символов.

    Кэшируются только символы: факты о вызовах в `FileParseResult` не входят,
    и разбирать ради них файл заново дешевле, чем заводить второй формат
    хранения, который придётся держать согласованным с первым.
    """
    parsed: list[_Parsed] = []
    for relative in relatives:
        source = (root / relative).read_bytes()
        tree = parse_tree(source)
        cached = cache.get(relative, content_hash(source)) if cache else None
        result = cached if cached is not None else parse_source(source, relative, tree=tree)
        if cache is not None and cached is None:
            cache.put(result)
        parsed.append(_Parsed(result=result, calls=extract_calls(tree.root_node, relative)))

    parsed.sort(key=lambda item: item.result.path)
    return parsed


def _registry_calls(config: DocpipeConfig) -> list[RegistryCall]:
    return [
        RegistryCall(
            route=item.route,
            discriminator_in=item.discriminator.where,
            name=item.discriminator.name,
            kind=item.kind,
        )
        for item in config.web.registry_calls
    ]


def _rewrite_for(module: WebModule, config: DocpipeConfig) -> RewriteRule | None:
    """Правило преобразования URL для модуля. `None` — модуль не настроен.

    Различие существенное: пустые поля значат «проверено, преобразования нет»,
    а отсутствие записи — «настройку забыли». Второе обязано печататься
    в отчёте связи, иначе оно выглядит как исправная связь.
    """
    rule = config.web.rewrite_for(module.module.name)
    if rule is None:
        return None
    return RewriteRule(
        module=rule.module, strip_prefix=rule.strip_prefix, add_prefix=rule.add_prefix
    )


def _calls_by_file(
    parsed: list[_Parsed], modules: list[WebModule], config: DocpipeConfig
) -> tuple[CallScan, dict[str, list[WebCall]]]:
    """Вызовы, разобранные по правилам своего модуля.

    Правило берётся по модулю файла, а не одно на прогон: у семи фронтов
    репозитория семь разных `pathRewrite`, и общий ключ склеил бы их маршруты.
    """
    by_module: dict[str, list[RawCall]] = {}
    module_of_file = map_files_to_modules([item.result.path for item in parsed], modules)
    for item in parsed:
        key = module_of_file.get(item.result.path)
        if key is not None:
            by_module.setdefault(key, []).extend(item.calls)

    registry = _registry_calls(config)
    calls: list[WebCall] = []
    unresolved: list[RawCall] = []
    registry_unresolved: list[WebCall] = []

    for module in modules:
        scan = build_calls(
            by_module.get(module.key, []),
            rewrite=_rewrite_for(module, config),
            registry=registry,
        )
        calls.extend(scan.calls)
        unresolved.extend(scan.unresolved)
        registry_unresolved.extend(scan.registry_unresolved)

    grouped: dict[str, list[WebCall]] = {}
    for call in calls:
        grouped.setdefault(call.file, []).append(call)

    return (
        CallScan(calls=calls, unresolved=unresolved, registry_unresolved=registry_unresolved),
        grouped,
    )


def _with_templates(root: Path, index: dict[str, Symbol]) -> dict[str, Symbol]:
    """Дописать компонентам их `.html`: в источники и в `impl_hash`.

    Переписанный экран обязан помечать документ устаревшим, а правка `.scss`
    — нет: стили смысла документа не меняют. Поэтому в хэш входит **шаблон**
    и только он.

    Путь шаблона попадает в `sources` рядом с `.ts`, а не в отдельное поле:
    шаблон и есть часть исходника компонента, а список источников агент шага 3
    и так читает — он же печатается в генерируемом блоке документа.
    """
    updated: dict[str, Symbol] = {}
    for key, symbol in index.items():
        template = next(
            (
                attribute.named_args["templateUrl"]
                for attribute in symbol.attributes
                if "templateUrl" in attribute.named_args
            ),
            None,
        )
        source = symbol.sources[0].path if symbol.sources else ""
        relative = _normalize(posixpath.dirname(source), template) if template else ""
        path = root / relative
        if not relative or not path.is_file():
            updated[key] = symbol
            continue

        content = path.read_bytes()
        updated[key] = symbol.model_copy(
            update={
                "sources": [
                    *symbol.sources,
                    SourceSpan(path=relative, start=1, end=max(1, content.count(b"\n") + 1)),
                ],
                "impl_hash": stable_hash([symbol.impl_hash, content_hash(content)]),
            }
        )
    return updated


def _normalize(directory: str, relative: str) -> str:
    joined = posixpath.join(directory, relative) if directory else relative
    return posixpath.normpath(joined)


def _routes_by_component(routes: RouteScan) -> dict[str, list[RouteEntry]]:
    grouped: dict[str, list[RouteEntry]] = {}
    for entry in routes.entries:
        grouped.setdefault(entry.component, []).append(entry)
    return grouped


# Повышения вида, которые движок правил сделать не может: он видит только
# `Symbol`, а таблица роутов и HTTP-вызовы разбираются отдельно и межфайлово.
PROMOTIONS: dict[str, tuple[str, str]] = {
    "component": ("page", "page"),
    "service": ("api-service", "api-service"),
}


def _promote(
    kind: str, template: str, calls: list[WebCall], routes: list[RouteEntry]
) -> tuple[str, str]:
    """Уточнить вид по фактам, которых нет в символе.

    Компонент, до которого можно дойти по таблице роутов, — страница; сервис,
    который делает HTTP-вызовы, — сервис API. Выразить это предикатом по имени
    или пути можно было бы только угадыванием, а угаданная страница ломает
    якорь бизнес-документа: он пишется на маршрут, которого у неё нет.

    Правило остаётся в `matched_rules` как есть: там записано, что сработало,
    а не что получилось. Иначе по отчёту нельзя понять, какое правило настраивать.
    """
    if kind == "component" and routes:
        return PROMOTIONS["component"]
    if kind == "service" and calls:
        return PROMOTIONS["service"]
    return kind, template


def _parameter_types(signature: str) -> list[str]:
    """Типы параметров конструктора TypeScript: `constructor(private http: HttpClient)`.

    Порядок в TS обратный C#: там `Тип имя`, здесь `имя: Тип`. Общую функцию
    из `tree.parameter_types` переиспользовать нельзя — она разберёт `private`
    как тип и выдаст зависимость от модификатора.

    Дженерик и объединение типов срезаются по первому небуквенному символу:
    `Store<AppState>` — зависимость от `Store`, а не от его параметра.
    """
    inner = signature.partition("(")[2].rpartition(")")[0]
    if not inner.strip():
        return []

    found: list[str] = []
    depth = 0
    current: list[str] = []
    for char in inner:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth -= 1
        if char == "," and depth == 0:
            found.append("".join(current))
            current = []
            continue
        current.append(char)
    found.append("".join(current))

    types: list[str] = []
    for part in found:
        annotation = part.partition(":")[2].strip()
        name = re.split(r"[^\w$.]", annotation, maxsplit=1)[0] if annotation else ""
        if name:
            types.append(name)
    return types


def _dependencies(symbol: Symbol, by_name: dict[str, str]) -> list[Dependency]:
    """Зависимости узла: параметры конструктора.

    В Angular внедрение конструкторное — `inject(…)` встречается на боевом
    модуле девять раз против сотен конструкторов, — поэтому другого источника
    зависимостей на фронте практически нет.
    """
    found: list[Dependency] = []
    for member in symbol.members:
        if member.kind != "constructor":
            continue
        for name in _parameter_types(member.signature):
            target = by_name.get(name)
            found.append(
                Dependency(
                    target=target or name,
                    via="constructor",
                    confidence="high" if target else "low",
                )
            )

    seen: dict[tuple[str, str], Dependency] = {}
    for item in found:
        seen.setdefault((item.target, item.via), item)
    return [seen[key] for key in sorted(seen)]


def build_nodes(
    index: dict[str, Symbol],
    modules: list[WebModule],
    ruleset: Ruleset,
    config: DocpipeConfig,
    calls: dict[str, list[WebCall]],
    routes: dict[str, list[RouteEntry]],
) -> tuple[list[Module], list[DocNode]]:
    """Собрать модули и узлы документации фронта.

    Вызовы попадают на узел **того файла, где записаны**: связь строит сервис,
    а не страница, и приписать их странице значило бы соврать о том, кто зовёт.
    Путь «страница → эндпоинт» собирает F13 по цепочке, а не подменой автора.
    """
    configured = [module.module for module in modules]
    by_key = {module.key: module.module for module in modules}

    # Простое имя -> FQN: в сигнатуре конструктора тип записан так, как в коде.
    # Побеждает лексикографически меньший FQN — не порядок обхода.
    by_name: dict[str, str] = {}
    for symbol in sorted(index.values(), key=lambda item: item.fqn):
        by_name.setdefault(symbol.name, symbol.fqn)

    nodes: list[DocNode] = []
    for key in sorted(index):
        symbol = index[key]
        module = by_key.get(symbol.module)
        if module is None or not module.enrolled:
            continue

        classification = classify(symbol, ruleset)
        if classification is None:
            continue

        files = {source.path for source in symbol.sources}
        node_calls = sorted(
            (call for path in files for call in calls.get(path, [])),
            key=lambda call: (call.file, call.line, call.key.route),
        )
        node_routes = sorted(
            routes.get(symbol.fqn, []), key=lambda entry: (entry.path, entry.component)
        )
        kind, template = _promote(
            classification.kind, classification.template, node_calls, node_routes
        )

        nodes.append(
            DocNode(
                id=f"type:{key}",
                kind=kind,
                template=template,
                title=symbol.name,
                doc_path=doc_path_for(
                    module.name,
                    kind,
                    symbol.name,
                    config.doc_layout,
                    config.modules_root,
                ),
                parent=module.id,
                module=module.name,
                domain=module.domain or module.name,
                symbol=symbol,
                dependencies=_dependencies(symbol, by_name),
                matched_rules=classification.matched_rules,
                signature_hash=signature_hash(symbol),
                impl_hash=symbol.impl_hash,
                web_calls=node_calls,
                routes=node_routes,
            )
        )

    return configured, sorted(nodes, key=lambda node: node.id)


def run(
    root: Path,
    config: DocpipeConfig | None = None,
    ruleset: Ruleset | None = None,
    cache_dir: Path | None = None,
) -> WebScanResult:
    """Прогон шага `web`: исходники -> манифест, метаданные и срезы."""
    started = time.perf_counter()
    config = config or DocpipeConfig()
    ruleset = ruleset or load_ruleset(Path(config.web.rules), "web")
    versions = parser_versions()

    found = discover(root, exclude_globs(config), roots=config.web.roots)
    modules = discover_modules(root, found)

    cache = ParseCache(cache_dir / "parse-web.sqlite", versions) if cache_dir else None
    try:
        parsed = _parse_files(root, found.ts_files, cache)
        if cache is not None:
            cache.prune(set(found.ts_files))
    finally:
        if cache is not None:
            cache.close()

    results = [item.result for item in parsed]
    context = _context(root, results, modules)
    file_to_module = map_files_to_modules([result.path for result in results], modules)
    index = _with_templates(
        root, compute_closures(build_symbol_index(results, file_to_module, context))
    )

    calls, calls_by_file = _calls_by_file(parsed, modules, config)
    sources = {relative: (root / relative).read_bytes() for relative in found.ts_files}
    routes = build_routes(sources, context)

    configured, nodes = build_nodes(
        index, modules, ruleset, config, calls_by_file, _routes_by_component(routes)
    )

    manifest = Manifest(
        ruleset_version=ruleset.ruleset_version,
        parser=versions,
        modules=configured,
        nodes=nodes,
    )

    broken = sorted(
        result.path for result in results if result.parse_errors and not result.declarations
    )
    meta = RunMeta(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        host=socket.gethostname(),
        duration_seconds=round(time.perf_counter() - started, 3),
        docpipe_version=__version__,
        stats={
            "files": len(results),
            "modules": len(configured),
            "symbols": len(index),
            "nodes": len(nodes),
            "calls_resolved": len(calls.calls),
            "calls_unresolved": len(calls.unresolved),
            "registry_unresolved": len(calls.registry_unresolved),
            "routes": len(routes.entries),
            "routes_unresolved": routes.unresolved,
        },
        parse_error_files=broken,
    )
    return WebScanResult(manifest=manifest, meta=meta, index=index, calls=calls, routes=routes)


def _context(
    root: Path, results: list[FileParseResult], modules: list[WebModule]
) -> ResolveContext:
    """Контекст резолва: таблицы алиасов всех модулей, слитые в одну.

    Одна таблица на прогон, а не по модулю. Разделение потребовало бы
    отдельного индекса символов на каждый модуль, и наследование через общий
    каталог перестало бы резолвиться — а именно так устроены `libs/` у nx.

    Цели алиасов уже репо-относительные, поэтому слияние безопасно: одинаковое
    имя в двух workspace указывает на разные пути, и побеждает модуль
    с меньшим ключом — детерминированно, а не по порядку обхода.
    """
    paths: dict[str, list[str]] = {}
    base_url = ""
    for module in sorted(modules, key=lambda item: item.key):
        table = load_aliases(root, module)
        base_url = base_url or table.base_url
        for pattern, targets in table.paths.items():
            paths.setdefault(pattern, targets)

    return build_context(results, TsConfig(base_url=base_url, paths=dict(sorted(paths.items()))))
