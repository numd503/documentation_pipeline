"""«Что решено об этом коде»: `setup explain PATH` (S23).

Вход «расширение области»: человек называет код — файл, каталог или глоб, —
и агент показывает, входит ли он в область и **какие решения настройки
и с какими причинами** о нём уже приняты. Отвечает одна функция, и ответ
построен вокруг поля `decisions`: плоского списка всех записей настройки,
которые решили судьбу этого кода, — шаблон `exclude`, запись `enrolled`
или `not_enrolled`, правила отсева и классификации, выигравшие в этом коде,
записи `pages.yaml`, `url_rewrite` модуля, `registry_calls`, `di_methods`,
`dispatch_interfaces`, правила секции `link`, правила владения. По нему
агент знает, что править.

Три вещи держат ответ честным:

- **отсечённое обходом не доходит ни до чего.** Под `exclude` нет ни
  символов, ни страниц, ни документов, и пустые разделы читались бы как
  «здесь ничего нет». Поэтому такой ответ называет шаблон и причину,
  а остальные разделы оставляет пустыми намеренно; то же — у кода вне
  `roots` и `web.roots`;
- **встроенный отсев — не решение человека.** У `**/obj/**` причины нет,
  и запись подписана файлом «встроенный отсев», умолчание `enrolled` —
  «умолчание»: иначе они выглядели бы решениями, которых никто не принимал;
- **только прогоны в памяти** (`SetupContext`): манифест на диске собран
  прошлой настройкой.
"""

import os
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from docpipe.classify import Ruleset
from docpipe.config import Scope, scope_entry, scope_of
from docpipe.discovery import WEB_FIELDS, file_field, map_files_to_modules, matches_glob
from docpipe.emit import DEFAULT_EXCLUDE, dispatch_name
from docpipe.explain import (
    ANY,
    PageRef,
    Row,
    Selection,
    SymbolRow,
    build_symbols_report,
    is_glob,
    path_matches,
    select,
)
from docpipe.hashing import stable_json_dumps
from docpipe.materialize.ownership import owner_of
from docpipe.model import DocNode, Lang, Module, RouteEntry, Symbol
from docpipe.route import normalize_route, route_key
from docpipe.setup.candidates import DEFAULT_LIMIT, declined_calls
from docpipe.setup.context import InputError, SetupContext
from docpipe.stats import STATE_TITLES
from docpipe.step2 import Step2Error
from docpipe.web.absorb import FEATURE_KIND, PAGE_KIND
from docpipe.web.calls import builder_for, name_matches, wrapper_matches
from docpipe.web.link import LinkDecision, LinkReport

SCHEMA_VERSION: Final = "1.0"

# Подписи «файла» у решений, которых человек не принимал. Отдельные строки,
# а не путь к `docpipe.yaml`: агент, увидевший `**/obj/**` с файлом настройки,
# пошёл бы искать в нём запись, которой там нет.
BUILTIN_FILE: Final = "встроенный отсев"
DEFAULT_FILE: Final = "умолчание"

_SCOPE_TITLES: Final[dict[str, str]] = {
    "enrolled": "в области",
    "not_enrolled": "вне области (решено)",
    "undecided": "решения нет",
}


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DecisionRef(_Base):
    """Запись настройки, решившая судьбу кода: где лежит, что в ней и почему.

    `file` — файл, который правят («docpipe.yaml», «rules/rules.yaml»), либо
    «встроенный отсев» и «умолчание» — у решений, которых человек не принимал.
    `key` — ключ в нём (`exclude`, `dotnet.rules`, `add`), `value` — шаблон,
    `id` правила, модуль или маршрут. `effect` — что запись сделала с этим
    кодом («вид service», «команда core»): по одному `id` правила этого
    не видно. `count` — сколько единиц кода в `target` решено записью:
    файлов, модулей, символов, вызовов, страниц или узлов — по ключу.
    """

    file: str
    key: str
    value: str
    reason: str
    effect: str
    count: int


class ModuleScope(_Base):
    """Модуль, в который попадает код, и решение о его области.

    `by` — запись, решившая область; `None` — решения нет (`undecided`).
    У модулей фронта области в настройке нет — их вводит в обход `web.roots`,
    и `by` называет запись корней.
    """

    module: str
    project_file: str
    lang: Lang
    scope: Scope
    by: DecisionRef | None


class DocumentRef(_Base):
    """Документ узла из этого кода: путь, статус и что с файлом сделает прогон."""

    doc_path: str
    status: str
    file_action: str
    node: str


class Note(_Base):
    """То, что ответ обязан сказать словами, иначе пустой раздел читается как «ничего нет».

    `code` — стабильный, латиницей; `message` — для человека.
    """

    code: str
    message: str


class PathExplain(_Base):
    """Ответ `setup explain`.

    `matched_files` — исходники под `target` на диске, до отсева и корней.
    `pages` — страницы и разделы, в документ которых попадает этот код:
    свои узлы-страницы и те, что поглотили его узлы.
    `roots` и `web_roots` — записи `roots` и `web.roots`, накрывающие хотя бы
    один не отсеянный исходник своего шага; пустой список — обход сюда
    не заходит. `symbol_rows` и `documents` — первые `limit` строк, всего —
    `sum(symbols.values())` и `documents_total`.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    target: str
    matched_files: int
    excluded_by: DecisionRef | None
    roots: list[str]
    web_roots: list[str]
    modules: list[ModuleScope]
    symbols: dict[str, int]
    symbol_rows: list[SymbolRow]
    pages: list[PageRef]
    page_overrides: list[DecisionRef]
    calls: dict[str, int]
    unresolved_reasons: list[tuple[str, int]]
    endpoints: dict[str, int]
    documents: list[DocumentRef]
    documents_total: int
    owners: dict[str, int]
    decisions: list[DecisionRef]
    notes: list[Note]


# --------------------------------------------------------------------------------------
# Цель и файлы под ней
# --------------------------------------------------------------------------------------


def normalize_target(target: str) -> str:
    """Цель от `--root`: POSIX, без `./` и хвостового `/`. Пустая строка — весь репозиторий.

    Абсолютный путь и `..` — отказ: ответ про код вне корня строился бы
    по файлам, которых обход не видит никогда, и выглядел бы правдой.
    """
    raw = target.strip()
    if "\\" in raw:
        raise InputError(f"путь {target!r}: разделитель — только `/`")
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        raise InputError(f"путь {target!r} обязан быть относительным `--root`")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise InputError(f"путь {target!r}: выход за корень (`..`) запрещён")
    return "/".join(parts)


def _matcher(target: str) -> Callable[[str], bool]:
    """Совпадение пути с целью: то же, что у `symbols --path`, и «всё» у пустой."""
    if not target:
        return lambda path: True
    return lambda path: path_matches(path, target)


def _static_prefix(glob: str) -> str:
    """Каталог, с которого начинать обход для глоба: сегменты до первого со знаком глоба."""
    parts: list[str] = []
    for part in glob.split("/"):
        if is_glob(part):
            break
        parts.append(part)
    return "/".join(parts)


def _files_on_disk(root: Path, target: str) -> list[str]:
    """Исходники под целью на диске — **без** отсева и корней.

    Обход свой, а не `discover`: тот отбрасывает отсечённое, а ответ обязан
    назвать и его. Символические ссылки не разыменовываются — как у обхода.
    Исходник — то, что берёт обход (`file_field`), а не любой файл:
    `README.md` в каталоге не делает его кодом.
    """
    base = _static_prefix(target) if is_glob(target) else target
    start = root / base if base else root
    found: list[str] = []
    if start.is_file():
        if file_field(start.name) is not None:
            found.append(base)
    elif start.is_dir():
        for dirpath, _, filenames in os.walk(start, followlinks=False):
            relative = Path(dirpath).relative_to(root).as_posix()
            prefix = "" if relative == "." else f"{relative}/"
            found.extend(f"{prefix}{name}" for name in filenames if file_field(name) is not None)
    match = _matcher(target)
    return sorted(path for path in found if match(path))


def covering_root(path: str, entries: Iterable[str]) -> str | None:
    """Самая длинная запись корней, под которой лежит файл. `.` и пустая накрывают всё.

    Сравнение по границе каталога, как у `discovery.in_scope`: `backend`
    не накрывает `backend-legacy/…`. Её же зовёт `setup status` (S24) для
    охвата записей корней: «какая запись накрыла файл» у двух команд одно.
    """
    covers = [
        entry
        for entry in entries
        if entry in ("", ".") or path == entry or path.startswith(entry + "/")
    ]
    # Длиннейшая, при равной длине — первая в порядке записи (`max` берёт первую).
    return max(covers, key=len, default=None)


def shown_root(entry: str) -> str:
    """Запись корней так, как её пишут: пустая — `.`. Общая с `setup status` (S24)."""
    return entry or "."


# --------------------------------------------------------------------------------------
# Сборка ответа
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pattern:
    glob: str
    file: str
    reason: str


class _Decisions:
    """Решения с суммой охвата: одна запись настройки — одна строка `decisions`."""

    def __init__(self) -> None:
        self._found: dict[tuple[str, str, str], DecisionRef] = {}

    def add(self, ref: DecisionRef) -> None:
        key = (ref.file, ref.key, ref.value)
        known = self._found.get(key)
        self._found[key] = (
            ref if known is None else known.model_copy(update={"count": known.count + ref.count})
        )

    def extend(self, refs: Iterable[DecisionRef]) -> None:
        for ref in refs:
            self.add(ref)

    def sorted(self) -> list[DecisionRef]:
        return sorted(self._found.values(), key=lambda ref: (ref.file, ref.key, ref.value))


def _merged(refs: Iterable[DecisionRef]) -> list[DecisionRef]:
    decisions = _Decisions()
    decisions.extend(refs)
    return decisions.sorted()


def _in_target(symbol: Symbol | None, match: Callable[[str], bool]) -> bool:
    """Символ в цели, если в ней хотя бы один его файл — как у `path_glob`."""
    return symbol is not None and any(match(source.path) for source in symbol.sources)


def _rule_decisions(rows: list[Row], rules_file: Path, section: str) -> list[DecisionRef]:
    """Правила отсева и классификации, **выигравшие** в этом коде.

    По победам, а не по совпадениям: правило, совпавшее со всем и нигде
    не выигравшее, ничего здесь не решило (S07).
    """
    found: list[DecisionRef] = []
    for row in rows:
        decision = row.decision
        if decision.exclusion is not None:
            found.append(
                DecisionRef(
                    file=rules_file.as_posix(),
                    key=f"{section}.exclude",
                    value=decision.exclusion.id,
                    reason=decision.exclusion.reason,
                    effect="не документируем",
                    count=1,
                )
            )
        elif decision.winner_rule is not None:
            found.append(
                DecisionRef(
                    file=rules_file.as_posix(),
                    key=f"{section}.rules",
                    value=decision.winner_rule,
                    reason="",
                    effect=f"вид {decision.kind}",
                    count=1,
                )
            )
    return found


def _select(
    index: dict[str, Symbol],
    nodes: list[DocNode],
    ruleset: Ruleset,
    enrolled: set[str],
    target: str,
) -> list[Row]:
    return select(index, nodes, ruleset, enrolled, state=ANY, path=target).rows


@dataclass
class _Draft:
    """Разделы ответа по мере сборки: шаг 1 и шаг `web` дописывают каждый своё."""

    modules: list[ModuleScope]
    rows: list[Row]
    pages: dict[str, str]
    page_overrides: list[DecisionRef]
    calls: Counter[str]
    unresolved_reasons: Counter[str]
    endpoints: Counter[str]
    # Узлы, у которых есть свой документ, — по манифесту (шаг 1 или `web`).
    documented: dict[Lang, list[DocNode]]
    decisions: _Decisions
    notes: list[Note]
    # Отчёт связи — один на ответ: его зовут и сторона .NET, и сторона фронта,
    # а неудачу (`cached_property` её не запоминает) повторять незачем.
    link: LinkReport | None = None
    link_tried: bool = False


def _link_report(ctx: SetupContext, draft: _Draft) -> LinkReport | None:
    """Отчёт связи, если он собирается; не собрался — заметка, а не отказ ответа.

    Зовётся, только когда правило секции `link` совпало с кодом под целью:
    решило ли оно что-то, знает лишь сведение двух сторон (вызов, которому
    эндпоинт нашёлся, — связь, и правило ему не нужно). Вторая сторона
    может не собраться — отказ шага `web` из-за `pages.yaml` не должен
    ронять ответ о каталоге .NET, — и тогда ответ говорит об этом словами.
    """
    if not draft.link_tried:
        draft.link_tried = True
        try:
            draft.link = ctx.link
        except InputError as exc:
            draft.notes.append(
                Note(
                    code="link.unavailable",
                    message=(
                        "правила секции `link` касаются этого кода, но отчёт связи"
                        f" не собрался: {exc}"
                    ),
                )
            )
    return draft.link


def _link_ref(key: str, decision: LinkDecision, effect: str, config: str) -> DecisionRef:
    return DecisionRef(
        file=config,
        key=key,
        value=decision.rule,
        reason=decision.reason,
        effect=effect,
        count=1,
    )


def _document_effect(what: str, document: bool) -> str:
    return f"{what}; документировать: {'да' if document else 'нет'}"


def _dotnet(ctx: SetupContext, target: str, seen: list[str], draft: _Draft) -> None:
    """Что решено о коде .NET под целью: модули, символы, эндпоинты, DI, диспетчеризация."""
    scan = ctx.scan
    match = _matcher(target)
    settings = ctx.settings
    config = ctx.config_label
    modules = {module.project_file: module for module in scan.manifest.modules}

    sources = [path for path in seen if path.endswith(".cs")]
    owned = map_files_to_modules(sources, sorted(modules))
    if outside := len(sources) - len(owned):
        draft.notes.append(
            Note(
                code="dotnet.outside_projects",
                message=(
                    f"файлов .cs вне проектов: {outside} — ни один `.csproj` выше по дереву"
                    " их не объявляет, символов они не дают"
                ),
            )
        )
    touched = sorted({*owned.values(), *(path for path in seen if path in modules)})
    for project_file in touched:
        module = modules[project_file]
        scope = scope_of(project_file, settings)
        entry = scope_entry(project_file, settings)
        by: DecisionRef | None = None
        if scope == "not_enrolled" and entry is not None:
            by = DecisionRef(
                file=config,
                key="not_enrolled",
                value=entry.glob,
                reason=entry.reason,
                effect="модуль вне области",
                count=1,
            )
        elif scope == "enrolled" and entry is not None:
            explicit = settings.enrolled_is_explicit
            by = DecisionRef(
                file=config if explicit else DEFAULT_FILE,
                key="enrolled",
                value=entry.glob,
                reason=entry.reason if explicit else "",
                effect="модуль в области" if explicit else "модуль в области: `enrolled` не задан",
                count=1,
            )
        if by is not None:
            draft.decisions.add(by)
        draft.modules.append(
            ModuleScope(
                module=module.name, project_file=project_file, lang="cs", scope=scope, by=by
            )
        )

    enrolled = {module.project_file for module in scan.manifest.modules if module.enrolled}
    rows = _select(scan.index, scan.manifest.nodes, ctx.ruleset, enrolled, target)
    draft.rows.extend(rows)
    draft.decisions.extend(_rule_decisions(rows, ctx.rules_file, "dotnet"))

    nodes = [node for node in scan.manifest.nodes if _in_target(node.symbol, match)]
    draft.documented["cs"] = [node for node in nodes if not node.absorbed_by]
    draft.endpoints.update(
        "routed" if endpoint.route else "unrouted" for node in nodes for endpoint in node.endpoints
    )
    # Ключи с нулём: «эндпоинтов 0» — посчитано, а не «не смотрели».
    draft.endpoints.update({"routed": 0, "unrouted": 0})

    # `link.external_callers` — решение об эндпоинте без вызывающего. Есть ли
    # вызывающий, знает только сведение с фронтом, поэтому оно идёт, лишь
    # когда правило совпало хоть с одним эндпоинтом здесь.
    keys = [
        route_key(endpoint.http_method, endpoint.route)
        for node in nodes
        for endpoint in node.endpoints
        if endpoint.route
    ]
    if any(settings.link.caller_for(key.http_method, key.route) for key in keys):
        report = _link_report(ctx, draft)
        ids = {node.id for node in nodes}
        for item in report.external_callers if report is not None else []:
            if item.node in ids:
                draft.decisions.add(
                    _link_ref(
                        "link.external_callers",
                        item.decision,
                        _document_effect("эндпоинт зовут извне", item.document),
                        config,
                    )
                )

    wrappers = {wrapper.name: wrapper for wrapper in settings.di_method_entries}
    for path, call in scan.registration_calls:
        wrapper = wrappers.get(call.method)
        # Лямбда-форма без типа регистрацией не становится и с ключом (`dotnet/di.py`).
        if wrapper is None or not match(path) or not (call.type_args or call.typeof_args):
            continue
        draft.decisions.add(
            DecisionRef(
                file=config,
                key="di_methods",
                value=wrapper.name,
                reason=wrapper.reason,
                effect="вызов считается регистрацией DI",
                count=1,
            )
        )

    interfaces = settings.dispatch_interface_entries
    for handler in scan.manifest.dispatch_handlers:
        if not match(handler.file):
            continue
        for interface in interfaces:
            if dispatch_name(interface.name) == handler.interface:
                draft.decisions.add(
                    DecisionRef(
                        file=config,
                        key="dispatch_interfaces",
                        value=interface.name,
                        reason=interface.reason,
                        effect="тип — обработчик запроса по типу",
                        count=1,
                    )
                )


def _web_module_of(path: str, keys: Iterable[str]) -> str | None:
    """Модуль фронта файла: самая длинная граница, как у `web.modules.module_of`."""
    return covering_root(path, keys)


def _web(ctx: SetupContext, target: str, seen: list[str], draft: _Draft) -> None:
    """Что решено о коде фронта: модули, символы, страницы, вызовы и правила шва."""
    web = ctx.web
    match = _matcher(target)
    settings = ctx.settings
    config = ctx.config_label
    modules = {module.id.removeprefix("module:"): module for module in web.manifest.modules}

    roots_file = config if "roots" in settings.web.model_fields_set else DEFAULT_FILE
    root_entries = settings.web.root_entries
    # Модуль — по границе файла и по самому файлу проекта: `angular.json`
    # лежит над `sourceRoot` и границей модуля не накрыт.
    touched = {key for path in seen if (key := _web_module_of(path, modules))}
    touched |= {key for key, module in modules.items() if module.project_file in seen}
    for key in sorted(touched):
        module = modules[key]
        entry = covering_root(module.project_file, [item.path for item in root_entries])
        by = None
        if entry is not None:
            reason = next((item.reason for item in root_entries if item.path == entry), "")
            by = DecisionRef(
                file=roots_file,
                key="web.roots",
                value=shown_root(entry),
                reason=reason,
                effect="фронт в обходе",
                count=1,
            )
        draft.modules.append(
            ModuleScope(
                module=module.name,
                project_file=module.project_file,
                lang="ts",
                scope="enrolled",
                by=by,
            )
        )

    enrolled = {key for key, module in modules.items() if module.enrolled}
    rows = _select(web.index, web.manifest.nodes, ctx.web_ruleset, enrolled, target)
    draft.rows.extend(rows)
    draft.decisions.extend(_rule_decisions(rows, ctx.web_rules_file, "web"))

    nodes = [node for node in web.manifest.nodes if _in_target(node.symbol, match)]
    pages = {node.id for node in nodes if node.kind in (PAGE_KIND, FEATURE_KIND)}
    pages |= {node.absorbed_by for node in nodes if node.absorbed_by}
    by_id = {node.id: node for node in web.manifest.nodes}
    # Страница, которой в манифесте нет, подписывается своим `id`, как
    # в `absorbed_page_refs`: пустой заголовок читался бы как «страницы нет».
    draft.pages.update({page: by_id[page].title if page in by_id else page for page in pages})
    own = [node for node in nodes if not node.absorbed_by and node.id not in pages]
    draft.documented["ts"] = sorted(
        [*own, *(by_id[page] for page in pages if page in by_id)], key=lambda node: node.id
    )

    _page_overrides(ctx, nodes, match, draft)
    _calls(ctx, match, modules, draft)


def _page_overrides(
    ctx: SetupContext, nodes: list[DocNode], match: Callable[[str], bool], draft: _Draft
) -> None:
    """Записи `pages.yaml`, которые касаются кода под целью."""
    if ctx.pages_file is None:
        return
    web, overrides, file = ctx.web, ctx.overrides, ctx.pages_file.as_posix()
    by_fqn = {symbol.fqn: symbol for symbol in web.index.values()}

    def inside(fqn: str) -> bool:
        return _in_target(by_fqn.get(fqn), match)

    found: list[DecisionRef] = []
    for rule in overrides.add:
        if inside(rule.component):
            found.append(
                DecisionRef(
                    file=file,
                    key="add",
                    value=rule.component,
                    reason=rule.reason,
                    effect=f"страница /{rule.normalized}",
                    count=1,
                )
            )

    # Снятие по маршруту ложится на компоненты этого маршрута — из таблицы
    # роутов и из добавлений, как у `apply_overrides`.
    entries = [
        *web.routes.entries,
        *(RouteEntry(path=rule.normalized, component=rule.component) for rule in overrides.add),
    ]
    for removal in overrides.remove:
        if removal.component:
            hits = {removal.component} if inside(removal.component) else set()
        else:
            hits = {
                entry.component
                for entry in entries
                if entry.path == removal.normalized
                and not entry.route_unresolved
                and inside(entry.component)
            }
        if hits:
            found.append(
                DecisionRef(
                    file=file,
                    key="remove",
                    value=removal.component or f"/{removal.normalized}",
                    reason=removal.reason,
                    effect="страница снята: класс документируется как компонент",
                    count=len(hits),
                )
            )

    for feature in overrides.features:
        inner = [
            node
            for node in nodes
            if node.symbol
            and node.symbol.sources
            and node.symbol.sources[0].path.startswith(feature.prefix)
            and node.kind != PAGE_KIND
        ]
        if inner:
            found.append(
                DecisionRef(
                    file=file,
                    key="features",
                    value=feature.name,
                    reason=feature.reason,
                    effect=f"раздел «{feature.heading}»: узлы каталога {feature.path}",
                    count=len(inner),
                )
            )

    draft.page_overrides.extend(found)
    draft.decisions.extend(found)


def _calls(
    ctx: SetupContext,
    match: Callable[[str], bool],
    modules: dict[str, Module],
    draft: _Draft,
) -> None:
    """Вызовы фронта под целью и правила шва, которые к ним применились.

    Правило `url_rewrite` берётся по модулю файла вызова, как у прогона:
    у семи фронтов репозитория семь разных `pathRewrite`.
    """
    web, settings, config = ctx.web, ctx.settings, ctx.config_label

    resolved = [call for call in web.calls.calls if match(call.file)]
    unresolved = [call for call in web.calls.unresolved if match(call.file)]
    draft.calls.update(
        {
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "registry_unresolved": sum(
                1 for call in web.calls.registry_unresolved if match(call.file)
            ),
        }
    )
    draft.unresolved_reasons.update(call.reason for call in unresolved)

    # Правило модуля — у каждого вызова, и у невосстановленного тоже (S35): тот же
    # счёт, что у охвата `setup status` (`_cover_calls`), и та же граница, что
    # у находки `link.module_without_rewrite` — модуль с любыми вызовами.
    without_rewrite: Counter[str] = Counter()
    for file in [*(call.file for call in resolved), *(call.file for call in unresolved)]:
        key = _web_module_of(file, modules)
        name = modules[key].name if key is not None else ""
        rule = settings.web.rewrite_for(name) if name else None
        if rule is not None:
            draft.decisions.add(
                DecisionRef(
                    file=config,
                    key="web.url_rewrite",
                    value=rule.module,
                    reason=rule.reason,
                    effect=f"strip_prefix {rule.strip_prefix!r}, add_prefix {rule.add_prefix!r}",
                    count=1,
                )
            )
        elif name:
            without_rewrite[name] += 1

    registry = [(normalize_route(rule.route), rule) for rule in settings.web.registry_calls]
    for call in resolved:
        for route, registry_rule in registry:
            if route == call.key.route:
                where = registry_rule.discriminator.where
                draft.decisions.add(
                    DecisionRef(
                        file=config,
                        key="web.registry_calls",
                        value=registry_rule.route,
                        reason=registry_rule.reason,
                        effect=f"различитель {where}.{registry_rule.discriminator.name}",
                        count=1,
                    )
                )

    _wrapper_decisions(ctx, match, draft)

    # `link.unresolvable` решает сам по себе: невосстановленный вызов связи
    # не имеет, и сведение со стороной .NET ему не нужно.
    for item in web.manifest.unresolved_calls:
        found = settings.link.unresolvable_for(item.file) if match(item.file) else None
        if found is not None:
            index, entry = found
            draft.decisions.add(
                _link_ref(
                    "link.unresolvable",
                    LinkDecision(index=index, rule=entry.label, reason=entry.reason),
                    "вызов невосстановим: в `declared_unresolvable`",
                    config,
                )
            )

    # `link.external_targets` — решение о вызове **без эндпоинта**: совпавшее
    # правило у связанного вызова ничего не решило, и отличить их может
    # только сведение. Без совпадения сведение не идёт — и шаг 1 тоже.
    if any(settings.link.target_for(call.host, call.key.route) for call in resolved):
        report = _link_report(ctx, draft)
        for external in report.external_targets if report is not None else []:
            if match(external.file):
                draft.decisions.add(
                    _link_ref(
                        "link.external_targets",
                        external.decision,
                        _document_effect("вызов во внешний адрес", external.document),
                        config,
                    )
                )

    for name, count in sorted(without_rewrite.items()):
        draft.notes.append(
            Note(
                code="link.module_without_rewrite",
                message=(
                    f"модуль фронта {name}: вызовов {count}, записи в `web.url_rewrite` нет —"
                    " маршруты сопоставляются как написаны; пустая запись значит «проверено,"
                    " преобразования нет»"
                ),
            )
        )


def _wrapper_decisions(ctx: SetupContext, match: Callable[[str], bool], draft: _Draft) -> None:
    """Записи `web.http_wrappers` и `web.url_builders`, через которые прошли вызовы под целью.

    Запись находится тем же сравнением, что у прогона (`wrapper_matches`,
    `builder_for`, `name_matches` для тела): «какая запись сделала этот
    вызов» и «к чему запись применилась» обязаны отвечать одинаково.
    Тело обёртки — вызов, которого нет ни в восстановленных, ни
    в невосстановленных; без счётчика сумма под целью молча меньше.
    Строка на запись одна (`_Decisions` сводит по записи), поэтому вызовы
    через обёртку и её тела посчитаны в одной строке раздельно.

    «Не обёртка» (`web.not_wrappers`) — вызовы-кандидаты под целью, которые
    запись сняла, тем же `declined_calls`, что у отчёта кандидатов и охвата.
    """
    web, settings, config = ctx.web, ctx.settings, ctx.config_label
    wrappers, builders = settings.web.http_wrappers, settings.web.url_builders
    declined = settings.web.not_wrappers
    if declined:
        taken: Counter[int] = Counter(
            index
            for index, call in declined_calls(web.candidate_calls, web.builder_uses, declined)
            if match(call.file)
        )
        for index, refusal in enumerate(declined):
            if taken[index]:
                draft.decisions.add(
                    DecisionRef(
                        file=config,
                        key="web.not_wrappers",
                        value=refusal.label,
                        reason=refusal.reason,
                        effect=(
                            f"не вызов HTTP: из кандидатов в обёртки снято вызовов {taken[index]}"
                        ),
                        count=taken[index],
                    )
                )
    if not wrappers and not builders:
        return

    through: Counter[int] = Counter()
    bodies: Counter[int] = Counter()
    built: Counter[int] = Counter()
    raws = [
        *(item.raw for item in web.calls.resolved if match(item.raw.file)),
        *(item for item in web.calls.unresolved if match(item.file)),
    ]
    for raw in raws:
        receiver, _, method = raw.wrapper.rpartition(".")
        for index, rule in enumerate(wrappers):
            if raw.wrapper and wrapper_matches(rule, receiver, method):
                through[index] += 1
        callee = raw.address.callee if raw.address is not None else None
        builder = builder_for(callee, builders) if raw.builder and callee is not None else None
        if builder is not None:
            built[builders.index(builder)] += 1

    inside = [raw for raw in web.calls.inside_wrappers if match(raw.file)]
    if inside:
        draft.calls["inside_wrappers"] = len(inside)
    for raw in inside:
        parameter = raw.address.parameter if raw.address is not None else None
        for index, rule in enumerate(wrappers):
            if parameter is not None and name_matches(rule, parameter.function):
                bodies[index] += 1

    for index, rule in enumerate(wrappers):
        if through[index] or bodies[index]:
            draft.decisions.add(
                DecisionRef(
                    file=config,
                    key="web.http_wrappers",
                    value=rule.label,
                    reason=rule.reason,
                    effect=(
                        f"вызовов через обёртку {through[index]}, тел обёртки"
                        f" {bodies[index]}; адрес — аргумент {rule.url.label}"
                    ),
                    count=through[index] + bodies[index],
                )
            )
    for index, builder in enumerate(builders):
        if built[index]:
            draft.decisions.add(
                DecisionRef(
                    file=config,
                    key="web.url_builders",
                    value=builder.label,
                    reason=builder.reason,
                    effect=f"адрес от построителя: путь — аргумент {builder.path.label}",
                    count=built[index],
                )
            )


def _documents(ctx: SetupContext, draft: _Draft) -> list[DocumentRef]:
    """Документы узлов из этого кода — по плану шага 2 в памяти.

    План шага 2 может не собраться (нет скелетов, битый `ownership.yaml`):
    это заметка с причиной, а не отказ команды — остальной ответ агенту
    нужен и тогда.
    """
    found: list[DocumentRef] = []
    for lang, nodes in sorted(draft.documented.items()):
        ids = {node.id for node in nodes}
        if not ids:
            continue
        try:
            planned = (ctx.plan if lang == "cs" else ctx.web_plan).plan
        except Step2Error as exc:
            step = "шага 1" if lang == "cs" else "фронта"
            draft.notes.append(
                Note(
                    code="documents.unavailable",
                    message=f"план шага 2 по манифесту {step} не собрался: {exc.message}",
                )
            )
            continue
        found.extend(
            DocumentRef(
                doc_path=doc.doc_path,
                status=doc.status,
                file_action=doc.file_action,
                node=doc.node_id or "",
            )
            for doc in planned.documents
            if doc.node_id in ids
        )
    return sorted(found, key=lambda doc: (doc.doc_path, doc.node))


def _owners(ctx: SetupContext, draft: _Draft) -> dict[str, int]:
    """Команды узлов, у которых есть документ: команда → узлов, `""` — без владельца."""
    nodes = sorted(
        (node for nodes in draft.documented.values() for node in nodes),
        key=lambda node: node.id,
    )
    if not nodes:
        return {}
    try:
        ownership = ctx.ownership
    except Step2Error as exc:
        draft.notes.append(
            Note(code="owners.unavailable", message=f"правила владения не читаются: {exc.message}")
        )
        return {}
    if ownership is None or ctx.ownership_file is None:
        draft.notes.append(
            Note(
                code="owners.not_configured",
                message="ключ `ownership` не задан: владельцев не назначает никто",
            )
        )
        return {}

    owners: Counter[str] = Counter()
    for node in nodes:
        decision = owner_of(node, ownership)
        owners[decision.team or ""] += 1
        if decision.winner is not None:
            draft.decisions.add(
                DecisionRef(
                    file=ctx.ownership_file.as_posix(),
                    key="rules",
                    value=decision.winner.id,
                    reason="",
                    effect=f"команда {decision.team}",
                    count=1,
                )
            )
    return dict(sorted(owners.items()))


def _exclusions(
    ctx: SetupContext, files: list[str]
) -> tuple[list[str], list[DecisionRef], DecisionRef | None]:
    """Отсев обходом: что осталось, какие шаблоны что отсекли и накрыта ли цель целиком.

    Шаблоны — встроенные (`DEFAULT_EXCLUDE`) и `exclude` настройки, каждый
    со своим счётом: правка одного из двух, совпавших с файлом, файл не вернёт,
    и показать надо оба. Цель накрыта целиком, когда отсечён каждый исходник;
    тогда её называет шаблон, накрывший больше всего файлов, при равенстве —
    встроенный раньше настройки, в порядке записи.
    """
    listed = [_Pattern(glob, BUILTIN_FILE, "") for glob in DEFAULT_EXCLUDE] + [
        _Pattern(entry.glob, ctx.config_label, entry.reason)
        for entry in ctx.settings.exclude_entries
    ]
    # Повтор шаблона в одном файле — одна запись: иначе файл посчитался бы
    # дважды, и охват записи вышел бы больше числа файлов под целью.
    patterns = [
        pattern
        for index, pattern in enumerate(listed)
        if all((pattern.file, pattern.glob) != (other.file, other.glob) for other in listed[:index])
    ]
    hits: Counter[int] = Counter()
    kept: list[str] = []
    for path in files:
        matched = [
            index for index, pattern in enumerate(patterns) if matches_glob(path, pattern.glob)
        ]
        hits.update(matched)
        if not matched:
            kept.append(path)

    refs = {
        index: DecisionRef(
            file=patterns[index].file,
            key="exclude",
            value=patterns[index].glob,
            reason=patterns[index].reason,
            effect="обход не читает файл",
            count=count,
        )
        for index, count in hits.items()
    }
    whole = None
    if files and not kept:
        whole = refs[min(hits, key=lambda index: (-hits[index], index))]
    return kept, _merged(refs.values()), whole


def explain_path(ctx: SetupContext, target: str, *, limit: int = DEFAULT_LIMIT) -> PathExplain:
    """Что решено о коде под `target` — файлом, каталогом или глобом от `--root`.

    Прогоны берутся из контекста и только нужные: под целью нет исходников
    .NET — шаг 1 не идёт, нет исходников фронта — не идёт шаг `web`. Цель
    целиком под отсевом — ответ без прогонов вовсе.

    `limit` — сколько строк символов и документов показать; `0` — все.
    """
    if limit < 0:
        raise InputError("limit не бывает отрицательным")
    target = normalize_target(target)
    settings = ctx.settings

    files = _files_on_disk(ctx.root, target)
    kept, exclusions, excluded_by = _exclusions(ctx, files)
    decisions = _Decisions()
    decisions.extend(exclusions)

    if excluded_by is not None:
        # Дальше обход не доходит, и пустые разделы ниже — ответ, а не
        # «здесь ничего нет»: об этом обязана сказать заметка.
        reason = f" ({excluded_by.reason})" if excluded_by.reason else ""
        note = Note(
            code="path.excluded",
            message=(
                f"отсечено шаблоном «{excluded_by.value}» — {excluded_by.file}{reason}:"
                " обход сюда не заходит, символов, страниц и документов нет"
            ),
        )
        return _empty(target, len(files), excluded_by, decisions.sorted(), [note])

    seen: dict[str, list[str]] = {"roots": [], "web.roots": []}
    covering: Counter[tuple[str, str]] = Counter()
    entries = {"roots": settings.roots, "web.roots": settings.web.root_paths}
    outside = 0
    for path in kept:
        key = "web.roots" if file_field(path.rsplit("/", 1)[-1]) in WEB_FIELDS else "roots"
        entry = covering_root(path, entries[key])
        if entry is None:
            outside += 1
            continue
        covering[(key, entry)] += 1
        seen[key].append(path)

    notes: list[Note] = []
    if not files:
        notes.append(
            Note(
                code="path.empty",
                message=(
                    "под целью нет исходников (.cs, .csproj, .sln, .sql, .ts, .html,"
                    " файлов проектов фронта)"
                ),
            )
        )
    if outside:
        notes.append(
            Note(
                code="path.outside_roots",
                message=(
                    f"исходников вне `roots` и `web.roots`: {outside} — обход их не читает,"
                    " решений о них нет"
                ),
            )
        )

    # Корни — решение, только когда ключ задан: умолчание `.` ничего не сужает.
    web_reasons = {item.path: item.reason for item in settings.web.root_entries}
    explicit = {
        "roots": "roots" in settings.model_fields_set,
        "web.roots": "roots" in settings.web.model_fields_set,
    }
    for (key, entry), count in sorted(covering.items()):
        if explicit[key]:
            decisions.add(
                DecisionRef(
                    file=ctx.config_label,
                    key=key,
                    value=shown_root(entry),
                    reason=web_reasons.get(entry, "") if key == "web.roots" else "",
                    effect="обход читает файл",
                    count=count,
                )
            )

    draft = _Draft(
        modules=[],
        rows=[],
        pages={},
        page_overrides=[],
        calls=Counter(),
        unresolved_reasons=Counter(),
        endpoints=Counter(),
        documented={},
        decisions=decisions,
        notes=notes,
    )
    if any(path.endswith((".cs", ".csproj")) for path in seen["roots"]):
        _dotnet(ctx, target, seen["roots"], draft)
    if seen["web.roots"]:
        _web(ctx, target, seen["web.roots"], draft)

    documents = _documents(ctx, draft)
    owners = _owners(ctx, draft)

    rows = sorted(draft.rows, key=lambda row: (row.symbol.fqn, row.symbol.module))
    shown = rows[:limit] if limit else rows
    symbols = build_symbols_report(Selection(rows=shown, total=len(rows), description=""))
    return PathExplain(
        target=target,
        matched_files=len(files),
        excluded_by=None,
        roots=sorted({shown_root(entry) for key, entry in covering if key == "roots"}),
        web_roots=sorted({shown_root(entry) for key, entry in covering if key == "web.roots"}),
        modules=sorted(draft.modules, key=lambda item: (item.lang, item.project_file)),
        symbols=dict(sorted(Counter(row.decision.state for row in rows).items())),
        symbol_rows=symbols.symbols,
        pages=[PageRef(id=page, title=title) for page, title in sorted(draft.pages.items())],
        page_overrides=_merged(draft.page_overrides),
        calls=dict(sorted(draft.calls.items())),
        unresolved_reasons=sorted(
            draft.unresolved_reasons.items(), key=lambda item: (-item[1], item[0])
        ),
        endpoints=dict(sorted(draft.endpoints.items())),
        documents=documents[:limit] if limit else documents,
        documents_total=len(documents),
        owners=owners,
        decisions=draft.decisions.sorted(),
        notes=sorted(draft.notes, key=lambda note: (note.code, note.message)),
    )


def _empty(
    target: str,
    matched_files: int,
    excluded_by: DecisionRef,
    decisions: list[DecisionRef],
    notes: list[Note],
) -> PathExplain:
    """Ответ о цели, отсечённой обходом целиком: кроме отсева, разделы пусты намеренно."""
    return PathExplain(
        target=target,
        matched_files=matched_files,
        excluded_by=excluded_by,
        roots=[],
        web_roots=[],
        modules=[],
        symbols={},
        symbol_rows=[],
        pages=[],
        page_overrides=[],
        calls={},
        unresolved_reasons=[],
        endpoints={},
        documents=[],
        documents_total=0,
        owners={},
        decisions=decisions,
        notes=notes,
    )


# --------------------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------------------


def explain_json(report: PathExplain) -> str:
    return stable_json_dumps(report.model_dump(mode="json"))


def _ref(ref: DecisionRef) -> str:
    reason = f" — «{ref.reason}»" if ref.reason else ""
    return f"{ref.file} · {ref.key} · {ref.value}: {ref.effect} ({ref.count}){reason}"


def _by(ref: DecisionRef) -> str:
    """Кто решил — коротко, для строки модуля и отсева."""
    reason = f", «{ref.reason}»" if ref.reason else ""
    return f"{ref.key}: {ref.value} ({ref.file}{reason})"


def _counts(values: dict[str, int], titles: dict[str, str]) -> str:
    """Счётчики в порядке подписей, затем остальные по ключу: порядок текста не алфавитный."""
    order = [key for key in titles if key in values] + sorted(set(values) - set(titles))
    return ", ".join(f"{titles.get(key, key)} {values[key]}" for key in order)


_CALL_TITLES: Final = {
    "resolved": "восстановлено",
    "unresolved": "не восстановлено",
    "registry_unresolved": "к реестру без различителя",
    "inside_wrappers": "в телах обёрток",
}
_ENDPOINT_TITLES: Final = {"routed": "с маршрутом", "unrouted": "без маршрута"}


def format_explain(report: PathExplain) -> str:
    """Текст для человека: сначала «доходит ли обход», потом что решено и кем."""
    lines = [f"Что решено о {report.target or '.'}: исходников {report.matched_files}."]
    steps = [
        f"{step} — {', '.join(f'«{entry}»' for entry in entries)}"
        for step, entries in (("шаг 1", report.roots), ("шаг web", report.web_roots))
        if entries
    ]
    if report.excluded_by is not None:
        lines.append(f"Отсечено обходом: {_by(report.excluded_by)}.")
    elif steps:
        lines.append(f"Корни обхода: {'; '.join(steps)}.")
    elif report.matched_files:
        lines.append("Корни обхода не накрывают ни одного исходника: обход сюда не заходит.")

    if report.modules:
        lines += ["", "Модули:"]
        for module in report.modules:
            by = f" — {_by(module.by)}" if module.by else ""
            lines.append(
                f"  {module.module} ({module.project_file}): {_SCOPE_TITLES[module.scope]}{by}"
            )

    total = sum(report.symbols.values())
    if total:
        lines += ["", f"Символы ({total}): {_counts(report.symbols, STATE_TITLES)}"]
        for row in report.symbol_rows:
            decided = row.winner_rule or (row.exclusion.id if row.exclusion else "")
            kind = f": {row.kind}" if row.kind else ""
            tail = f" по {decided}" if decided else ""
            lines.append(f"  {row.fqn} — {STATE_TITLES.get(row.state, row.state)}{kind}{tail}")
        if total > len(report.symbol_rows):
            lines.append(f"  показано {len(report.symbol_rows)} из {total}; все — --limit 0")

    if report.calls:
        lines += ["", f"Вызовы фронта: {_counts(report.calls, _CALL_TITLES)}"]
        lines += [
            f"  не восстановлено, {reason}: {count}" for reason, count in report.unresolved_reasons
        ]
    if any(report.endpoints.values()):
        lines += ["", f"Эндпоинты: {_counts(report.endpoints, _ENDPOINT_TITLES)}"]
    if report.pages:
        lines += ["", "Страницы и разделы:"]
        lines += [f"  {page.title}  {page.id}" for page in report.pages]

    if report.documents_total:
        lines += ["", f"Документы ({report.documents_total}):"]
        lines += [f"  {doc.doc_path}  {doc.status}  {doc.file_action}" for doc in report.documents]
        if report.documents_total > len(report.documents):
            lines.append(
                f"  показано {len(report.documents)} из {report.documents_total}; все — --limit 0"
            )
    if report.owners:
        teams = ", ".join(
            f"{team or 'без владельца'} {count}" for team, count in report.owners.items()
        )
        lines += ["", f"Владельцы: {teams}"]

    lines += ["", f"Решения ({len(report.decisions)}):"]
    lines += [f"  {_ref(ref)}" for ref in report.decisions] or ["  нет"]
    if report.notes:
        lines += ["", "Заметки:"]
        lines += [f"  {note.message}" for note in report.notes]
    return "\n".join(lines) + "\n"


__all__ = [
    "BUILTIN_FILE",
    "DEFAULT_FILE",
    "DecisionRef",
    "DocumentRef",
    "ModuleScope",
    "Note",
    "PathExplain",
    "covering_root",
    "explain_json",
    "explain_path",
    "format_explain",
    "normalize_target",
    "shown_root",
]
