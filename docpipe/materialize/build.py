"""Сборка проекции манифеста: front matter и генерируемый блок.

Единственный модуль шага 2, знающий про устройство `DocNode`. Остальные —
разбор документа, шаблоны, план, запись — работают с зонами и файлами и от
языка исходников не зависят; на этом стоит бизнес-слой, у которого свой `build`.

Ни одной строки YAML не собирается вручную: `node_id` содержит обратную кавычку
и `#`, а решение «когда квотировать» знает дампер, а не человек.
"""

import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from docpipe.materialize.template import Template, resolve_template
from docpipe.model import DocNode, Manifest, SourceSpan
from docpipe.web.pages import DEFAULT_DEPTH as PAGE_DEPTH
from docpipe.web.pages import STATE_KIND, called, index_by_fqn, state_name

SCHEMA: Final[str] = "materialize/1"

_TYPE_KIND_TEXT: Final[dict[str, str]] = {
    "record_struct": "record struct",
}


class DocpipeFrontMatter(BaseModel):
    """Зона проекции. Порядок полей = порядок в файле, поэтому он значим.

    Правило отбора: поле попадает сюда, только если по нему кто-то **принимает
    решение** — скрипт или агент. Всё, что нужно только глазами, живёт
    в генерируемом блоке.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_id: str = Field(alias="schema", default=SCHEMA)
    node_id: str
    doc_path: str
    title: str
    fqn: str
    kind: str
    template: str
    template_ref: str
    example_ref: str | None
    module: str
    module_project_file: str
    domain: str
    team: str | None
    signature_hash: str
    impl_hash: str
    ruleset_version: str
    sources: list[SourceSpan] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedLink:
    """Цель ссылки: узел и то, как он найден."""

    node: DocNode
    via: Literal["direct", "implementation"]


@dataclass(frozen=True)
class BuildContext:
    """Всё, что нужно для сборки, посчитанное по манифесту один раз."""

    manifest: Manifest
    templates: dict[str, Template]
    examples: frozenset[str] = frozenset()
    templates_dir: str = "templates"
    by_fqn: dict[str, list[DocNode]] = field(default_factory=dict)
    implementors: dict[str, list[DocNode]] = field(default_factory=dict)
    module_refs: dict[str, set[str]] = field(default_factory=dict)

    # Обратный индекс «узел → бизнес-документы» приходит СЮДА КАК ДАННЫЕ.
    # `docpipe/materialize/**` не импортирует `docpipe/business/**`: иначе шаг 2
    # перестанет быть самостоятельным, а бизнес-слой — необязательным.
    #
    # `business_root` отличает «каталог не задан» от «каталог задан, но этот узел
    # в нём не упомянут». Без этого различия раздел «Бизнес-контекст» появлялся бы
    # у всех сразу при первом же бизнес-документе в репозитории.
    business_root: str | None = None
    business_links: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    # Имена модулей фронта. По ним генерируемый блок решает, печатать ли разделы
    # «Маршрут» и «Вызовы к бэкенду»: у узла .NET маршрута страницы нет и быть
    # не может, а пустой раздел «Нет.» в каждом документе бэкенда был бы шумом
    # ради симметрии, которой в предметной области нет.
    web_modules: frozenset[str] = frozenset()


def build_context(
    manifest: Manifest,
    templates: dict[str, Template],
    examples: frozenset[str] = frozenset(),
    templates_dir: str = "templates",
) -> BuildContext:
    """Построить индексы.

    `by_fqn` даёт списки, а не узлы: FQN не уникален — на ABP 255 коллизий
    на 9075 объявлений, — и словарь «FQN → узел» молча терял бы половину.

    Ссылки между модулями строятся по **идентификаторам**, а не по именам:
    имена проектов не уникальны (в ABP 39 повторов), и сужение по имени
    выбирало бы произвольный из одноимённых.
    """
    by_fqn: defaultdict[str, list[DocNode]] = defaultdict(list)
    implementors: defaultdict[str, list[DocNode]] = defaultdict(list)

    for node in manifest.nodes:
        if node.symbol is not None:
            by_fqn[node.symbol.fqn].append(node)
        for relation in node.related:
            if relation.relation == "implements":
                implementors[relation.target].append(node)

    web_modules = frozenset(module.name for module in manifest.modules if module.lang == "ts")
    id_by_csproj = {module.project_file: module.id for module in manifest.modules}
    module_refs = {
        module.id: {
            id_by_csproj[path] for path in module.project_references if path in id_by_csproj
        }
        for module in manifest.modules
    }

    def ordered(nodes: list[DocNode]) -> list[DocNode]:
        return sorted(nodes, key=lambda node: (node.doc_path, node.id))

    return BuildContext(
        manifest=manifest,
        templates=templates,
        examples=examples,
        templates_dir=templates_dir,
        by_fqn={fqn: ordered(nodes) for fqn, nodes in sorted(by_fqn.items())},
        implementors={fqn: ordered(nodes) for fqn, nodes in sorted(implementors.items())},
        module_refs=module_refs,
        web_modules=web_modules,
    )


# --------------------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------------------


def template_refs(node: DocNode, context: BuildContext) -> tuple[str, str | None]:
    """Пути к скелету и образцу. `example_ref` бывает `None`.

    Образцов четыре, а шаблонов семь: для Ignite их нет намеренно. Подставлять
    путь к несуществующему файлу нельзя — агент пошёл бы его читать.

    По той же причине здесь **применённый** скелет, а не запрошенный: узел
    с неизвестным `template` документируется базовым, и ссылка обязана вести
    на него. Образца у базового нет: показывать глубину и стиль на неизвестном
    виде сущности не на чем.
    """
    applied = resolve_template(node.template, context.templates) or node.template
    example = f"{context.templates_dir}/examples/{applied}.md"
    return (
        f"{context.templates_dir}/{applied}.md",
        example if applied in context.examples else None,
    )


def build_front_matter(
    node: DocNode, context: BuildContext, team: str | None
) -> DocpipeFrontMatter:
    """Проекция узла. Владение приходит снаружи: оно считается на шаге 2."""
    module = next(
        (m for m in context.manifest.modules if m.id == node.parent),
        None,
    )
    template_ref, example_ref = template_refs(node, context)

    return DocpipeFrontMatter(
        node_id=node.id,
        doc_path=node.doc_path,
        title=node.title,
        fqn=node.symbol.fqn if node.symbol else node.title,
        kind=node.kind,
        template=node.template,
        template_ref=template_ref,
        example_ref=example_ref,
        module=node.module,
        module_project_file=module.project_file if module else "",
        domain=node.domain,
        team=team,
        signature_hash=node.signature_hash,
        impl_hash=node.impl_hash,
        ruleset_version=context.manifest.ruleset_version,
        sources=list(node.symbol.sources) if node.symbol else [],
    )


def _dump(mapping: dict[str, Any]) -> str:
    """Единственный способ, которым в этом проекте появляется YAML.

    `width=10**9` — иначе PyYAML разрывает значение с пробелами длиннее
    80 символов на две строки, и текст файла становится зависимым от версии
    библиотеки. `allow_unicode=True` — иначе кириллица в `domain` и `team`
    превращается в `\\u0426…`.
    """
    return yaml.safe_dump(
        mapping,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10**9,
    )


def dump_front_matter(
    docpipe: DocpipeFrontMatter,
    state: dict[str, Any] | None,
    preserved: dict[str, Any] | None = None,
) -> str:
    """Собрать текст front matter.

    `sort_keys=False` действует на весь дамп, поэтому порядок задаётся порядком
    вставки: `docpipe` первым (порядок полей модели), `docpipe_state` вторым,
    чужие ключи — **отсортированными**. Последнее обязательно: их порядок —
    тот, в котором их вернул разбор, то есть тот, что человек мог поменять
    руками, и без сортировки перестановка двух строк вызывала бы перезапись.
    """
    mapping: dict[str, Any] = {"docpipe": docpipe.model_dump(mode="json", by_alias=True)}
    mapping["docpipe_state"] = state if state is not None else {"accepted": None, "review": None}
    for key in sorted(preserved or {}):
        mapping[key] = (preserved or {})[key]

    # Разделяющая пустая строка сюда НЕ входит: при слиянии она уже есть
    # в сохранённом теле документа, и вторая копилась бы при каждом прогоне.
    return f"---\n{_dump(mapping)}---\n"


# --------------------------------------------------------------------------------------
# Кросс-ссылки
# --------------------------------------------------------------------------------------


def resolve_link(target: str, node: DocNode, context: BuildContext) -> list[ResolvedLink]:
    """Найти узлы, на которые указывает FQN из `dependencies` или `related`.

    Шаг «реализации интерфейса» обязателен: в наборе правил по умолчанию
    интерфейсы не документируются (`type_kind: ["class", "record"]`), а
    зависимости почти всегда указывают именно на интерфейс. Без него таблица
    зависимостей у каждого сервиса состояла бы из строк «вне дерева».
    """
    candidates = [c for c in context.by_fqn.get(target, []) if c.id != node.id]

    if len(candidates) > 1:
        same_module = [c for c in candidates if c.parent == node.parent]
        referenced = [
            c for c in candidates if c.parent in context.module_refs.get(node.parent or "", set())
        ]
        # Показывать всех при неоднозначности, а не выбирать одного: выбор —
        # это ложь в документации, которая обнаружится нескоро.
        candidates = same_module or referenced or candidates

    if candidates:
        return [ResolvedLink(node=c, via="direct") for c in candidates]

    return [
        ResolvedLink(node=c, via="implementation")
        for c in context.implementors.get(target, [])
        if c.id != node.id
    ]


def _relative(doc_path: str, target: str) -> str:
    """Ссылка от каталога документа к цели.

    `posixpath.relpath`, а не `os.path.relpath`: последний на Windows даёт
    `..\\services\\…`, и дерево, собранное там, разошлось бы с собранным в CI.
    """
    return posixpath.relpath(target, posixpath.dirname(doc_path))


# --------------------------------------------------------------------------------------
# Генерируемый блок
# --------------------------------------------------------------------------------------


def _cell(value: str) -> str:
    """Значение в ячейке таблицы.

    `|` внутри значения режет ячейку и ломает таблицу целиком, поэтому
    экранируется — в том числе внутри обратных кавычек, где интуиция
    подсказывает обратное.
    """
    return "`" + value.replace("|", "\\|") + "`"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["Нет."]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def _link_text(links: list[ResolvedLink], node: DocNode, target: str) -> str:
    if not links:
        return "вне дерева документации"

    parts = []
    for link in sorted(links, key=lambda item: item.node.doc_path):
        href = _relative(node.doc_path, link.node.doc_path)
        note = " — реализация интерфейса" if link.via == "implementation" else ""
        module = f" ({link.node.module})" if len(links) > 1 else ""
        parts.append(f"[{link.node.title}]({href}){module}{note}")
    return ", ".join(parts)


def _page_sections(node: DocNode, context: BuildContext) -> list[str]:
    """Разделы агрегата: состояние, что зовёт страница, состав документа.

    Документ страницы — единица документации: сервис, до которого дотягивается
    только она, описывается здесь же, а не отдельным файлом. Общий сервис
    остаётся отдельным документом и попадает сюда **ссылкой**: копия текста
    разошлась бы с оригиналом на первой правке — то же правило, по которому
    бизнес-слой ссылается на технический, а не пересказывает его.
    """
    by_fqn = index_by_fqn(context.manifest)
    walk = called(node, by_fqn, PAGE_DEPTH)
    calls, participants = walk.calls, walk.participants

    reached = [by_fqn[fqn] for fqn in sorted(participants) if fqn in by_fqn]
    absorbed = [item for item in reached if item.absorbed_by == node.id]
    shared = [item for item in reached if item.absorbed_by != node.id]
    states = [item for item in reached if item.kind == STATE_KIND]

    lines = ["", "### Дочерние страницы", ""]
    children = _children(node, context)
    lines += [f"- [`/{path}`]({_relative(node.doc_path, doc)})" for path, doc in children] or [
        "Нет."
    ]

    lines += ["", "### Состояние", ""]
    lines += _table(
        ["Стейт", "Имя", "Члены", "Где описан"],
        [
            [
                _cell(item.title),
                _cell(state_name(item)),
                _cell(", ".join(_members_called(item, node, by_fqn))),
                "в этом документе"
                if item.absorbed_by == node.id
                else _link_text(
                    resolve_link(item.symbol.fqn if item.symbol else item.title, node, context),
                    node,
                    item.title,
                ),
            ]
            for item in states
        ],
    )

    lines += ["", "### Что зовёт страница", ""]
    lines += _table(
        ["Узел", "Вид", "Члены", "Где описан"],
        [
            [
                _cell(item.title),
                item.kind,
                _cell(", ".join(_members_called(item, node, by_fqn))),
                "в этом документе"
                if item.absorbed_by == node.id
                else _link_text(
                    resolve_link(item.symbol.fqn if item.symbol else item.title, node, context),
                    node,
                    item.title,
                ),
            ]
            for item in reached
        ],
    )

    lines += ["", "### Данные", ""]
    lines += _table(
        ["Метод", "Маршрут", "Список", "Через", "Экшен"],
        [
            [
                _cell(call.http_method),
                _cell(call.route),
                _cell(call.discriminator),
                _cell(call.through),
                _cell(call.action),
            ]
            for call in calls
        ],
    )

    lines += ["", "### Состав документа", ""]
    lines += [
        f"- `{item.title}` ({item.kind}) — своего документа нет, описывается здесь"
        for item in absorbed
    ] or ["Нет."]
    if shared:
        lines += [
            "",
            "Общие узлы, у которых свой документ (их зовёт не только эта страница): "
            + ", ".join(f"`{item.title}`" for item in shared)
            + ".",
        ]
    return lines


def _members_called(target: DocNode, page: DocNode, by_fqn: dict[str, DocNode]) -> list[str]:
    """Члены цели, которые зовут на пути от страницы."""
    found: set[str] = set()
    fqn = target.symbol.fqn if target.symbol else ""
    for item in [page, *by_fqn.values()]:
        for usage in item.uses:
            if usage.target == fqn:
                found.add(usage.member)
    return sorted(found)


def _children(node: DocNode, context: BuildContext) -> list[tuple[str, str]]:
    """Страницы, лежащие под маршрутом этой: пара «маршрут, документ»."""
    parents = [
        entry.path.split("/") if entry.path else []
        for entry in node.routes
        if not entry.route_unresolved
    ]
    found: list[tuple[str, str]] = []
    for other in context.manifest.nodes:
        if other.id == node.id or other.kind != "page":
            continue
        for entry in other.routes:
            if entry.route_unresolved:
                continue
            child = entry.path.split("/") if entry.path else []
            if any(
                len(child) > len(parent) and child[: len(parent)] == parent for parent in parents
            ):
                found.append((entry.path, other.doc_path))
                break
    return sorted(found)


def _web_sections(node: DocNode) -> list[str]:
    """Разделы, которые есть только у фронта: маршрут страницы и вызовы к бэкенду.

    Печатаются у узлов модулей с `lang: ts` и только у них. Симметрии
    с бэкендом здесь нет и быть не может: у контроллера нет маршрута страницы,
    и пустой раздел «Нет.» в каждом документе .NET был бы шумом ради формы.

    Невосстановленный маршрут печатается словами, а не пропускается: пропуск
    неотличим от «страницы нет», и покрытие посчиталось бы по заниженному
    знаменателю.
    """
    lines = ["", "### Маршрут страницы", ""]
    lines += _table(
        ["Маршрут", "Состояние"],
        [
            [
                _cell(f"/{entry.path}" if entry.path else "/"),
                "маршрут собрать не удалось" if entry.route_unresolved else "собран",
            ]
            for entry in node.routes
        ],
    )

    lines += ["", "### Вызовы к бэкенду", ""]
    lines += _table(
        ["Метод", "Маршрут", "Список", "Уверенность", "Строка"],
        [
            [
                _cell(call.key.http_method),
                _cell(call.key.route),
                _cell(call.key.discriminator),
                call.confidence,
                str(call.line),
            ]
            for call in node.web_calls
        ],
    )
    return lines


def build_generated_block(
    node: DocNode,
    context: BuildContext,
    team: str | None = None,
    notes: list[str] | None = None,
) -> str:
    """Собрать содержимое генерируемого блока.

    Порядок разделов фиксирован, и раздел без данных печатается со словом
    «Нет.», а не пропускается: пропуск означал бы, что появление первой
    зависимости меняет и структурные строки документа.
    """
    symbol = node.symbol
    lines: list[str] = []

    kind_text = _TYPE_KIND_TEXT.get(symbol.type_kind, symbol.type_kind) if symbol else node.kind
    modifiers = " ".join(symbol.modifiers) if symbol and symbol.modifiers else ""
    shape = f"{modifiers} {kind_text}".strip()
    owner = f"владелец `{team}`" if team else "владелец не задан"
    fqn = symbol.fqn if symbol else node.title
    lines += [f"`{fqn}` — {shape}, модуль `{node.module}`, домен `{node.domain}`, {owner}.", ""]

    # Замечания живут в генерируемом блоке намеренно: они исчезнут сами, как
    # только исчезнет их причина. В авторской секции их пришлось бы убирать руками.
    lines += [*(notes or []), ""] if notes else []
    lines += ["### Исходники", ""]

    if symbol and symbol.sources:
        lines += [
            f"- [`{source.path}`]({_relative(node.doc_path, source.path)})"
            f" — строки {source.start}–{source.end}"
            for source in symbol.sources
        ]
    else:
        lines.append("Нет.")

    lines += ["", "### HTTP-эндпоинты", ""]
    lines += _table(
        ["Метод", "Маршрут", "Член", "Строка"],
        [
            [_cell(e.http_method), _cell(e.route), _cell(e.member), str(e.line)]
            for e in node.endpoints
        ],
    )

    if node.module in context.web_modules:
        lines += _web_sections(node)
        if node.kind == "page":
            lines += _page_sections(node, context)

    lines += ["", "### Зависимости", ""]
    lines += _table(
        ["Тип", "Через", "Документ"],
        [
            [
                _cell(d.target),
                d.via,
                _link_text(resolve_link(d.target, node, context), node, d.target),
            ]
            for d in node.dependencies
        ],
    )

    lines += ["", "### Связи", ""]
    lines += _table(
        ["Тип", "Связь", "Документ"],
        [
            [
                _cell(r.target),
                r.relation,
                _link_text(resolve_link(r.target, node, context), node, r.target),
            ]
            for r in node.related
        ],
    )

    lines += ["", "### XML-doc из кода", ""]
    if symbol and symbol.xml_doc:
        # Цитатой, а не в ячейку: перевод строки внутри ячейки ломает таблицу.
        lines += [f"> {line}" for line in symbol.xml_doc.splitlines()]
    else:
        lines.append("Нет.")

    # Раздел появляется, только когда бизнес-каталог задан. Без него шаг 2
    # обязан работать ровно как прежде — вплоть до байта в золотом дереве.
    if context.business_root is not None:
        lines += ["", "### Бизнес-контекст", ""]
        found = context.business_links.get(node.id, [])
        listed = [f"- [{title}]({_relative(node.doc_path, path)})" for title, path in found]
        lines += listed or ["Нет."]

    return "\n".join(lines) + "\n"
