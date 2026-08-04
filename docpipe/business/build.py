"""Сборка бизнес-документа: front matter и генерируемый блок.

Зоны и правило записи — те же, что в шаге 2, и по той же причине: `docpipe:` —
проекция, пересобирается всегда; `docpipe_state:` — состояние, сохраняется;
секции — авторские, **никогда** не затираются. Инвариант
`assemble(parse_document(t)) == t` обязан держаться и здесь.

Генерируемый блок отвечает на вопрос «чем этот процесс является в коде»
**без единой написанной руками ссылки**. Ссылка, поставленная человеком
в авторской секции, при переносе технического документа не починится, и узнают
об этом нескоро; собранная здесь — пересобирается в том же прогоне.
"""

import posixpath
from typing import Any

import yaml

from docpipe.business.model import Anchor, BusinessDoc, Catalog
from docpipe.business.resolve import (
    ANCHOR_KIND_BY_REGISTRY,
    Resolution,
    ResolveContext,
    resolve_all,
)
from docpipe.materialize.document import MANAGED_END, MANAGED_START, parse_document
from docpipe.materialize.ownership import Ownership, owner_of
from docpipe.registry.anchors import AnchorMatch

NOT_OWNED = "(не задан)"


def relative(source: str, target: str) -> str:
    """Ссылка из одного документа в другой.

    `posixpath.relpath`, а не `os.path.relpath`: последний на Windows даёт
    `..\\modules\\...`, и дерево документации оказывалось бы разным
    в зависимости от того, на какой машине его собрали.
    """
    return posixpath.relpath(target, posixpath.dirname(source))


def cell(value: str) -> str:
    """Значение для ячейки markdown-таблицы.

    `JOBTITLE` содержит двоеточия и пробелы, `DisplayName` и `KeyValue`
    workflow — кириллицу, а вертикальная черта внутри значения разорвала бы
    таблицу. Обратные кавычки плюс экранирование `|` закрывают оба случая.
    """
    return f"`{value.replace('|', '\\|')}`" if value else "—"


# --------------------------------------------------------------------------------------
# front matter
# --------------------------------------------------------------------------------------


def front_matter(
    doc: BusinessDoc,
    state: dict[str, Any] | None,
    preserved: dict[str, Any] | None = None,
) -> str:
    """Собрать front matter: проекция, состояние и чужие ключи.

    **Текущего `business_hash` в проекции нет намеренно.** Единственный
    хранимый хэш — принятый, и он лежит в `docpipe_state`. Зеркало текущего
    завело бы второй источник, который обязан отстать: документ правят руками
    чаще, чем пересобирают, — и `accept`, взявший значение оттуда, зафиксировал
    бы устаревшее. Пересчитать хэш дешевле, чем сверять два.

    Чужие ключи выводятся **отсортированными**: их порядок — тот, в котором их
    вернул разбор, то есть тот, что человек мог поменять руками, и без
    сортировки перестановка двух строк вызывала бы перезапись.
    """
    projection: dict[str, Any] = {
        "schema": doc.schema_id,
        "id": doc.id,
        "kind": doc.kind,
        "title": doc.title,
    }
    for key in ("capability", "owner_team", "external_ref"):
        value = getattr(doc, key)
        if value is not None:
            projection[key] = value
    projection["status"] = doc.status
    if doc.supersedes:
        projection["supersedes"] = list(doc.supersedes)

    for key in ("entry", "upstream", "produces"):
        anchors: list[Anchor] = getattr(doc, key)
        if anchors:
            projection[key] = [
                anchor.model_dump(mode="json", exclude_defaults=True) for anchor in anchors
            ]
    if doc.contracts:
        projection["contracts"] = [item.model_dump(mode="json") for item in doc.contracts]

    mapping: dict[str, Any] = {"docpipe": projection}
    mapping["docpipe_state"] = state if state is not None else {"accepted": None, "review": None}
    for key in sorted(preserved or {}):
        mapping[key] = (preserved or {})[key]

    dumped = yaml.safe_dump(
        mapping, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10**6
    )
    return f"---\n{dumped}---\n"


def entry_snippet(match: AnchorMatch) -> str:
    """Готовый кусок `entry` для найденного якоря.

    Печатается ровно то, что вставляется в документ, а не описание того, что
    надо вставить. Половина вопросов при настройке — «что именно из вывода
    переносить»: строка показа собрана для человека и обратно **не
    разбирается**, поэтому собирать её глазами обратно в поля никто не обязан.
    """
    kind = ANCHOR_KIND_BY_REGISTRY.get(match.anchor.kind, match.anchor.kind)

    # Порядок ключей — как в `Anchor`, потому что `front_matter` пересоберёт
    # проекцию через `model_dump`. Иначе первый же `business build` переставил
    # бы строки, и человек решил бы, что вставил что-то не то.
    item: dict[str, Any] = {"kind": kind, "ref": match.anchor.ref}
    if match.scope:
        item["scope"] = match.scope
    if match.anchor.version:
        item["version"] = match.anchor.version

    dumped = yaml.safe_dump(
        [item], allow_unicode=True, sort_keys=False, default_flow_style=False, width=10**6
    )
    # Отступ уровня `docpipe:` уже здесь: сниппет вставляется в front matter
    # как есть, и требовать от человека досчитать пробелы — это ещё один шаг,
    # на котором ошибаются.
    return "  entry:\n" + "".join(f"  {line}\n" for line in dumped.strip().splitlines())


# --------------------------------------------------------------------------------------
# Генерируемый блок
# --------------------------------------------------------------------------------------


def _entries(resolutions: list[Resolution]) -> list[str]:
    if not resolutions:
        return ["Нет."]

    lines = ["| Вид | Якорь | Где объявлено |", "|---|---|---|"]
    for item in resolutions:
        where = ", ".join(item.sources) if item.sources else "не найдено"
        lines.append(f"| {cell(item.anchor.kind)} | {cell(item.anchor.display)} | {cell(where)} |")

    # Сужение обязано быть видно в самом документе. Иначе читатель решит, что
    # в «Реализации» перечислено всё, что вызывается по этому якорю, — а там
    # только наша часть, и остальное описано в другом месте или нигде.
    narrowed = [item.anchor for item in resolutions if item.anchor.only is not None]
    if narrowed:
        lines.append("")
        for anchor in narrowed:
            assert anchor.only is not None
            lines.append(f"Сужено до своей части: {cell(anchor.display)} — {anchor.only.display}.")
        lines.append("Остальные участники этих точек входа описаны не здесь.")
    return lines


def _implementation(doc: BusinessDoc, resolutions: list[Resolution]) -> list[str]:
    rows: list[tuple[str, str]] = []
    for item in resolutions:
        for target in item.targets:
            if target.doc_path:
                href = relative(doc.doc_path, target.doc_path)
                rows.append((item.anchor.display, f"[{target.fqn}]({href})"))
            else:
                rows.append((item.anchor.display, f"`{target.fqn}` — вне дерева документации"))

    if not rows:
        return ["Нет."]

    lines = ["| Якорь | Документ типа |", "|---|---|"]
    lines += [f"| {cell(anchor)} | {link} |" for anchor, link in sorted(set(rows))]
    return lines


def _participants(
    resolutions: list[Resolution], ctx: ResolveContext, ownership: Ownership | None, own: str | None
) -> list[str]:
    """Разбивка шагов процесса по командам.

    Ради этого раздела всё и затевается: граница ответственности внутри чужого
    процесса **вычисляется**, а не ведётся руками. Команда описывает свою часть
    и видит, чья остальная, — без списка, который надо сопровождать.
    """
    steps: list[tuple[str, str]] = []
    for item in resolutions:
        if item.anchor.kind != "workflow":
            continue
        for step in item.facts.get("steps", []):
            steps.append((str(step["id"]), _step_team(item, str(step["id"]), ctx, ownership)))

    if not steps:
        return ["Нет."]

    counted: dict[str, list[str]] = {}
    for step_id, team in sorted(steps):
        counted.setdefault(team, []).append(step_id)

    lines = []
    for team, names in sorted(counted.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        mark = " (наши)" if own and team == own else ""
        listed = ", ".join(f"`{name}`" for name in names)
        lines.append(f"- {cell(team)}{mark} — шагов {len(names)}: {listed}")
    return lines


def _step_team(
    item: Resolution, step_id: str, ctx: ResolveContext, ownership: Ownership | None
) -> str:
    """Команда шага — по реализации, а не по объявлению.

    `StepType` в хэш не входит (переименование класса шага смысла не меняет),
    но для ответа «чей это шаг» он единственный источник: в реестре команды
    у шага нет.
    """
    if ownership is None:
        return NOT_OWNED

    for anchors in ctx.by_key.values():
        for anchor in anchors:
            if anchor.kind != "workflow_step" or anchor.ref != step_id:
                continue
            for target in anchor.targets:
                node = ctx.nodes_by_id.get(target.node_id or "")
                if node is not None:
                    decision = owner_of(node, ownership)
                    if decision.team:
                        return decision.team
    return NOT_OWNED


DIRECTIONS = {"in": "на входе", "out": "на выходе", "state": "состояние"}


def _data(doc: BusinessDoc, resolutions: list[Resolution]) -> list[str]:
    """Данные, пересекающие границу процесса.

    Агрегат состояния workflow выводится из реестра, а не объявляется, поэтому
    объявленный контракт с тем же `ref` не повторяется: одна и та же строка
    дважды выглядит как две разные вещи.
    """
    derived: list[str] = []
    for item in resolutions:
        if item.anchor.kind != "workflow":
            continue
        derived += [target.fqn for target in item.targets if target.field == "data_type"]

    lines = [f"- состояние процесса: {cell(value)}" for value in sorted(set(derived))]
    lines += [
        f"- {DIRECTIONS[contract.direction]}: {cell(contract.ref)}"
        for contract in doc.contracts
        if contract.ref not in derived
    ]
    return lines or ["Нет."]


def _outside(doc: BusinessDoc) -> list[str]:
    if not doc.upstream:
        return ["Нет."]

    lines = []
    for anchor in doc.upstream:
        owner = f", владелец {cell(anchor.owner)}" if anchor.owner else ""
        note = f" — {anchor.note}" if anchor.note else ""
        mark = "" if anchor.verify else ", не проверяется"
        lines.append(f"- {cell(anchor.kind)} {cell(anchor.ref)}{owner}{mark}{note}")
    return lines


def generated_block(
    doc: BusinessDoc,
    resolutions: list[Resolution],
    ctx: ResolveContext,
    ownership: Ownership | None = None,
    notes: list[str] | None = None,
) -> str:
    """Содержимое генерируемого блока. Порядок разделов фиксирован.

    Раздел без данных печатается со словом «Нет.»: пропущенный раздел выглядит
    как «инструмент не умеет», а «Нет.» — как факт, и это разные сообщения.
    """
    entry_resolutions = [item for item in resolutions if item.anchor in doc.entry]

    lines = [
        f"**{doc.title}** — {doc.kind}"
        + (f", возможность `{doc.capability}`" if doc.capability else "")
        + (f", команда `{doc.owner_team}`" if doc.owner_team else "")
        + f", статус `{doc.status}`.",
        "",
        "Собрано `docpipe business build` по реестрам и манифесту.",
        "Инструмент видит репозиторий, а не боевую БД: фактическое состояние",
        "джобов и workflow в среде может отличаться.",
    ]
    for note in notes or []:
        lines += ["", note]

    for title, body in (
        ("Точки входа", _entries(entry_resolutions)),
        ("Реализация", _implementation(doc, resolutions)),
        ("Участники", _participants(resolutions, ctx, ownership, doc.owner_team)),
        ("Данные", _data(doc, resolutions)),
        ("Вне зоны ответственности", _outside(doc)),
    ):
        lines += ["", f"### {title}", ""] + body

    return "\n".join(lines) + "\n"


def compose(
    doc: BusinessDoc,
    ctx: ResolveContext,
    existing: str,
    ownership: Ownership | None = None,
    notes: list[str] | None = None,
    state: dict[str, Any] | None = None,
) -> str:
    """Пересобрать документ поверх его текущего текста.

    Разбор и сборка — из шага 2 без изменений: формат зон общий, и вторая
    реализация слияния разошлась бы с первой на первом же документе с чужими
    ключами front matter.

    Скелет здесь не участвует. Создание документа — отдельное действие
    (`business new`), и совмещать его со сборкой нельзя: тогда `build` начал бы
    порождать файлы, а бизнес-документы создаются людьми, а не выводятся из кода.
    """
    resolutions = resolve_all(doc.anchors, ctx)
    body = generated_block(doc, resolutions, ctx, ownership, notes)
    block = f"{MANAGED_START}\n{body}{MANAGED_END}\n"

    parsed = parse_document(existing)

    # Маркеры генерируемого блока принадлежат инструменту, поэтому приводятся
    # к каноническому виду. Маркеры авторских секций — нет: их писал человек.
    segments = [
        segment.model_copy(update={"body": "", "text": block})
        if segment.kind == "generated"
        else segment
        for segment in parsed.segments
    ]

    preserved = {
        key: value
        for key, value in (parsed.front_matter or {}).items()
        if key not in ("docpipe", "docpipe_state")
    }

    # Приёмка идёт через ту же запись, что и обычная пересборка. Отдельный путь
    # был бы второй реализацией слияния зон, и разошлись бы они на первом же
    # документе с чужими ключами front matter.
    #
    # Разделяющая пустая строка сюда не добавляется: она уже есть в сохранённом
    # теле документа, и вторая копилась бы при каждом прогоне.
    head = front_matter(doc, state if state is not None else parsed.state, preserved)
    return head + "".join(segment.text for segment in segments)


def link_warnings(doc: BusinessDoc, resolutions: list[Resolution]) -> list[str]:
    """Якоря документа, которые не дадут ссылки на техническую документацию.

    Сборка молчит об этом сама по себе: «Точки входа» заполнены, «Где объявлено»
    указывает на файл реестра — документ выглядит собранным. А «Реализация»
    при этом пуста, и понять, почему, можно только прочитав её глазами и зная,
    как устроен резолв. Три причины дают ровно один и тот же вид, и различить
    их обязан инструмент, а не человек.
    """
    warnings: list[str] = []
    for item in resolutions:
        anchor = item.anchor

        # `verify: false` — объявленная чужая зона, ссылки там не ждут.
        # Неразрешённый якорь тоже молчит здесь: про него говорит `unresolved`
        # в линте, а дублирование приучает пролистывать оба сообщения.
        if not anchor.verify:
            continue

        if item.selector_missed:
            assert anchor.only is not None
            holders = ", ".join(item.candidates) or "никто"
            warnings.append(
                f"{doc.doc_path}: якорь {anchor.kind} {anchor.display} разрешён, но"
                f" `only` ({anchor.only.display}) не совпал ни с одной записью —"
                f" ссылки на технический документ не будет. Сейчас на якоре: {holders}"
            )
        elif item.confidence == "registry" and not item.targets:
            warnings.append(
                f"{doc.doc_path}: якорь {anchor.kind} {anchor.display} разрешён, но"
                " в записи реестра не объявлен класс реализации — ссылки"
                " на технический документ не будет"
            )
        elif item.targets and not any(target.node_id for target in item.targets):
            missing = ", ".join(sorted({target.fqn for target in item.targets}))
            warnings.append(
                f"{doc.doc_path}: якорь {anchor.kind} {anchor.display} разрешён, но"
                f" реализация не найдена среди узлов документации ({missing}) —"
                " ссылки на технический документ не будет"
            )
    return warnings


def backlinks(catalog: Catalog, ctx: ResolveContext) -> dict[str, list[tuple[str, str]]]:
    """Обратный индекс «узел документации → бизнес-документы».

    Строится один раз по каталогу и передаётся шагу 2 **как данные**:
    `docpipe/materialize/**` не должен импортировать `docpipe/business/**`,
    иначе шаг 2 перестанет быть самостоятельным.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for doc in catalog.docs:
        for item in resolve_all(doc.anchors, ctx):
            for target in item.targets:
                if target.node_id:
                    index.setdefault(target.node_id, []).append((doc.title, doc.doc_path))
    return {node_id: sorted(set(pairs)) for node_id, pairs in sorted(index.items())}
