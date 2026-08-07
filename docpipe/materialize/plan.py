"""План шага 2: манифест плюс срез файловой системы → что делать с каждым файлом.

Чистая функция. Ни одной записи на диск: сначала считается весь план, потом
проверяется, и только потом применяется. Иначе ошибка на середине оставляет
дерево наполовину обновлённым, и об этом никто не знает.

Два «действия», которые легко перепутать, и потому названы по-разному:

* `file_action` — что сделать с файлом: создать, обновить, не трогать, отказаться;
* `agent_action` — что делать агенту шага 3: писать, проверить, пропустить.

Документ со статусом `current` всё равно получает `update`, если сменился
владелец или домен: это поля проекции.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

from docpipe.discovery import matches_glob
from docpipe.materialize.build import (
    BuildContext,
    build_front_matter,
    build_generated_block,
    dump_front_matter,
    template_refs,
)
from docpipe.materialize.document import (
    MANAGED_END,
    MANAGED_START,
    DocumentError,
    is_section_empty,
    parse_document,
    read_document,
)
from docpipe.materialize.model import ParsedDocument, Segment
from docpipe.materialize.ownership import Ownership, owner_of
from docpipe.materialize.template import (
    DEFAULT_TEMPLATE,
    Template,
    resolve_template,
    substitute,
)
from docpipe.model import DocNode, Manifest, SourceSpan

FileAction = Literal["create", "update", "unchanged", "refuse", "relocate"]
AgentAction = Literal["write", "review", "skip"]
Status = Literal[
    "missing", "empty", "undeclared", "stale", "drifted", "relocated", "current", "broken", "orphan"
]
Confidence = Literal["exact", "high", "probable"]

SCHEMA_PREFIX: Final[str] = "materialize/"

# Встроенный список, складывается с конфигурацией по образцу `emit.exclude_globs`.
# На АС CF каталог `docs/` содержит сам инструмент вместе с окружением, и обход
# без исключений зашёл бы в site-packages, где тысячи `.md`.
DEFAULT_DOCS_SCAN_EXCLUDE: Final[list[str]] = [
    "**/.venv/**",
    "**/site-packages/**",
    "**/node_modules/**",
    "**/.git/**",
]

# Порядок «сначала то, что важнее». Публичный: по нему же `worklist`
# упорядочивает очередь, и второй список разошёлся бы с этим при первом
# новом статусе.
STATUS_ORDER: Final[list[Status]] = [
    "broken",
    "missing",
    "stale",
    "drifted",
    "undeclared",
    "empty",
    "relocated",
    "orphan",
    "current",
]


@dataclass(frozen=True)
class Accepted:
    """Принятое состояние документа. Живёт только в самом документе."""

    signature_hash: str | None = None
    impl_hash: str | None = None
    kind: str | None = None
    members: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExistingDoc:
    """Документ, найденный на диске."""

    path: str
    text: str = ""
    parsed: ParsedDocument | None = None
    error: str | None = None

    @property
    def docpipe(self) -> dict[str, Any]:
        return (self.parsed.docpipe if self.parsed else None) or {}

    @property
    def node_id(self) -> str | None:
        value = self.docpipe.get("node_id")
        return value if isinstance(value, str) else None

    @property
    def accepted(self) -> Accepted | None:
        state = (self.parsed.state if self.parsed else None) or {}
        raw = state.get("accepted")
        if not isinstance(raw, dict):
            return None
        members = raw.get("members")
        return Accepted(
            signature_hash=raw.get("signature_hash"),
            impl_hash=raw.get("impl_hash"),
            kind=raw.get("kind"),
            members=sorted(members) if isinstance(members, list) else [],
        )

    @property
    def review_reason(self) -> str | None:
        state = (self.parsed.state if self.parsed else None) or {}
        raw = state.get("review")
        return raw.get("reason") if isinstance(raw, dict) else None


@dataclass(frozen=True)
class Changes:
    """Что именно изменилось — чтобы агент не перечитывал манифест."""

    members_added: list[str] = field(default_factory=list)
    members_removed: list[str] = field(default_factory=list)
    kind_changed: str | None = None
    relocated_from: str | None = None


@dataclass(frozen=True)
class PlannedDoc:
    doc_path: str
    node_id: str | None
    file_action: FileAction
    status: Status
    agent_action: AgentAction
    reason: str = ""
    content: str | None = None
    relocate_from: str | None = None
    confidence: Confidence | None = None
    team: str | None = None
    template: str = ""
    kind: str = ""
    template_ref: str = ""
    example_ref: str | None = None
    sources: list[SourceSpan] = field(default_factory=list)
    broken_links: list[str] = field(default_factory=list)
    empty_sections: list[str] = field(default_factory=list)
    orphan_sections: list[str] = field(default_factory=list)
    changes: Changes = field(default_factory=Changes)
    error: str | None = None


@dataclass(frozen=True)
class PlanOptions:
    docs_root: str = "docs"
    teams: tuple[str, ...] = ()
    force: bool = False
    # Документы, чьё принятое состояние надо переписать текущим (`docs accept`).
    # Приёмка проходит через тот же план и ту же запись: отдельный путь записи
    # означал бы вторую реализацию слияния зон.
    accept: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterializePlan:
    documents: list[PlannedDoc] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    manifest_partial: bool = False
    # Виды сущностей, документированные базовым скелетом за неимением своего,
    # и сколько узлов это затронуло. Подстановка обязана быть видимой числом:
    # `template: contoller` с опечаткой — не новый вид, а ошибка, и раньше её
    # ловил отказ прогона.
    substituted: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        counted = Counter(doc.status for doc in self.documents)
        return {status: counted[status] for status in STATUS_ORDER if counted[status]}


# --------------------------------------------------------------------------------------
# Обход дерева документации
# --------------------------------------------------------------------------------------


def scan_docs(root: Path, docs_root: str, exclude: list[str] | None = None) -> list[ExistingDoc]:
    """Найти документы шага 2.

    Документом считается `.md`, у которого во front matter есть `docpipe.node_id`
    **и** `docpipe.schema` вида `materialize/*`. Проверка схемы обязательна:
    бизнес-каталог лежит в том же дереве, имеет тот же формат зон и тот же ключ
    `docpipe:` — отбор по одному лишь `node_id` объявил бы его сиротами.
    """
    base = root / docs_root
    if not base.is_dir():
        return []

    globs = DEFAULT_DOCS_SCAN_EXCLUDE + list(exclude or [])
    found: list[ExistingDoc] = []

    for path in sorted(base.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if any(matches_glob(rel, glob) for glob in globs):
            continue

        # Отсев по первым байтам до чтения целиком: в `.venv` встречаются `.md`
        # на мегабайты, и читать их незачем.
        try:
            if path.read_bytes()[:4] not in (b"---\n", b"---\r"):
                continue
        except OSError:
            continue

        try:
            text, parsed = read_document(path)
        except DocumentError as exc:
            # Битый документ — кандидат в `broken`, но только если он вообще
            # наш. Определить это по испорченному файлу нельзя, поэтому берём
            # всё, что лежит под `docs_root` и начинается с `---`.
            found.append(ExistingDoc(path=rel, error=str(exc)))
            continue
        except OSError as exc:
            found.append(ExistingDoc(path=rel, error=f"файл не читается: {exc}"))
            continue

        schema = parsed.schema_id or ""
        docpipe = parsed.docpipe or {}
        if not schema.startswith(SCHEMA_PREFIX) or not docpipe.get("node_id"):
            continue

        found.append(ExistingDoc(path=rel, text=text, parsed=parsed))

    return found


# --------------------------------------------------------------------------------------
# Сборка текста документа
# --------------------------------------------------------------------------------------


def _generated_segment(body: str) -> str:
    """Генерируемый блок собирается канонически.

    Маркеры авторских секций сохраняются дословно — их писал человек. Маркеры
    генерируемого блока принадлежат инструменту, и приведение их к каноническому
    виду безопасно: идемпотентность формулируется как сходимость, а не как
    совпадение с исходником.
    """
    return f"{MANAGED_START}\n{body}{MANAGED_END}\n"


def _template_blocks(template: Template) -> dict[str, str]:
    """Секции шаблона вместе с их обвязкой — заголовком перед маркером.

    Дописывать секцию без заголовка значило бы вставить в документ голые
    маркеры, а с угаданным заголовком — придумать текст, которого в шаблоне нет.
    """
    parsed = _parse_template(template)
    blocks: dict[str, str] = {}
    chrome = ""
    for segment in parsed.segments:
        if segment.kind == "literal":
            chrome = segment.text
        elif segment.kind == "section" and segment.name:
            blocks[segment.name] = chrome + segment.text
            chrome = ""
    return blocks


def _parse_template(template: Template) -> ParsedDocument:
    return parse_document(template.text)


def _render(front_matter: str, segments: list[Segment]) -> str:
    return front_matter + "".join(segment.text for segment in segments)


def _substitution_values(node: DocNode, team: str | None) -> dict[str, str]:
    return {
        "title": node.title,
        "fqn": node.symbol.fqn if node.symbol else node.title,
        "kind": node.kind,
        "module": node.module,
        "domain": node.domain,
        "team": team or "",
        "doc_path": node.doc_path,
        "node_id": node.id,
    }


def _compose(
    node: DocNode,
    template: Template,
    context: BuildContext,
    team: str | None,
    existing: ExistingDoc | None,
    state: dict[str, Any] | None,
) -> tuple[str, list[str], list[str]]:
    """Собрать текст документа. Возвращает `(текст, пустые секции, осиротевшие)`."""
    values = _substitution_values(node, team)

    # Осиротевшие секции считаются по ФАЙЛУ, а не по собранному тексту: они
    # приходят из документа, а не из шаблона, и знать о них надо до сборки —
    # строка про них идёт в генерируемый блок.
    present = list(existing.parsed.section_names) if existing and existing.parsed else []
    orphan_sections = [name for name in present if name not in template.sections]
    notes = (
        [
            f"Секции не из текущего шаблона `{template.name}`: "
            + ", ".join(f"`{name}`" for name in orphan_sections)
        ]
        if orphan_sections
        else []
    )
    generated = _generated_segment(build_generated_block(node, context, team, notes))

    if existing is None or existing.parsed is None:
        base = _parse_template(template)
        segments = [
            Segment(kind="generated", body="", text=generated)
            if segment.kind == "generated"
            else segment.model_copy(update={"text": substitute(segment.text, values)})
            for segment in base.segments
        ]
        preserved: dict[str, Any] = {}
    else:
        parsed = existing.parsed
        segments = [
            Segment(kind="generated", body="", text=generated)
            if segment.kind == "generated"
            else segment
            for segment in parsed.segments
        ]
        if not any(segment.kind == "generated" for segment in segments):
            segments.insert(0, Segment(kind="generated", body="", text=generated))

        # Секции шаблона, которых в файле нет, дописываются В КОНЕЦ. Вставка
        # по порядку шаблона требует угадать позицию относительно обвязки,
        # которую человек мог переписать; цена ошибки — перемешанный документ.
        blocks = _template_blocks(template)
        for name in template.sections:
            if name not in present:
                segments.append(
                    Segment(kind="literal", body="", text=substitute(blocks[name], values))
                )

        preserved = {
            key: value
            for key, value in (parsed.front_matter or {}).items()
            if key not in ("docpipe", "docpipe_state")
        }

    front_matter = dump_front_matter(build_front_matter(node, context, team), state, preserved)
    separator = "\n" if existing is None or existing.parsed is None else ""
    text = _render(front_matter + separator, segments)

    final = parse_document(text)
    empty = [name for name in final.section_names if is_section_empty(_body(final, name))]
    return text, empty, orphan_sections


def _body(parsed: ParsedDocument, name: str) -> str:
    segment = parsed.section(name)
    return segment.body if segment else ""


# --------------------------------------------------------------------------------------
# Решение для агента
# --------------------------------------------------------------------------------------


def public_members(node: DocNode) -> list[str]:
    """Имена публичных членов, отсортированные.

    Один источник и для приёмки, и для сравнения: считай их по-разному —
    и `accept` зафиксирует один список, а `decide` сравнит с другим, из-за чего
    документ навсегда останется `stale`.
    """
    if node.symbol is None:
        return []
    return sorted({m.name for m in node.symbol.members if "public" in m.modifiers})


def accepted_state(node: DocNode) -> dict[str, Any]:
    """Принятое состояние по узлу.

    Хэши берутся ИЗ МАНИФЕСТА, а не из front matter документа: front matter —
    зеркало и мог отстать (документ не материализовали после последнего `scan`).
    Взяв значение оттуда, приёмка зафиксировала бы устаревший хэш, и документ
    навсегда остался бы `current`, будучи `stale`.

    Времени здесь нет: оно сделало бы приёмку неидемпотентной — два прогона
    подряд меняли бы файл. История правок точнее хранится в git.
    """
    return {
        "accepted": {
            "signature_hash": node.signature_hash,
            "impl_hash": node.impl_hash,
            "kind": node.kind,
            "members": public_members(node),
        },
        "review": None,
    }


def decide(
    node: DocNode,
    existing: ExistingDoc | None,
    empty_sections: list[str],
    relocated_from: str | None = None,
) -> tuple[Status, AgentAction, str, Changes]:
    """Что делать агенту. Первое сработавшее условие выигрывает."""
    accepted = existing.accepted if existing else None
    members = public_members(node)

    if existing is None:
        return "missing", "write", "документа ещё нет", Changes(relocated_from=relocated_from)

    if empty_sections:
        return (
            "empty",
            "write",
            f"документ не наполнен: {', '.join(empty_sections)}",
            Changes(relocated_from=relocated_from),
        )

    if accepted and accepted.signature_hash and accepted.signature_hash != node.signature_hash:
        added = sorted(set(members) - set(accepted.members))
        removed = sorted(set(accepted.members) - set(members))
        detail = []
        if added:
            detail.append(f"добавлены {', '.join(added)}")
        if removed:
            detail.append(f"удалены {', '.join(removed)}")
        return (
            "stale",
            "write",
            "контракт изменился" + (": " + "; ".join(detail) if detail else ""),
            Changes(members_added=added, members_removed=removed, relocated_from=relocated_from),
        )

    if accepted is None:
        return (
            "undeclared",
            "review",
            "текст не сверялся с кодом",
            Changes(relocated_from=relocated_from),
        )

    if relocated_from or existing.review_reason:
        return (
            "relocated",
            "review",
            f"документ перенесён из {relocated_from}"
            if relocated_from
            else f"помечен к пересмотру: {existing.review_reason}",
            Changes(relocated_from=relocated_from),
        )

    if accepted.kind and accepted.kind != node.kind:
        return (
            "drifted",
            "review",
            f"вид сущности: {accepted.kind} → {node.kind}",
            Changes(kind_changed=f"{accepted.kind} → {node.kind}"),
        )

    if accepted.impl_hash and accepted.impl_hash != node.impl_hash:
        return "drifted", "review", "реализация изменилась, контракт тот же", Changes()

    return "current", "skip", "", Changes()


# --------------------------------------------------------------------------------------
# Сопоставление переносов
# --------------------------------------------------------------------------------------


def _doc_sources(doc: ExistingDoc) -> set[str]:
    raw = doc.docpipe.get("sources")
    if not isinstance(raw, list):
        return set()
    return {item["path"] for item in raw if isinstance(item, dict) and "path" in item}


def match_relocations(
    homeless: list[DocNode], orphans: list[ExistingDoc]
) -> tuple[dict[str, tuple[ExistingDoc, Confidence]], list[str]]:
    """Сопоставить узлы без файла с файлами без узла.

    Переезд — это когда изменился **код** и пересчитался `doc_path`, а файл
    с текстом остался на старом месте. Файл никто не переносил.

    `docpipe diff` здесь не помощник: он сопоставляет узлы по `id`, поэтому
    переименование типа даёт ему `removed` + `added`, а не `moved`.
    """
    matched: dict[str, tuple[ExistingDoc, Confidence]] = {}
    notes: list[str] = []
    used: set[str] = set()

    by_node_id = {doc.node_id: doc for doc in orphans if doc.node_id}
    for node in homeless:
        doc = by_node_id.get(node.id)
        if doc is not None:
            matched[node.id] = (doc, "exact")
            used.add(doc.path)

    # Требование «пара единственная в обе стороны» снимает главный риск —
    # два типа, обменявшихся именами.
    candidates: defaultdict[str, list[ExistingDoc]] = defaultdict(list)
    claimed: defaultdict[str, list[str]] = defaultdict(list)
    for node in homeless:
        if node.id in matched:
            continue
        node_sources = {source.path for source in node.symbol.sources} if node.symbol else set()
        for doc in orphans:
            if doc.path in used or doc.parsed is None:
                continue
            same_file = bool(node_sources & _doc_sources(doc))
            same_impl = bool(node.impl_hash) and doc.docpipe.get("impl_hash") == node.impl_hash
            if same_file or same_impl:
                candidates[node.id].append(doc)
                claimed[doc.path].append(node.id)

    for node_id, docs in sorted(candidates.items()):
        if len(docs) == 1 and len(claimed[docs[0].path]) == 1:
            matched[node_id] = (docs[0], "high")
            used.add(docs[0].path)
        else:
            notes.append(
                f"{node_id}: кандидатов на перенос несколько"
                f" ({', '.join(sorted(doc.path for doc in docs))});"
                " перенос вручную через `docs adopt`"
            )

    return matched, notes


# --------------------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------------------


def _blocking_errors(
    manifest: Manifest, existing: list[ExistingDoc], templates: dict[str, Template]
) -> list[str]:
    errors: list[str] = []

    # `docpipe validate` это ловит, но манифест мог быть собран старой версией
    # или отредактирован. Без повторной проверки второй узел молча затрёт
    # первый, и потеряется не пустой скелет, а написанный документ.
    paths: defaultdict[str, list[str]] = defaultdict(list)
    for node in manifest.nodes:
        paths[node.doc_path].append(node.id)
    for path, ids in sorted(paths.items()):
        if len(ids) > 1:
            errors.append(f"{path}: на один путь претендуют узлы {', '.join(sorted(ids))}")

    # На macOS и Windows файловая система регистронезависима, а имя модуля
    # в `doc_path` не слагифицируется. `docpipe validate` этого не ловит —
    # он сравнивает точные строки.
    lowered: defaultdict[str, set[str]] = defaultdict(set)
    for node in manifest.nodes:
        lowered[node.doc_path.lower()].add(node.doc_path)
    for _, variants in sorted(lowered.items()):
        if len(variants) > 1:
            errors.append(
                "пути различаются только регистром и на macOS/Windows совпадут: "
                + ", ".join(sorted(variants))
            )

    by_node: defaultdict[str, list[str]] = defaultdict(list)
    for doc in existing:
        if doc.node_id:
            by_node[doc.node_id].append(doc.path)
    for node_id, docs in sorted(by_node.items()):
        if len(docs) > 1:
            errors.append(f"{node_id}: один узел в нескольких файлах: {', '.join(sorted(docs))}")

    # Отказ остаётся только там, где подставить нечего: без `default.md`
    # молчаливой подстановки «ничего» быть не должно.
    missing = sorted(
        {
            node.template
            for node in manifest.nodes
            if resolve_template(node.template, templates) is None
        }
    )
    if missing:
        known = ", ".join(sorted(templates)) or "(шаблонов нет)"
        errors.append(
            f"нет шаблонов: {', '.join(missing)}; загружены: {known}."
            f" Заведите `{DEFAULT_TEMPLATE}.md` — он применяется, когда своего скелета нет"
        )

    return errors


def build_plan(
    manifest: Manifest,
    existing: list[ExistingDoc],
    templates: dict[str, Template],
    context: BuildContext,
    ownership: Ownership | None = None,
    options: PlanOptions | None = None,
) -> MaterializePlan:
    """Построить план. Ничего не пишет."""
    options = options or PlanOptions()
    errors = _blocking_errors(manifest, existing, templates)
    if errors:
        return MaterializePlan(errors=errors)

    teams = {
        node.id: (owner_of(node, ownership).team if ownership else None) for node in manifest.nodes
    }
    by_path = {doc.path: doc for doc in existing if doc.error is None}
    node_ids = {node.id for node in manifest.nodes}

    broken = {doc.path: doc for doc in existing if doc.error is not None}
    node_paths = {node.doc_path for node in manifest.nodes}

    documents: list[PlannedDoc] = []
    for path, doc in sorted(broken.items()):
        # Испорченный файл на пути узла обрабатывается вместе с узлом: иначе
        # узел окажется «без файла», получит `create` и молча затрёт документ,
        # прочитать который не удалось. Ровно то, что запрещено.
        if path not in node_paths:
            documents.append(
                PlannedDoc(
                    doc_path=path,
                    node_id=None,
                    file_action="refuse",
                    status="broken",
                    agent_action="review",
                    reason="структура документа испорчена, файл не тронут",
                    error=doc.error,
                )
            )

    # Кандидат на перенос — файл, который не стоит на `doc_path` НИ ОДНОГО узла.
    # Проверять «node_id нет в манифесте» недостаточно: при переклассификации
    # узел остаётся тем же, а путь меняется, и такой файл иначе не нашёлся бы.
    #
    # Множество строится по ПОЛНОМУ набору узлов, даже при `--team`: фильтр
    # сужает то, что пишется, множество сравнения — никогда.
    unplaced = [doc for doc in existing if doc.error is None and doc.path not in node_paths]
    homeless = [node for node in manifest.nodes if node.doc_path not in by_path]
    relocations, notes = match_relocations(homeless, unplaced)
    relocated_paths = {doc.path for doc, _ in relocations.values()}
    orphan_docs = [doc for doc in unplaced if doc.node_id not in node_ids]
    substituted: Counter[str] = Counter()

    for node in sorted(manifest.nodes, key=lambda item: item.doc_path):
        team = teams[node.id]
        if options.teams and team not in options.teams:
            continue

        # Считается по тому, что прогон действительно делает, а не по всему
        # манифесту: при `--team` чужие узлы не пишутся, и их подстановки
        # в отчёте этого прогона были бы шумом.
        resolved = resolve_template(node.template, templates)
        assert resolved is not None  # проверено в `_blocking_errors`
        if resolved != node.template:
            substituted[node.template] += 1
        template = templates[resolved]
        source = by_path.get(node.doc_path)
        moved = relocations.get(node.id)
        if source is None and moved is not None:
            source = moved[0]

        state = (source.parsed.state if source and source.parsed else None) if source else None
        if node.doc_path in options.accept:
            state = accepted_state(node)
        relocated_from = moved[0].path if moved else None
        if relocated_from and node.doc_path not in options.accept:
            # Отметка о пересмотре ставится здесь, а не при записи: содержимое
            # документа целиком считается планом, и `apply` остаётся тупым.
            state = {
                **(state or {"accepted": None}),
                "review": {"reason": "relocated", "from": relocated_from},
            }

        template_ref, example_ref = template_refs(node, context)
        damaged = broken.get(node.doc_path)
        text, empty, orphan_sections = _compose(
            node, template, context, team, None if damaged else source, state
        )
        status, agent_action, reason, changes = decide(node, source, empty, relocated_from)

        if damaged is not None:
            documents.append(
                PlannedDoc(
                    doc_path=node.doc_path,
                    node_id=node.id,
                    file_action="refuse",
                    status="broken",
                    agent_action="review",
                    reason="структура документа испорчена, файл не тронут",
                    # Содержимое собрано из шаблона: оно нужно только `--force`,
                    # который сохраняет исходник рядом и пересоздаёт документ.
                    content=text,
                    team=team,
                    template=node.template,
                    error=damaged.error,
                )
            )
            continue

        if source is None:
            file_action: FileAction = "create"
        elif moved is not None:
            file_action = "relocate"
        elif text == source.text:
            file_action = "unchanged"
        else:
            file_action = "update"

        documents.append(
            PlannedDoc(
                doc_path=node.doc_path,
                node_id=node.id,
                file_action=file_action,
                status=status,
                agent_action=agent_action,
                reason=reason,
                content=None if file_action == "unchanged" else text,
                relocate_from=relocated_from,
                confidence=moved[1] if moved else None,
                team=team,
                template=node.template,
                kind=node.kind,
                template_ref=template_ref,
                example_ref=example_ref,
                sources=list(node.symbol.sources) if node.symbol else [],
                empty_sections=empty,
                orphan_sections=orphan_sections,
                changes=changes,
            )
        )

    for doc in sorted(orphan_docs, key=lambda item: item.path):
        if doc.path in relocated_paths:
            continue
        documents.append(
            PlannedDoc(
                doc_path=doc.path,
                node_id=doc.node_id,
                file_action="unchanged",
                status="orphan",
                agent_action="review",
                reason="узла с таким id в манифесте нет",
                # У сироты узла нет, поэтому команда берётся из её собственного
                # front matter; иначе она была бы молча приписана текущей.
                team=doc.docpipe.get("team"),
            )
        )

    return MaterializePlan(
        documents=sorted(documents, key=lambda item: item.doc_path),
        errors=[],
        notes=notes,
        manifest_partial=manifest.partial is not None,
        # Явный ключ, а не порядок вставки: цифры идут в отчёт, а отчёты
        # сравнивают между прогонами.
        substituted=dict(sorted(substituted.items(), key=lambda item: (-item[1], item[0]))),
    )


_LINK = re.compile(r"\]\(([^)]+)\)")


def check_links(existing: list[ExistingDoc], root: Path) -> dict[str, list[str]]:
    """Относительные ссылки в **авторских** секциях, которые никуда не ведут.

    Ссылки в генерируемых блоках чинятся сами: блоки пересобираются в том же
    прогоне. Поставленные агентом внутри своих секций — не чинятся, поэтому
    о них надо сообщать.

    Внешние адреса и якоря не проверяются: они не про файловую систему.
    """
    broken: dict[str, list[str]] = {}
    for doc in existing:
        if doc.parsed is None:
            continue
        targets: list[str] = []
        for segment in doc.parsed.segments:
            if segment.kind != "section":
                continue
            for match in _LINK.finditer(segment.body):
                target = match.group(1).split("#", 1)[0].split(" ", 1)[0].strip()
                if not target or "://" in target or target.startswith(("/", "#", "mailto:")):
                    continue
                resolved = (root / doc.path).parent / target
                if not resolved.exists():
                    targets.append(target)
        if targets:
            broken[doc.path] = sorted(set(targets))
    return broken


def with_links(plan: MaterializePlan, broken: dict[str, list[str]]) -> MaterializePlan:
    """Приписать плану найденные битые ссылки. План остаётся чистой функцией:
    файловую систему трогает `check_links`, а не он."""
    if not broken:
        return plan
    return replace(
        plan,
        documents=[
            replace(doc, broken_links=broken[doc.doc_path]) if doc.doc_path in broken else doc
            for doc in plan.documents
        ],
    )


def relocation_note(plan: MaterializePlan) -> list[str]:
    """Строки про переносы — для отчёта."""
    return [
        f"{doc.relocate_from} → {doc.doc_path} ({doc.confidence})"
        for doc in plan.documents
        if doc.relocate_from
    ]


__all__ = [
    "DEFAULT_DOCS_SCAN_EXCLUDE",
    "check_links",
    "Accepted",
    "Changes",
    "accepted_state",
    "ExistingDoc",
    "MaterializePlan",
    "PlanOptions",
    "PlannedDoc",
    "STATUS_ORDER",
    "build_plan",
    "decide",
    "match_relocations",
    "relocation_note",
    "scan_docs",
    "public_members",
    "with_links",
]
