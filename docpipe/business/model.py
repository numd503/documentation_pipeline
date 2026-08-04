"""Модели бизнес-каталога.

Бизнес-слой ссылается на технику **только якорями** — строками, которые обязан
знать вызывающий: имя сервиса кластера, заголовок джоба, идентификатор workflow,
пара «список + событие». Ни `node_id`, ни `doc_path`, ни FQN здесь не появляются
никогда: тогда рефакторинг физически не может сломать бизнес-документ.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

DocKind = Literal["process", "entity", "capability"]
Status = Literal["draft", "active", "deprecated"]
Direction = Literal["in", "out", "state"]

# Виды якорей. Проверяются при загрузке, а не при первом применении: опечатка
# иначе означает якорь, который просто никогда не разрешится, и выглядит это
# как «инструмент не нашёл», а не как «в документе ошибка».
ANCHOR_KINDS: Final[frozenset[str]] = frozenset(
    {
        "grid_service",
        "job",
        "workflow",
        "workflow_step",
        "list_event",
        "kafka",
        "http",
        "table",
        "type",
    }
)

# Префикс идентификатора однозначно задаёт вид и каталог. Второго источника
# истины про вид документа быть не должно — они разойдутся.
KIND_BY_PREFIX: Final[dict[str, DocKind]] = {
    "bp": "process",
    "be": "entity",
    "cap": "capability",
}

FOLDER_BY_PREFIX: Final[dict[str, str]] = {
    "bp": "processes",
    "be": "entities",
    "cap": "capabilities",
}


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class Selector(_Base):
    """Сужение якоря до своей части общей точки входа.

    У одной пары «список + `EventType`» бывает несколько подписчиков, и якорь
    их не различает: он адресует контракт, а не класс. Селектор не заменяет
    якорь и не участвует в поиске — он отбирает из уже найденного, поэтому
    ошибка в нём означает «сузили в пустоту», а не «точка входа исчезла».

    `assembly` работает сразу и переживает переименование класса: сборка —
    единица поставки, а не имя типа. `team` устойчивее (переживает и переезд
    между сборками), но требует заполненного `ownership.yaml`.

    Два селектора сразу — два источника истины, поэтому запрещено.
    """

    assembly: str | None = None
    team: str | None = None

    @property
    def display(self) -> str:
        return f"сборка {self.assembly}" if self.assembly else f"команда {self.team}"


class Anchor(_Base):
    """Точка контракта, на которую ссылается документ.

    `scope` — то, внутри чего якорь объявлен: список для `list_event`, workflow
    для `workflow_step`. Имя общее с `ResolvedAnchor` намеренно: одна вещь —
    одно имя, иначе сопоставление каталога с инвентаризацией превращается
    в перевод.

    `verify: false` — граница зоны ответственности. Такой якорь не проверяется
    никогда и обязательств не создаёт: процесс может начинаться в чужой команде,
    и требовать доказательства чужого триггера — значит получить вечно красный
    линт и его отключение.
    """

    kind: str
    ref: str
    scope: str | None = None
    version: str | None = None
    only: Selector | None = None
    owner: str | None = None
    verify: bool = True
    note: str | None = None

    @property
    def display(self) -> str:
        """Только для показа. Обратно **не разбирается**."""
        text = f"{self.scope}/{self.ref}" if self.scope else self.ref
        return f"{text}@{self.version}" if self.version else text


class Contract(_Base):
    """Данные, пересекающие границу процесса.

    `state` — агрегат состояния: у workflow это `DataType`, то есть то, чем
    процесс оперирует от старта до завершения.
    """

    direction: Direction
    ref: str


class BusinessDoc(_Base):
    """Один бизнес-документ: процесс, сущность или возможность."""

    schema_id: str = Field(alias="schema")
    id: str
    kind: DocKind
    title: str
    capability: str | None = None
    owner_team: str | None = None
    status: Status = "draft"
    supersedes: list[str] = Field(default_factory=list)
    entry: list[Anchor] = Field(default_factory=list)
    upstream: list[Anchor] = Field(default_factory=list)
    produces: list[Anchor] = Field(default_factory=list)
    contracts: list[Contract] = Field(default_factory=list)
    external_ref: str | None = None
    doc_path: str = ""

    @property
    def anchors(self) -> list[Anchor]:
        """Все якоря документа. `upstream` входит: он тоже описывает связь,
        просто не проверяется."""
        return [*self.entry, *self.upstream, *self.produces]


class Capability(_Base):
    """Узел дерева возможностей. Дерево маленькое и меняется раз в год,
    поэтому живёт одним файлом, а не документом на возможность."""

    id: str
    title: str
    parent: str | None = None


class Catalog(_Base):
    """Каталог целиком.

    Ошибки собираются, а не бросаются на первой: аналитик правит документы
    пачкой, и увидеть все проблемы сразу дешевле, чем по одной за прогон.
    """

    docs: list[BusinessDoc] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def by_id(self) -> dict[str, BusinessDoc]:
        return {doc.id: doc for doc in self.docs}
