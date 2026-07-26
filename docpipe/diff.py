"""Сравнение двух манифестов — вход для шага 3.

Шагу 3 нужно знать не «что-то изменилось», а **что именно** и насколько глубоко:
переклассифицированный тип нужно переписать целиком, у переехавшего достаточно
перенести файл, а у изменившего сигнатуру — обновить разделы про API.

На узел приходится ровно одно изменение, самое существенное. Список всех
затронутых полей здесь не нужен: решение шага 3 определяется характером
изменения, а не их количеством.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from docpipe.model import DocNode, Manifest

ChangeKind = Literal["added", "removed", "reclassified", "moved", "signature_changed"]


class NodeChange(BaseModel):
    """Одно изменение узла между двумя манифестами."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    change: ChangeKind
    details: dict[str, str] = Field(default_factory=dict)


def _changed(old: DocNode, new: DocNode) -> NodeChange | None:
    """Самое существенное изменение узла. `None` — узел не менялся.

    Порядок проверок — от более общего к более частному. Смена вида влечёт
    и смену `doc_path` (вид входит в путь), поэтому `reclassified` идёт первым:
    сообщать про переезд там, где тип сменил природу, значит скрыть главное.
    """
    if old.kind != new.kind:
        return NodeChange(
            node_id=new.id, change="reclassified", details={"from": old.kind, "to": new.kind}
        )
    if old.doc_path != new.doc_path:
        return NodeChange(
            node_id=new.id, change="moved", details={"from": old.doc_path, "to": new.doc_path}
        )
    if old.signature_hash != new.signature_hash:
        return NodeChange(node_id=new.id, change="signature_changed")
    return None


def diff_manifests(old: Manifest, new: Manifest) -> list[NodeChange]:
    """Изменения между манифестами. Узлы без изменений в результат не входят."""
    before = {node.id: node for node in old.nodes}
    after = {node.id: node for node in new.nodes}

    changes = [
        NodeChange(node_id=node_id, change="added") for node_id in after.keys() - before.keys()
    ]
    changes += [
        NodeChange(node_id=node_id, change="removed") for node_id in before.keys() - after.keys()
    ]
    changes += [
        change
        for node_id in before.keys() & after.keys()
        if (change := _changed(before[node_id], after[node_id])) is not None
    ]

    changes.sort(key=lambda item: (item.node_id, item.change))
    return changes


def format_changes(changes: list[NodeChange]) -> str:
    """Человекочитаемый вывод. Пустой список — отдельная строка, а не пустота."""
    if not changes:
        return "Изменений нет."

    lines = []
    for change in changes:
        detail = ""
        if change.details:
            detail = f"  {change.details.get('from', '')} -> {change.details.get('to', '')}"
        lines.append(f"{change.change:18} {change.node_id}{detail}")

    counts = {
        kind: sum(1 for c in changes if c.change == kind) for kind in {c.change for c in changes}
    }
    summary = ", ".join(f"{kind}: {count}" for kind, count in sorted(counts.items()))
    lines.append(f"\nВсего изменений: {len(changes)} ({summary})")
    return "\n".join(lines)
