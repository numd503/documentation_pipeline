"""Модели нормализованного реестра архитектурных элементов (R03).

Формат — контракт между разведкой и графом. Слева от него догадки и ручная
работа, справа детерминированная сборка (Р10): всё, что попало сюда, человек
прочитал и закоммитил, а всё, что сюда не попало, в граф не входит.

Четыре вида записей и ни одного «прочего»:

* `entry_point` — корень графа: workflow, джоб, обработчик события,
  HTTP-эндпоинт, грид-сервис, страница. То, что **запускают**;
* `data` — таблица, представление, процедура. То, что **трогают**;
* `seam` — шов между языками или через кластер: маршрут, имя грид-сервиса,
  имя очереди. Строка, которую обязаны знать обе стороны (Р2);
* `layer` — модуль и его роль. То, из чего репозиторий состоит.

Формат намеренно **не** спроектирован под конкретный репозиторий: ни одного
поля, которое имеет смысл только там, где мы его придумали. Проверка простая
и записана в критериях приёмки: заполнить файл руками на открытом репозитории
за пятнадцать минут, не упомянув ни одной чужой системы.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from docpipe.keys import (
    normalize_data_name,
    normalize_identifier,
    normalize_route_reference,
    normalize_seam_literal,
)

# Версия формата файла. Несовпадение — отказ с командой миграции, а не попытка
# прочитать: файл другой версии, прочитанный как этот, даёт записи, которых
# автор не писал.
ARCH_VERSION = "1"

# Словари видов закрыты, и у каждого есть `other`. Открытая строка выглядит
# гибче, но опечатка в ней создаёт категорию, которую никто не спрашивает,
# — реестр, который просто ничего не находит (та же причина, по которой
# `registries.yaml` проверяется целиком при загрузке). `other` оставляет
# выход для случая, которого мы не видели: вид кладётся в `attributes`.
EntryKind = Literal[
    "workflow",
    # Шаг объявленного процесса. Общий случай, а не частность платформы:
    # шаги есть у любого декларативного движка — задачи workflow, задания
    # конвейера CI, операторы расписания. Проверено на открытом репозитории:
    # `Task/@name` в 756 файлах workflow — это именно шаги.
    "workflow_step",
    "job",
    "event_handler",
    "http_endpoint",
    "grid_service",
    "page",
    "service",
    "cli",
    "other",
]
DataKind = Literal["table", "view", "procedure", "other"]
SeamKind = Literal["http_route", "grid_service", "queue", "topic", "file", "other"]
LayerRole = Literal[
    "host", "service", "library", "frontend", "tests", "generated", "tooling", "other"
]

# Как запись попала в файл. Не украшение: разница между «скилл предложил»
# и «человек подтвердил» — это и есть граница Р10, и она обязана быть видна
# в данных. Ранний признак того, что подтверждение выпало из цикла (риск Р-11),
# — файл, в котором `skill_proposed` не сменился ни разу.
Provenance = Literal["manual", "skill_proposed", "skill_confirmed", "adapter"]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(BaseModel):
    """Откуда взята запись: файл и место в нём.

    Запись без источника не принимается на уровне загрузки. Причина та же,
    по которой в правилах отсева обязателен `reason`: безымянная запись через
    месяц неотличима от выдумки, а выдумка в реестре становится уверенно
    неверным ребром графа — худшим видом ошибки (Р6).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Репо-относительный путь. Файл может и не существовать на момент проверки
    # (переименовали, удалили) — это находка `arch status`, а не отказ загрузки.
    file: str = Field(min_length=1)

    # Адрес записи внутри файла: XPath, ключ JSON, номер строки, имя символа.
    # Пусто законно, когда записью является файл целиком (один workflow —
    # один XML).
    record: str = ""

    # Хэш источника на момент снимка, в формате `sha256:…`. Единственный
    # способ узнать, что зеркало отстало, — хранить хэш того, что зеркалится;
    # правило перенесено из принятых хэшей документа без изменений.
    hash: str = ""


class DataField(_Base):
    """Поле узла данных: внутреннее имя, вид и человеческое название.

    `kind` — свободная строка, а не словарь: перечень видов полей у платформы
    заведомо неполон (G05b п. 4 требует читать их по префиксу, а не по белому
    списку). Ошибиться здесь дешевле, чем потерять поле.
    """

    name: str = Field(min_length=1)
    kind: str = ""
    display_name: str = ""
    # Ключ другого узла данных, на который ссылается поле. Связь читается
    # из декларации, а не выводится из графа кода.
    references: str = ""


class _RecordBase(_Base):
    """Общее у всех записей."""

    key: str = Field(min_length=1)
    # Человеческое имя. Пусто законно: у HTTP-эндпоинта ключ читаем сам по себе.
    # Для узлов данных сюда идёт `DisplayName` — на целевом репозитории это
    # единственный источник предметных слов, и он обязан попасть в поиск (G10).
    name: str = ""
    source: Source
    provenance: Provenance
    # Свободные пары для того, чего в схеме нет. Типизированный словарь,
    # а не `extra="allow"`: неизвестный ключ верхнего уровня — это опечатка,
    # и она обязана падать, а не молча оседать в модели.
    attributes: dict[str, str] = Field(default_factory=dict)
    note: str = ""


class EntryPointRecord(_RecordBase):
    """Корень графа: то, что запускают.

    Ключ — то, что обязан знать вызывающий: идентификатор workflow, имя джоба,
    имя грид-сервиса, маршрут. **Не** имя класса и не путь: они меняются
    от вещей без бизнес-смысла.
    """

    kind: Literal["entry_point"] = "entry_point"
    entry_kind: EntryKind
    # Подсказки на код: FQN, имена классов, пути. Именно подсказки — связь
    # «запись → узел кода» делает граф (G03), и её отсутствие не дефект:
    # корень существует в графе независимо от того, нашёлся ли под него код.
    #
    # Список, а не строка, потому что у одного якоря бывает несколько
    # реализаций, и это не экзотика: на паре «список + EventType» в реальном
    # реестре сидят два обработчика — аудит и запуск процесса, — и оба
    # срабатывают. Одна строка потеряла бы второй молча, а «молча потерянный
    # корень» — это ветка графа, которой в ответе просто нет.
    #
    # В файле пишется и строкой, и списком: у большинства записей реализация
    # одна, и заставлять человека писать список ради единственного значения
    # — плата без выигрыша.
    impl: tuple[str, ...] = ()

    @field_validator("impl", mode="before")
    @classmethod
    def _impl_as_tuple(cls, value: object) -> object:
        return (value,) if isinstance(value, str) and value else value

    route: str = ""
    http_method: str = ""
    # Ключи записей `data`, на которых точка входа сидит по декларации.
    # «Список + EventType» ссылается на свою таблицу без прохода через код.
    touches: tuple[str, ...] = ()

    @property
    def normalized_key(self) -> str:
        if self.entry_kind == "http_endpoint":
            return normalize_route_reference(self.http_method, self.route or self.key)
        return normalize_identifier(self.key)


class DataRecord(_RecordBase):
    """Узел данных, объявленный в реестре, а не выведенный из кода."""

    kind: Literal["data"] = "data"
    data_kind: DataKind = "table"
    # Таблица-носитель, когда ключ — логическое имя списка, а не имя таблицы.
    table: str = ""
    fields: tuple[DataField, ...] = ()
    # Связи сущностей, объявленные в реестре: рёбра `references` (G05b).
    references: tuple[str, ...] = ()

    @property
    def normalized_key(self) -> str:
        return normalize_data_name(self.table or self.key)


class SeamRecord(_RecordBase):
    """Шов: строка, которую знают обе стороны.

    Между языками вызовов нет — есть сообщение по литералу, и другой
    реализуемой архитектуры связи не существует (Р2).
    """

    kind: Literal["seam"] = "seam"
    seam_kind: SeamKind
    literal: str = ""
    http_method: str = ""
    # Стороны шва: языки, модули, проекты — как их называет репозиторий.
    # Свободные строки: словарь сторон у каждого репозитория свой.
    sides: tuple[str, ...] = ()

    @property
    def normalized_key(self) -> str:
        return normalize_seam_literal(self.seam_kind, self.literal or self.key)


class LayerRecord(_RecordBase):
    """Модуль и его роль: из чего репозиторий состоит."""

    kind: Literal["layer"] = "layer"
    role: LayerRole
    path: str = ""
    language: str = ""

    @property
    def normalized_key(self) -> str:
        return normalize_identifier(self.path or self.key)


ArchRecord = Annotated[
    EntryPointRecord | DataRecord | SeamRecord | LayerRecord,
    Field(discriminator="kind"),
]


class ArchRegistry(_Base):
    """Содержимое `arch-registry.yaml`.

    Пустой реестр — валидное состояние, а не ошибка: на репозитории без
    реестров граф строится и без него, и «реестров здесь нет» — успешный
    исход разведки, а не пустой.
    """

    version: str = ARCH_VERSION
    records: tuple[ArchRecord, ...] = ()

    def of_kind(self, kind: str) -> list[ArchRecord]:
        """Записи одного вида, в порядке файла.

        Порядок файла, а не сортировка: он несёт смысл для человека, который
        файл читает, а всё, что уходит в детерминированный вывод, сортируется
        на своей стороне явным ключом.
        """
        return [record for record in self.records if record.kind == kind]

    def find(self, kind: str, key: str) -> ArchRecord | None:
        """Запись по нормализованному ключу.

        Нормализация обеих сторон обязательна: спрашивающий пишет ключ так,
        как его знает он, а не так, как его записал автор реестра.
        """
        wanted = _normalize_for_lookup(kind, key)
        for record in self.records:
            if record.kind == kind and record.normalized_key == wanted:
                return record
        return None


def _normalize_for_lookup(kind: str, key: str) -> str:
    if kind == "data":
        return normalize_data_name(key)
    return normalize_identifier(key)
