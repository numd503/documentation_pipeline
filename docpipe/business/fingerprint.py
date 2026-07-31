"""`business_hash` — отпечаток бизнес-смысла документа.

Считается по **результату разрешения** якорей, а не по тексту документа.
Хэш по тексту менялся бы только тогда, когда аналитик сам правит файл, то есть
не ловил бы ровно того, ради чего заводится: изменения в коде и реестрах,
о котором аналитик не знает.

Из состава хэша напрямую следует, какой рефакторинг создаёт работу. Правило
одно: в хэш входит то, что обязан знать вызывающий, и не входит ничего, что
можно переименовать, никого снаружи не сломав. Поэтому имён C#-типов, путей
и namespace здесь нет — единственное исключение, имена методов grid-сервиса,
оговорено в `resolve._grid_service_methods`.
"""

from docpipe.business.model import Anchor, BusinessDoc
from docpipe.business.resolve import Resolution
from docpipe.hashing import stable_hash


def hashed_anchors(doc: BusinessDoc) -> list[Anchor]:
    """Якоря, участвующие в хэше: `entry` и `produces`.

    `upstream` не входит никогда. Это чужая зона ответственности: процесс
    может начинаться у другой команды, её правки — не наше изменение,
    и реагировать на них `review` значило бы просить сверять чужой код.
    """
    return [*doc.entry, *doc.produces]


def business_hash(resolutions: list[Resolution]) -> str:
    """Отпечаток по набору разрешений.

    Порядок якорей во front matter на результат не влияет: набор сортируется.
    Иначе перестановка двух строк в документе давала бы `review` на пустом
    месте, а документ правят руками постоянно.

    Якорь с `verify: false` не входит вовсе — разрешать в нём нечего.
    Неразрешённый якорь входит своей идентичностью с пустыми фактами: то, что
    точка входа перестала находиться, — событие, и увидеть его надо.
    """
    payload = sorted(
        (
            {
                "kind": item.anchor.kind,
                "scope": item.anchor.scope or "",
                "ref": item.anchor.ref,
                "version": item.anchor.version or "",
                "facts": item.facts,
            }
            for item in resolutions
            if item.anchor.verify
        ),
        key=lambda entry: (entry["kind"], entry["scope"], entry["ref"], entry["version"]),
    )
    return stable_hash(payload)
