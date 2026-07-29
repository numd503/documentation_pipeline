"""Разбор значений, которые в реестрах хранятся не как значения.

Реестры АС CF складывают внутрь одного атрибута то, что по смыслу является
структурой: расписание — вложенным XML, тип — парой «FQN, сборка». Общий
читатель отдаёт такое поле строкой; здесь оно превращается в данные.

Обе функции терпимы к мусору: реестр правился годами, и одна нечитаемая запись
не должна ронять инвентаризацию. Признак неудачи — `None`, о котором сообщает
вызывающий.
"""

import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict


class Schedule(BaseModel):
    """Расписание джоба. `interval_seconds` — именно секунды, проверено на АС CF.

    Из этого следует вывод, важный для формулировок в документации: `Interval=60`
    у отчётного джоба — это опрашивающий цикл, а не «отчёты формируются раз
    в минуту». Расписанием интервал становится на крупных значениях (43200, 86400).
    Различать одно от другого — дело сборщика документа, а не разбора.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    interval_seconds: int | None = None
    first_time: str | None = None


def parse_schedule(raw: str) -> Schedule | None:
    """Разобрать `JOBSCHEDULE`.

    В файле значение записано экранированным XML внутри атрибута
    (`&lt;JobSchedule Interval=&quot;60&quot; /&gt;`), но разэкранирование делает
    уже первый разбор XML — сюда строка приходит в виде `<JobSchedule …/>`.
    Остаётся второй `fromstring`; именно его легко не заметить, приняв значение
    за обычную строку.
    """
    try:
        elem = ET.fromstring(raw)
    except ET.ParseError:
        return None

    raw_interval = elem.get("Interval")
    interval: int | None
    try:
        interval = None if raw_interval is None else int(raw_interval)
    except ValueError:
        interval = None

    return Schedule(interval_seconds=interval, first_time=elem.get("FirstTime"))


def split_type_name(raw: str) -> tuple[str, str | None]:
    """Разделить assembly-qualified имя типа на FQN и простое имя сборки.

    Резать надо по первой запятой **вне квадратных скобок**: у закрытого дженерика
    аргументы записаны как `Foo`2[[System.String, mscorlib],[System.Int32, mscorlib]], Asm`,
    и наивный `split(",")[0]` вернёт `Foo`2[[System.String` — обрубок, который
    не найдётся ни в одном индексе символов и будет выглядеть как отсутствующий тип.

    Из остатка берётся только первый сегмент: полное отображаемое имя сборки
    содержит `Version=`, `Culture=`, `PublicKeyToken=`, а сопоставляется всё это
    с именем проекта, то есть с простым именем.
    """
    depth = 0
    for index, char in enumerate(raw):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            fqn = raw[:index].strip()
            rest = raw[index + 1 :]
            assembly = rest.split(",", 1)[0].strip()
            return fqn, assembly or None

    return raw.strip(), None
