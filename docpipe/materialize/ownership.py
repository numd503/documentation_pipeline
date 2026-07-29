"""Владение: `DocNode` → команда, плюс диагностика набора правил.

Считается **на шаге 2**, а не в манифесте. Состав команд меняется чаще кода;
попав в `DocNode`, владение сделало бы `doc-tree.json` зависящим от орг-структуры
и сломало бы свойство «манифест — чистая функция кода и правил классификации».

Владелец определяется по узлу, а не по модулю. Соблазн «команда = проект»
разбивается о шэренный `.csproj` — ровно ту ситуацию, ради которой владение
и заводится.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from docpipe.discovery import matches_glob
from docpipe.model import DocNode
from docpipe.ruleset import (
    PredicateTable,
    evaluate,
    load_rule_items,
    pick_winner,
    validate_condition,
)

_TOP: Final[int] = 10


@dataclass(frozen=True)
class Team:
    id: str
    title: str


@dataclass(frozen=True)
class OwnershipRule:
    id: str
    team: str
    priority: int
    when: dict[str, Any]


@dataclass(frozen=True)
class Ownership:
    version: str
    ownership_version: str
    teams: list[Team]
    rules: list[OwnershipRule]


@dataclass(frozen=True)
class OwnerDecision:
    """Кто владеет узлом и почему.

    `matched` содержит **все** совпавшие правила, а не только победившее: без
    этого настройка границ команд превращается в гадание. `tie` отмечает случай,
    когда максимальный приоритет делят несколько правил — формально решение
    детерминированно, но почти всегда это забытый `priority`.
    """

    team: str | None
    matched: list[OwnershipRule] = field(default_factory=list)
    winner: OwnershipRule | None = None
    tie: bool = False


# --------------------------------------------------------------------------------------
# Предикаты над узлом
# --------------------------------------------------------------------------------------


def _path_glob(node: DocNode, values: list[str]) -> bool:
    # `matches_glob`, а не `fnmatch`: последний не понимает `**` как «ноль или
    # больше сегментов», и `**/Pricing/**` не поймал бы `Pricing/CurveStore.cs`
    # в корне модуля.
    #
    # Истинно, если совпал ХОТЯ БЫ ОДИН источник. У `partial`-класса файлы
    # бывают в каталогах разных команд; правило «любой источник» предсказуемо,
    # а «все источники» приводит к тому, что такой тип не принадлежит никому,
    # и это выглядит как дефект инструмента. Такие узлы идут в предупреждения.
    sources = node.symbol.sources if node.symbol else []
    return any(matches_glob(source.path, value) for source in sources for value in values)


def _module(node: DocNode, values: list[str]) -> bool:
    return node.module in values


def _module_glob(node: DocNode, values: list[str]) -> bool:
    # По пути `.csproj`, а не по имени: имена проектов не уникальны.
    csproj = (node.parent or "").removeprefix("module:")
    return any(matches_glob(csproj, value) for value in values)


def _domain(node: DocNode, values: list[str]) -> bool:
    return node.domain in values


def _kind(node: DocNode, values: list[str]) -> bool:
    return node.kind in values


def _namespace_prefix(node: DocNode, values: list[str]) -> bool:
    namespace = node.symbol.namespace if node.symbol else ""
    return any(namespace.startswith(value) for value in values)


def _namespace_regex(node: DocNode, values: list[str]) -> bool:
    namespace = node.symbol.namespace if node.symbol else ""
    return any(re.fullmatch(value, namespace) for value in values)


def _fqn_prefix(node: DocNode, values: list[str]) -> bool:
    fqn = node.symbol.fqn if node.symbol else ""
    return any(fqn.startswith(value) for value in values)


# Восемь предикатов, намеренно мало — та же причина, что в `classify.py`:
# большой набор означает, что настройщик каждый раз выбирает из десяти способов
# написать одно и то же.
TABLE: Final[PredicateTable] = PredicateTable(
    predicates={
        "path_glob": _path_glob,
        "module": _module,
        "module_glob": _module_glob,
        "domain": _domain,
        "kind": _kind,
        "namespace_prefix": _namespace_prefix,
        "namespace_regex": _namespace_regex,
        "fqn_prefix": _fqn_prefix,
    },
    regex_keys=frozenset({"namespace_regex"}),
)


# --------------------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------------------


def load_ownership(path: Path) -> Ownership:
    """Загрузить правила владения с полной проверкой.

    Проверяется при загрузке: правило с несуществующей командой или с опечаткой
    в предикате просто никогда не сработает, и узлы молча окажутся ничьими.
    """
    raw: Any = yaml.safe_load(path.read_bytes().decode("utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: правила владения должны быть словарём")

    teams: list[Team] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("teams") or []):
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError(f"{path}: команда #{index} должна быть словарём с ключом `id`")
        if item["id"] in seen:
            raise ValueError(f"{path}: повтор id команды {item['id']!r}")
        seen.add(item["id"])
        teams.append(Team(id=item["id"], title=str(item.get("title", item["id"]))))

    rules: list[OwnershipRule] = []
    for item in load_rule_items(raw.get("rules"), path, {"id", "team", "priority", "when"}):
        if item["team"] not in seen:
            known = ", ".join(sorted(seen)) or "(список пуст)"
            raise ValueError(
                f"{path}: правило {item['id']!r} назначает команду {item['team']!r},"
                f" которой нет в `teams`; объявлены: {known}"
            )
        validate_condition(item["when"], f"{path}:{item['id']}.when", TABLE)
        rules.append(
            OwnershipRule(
                id=item["id"],
                team=item["team"],
                priority=int(item["priority"]),
                when=item["when"],
            )
        )

    return Ownership(
        version=str(raw.get("version", "1")),
        ownership_version=str(raw.get("ownership_version", "")),
        teams=teams,
        rules=rules,
    )


# --------------------------------------------------------------------------------------
# Применение
# --------------------------------------------------------------------------------------


def owner_of(node: DocNode, ownership: Ownership) -> OwnerDecision:
    """Владелец узла.

    Правила **только положительные**: более специфичное выражается более высоким
    приоритетом, а не исключением из общего. Отрицаний нет намеренно — они
    превращают набор в систему уравнений, которую нельзя прочитать построчно.
    """
    matched = [rule for rule in ownership.rules if evaluate(rule.when, node, TABLE)]
    if not matched:
        return OwnerDecision(team=None)

    winner = pick_winner(matched)
    top = [rule for rule in matched if rule.priority == winner.priority]
    return OwnerDecision(
        team=winner.team,
        matched=sorted(matched, key=lambda rule: (-rule.priority, rule.id)),
        winner=winner,
        tie=len(top) > 1,
    )


def _top(values: list[str]) -> list[tuple[str, int]]:
    return Counter(values).most_common(_TOP)


def _slice(title: str, rows: list[tuple[str, int]]) -> list[str]:
    return [f"  {title}:"] + [f"    {count:6}  {name}" for name, count in rows]


def lint(nodes: list[DocNode], ownership: Ownership) -> tuple[list[str], list[str]]:
    """Диагностика набора правил: `(находки, предупреждения)`.

    Находка — то, что почти наверняка дефект настройки. Предупреждение — то, что
    формально корректно, но обычно означает недосмотр.
    """
    decisions = {node.id: owner_of(node, ownership) for node in nodes}
    used = {rule.id for decision in decisions.values() for rule in decision.matched}
    owned = {node.id for node in nodes if decisions[node.id].team}

    findings: list[str] = []

    # 1. Мёртвые правила — самый частый дефект сопровождения: каталог
    #    переименовали, правило осталось.
    dead = sorted(rule.id for rule in ownership.rules if rule.id not in used)
    if dead:
        findings.append(f"Правила, не совпавшие ни с одним узлом: {', '.join(dead)}")

    # 2. Узлы без владельца. Один счётчик бесполезен: именно срезы показывают,
    #    что дописать.
    orphans = [node for node in nodes if node.id not in owned]
    if orphans:
        findings.append(f"Узлов без владельца: {len(orphans)} из {len(nodes)}")
        findings += _slice("модули", _top([node.module for node in orphans]))
        findings += _slice(
            "каталоги внутри модуля",
            _top(
                [
                    (node.symbol.sources[0].path.rsplit("/", 1)[0] if node.symbol.sources else "?")
                    for node in orphans
                    if node.symbol
                ]
            ),
        )

    # 3. Команды без узлов.
    assigned = {decisions[node.id].team for node in nodes}
    idle = sorted(team.id for team in ownership.teams if team.id not in assigned)
    if idle:
        findings.append(f"Команды, которым не досталось ни одного узла: {', '.join(idle)}")

    warnings: list[str] = []

    # 4. Ничьи по приоритету. Формально детерминированно, но человек узнаёт
    #    об этом, только когда документ уезжает не в ту команду.
    ties = sorted(node.doc_path for node in nodes if decisions[node.id].tie)
    if ties:
        warnings.append(f"Ничьи по приоритету у {len(ties)} узлов, победил меньший id:")
        warnings += [f"    {path}" for path in ties[:_TOP]]

    # Partial-класс, чьи файлы лежат в зонах разных команд: правило «любой
    # источник» отдаёт его одной из них, и это стоит увидеть глазами.
    split = sorted(
        node.doc_path
        for node in nodes
        if node.symbol
        and len({source.path.rsplit("/", 1)[0] for source in node.symbol.sources}) > 1
        and decisions[node.id].team
    )
    if split:
        warnings.append(f"Типов, чьи файлы лежат в разных каталогах: {len(split)}")
        warnings += [f"    {path}" for path in split[:_TOP]]

    return findings, warnings


def explain(node: DocNode, ownership: Ownership) -> str:
    """Почему у узла именно этот владелец."""
    decision = owner_of(node, ownership)
    lines = [
        f"{node.title}  ({node.kind})",
        f"  узел:      {node.id}",
        f"  документ:  {node.doc_path}",
        f"  модуль:    {node.module}  ({(node.parent or '').removeprefix('module:')})",
        f"  домен:     {node.domain}",
    ]
    if node.symbol:
        lines.append(f"  namespace: {node.symbol.namespace or '(глобальный)'}")
        lines.append("  источники:")
        lines += [f"    {source.path}" for source in node.symbol.sources]

    lines.append("  совпавшие правила:")
    if not decision.matched:
        lines.append("    нет")
    for rule in decision.matched:
        mark = " ← победитель" if rule is decision.winner else ""
        lines.append(f"    {rule.priority:>4}  {rule.id:<28} → {rule.team}{mark}")

    if decision.tie:
        lines.append("  ничья по приоритету: победил лексикографически меньший id")
    lines.append(f"  владелец:  {decision.team or 'не задан'}")
    return "\n".join(lines)
