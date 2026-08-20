"""Объявленные швы: литерал, который знают обе стороны (G16 п. 4, Р2).

**Между языками вызовов нет.** Питоновский сервис не «зовёт» метод C# —
он посылает сообщение по литералу: маршрут, имя очереди, имя топика, путь
файла. Другой реализуемой архитектуры связи не существует, и попытка вывести
её из кода даёт либо ничего, либо выдумку.

Литерал приходит **из реестра** (R03/R04), а не из разбора: сторона, которая
его пишет, часто собирает строку из кусков, и статический поиск даёт ноль.
Реестр же — то, что человек прочитал и закоммитил (Р10).

Шов становится **узлом**, а не просто ребром. Причина та же, по которой
процедура остаётся звеном пути: ответ «страница дошла до эндпоинта» без
названного шва не даёт зацепиться — читателю нужен литерал, по которому
это произошло, и файл, где литерал объявлен.

Направление ребра: **сторона источника зовёт, сторона литерала отвечает.**
Литерал записан там, где им пользуются, чтобы дотянуться до другой стороны;
если бы он принадлежал отвечающей стороне, эта сторона уже была бы в графе
точкой входа из кода. Совпадение сторон (источник и есть отвечающий)
пропускается — иначе получилось бы ребро узла в самого себя через шов.
"""

from dataclasses import dataclass, field
from typing import Final

from docpipe.arch.model import ArchRegistry, SeamRecord
from docpipe.graph.entrypoints import entry_key
from docpipe.graph.model import GraphEdge, GraphNode
from docpipe.keys import normalize_identifier, normalize_route_reference

VIA_DECLARED: Final[str] = "seam:declared"

# Вид шва → вид точки входа, которой он отвечает. Таблица явная и закрытая:
# `queue` и `topic` — разные слова у разных платформ для одного механизма,
# и сводить их в графе нельзя, а вот искать точку входа обоих видов нужно.
# `file` и `other` точки входа не имеют — шов остаётся с одной стороной,
# и это законный, посчитанный исход, а не потеря.
ANSWERING_KINDS: Final[dict[str, tuple[str, ...]]] = {
    "http_route": ("http_endpoint",),
    "grid_service": ("grid_service",),
    "queue": ("queue", "kafka_topic", "event"),
    "topic": ("kafka_topic", "queue", "event"),
    "file": (),
    "other": (),
}


@dataclass(frozen=True)
class SeamReport:
    """Сколько швов объявлено и сколько из них соединили две стороны."""

    declared: int = 0
    both_sides: int = 0
    answering_only: int = 0
    calling_only: int = 0
    dangling: int = 0
    examples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_counts(self) -> dict[str, int]:
        return {
            "швов объявлено": self.declared,
            "швов соединили обе стороны": self.both_sides,
            "швов: есть отвечающая сторона, нет зовущей": self.answering_only,
            "швов: есть зовущая сторона, нет отвечающей": self.calling_only,
            "швов без обеих сторон": self.dangling,
        }


def seam_key(record: SeamRecord) -> str:
    """Ключ узла шва. Отдельное пространство от кода и от точек входа."""
    return f"seam:{record.seam_kind}:{record.normalized_key}"


def _answering(record: SeamRecord, nodes: tuple[GraphNode, ...]) -> list[str]:
    """Точки входа, которым этот литерал отвечает."""
    kinds = ANSWERING_KINDS.get(record.seam_kind, ())
    if not kinds:
        return []

    # Ключи собираются **тем же** `entry_key`, а не сравнением кусков строки:
    # он нормализует значение (в частности, опускает регистр), и сравнение
    # «в лоб» разошлось бы с ним молча — `GET api/…` против `get api/…`.
    literal = record.literal or record.key
    if record.seam_kind == "http_route":
        # У HTTP-точки входа ключ несёт глагол, у шва он отдельным полем.
        wanted = {
            entry_key("http_endpoint", normalize_route_reference(record.http_method, literal))
        }
    else:
        wanted = {entry_key(kind, literal) for kind in kinds}

    found: list[str] = []
    for node in nodes:
        if node.kind != "entry_point":
            continue
        if node.attributes.get("entry_kind") not in kinds:
            continue
        if node.key in wanted:
            found.append(node.key)
        elif not record.http_method and record.seam_kind == "http_route":
            # Пустой метод законен: сторона, которая глагола не знает, пишет
            # только маршрут, и тогда сходятся все глаголы этого маршрута.
            route = node.key.split(":", 2)[-1].split(" ", 1)[-1]
            if route == normalize_identifier(record.normalized_key):
                found.append(node.key)
    return sorted(found)


def _calling(record: SeamRecord, nodes: tuple[GraphNode, ...]) -> list[str]:
    """Узлы кода, объявившие литерал: файл источника, а при указанном символе — он.

    Гранулярность здесь — то, что записал человек. `source.record` со именем
    символа даёт член или тип; пустой `record` — файл целиком, и тогда
    зовущей стороной считается **тип**, а не все члены подряд: ребро от
    каждого члена файла было бы уверенным неверным ребром на ровном месте.
    """
    file = record.source.file
    symbol = normalize_identifier(record.source.record)
    in_file = [node for node in nodes if node.file == file and node.kind in ("type", "member")]
    if not in_file:
        return []

    if symbol:
        named = [
            node.key
            for node in in_file
            if normalize_identifier(node.name) == symbol
            or normalize_identifier(f"{node.owner}.{node.name}") == symbol
        ]
        if named:
            return sorted(named)

    return sorted(node.key for node in in_file if node.kind == "type")


def _implementing(
    answering: list[str], edges: tuple[GraphEdge, ...], by_key: dict[str, GraphNode]
) -> set[str]:
    """Узлы кода, которыми отвечающая сторона и является.

    Литерал бывает записан на отвечающей стороне: человек увидел маршрут
    в контроллере, а не в клиенте. Без этой проверки получилось бы ребро
    «контроллер зовёт собственный эндпоинт» — уверенное неверное ребро
    из ничего.
    """
    roots = set(answering)
    files: set[str] = set()
    for edge in edges:
        if edge.source in roots:
            target = by_key.get(edge.target)
            if target is not None and target.file:
                files.add(target.file)
    return {node.key for node in by_key.values() if node.file in files}


def declared(
    registry: ArchRegistry | None,
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...] = (),
) -> tuple[list[GraphNode], list[GraphEdge], SeamReport]:
    """Узлы и рёбра объявленных швов.

    `edges` нужны ради одной проверки: literal бывает записан на **отвечающей**
    стороне (человек увидел маршрут в контроллере, а не в клиенте). Тогда
    зовущая сторона совпадает с отвечающей, и ребро вело бы из контроллера
    в собственный эндпоинт — «класс зовёт сам себя через шов».
    """
    if registry is None:
        return [], [], SeamReport()

    records = [record for record in registry.records if isinstance(record, SeamRecord)]
    if not records:
        return [], [], SeamReport()

    by_key = {node.key: node for node in nodes}
    produced: list[GraphNode] = []
    made: list[GraphEdge] = []
    both = answering_only = calling_only = dangling = 0
    lonely: list[str] = []

    for record in sorted(records, key=lambda item: (item.seam_kind, item.normalized_key)):
        key = seam_key(record)
        answering = _answering(record, nodes)
        implementing = _implementing(answering, edges, by_key)
        calling = [
            node
            for node in _calling(record, nodes)
            if node not in answering and node not in implementing
        ]

        produced.append(
            GraphNode(
                key=key,
                kind="seam",
                name=record.name or record.literal or record.key,
                file=record.source.file,
                source="registry",
                attributes={
                    name: value
                    for name, value in {
                        "seam_kind": record.seam_kind,
                        "literal": record.literal or record.key,
                        "http_method": record.http_method,
                        "sides": ", ".join(record.sides),
                        "provenance": record.provenance,
                        "source_record": record.source.record,
                    }.items()
                    if value
                },
            )
        )

        for source in calling:
            made.append(
                GraphEdge(
                    kind="crosses", source=source, target=key, via=VIA_DECLARED, confidence=0.9
                )
            )
        for target in answering:
            made.append(
                GraphEdge(
                    kind="crosses", source=key, target=target, via=VIA_DECLARED, confidence=0.9
                )
            )

        if calling and answering:
            both += 1
        elif answering:
            answering_only += 1
            lonely.append(f"{key}: отвечающая сторона есть, зовущей нет ({record.source.file})")
        elif calling:
            calling_only += 1
            lonely.append(f"{key}: зовущая сторона есть, отвечающей нет")
        else:
            dangling += 1
            lonely.append(f"{key}: в графе нет ни одной стороны")

    report = SeamReport(
        declared=len(records),
        both_sides=both,
        answering_only=answering_only,
        calling_only=calling_only,
        dangling=dangling,
        examples={"швы с одной стороной": tuple(sorted(lonely)[:10])},
    )
    return produced, made, report
