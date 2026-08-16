"""Кто живёт внутри документа страницы, а кто остаётся отдельным документом.

Правило одно: **узел поглощается страницей, только если достижим единственной
страницей**. Сервис, до которого не дотягивается никто, кроме
`/forecast/{}/structuring/create`, — раздел её документа. `ItemsService`,
до которого дотягиваются восемь страниц, — самостоятельный документ, на который
страницы ссылаются.

Порога здесь нет и быть не может: «общий» — это «появился второй потребитель»,
а не «потребителей больше N». Замер на боевом модуле объясняет почему: 43
эндпоинта из 61 достижимы ровно с четырёх страниц, и попытка приписать сервис
«главной» странице продублировала бы `ItemsService` восемь раз — восемь копий
разошлись бы на первой же правке.

Достижимость считается по **графу вызовов** (`uses`), а не по внедрению:
внедрённый и ни разу не позванный сервис к содержанию документа страницы
отношения не имеет.
"""

from docpipe.hashing import stable_hash
from docpipe.model import DocNode

PAGE_KIND = "page"


def _by_fqn(nodes: list[DocNode]) -> dict[str, DocNode]:
    found: dict[str, DocNode] = {}
    for node in sorted(nodes, key=lambda item: item.id):
        if node.symbol is not None:
            found.setdefault(node.symbol.fqn, node)
    return found


def reachable_from(node: DocNode, by_fqn: dict[str, DocNode]) -> set[str]:
    """Транзитивное замыкание по рёбрам вызовов. Циклы не вешают обход.

    Глубина здесь не ограничена намеренно, в отличие от отчёта: утилита,
    которую зовёт сервис, который зовёт страница, принадлежит этой странице
    так же, как и сам сервис. Ограничение глубины сделало бы принадлежность
    зависящей от длины цепочки, а не от того, кто ею пользуется.
    """
    seen: set[str] = set()
    frontier = [usage.target for usage in node.uses]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        reached = by_fqn.get(current)
        if reached is not None:
            frontier.extend(usage.target for usage in reached.uses)
    return seen


def absorb(nodes: list[DocNode]) -> list[DocNode]:
    """Проставить `absorbed_by` тем узлам, до кого дотягивается одна страница.

    Страница страницей не поглощается никогда: у неё свой документ и свой
    маршрут, и вложить экран в экран значило бы потерять его якорь.
    """
    by_fqn = _by_fqn(nodes)

    owners: dict[str, list[str]] = {}
    for page in sorted(
        (node for node in nodes if node.kind == PAGE_KIND), key=lambda item: item.id
    ):
        for fqn in sorted(reachable_from(page, by_fqn)):
            owners.setdefault(fqn, []).append(page.id)

    absorbed: dict[str, str] = {}
    for fqn, pages in owners.items():
        reached = by_fqn.get(fqn)
        if reached is None or reached.kind == PAGE_KIND or len(set(pages)) != 1:
            continue
        absorbed[reached.id] = pages[0]

    inside: dict[str, list[DocNode]] = {}
    for node in nodes:
        if node.id in absorbed:
            inside.setdefault(absorbed[node.id], []).append(node)

    return [
        node.model_copy(update={"absorbed_by": absorbed[node.id]})
        if node.id in absorbed
        else _with_aggregate_hashes(node, inside.get(node.id, []))
        for node in nodes
    ]


def _with_aggregate_hashes(page: DocNode, inside: list[DocNode]) -> DocNode:
    """Дописать в хэши страницы хэши поглощённых узлов.

    Без этого правка сервиса, живущего внутри страницы, не помечает её документ
    устаревшим, и раздел «Логика» остаётся ложью — при том, что своего документа
    у сервиса больше нет и заметить это негде.

    Два хэша меняются от разного, и это различие сохраняется:

    - `impl_hash` — от **тела** поглощённых: переписали метод сервиса, документ
      страницы устарел;
    - `signature_hash` — от **состава**: сервис ушёл из страницы или пришёл в неё,
      и документ устарел, даже если ни строки в коде не изменилось.
    """
    if not inside:
        return page

    members = sorted(node.id for node in inside)
    return page.model_copy(
        update={
            "impl_hash": stable_hash([page.impl_hash, *sorted(node.impl_hash for node in inside)]),
            "signature_hash": stable_hash([page.signature_hash, *members]),
        }
    )
