"""Сборка дерева документации: символы и правила -> узлы манифеста.

Последний детерминированный шаг. Дальше манифест уходит наружу, поэтому здесь
не остаётся ни одного решения, зависящего от порядка обхода: домены, enrollment,
идентификаторы, пути документов и связи считаются как чистые функции входа.
"""

import re
from collections import defaultdict

from docpipe.classify import Ruleset, classify
from docpipe.config import DocpipeConfig
from docpipe.discovery import matches_glob
from docpipe.dotnet.resolve import strip_generics, symbol_key
from docpipe.hashing import slugify, stable_hash
from docpipe.model import (
    Dependency,
    DiRegistration,
    DocNode,
    Endpoint,
    Module,
    Relation,
    Symbol,
)

# Слова, которые в списке параметров стоят перед типом и типом не являются.
_PARAMETER_MODIFIERS = frozenset({"ref", "out", "in", "params", "this", "scoped", "readonly"})
_ATTRIBUTE_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")


def node_id(module_csproj: str, symbol: Symbol) -> str:
    """Идентификатор узла — тот же ключ, что и у символа, с префиксом `type:`.

    Схема ключа обязана совпадать с `resolve.symbol_key`: иначе узлы перестанут
    сопоставляться с символами, а FQN сам по себе типы не различает (см. T10).
    """
    return f"type:{symbol_key(module_csproj, symbol.fqn, len(symbol.type_parameters))}"


# --------------------------------------------------------------------------------------
# Домены и enrollment
# --------------------------------------------------------------------------------------


def _domain_of(module: Module, domains: dict[str, str]) -> str:
    """Домен модуля по первому совпавшему glob в лексикографическом порядке ключей.

    Порядок ключей, а не порядок в YAML: словарь пришёл из конфигурации, и полагаться
    на порядок записи в файле нельзя — перестановка строк изменила бы манифест.
    """
    for glob in sorted(domains):
        if matches_glob(module.csproj, glob):
            return domains[glob]
    return module.name


def _apply_config(modules: list[Module], config: DocpipeConfig) -> list[Module]:
    return sorted(
        (
            module.model_copy(
                update={
                    "domain": _domain_of(module, config.domains),
                    "enrolled": any(matches_glob(module.csproj, g) for g in config.enrolled),
                }
            )
            for module in modules
        ),
        key=lambda module: module.id,
    )


# --------------------------------------------------------------------------------------
# Зависимости
# --------------------------------------------------------------------------------------


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """Разбить по разделителю, игнорируя вложенные скобки любого вида.

    Без учёта вложенности `IReadOnlyDictionary<string, int> map` распалось бы
    на два «параметра», и оба были бы мусором.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _parameter_list(signature: str) -> str | None:
    """Текст между скобками списка параметров. `None` — скобок нет.

    Ищется **парная** закрывающая скобка, а не последняя в строке: сигнатура
    конструктора включает инициализатор, и `public C(IOptions o) : base(o)`
    по последней скобке дал бы «параметр» `IOptions o) : base(o`.
    На ABP так ломался каждый пятый конструктор.
    """
    start = signature.find("(")
    if start == -1:
        return None

    depth = 0
    for position in range(start, len(signature)):
        if signature[position] == "(":
            depth += 1
        elif signature[position] == ")":
            depth -= 1
            if depth == 0:
                return signature[start + 1 : position]
    return None


def parameter_types(signature: str) -> list[str]:
    """Типы параметров из текста сигнатуры.

    Разбирается именно текст, потому что `Member` хранит сигнатуру строкой:
    структурированного списка параметров парсер не отдаёт.

    Отбрасываются атрибуты параметра (`[FromServices]`), модификаторы
    (`ref`, `out`, `params`, …) и значение по умолчанию. Тип — всё, что осталось
    до имени параметра; имя всегда последнее слово, поэтому по нему и режем.
    """
    inner = _parameter_list(signature)
    if inner is None:
        return []

    types: list[str] = []
    for parameter in _split_top_level(inner):
        cleaned = _ATTRIBUTE_PREFIX.sub("", parameter)
        cleaned = _split_top_level(cleaned, "=")[0].strip() if "=" in cleaned else cleaned

        words = cleaned.split()
        while words and words[0] in _PARAMETER_MODIFIERS:
            words.pop(0)
        if len(words) < 2:
            # Только тип без имени (или наоборот) — доверять такому нечему.
            continue
        types.append(" ".join(words[:-1]))
    return types


def _resolve_type_name(name: str, known_fqns: set[str], simple_names: dict[str, str]) -> str | None:
    """Имя типа из кода -> FQN символа из индекса. `None` — тип внешний.

    В сигнатуре тип записан так, как в исходнике, то есть обычно без namespace,
    поэтому одного сравнения с FQN мало.
    """
    if name in known_fqns:
        return name
    return simple_names.get(name.rpartition(".")[2])


def _constructor_dependencies(
    symbol: Symbol, known_fqns: set[str], simple_names: dict[str, str]
) -> list[Dependency]:
    found: list[Dependency] = []
    for member in symbol.members:
        if member.kind != "constructor":
            continue
        for raw in parameter_types(member.signature):
            name = strip_generics(raw)
            target = _resolve_type_name(name, known_fqns, simple_names)
            found.append(
                Dependency(
                    target=target or name,
                    via="constructor",
                    confidence="high" if target else "low",
                )
            )
    return found


def _di_dependencies(
    symbol: Symbol,
    registrations: list[DiRegistration],
    known_fqns: set[str],
    simple_names: dict[str, str],
) -> list[Dependency]:
    """Регистрации, реализацией в которых выступает этот тип.

    `target` — интерфейс, под которым тип зарегистрирован, то есть ребро читается
    как «этот тип поставляется в контейнер как такой-то сервис».

    Сопоставление через `strip_generics`: T13 сохраняет имена как написаны,
    и открытый дженерик `KernelAccessor<>` буквально с именем символа
    `KernelAccessor` не совпадёт.

    Регистрация типа на самого себя (`AddTransient<ValuationWorkflow>()`) ребра
    не даёт: петля в графе зависимостей — это не факт о коде, а артефакт формы
    записи. Время жизни при этом теряется — `Dependency` его не хранит.
    """
    found: list[Dependency] = []
    for registration in registrations:
        if registration.impl_type is None:
            continue
        implementation = strip_generics(registration.impl_type)
        if implementation.rpartition(".")[2] != symbol.name:
            continue

        service = strip_generics(registration.service_type)
        if service.rpartition(".")[2] == symbol.name:
            continue

        found.append(
            Dependency(
                target=_resolve_type_name(service, known_fqns, simple_names) or service,
                via="di",
                confidence=registration.confidence,
            )
        )
    return found


def _unique_sorted(dependencies: list[Dependency]) -> list[Dependency]:
    by_key = {(d.target, d.via): d for d in sorted(dependencies, key=lambda d: (d.target, d.via))}
    return [by_key[key] for key in sorted(by_key)]


# --------------------------------------------------------------------------------------
# Связи и хэш сигнатуры
# --------------------------------------------------------------------------------------


def _relations(symbol: Symbol, interfaces: set[str]) -> list[Relation]:
    """Рёбра `implements` на интерфейсы, которые есть в индексе.

    Обратное ребро не создаётся: интерфейс мог не стать узлом документации,
    и ссылка вела бы в пустоту.
    """
    targets = sorted({fqn for fqn in symbol.base_type_closure if fqn in interfaces})
    return [Relation(target=target, relation="implements") for target in targets]


def signature_hash(symbol: Symbol) -> str:
    """Хэш формы типа.

    **Не включает** номера строк и пути. Переформатирование файла или перенос
    кода не должны менять хэш: иначе агент шага 3 будет перегенерировать
    документ впустую, и вся экономия от инкрементальности пропадёт.
    """
    payload = {
        "fqn": symbol.fqn,
        "type_kind": symbol.type_kind,
        "modifiers": sorted(symbol.modifiers),
        "base_types": sorted(symbol.base_types),
        "attributes": [
            {"name": a.name, "args": a.args, "named_args": a.named_args} for a in symbol.attributes
        ],
        "members": sorted(
            (
                {
                    "name": m.name,
                    "kind": m.kind,
                    "signature": m.signature,
                    "attributes": sorted(a.name for a in m.attributes),
                }
                for m in symbol.members
            ),
            key=lambda m: (m["kind"], m["name"], m["signature"]),
        ),
    }
    return stable_hash(payload)


# --------------------------------------------------------------------------------------
# Пути документов
# --------------------------------------------------------------------------------------


def _kind_plural(kind: str) -> str:
    return f"{kind}s"


def _assign_doc_paths(nodes: list[DocNode]) -> list[DocNode]:
    """Развести узлы, претендующие на один файл.

    Суффикс считается от **id узла, а не от FQN**: там, где FQN совпадает
    (перегрузка по арности), хэш от FQN не различал бы ничего. На ABP внутри
    одного модуля 195 групп типов дают одинаковый slug, и в 105 из них FQN
    тоже одинаков.
    """
    grouped: defaultdict[str, list[DocNode]] = defaultdict(list)
    for node in nodes:
        grouped[node.doc_path].append(node)

    resolved: list[DocNode] = []
    for path, group in grouped.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue
        base = path.removesuffix(".md")
        for node in group:
            suffix = stable_hash(node.id).removeprefix("sha256:")[:8]
            resolved.append(node.model_copy(update={"doc_path": f"{base}-{suffix}.md"}))
    return resolved


# --------------------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------------------


def build_nodes(
    symbols: dict[str, Symbol],
    modules: list[Module],
    ruleset: Ruleset,
    config: DocpipeConfig,
    registrations: list[DiRegistration] | None = None,
    endpoints: dict[str, list[Endpoint]] | None = None,
) -> tuple[list[Module], list[DocNode]]:
    """Собрать модули с доменами и узлы документации.

    `registrations` и `endpoints` необязательны: без них узлы получаются
    беднее, но структура дерева не меняется — это позволяет собирать дерево
    в тестах, не поднимая весь пайплайн.
    """
    configured = _apply_config(modules, config)
    enrolled = {module.csproj: module for module in configured if module.enrolled}

    known_fqns = {symbol.fqn for symbol in symbols.values()}
    interfaces = {s.fqn for s in symbols.values() if s.type_kind == "interface"}
    # Простое имя -> FQN. Нужен для параметров конструктора: в сигнатуре тип
    # записан так, как в коде, то есть обычно без namespace.
    simple_names: dict[str, str] = {}
    for symbol in sorted(symbols.values(), key=lambda s: s.fqn):
        simple_names.setdefault(symbol.name, symbol.fqn)

    all_registrations = registrations or []
    nodes: list[DocNode] = []

    for key in sorted(symbols):
        symbol = symbols[key]
        module = enrolled.get(symbol.module)
        if module is None:
            continue

        classification = classify(symbol, ruleset)
        if classification is None:
            continue

        identifier = node_id(module.csproj, symbol)
        dependencies = _constructor_dependencies(symbol, known_fqns, simple_names)
        dependencies += _di_dependencies(symbol, all_registrations, known_fqns, simple_names)

        nodes.append(
            DocNode(
                id=identifier,
                kind=classification.kind,
                template=classification.template,
                title=symbol.name,
                doc_path=(
                    f"docs/modules/{module.name}/"
                    f"{_kind_plural(classification.kind)}/{slugify(symbol.name)}.md"
                ),
                parent=module.id,
                module=module.name,
                domain=module.domain,
                symbol=symbol,
                endpoints=(endpoints or {}).get(key, []),
                dependencies=_unique_sorted(dependencies),
                related=_relations(symbol, interfaces),
                matched_rules=classification.matched_rules,
                signature_hash=signature_hash(symbol),
                impl_hash=symbol.impl_hash,
            )
        )

    return configured, sorted(_assign_doc_paths(nodes), key=lambda node: node.id)
