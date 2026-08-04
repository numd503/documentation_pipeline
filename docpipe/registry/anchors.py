"""Инвентаризация точек входа: записи реестров, сопоставленные с манифестом.

Отвечает на вопрос «сколько в системе точек входа и что их реализует». Это
первый артефакт бизнес-слоя, полезный сам по себе: такой таблицы в АС CF,
по-видимому, не существует, а без неё объём работы по каталогу процессов —
догадка.

Пакет не импортирует `docpipe.dotnet`: резолв идёт по манифесту, который
языконезависим.
"""

from collections import Counter, defaultdict
from difflib import get_close_matches
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.model import DocNode, Manifest
from docpipe.registry.model import RegistryItem, RegistryResult
from docpipe.registry.parse import split_type_name, strip_generic_arity

# Виды якорей, которые являются точками входа. Остальное (поля списка, шаги
# workflow) описывает устройство, а не вход, и в счётчик входов не идёт.
ENTRY_KINDS: Final[frozenset[str]] = frozenset(
    {"grid_service", "job", "workflow", "list_event", "kafka_topic", "http"}
)

# Поля, значение которых — имя типа. Соглашение об именах вместо разбора по
# видам реестра: новый реестр достаточно описать, назвав поле так же.
TYPE_FIELDS: Final[tuple[str, ...]] = ("impl_fqn", "contract_fqn", "impl_type", "data_type")


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnchorTarget(_Base):
    """Тип, на который ссылается запись реестра, и его узел документации.

    `node_id` пуст, если узла нет. Это **не** означает, что типа не существует:
    узлами становятся только enrolled и классифицированные типы. Отличить
    «типа нет» от «тип не документируется» можно лишь по индексу символов,
    которого в манифесте нет.
    """

    field: str
    fqn: str
    assembly: str | None = None
    via: Literal["direct", "implementation", "unresolved"]
    node_id: str | None = None
    doc_path: str | None = None
    module: str | None = None


class ResolvedAnchor(_Base):
    """Одна точка входа или одна сущность, сопоставленная с кодом."""

    kind: str
    ref: str
    scope: str | None = None
    version: str | None = None
    registry: str
    source_path: str
    title: str | None = None
    team: str | None = None
    targets: list[AnchorTarget] = Field(default_factory=list)
    children: list["ResolvedAnchor"] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)

    @property
    def display(self) -> str:
        """Строка для человека. Обратно **не разбирается**.

        `JOBTITLE` содержит пробелы и двоеточия, заголовок workflow — кириллицу:
        любой парсер такой строки был бы источником багов.
        """
        text = f"{self.scope}/{self.ref}" if self.scope else self.ref
        return f"{text}@{self.version}" if self.version else text

    @property
    def resolved(self) -> bool:
        return any(target.node_id for target in self.targets)


ResolvedAnchor.model_rebuild()


class ManifestIndex(_Base):
    """Два индекса по манифесту: по FQN и по реализуемым интерфейсам."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    by_fqn: dict[str, list[DocNode]] = Field(default_factory=dict)
    implementors: dict[str, list[DocNode]] = Field(default_factory=dict)


def build_index(manifest: Manifest) -> ManifestIndex:
    """Собрать индексы.

    Обратного ребра `implemented_by` в манифесте нет — `tree._relations` его
    намеренно не создаёт, потому что интерфейс мог не стать узлом. Поэтому
    индекс реализаций строится перебором `related` у каждого узла.
    """
    by_fqn: defaultdict[str, list[DocNode]] = defaultdict(list)
    implementors: defaultdict[str, list[DocNode]] = defaultdict(list)

    for node in manifest.nodes:
        if node.symbol is not None:
            by_fqn[node.symbol.fqn].append(node)
        for relation in node.related:
            if relation.relation == "implements":
                implementors[relation.target].append(node)

    def ordered(nodes: list[DocNode]) -> list[DocNode]:
        """FQN не уникален (на ABP 255 коллизий), поэтому кандидатов бывает
        несколько, и их порядок обязан быть детерминированным."""
        return sorted(nodes, key=lambda node: node.id)

    return ManifestIndex(
        by_fqn={fqn: ordered(nodes) for fqn, nodes in sorted(by_fqn.items())},
        implementors={fqn: ordered(nodes) for fqn, nodes in sorted(implementors.items())},
    )


def _target(field: str, raw: str, index: ManifestIndex) -> list[AnchorTarget]:
    """Сопоставить имя типа из реестра с узлами.

    Сначала прямое совпадение, затем реализации интерфейса. Такой порядок
    обязателен: `JOBCLASS` — интерфейс, а интерфейсы в наборе правил
    по умолчанию не документируются (`type_kind: ["class", "record"]`), и без
    второго шага каждый джоб выглядел бы неразрешённым.
    """
    fqn, assembly = split_type_name(raw)
    fqn = strip_generic_arity(fqn)

    nodes = index.by_fqn.get(fqn)
    via: Literal["direct", "implementation", "unresolved"] = "direct"
    if not nodes:
        nodes = index.implementors.get(fqn)
        via = "implementation"

    if not nodes:
        return [AnchorTarget(field=field, fqn=fqn, assembly=assembly, via="unresolved")]

    return [
        AnchorTarget(
            field=field,
            fqn=fqn,
            assembly=assembly,
            via=via,
            node_id=node.id,
            doc_path=node.doc_path,
            module=node.module,
        )
        for node in nodes
    ]


def _targets(item: RegistryItem, index: ManifestIndex) -> list[AnchorTarget]:
    targets: list[AnchorTarget] = []
    for field in TYPE_FIELDS:
        raw = item.fields.get(field)
        if raw:
            targets.extend(_target(field, raw, index))
    return targets


def _anchor(item: RegistryItem, index: ManifestIndex, scope: str | None = None) -> ResolvedAnchor:
    # Дети, которые сами являются точками входа, поднимаются на верхний
    # уровень отдельными якорями: обработчик события — это вход, а список,
    # внутри которого он объявлен, — нет.
    children = [_anchor(child, index) for child in item.children if child.kind not in ENTRY_KINDS]
    return ResolvedAnchor(
        kind=item.kind,
        ref=item.ref,
        scope=scope,
        version=item.fields.get("version"),
        registry=item.registry,
        source_path=item.source_path,
        title=item.fields.get("title") or item.fields.get("display_name"),
        team=item.fields.get("team"),
        targets=_targets(item, index),
        children=children,
        fields=item.fields,
    )


def resolve_anchors(results: list[RegistryResult], manifest: Manifest) -> list[ResolvedAnchor]:
    """Развернуть записи реестров в плоский список якорей."""
    index = build_index(manifest)
    anchors: list[ResolvedAnchor] = []

    for result in results:
        for item in result.items:
            anchors.append(_anchor(item, index))
            for child in item.children:
                if child.kind in ENTRY_KINDS:
                    anchors.append(_anchor(child, index, scope=item.ref))

    return sorted(anchors, key=lambda a: (a.kind, a.scope or "", a.ref, a.version or ""))


class AnchorMatch(_Base):
    """Якорь, найденный по реализации, и то, чем именно он совпал.

    `scope` берётся у родителя, а не у самой записи: шаги workflow и поля
    списка на верхний уровень не поднимаются, но искать по ним надо — команда
    чаще владеет шагом, чем процессом целиком.

    `siblings` — остальные записи того же якоря. У пары «список + EventType»
    подписчиков бывает несколько, и увидеть их в момент выбора якоря важнее,
    чем потом объяснять, откуда в документе чужой класс.
    """

    anchor: ResolvedAnchor
    scope: str | None
    matched_field: str
    matched_fqn: str
    siblings: list[ResolvedAnchor] = Field(default_factory=list)

    @property
    def display(self) -> str:
        text = f"{self.scope}/{self.anchor.ref}" if self.scope else self.anchor.ref
        return f"{text}@{self.anchor.version}" if self.anchor.version else text


def _matches(anchor: ResolvedAnchor, query: str) -> tuple[str, str] | None:
    """Совпадение записи с запросом: FQN, `doc_path` или простое имя типа.

    Простое имя разрешено намеренно: человек помнит `PricingService`, а не
    полное имя с namespace. Двусмысленность при этом не скрывается — совпавших
    печатается столько, сколько нашлось.
    """
    for target in anchor.targets:
        if query in (target.fqn, target.doc_path, target.fqn.rsplit(".", 1)[-1]):
            return target.field, target.fqn
    return None


def similar_names(anchors: list[ResolvedAnchor], query: str, limit: int = 5) -> list[str]:
    """Близкие имена типов среди тех, что вызываются по данным.

    Нужны ровно в одном случае: запрос отличается от настоящего имени
    на опечатку. Тогда «не найдено» отправляет человека проверять реестры,
    которые в порядке, — а надо всего лишь дописать букву.

    Сравниваются и полные имена, и простые: человек ошибается чаще в коротком.
    """
    pool: set[str] = set()
    for anchor in anchors:
        for candidate in [anchor, *anchor.children]:
            for target in candidate.targets:
                pool.add(target.fqn)
                pool.add(target.fqn.rsplit(".", 1)[-1])

    matched = get_close_matches(query, sorted(pool), n=limit, cutoff=0.7)
    # Полное имя информативнее простого, поэтому при совпадении хвоста
    # короткая форма убирается: две строки про один тип ничего не добавляют.
    full = {name for name in matched if "." in name}
    return [
        name
        for name in matched
        if "." in name or not any(other.endswith(f".{name}") for other in full)
    ]


def find_by_implementation(anchors: list[ResolvedAnchor], query: str) -> list[AnchorMatch]:
    """Обратный поиск: от типа к якорям, которые на него ссылаются.

    Направление «реестр → код» даёт `anchors list`, но аналитик начинает
    с другого конца: он знает свой класс и не знает, какой строкой его
    вызывают. Без этой команды остаётся глазами просматривать перечень
    на сотни записей, а данные для ответа уже посчитаны.
    """
    found: list[AnchorMatch] = []

    # Соседи ищутся в том же наборе, откуда пришёл кандидат: у поднятого
    # обработчика события это верхний уровень, у шага workflow — дети своего
    # workflow. Единый перебор по `anchors` не нашёл бы вторых вовсе.
    pools: list[tuple[list[ResolvedAnchor], str | None]] = [(anchors, None)]
    pools += [(anchor.children, anchor.ref) for anchor in anchors if anchor.children]

    for pool, parent_ref in pools:
        for candidate in pool:
            matched = _matches(candidate, query)
            if matched is None:
                continue
            scope = parent_ref if parent_ref is not None else candidate.scope
            siblings = [
                other
                for other in pool
                if other is not candidate
                and other.kind == candidate.kind
                and other.ref == candidate.ref
            ]
            found.append(
                AnchorMatch(
                    anchor=candidate,
                    scope=scope,
                    matched_field=matched[0],
                    matched_fqn=matched[1],
                    siblings=siblings,
                )
            )

    return sorted(found, key=lambda m: (m.anchor.kind, m.scope or "", m.anchor.ref, m.matched_fqn))


def filter_anchors(
    anchors: list[ResolvedAnchor], kinds: list[str], teams: list[str]
) -> list[ResolvedAnchor]:
    """Сузить множество. Пустой фильтр не сужает ничего."""
    selected = anchors
    if kinds:
        selected = [anchor for anchor in selected if anchor.kind in set(kinds)]
    if teams:
        selected = [anchor for anchor in selected if anchor.team in set(teams)]
    return selected


def counts(anchors: list[ResolvedAnchor]) -> dict[str, int]:
    return dict(sorted(Counter(anchor.kind for anchor in anchors).items()))


def format_which(
    matches: list[AnchorMatch],
    query: str,
    snippets: list[str],
    similar: list[str] | None = None,
) -> str:
    """Отчёт обратного поиска.

    Готовые куски `entry` приходят снаружи по одному на совпадение: формат
    документа — знание бизнес-слоя, и реестр о нём знать не должен, иначе
    зависимость пойдёт в обратную сторону.
    """
    if not matches:
        lines = [
            f"Якорей на {query} не найдено.",
            "  Это нормальное состояние: точка входа есть не у всякого типа.",
            "  Если тип точно вызывается по данным — проверьте, описан ли",
            "  соответствующий реестр в registries.yaml.",
        ]
        # «Не найдено» и «нашлось похожее» — разные ответы. Опечатка в одну
        # букву даёт первый на второй случай, и человек идёт проверять реестры,
        # которые в порядке.
        if similar:
            lines += ["", "  Похожие имена среди тех, что вызываются по данным:"]
            lines += [f"    {name}" for name in similar]
        return "\n".join(lines)

    # Один и тот же якорь, объявленный в нескольких файлах, — это версии
    # workflow. Сниппет для такого шага вставлять нельзя: без `version` он
    # неоднозначен, а `version` у записи шага нет. Молча напечатать его
    # значило бы выдать заведомо нерабочий кусок за готовый к вставке.
    seen = Counter((m.anchor.kind, m.scope or "", m.anchor.ref) for m in matches)

    lines = [f"Найдено якорей: {len(matches)}"]
    for match, snippet in zip(matches, snippets, strict=True):
        lines += [
            "",
            f"{match.anchor.kind}  {match.display}",
            f"  реестр:  {match.anchor.registry}",
            f"  файл:    {match.anchor.source_path or '—'}",
            f"  совпало: {match.matched_field} = {match.matched_fqn}",
        ]
        if match.anchor.team:
            lines.append(f"  команда: {match.anchor.team}")
        for sibling in match.siblings:
            other = sibling.fields.get("impl_fqn") or sibling.fields.get("contract_fqn") or "—"
            assembly = sibling.fields.get("assembly")
            suffix = f"  (сборка {assembly})" if assembly else ""
            lines.append(f"  на том же якоре ещё: {other}{suffix}")
        if match.siblings:
            # Сказать это надо в момент выбора якоря, а не когда чужой класс
            # уже появился в собранном документе: якорь адресует контракт,
            # а не подписчика, и разделить их нельзя.
            lines.append("  якорь общий на всех: он адресует контракт, а не класс")

        if seen[(match.anchor.kind, match.scope or "", match.anchor.ref)] > 1:
            lines += [
                "  ОСТОРОЖНО: такой якорь объявлен в нескольких файлах —"
                " это разные версии workflow.",
                "  Сниппет ниже неоднозначен: `version` у записи шага нет, и линт назовёт его",
                "  `ambiguous-version`. Сошлитесь на workflow целиком"
                " с `version` либо опишите шаг прозой.",
            ]

        lines += ["", *snippet.rstrip("\n").splitlines()]

    return "\n".join(lines)


def format_anchors(anchors: list[ResolvedAnchor], errors: list[str]) -> str:
    """Текстовый отчёт: счётчики, перечень, неразрешённые ссылки."""
    by_kind = counts(anchors)
    entries = sum(count for kind, count in by_kind.items() if kind in ENTRY_KINDS)

    lines = [f"Точек входа: {entries}", ""]
    for kind, count in by_kind.items():
        mark = " " if kind in ENTRY_KINDS else "·"
        lines.append(f"  {mark} {kind:<14} {count:>5}")

    for kind in by_kind:
        lines += ["", f"{kind}:"]
        for anchor in (a for a in anchors if a.kind == kind):
            team = f"  [{anchor.team}]" if anchor.team else ""
            title = f"  — {anchor.title}" if anchor.title else ""
            lines.append(f"  {anchor.display}{team}{title}")
            for target in anchor.targets:
                where = target.doc_path or "не найден среди узлов документации"
                note = " (через реализацию интерфейса)" if target.via == "implementation" else ""
                lines.append(f"      {target.field}: {target.fqn}{note}")
                lines.append(f"          → {where}")
            if anchor.children:
                kinds = dict(sorted(Counter(c.kind for c in anchor.children).items()))
                inner = ", ".join(f"{kind}: {count}" for kind, count in kinds.items())
                lines.append(f"      вложено — {inner}")

    unresolved = sum(
        1 for anchor in anchors for target in anchor.targets if target.via == "unresolved"
    )
    if unresolved:
        lines += [
            "",
            f"Ссылок на типы, не найденные среди узлов документации: {unresolved}.",
            "  Это не обязательно мёртвая запись реестра: узлами становятся только",
            "  enrolled и классифицированные типы. Отличить «типа нет» от «тип",
            "  не документируется» можно лишь по индексу символов, которого",
            "  в манифесте нет.",
        ]

    if errors:
        lines += ["", "Замечания при чтении реестров:"] + [f"  {error}" for error in errors]

    return "\n".join(lines)


def format_explain(anchor: ResolvedAnchor, team: str | None = None, shared: bool = False) -> str:
    """Подробности по одному якорю: откуда прочитан и что его реализует.

    `shared` означает, что на якоре несколько записей. Тогда печатается ещё
    и то, каким селектором сузить его до **этой** записи: писать `only` наугад
    по перечню полей — лишний шаг, на котором ошибаются, а промах селектора
    выглядит как оборванная связность.
    """
    lines = [
        f"{anchor.kind}  {anchor.display}",
        f"  реестр:  {anchor.registry}",
        f"  файл:    {anchor.source_path or '—'}",
    ]
    if anchor.title:
        lines.append(f"  заголовок: {anchor.title}")
    if anchor.team:
        lines.append(f"  команда: {anchor.team}")
    if team:
        lines.append(f"  команда по ownership.yaml: {team}")

    for name, value in sorted(anchor.fields.items()):
        lines.append(f"  {name}: {value}")

    if shared:
        lines.append("  сузить до этой записи:")
        if assembly := anchor.fields.get("assembly"):
            lines.append(f"    only: {{assembly: {assembly}}}")
        if team:
            lines.append(f"    only: {{team: {team}}}")
        if not anchor.fields.get("assembly") and not team:
            lines.append("    нечем: у записи нет ни `assembly`, ни команды по ownership.yaml")

    lines.append("  реализация:")
    if not anchor.targets:
        lines.append("    ссылок на типы в записи нет")
    for target in anchor.targets:
        via = {
            "direct": "напрямую",
            "implementation": "через реализацию интерфейса",
            "unresolved": "не найден среди узлов документации",
        }[target.via]
        lines.append(f"    {target.field}: {target.fqn}  ({via})")
        if target.node_id:
            lines.append(f"      узел:     {target.node_id}")
            lines.append(f"      документ: {target.doc_path}")
            lines.append(f"      модуль:   {target.module}")

    if anchor.children:
        lines.append("  вложенные записи:")
        for child in anchor.children:
            where = ", ".join(
                target.doc_path or f"{target.fqn} — вне дерева" for target in child.targets
            )
            lines.append(f"    {child.kind}  {child.ref}  → {where or '—'}")

    return "\n".join(lines)
