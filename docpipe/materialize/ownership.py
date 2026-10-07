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
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from docpipe.classify import matches_any_type
from docpipe.discovery import matches_glob
from docpipe.model import DocNode
from docpipe.ruleset import (
    PredicateTable,
    evaluate,
    load_rule_items,
    pick_winner,
    reject_unknown_keys,
    validate_condition,
)

_TOP: Final[int] = 10

# Допустимые ключи `ownership.yaml`. Без них `titel` у команды молча давал
# заголовок, равный `id`; `team:` вместо `teams:` наверху оставлял список
# команд пустым, и отказ звучал как «правило назначает команду, которой нет»
# — про правило, а не про опечатку; `priorty` у правила давал «без полей
# ['priority']», не называя написанного.
_OWNERSHIP_KEYS: Final[frozenset[str]] = frozenset(
    {"version", "ownership_version", "teams", "rules"}
)
_TEAM_KEYS: Final[frozenset[str]] = frozenset({"id", "title"})
_OWNERSHIP_RULE_KEYS: Final[frozenset[str]] = frozenset({"id", "team", "priority", "when"})


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


# --------------------------------------------------------------------------------------
# Предикаты по контракту типа
# --------------------------------------------------------------------------------------
#
# Те же три, что и в правилах классификации, с той же трактовкой имён: условие
# копируется из `rules/dotnet.yaml` в `ownership.yaml` буквально. Заведены потому,
# что команда, опознающая свои типы по базовому классу («наследник
# `MlApiControllerBase` — наш контроллер»), иначе обязана выразить ту же границу
# вторым способом, через путь. Два описания одной границы расходятся на первом
# же файле, переехавшем в другой каталог, и расхождение видно не сразу:
# документ просто уезжает не в ту команду.
#
# ЛОВУШКА, которой нет у предикатов расположения. `base_type_closure` строится
# по индексу символов прогона, и через модуль, исключённый в `docpipe.yaml`,
# наследование рвётся: правило по базовому типу оттуда молча не сработает, узел
# останется ничьим либо уедет к менее специфичному правилу. Ищется это в
# `docs owners --lint` — «правила, не совпавшие ни с одним узлом» и «узлов
# без владельца».


def _attribute(node: DocNode, values: list[str]) -> bool:
    names = {attribute.name for attribute in node.symbol.attributes} if node.symbol else set()
    return any(value in names for value in values)


def _base_type(node: DocNode, values: list[str]) -> bool:
    """Только прямые базы объявления."""
    if node.symbol is None:
        return False
    return matches_any_type(values, node.symbol.base_types + node.symbol.base_types_raw)


def _inherits(node: DocNode, values: list[str]) -> bool:
    """Всё замыкание наследования: сработает и на типе, который наследует
    названный базовый через свой промежуточный."""
    if node.symbol is None:
        return False
    return matches_any_type(values, node.symbol.base_type_closure)


# Одиннадцать предикатов: восемь про расположение узла (путь, проект, namespace,
# домен, FQN) плюс `kind` и три про контракт типа. Дальше набор намеренно
# не растёт — довод тот же, что в `classify.py`: большой набор означает, что
# настройщик каждый раз выбирает из десяти способов написать одно и то же.
# `name_suffix`, `name_regex`, `type_kind`, `modifier` не заведены сознательно:
# они описывают форму типа, а не границу ответственности, и владение по имени
# уже выражается `fqn_prefix`.
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
        "attribute": _attribute,
        "base_type": _base_type,
        "inherits": _inherits,
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
    reject_unknown_keys(raw, _OWNERSHIP_KEYS, str(path))

    teams: list[Team] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("teams") or []):
        if isinstance(item, dict):
            label = f" ({item['id']})" if "id" in item else ""
            reject_unknown_keys(item, _TEAM_KEYS, f"{path}: команда #{index}{label}")
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError(f"{path}: команда #{index} должна быть словарём с ключом `id`")
        if item["id"] in seen:
            raise ValueError(f"{path}: повтор id команды {item['id']!r}")
        seen.add(item["id"])
        teams.append(Team(id=item["id"], title=str(item.get("title", item["id"]))))

    rules: list[OwnershipRule] = []
    for item in load_rule_items(
        raw.get("rules"),
        path,
        {"id", "team", "priority", "when"},
        allowed=_OWNERSHIP_RULE_KEYS,
    ):
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


# Версия отчёта `docs owners --lint --format json`.
LINT_SCHEMA_VERSION: Final[Literal["1.0"]] = "1.0"

# По коду на каждый вид строки, который печатает `lint`. Порядок — порядок
# строк в тексте: по нему же собирается текстовая обёртка.
LintCode = Literal[
    "dead-rule",
    "unowned-nodes",
    "unowned-module",
    "unowned-directory",
    "idle-team",
    "priority-tie",
    "split-type",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OwnershipFinding(_Model):
    """Одна находка линта владения.

    `subject` — то, о чём находка: id правила или команды, модуль, каталог,
    `doc_path` узла; у сводки `unowned-nodes` — пустая строка, она о дереве
    целиком. `count` — число, ради которого находка заведена: узлов без
    владельца (в дереве, модуле, каталоге), правил с равным приоритетом
    у ничьей, каталогов у типа, разложенного по нескольким; у мёртвого
    правила и команды без узлов — ноль совпавших узлов.
    """

    code: LintCode
    subject: str
    count: int
    message: str


class OwnershipLint(_Model):
    """Диагностика набора правил владения структурой.

    `findings` — почти наверняка дефект настройки, `warnings` — формально
    корректно, но обычно недосмотр: то же деление, что у текста (stdout
    и stderr). Списки полные; срез по десять — дело печати (`lint`), иначе
    агент, читающий JSON, не узнал бы, что модулей без владельца больше.
    """

    schema_version: Literal["1.0"] = LINT_SCHEMA_VERSION
    nodes: int
    findings: list[OwnershipFinding]
    warnings: list[OwnershipFinding]


def _ranked(values: list[str]) -> list[tuple[str, int]]:
    """Счётчик по убыванию, при равенстве по имени.

    Явный ключ, а не `Counter.most_common`: тот при равенстве оставляет
    порядок первого появления, то есть порядок узлов в манифесте, и срез
    «топ-10» зависел бы от того, как отсортированы узлы.
    """
    return sorted(Counter(values).items(), key=lambda item: (-item[1], item[0]))


def lint_findings(nodes: list[DocNode], ownership: Ownership) -> OwnershipLint:
    """Диагностика набора правил: находки и предупреждения структурой."""
    decisions = {node.id: owner_of(node, ownership) for node in nodes}
    used = {rule.id for decision in decisions.values() for rule in decision.matched}
    owned = {node.id for node in nodes if decisions[node.id].team}

    findings: list[OwnershipFinding] = []

    # 1. Мёртвые правила — самый частый дефект сопровождения: каталог
    #    переименовали, правило осталось.
    findings += [
        OwnershipFinding(
            code="dead-rule",
            subject=rule_id,
            count=0,
            message="правило не совпало ни с одним узлом",
        )
        for rule_id in sorted(rule.id for rule in ownership.rules if rule.id not in used)
    ]

    # 2. Узлы без владельца. Один счётчик бесполезен: именно срезы показывают,
    #    что дописать.
    orphans = [node for node in nodes if node.id not in owned]
    if orphans:
        findings.append(
            OwnershipFinding(
                code="unowned-nodes",
                subject="",
                count=len(orphans),
                message=f"узлов без владельца: {len(orphans)} из {len(nodes)}",
            )
        )
        findings += [
            OwnershipFinding(
                code="unowned-module",
                subject=module,
                count=count,
                message=f"узлов без владельца в модуле: {count}",
            )
            for module, count in _ranked([node.module for node in orphans])
        ]
        findings += [
            OwnershipFinding(
                code="unowned-directory",
                subject=directory,
                count=count,
                message=f"узлов без владельца в каталоге: {count}",
            )
            for directory, count in _ranked(
                [
                    (node.symbol.sources[0].path.rsplit("/", 1)[0] if node.symbol.sources else "?")
                    for node in orphans
                    if node.symbol
                ]
            )
        ]

    # 3. Команды без узлов.
    assigned = {decisions[node.id].team for node in nodes}
    findings += [
        OwnershipFinding(
            code="idle-team",
            subject=team_id,
            count=0,
            message="команде не досталось ни одного узла",
        )
        for team_id in sorted(team.id for team in ownership.teams if team.id not in assigned)
    ]

    warnings: list[OwnershipFinding] = []

    # 4. Ничьи по приоритету. Формально детерминированно, но человек узнаёт
    #    об этом, только когда документ уезжает не в ту команду.
    for node in sorted(nodes, key=lambda item: (item.doc_path, item.id)):
        decision = decisions[node.id]
        if decision.tie and decision.winner is not None:
            top = decision.winner.priority
            rivals = sum(1 for rule in decision.matched if rule.priority == top)
            warnings.append(
                OwnershipFinding(
                    code="priority-tie",
                    subject=node.doc_path,
                    count=rivals,
                    message=f"ничья по приоритету между {rivals} правилами, победил меньший id",
                )
            )

    # Partial-класс, чьи файлы лежат в зонах разных команд: правило «любой
    # источник» отдаёт его одной из них, и это стоит увидеть глазами.
    for node in sorted(nodes, key=lambda item: (item.doc_path, item.id)):
        if node.symbol is None or not decisions[node.id].team:
            continue
        folders = len({source.path.rsplit("/", 1)[0] for source in node.symbol.sources})
        if folders > 1:
            warnings.append(
                OwnershipFinding(
                    code="split-type",
                    subject=node.doc_path,
                    count=folders,
                    message=f"файлы типа лежат в {folders} каталогах",
                )
            )

    return OwnershipLint(nodes=len(nodes), findings=findings, warnings=warnings)


def _slice(title: str, rows: list[OwnershipFinding]) -> list[str]:
    return [f"  {title}:"] + [f"    {row.count:6}  {row.subject}" for row in rows[:_TOP]]


def _of(rows: list[OwnershipFinding], code: LintCode) -> list[OwnershipFinding]:
    return [row for row in rows if row.code == code]


def format_lint(report: OwnershipLint) -> tuple[list[str], list[str]]:
    """Текст линта: `(находки, предупреждения)` строками, срезы — по десять."""
    findings: list[str] = []

    dead = _of(report.findings, "dead-rule")
    if dead:
        names = ", ".join(row.subject for row in dead)
        findings.append(f"Правила, не совпавшие ни с одним узлом: {names}")

    for summary in _of(report.findings, "unowned-nodes"):
        findings.append(f"Узлов без владельца: {summary.count} из {report.nodes}")
        findings += _slice("модули", _of(report.findings, "unowned-module"))
        findings += _slice("каталоги внутри модуля", _of(report.findings, "unowned-directory"))

    idle = _of(report.findings, "idle-team")
    if idle:
        names = ", ".join(row.subject for row in idle)
        findings.append(f"Команды, которым не досталось ни одного узла: {names}")

    warnings: list[str] = []

    ties = _of(report.warnings, "priority-tie")
    if ties:
        warnings.append(f"Ничьи по приоритету у {len(ties)} узлов, победил меньший id:")
        warnings += [f"    {row.subject}" for row in ties[:_TOP]]

    split = _of(report.warnings, "split-type")
    if split:
        warnings.append(f"Типов, чьи файлы лежат в разных каталогах: {len(split)}")
        warnings += [f"    {row.subject}" for row in split[:_TOP]]

    return findings, warnings


def lint(nodes: list[DocNode], ownership: Ownership) -> tuple[list[str], list[str]]:
    """Диагностика набора правил: `(находки, предупреждения)` строками.

    Находка — то, что почти наверняка дефект настройки. Предупреждение — то, что
    формально корректно, но обычно означает недосмотр. Текстовая обёртка над
    `lint_findings`: структура — для агента, строки — для человека и для тех,
    кто зовёт `lint` по-старому.
    """
    return format_lint(lint_findings(nodes, ownership))


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
