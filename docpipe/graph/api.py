"""Библиотека запросов: ограниченный набор форм вопроса (G11).

Универсальный обход графа наружу не выпускается — им немедленно построят
лавину. Наружу выходят семь форм, каждая названа вопросом пользователя,
а не структурой данных под ней; форма, для которой нельзя назвать вопрос,
в набор не входит.

| Форма      | Вопрос                                             |
|------------|----------------------------------------------------|
| `resolve`  | что это такое, если я знаю приблизительное имя     |
| `overview` | что это за репозиторий и что читать первым         |
| `card`     | что это за узел                                    |
| `why`      | почему этот код такой                              |
| `reaches`  | что достигает эта точка входа                      |
| `affects`  | какие точки входа затронет изменение               |
| `path`     | как связаны эти две сущности                       |

**Каждый ответ несёт признак неполноты**, относящийся к этому ответу,
а не только общую сводку: без него нельзя понять, чему верить.

**Обход графа делает только `path`.** Остальное — чтение предвычисленного.
Если запрос требует обхода, нарушена архитектура, а не «медленно работает».

`overview` и `why` считаются **без графа** — из разведки и git. Они доступны
на репозитории, где индекс ещё не собран, и являются первым, что инструмент
вообще может сказать о незнакомом коде.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Final

from docpipe.graph.model import GraphIndex, GraphMeta, GraphNode
from docpipe.graph.reach import DEFAULT_FANOUT_THRESHOLD, Reachability
from docpipe.graph.reach import path as walk
from docpipe.graph.search import resolve as resolve_names

# Ответ ограничен по размеру, и усечение **восстановимо**: маркер несёт
# команду, которой усечённое разворачивается. «Показано 20 из 300» без
# способа увидеть остальные заставляет звать инструмент заново с другими
# параметрами — то есть угадывать.
PAGE: Final[int] = 20


def _truncate(values: list[Any], limit: int, command: str) -> dict[str, Any]:
    shown = values[:limit]
    answer: dict[str, Any] = {"items": shown, "total": len(values)}
    if len(values) > limit:
        answer["truncated"] = {
            "shown": len(shown),
            "of": len(values),
            "how_to_see_the_rest": command,
        }
    return answer


def _incompleteness(meta: GraphMeta, categories: list[str]) -> dict[str, Any]:
    """Признак неполноты, относящийся к этому ответу.

    Берутся именно те категории, которые влияют на этот вид ответа, а не вся
    сводка: общая сводка на вопрос «чему здесь нельзя верить» не отвечает.
    """
    relevant = {
        name: number
        for name, number in meta.report.items()
        if any(marker in name for marker in categories)
    }
    return {
        "categories": dict(sorted(relevant.items())),
        "note": (
            "числа — это то, что не разрешилось на сборке индекса; "
            "полный список — `docpipe graph health`"
        ),
    }


def resolve(path: Path, index: GraphIndex, query: str, limit: int = PAGE) -> dict[str, Any]:
    """Что это такое, если известно только приблизительное имя."""
    nodes = {node.key: node for node in index.nodes}
    matches, inexact = resolve_names(path, query, nodes, limit)
    return {
        "query": query,
        "exact": not inexact,
        "candidates": [
            {
                "node": match.node,
                "kind": match.kind,
                "name": match.name,
                "module": match.module,
                "matched_field": match.field,
                "matched_how": match.how,
                "fragment": match.fragment,
                "score": match.score,
            }
            for match in matches
        ],
        "note": (
            "если точного совпадения нет, список — ближайшее по тому, чем "
            "совпало; переформулируйте, зная это"
        ),
    }


def card(
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
    key: str,
    threshold: int = DEFAULT_FANOUT_THRESHOLD,
) -> dict[str, Any]:
    """Что это за узел: вид, модуль, документ, кто его достигает."""
    node = next((item for item in index.nodes if item.key == key), None)
    if node is None:
        return {
            "found": False,
            "asked": key,
            "note": "узел не найден; попробуйте `resolve` — он ищет по приблизительному имени",
        }

    incoming = [edge for edge in index.edges if edge.target == key]
    outgoing = [edge for edge in index.edges if edge.source == key]
    fanout = reachability.fanout(key)
    return {
        "found": True,
        "node": node.key,
        "kind": node.kind,
        "name": node.name,
        "module": node.attributes.get("module", ""),
        "file": node.file,
        "document": node.attributes.get("doc_node", ""),
        "attributes": dict(sorted(node.attributes.items())),
        "fanout": fanout,
        "shared_component": fanout > threshold,
        "edges_in": _truncate(
            [{"kind": edge.kind, "from": edge.source, "via": edge.via} for edge in incoming],
            PAGE,
            f"docpipe graph affects {key}",
        ),
        "edges_out": _truncate(
            [{"kind": edge.kind, "to": edge.target, "via": edge.via} for edge in outgoing],
            PAGE,
            f"docpipe graph reaches {key}",
        ),
        "incomplete": _incompleteness(meta, ["сопоставлено", "перегрузки", "фронта"]),
    }


def reaches(
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
    root: str,
    limit: int = PAGE,
) -> dict[str, Any]:
    """Что достигает эта точка входа: код, таблицы, швы."""
    nodes = {node.key: node for node in index.nodes}
    if root not in nodes:
        return {"found": False, "asked": root, "note": "корень не найден; попробуйте `resolve`"}

    # Сам корень из ответа исключается: «точка входа достигает саму себя» —
    # верно и бесполезно, а в списке из двадцати строк занимает место.
    reached = [nodes[key] for key in reachability.reached_by(root) if key in nodes and key != root]
    grouped: dict[str, list[dict[str, str]]] = {}
    for node in reached:
        grouped.setdefault(node.kind, []).append({"node": node.key, "name": node.name})
    return {
        "found": True,
        "root": root,
        "name": nodes[root].name,
        "reached": {
            kind: _truncate(sorted(items, key=lambda item: item["name"]), limit, f"{kind}: см. CLI")
            for kind, items in sorted(grouped.items())
        },
        "incomplete": _incompleteness(
            meta, ["корней", "довершено", "разошлось", "данным", "соглашению"]
        ),
    }


def affects(
    index: GraphIndex,
    meta: GraphMeta,
    reachability: Reachability,
    keys: list[str],
    threshold: int = DEFAULT_FANOUT_THRESHOLD,
) -> dict[str, Any]:
    """Какие точки входа затронет изменение этих узлов или файлов.

    Принимает и ключи узлов, и пути файлов: вывод `git diff --name-only` —
    основной сценарий в PR, и требовать от вызывающего перевода путей
    в ключи значит требовать знания, которого у него нет.
    """
    nodes = {node.key: node for node in index.nodes}
    by_file: dict[str, list[GraphNode]] = {}
    for node in index.nodes:
        if node.file:
            by_file.setdefault(node.file, []).append(node)

    selected: list[GraphNode] = []
    unknown: list[str] = []
    for key in keys:
        if key in nodes:
            selected.append(nodes[key])
        elif key in by_file:
            selected.extend(by_file[key])
        else:
            unknown.append(key)

    if not selected:
        return {
            "found": False,
            "asked": keys,
            "unknown": unknown,
            "note": (
                "ни один из входов не найден ни как узел, ни как файл индекса; "
                "изменение могло затронуть код, которого нет в графе"
            ),
        }

    roots: set[str] = set()
    shared: list[dict[str, Any]] = []
    for node in selected:
        fanout = reachability.fanout(node.key)
        if fanout > threshold:
            # Узел, достижимый из сотен точек входа, — это не «затронуто
            # сотни процессов», а общий компонент: анализ не сужает.
            shared.append({"node": node.key, "fanout": fanout})
            continue
        roots.update(reachability.roots_of(node.key))

    return {
        "found": True,
        "asked": keys,
        "unknown": unknown,
        "nodes_considered": len(selected),
        "shared_components": shared,
        "entry_points": _truncate(
            sorted(
                (
                    {
                        "node": key,
                        "name": nodes[key].name,
                        "kind": nodes[key].attributes.get("entry_kind", ""),
                    }
                    for key in roots
                    if key in nodes
                ),
                key=lambda item: str(item["name"]),
            ),
            PAGE,
            "docpipe graph affects <узел>",
        ),
        "note": (
            "для общих компонентов список точек входа не строится намеренно: "
            "инструмент, который всегда что-то отвечает, теряет доверие целиком"
        ),
        "incomplete": _incompleteness(meta, ["довершено", "разошлось", "корней", "фронта"]),
    }


def path(index: GraphIndex, source: str, target: str, depth: int = 12) -> dict[str, Any]:
    """Как связаны две сущности. Единственная форма, которая обходит граф."""
    nodes = {node.key: node for node in index.nodes}
    missing = [key for key in (source, target) if key not in nodes]
    if missing:
        # Какая сторона не разрешилась — обязательная часть ответа:
        # «связь не найдена» в этом случае запрещено.
        return {
            "found": False,
            "unresolved_side": missing,
            "note": "не разрешена сторона запроса, а не отсутствует связь",
        }

    steps = walk(index, source, target, depth)
    if not steps:
        return {
            "found": False,
            "depth": depth,
            "note": (
                "пути не нашлось при этой глубине; это может значить и «связи нет», "
                "и «цепочка длиннее предела»"
            ),
        }
    return {
        "found": True,
        "steps": [
            {"kind": step.kind, "from": step.source, "to": step.target, "via": step.via}
            for step in steps
        ],
        "length": len(steps),
    }


def overview(root: Path, recon: Path | None = None, script: Path | None = None) -> dict[str, Any]:
    """Что это за репозиторий: чем собран, что читать первым, где центр.

    Считается **без графа** — из разведки. Доступна на репозитории, где индекс
    ещё не собран, и является первым, что инструмент может сказать о коде.
    """
    if recon is not None and recon.is_file():
        return {"source": str(recon), **_recon_summary(json.loads(recon.read_text("utf-8")))}

    candidate = script or Path("tools/recon.py")
    if not candidate.is_file():
        return {
            "available": False,
            "note": (
                "разведка не запускалась и скрипт не найден. Запустите "
                "`python3 tools/recon.py --root <репозиторий> --json recon.json` "
                "и передайте файл"
            ),
        }
    proc = subprocess.run(
        [
            "python3",
            str(candidate),
            "--root",
            str(root),
            "--json",
            "/dev/stdout",
            "--text",
            "/dev/null",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        return {"available": False, "note": f"разведка завершилась с ошибкой: {proc.stderr[:200]}"}
    return {"source": "разведка на лету", **_recon_summary(json.loads(proc.stdout))}


def _recon_summary(report: dict[str, Any]) -> dict[str, Any]:
    blocks = {block["id"]: block["data"] for block in report.get("blocks", [])}
    composition = blocks.get("composition", {})
    archaeology = blocks.get("archaeology", {})
    structure = blocks.get("structure", {})
    return {
        "available": True,
        "repo": report.get("repo", ""),
        "stacks": composition.get("stacks", []),
        "languages": composition.get("languages", [])[:5],
        "build_files": [row["pattern"] for row in composition.get("build_files", [])][:10],
        "read_first": [row["path"] for row in archaeology.get("hotspots", [])][:10],
        "center": [row["path"] for row in structure.get("center", [])][:10],
        "registries": blocks.get("registries", {}).get("found", 0),
        "seams": blocks.get("seams", {}).get("found", 0),
        "note": (
            "это разведка, а не граф: числа отвечают на «что здесь есть», а не «что с чем связано»"
        ),
    }


def why(root: Path, file: str, limit: int = 10) -> dict[str, Any]:
    """Почему этот код такой: что с ним делали и кто.

    Считается **без графа** — из git. Ответ не претендует на объяснение
    замысла: он показывает, что с файлом происходило, а замысел ищут
    в сообщениях коммитов и в обсуждениях.
    """
    proc = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "-C",
            str(root),
            "log",
            f"-{limit}",
            "--format=%h%x02%an%x02%cI%x02%s",
            "--",
            file,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return {"available": False, "note": "git недоступен или файл вне репозитория"}
    commits = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x02")
        if len(parts) == 4:
            commits.append(
                {"commit": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
            )
    if not commits:
        return {
            "available": True,
            "file": file,
            "commits": [],
            "note": "история пуста: файл не в git, только что добавлен или переименован",
        }
    authors = sorted({commit["author"] for commit in commits})
    fixes = [commit for commit in commits if "fix" in commit["subject"].lower()]
    return {
        "available": True,
        "file": file,
        "commits": commits,
        "authors": authors,
        "fix_commits": len(fixes),
        "note": "история из git, без графа: она доступна и на репозитории без индекса",
    }
