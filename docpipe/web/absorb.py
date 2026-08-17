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
from docpipe.web.overrides import Feature

PAGE_KIND = "page"
FEATURE_KIND = "feature"


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


def absorb(nodes: list[DocNode], features: list[Feature] | None = None) -> list[DocNode]:
    """Проставить `absorbed_by`: сначала объявленные разделы, потом страницы.

    Порядок фиксирован, и он же порядок доверия. Раздел объявил человек, и его
    границей служит каталог; поглощение страницей выводится из графа. Там, где
    они спорят, побеждает объявленное: инструмент не переигрывает решение,
    которое человек записал явно.

    Страница ни разделом, ни страницей не поглощается: у неё свой документ
    и свой маршрут, и вложить экран в экран значило бы потерять его якорь.
    """
    by_fqn = _by_fqn(nodes)
    declared = _declared(nodes, features or [])

    owners: dict[str, list[str]] = {}
    for page in sorted(
        (node for node in nodes if node.kind == PAGE_KIND), key=lambda item: item.id
    ):
        for fqn in sorted(reachable_from(page, by_fqn)):
            owners.setdefault(fqn, []).append(page.id)

    absorbed: dict[str, str] = dict(declared)
    for fqn, pages in owners.items():
        reached = by_fqn.get(fqn)
        if reached is None or reached.kind == PAGE_KIND or reached.id in absorbed:
            continue
        if len(set(pages)) != 1:
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


def _declared(nodes: list[DocNode], features: list[Feature]) -> dict[str, str]:
    """Узлы, лежащие под каталогом объявленного раздела.

    Граница — каталог, а не достижимость: раздел `inner-debt` зовут и `loader`,
    и `structuring`, поэтому по графу он «общий» и не поглотился бы никогда.
    Человек знает, что это одна вещь, и говорит это каталогом.

    Сравнение по префиксу с завершающим слэшем: `…/leasing` иначе накрыл бы
    `…/leasing-report`, и чужие узлы уехали бы в чужой документ.
    """
    by_name = {feature.name: f"feature:{feature.name}" for feature in features}
    found: dict[str, str] = {}
    for node in sorted(nodes, key=lambda item: item.id):
        if node.kind in (PAGE_KIND, FEATURE_KIND) or node.symbol is None or not node.symbol.sources:
            continue
        path = node.symbol.sources[0].path
        for feature in features:
            if path.startswith(feature.prefix):
                found[node.id] = by_name[feature.name]
                break
    return found


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
