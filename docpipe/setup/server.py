"""Сервер настройки `docpipe setup serve` (S27): инструменты настройки агенту по MCP.

Здесь только то, чем этот сервер отличается от сервера графа: инструменты,
их аргументы и бюджет ответа. Протокол — общий (`docpipe.mcp`, S26), а каждый
инструмент зовёт ту же функцию, что его CLI-двойник: копия логики в сервере
разошлась бы с командой на первом же ключе, ровно так разошлись три копии
`_prepare`. Соответствие держит таблица `CLI_TWIN` и тест на неё.

Четыре свойства, каждое из которых ломается молча:

- **каждый вызов собирает контекст заново** (`SetupContext`): агент правит
  настройку между вызовами (Р-1 плана), и ответ обязан это видеть. Помнится
  одно — прошлый ответ `setup_status`, база сравнения `baseline: previous`;
  повторный прогон дешёв за счёт кэша разбора;
- **ответ не длиннее бюджета** (`fit`): агент контура обрезает вывод
  инструмента на 25 000 символов или 1000 строк, и ответ на 30 000 будет
  прочитан как полный. Списки урезаются детерминированно, урезанное названо
  (`truncated`, `truncated_lists`), продолжение — `next_offset`;
- **конфигурации может не быть** (онбординг): `setup_config_check` говорит
  `config_missing`, остальные работают на умолчаниях. Сервер, падающий без
  `docpipe.yaml`, как `graph serve`, оставил бы агента без инструментов
  ровно там, где настройку только начинают;
- **сервер ничего не пишет**, кроме кэша разбора (`use_cache=False` — и его нет).
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

import yaml

from docpipe import recon
from docpipe.config import DocpipeConfig, load_config
from docpipe.configcheck import check_config
from docpipe.explain import ANY, build_symbols_report, select
from docpipe.materialize.explain import explain_report
from docpipe.materialize.plan import DEFAULT_DOCS_SCAN_EXCLUDE
from docpipe.materialize.status import STATUSES, filter_documents, status_report
from docpipe.mcp import tool_text
from docpipe.model import Lang, Manifest
from docpipe.setup.candidates import DEFAULT_LIMIT, candidates
from docpipe.setup.candidates import KINDS as CANDIDATE_KINDS
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.explain import explain_path
from docpipe.setup.link import BY_KEYS as LINK_BY_KEYS
from docpipe.setup.link import CATEGORIES as LINK_CATEGORIES
from docpipe.setup.link import DEFAULT_CATEGORY, link_clusters
from docpipe.setup.link import KEYS as LINK_KEYS
from docpipe.setup.review import HistoryError, build_review
from docpipe.setup.status import SetupStatus, build_status
from docpipe.stats import (
    STATE_TITLES,
    TOP,
    UNDECIDED,
    build_stats_report,
    collect_stats,
    enrolled_keys,
    scope_info,
)
from docpipe.step2 import Step2Error, Step2Inputs, prepare
from docpipe.web.pages import NOTE_CODES
from docpipe.web.pages import build_report as build_pages_report

SERVER_NAME: Final = "docpipe-setup"

# Бюджет ответа. 20 000 — запас к обрезке агента контура на 25 000 символов
# (правило 5 плана); 800 строк — тот же запас к его второму порогу, 1000 строк.
# Строки не лишние: ответ уходит с отступами (`mcp.tool_text`), и список
# коротких имён (`public_members`, `base_type_closure`) упирается в 1000 строк
# раньше, чем в 20 000 символов.
MAX_CHARS: Final = 20_000
MAX_LINES: Final = 800

# Доля бюджета на всё, кроме страничного списка, когда ответ листается. Без
# доли страница выходила бы в один элемент рядом с длинным соседом (`not_pages`
# у `setup_pages` — 245 компонентов на squidex). Не половина: охват решений
# `setup_status` на squidex и abp — 10–11 тыс. символов, и при половине он
# урезался бы на каждой странице ради трёх записей.
REST_SHARE: Final = 0.6

INSTRUCTIONS: Final = """
Сервер настройки docpipe на этот репозиторий: факты и проверки — те же
функции, что у команд `docpipe setup …`, `config check`, `recon`, `symbols`,
`docs status` (CLI-двойник назван в описании каждого инструмента). Индекс
графа не нужен, сервер работает до любой сборки.

1. Начинайте с `setup_config_check`, затем `setup_status`: что в области без
   решения и что сломано, у каждой находки — где лежит решение.
2. Сервер ничего не пишет. Файлы настройки (docpipe.yaml, наборы правил,
   pages.yaml, ownership.yaml) правите вы своими средствами.
3. После правки файла настройки позовите инструмент ещё раз и сравните ответ:
   каждый вызов перечитывает настройку. `setup_status` сравнивает с прошлым
   своим ответом сам (`previous` у находок и охвата).
4. Списки — страницами: `limit` и `offset`. Ответ с `truncated: true`
   неполон: `next_offset` — следующая страница, `truncated_lists` — какие
   списки урезаны и сколько в них было. Сузьте запрос или возьмите CLI-двойник.
""".strip()

# CLI-двойник каждого инструмента: команда, которая зовёт ту же функцию.
# Инструмент без двойника — вторая реализация, и тест держит, что каждый
# двойник — зарегистрированная команда (с названными флагами) приложения.
CLI_TWIN: Final[dict[str, tuple[str, ...]]] = {
    "setup_config_check": ("config check",),
    "setup_recon": ("recon",),
    "setup_status": ("setup status",),
    "setup_review": ("setup review",),
    "setup_explain": ("setup explain",),
    "setup_stats": ("scan --stats", "web scan --stats"),
    "setup_symbols": ("symbols",),
    "setup_candidates": ("setup candidates",),
    "setup_link": ("setup link",),
    "setup_pages": ("web pages",),
    "setup_docs": ("docs status",),
    "setup_docs_explain": ("docs explain",),
}

# Кластеров на срез находки у `setup_status` по умолчанию. Не 20, как у CLI:
# при 20 одна находка `dotnet.undecided` на squidex — 10,9 тыс. символов, больше
# половины бюджета, и обзор восьми находок расползается на шесть страниц;
# при 5 — три. Больше кластеров — `limit`, подробности — `setup_symbols`
# и `setup_link` с фильтром по кластеру.
STATUS_LIMIT: Final = 5

# Блоки разведки, которые отдаёт `setup_recon`: чем собран (с полными списками
# проектов — S10), чем заякорен, швы, чему не верить. «Что читать первым»
# и «где центр» — вопросы чтения кода, а не настройки; они у `docpipe_overview`.
RECON_BLOCKS: Final = ("composition", "registries", "seams", "limits")

# Статус конфигурации у `setup_config_check`, когда отчёта проверки нет.
CONFIG_MISSING: Final = "config_missing"
CONFIG_UNREADABLE: Final = "config_unreadable"


# --------------------------------------------------------------------------------------
# Бюджет ответа
# --------------------------------------------------------------------------------------

# Путь в ответе — ключи словарей и номера элементов. У семейства списков вместо
# номера стоит `*`: `findings.*.clusters` — кластеры каждой находки.
_Path = tuple[str | int, ...]
_EACH: Final = "*"


@dataclass(frozen=True)
class _Budget:
    """Сколько символов и строк может занять текст ответа (`mcp.tool_text`)."""

    chars: int
    lines: int

    def holds(self, answer: dict[str, Any]) -> bool:
        text = tool_text(answer)
        return len(text) <= self.chars and text.count("\n") < self.lines

    def share(self, part: float) -> "_Budget":
        return _Budget(int(self.chars * part), int(self.lines * part))


def fit(
    answer: dict[str, Any],
    max_chars: int = MAX_CHARS,
    *,
    max_lines: int = MAX_LINES,
    paged: str | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Уложить ответ в бюджет: урезать списки детерминированно и сказать об этом.

    Влезает — ответ возвращается как есть, без единого нового ключа. Не влезает —
    в начало ответа встают `truncated: true`, `truncated_lists` (семейство
    списков → сколько было в самом длинном из них) и `next_offset`: продолжение
    страничного списка `paged` (`offset` — с какого места он начинался), `null`
    — урезано только то, что страницами не листается.

    Порядок: остальное — не больше доли `REST_SHARE` (иначе страница вышла бы
    в один элемент), страница — сколько влезет, но не меньше одного элемента
    (иначе `next_offset` стоял бы на месте и агент листал бы вечно); не влез
    и один — он урезается изнутри; затем всё, что ещё не влезает.

    Урезается самое тяжёлое семейство — списки одного места во всех элементах
    (`blocks.*.data.candidates.*.examples`), всем им одна граница длины. Вес —
    собственный, без вложенных списков, и по тексту с отступами: по полной
    длине первым выходил бы список-контейнер (`blocks` разведки — целые блоки),
    по одному списку — сотня мелких списков примеров не перевесила бы ни
    одного соседа, а глубокая вложенность в компактном JSON легче, чем в тексте,
    который уйдёт. При равном весе — семейство с меньшим путём; в списке
    остаётся начало. Всё решает содержимое: один ответ урезается одинаково.
    """
    budget = _Budget(max_chars, max_lines)
    if budget.holds(answer):
        return answer
    return _Fitter(answer, budget, paged, offset).run()


class _Fitter:
    """Состояние урезания одного ответа: тело, что урезано, где продолжение."""

    def __init__(
        self, answer: dict[str, Any], budget: _Budget, paged: str | None, offset: int
    ) -> None:
        # Копия через JSON: ответ — данные для `tool_text`, и копировать его
        # иначе, чем его сериализуют, незачем.
        self.body: dict[str, Any] = json.loads(json.dumps(answer, ensure_ascii=False))
        self.budget = budget
        self.cut: dict[_Path, int] = {}
        self.next_offset: int | None = None
        self.paged = paged
        self.page: _Path | None = (paged,) if paged is not None else None
        self.offset = offset

    def run(self) -> dict[str, Any]:
        def full() -> bool:
            return self.holds(self.budget)

        page = self.page
        items = self.body.get(self.paged) if self.paged is not None else None
        if page is not None and isinstance(items, list) and items:
            rest = self.budget.share(REST_SHARE)
            _set(self.body, page, [])
            self._cut_until(lambda: self.holds(rest), lambda family: not _under(family, page))
            _set(self.body, page, items)
            self._fill_page(page, items)
            # Не влез и один элемент страницы — урезать его изнутри, а не то,
            # что рядом: переполняет ответ он, а соседи уже уложены в свою долю.
            self._cut_until(full, lambda family: family != page and _under(family, page))
        if not self._cut_until(full, lambda family: family != page):
            return {
                "truncated": True,
                "error": (
                    f"ответ не умещается в {self.budget.chars} символов и "
                    f"{self.budget.lines} строк даже без списков: сузьте запрос"
                ),
            }
        return self.framed()

    def framed(self) -> dict[str, Any]:
        """Тело с пометкой об урезании. Семейства, исчезнувшие с родителем, не называются."""
        lists = {
            _dotted(family): longest
            for family, longest in sorted(self.cut.items(), key=lambda item: _dotted(item[0]))
            if any(True for _ in _members(self.body, family))
        }
        return {
            "truncated": True,
            "next_offset": self.next_offset,
            "truncated_lists": lists,
            **self.body,
        }

    def holds(self, budget: _Budget) -> bool:
        return budget.holds(self.framed())

    def _cut_until(self, holds: Callable[[], bool], allowed: Callable[[_Path], bool]) -> bool:
        """Урезать семейства, самое тяжёлое первым, пока ответ не влезет. `False` — нечего."""
        while not holds():
            family = self._heaviest(allowed)
            if family is None:
                return False
            self._shrink(family, holds)
        return True

    def _fill_page(self, page: _Path, items: list[Any]) -> None:
        """Страница — самое длинное начало, которое влезает; не меньше одного элемента."""
        total = len(items)
        self.cut[page] = total

        def holds(count: int) -> bool:
            _set(self.body, page, items[:count])
            self.next_offset = self.offset + count if count < total else None
            return self.holds(self.budget)

        if holds(total):
            del self.cut[page]
            return
        low, high = 1, total - 1
        while low < high:
            middle = (low + high + 1) // 2
            if holds(middle):
                low = middle
            else:
                high = middle - 1
        holds(low)

    def _heaviest(self, allowed: Callable[[_Path], bool]) -> _Path | None:
        """Самое тяжёлое семейство непустых списков из разрешённых."""
        weights: dict[_Path, list[int]] = {}
        found: list[tuple[_Path, int, int]] = []
        _measure(self.body, (), 0, found)
        for path, chars, lines in found:
            family = _family(path)
            if allowed(family):
                total = weights.setdefault(family, [0, 0])
                total[0] += chars
                total[1] += lines
        if not weights:
            return None

        def rank(item: tuple[_Path, list[int]]) -> tuple[float, str]:
            chars, lines = item[1]
            share = max(chars / self.budget.chars, lines / self.budget.lines)
            return -share, _dotted(item[0])

        return min(weights.items(), key=rank)[0]

    def _shrink(self, family: _Path, holds: Callable[[], bool]) -> None:
        """Одна граница длины всем спискам семейства: ровно сколько нужно, иначе вдвое."""
        members = list(_members(self.body, family))
        longest = max(len(items) for _, items in members)
        self.cut.setdefault(family, longest)

        def cap(count: int) -> None:
            for path, items in members:
                _set(self.body, path, items[:count])

        cap(0)
        if not holds():
            # Одного семейства мало: вдвое, и дальше — следующее самое тяжёлое.
            # Пополам, а не до нуля: иначе первое по весу исчезало бы целиком,
            # а остальные не теряли бы ничего.
            cap(longest // 2)
            return
        low, high = 0, longest - 1
        while low < high:
            middle = (low + high + 1) // 2
            cap(middle)
            if holds():
                low = middle
            else:
                high = middle - 1
        cap(low)


def _measure(
    node: Any, path: _Path, depth: int, found: list[tuple[_Path, int, int]]
) -> tuple[int, int, int, int]:
    """Символы и переводы строк узла в тексте с отступами — и сколько из них во вложенных списках.

    Узел стоит на глубине `depth` (отступ `2 * depth`), как в `json.dumps(..., indent=2)`.
    В `found` — непустые списки с собственным весом: свои символы и строки минус
    те, что во вложенных в элементы списках. Для родителя список целиком — вложенный.
    """
    if not isinstance(node, dict | list):
        return len(json.dumps(node, ensure_ascii=False)), 0, 0, 0
    if not node:
        return 2, 0, 0, 0
    pad = 2 * (depth + 1)
    # «[», перевод строки, запятые между элементами, отступ и «]» закрытия.
    chars, lines, nested_chars, nested_lines = 2 + len(node) - 1 + 2 * depth + 1, 1, 0, 0
    entries = node.items() if isinstance(node, dict) else enumerate(node)
    for key, value in entries:
        inner, inner_lines, inner_nested, inner_nested_lines = _measure(
            value, (*path, key), depth + 1, found
        )
        label = len(json.dumps(key, ensure_ascii=False)) + 2 if isinstance(node, dict) else 0
        chars += pad + label + inner + 1
        lines += 1 + inner_lines
        nested_chars += inner_nested
        nested_lines += inner_nested_lines
    if isinstance(node, dict):
        return chars, lines, nested_chars, nested_lines
    found.append((path, chars - nested_chars, lines - nested_lines))
    return chars, lines, chars, lines


def _family(path: _Path) -> _Path:
    return tuple(_EACH if isinstance(part, int) else part for part in path)


def _members(node: Any, family: _Path, path: _Path = ()) -> Iterator[tuple[_Path, list[Any]]]:
    """Списки семейства, какие есть сейчас: путь и сам список."""
    if not family:
        if isinstance(node, list):
            yield path, node
        return
    head, rest = family[0], family[1:]
    if head == _EACH:
        if isinstance(node, list):
            for index, item in enumerate(node):
                yield from _members(item, rest, (*path, index))
    elif isinstance(node, dict) and head in node:
        yield from _members(node[head], rest, (*path, head))


def _under(path: _Path, tree: _Path | None) -> bool:
    return tree is not None and path[: len(tree)] == tree


def _dotted(path: _Path) -> str:
    return ".".join(str(part) for part in path)


def _set(node: Any, path: _Path, value: Any) -> None:
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


# --------------------------------------------------------------------------------------
# Аргументы инструментов
# --------------------------------------------------------------------------------------


class ArgumentError(Exception):
    """Аргумент вызова негоден: не того типа, вне перечня, отрицательный, лишний."""


@dataclass(frozen=True)
class Param:
    """Аргумент инструмента: из него — и схема в `tools/list`, и проверка вызова.

    Одно описание на оба: схема, объявляющая одно, и проверка, принимающая
    другое, — та самая ловушка `limit: "abc"`, ронявшая сервер до S26.
    `default=None` у необязательного — «не задан»; пустая строка — то же.
    """

    name: str
    kind: Literal["integer", "string"]
    description: str
    default: str | int | None = None
    choices: tuple[str, ...] = ()
    required: bool = False

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.kind, "description": self.description}
        if self.choices:
            out["enum"] = list(self.choices)
        if self.kind == "integer":
            out["minimum"] = 0
        if self.default is not None:
            out["default"] = self.default
        return out

    def parse(self, value: object) -> str | int | None:
        if value is None or value == "":
            if self.required:
                raise ArgumentError(f"нет обязательного аргумента {self.name}")
            return self.default
        if self.kind == "integer":
            return self._integer(value)
        if not isinstance(value, str):
            raise ArgumentError(f"{self.name} — не строка, а {type(value).__name__}: {value!r}")
        if self.choices and value not in self.choices:
            raise ArgumentError(f"{self.name}: {value!r}; допустимы: {', '.join(self.choices)}")
        return value

    def _integer(self, value: object) -> int:
        # Строка из цифр принимается: клиенты MCP шлют числа и строкой, и отказ
        # «"20" — не число» отправил бы агента чинить не то.
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArgumentError(f"{self.name} — не целое число: {value!r}")
        if value < 0:
            raise ArgumentError(f"{self.name} не бывает отрицательным: {value}")
        return value


@dataclass(frozen=True)
class Answer:
    """Ответ инструмента до бюджета: тело и какой его список листается страницами."""

    body: dict[str, Any]
    paged: str | None = None
    offset: int = 0


Handler = Callable[["SetupTools", dict[str, Any]], Answer]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: tuple[Param, ...]
    handler: Handler

    def schema(self) -> dict[str, Any]:
        twins = " или ".join(f"`docpipe {twin}`" for twin in CLI_TWIN[self.name])
        return {
            "name": self.name,
            "description": f"{self.description} CLI-двойник: {twins}.",
            "inputSchema": {
                "type": "object",
                "properties": {param.name: param.schema() for param in self.params},
                "required": [param.name for param in self.params if param.required],
                "additionalProperties": False,
            },
        }

    def parse(self, arguments: dict[str, Any]) -> dict[str, Any]:
        known = {param.name for param in self.params}
        if unknown := sorted(set(arguments) - known):
            listing = ", ".join(sorted(known)) or "аргументов нет"
            raise ArgumentError(f"неизвестные аргументы {', '.join(unknown)}; известны: {listing}")
        return {param.name: param.parse(arguments.get(param.name)) for param in self.params}


def _limit(default: int = DEFAULT_LIMIT, what: str = "Сколько показать") -> Param:
    return Param("limit", "integer", f"{what}; 0 — все.", default)


def _offset() -> Param:
    return Param(
        "offset", "integer", "Сколько пропустить с начала: next_offset прошлого ответа.", 0
    )


def _lang() -> Param:
    return Param("lang", "string", "cs — .NET (шаг 1), ts — фронт (шаг web).", "cs", ("cs", "ts"))


def _window(items: list[Any], offset: int, limit: int) -> list[Any]:
    """Страница списка: с `offset`, не больше `limit` (0 — до конца)."""
    rest = items[offset:]
    return rest[:limit] if limit else rest


def _as_lang(value: str) -> Lang:
    return "cs" if value == "cs" else "ts"


# --------------------------------------------------------------------------------------
# Инструменты
# --------------------------------------------------------------------------------------


def _config_check(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    config = tools.config_file
    label = tools.config.as_posix() if tools.config is not None else None
    if config is None:
        where = f"файл {label} не найден" if label else "файл не назван (--config)"
        return Answer(
            {
                "status": CONFIG_MISSING,
                "config": label,
                "message": (
                    f"docpipe.yaml нет: {where}. Остальные инструменты работают на умолчаниях; "
                    "настройка начинается с этого файла."
                ),
            }
        )
    try:
        settings = load_config(config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # Ответ, а не ошибка: найти, что сломано в настройке, — работа этого
        # инструмента. Остальные инструменты на такой настройке отказывают.
        return Answer(
            {
                "status": CONFIG_UNREADABLE,
                "config": label,
                "message": f"Конфигурация не читается: {exc}",
            }
        )
    report = check_config(settings, config, tools.root, Path.cwd())
    return Answer(report.model_dump(mode="json"))


def _recon(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    report = recon.build_report(tools.root, recon.DEFAULT_MONTHS, args["top"], [])
    blocks: list[dict[str, Any]] = []
    for block in report["blocks"]:
        if block["id"] not in RECON_BLOCKS:
            continue
        data = block["data"]
        if block["id"] == "composition":
            # Полные списки файлов сборки повторяют `projects` и съели бы бюджет
            # ответа: остаются счёт и примеры, полные списки проектов — в `projects`.
            rows = [{k: v for k, v in row.items() if k != "paths"} for row in data["build_files"]]
            data = {**data, "build_files": rows}
        blocks.append({**block, "data": data})
    body = {key: report[key] for key in ("schema", "repo", "vcs", "head", "params")}
    return Answer({**body, "blocks": blocks})


def _status(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    offset: int = args["offset"]
    context = tools.context()
    report = tools.remember_status(
        lambda baseline: build_status(context, baseline=baseline, limit=args["limit"]),
        previous=args["baseline"] == "previous",
        first_page=offset == 0,
    )
    body = report.model_dump(mode="json")
    body["findings"] = body["findings"][offset:]
    return Answer(body, paged="findings", offset=offset)


def _review(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    offset: int = args["offset"]
    report = build_review(tools.context(), since=args["since"], limit=args["limit"])
    body = report.model_dump(mode="json")
    body["applied"] = body["applied"][offset:]
    return Answer(body, paged="applied", offset=offset)


def _explain(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    offset: int = args["offset"]
    report = explain_path(tools.context(), args["path"], limit=args["limit"])
    body = report.model_dump(mode="json")
    # Страницы — по `decisions`, главному полю ответа: без страницы бюджет
    # урезал бы его наравне с примерами символов, и «что решило судьбу этого
    # кода» оборвалось бы на середине без продолжения.
    body["decisions"] = body["decisions"][offset:]
    return Answer(body, paged="decisions", offset=offset)


def _stats(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    context, top = tools.context(), args["top"]
    if args["lang"] == "cs":
        scan = context.scan
        report = build_stats_report(
            scan.stats,
            lang="cs",
            top=top,
            scope=scope_info(scan.manifest, scan.meta),
            parse_error_files=scan.meta.parse_error_files,
        )
    else:
        web = context.web
        statistics = collect_stats(
            web.index, web.manifest.nodes, context.web_ruleset, enrolled_keys(web.manifest, "ts")
        )
        report = build_stats_report(
            statistics,
            lang="ts",
            top=top,
            stale=web.overrides.stale,
            parse_error_files=web.meta.parse_error_files,
        )
    return Answer(report.model_dump(mode="json"))


def _symbols(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    context, lang = tools.context(), _as_lang(args["lang"])
    if lang == "cs":
        index, manifest, ruleset = context.scan.index, context.scan.manifest, context.ruleset
    else:
        index, manifest, ruleset = context.web.index, context.web.manifest, context.web_ruleset
    selection = select(
        index,
        manifest.nodes,
        ruleset,
        enrolled_keys(manifest, lang),
        state=args["state"],
        module=args["module"] or "",
        namespace=args["namespace"] or "",
        path=args["path"] or "",
        rule=args["rule"] or "",
        kind=args["kind"] or "",
        limit=args["limit"],
        offset=args["offset"],
    )
    body = build_symbols_report(selection).model_dump(mode="json")
    return Answer(body, paged="symbols", offset=args["offset"])


def _candidates(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    report = candidates(args["kind"], tools.context(), limit=args["limit"], offset=args["offset"])
    return Answer(report.model_dump(mode="json"), paged="items", offset=args["offset"])


def _link(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    category, by = args["category"], args["by"]
    if by is not None and by not in LINK_BY_KEYS[category]:
        # До прогонов и как у CLI — ошибкой аргумента: `link_clusters` назвал
        # бы её ошибкой конфигурации, и агент пошёл бы править docpipe.yaml.
        allowed = ", ".join(LINK_BY_KEYS[category])
        raise ArgumentError(f"by: {by!r}; у {category} допустимы: {allowed}")
    report = link_clusters(
        tools.context(),
        category=category,
        by=by,
        limit=args["limit"],
        offset=args["offset"],
    )
    return Answer(report.model_dump(mode="json"), paged="clusters", offset=args["offset"])


def _pages(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    report = build_pages_report(tools.context().web.manifest, note=args["note"] or "")
    body = report.model_dump(mode="json")
    # `pages_total` — сколько страниц прошло отбор: `counts` считается по всему
    # дереву, и без этого числа страница списка читалась бы как весь отбор.
    body["pages_total"] = len(body["pages"])
    body["pages"] = _window(body["pages"], args["offset"], args["limit"])
    return Answer(body, paged="pages", offset=args["offset"])


def _step2(context: SetupContext, lang: Lang) -> Step2Inputs:
    """Вход шага 2 по манифесту в памяти — как `docs status` и `docs explain` (`links=True`)."""
    manifest: Manifest = context.scan.manifest if lang == "cs" else context.web.manifest
    return prepare(manifest, context.root, context.settings, context.config, links=True)


def _docs(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    loaded = _step2(tools.context(), _as_lang(args["lang"]))
    status = args["status"]
    selected = filter_documents(loaded.plan.documents, [], [], statuses=[status] if status else [])
    body = status_report(loaded.plan, selected).model_dump(mode="json")
    body["documents"] = _window(body["documents"], args["offset"], args["limit"])
    if loaded.warnings:
        # CLI печатает их в stderr, а stderr сервера агент не видит.
        body["warnings"] = list(loaded.warnings)
    return Answer(body, paged="documents", offset=args["offset"])


def _docs_explain(tools: "SetupTools", args: dict[str, Any]) -> Answer:
    context = tools.context()
    loaded = _step2(context, _as_lang(args["lang"]))
    target = _doc_target(args["path"], context.root)
    globs = DEFAULT_DOCS_SCAN_EXCLUDE + list(context.settings.docs_scan_exclude)
    plan = loaded.plan
    if plan.errors:
        report = explain_report(target, None, context.root, globs, plan.errors)
    else:
        doc = next((item for item in plan.documents if item.doc_path == target), None)
        report = explain_report(target, doc, context.root, globs)
    return Answer(report.model_dump(mode="json"))


def _doc_target(path: str, root: Path) -> str:
    """Путь документа от корня: как его печатает `docs status`, абсолютный или с `./`."""
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    text = PurePosixPath(path.replace("\\", "/")).as_posix()
    return text[2:] if text.startswith("./") else text


TOOLS: Final[tuple[Tool, ...]] = (
    Tool(
        "setup_config_check",
        "Во что разрешается каждый путь docpipe.yaml и что из этого есть: входы, цели "
        "записи, корни обхода, входы адаптеров, движок; `problems` — что сломано. "
        "Без docpipe.yaml — `status: config_missing`, нечитаемый — `config_unreadable`.",
        (),
        _config_check,
    ),
    Tool(
        "setup_recon",
        "Разведка репозитория, настройки не требует: чем собран (языки, файлы сборки, "
        "полные списки проектов .NET, решений и фронтов — `projects`), чем заякорен "
        "(кандидаты в реестры), как языки говорят между собой, чему в отчёте не верить.",
        (Param("top", "integer", "Длина списков в блоках.", recon.DEFAULT_TOP),),
        _recon,
    ),
    Tool(
        "setup_status",
        "Что в области ещё без решения и что сломано: находки по кодам с кластерами "
        "и местом решения (`decision_home`), охват каждого решения настройки, что решено "
        "не брать. Страница — по `findings`; `unexplained` и `defects` — по всем.",
        (
            _limit(STATUS_LIMIT, "Кластеров на срез находки"),
            Param(
                "baseline",
                "string",
                "previous — сравнить с прошлым ответом этого сервера (`previous` у находок "
                "и охвата); страницы offset > 0 сравниваются с той же базой, что offset 0. "
                "none — без сравнения.",
                "previous",
                ("previous", "none"),
            ),
            _offset(),
        ),
        _status,
    ),
    Tool(
        "setup_review",
        "Ревью: что появилось после коммита файлов настройки и как прежние решения "
        "обошлись с новым кодом (`applied`), новые находки, решения без охвата, "
        "незакоммиченная настройка. Нужен git. Страница — по `applied`.",
        (
            Param(
                "since",
                "string",
                "Ревизия базы; по умолчанию — последний коммит файлов настройки.",
            ),
            _limit(what="Новых файлов, файлов у решения, примеров у находки"),
            _offset(),
        ),
        _review,
    ),
    Tool(
        "setup_explain",
        "Что решено об этом коде: обход и отсев, область модуля, символы с решениями, "
        "страницы, вызовы, документы, владение и главное — `decisions`: какие записи "
        "настройки и с какими причинами решили его судьбу. По ним видно, что править. "
        "Страница — по `decisions`.",
        (
            Param(
                "path",
                "string",
                "Файл, каталог или глоб от корня репозитория; `.` — весь репозиторий.",
                required=True,
            ),
            _limit(what="Строк символов и документов"),
            _offset(),
        ),
        _explain,
    ),
    Tool(
        "setup_stats",
        "Счётчики решений по символам шага: сколько документируется, отсеяно с причиной, "
        "вне области, без решения; срезы нерешённых — модули, окончания и последнее "
        "слово имени, базы, атрибуты, пространства имён.",
        (_lang(), Param("top", "integer", "Строк в каждом срезе.", TOP)),
        _stats,
    ),
    Tool(
        "setup_symbols",
        "Сами символы с решением о каждом: правило-победитель, отсев с причиной, "
        "страница, базы, атрибуты, публичные члены. По умолчанию — без решения.",
        (
            _lang(),
            Param(
                "state",
                "string",
                "Состояние решения; any — все.",
                UNDECIDED,
                (*sorted(STATE_TITLES), ANY),
            ),
            Param("module", "string", "Подстрока пути .csproj; с `*` — глоб, как в enrolled."),
            Param("namespace", "string", "Начало namespace."),
            Param("path", "string", "Файл или каталог от корня; с `*?[` — глоб, как path_glob."),
            Param("rule", "string", "Только символы, к которым причастно это правило."),
            Param("kind", "string", "Только этот вид сущности."),
            _limit(),
            _offset(),
        ),
        _symbols,
    ),
    Tool(
        "setup_candidates",
        "Кандидаты в ключ настройки — факты, из которых строится вопрос человеку: "
        "обёртки DI (di-methods), интерфейсы диспетчеризации, обращения к реестру, "
        "разделы без маршрута (features), HTTP-обёртки и построители адреса фронта.",
        (
            Param("kind", "string", "Вид кандидатов.", choices=CANDIDATE_KINDS, required=True),
            _limit(),
            _offset(),
        ),
        _candidates,
    ),
    Tool(
        "setup_link",
        "Шов фронт↔.NET кластерами: что не связалось, где, до трёх примеров; у вызовов "
        "без эндпоинта по модулю — подсказка записи web.url_rewrite с числом связанных.",
        (
            Param("category", "string", "Категория шва.", DEFAULT_CATEGORY, LINK_CATEGORIES),
            Param(
                "by",
                "string",
                "Ключ кластера; у категории свои, по умолчанию — первый из них.",
                choices=LINK_KEYS,
            ),
            _limit(what="Кластеров"),
            _offset(),
        ),
        _link,
    ),
    Tool(
        "setup_pages",
        "Страницы фронта и почему каждая — страница: маршруты с источником, зависимости, "
        "вызовы, заметки. Разделы и компоненты, страницами не ставшие, — рядом. "
        "Страница списка — по `pages`, `pages_total` — сколько прошло отбор.",
        (
            Param("note", "string", "Только страницы с этой заметкой.", choices=tuple(NOTE_CODES)),
            _limit(what="Страниц"),
            _offset(),
        ),
        _pages,
    ),
    Tool(
        "setup_docs",
        "Что прогон шага 2 сделает с каждым документом: статус, действие агента и с файлом, "
        "причина, битые ссылки. По манифесту в памяти, а не с диска.",
        (
            Param(
                "status",
                "string",
                "Только документы с этим статусом.",
                choices=tuple(sorted(STATUSES)),
            ),
            _lang(),
            _limit(what="Документов"),
            _offset(),
        ),
        _docs,
    ),
    Tool(
        "setup_docs_explain",
        "Почему с этим документом сделают именно это: какой фильтр обхода его отбросил, "
        "план, чем собранный текст отличается от файла и задевает ли авторские секции.",
        (
            Param("path", "string", "Путь документа от корня репозитория.", required=True),
            _lang(),
        ),
        _docs_explain,
    ),
)

_BY_NAME: Final[dict[str, Tool]] = {tool.name: tool for tool in TOOLS}


# --------------------------------------------------------------------------------------
# Сервер
# --------------------------------------------------------------------------------------


class SetupTools:
    """Инструменты настройки как набор инструментов общего протокола (`docpipe.mcp.ToolSet`).

    `config` — путь к `docpipe.yaml`, как его передали (`--config`); файла может
    не быть. `use_cache=False` — без кэша разбора: замер на чужом клоне
    «только для чтения». `max_chars`/`max_lines` — бюджет ответа.
    """

    server_name: Final[str] = SERVER_NAME
    instructions: Final[str] = INSTRUCTIONS

    def __init__(
        self,
        root: Path,
        config: Path | None,
        *,
        use_cache: bool = True,
        max_chars: int = MAX_CHARS,
        max_lines: int = MAX_LINES,
    ) -> None:
        self.root = root
        self.config = config
        self.use_cache = use_cache
        self.max_chars = max_chars
        self.max_lines = max_lines
        # Прошлый ответ `setup_status` (страница offset 0) и база, с которой
        # сравнивали его: со второй страницы сравнение идёт с той же базой,
        # иначе разница правки была бы видна только на первой.
        self._last_status: SetupStatus | None = None
        self._last_baseline: SetupStatus | None = None

    @property
    def config_file(self) -> Path | None:
        """`docpipe.yaml`, если он есть; иначе `None` — работа на умолчаниях."""
        if self.config is not None and self.config.is_file():
            return self.config
        return None

    def context(self) -> SetupContext:
        """Контекст на один вызов: настройка перечитывается каждый раз (Р-1)."""
        config = self.config_file
        if config is None:
            return SetupContext(self.root, DocpipeConfig(), None, self.use_cache)
        return SetupContext.build(self.root, config, use_cache=self.use_cache)

    def remember_status(
        self,
        build: Callable[[SetupStatus | None], SetupStatus],
        *,
        previous: bool,
        first_page: bool,
    ) -> SetupStatus:
        """Отчёт `setup_status` с базой из памяти; первая страница становится прошлым."""
        if first_page:
            baseline = self._last_status if previous else None
        else:
            baseline = self._last_baseline if previous else None
        report = build(baseline)
        if first_page:
            self._last_status, self._last_baseline = report, baseline
        return report

    def tools(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in TOOLS]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Выполнить инструмент. Негодный вход — ответ с `error`; сбой — исключение протоколу."""
        tool = _BY_NAME.get(name)
        if tool is None:
            return {"error": f"инструмент {name!r} неизвестен; список — в tools/list"}
        try:
            answer = tool.handler(self, tool.parse(arguments))
        except ArgumentError as exc:
            return {"error": f"Ошибка аргумента: {exc}"}
        except HistoryError as exc:
            return {"error": f"Ревью не построено: {exc}"}
        except InputError as exc:
            return {"error": f"Ошибка конфигурации: {exc}"}
        except Step2Error as exc:
            return {"error": f"Шаг 2 не собрался: {exc.message}"}
        except yaml.YAMLError as exc:
            # Загрузчики настройки не переводят синтаксическую ошибку YAML
            # в отказ (бэклог, S03); агент правит файлы текстом, и битый
            # отступ — самая частая его ошибка: ответ, а не трассировка.
            return {"error": f"Файл настройки не разбирается как YAML: {exc}"}
        return fit(
            answer.body,
            self.max_chars,
            max_lines=self.max_lines,
            paged=answer.paged,
            offset=answer.offset,
        )


__all__ = [
    "CLI_TWIN",
    "CONFIG_MISSING",
    "CONFIG_UNREADABLE",
    "INSTRUCTIONS",
    "MAX_CHARS",
    "MAX_LINES",
    "SERVER_NAME",
    "TOOLS",
    "SetupTools",
    "fit",
]
