"""Клиент на Python, ходящий в .NET по HTTP.

Конструкция поймана на боевой системе: маршрут собирается конкатенацией,
поэтому статический поиск литерала `api/ml/innerdebts/state/byclient`
не находит **ничего** — ни в этом файле, ни в каком другом. Единственный
способ узнать про эту связь — разведка и запись шва в реестр (Р2).

Файл лежит в фикстуре .NET намеренно: репозиторий, в котором соседствуют
C# и Python, — это и есть проверяемый случай, а не искусственная пара.

Каталог называется `clients`, а не `tools`, и это не вкусовщина: разборщик
0.6.0 **молча** пропускает файлы под `tools/`, `scripts/`, `build/`,
`vendor/` и `bin/` — на любом языке. Замерено прямым прогоном; ни ошибки,
ни категории в отчёте при этом не возникает.
"""

import urllib.request

BASE = "http://cf-api/api/ml"


class PriceClient:
    """Забирает состояние по клиенту и публикует пересчёт."""

    def __init__(self, publisher):
        self._publisher = publisher

    def load(self, client_id):
        # Маршрут собран из кусков: ни одного целого литерала здесь нет.
        url = BASE + "/innerdebts" + "/state/byclient?clientId=" + str(client_id)
        with urllib.request.urlopen(url) as response:
            return response.read()

    def publish(self, payload):
        # Имя топика тоже собрано: префикс окружения плюс постоянная часть.
        self._publisher.send("prices" + "-recalculated", payload)
