"""Сборка индекса символов: FQN, слияние `partial`, резолв базовых типов.

Первый модуль, где данные из разных файлов сходятся вместе. До него всё было
свойством одного файла, здесь появляется решение целиком.

Резолв здесь синтаксический и заведомо неполный: типы из NuGet-пакетов и BCL
в индекс не попадают и остаются сырыми именами. Это не недоделка, а граница
подхода — правила классификации матчатся именно по таким именам
(`ControllerBase`, `AbpModule`), и знать о них больше не требуется.
"""

from collections import defaultdict

from docpipe.model import Attribute, FileParseResult, Member, RawDeclaration, SourceSpan, Symbol

# Разделители ключа символа. Ни один не встречается в FQN, поэтому ключ
# разбирается обратно однозначно.
_MODULE_SEPARATOR = "#"
_ARITY_SEPARATOR = "`"


def symbol_key(module: str, fqn: str, arity: int) -> str:
    """Ключ символа: модуль, FQN и число параметров-дженериков.

    FQN сам по себе типы **не различает** — проверено на ABP, 255 коллизий
    на 9075 объявлений:

    - `ICrudAppService`, `ICrudAppService<T>` и `ICrudAppService<T, K>` — три
      разных типа с одинаковым FQN (таких групп 112, рекорд — шесть арностей);
    - один и тот же FQN законно живёт в разных сборках; ключ без модуля склеил
      бы 155 таких пар.

    Формат совпадает с id узла документации на T15 — там к нему добавляется
    префикс `type:`. Держать две разные схемы ключей нельзя: узлы перестанут
    сопоставляться с символами.
    """
    return f"{module}{_MODULE_SEPARATOR}{fqn}{_ARITY_SEPARATOR}{arity}"


def declaration_fqn(declaration: RawDeclaration) -> str:
    """`namespace.containing_type.name`, пустые части пропускаются.

    Параметры-дженерики в FQN не входят: `List<T>` и `List<int>` — один тип.
    Различает их арность в ключе, а не имя.
    """
    parts = (declaration.namespace, declaration.containing_type or "", declaration.name)
    return ".".join(part for part in parts if part)


def strip_generics(text: str) -> str:
    """Убрать аргументы-дженерики, сохранив остальное имя.

    Удаляются **сбалансированные** группы `<…>`, а не всё от первого `<`:
    `A.B<C<D>>.E` — это вложенный тип `E` внутри generic-типа `B`, и его FQN
    равен `A.B.E`. Обрезка по первому `<` дала бы `A` и потеряла бы тип.

    Квалификатор `global::` снимается по той же причине, что и в usings:
    он не часть имени.
    """
    out: list[str] = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out).strip().removeprefix("global::")


def _namespace_prefixes(namespace: str) -> list[str]:
    """Namespace и все его префиксы от длинного к короткому, включая пустой.

    Пустой префикс обязателен: глобальное пространство имён видно из любого
    места, и тип без namespace обязан находиться.
    """
    if not namespace:
        return [""]
    parts = namespace.split(".")
    return [".".join(parts[:index]) for index in range(len(parts), 0, -1)] + [""]


def _qualify(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _resolve_base_type(
    raw: str, namespace: str, usings: list[str], known: set[str]
) -> tuple[str, bool]:
    """Резолв имени базового типа в FQN. Возвращает `(имя, неоднозначно_ли)`.

    Порядок повторяет правила C#, а не перебирает всё скопом:

    1. полностью квалифицированное имя, если такое есть в индексе;
    2. охватывающий namespace и его префиксы, **от длинного к короткому** —
       побеждает первое совпадение, ближнее имя перекрывает дальнее;
    3. только если ничего не нашлось — usings; вот здесь несколько совпадений
       действительно означают неоднозначность.

    Перебор «собрать все совпадения из namespace и usings разом» пометил бы
    неоднозначным обычный случай, когда тип виден и по namespace, и по using, —
    хотя в C# никакой неоднозначности тут нет.

    Ненайденное имя возвращается как есть: внешние типы (`ControllerBase`,
    `AbpModule`) не резолвятся принципиально, и правила матчатся по ним.
    """
    name = strip_generics(raw)
    if not name:
        return raw, False

    if "." in name and name in known:
        return name, False

    for prefix in _namespace_prefixes(namespace):
        candidate = _qualify(prefix, name)
        if candidate in known:
            return candidate, False

    matches = sorted({_qualify(u, name) for u in usings if _qualify(u, name) in known})
    if matches:
        return matches[0], len(matches) > 1

    return name, False


def _unique_attributes(attributes: list[Attribute]) -> list[Attribute]:
    """Дедупликация атрибутов с устойчивым порядком.

    Ключ строится вручную: аргументы — списки и словари, поэтому модель
    нехэшируема и `set` тут не работает.
    """
    seen: dict[tuple[str, str], Attribute] = {}
    for attribute in attributes:
        key = (attribute.name, repr((attribute.args, sorted(attribute.named_args.items()))))
        seen.setdefault(key, attribute)
    return [seen[key] for key in sorted(seen)]


class _Group:
    """Объявления одного типа, собранные из всех файлов."""

    def __init__(self) -> None:
        self.items: list[tuple[str, RawDeclaration]] = []

    def add(self, path: str, declaration: RawDeclaration) -> None:
        self.items.append((path, declaration))

    def sorted_items(self) -> list[tuple[str, RawDeclaration]]:
        # Порядок файлов, а не порядок обхода: от него зависят type_kind,
        # xml_doc и порядок членов, то есть содержимое манифеста.
        return sorted(self.items, key=lambda item: (item[0], item[1].span.start))


def build_symbol_index(
    results: list[FileParseResult],
    file_to_module: dict[str, str],
) -> dict[str, Symbol]:
    """Собрать индекс символов из разобранных файлов.

    Ключ — `symbol_key(модуль, FQN, арность)`. `file_to_module` сопоставляет
    репо-относительный путь файла с путём его `.csproj` (тем же, что в
    `Module.csproj`); файлы без модуля пропускаются — документировать код,
    не входящий ни в один проект, всё равно некуда.
    """
    groups: defaultdict[str, _Group] = defaultdict(_Group)
    known_fqns: set[str] = set()
    own_usings: dict[str, set[str]] = {}
    module_globals: defaultdict[str, set[str]] = defaultdict(set)

    for result in results:
        module = file_to_module.get(result.path)
        if module is None:
            continue
        own_usings[result.path] = set(result.usings)
        # `global using` действует на весь проект, а не на файл, в котором объявлен:
        # обычно все они собраны в одном GlobalUsings.cs, и без этого шага
        # не резолвился бы ни один тип, видимый только через них.
        module_globals[module].update(result.global_usings)

        for declaration in result.declarations:
            fqn = declaration_fqn(declaration)
            known_fqns.add(fqn)
            groups[symbol_key(module, fqn, len(declaration.type_parameters))].add(
                result.path, declaration
            )

    usings_by_file = {
        path: sorted(usings | module_globals[file_to_module[path]])
        for path, usings in own_usings.items()
    }

    return {
        key: _build_symbol(group, usings_by_file, known_fqns, file_to_module)
        for key, group in sorted(groups.items())
    }


def _build_symbol(
    group: _Group,
    usings_by_file: dict[str, list[str]],
    known_fqns: set[str],
    file_to_module: dict[str, str],
) -> Symbol:
    items = group.sorted_items()
    first_path, first = items[0]

    modifiers: set[str] = set()
    base_types_raw: set[str] = set()
    base_types: set[str] = set()
    attributes: list[Attribute] = []
    members: list[tuple[str, int, str, Member]] = []
    sources: list[SourceSpan] = []
    ambiguous = False

    for path, declaration in items:
        modifiers.update(declaration.modifiers)
        base_types_raw.update(declaration.base_types)
        attributes.extend(declaration.attributes)
        sources.append(declaration.span)
        for member in declaration.members:
            members.append((path, member.line, member.name, member))

        usings = usings_by_file.get(path, [])
        for raw in declaration.base_types:
            resolved, is_ambiguous = _resolve_base_type(
                raw, declaration.namespace, usings, known_fqns
            )
            base_types.add(resolved)
            ambiguous = ambiguous or is_ambiguous

    xml_doc = next((d.xml_doc for _, d in items if d.xml_doc), None)

    return Symbol(
        fqn=declaration_fqn(first),
        name=first.name,
        type_kind=first.type_kind,
        namespace=first.namespace,
        module=file_to_module[first_path],
        modifiers=sorted(modifiers),
        type_parameters=list(first.type_parameters),
        base_types=sorted(base_types),
        base_types_raw=sorted(base_types_raw),
        attributes=_unique_attributes(attributes),
        members=[member for *_, member in sorted(members, key=lambda item: item[:3])],
        sources=sorted(sources, key=lambda span: (span.path, span.start)),
        xml_doc=xml_doc,
        ambiguous=ambiguous,
    )


def index_by_fqn(index: dict[str, Symbol]) -> dict[str, list[str]]:
    """FQN -> ключи символов с этим FQN, отсортированные.

    Нужен там, где известен только FQN: базовые типы хранятся как FQN, а не как
    ключи, чтобы по ним матчились правила классификации. Список, а не одно
    значение, потому что один FQN законно принадлежит нескольким типам —
    разной арности или из разных сборок.
    """
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for key, symbol in index.items():
        grouped[symbol.fqn].append(key)
    return {fqn: sorted(keys) for fqn, keys in sorted(grouped.items())}
