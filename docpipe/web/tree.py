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
import socket
import time
from collections import Counter
from dataclasses import dataclass, field
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
    Usage,
    WebCall,
)
from docpipe.route import RewriteRule
from docpipe.tree import doc_path_for, signature_hash
from docpipe.web.absorb import FEATURE_KIND, PAGE_KIND, absorb
from docpipe.web.calls import CallScan, RawCall, RegistryCall, build_calls, extract_calls
from docpipe.web.members import MemberRanges, member_ranges
from docpipe.web.modules import (
    WebModule,
    discover_modules,
    load_aliases,
    map_files_to_modules,
)
from docpipe.web.overrides import (
    MANUAL_SOURCE,
    MANUAL_TABLE,
    Feature,
    OverrideReport,
    Overrides,
    StaleRule,
)
from docpipe.web.parser import parse_source, parse_tree
from docpipe.web.resolve import (
    ResolveContext,
    TsConfig,
    build_context,
    build_symbol_index,
    compute_closures,
    module_fqn,
    resolve_name,
)
from docpipe.web.routes import RouteScan, build_routes
from docpipe.web.store import (
    RawDispatch,
    RawSelect,
    action_types,
    extract_dispatches,
    extract_selects,
)
from docpipe.web.templates import TEMPLATE_MEMBER, template_of
from docpipe.web.templates import calls as template_calls
from docpipe.web.usage import (
    RawUsage,
    constructor_bindings,
    extract_usages,
    injected_fields,
    parameter_types,
    typed_fields,
)


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
    overrides: OverrideReport = field(default_factory=OverrideReport)


@dataclass(frozen=True)
class _Parsed:
    """Разбор одного файла: символы и факты из одного дерева.

    Всё, что здесь перечислено, берётся за один проход по дереву: второй
    разбор того же файла ради соседнего факта стоит столько же, сколько первый.
    """

    result: FileParseResult
    calls: list[RawCall]
    usages: list[RawUsage]
    injected: list[tuple[int, str, str]]
    fields: list[tuple[int, str, str]]
    dispatches: list[RawDispatch]
    selects: list[RawSelect]
    action_types: dict[str, str]


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
        parsed.append(
            _Parsed(
                result=result,
                calls=extract_calls(tree.root_node, relative),
                usages=extract_usages(tree.root_node, relative),
                injected=injected_fields(tree.root_node),
                fields=typed_fields(tree.root_node),
                dispatches=extract_dispatches(tree.root_node, relative),
                selects=extract_selects(tree.root_node, relative),
                action_types=action_types(tree.root_node),
            )
        )

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
    parsed: list[_Parsed],
    modules: list[WebModule],
    config: DocpipeConfig,
    ranges: MemberRanges,
    action_of_member: dict[tuple[str, str], str],
) -> tuple[CallScan, dict[str, list[WebCall]]]:
    """Вызовы, разобранные по правилам своего модуля.

    Правило берётся по модулю файла, а не одно на прогон: у семи фронтов
    репозитория семь разных `pathRewrite`, и общий ключ склеил бы их маршруты.

    Здесь же вызову проставляется член, в котором он записан: дальше по этому
    полю страница отличит «зову метод, который ходит вот сюда» от «внедрил
    сервис, у которого есть такой метод». Вызов из члена, помеченного `@Action`,
    получает и тип экшена: страница до него дошла диспатчем, а не вызовом.
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

        def with_member(call: WebCall) -> WebCall:
            member = ranges.of(call.file, call.line)
            return call.model_copy(
                update={
                    "member": member,
                    "via_action": action_of_member.get((call.file, member)),
                }
            )

        calls.extend(with_member(call) for call in scan.calls)
        unresolved.extend(scan.unresolved)
        # Тот же список проходит ту же обработку: `registry_unresolved` —
        # подмножество `calls`, и разное наполнение полей у одного вызова
        # в двух списках читалось бы как два разных вызова.
        registry_unresolved.extend(with_member(call) for call in scan.registry_unresolved)

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


def _own_bindings(symbol: Symbol, parsed_by_file: dict[str, _Parsed]) -> dict[str, str]:
    """Пары «имя получателя -> имя типа», объявленные в самом классе.

    Три источника, и все три однозначны: параметр конструктора, поле
    с `inject(…)` и поле с аннотацией типа. Последнее добавлено по замеру
    на боевом модуле — там 63 % обращений к членам не разрешались, и половина
    из них это поля, присвоенные в `ngOnInit` или пришедшие через `@Input()`.

    Поля берутся по диапазону символа: два класса в одном файле законно
    объявляют одноимённое поле с разными типами.
    """
    if not symbol.sources:
        return {}
    parsed = parsed_by_file.get(symbol.sources[0].path)
    if parsed is None:
        return {}

    span = symbol.sources[0]
    bindings: dict[str, str] = {}
    for member in symbol.members:
        if member.kind == "constructor":
            for name, type_name in constructor_bindings(member.signature):
                bindings.setdefault(name, type_name)
    for line, name, type_name in [*parsed.injected, *parsed.fields]:
        if span.start <= line <= span.end:
            bindings.setdefault(name, type_name)
    return bindings


def _bindings_of(
    symbol: Symbol, parsed_by_file: dict[str, _Parsed], by_fqn: dict[str, Symbol]
) -> dict[str, str]:
    """Связывания класса вместе с унаследованными.

    `this.http` в сервисе-наследнике объявлено в базовом классе, и без обхода
    замыкания наследования такое обращение остаётся неразрешённым. В Angular
    базовый компонент с внедрёнными зависимостями — обычная форма, а на боевом
    модуле сервисы поголовно наследуют `BaseApiService`.

    Своё сильнее унаследованного: имя, переопределённое в наследнике, значит
    его тип, а не тип базы.
    """
    bindings = _own_bindings(symbol, parsed_by_file)
    for base in symbol.base_type_closure:
        parent = by_fqn.get(base)
        if parent is None:
            continue
        for name, type_name in _own_bindings(parent, parsed_by_file).items():
            bindings.setdefault(name, type_name)
    return bindings


def _usages_of(
    symbol: Symbol,
    parsed_by_file: dict[str, _Parsed],
    ranges: MemberRanges,
    ctx: ResolveContext,
    by_fqn: dict[str, Symbol],
) -> tuple[list[Usage], Counter[str]]:
    """Рёбра графа вызовов, исходящие из символа, и счётчики того, что не вышло.

    Тип получателя берётся из **своего** класса: пары «имя -> тип» собираются
    из конструктора и из полей `inject(…)`, попавших в диапазон символа. Таблица
    на файл подставила бы чужой тип там, где два класса в одном файле объявляют
    одноимённое поле, — молча и правдоподобно.

    Имя типа разрешается таблицей импортов файла, а не глобальным индексом
    простых имён: одноимённые классы в разных каталогах — норма для TypeScript,
    где единица изоляции файл.
    """
    if not symbol.sources:
        return [], Counter()

    file = symbol.sources[0].path
    parsed = parsed_by_file.get(file)
    if parsed is None:
        return [], Counter()

    span = symbol.sources[0]
    bindings = _bindings_of(symbol, parsed_by_file, by_fqn)

    found: dict[tuple[str, str, str], Usage] = {}
    counters: Counter[str] = Counter()
    for usage in parsed.usages:
        if not span.start <= usage.line <= span.end:
            continue

        bound = bindings.get(usage.receiver)
        if bound is None:
            # Локальная переменная, поле без типа, чужая цепочка. Ложное ребро
            # втянуло бы в страницу посторонний сервис, поэтому только счётчик.
            counters["unresolved"] += 1
            continue

        declared = resolve_name(bound, file, ctx)
        target = f"{module_fqn(declared)}.{bound}" if declared else ""
        if not target or target not in by_fqn:
            # `HttpClient`, `Router`, `Store` — внешние типы. Это не пробел
            # разбора: у них нет узла документации, и ребро вести некуда.
            counters["external"] += 1
            continue

        counters["resolved"] += 1
        key = (target, usage.method, ranges.of(file, usage.line))
        found.setdefault(key, Usage(target=key[0], member=key[1], via=key[2]))

    return [found[key] for key in sorted(found)], counters


def _dependencies(symbol: Symbol, by_name: dict[str, str]) -> list[Dependency]:
    """Зависимости узла: параметры конструктора.

    В Angular внедрение конструкторное — `inject(…)` встречается на боевом
    модуле девять раз против сотен конструкторов, — поэтому другого источника
    зависимостей на фронте практически нет.

    Разбор сигнатуры живёт в `web/usage.py` и общий с графом вызовов: две копии
    разошлись бы на первой же форме параметра, и зависимости узла перестали бы
    сходиться с его рёбрами.
    """
    found: list[Dependency] = []
    for member in symbol.members:
        if member.kind != "constructor":
            continue
        for name in parameter_types(member.signature):
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


def _resolved_type(name: str, file: str, ctx: ResolveContext) -> str:
    """Имя типа -> FQN по таблице импортов файла. Не разрешилось — пустая строка.

    Именно по таблице импортов, а не по глобальному индексу простых имён:
    одноимённые классы в разных каталогах — норма там, где единица изоляции файл.
    """
    declared = resolve_name(name, file, ctx)
    return f"{module_fqn(declared)}.{name}" if declared else ""


@dataclass(frozen=True)
class _Handlers:
    """Кто обрабатывает экшен и каким типом он объявлен.

    `by_action` — список, а не одно значение: в NGXS один экшен законно
    обрабатывают несколько стейтов (сохранение пишет данные в одном и журнал
    в другом), и реализация «первый попавшийся» потеряла бы половину состояния
    страницы молча.
    """

    by_action: dict[str, list[tuple[str, str]]]
    type_by_member: dict[tuple[str, str], str]
    literal_by_action: dict[str, str]


def _handlers(
    by_fqn: dict[str, Symbol], parsed_by_file: dict[str, _Parsed], ctx: ResolveContext
) -> _Handlers:
    """Индекс обработчиков: `@Action(LoadInnerDebts) load(…)` в стейтах."""
    by_action: dict[str, list[tuple[str, str]]] = {}
    type_by_member: dict[tuple[str, str], str] = {}
    literal_by_action: dict[str, str] = {}

    for fqn in sorted(by_fqn):
        symbol = by_fqn[fqn]
        if not symbol.sources:
            continue
        file = symbol.sources[0].path
        for member in symbol.members:
            for attribute in member.attributes:
                if attribute.name != "Action" or not attribute.args:
                    continue
                name = attribute.args[0]
                declared_in = resolve_name(name, file, ctx)
                if declared_in is None:
                    continue
                action = f"{module_fqn(declared_in)}.{name}"
                by_action.setdefault(action, []).append((fqn, member.name))

                # Строка типа переживает переименование класса, поэтому
                # в `via_action` идёт она; имя класса — запасной вариант
                # для экшена, объявленного без поля `type`. Файл берётся
                # от резолва, а не собирается из FQN обратно: у обратной
                # сборки своё представление о расширении файла.
                declared = parsed_by_file.get(declared_in)
                literal = declared.action_types.get(name) if declared else None
                type_by_member[(file, member.name)] = literal or name
                literal_by_action[action] = literal or name

    return _Handlers(
        by_action={key: sorted(set(items)) for key, items in by_action.items()},
        type_by_member=type_by_member,
        literal_by_action=literal_by_action,
    )


def _store_usages_of(
    symbol: Symbol,
    parsed_by_file: dict[str, _Parsed],
    ranges: MemberRanges,
    ctx: ResolveContext,
    by_fqn: dict[str, Symbol],
    handlers: _Handlers,
) -> tuple[list[Usage], Counter[str]]:
    """Рёбра через стор: диспатч экшена и выборка из стейта.

    Оба вида дают ребро «страница обращается к члену стейта» — то же ребро,
    что и у прямого вызова сервиса. Отдельный вид связи потребовал бы второго
    обхода в каждой задаче, которая ходит по графу.
    """
    if not symbol.sources:
        return [], Counter()
    file = symbol.sources[0].path
    parsed = parsed_by_file.get(file)
    if parsed is None:
        return [], Counter()

    span = symbol.sources[0]
    found: dict[tuple[str, str, str], Usage] = {}
    counters: Counter[str] = Counter()

    for dispatch in parsed.dispatches:
        if not span.start <= dispatch.line <= span.end:
            continue
        action = _resolved_type(dispatch.action, file, ctx)
        targets = handlers.by_action.get(action, [])
        if not targets:
            # Экшен без обработчика: он может обрабатываться подпиской
            # или эффектом вне стейта. Состояние работы, а не ошибка разбора.
            counters["actions_without_handler"] += 1
            continue
        counters["dispatches"] += 1
        for state_fqn, member in targets:
            key = (state_fqn, member, ranges.of(file, dispatch.line))
            found.setdefault(
                key,
                Usage(
                    target=key[0],
                    member=key[1],
                    via=key[2],
                    action=handlers.literal_by_action.get(action, ""),
                ),
            )

    for select in parsed.selects:
        if not span.start <= select.line <= span.end:
            continue
        state_fqn = _resolved_type(select.owner, file, ctx)
        if not state_fqn or state_fqn not in by_fqn:
            counters["selects_unresolved"] += 1
            continue
        counters["selects"] += 1
        key = (state_fqn, select.member, ranges.of(file, select.line))
        found.setdefault(key, Usage(target=key[0], member=key[1], via=key[2]))

    return [found[key] for key in sorted(found)], counters


def _template_usages(
    root: Path,
    symbol: Symbol,
    parsed_by_file: dict[str, _Parsed],
    ctx: ResolveContext,
    by_fqn: dict[str, Symbol],
) -> tuple[list[Usage], Counter[str]]:
    """Обращения из шаблона компонента.

    Известная дыра разбора `.ts`: `(click)="save()"` и `service.list() | async`
    живут в разметке. Для страницы это существенно — метод, который зовут
    только из шаблона, иначе выглядит мёртвым, а сервис, к которому обращаются
    прямо из разметки, не связан со страницей вовсе.

    Источником вызова записывается **шаблон**, а не член: вызов пишет разметка,
    и приписать его конструктору или первому методу значило бы соврать про то,
    кто зовёт.
    """
    template = template_of(root, symbol)
    counters: Counter[str] = Counter()
    if template is None:
        return [], counters

    counters["templates"] += 1
    members = {member.name for member in symbol.members}
    bindings = _bindings_of(symbol, parsed_by_file, by_fqn)
    file = symbol.sources[0].path if symbol.sources else ""

    found: dict[tuple[str, str, str], Usage] = {}
    for call in template_calls(template.text, members, set(bindings)):
        if call.own:
            # Свой член ребром НЕ становится: `Usage` — это обращение узла
            # к члену **другого** узла, исамоссылка сломало бы и модель,
            # и список зависимостей страницы. Число печатается: оно отвечает
            # на вопрос «сколько членов зовут только из разметки», а сама
            # достижимость такого члена от страницы обеспечивается связью
            # «тип содержит член» в графе.
            counters["template_own"] += 1
            continue

        bound = bindings.get(call.receiver)
        declared = resolve_name(bound, file, ctx) if bound else None
        target = f"{module_fqn(declared)}.{bound}" if declared and bound else ""
        if not target or target not in by_fqn:
            counters["template_external"] += 1
            continue
        found[(target, call.member, TEMPLATE_MEMBER)] = Usage(
            target=target, member=call.member, via=TEMPLATE_MEMBER
        )
        counters["template_resolved"] += 1

    return [found[key] for key in sorted(found)], counters


def _usages(
    index: dict[str, Symbol],
    parsed: list[_Parsed],
    ranges: MemberRanges,
    ctx: ResolveContext,
    root: Path | None = None,
) -> tuple[dict[str, list[Usage]], Counter[str], dict[tuple[str, str], str]]:
    """Граф вызовов: FQN источника -> рёбра, счётчики неудач и типы экшенов."""
    parsed_by_file = {item.result.path: item for item in parsed}
    by_fqn = {symbol.fqn: symbol for symbol in sorted(index.values(), key=lambda s: s.fqn)}
    handlers = _handlers(by_fqn, parsed_by_file, ctx)

    edges: dict[str, list[Usage]] = {}
    counters: Counter[str] = Counter()
    for fqn in sorted(by_fqn):
        direct, counted = _usages_of(by_fqn[fqn], parsed_by_file, ranges, ctx, by_fqn)
        counters.update(counted)
        through_store, counted = _store_usages_of(
            by_fqn[fqn], parsed_by_file, ranges, ctx, by_fqn, handlers
        )
        counters.update(counted)

        from_template: list[Usage] = []
        if root is not None:
            from_template, counted = _template_usages(
                root, by_fqn[fqn], parsed_by_file, ctx, by_fqn
            )
            counters.update(counted)

        merged = {
            (item.target, item.member, item.via): item
            for item in direct + through_store + from_template
        }
        if merged:
            edges[fqn] = [merged[key] for key in sorted(merged)]
    return edges, counters, handlers.type_by_member


def apply_overrides(
    routes: dict[str, list[RouteEntry]],
    overrides: Overrides,
    by_fqn: dict[str, Symbol],
) -> tuple[dict[str, list[RouteEntry]], set[str], OverrideReport]:
    """Наложить ручные правила на таблицу маршрутов.

    Порядок фиксирован: автоматика -> добавления -> снятия. Снятие сильнее
    добавления, потому что это последнее слово человека.

    Возвращает маршруты, множество снятых компонентов и отчёт. Отчёт нужен
    даже когда всё сработало: правило, которое перестало на что-то ложиться,
    иначе исчезает вместе со страницей и не оставляет следа.
    """
    updated = {fqn: list(items) for fqn, items in routes.items()}
    stale: list[StaleRule] = []
    added: list[str] = []

    for rule in overrides.add:
        if rule.component not in by_fqn:
            stale.append(StaleRule(kind="add-missed", key=rule.component, reason=rule.reason))
            continue
        # Лишним правило считается, только когда обход нашёл **этот же**
        # маршрут. Вторая точка входа у той же страницы — законный случай:
        # экран открывается и по своему пути, и из окна печати.
        if any(
            entry.path == rule.normalized and not entry.route_unresolved
            for entry in updated.get(rule.component, [])
        ):
            stale.append(StaleRule(kind="add-redundant", key=rule.component, reason=rule.reason))
            continue
        updated.setdefault(rule.component, []).append(
            RouteEntry(
                path=rule.normalized,
                component=rule.component,
                source=MANUAL_SOURCE,
                table=MANUAL_TABLE,
            )
        )
        added.append(rule.component)

    demoted: set[str] = set()
    for removal in overrides.remove:
        matched = _removal_targets(removal.component, removal.normalized, updated)
        if not matched:
            key = removal.component or f"/{removal.normalized}"
            stale.append(StaleRule(kind="remove-missed", key=key, reason=removal.reason))
            continue
        demoted.update(matched)

    return (
        {fqn: sorted(items, key=_route_key) for fqn, items in updated.items()},
        demoted,
        OverrideReport(added=sorted(added), removed=sorted(demoted), stale=stale),
    )


def _route_key(entry: RouteEntry) -> tuple[str, str, str, str]:
    return (entry.path, entry.component, entry.source, entry.table)


def _removal_targets(component: str, route: str, routes: dict[str, list[RouteEntry]]) -> set[str]:
    """Кого снимает правило. Неоднозначное снятие — отказ, а не выбор наугад.

    Компонент, стоящий на двух маршрутах (`create` и `edit` — один класс),
    снимается целиком: вид у него один. Поэтому правило по маршруту, которое
    задело бы и второй маршрут, обязано отказать и назвать, что нашлось.
    """
    if component:
        return {component} if component in routes else set()

    matched = {
        fqn
        for fqn, items in routes.items()
        if any(entry.path == route and not entry.route_unresolved for entry in items)
    }
    for fqn in sorted(matched):
        others = sorted(
            {
                entry.path
                for entry in routes[fqn]
                if not entry.route_unresolved and entry.path != route
            }
        )
        if others:
            raise ValueError(
                f"снятие по маршруту /{route} неоднозначно: {fqn} стои́т ещё на "
                + ", ".join(f"/{path}" for path in others)
                + ". Снимайте по `component`, если хотите убрать страницу целиком."
            )
    return matched


def build_nodes(
    index: dict[str, Symbol],
    modules: list[WebModule],
    ruleset: Ruleset,
    config: DocpipeConfig,
    calls: dict[str, list[WebCall]],
    routes: dict[str, list[RouteEntry]],
    uses: dict[str, list[Usage]] | None = None,
    demoted: set[str] | None = None,
) -> tuple[list[Module], list[DocNode]]:
    """Собрать модули и узлы документации фронта.

    Вызовы попадают на узел **того файла, где записаны**: связь строит сервис,
    а не страница, и приписать их странице значило бы соврать о том, кто зовёт.
    Путь «страница → эндпоинт» собирается по рёбрам `uses`, а не подменой автора.
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
            routes.get(symbol.fqn, []),
            key=lambda entry: (entry.path, entry.component, entry.source, entry.table),
        )
        kind, template = (
            (classification.kind, classification.template)
            if symbol.fqn in (demoted or set())
            else _promote(classification.kind, classification.template, node_calls, node_routes)
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
                    config.web_modules_root,
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
                uses=(uses or {}).get(symbol.fqn, []),
            )
        )

    return configured, sorted(nodes, key=lambda node: node.id)


def _feature_nodes(
    nodes: list[DocNode],
    features: list[Feature],
    config: DocpipeConfig,
) -> tuple[list[DocNode], list[StaleRule]]:
    """Синтетические узлы разделов и находки по пустым объявлениям.

    У раздела нет символа: он не класс, а решение человека о том, что вот эта
    кучка классов — одна вещь. Поэтому `symbol` пустой, а состав собирается
    по каталогу; хэши досчитает `absorb` вместе с составом.

    `uses` и `web_calls` объединяются со всех узлов раздела: дальше документ
    и отчёт ходят по разделу тем же обходом, что и по странице, и второй
    реализации обхода не заводится.
    """
    found: list[DocNode] = []
    stale: list[StaleRule] = []

    for feature in features:
        inside = [
            node
            for node in nodes
            if node.symbol
            and node.symbol.sources
            and node.symbol.sources[0].path.startswith(feature.prefix)
            and node.kind != PAGE_KIND
        ]
        if not inside:
            # Каталог переименовали или раздел ещё не написан. Молчать нельзя:
            # документ раздела просто не появится, и в отчёте будет на строку
            # меньше — ровно то, ради чего заведены находки о протухших правилах.
            stale.append(StaleRule(kind="feature-empty", key=feature.path, reason=feature.reason))
            continue

        module = min(node.module for node in inside)
        domain = min(node.domain for node in inside)
        uses = {
            (item.target, item.member, item.via, item.action): item
            for node in inside
            for item in node.uses
        }
        # Раздел «зовёт своих»: синтетическое ребро на каждый член своего узла,
        # в котором записан вызов. Без него вызовы сервиса, к которому внутри
        # раздела никто не обращается (его зовут снаружи), пропали бы из раздела
        # «Данные» — при том, что своего документа у сервиса больше нет.
        #
        # Копировать сами вызовы на узел раздела нельзя: тот же вызов пришёл бы
        # в отчёт дважды — своим и через ребро, — и таблица «Данные» удвоилась бы.
        for node in inside:
            if node.symbol is None:
                continue
            for call in node.web_calls:
                if call.member:
                    key = (node.symbol.fqn, call.member, "", "")
                    uses.setdefault(key, Usage(target=key[0], member=key[1], via="", action=""))

        found.append(
            DocNode(
                id=f"feature:{feature.name}",
                kind=FEATURE_KIND,
                template=FEATURE_KIND,
                title=feature.heading,
                doc_path=doc_path_for(
                    module, FEATURE_KIND, feature.name, config.doc_layout, config.web_modules_root
                ),
                module=module,
                domain=domain,
                signature_hash=stable_hash(["feature", feature.name, feature.path]),
                uses=[uses[key] for key in sorted(uses)],
            )
        )

    return found, stale


def run(
    root: Path,
    config: DocpipeConfig | None = None,
    ruleset: Ruleset | None = None,
    cache_dir: Path | None = None,
    overrides: Overrides | None = None,
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

    ranges = member_ranges(index.values())
    uses, usage_counts, action_of_member = _usages(index, parsed, ranges, context, root)
    calls, calls_by_file = _calls_by_file(parsed, modules, config, ranges, action_of_member)
    sources = {relative: (root / relative).read_bytes() for relative in found.ts_files}
    routes = build_routes(sources, context)

    by_component, demoted, override_report = apply_overrides(
        _routes_by_component(routes), overrides or Overrides(), {s.fqn: s for s in index.values()}
    )
    configured, nodes = build_nodes(
        index, modules, ruleset, config, calls_by_file, by_component, uses, demoted
    )

    # Разделы собираются на готовых узлах: их состав — каталог, а узлы
    # появляются только после классификации.
    features = (overrides or Overrides()).features
    feature_nodes, feature_stale = _feature_nodes(nodes, features, config)
    nodes = absorb(sorted([*nodes, *feature_nodes], key=lambda node: node.id), features)
    override_report = override_report.model_copy(
        update={"stale": [*override_report.stale, *feature_stale]}
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
            # Три числа вместо одного: ребро найдено, получатель внешний
            # (`HttpClient`, `Router`, `Store` — узла документации у них нет),
            # получатель не разрешён вовсе. Одно число «рёбер 128» не сказало бы,
            # сколько графа потеряно.
            "usages": usage_counts["resolved"],
            "usages_external": usage_counts["external"],
            "usages_unresolved": usage_counts["unresolved"],
            # Цепочка NGXS. Экшен без обработчика — состояние работы, а не
            # ошибка разбора: обработчик может жить в подписке или в эффекте
            # вне стейта. Поэтому число, а не отказ.
            "dispatches": usage_counts["dispatches"],
            "actions_without_handler": usage_counts["actions_without_handler"],
            "selects": usage_counts["selects"],
            # Шаблоны: сколько прочитано, сколько обращений из разметки
            # дошло до чужого узла и сколько пришлось на свои члены.
            # Последнее число — это ровно те методы, которые в `.ts`
            # выглядят никем не зовомыми.
            "templates": usage_counts["templates"],
            "template_usages": usage_counts["template_resolved"],
            "template_own_members": usage_counts["template_own"],
            "template_external": usage_counts["template_external"],
            "pages_added": len(override_report.added),
            "pages_removed": len(override_report.removed),
            "overrides_stale": len(override_report.stale),
            "absorbed": sum(1 for node in nodes if node.absorbed_by),
            "features": len(feature_nodes),
        },
        parse_error_files=broken,
    )
    return WebScanResult(
        manifest=manifest,
        meta=meta,
        index=index,
        calls=calls,
        routes=routes,
        overrides=override_report,
    )


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
