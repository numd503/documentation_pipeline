"""Кто владеет строкой: член, в теле которого записан факт.

Нужно трём задачам сразу — вызову (P01), обращению к зависимости (P02)
и диспатчу экшена (P03), — поэтому живёт отдельно, а не копией в каждой.

Разбора здесь нет вовсе: у `WebCall` есть файл и строка, у члена символа —
`line` и `end_line`, и этого достаточно. Отдельный проход по дереву ради
той же величины дал бы второй источник истины, который разойдётся с первым
на первой же форме объявления.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from docpipe.model import Symbol


@dataclass(frozen=True)
class MemberRanges:
    """Диапазоны членов по файлам.

    Ключ — файл объявления символа, то есть **первый** источник: шаблон `.html`
    дописывается в `sources` шагом `web` рядом с `.ts`, и его строки к членам
    отношения не имеют.
    """

    by_file: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)

    def of(self, file: str, line: int) -> str:
        """Имя члена, накрывающего строку. Пустая строка — вне членов.

        Побеждает **самый узкий** накрывающий диапазон: метод, содержащий
        локальную функцию с вызовом, накрывает её целиком, и перебор в порядке
        объявления вернул бы первый попавшийся. При равной ширине — меньшее
        имя: два члена с одинаковым диапазоном означают ошибку разбора,
        а не выбор, но результат обязан быть детерминированным.
        """
        found: tuple[int, str] | None = None
        for start, end, name in self.by_file.get(file, ()):
            if not start <= line <= end:
                continue
            width = end - start
            if found is None or (width, name) < found:
                found = (width, name)
        return found[1] if found is not None else ""


def member_ranges(symbols: Iterable[Symbol]) -> MemberRanges:
    """Собрать диапазоны членов всех символов.

    Сортировка не нужна для правильности — победитель выбирается сравнением, —
    но список складывается детерминированно, чтобы отладочный вывод совпадал
    между прогонами.
    """
    by_file: dict[str, list[tuple[int, int, str]]] = {}
    for symbol in sorted(symbols, key=lambda item: item.fqn):
        if not symbol.sources:
            continue
        file = symbol.sources[0].path
        for member in symbol.members:
            by_file.setdefault(file, []).append((member.line, member.end_line, member.name))
    return MemberRanges(by_file={file: sorted(items) for file, items in sorted(by_file.items())})
