"""Предвычисленная достижимость и веерность (G07).

Одно вычисление, три ответа: что достигает точка входа, какие точки входа
достигают узел, насколько узел общий.

**Почему не обход от каждого корня.** Корней на целевом репозитории тысячи
(эндпоинты всех модулей, страницы фронтов, workflow, джобы, ресиверы,
грид-сервисы), рёбер — сотни тысяч. Тысяча независимых обходов по такому
графу на чистом Python не влезает ни в какой бюджет: наивная реализация
пройдёт приёмку на фикстуре и умрёт на целевом репозитории.

Вместо этого — **конденсация SCC**. Циклы в графе вызовов есть всегда
(рекурсия, взаимные вызовы), конденсация даёт DAG, и маска «какие корни меня
достигают» собирается одним проходом в топологическом порядке: ИЛИ масок
предшественников плюс собственные корни узла.

**Что считается ребром достижимости.** Не всё: `binds` — это «кто мог бы
позвать», а не «зовёт», и включать его значило бы объявить достижимым весь
код, куда ведёт хоть одна регистрация. Наследование тоже не путь исполнения.
Остаются вызовы, диспетчеризация, швы и обращения к данным.

**Член достигнут — достигнут и его тип.** Обращения к данным объявлены
на типе (поле репозитория объявлено там), поэтому в графе достижимости
есть ребро «член → его тип». Это огрубление в сторону «достижимо больше»,
и оно названо: сервис, чей единственный метод позвали, считается трогающим
все свои таблицы.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.graph.model import GraphEdge, GraphIndex, GraphNode

# Виды рёбер, по которым идёт исполнение. Перечень положительный: новый вид
# ребра обязан попасть сюда осознанно, а не начать молча расширять ответы.
TRAVERSED: Final[frozenset[str]] = frozenset(
    # `reads` и `writes` — тоже путь: «процесс трогает таблицу» это ровно
    # тот ответ, ради которого достижимость и считается. Их отсутствие здесь
    # означало бы, что процедура достигнута, а её таблицы — нет.
    {"calls", "dispatches", "crosses", "touches", "reads", "writes"}
)

# Узел, достижимый более чем от стольких корней, считается общим компонентом.
# Значение по умолчанию — отправная точка, а не истина: план требует обосновать
# его измерением на целевом репозитории, и до этого измерения оно временное.
DEFAULT_FANOUT_THRESHOLD: Final[int] = 50


@dataclass(frozen=True)
class Reachability:
    """Результат предвычисления.

    `masks` — по маске на узел: биты назначены корням в порядке `roots`.
    Хранение битовыми масками, а не списками, не оптимизация: при тысячах
    корней списки достижимых узлов не помещаются ни в память, ни в бюджет
    диска.
    """

    roots: tuple[str, ...] = ()
    masks: dict[str, int] = field(default_factory=dict)
    # Число узлов в компоненте сильной связности узла: 1 у обычного,
    # больше — у участника цикла. Нужно отчёту: цикл в графе вызовов —
    # это не дефект, но знать о нём полезно.
    component_size: dict[str, int] = field(default_factory=dict)

    def fanout(self, key: str) -> int:
        return int(self.masks.get(key, 0).bit_count())

    def roots_of(self, key: str) -> list[str]:
        mask = self.masks.get(key, 0)
        return [root for index, root in enumerate(self.roots) if mask >> index & 1]

    def reached_by(self, root: str) -> list[str]:
        """Что достигает корень. Инверсия того же массива.

        Стоит проход по маскам всех узлов: отдельного массива «корень →
        достижимые» нет намеренно, он удваивает и без того самую крупную
        структуру индекса.
        """
        if root not in self.roots:
            return []
        bit = 1 << self.roots.index(root)
        return sorted(key for key, mask in self.masks.items() if mask & bit)


def _members_of(index: GraphIndex) -> dict[str, list[str]]:
    """Тип → его члены. Нужно ровно для точек входа, см. `_adjacency`."""
    owned: dict[str, list[str]] = {}
    keys = {node.key for node in index.nodes}
    for node in index.nodes:
        if node.kind != "member" or not node.owner:
            continue
        owner = f"{node.file}#{node.owner}" if node.file else node.owner
        if owner in keys:
            owned.setdefault(owner, []).append(node.key)
    return owned


def _adjacency(index: GraphIndex) -> dict[str, list[str]]:
    """Рёбра исполнения, связь члена с его типом и раскрытие типа-корня."""
    keys = {node.key for node in index.nodes}
    successors: dict[str, list[str]] = {}
    for edge in index.edges:
        if edge.kind not in TRAVERSED:
            continue
        if edge.source in keys and edge.target in keys:
            successors.setdefault(edge.source, []).append(edge.target)

    owned = _members_of(index)
    for node in index.nodes:
        if node.kind != "member" or not node.owner:
            continue
        owner = f"{node.file}#{node.owner}" if node.file else node.owner
        if owner in keys:
            successors.setdefault(node.key, []).append(owner)

    # Точка входа, объявленная классом, запускает его члены. Раскрытие
    # «тип → его члены» делается ТОЛЬКО для цели диспетчеризации: сделать
    # его общим правилом значило бы, что любой достигнутый тип тянет за собой
    # все свои методы, и достижимость перестала бы что-либо сужать. Реестр
    # же говорит именно «этот класс и есть джоб», и без раскрытия такой
    # корень не достигает ничего.
    by_key = {node.key: node for node in index.nodes}
    for edge in index.edges:
        if edge.kind != "dispatches":
            continue
        target = by_key.get(edge.target)
        source = by_key.get(edge.source)
        if target is None or source is None or source.kind != "entry_point":
            continue
        if target.kind == "type":
            successors.setdefault(target.key, []).extend(owned.get(target.key, ()))

    return {key: sorted(set(values)) for key, values in successors.items()}


def _components(
    nodes: list[str], successors: dict[str, list[str]]
) -> tuple[dict[str, int], list[list[str]]]:
    """Компоненты сильной связности, итеративный Тарьян.

    Итеративный, а не рекурсивный: глубина цепочки вызовов на реальном
    репозитории спокойно превышает предел рекурсии Python, и падение
    случилось бы ровно на большом репозитории, где проверять дороже всего.
    """
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for start in nodes:
        if start in index_of:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, child = work[-1]
            if child == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = successors.get(node, ())
            if child < len(children):
                work[-1] = (node, child + 1)
                target = children[child]
                if target not in index_of:
                    work.append((target, 0))
                elif target in on_stack:
                    low[node] = min(low[node], index_of[target])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                result.append(sorted(component))

    membership = {node: number for number, component in enumerate(result) for node in component}
    return membership, result


def compute(index: GraphIndex, roots: tuple[str, ...] | None = None) -> Reachability:
    """Посчитать достижимость от всех корней одним проходом."""
    nodes = sorted(node.key for node in index.nodes)
    root_keys = roots
    if root_keys is None:
        root_keys = tuple(sorted(node.key for node in index.nodes if node.kind == "entry_point"))
    bit_of = {root: index_ for index_, root in enumerate(root_keys)}

    successors = _adjacency(index)
    membership, components = _components(nodes, successors)

    # Рёбра конденсации и её топологический порядок. Считаем по числу
    # входящих: сортировка Кана не требует рекурсии и даёт устойчивый
    # порядок при явной сортировке очереди.
    condensed: dict[int, set[int]] = {number: set() for number in range(len(components))}
    incoming: dict[int, int] = {number: 0 for number in range(len(components))}
    for source, targets in successors.items():
        from_component = membership[source]
        for target in targets:
            to_component = membership.get(target)
            if to_component is None or to_component == from_component:
                continue
            if to_component not in condensed[from_component]:
                condensed[from_component].add(to_component)
                incoming[to_component] += 1

    order: list[int] = []
    queue: list[int] = sorted(number for number, count in incoming.items() if count == 0)
    while queue:
        current = queue.pop(0)
        order.append(current)
        for component_number in sorted(condensed[current]):
            incoming[component_number] -= 1
            if incoming[component_number] == 0:
                queue.append(component_number)
                queue.sort()

    masks: dict[int, int] = {number: 0 for number in range(len(components))}
    for number, component in enumerate(components):
        for key in component:
            if key in bit_of:
                masks[number] |= 1 << bit_of[key]

    for number in order:
        current_mask = masks[number]
        if not current_mask:
            continue
        for component_number in condensed[number]:
            masks[component_number] |= current_mask

    node_masks = {key: masks[membership[key]] for key in nodes}
    sizes = {key: len(components[membership[key]]) for key in nodes}
    return Reachability(roots=tuple(root_keys), masks=node_masks, component_size=sizes)


def path(index: GraphIndex, source: str, target: str, depth: int = 12) -> list[GraphEdge]:
    """Восстановить путь между двумя узлами. Обход — только здесь.

    Глубина ограничена, и обрыв виден числом: приём перенесён с фронта,
    где он уже показал, что бесконечная цепочка выглядит как найденная связь.
    """
    # Имена переменных здесь не случайны: `target` — это параметр функции,
    # и переиспользовать его под ключ смежности нельзя. Такая тень уже
    # однажды дала путь, обрывающийся на предпоследнем узле, — обход искал
    # не тот конец и находил «первый попавшийся».
    successors: dict[str, list[GraphEdge]] = {}
    known = {(edge.source, edge.target): edge for edge in index.edges}
    for origin, neighbours in _adjacency(index).items():
        for neighbour in neighbours:
            successors.setdefault(origin, []).append(
                known.get((origin, neighbour))
                or GraphEdge(
                    # `owns` — не вызов, и называть его вызовом нельзя: это
                    # связь «тип содержит член», по которой достижимость идёт,
                    # а исполнение — нет. В индексе такого ребра не лежит,
                    # оно существует только в ответе о пути.
                    kind="owns",
                    source=origin,
                    target=neighbour,
                    via="владение",
                    confidence=1.0,
                )
            )

    queue: list[tuple[str, list[GraphEdge]]] = [(source, [])]
    seen = {source}
    while queue:
        current, trail = queue.pop(0)
        if len(trail) >= depth:
            continue
        for edge in sorted(successors.get(current, ()), key=lambda item: item.target):
            if edge.target == target:
                return [*trail, edge]
            if edge.target in seen:
                continue
            seen.add(edge.target)
            queue.append((edge.target, [*trail, edge]))
    return []


def shared_components(
    reachability: Reachability, nodes: tuple[GraphNode, ...], threshold: int
) -> list[tuple[str, int]]:
    """Узлы выше порога веерности: анализ по ним не сужает.

    Ответ на запрос влияния для такого узла — не список процессов, а признание,
    что анализ не сужает, плюс само число. Инструмент, который всегда что-то
    отвечает, теряет доверие целиком.
    """
    found = [
        (node.key, reachability.fanout(node.key))
        for node in nodes
        if reachability.fanout(node.key) > threshold
    ]
    return sorted(found, key=lambda item: (-item[1], item[0]))
