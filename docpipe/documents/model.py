"""Модели документа: разбор на зоны.

Документ состоит из трёх зон с разной судьбой при прогоне:

* front matter — проекция манифеста плюс сохраняемое состояние;
* генерируемый блок — пересобирается всегда;
* авторские секции — **никогда** не затираются.

Сегмент хранит и тело, и полный исходный текст с маркерами. Это не избыточность:
без исходного текста сборка нормализовала бы маркеры к каноническому виду, и
обратимость терялась бы на первом же документе, который правили руками.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SegmentKind = Literal["literal", "generated", "section"]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Segment(_Base):
    """Кусок тела документа.

    `literal` — всё вне маркеров, сохраняется дословно;
    `generated` — пересобирается инструментом;
    `section` — авторский текст, сохраняется дословно.
    """

    kind: SegmentKind
    name: str | None = None
    body: str = ""
    text: str = ""


class ParsedDocument(_Base):
    """Разобранный документ.

    `assemble(parse_document(t)) == t` байт в байт — инвариант, на котором стоит
    вся сохранность авторского текста.
    """

    front_matter: dict[str, Any] | None = None
    front_matter_text: str = ""
    segments: list[Segment] = Field(default_factory=list)

    @property
    def docpipe(self) -> dict[str, Any] | None:
        """Зона проекции. `None`, если это не документ docpipe."""
        if self.front_matter is None:
            return None
        value = self.front_matter.get("docpipe")
        return value if isinstance(value, dict) else None

    @property
    def schema_id(self) -> str | None:
        """Версия формата **документа**: `materialize/1`, `business/1`.

        Именно по ней слои отличают свои документы от чужих. Отбор по одному лишь
        наличию `node_id` объявил бы весь бизнес-каталог сиротами шага 2.
        """
        docpipe = self.docpipe
        value = docpipe.get("schema") if docpipe else None
        return value if isinstance(value, str) else None

    @property
    def state(self) -> dict[str, Any] | None:
        if self.front_matter is None:
            return None
        value = self.front_matter.get("docpipe_state")
        return value if isinstance(value, dict) else None

    def section(self, name: str) -> Segment | None:
        return next((s for s in self.segments if s.kind == "section" and s.name == name), None)

    @property
    def section_names(self) -> list[str]:
        return [s.name for s in self.segments if s.kind == "section" and s.name]

    @property
    def generated(self) -> Segment | None:
        return next((s for s in self.segments if s.kind == "generated"), None)
