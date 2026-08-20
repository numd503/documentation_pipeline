"""Таблица точек входа — предъявляемый артефакт Вехи 2 (G07 п. 9).

Для каждой точки входа: какой код достигается, какие таблицы трогаются,
где обрывается анализ. Одна команда, а не сумма трёх, которые надо
догадаться позвать.

Формат — детерминированный markdown: ни времени, ни хоста, ни абсолютных
путей. Отчёт кладут в ревью и сравнивают между прогонами, а отчёт, который
меняется сам по себе, сравнивать нельзя.
"""

from typing import Final

from docpipe.graph.model import GraphIndex, GraphMeta, GraphNode
from docpipe.graph.reach import Reachability, shared_components

EXAMPLES: Final[int] = 5


def _table(rows: list[list[str]], headers: list[str]) -> list[str]:
    if not rows:
        return ["_нет записей_", ""]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    lines.append("")
    return lines


def render(
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
    threshold: int,
) -> str:
    """Собрать отчёт. Чистая функция от индекса: одинаковый вход — одинаковый текст."""
    nodes = {node.key: node for node in index.nodes}
    linked = {edge.source for edge in index.edges if edge.kind == "dispatches"}
    roots = sorted(
        (node for node in index.nodes if node.kind == "entry_point"), key=lambda item: item.key
    )

    lines: list[str] = [
        "# Точки входа: что достигает каждая",
        "",
        f"Репозиторий: `{meta.repo}`. Поколение индекса: `{meta.generation}`.",
        f"Узлов: {meta.counts.get('nodes', 0)}, рёбер: {meta.counts.get('edges', 0)}, "
        f"корней: {len(roots)}.",
        "",
        "Времени в отчёте нет намеренно: его кладут в ревью и сравнивают между",
        "прогонами, а отчёт, который меняется сам по себе, сравнивать нельзя.",
        "",
        "## Таблица точек входа",
        "",
    ]

    rows: list[list[str]] = []
    for root in roots:
        reached = reachability.reached_by(root.key)
        members = sum(1 for key in reached if nodes.get(key) and nodes[key].kind == "member")
        types = sum(1 for key in reached if nodes.get(key) and nodes[key].kind == "type")
        tables = sorted(
            nodes[key].name for key in reached if nodes.get(key) and nodes[key].kind == "data"
        )
        note = "" if root.key in linked else "узел кода не найден"
        if root.key in linked and not members and not types:
            note = "код найден, вызовов из него нет"
        rows.append(
            [
                root.name,
                root.attributes.get("entry_kind", "—"),
                "реестр" if root.source == "registry" else "код",
                str(members),
                str(types),
                ", ".join(tables[:EXAMPLES]) + ("…" if len(tables) > EXAMPLES else "") or "—",
                note or "—",
            ]
        )
    lines.extend(
        _table(
            rows,
            ["Точка входа", "Вид", "Источник", "Членов", "Типов", "Таблицы", "Где обрывается"],
        )
    )

    unlinked = [root for root in roots if root.key not in linked]
    lines.extend(
        [
            "## Корни без узла кода",
            "",
            "Состояние работы, а не дефект: реестр объявляет точку входа, а класс "
            "под неё может быть в другом репозитории, переименован или ещё не написан.",
            "",
        ]
    )
    lines.extend(
        _table(
            [
                [root.name, root.attributes.get("entry_kind", "—"), root.file or "—"]
                for root in unlinked
            ],
            ["Точка входа", "Вид", "Источник"],
        )
    )

    shared = shared_components(reachability, index.nodes, threshold)
    lines.extend(
        [
            "## Общие компоненты",
            "",
            f"Узлы, достижимые более чем от {threshold} корней. Ответ на вопрос "
            "о влиянии для них — не список процессов, а признание, что анализ "
            "не сужает, плюс само число.",
            "",
        ]
    )
    lines.extend(
        _table(
            [[nodes[key].name or key, str(count)] for key, count in shared[:20]],
            ["Узел", "Достижим от корней"],
        )
    )

    lines.extend(["## Чему в этом отчёте нельзя верить", ""])
    if meta.report:
        lines.extend(
            _table(
                [[name, str(number)] for name, number in sorted(meta.report.items())],
                ["Категория", "Число"],
            )
        )
    else:
        lines.extend(["_категорий неполноты не записано_", ""])
    lines.extend(
        [
            "Достижимость огрубляет в сторону «достижимо больше»: обращения",
            "к данным объявлены на типе, поэтому член, который позвали, считается",
            "достигающим все таблицы своего типа.",
            "",
        ]
    )
    return "\n".join(lines)


def health(meta: GraphMeta) -> str:
    """Отчёт о неполноте (G08): категории с числами.

    Красным по умолчанию не бывает: линт, красный с первого дня, выключают
    на второй. Порог задаёт вызывающий.
    """
    if not meta.report:
        return (
            "Категорий неполноты не записано. Это не «всё разрешилось»: "
            "индекс мог быть собран без манифеста и без реестра, и тогда "
            "считать было нечего.\n"
        )
    width = max(len(name) for name in meta.report)
    lines = [f"Репозиторий: {meta.repo}, поколение: {meta.generation}", ""]
    lines.extend(f"  {name.ljust(width)}  {number}" for name, number in sorted(meta.report.items()))
    lines.append("")
    return "\n".join(lines)


def describe(node: GraphNode, reachability: Reachability, threshold: int) -> str:
    """Одна строка про узел: чем он является и насколько он общий."""
    fanout = reachability.fanout(node.key)
    shared = " — ОБЩИЙ КОМПОНЕНТ, анализ не сужает" if fanout > threshold else ""
    return f"{node.kind:<12} {node.name or node.key}  достижим от корней: {fanout}{shared}"
