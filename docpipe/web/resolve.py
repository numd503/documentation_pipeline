"""Резолв TypeScript: импорты, алиасы `tsconfig`, бочки, замыкание наследования.

Первый модуль шага `web`, где данные из разных файлов сходятся вместе.

Резолв синтаксический и заведомо неполный: типы из `node_modules` в индекс
не попадают и остаются сырыми именами. Это не недоделка, а граница подхода —
правила классификации матчатся именно по таким именам (`HttpInterceptor`,
`CanActivateFn`), и знать о них больше не требуется.

Разница с .NET принципиальная и определяет всё устройство модуля: там имя
резолвится по namespace и usings, то есть по **пространству имён**, здесь —
по импортам, то есть по **файлам**. Поэтому арность в переходе «базовый тип →
символ» не нужна: пара «файл + имя» уникальна сама по себе.
"""

import json
import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docpipe.hashing import stable_hash
from docpipe.model import (
    Attribute,
    FileParseResult,
    Member,
    ModuleImport,
    RawDeclaration,
    Symbol,
)
from docpipe.symbols import strip_generics, symbol_key

# Порядок проб при разрешении пути модуля. `.d.ts` идёт после `.ts`: если рядом
# лежат оба, TypeScript выбирает реализацию, а не декларацию.
_FILE_SUFFIXES = ("", ".ts", ".d.ts", "/index.ts")

_TYPESCRIPT_SUFFIXES = (".d.ts", ".ts")

# Каталог, из которого обход вырезан. Алиас может указывать внутрь него
# (в АС CF так подключён `exceljs`), и такая цель обязана считаться внешней:
# иначе вырезанное дерево вернётся в разбор через таблицу алиасов.
_VENDOR_DIRECTORY = "node_modules"


# --------------------------------------------------------------------------------------
# tsconfig: JSON с комментариями и цепочка extends
# --------------------------------------------------------------------------------------


def parse_jsonc(text: str) -> Any:
    """Разобрать JSON с комментариями и висящими запятыми.

    `tsconfig.json` — **не** JSON: и `ng new`, и `nx` генерируют его
    с комментариями (`/* To learn more about this file see: … */`), а руками
    туда дописывают `//`. `json.loads` на таком файле бросает исключение,
    то есть без этого разбора не читается ни один боевой `tsconfig`, а вместе
    с ним не разрешается ни один импорт вида `@shared/…`.

    Комментарии вырезаются сканером, а не регулярным выражением: `//` внутри
    строки (`"url": "http://host"`) регулярное выражение приняло бы за начало
    комментария и съело бы остаток строки.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False

    while index < length:
        char = text[index]

        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                index += 1
            continue

        if char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue

        if char in "}]":
            # Висящая запятая. Снимается здесь же, вне строки, поэтому запятая
            # внутри значения не пострадает.
            while out and out[-1].isspace():
                out.pop()
            if out and out[-1] == ",":
                out.pop()

        out.append(char)
        index += 1

    return json.loads("".join(out))


@dataclass(frozen=True)
class TsConfig:
    """Разрешённая таблица алиасов одного `tsconfig`.

    `paths` уже репо-относительные: пересчёт относительно `baseUrl` сделан
    при загрузке, чтобы вызывающему не пришлось знать, какой файл цепочки
    `extends` объявил какое поле.
    """

    base_url: str = ""
    paths: dict[str, list[str]] = field(default_factory=dict)


def _normalize(*parts: str) -> str:
    joined = posixpath.join(*[part for part in parts if part])
    normalized = posixpath.normpath(joined)
    return "" if normalized == "." else normalized


def _load_raw(root: Path, relative: str, seen: set[str]) -> tuple[str | None, dict[str, list[str]]]:
    """`(baseUrl, paths)` одного файла с учётом всей цепочки `extends`.

    `paths` дочернего конфига **замещает** родительский целиком, а не дополняет
    его: так работает TypeScript. Слияние выглядит логичнее и даёт набор алиасов,
    которого у компилятора нет, — то есть резолв, расходящийся со сборкой.
    """
    if relative in seen or not (root / relative).is_file():
        return None, {}
    seen.add(relative)

    data = parse_jsonc((root / relative).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        return None, {}

    directory = posixpath.dirname(relative)

    inherited_base: str | None = None
    inherited_paths: dict[str, list[str]] = {}
    extends = data.get("extends")
    if isinstance(extends, str):
        target = _normalize(directory, extends)
        if not target.endswith(".json"):
            target = f"{target}.json"
        inherited_base, inherited_paths = _load_raw(root, target, seen)

    options = data.get("compilerOptions")
    if not isinstance(options, dict):
        return inherited_base, inherited_paths

    base_url = inherited_base
    if isinstance(options.get("baseUrl"), str):
        base_url = _normalize(directory, options["baseUrl"])

    raw_paths = options.get("paths")
    if not isinstance(raw_paths, dict):
        return base_url, inherited_paths

    # Цели `paths` считаются от `baseUrl`, а при его отсутствии — от каталога
    # того файла, который объявил `paths`.
    anchor = base_url if base_url is not None else directory
    resolved = {
        pattern: [_normalize(anchor, target) for target in targets if isinstance(target, str)]
        for pattern, targets in raw_paths.items()
        if isinstance(targets, list)
    }
    return base_url, resolved


def load_tsconfig(root: Path, relative: str) -> TsConfig:
    """Загрузить `tsconfig` вместе с цепочкой `extends`."""
    base_url, paths = _load_raw(root, relative, set())
    return TsConfig(base_url=base_url or "", paths=dict(sorted(paths.items())))


# --------------------------------------------------------------------------------------
# Разрешение спецификатора модуля
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedModule:
    """Куда указывает спецификатор импорта.

    `external` и пустой `path` — разные вещи только по смыслу сообщения:
    у внешней цели путь не возвращается **намеренно**, чтобы её нельзя было
    случайно втянуть в обход.
    """

    path: str | None = None
    external: bool = False


def _resolve_file(base: str, files: frozenset[str]) -> str | None:
    """Путь модуля по правилам разрешения TypeScript: `x.ts`, затем `x/index.ts`."""
    for suffix in _FILE_SUFFIXES:
        candidate = f"{base}{suffix}"
        if candidate in files:
            return candidate
    return None


def _alias_targets(specifier: str, paths: dict[str, list[str]]) -> list[str]:
    """Цели алиаса. Побеждает шаблон с самым длинным префиксом до `*`.

    Правило самого TypeScript: при `@shared` и `@shared/*` импорт `@shared/x`
    обязан пойти по второму. Выбор «первый попавшийся» дал бы резолв,
    зависящий от порядка ключей в файле.
    """
    exact = paths.get(specifier)
    if exact is not None:
        return exact

    best: tuple[int, list[str]] | None = None
    for pattern, targets in paths.items():
        prefix, star, suffix = pattern.partition("*")
        if not star or not specifier.startswith(prefix) or not specifier.endswith(suffix):
            continue
        middle = specifier[len(prefix) : len(specifier) - len(suffix) or None]
        if best is None or len(prefix) > best[0]:
            best = (len(prefix), [target.replace("*", middle) for target in targets])

    return best[1] if best else []


def resolve_specifier(
    specifier: str, from_file: str, config: TsConfig, files: frozenset[str]
) -> ResolvedModule:
    """Разрешить спецификатор импорта в путь файла репозитория.

    Три случая, и третий — самый частый: `@angular/core`, `rxjs`, `@ngxs/store`
    внешние, и это нормальное состояние, а не потеря.
    """
    if specifier.startswith("."):
        base = _normalize(posixpath.dirname(from_file), specifier)
        return ResolvedModule(path=_resolve_file(base, files))

    for target in _alias_targets(specifier, config.paths):
        if _VENDOR_DIRECTORY in target.split("/"):
            return ResolvedModule(external=True)
        resolved = _resolve_file(target, files)
        if resolved is not None:
            return ResolvedModule(path=resolved)

    return ResolvedModule(external=True)


# --------------------------------------------------------------------------------------
# Разрешение имени: импорт, бочка, собственное объявление
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveContext:
    """Всё, что нужно для разрешения имени: кто что объявляет и кто откуда импортирует."""

    config: TsConfig
    files: frozenset[str]
    declared: dict[str, frozenset[str]]
    imports: dict[str, list[ModuleImport]]


def build_context(results: list[FileParseResult], config: TsConfig) -> ResolveContext:
    return ResolveContext(
        config=config,
        files=frozenset(result.path for result in results),
        declared={
            result.path: frozenset(declaration.name for declaration in result.declarations)
            for result in results
        },
        imports={result.path: list(result.imports) for result in results},
    )


def _exported_from(file: str, name: str, ctx: ResolveContext, visited: set[str]) -> str | None:
    """Файл, в котором объявлено имя, экспортируемое модулем `file`.

    Бочка (`index.ts` с `export * from './x'`) — не экзотика, а обычный способ
    собрать публичный API каталога: имя импортируется не оттуда, где объявлено.
    Резолв, знающий только про относительные пути и алиасы, найдёт здесь ноль
    объявлений и потеряет цепочку наследования, ничего не сообщив.
    """
    if file in visited:
        return None
    visited.add(file)

    if name in ctx.declared.get(file, frozenset()):
        return file

    for item in ctx.imports.get(file, []):
        if not item.re_export:
            continue
        wanted = next((n.imported for n in item.names if n.local == name), None)
        if wanted is None and not item.star:
            continue
        target = resolve_specifier(item.source, file, ctx.config, ctx.files)
        if target.path is None:
            continue
        found = _exported_from(target.path, wanted or name, ctx, visited)
        if found is not None:
            return found

    return None


def resolve_exported(file: str, name: str, ctx: ResolveContext) -> str | None:
    """Файл, в котором объявлено имя, экспортируемое модулем `file`.

    Нужен там, где модуль уже известен, а имя надо найти сквозь бочки:
    `loadChildren: () => import('./x').then(m => m.ROUTES)`.
    """
    return _exported_from(file, name, ctx, set())


def resolve_name(name: str, from_file: str, ctx: ResolveContext) -> str | None:
    """Файл, в котором объявлено имя, видимое в `from_file`. `None` — не наше.

    Порядок проверок повторяет правила TypeScript: собственное объявление файла
    перекрывает импортированное с тем же именем.
    """
    if name in ctx.declared.get(from_file, frozenset()):
        return from_file

    for item in ctx.imports.get(from_file, []):
        if item.re_export:
            continue
        wanted = next((n.imported for n in item.names if n.local == name), None)
        if wanted is None:
            continue
        target = resolve_specifier(item.source, from_file, ctx.config, ctx.files)
        if target.path is None:
            continue
        found = _exported_from(target.path, wanted, ctx, set())
        if found is not None:
            return found

    return None


# --------------------------------------------------------------------------------------
# Индекс символов
# --------------------------------------------------------------------------------------


def module_fqn(path: str) -> str:
    """Путь файла без расширения — пространство имён модуля TypeScript.

    В TypeScript единица изоляции — файл, а не каталог: два файла одного
    каталога законно объявляют одноимённый `Helper`. FQN по каталогу склеил бы
    их в один символ, и один из двух исчез бы из документации молча.
    """
    for suffix in _TYPESCRIPT_SUFFIXES:
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


def declaration_fqn(path: str, declaration: RawDeclaration) -> str:
    parts = (module_fqn(path), declaration.containing_type or "", declaration.name)
    return ".".join(part for part in parts if part)


def _unique_attributes(attributes: list[Attribute]) -> list[Attribute]:
    """Дедупликация атрибутов с устойчивым порядком (модель нехэшируема)."""
    seen: dict[tuple[str, str], Attribute] = {}
    for attribute in attributes:
        key = (attribute.name, repr((attribute.args, sorted(attribute.named_args.items()))))
        seen.setdefault(key, attribute)
    return [seen[key] for key in sorted(seen)]


def build_symbol_index(
    results: list[FileParseResult],
    file_to_module: dict[str, str],
    ctx: ResolveContext,
) -> dict[str, Symbol]:
    """Собрать индекс символов фронта. Ключ — `symbol_key(модуль, FQN, арность)`.

    Базовый тип, который не удалось разрешить, сохраняется **сырым именем**,
    а не теряется: правила классификации матчатся ровно по таким именам, а
    исчезнувший базовый тип превратил бы компонент в неклассифицированный
    символ без всякого следа причины.
    """
    items: list[tuple[str, str, RawDeclaration]] = []
    for result in results:
        module = file_to_module.get(result.path)
        if module is None:
            continue
        for declaration in result.declarations:
            items.append((module, result.path, declaration))

    # Пара «файл + имя» -> ключ символа. Ею и разрешается базовый тип: арность
    # для этого не нужна, файл уже однозначен.
    key_by_file_name = {
        (path, declaration.name): symbol_key(
            module, declaration_fqn(path, declaration), len(declaration.type_parameters)
        )
        for module, path, declaration in items
    }

    index: dict[str, Symbol] = {}
    for module, path, declaration in items:
        key = key_by_file_name[(path, declaration.name)]
        base_types: list[str] = []
        base_types_raw: list[str] = []

        for raw in declaration.base_types:
            name = strip_generics(raw)
            base_types_raw.append(raw)
            target = resolve_name(name, path, ctx)
            base_key = key_by_file_name.get((target or "", name))
            base_types.append(index_fqn(base_key) if base_key else name)

        index[key] = Symbol(
            fqn=declaration_fqn(path, declaration),
            name=declaration.name,
            type_kind=declaration.type_kind,
            namespace=declaration.namespace,
            module=module,
            modifiers=list(declaration.modifiers),
            type_parameters=list(declaration.type_parameters),
            base_types=base_types,
            base_types_raw=base_types_raw,
            attributes=_unique_attributes(list(declaration.attributes)),
            members=_sorted_members(declaration.members),
            sources=[declaration.span],
            xml_doc=declaration.xml_doc,
            impl_hash=stable_hash([declaration.decl_hash]),
        )

    return dict(sorted(index.items()))


def index_fqn(key: str) -> str:
    """FQN из ключа символа. Ключ — `модуль#FQN\\`арность`."""
    return key.partition("#")[2].rpartition("`")[0]


def _sorted_members(members: list[Member]) -> list[Member]:
    return sorted(members, key=lambda member: (member.line, member.name))


def _by_fqn(index: dict[str, Symbol]) -> dict[str, list[str]]:
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for key, symbol in index.items():
        grouped[symbol.fqn].append(key)
    return {fqn: sorted(keys) for fqn, keys in grouped.items()}


def compute_closures(index: dict[str, Symbol]) -> dict[str, Symbol]:
    """Заполнить `base_type_closure` — транзитивное замыкание базовых типов.

    Ради этого шага индекс и существует: правило «сервис — это то, что
    наследуется от `BaseApiService`» обязано срабатывать и тогда, когда
    наследование идёт через собственный базовый класс из другого файла.

    Нерезолвнутые имена в замыкание попадают, но не раскрываются: их
    объявлений у нас нет.
    """
    by_fqn = _by_fqn(index)
    result: dict[str, Symbol] = {}

    for key, symbol in index.items():
        closure: set[str] = set()
        visited = {key}
        queue = [key]

        while queue:
            current = index[queue.pop()]
            for fqn in current.base_types:
                closure.add(fqn)
                # `A extends B`, `B extends A` в TypeScript невозможны, но
                # получить их из битого или частично разобранного кода можно.
                for base_key in by_fqn.get(fqn, []):
                    if base_key not in visited:
                        visited.add(base_key)
                        queue.append(base_key)

        closure.discard(symbol.fqn)
        result[key] = symbol.model_copy(update={"base_type_closure": sorted(closure)})

    return result
