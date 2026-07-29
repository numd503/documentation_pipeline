"""Инвентаризация точек входа: записи реестров, сопоставленные с манифестом.

Отвечает на вопрос «сколько в системе точек входа и что их реализует». Это
первый артефакт бизнес-слоя, полезный сам по себе: такой таблицы в АС CF,
по-видимому, не существует, а без неё объём работы по каталогу процессов —
догадка.

Пакет не импортирует `docpipe.dotnet`: резолв идёт по манифесту, который
языконезависим.
"""

from collections import Counter, defaultdict
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


def format_explain(anchor: ResolvedAnchor) -> str:
    """Подробности по одному якорю: откуда прочитан и что его реализует."""
    lines = [
        f"{anchor.kind}  {anchor.display}",
        f"  реестр:  {anchor.registry}",
        f"  файл:    {anchor.source_path or '—'}",
    ]
    if anchor.title:
        lines.append(f"  заголовок: {anchor.title}")
    if anchor.team:
        lines.append(f"  команда: {anchor.team}")

    for name, value in sorted(anchor.fields.items()):
        lines.append(f"  {name}: {value}")

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
