"""Сопоставление манифеста и графа (G02).

Два пространства имён и одно сопоставление. У манифеста ключ уже есть —
модуль, FQN, арность; узлы графа приходят из разбора и адресуются файлом
и именем. Сходятся они по файлу и имени: оба источника знают файл, а имя
типа и имя члена — то немногое, что у них общее.

**Что здесь считается, а что нет.** Манифест — дерево документации, а не
полный список объявлений: в нём лежат только те символы, про которые правила
сказали «документируем». Поэтому число «есть в графе — нет в манифесте»
велико по построению (на открытом репозитории 18 узлов манифеста против
253 символов), и это состояние настройки правил, а не дефект. Полезно
обратное число: документированный узел, которого нет в графе, — это дыра
разбора, и её видно сразу.

**Перегрузки различает манифест, а не граф.** У стороннего разбора одно имя
на все перегрузки: разводить их нечем, потому что информации нет в источнике.
Ключ с отпечатком параметров существует (`identity.member_key`) и работает
на стороне манифеста; на стороне графа перегрузки остаются одним узлом,
и их число печатается. Это измеренная граница источника, а не выбор.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.graph.identity import symbol_member_key, symbol_type_key
from docpipe.graph.model import GraphNode
from docpipe.model import Manifest

EXAMPLES: Final[int] = 10


@dataclass(frozen=True)
class MatchReport:
    """Три числа и примеры к двум из них — ровно то, что требует приёмка."""

    matched: int = 0
    only_manifest: int = 0
    only_graph: int = 0
    ambiguous: int = 0
    examples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_counts(self) -> dict[str, int]:
        return {
            "сопоставлено с манифестом": self.matched,
            "есть в манифесте — нет в графе": self.only_manifest,
            "есть в графе — нет в манифесте": self.only_graph,
            "узлов графа на несколько объявлений манифеста": self.ambiguous,
        }


def _manifest_index(
    manifest: Manifest,
) -> tuple[
    dict[tuple[str, str], list[dict[str, str]]], dict[tuple[str, str, str], list[dict[str, str]]]
]:
    """Разложить манифест по парам «файл + имя» — по одной записи на объявление."""
    types: dict[tuple[str, str], list[dict[str, str]]] = {}
    members: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for node in manifest.nodes:
        symbol = node.symbol
        if symbol is None:
            continue
        key = symbol_type_key(symbol)
        common = {
            "symbol_key": key,
            "doc_node": node.id,
            "module": symbol.module,
            "fqn": symbol.fqn,
        }
        # `sources` — список, потому что `partial class` живёт в нескольких
        # файлах: искать надо по каждому, иначе половина объявлений не найдётся.
        for source in symbol.sources:
            types.setdefault((source.path, symbol.name), []).append(dict(common))
            for member in symbol.members:
                entry = dict(common)
                entry["member_key"] = symbol_member_key(symbol, member)
                entry["member_kind"] = member.kind
                members.setdefault((source.path, symbol.name, member.name), []).append(entry)
    return types, members


def match(
    nodes: tuple[GraphNode, ...], manifest: Manifest
) -> tuple[dict[str, dict[str, str]], MatchReport]:
    """Сопоставить узлы графа с манифестом.

    Возвращает атрибуты для узлов, которые нашли пару, и отчёт. Порядок
    на входе на результат не влияет: всё, что попадает в отчёт, сортируется
    явным ключом.
    """
    types, members = _manifest_index(manifest)
    attributes: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    matched = ambiguous = 0
    unmatched: list[str] = []

    for node in sorted(nodes, key=lambda item: item.key):
        if node.kind == "type":
            candidates = types.get((node.file, node.name), [])
        elif node.kind == "member":
            # Имя типа-владельца — последний сегмент: у вложенного типа
            # владелец записан как `Outer.Inner`, а в манифесте имя типа
            # короткое.
            owner = node.owner.rsplit(".", 1)[-1]
            candidates = members.get((node.file, owner, node.name), [])
        else:
            continue

        if not candidates:
            unmatched.append(node.key)
            continue

        matched += 1
        first = candidates[0]
        found = {name: value for name, value in first.items() if value}
        if len(candidates) > 1:
            ambiguous += 1
            # Перегрузки: у графа один узел на все. Число печатается,
            # выбор одного из нескольких молча не делается.
            found["declarations"] = str(len(candidates))
            found.pop("member_key", None)
        attributes[node.key] = found
        used.add(first["symbol_key"])
        if "member_key" in first:
            used.add(first["member_key"])

    only_manifest = sorted(
        {entry["symbol_key"] for entries in types.values() for entry in entries} - used
    )
    report = MatchReport(
        matched=matched,
        only_manifest=len(only_manifest),
        only_graph=len(unmatched),
        ambiguous=ambiguous,
        examples={
            "есть в манифесте — нет в графе": tuple(only_manifest[:EXAMPLES]),
            "есть в графе — нет в манифесте": tuple(sorted(unmatched)[:EXAMPLES]),
        },
    )
    return attributes, report


def apply(
    nodes: tuple[GraphNode, ...], attributes: dict[str, dict[str, str]]
) -> tuple[GraphNode, ...]:
    """Дописать узлам найденные атрибуты, не трогая ключи.

    Ключ узла графа не меняется на ключ манифеста намеренно: пространство
    ключей индекса должно быть одно, иначе половина узлов адресуется одним
    способом, а половина — другим, и первый же запрос это перепутает.
    Связь с манифестом живёт полем, а не подменой ключа.
    """
    return tuple(
        node.model_copy(update={"attributes": {**node.attributes, **attributes[node.key]}})
        if node.key in attributes
        else node
        for node in nodes
    )
