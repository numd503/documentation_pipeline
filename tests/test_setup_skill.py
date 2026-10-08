"""Скилл `setup` (S29): всё, что в нём названо именем кода, сверяется с кодом.

Скилл читает агент контура — модель уровня GLM-5.3 Flash, — и ссылка на
несуществующую команду, инструмент или ключ проходит любое ревью глазами,
а ломается у агента на первом шаге: он впишет в настройку то, что загрузчик
отвергнет, или позовёт то, чего нет. Поэтому здесь проверяется то, что
проверяемо: команды `docpipe …` — зарегистрированы, инструменты `setup_*` —
из таблицы плана S27 и сервера, коды находок и ключи — из кода и карты,
каждый пример YAML грузится своим загрузчиком, команды в блоках кода
проходят оболочку агента, длины тел — в пределах, которые агент не обрежет.

Разметка, на которой держится разбор: пример YAML начинается строкой
`# file: <файл>[#<секция>]`; фаза — файл `phases/NN-имя.md` с разделами
`## Цель`, `## Инструменты`, `## Контрольная точка`, `## Ловушки`,
`## Правка файлов`.
"""

import importlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pytest
import typer
import yaml

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import load_config
from docpipe.materialize.ownership import load_ownership
from docpipe.setup import review
from docpipe.setup.candidates import KINDS
from docpipe.setup.link import BY_KEYS, CATEGORIES
from docpipe.setup.status import CODES
from docpipe.web.overrides import load_overrides
from tests.setup_map_support import all_map_keys
from tests.test_skills import split_skill

SKILL_DIR: Final = Path(".gigacode/skills/setup")
SKILL: Final = SKILL_DIR / "SKILL.md"
PHASES_DIR: Final = SKILL_DIR / "phases"
PLAN: Final = Path("docs/setup-implementation-plan.md")
# Набор, который установщик кладёт в репозиторий продукта: пример правила
# дописывается к нему, а не к пустому файлу — так его правит агент.
RULES_BASE: Final = Path("deploy/generic-docspipe/rules.yaml")

# Состав фаз — спецификация S29, п. 2. Фаза Python добавится файлом сюда.
PHASES: Final = (
    "00-scope.md",
    "10-dotnet.md",
    "20-web.md",
    "30-link.md",
    "40-pages.md",
    "50-docs.md",
    "60-ownership.md",
    "80-expand.md",
    "90-review.md",
)
PHASE_BLOCKS: Final = ("Цель", "Инструменты", "Контрольная точка", "Ловушки", "Правка файлов")

# Длинное тело агент обрезает: инструкция, потерявшая конец, теряет остановку.
BODY_LIMIT: Final = 250
PHASE_LIMIT: Final = 200

# Разметка примера → загрузчик. Секция у `rules.yaml` обязательна: файл
# секционный, и пример без неё не сказал бы, куда его дописывать.
MARKS: Final = (
    "docpipe.yaml",
    "rules.yaml#dotnet",
    "rules.yaml#web",
    "pages.yaml",
    "ownership.yaml",
)

_FENCE = re.compile(r"^```([a-z]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_SPAN = re.compile(r"`([^`\n]+)`")
_DOCPIPE = re.compile(r"(?:^|(?<=[\s;&|]))docpipe((?: [a-z][a-z-]*)+)")
_TOOL = re.compile(r"\bsetup_[a-z_]+\b")
_CODE = re.compile(r"[a-z]+\.[a-z_]+")
_DOTTED = re.compile(r"[a-z_]+(?:\.[a-z_]+|\[\])+")
_PHASE_REF = re.compile(r"phases/([0-9]{2}-[a-z]+\.md)")
_CLONE_PATH = re.compile(r"(?:docs|deploy|tools)/[\w./-]+\.(?:md|py|sh)")
_MARK = re.compile(r"# file: (\S+)")
FILE_SUFFIXES: Final = frozenset({"yaml", "md", "json", "ts", "cs", "csproj", "txt", "py", "sh"})

_, BODY = split_skill(SKILL)
TEXTS: Final[dict[str, str]] = {"SKILL.md": BODY} | {
    f"phases/{path.name}": path.read_text(encoding="utf-8")
    for path in sorted(PHASES_DIR.glob("*.md"))
}


def _blocks(text: str) -> list[tuple[str, str]]:
    """Блоки кода: язык и содержимое."""
    return [(match.group(1), match.group(2)) for match in _FENCE.finditer(text)]


def _prose(text: str) -> str:
    """Текст без блоков кода: в нём ищутся вставки `…`."""
    return _FENCE.sub("", text)


def _spans(text: str) -> list[str]:
    return _SPAN.findall(_prose(text))


ALL_SPANS: Final = [span for text in TEXTS.values() for span in _spans(text)]
ALL_TEXT: Final = "\n".join(TEXTS.values())


# --------------------------------------------------------------------------------------
# Команды, инструменты, коды и ключи
# --------------------------------------------------------------------------------------


def _command(words: list[str]) -> tuple[str, ...] | None:
    """Путь до зарегистрированной команды или `None`.

    Слова после команды — аргументы (`setup candidates di-methods`) и не
    проверяются; остановка на группе (`docpipe setup`) — не команда.
    """
    current: object = typer.main.get_command(app)
    path: list[str] = []
    for word in words:
        commands = getattr(current, "commands", None)
        if commands is None:
            break
        if word not in commands:
            return None
        current = commands[word]
        path.append(word)
    return tuple(path) if getattr(current, "commands", None) is None else None


def _mentioned_commands() -> list[str]:
    """Каждое `docpipe …` во вставках и строках блоков кода."""
    places = ALL_SPANS + [
        line for text in TEXTS.values() for _, body in _blocks(text) for line in body.splitlines()
    ]
    return sorted({match.strip() for place in places for match in _DOCPIPE.findall(place)})


def _plan_tools() -> dict[str, tuple[str, ...]]:
    """Таблица инструментов раздела S27 плана: имя → CLI-двойники без флагов.

    Плана с таблицей, разобранной из текста, достаточно, чтобы опечатка
    в имени инструмента ловилась ещё до того, как влит сервер.
    """
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("\n## S27 ")
    section = text[start : text.index("\n## ", start + 1)]
    tools: dict[str, tuple[str, ...]] = {}
    for line in section.splitlines():
        # `\|` внутри ячейки (`previous \| none`) — не граница ячеек.
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) == 4 and re.fullmatch(r"`setup_[a-z_]+`", cells[0]):
            twins = tuple(
                " ".join(word for word in span.split() if not word.startswith("--"))
                for span in re.findall(r"`([^`]+)`", cells[3])
            )
            tools[cells[0].strip("`")] = twins
    return tools


PLAN_TOOLS: Final = _plan_tools()
NAMED_TOOLS: Final = frozenset(_TOOL.findall(ALL_TEXT))


def test_plan_table_of_tools_is_read() -> None:
    """Разбор таблицы S27 не пуст и двойники — команды: иначе сверка ниже вырождена."""
    assert len(PLAN_TOOLS) == 12, sorted(PLAN_TOOLS)
    for tool, twins in PLAN_TOOLS.items():
        assert twins, tool
        for twin in twins:
            assert _command(twin.split()) is not None, f"{tool}: двойник {twin!r} не команда"


def test_every_command_is_registered() -> None:
    """Команда `docpipe …` в скилле — зарегистрированная команда приложения."""
    commands = _mentioned_commands()
    assert commands
    broken = [command for command in commands if _command(command.split()) is None]
    assert not broken


def test_every_cli_twin_is_named_in_the_skill() -> None:
    """Без сервера агент идёт по двойникам: каждый из них назван в `SKILL.md`."""
    named = {_command(command.split()) for command in _DOCPIPE.findall(BODY)}
    named |= {
        _command(command.strip().split())
        for span in _spans(BODY)
        for command in _DOCPIPE.findall(span)
    }
    twins = {tuple(twin.split()) for twins in PLAN_TOOLS.values() for twin in twins}
    assert twins <= named, sorted(twins - named)


def test_every_named_tool_is_in_the_plan_table() -> None:
    """Опечатка в имени `setup_*` ловится по таблице плана, пока сервера нет."""
    assert NAMED_TOOLS
    assert PLAN_TOOLS.keys() >= NAMED_TOOLS, sorted(NAMED_TOOLS - PLAN_TOOLS.keys())


def test_skill_lists_every_tool_of_the_table() -> None:
    """Таблица инструментов `SKILL.md` полна: инструмент, которого в ней нет,
    агент не позовёт, а его двойника не найдёт."""
    in_body = set(_TOOL.findall(BODY))
    assert in_body == PLAN_TOOLS.keys(), sorted(PLAN_TOOLS.keys() - in_body)


def _table_arguments() -> dict[str, set[str]]:
    """Таблица инструментов `SKILL.md`: инструмент → имена из колонки «Аргументы»."""
    rows: dict[str, set[str]] = {}
    for line in BODY.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and re.fullmatch(r"`setup_[a-z_]+`", cells[0]):
            rows[cells[0].strip("`")] = set(_SPAN.findall(cells[1]))
    return rows


def test_skill_tool_table_matches_server_params() -> None:
    """Колонка «Аргументы» равна параметрам сервера (S33, ловушка 5 прогона S31).

    Без `offset` агент не прочтёт `next_offset`, без `lang` `setup_docs`
    покажет только .NET (squidex: 118 документов при 583 ничьих фронта) —
    и «не показали» станет «нет».
    """
    server = importlib.import_module("docpipe.setup.server")
    table = _table_arguments()
    assert table.keys() == {tool.name for tool in server.TOOLS}
    for tool in server.TOOLS:
        assert table[tool.name] == {param.name for param in tool.params}, tool.name


def test_every_named_tool_is_served_by_the_setup_server() -> None:
    """Каждый упомянутый `setup_*` есть в `SetupTools.tools()` (S27)."""
    try:
        server = importlib.import_module("docpipe.setup.server")
    except ModuleNotFoundError as error:
        if error.name != "docpipe.setup.server":
            raise
        pytest.skip("ждёт S27: docpipe.setup.server ещё не влит")
    tools = server.SetupTools(root=Path("tests/fixtures/SampleSolution"), config=None)
    served = {tool["name"] for tool in tools.tools()}
    assert served >= NAMED_TOOLS, sorted(NAMED_TOOLS - served)
    assert served <= NAMED_TOOLS, sorted(served - NAMED_TOOLS)


MAP_KEYS: Final = all_map_keys()
NOTES: Final = frozenset(
    value
    for name, value in vars(review).items()
    if name.startswith("NOTE_") and isinstance(value, str)
)
# Первые сегменты имён, которые сверяются: коды находок, ключи карты, `when`.
HEADS: Final = frozenset(
    {code.split(".")[0] for code in CODES} | {re.split(r"[.\[]", key)[0] for key in MAP_KEYS}
)


def _is_key(token: str) -> bool:
    """Ключ карты или его начало по границе сегмента.

    `web.url_rewrite` — начало `web.url_rewrite[].module`, `web.url` — нет.
    """
    return any(key == token or key.startswith((f"{token}.", f"{token}[")) for key in MAP_KEYS)


def test_every_finding_code_and_key_is_known() -> None:
    """Имя вида `link.calls_invisible` или `web.http_wrappers[].url.arg` — код
    находки из `FINDING_CODES`, пометка ревью или ключ карты `setup-map.md`.

    Коды и ключи живут в одном пространстве имён (`web.undecided` — код,
    `web.roots` — ключ), поэтому и сверяются вместе: опечатка в любом из них
    — красный тест, а не «инструмент не нашёл».
    """
    tokens = sorted(
        {
            span
            for span in ALL_SPANS
            if _DOTTED.fullmatch(span)
            and re.split(r"[.\[]", span)[0] in HEADS
            and span.rsplit(".", 1)[-1] not in FILE_SUFFIXES
        }
    )
    assert any(token in CODES for token in tokens)
    unknown = [
        token
        for token in tokens
        if token not in CODES and token not in NOTES and not _is_key(token)
    ]
    assert not unknown


def test_phase_table_names_codes_of_findings() -> None:
    """Колонка «Находки» таблицы фаз — коды из `FINDING_CODES`."""
    rows = [line for line in BODY.splitlines() if line.startswith("| `phases/")]
    assert len(rows) == len(PHASES)
    codes = {
        span for row in rows for span in _SPAN.findall(row.split("|")[3]) if _CODE.fullmatch(span)
    }
    assert codes
    assert codes <= CODES.keys(), sorted(codes - CODES.keys())


def test_candidate_kinds_and_link_categories_are_known() -> None:
    """`kind: …` у кандидатов, `category: …` и `by: …` у шва — значения из кода."""
    kinds = {span.split(": ", 1)[1] for span in ALL_SPANS if span.startswith("kind: ")}
    kinds |= set(re.findall(r"setup candidates ([a-z-]+)", ALL_TEXT))
    assert kinds
    assert kinds <= set(KINDS), sorted(kinds - set(KINDS))
    categories = {span.split(": ", 1)[1] for span in ALL_SPANS if span.startswith("category: ")}
    assert categories
    assert categories <= set(CATEGORIES), sorted(categories - set(CATEGORIES))
    pairs = re.findall(r"`category: ([a-z_]+)`,?\s+`by: ([a-z_]+)`", _prose(ALL_TEXT))
    assert pairs
    for category, by in pairs:
        assert by in BY_KEYS[category], f"{category}: нет ключа {by!r}"
    langs = {span.split(": ", 1)[1] for span in ALL_SPANS if span.startswith("lang: ")}
    assert langs <= {"cs", "ts"}


def test_referenced_clone_files_exist() -> None:
    """Справочники и инструменты клона, на которые ссылается скилл, существуют."""
    paths = sorted({path for span in ALL_SPANS for path in _CLONE_PATH.findall(span)})
    assert "docs/setup-interview.md" in paths
    assert "docs/setup-map.md" in paths
    missing = [path for path in paths if not Path(path).is_file()]
    assert not missing


# --------------------------------------------------------------------------------------
# Форма: фазы, длины, оболочка
# --------------------------------------------------------------------------------------


def test_phases_are_the_specified_set() -> None:
    assert tuple(sorted(path.name for path in PHASES_DIR.glob("*.md"))) == PHASES


def test_every_phase_is_named_in_the_skill_and_every_named_exists() -> None:
    named = set(_PHASE_REF.findall(BODY))
    on_disk = {path.name for path in PHASES_DIR.glob("*.md")}
    assert named == on_disk, (sorted(named - on_disk), sorted(on_disk - named))


@pytest.mark.parametrize("phase", PHASES)
def test_phase_has_its_blocks_and_a_checkpoint_by_a_tool(phase: str) -> None:
    """Контрольная точка — результат инструмента, а не мнение агента."""
    text = TEXTS[f"phases/{phase}"]
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    missing = [block for block in PHASE_BLOCKS if block not in headings]
    assert not missing, phase
    order = [headings.index(block) for block in PHASE_BLOCKS]
    assert order == sorted(order), f"{phase}: разделы не в порядке {PHASE_BLOCKS}"
    checkpoint = text.split("## Контрольная точка", 1)[1].split("\n## ", 1)[0]
    assert _TOOL.search(checkpoint) or _DOCPIPE.search(checkpoint), phase


def test_lengths_fit() -> None:
    """Тело `SKILL.md` — не больше 250 строк, фаза — не больше 200."""
    assert len(BODY.splitlines()) <= BODY_LIMIT, len(BODY.splitlines())
    for name, text in TEXTS.items():
        if name.startswith("phases/"):
            assert len(text.splitlines()) <= PHASE_LIMIT, name


def test_code_blocks_pass_the_agent_shell() -> None:
    """В блоках кода нет `$(` и обратных кавычек: оболочка gigacode такие
    команды отвергает (сторонний отчёт, проверка на контуре — S32)."""
    for name, text in TEXTS.items():
        for _, body in _blocks(text):
            assert "$(" not in body, name
            assert "`" not in body, name
    assert not [span for span in ALL_SPANS if "docpipe " in span and "$(" in span]


def test_inline_code_does_not_cross_lines() -> None:
    """Вставка `…`, разорванная переводом строки, сбивает разбор всех следующих:
    имена в ней перестали бы сверяться с кодом молча."""
    for name, text in TEXTS.items():
        for number, line in enumerate(_prose(text).splitlines(), start=1):
            assert line.count("`") % 2 == 0, f"{name}:{number}: {line!r}"


_STEP: Final = re.compile(r"^\d+\. ", re.MULTILINE)
_QUESTION: Final = re.compile(r"[Вв]опрос(?:ом)?\s+«")
# Законные формы слова «вопрос» в шагах: вопрос с заголовком раздела каталога
# и отрицания. Всё остальное — «вопрос человеку», «вопрос по разделу»,
# «Вопрос:» — вопрос без кода находки, тот самый, что задал прогон S31.
_ALLOWED: Final = re.compile(r"[Вв]опрос(?:ом)?\s+«|[Вв]опроса нет|[Вв]опросов нет|до вопросов")
_ANY_QUESTION: Final = re.compile(r"[Вв]опрос\w*")
_ASK: Final = re.compile(r"\b[Сс]проси\b")
# Код, по которому строится вопрос: находка `setup status` или новая находка ревью.
QUESTION_CODES: Final = frozenset(CODES) | {"new_findings"}


def _phase_steps(text: str) -> tuple[str, list[str]]:
    """Раздел `## Шаги` одной строкой и его шаги верхнего уровня (перевод строки — пробел)."""
    section = _prose(text.split("\n## Шаги\n", 1)[1].split("\n## ", 1)[0])
    starts = [match.start() for match in _STEP.finditer(section)]
    ends = [*starts[1:], len(section)]
    steps = [" ".join(section[start:end].split()) for start, end in zip(starts, ends, strict=True)]
    return " ".join(section.split()), steps


@pytest.mark.parametrize("phase", PHASES)
def test_every_question_in_phase_steps_names_a_finding_code(phase: str) -> None:
    """Вопрос человеку — только из находки с кодом (S33, п. 11).

    Шаг, где стоит `вопрос «…»`, называет код находки во вставке; других
    форм вопроса и повелительного «спроси» в шагах нет. Без второй половины
    правило обходилось бы словом «спроси» — ровно так фазы 40, 50, 60
    и 90 велели вопросы без кода в прогоне S31. Факт машины (какой
    `docpipe.yaml`, где движок) — «уточни у человека».
    """
    text = TEXTS[f"phases/{phase}"]
    assert "\n## Шаги\n" in text, phase
    section, steps = _phase_steps(text)
    assert steps, phase
    for step in steps:
        if _QUESTION.search(step):
            named = set(_SPAN.findall(step)) & QUESTION_CODES
            assert named, f"{phase}: вопрос без кода находки — {step[:80]!r}"
    rest = _ALLOWED.sub("", section)
    assert not _ANY_QUESTION.findall(rest), f"{phase}: {_ANY_QUESTION.findall(rest)}"
    assert not _ASK.search(section), phase


def test_question_rule_catches_the_old_forms() -> None:
    """Разбор правила вопросов не вырожден: старые формы фаз S29 он ловит."""
    for old in ("спроси человека", "вопрос человеку", "вопрос по разделу", "Вопрос: удалить"):
        assert _ASK.search(old) or _ANY_QUESTION.findall(_ALLOWED.sub("", old)), old
    for fine in ("вопроса нет", "до вопросов", "уже спросили", "вопросом «Маршрут»"):
        assert not _ASK.search(fine) and not _ANY_QUESTION.findall(_ALLOWED.sub("", fine)), fine


def test_skill_states_the_boundary_and_points_at_the_protocol() -> None:
    """Граница S29 п. 1 и запрет дописывать причину (ловушка S28) — в теле,
    протокол вопросов — ссылкой, а не копией каталога."""
    text = BODY.lower()
    assert "пишешь только файлы каталога настройки" in text
    assert "с причиной, которую назвал" in text
    assert "не ходишь в сеть" in text
    assert "не исполняешь код репозитория" in text
    assert "docs/setup-interview.md" in BODY
    copied = [
        line for line in ALL_TEXT.splitlines() if re.match(r"### `?[a-z_]+\.[a-z_]+`?\s*$", line)
    ]
    assert not copied, "каталог вопросов скопирован в скилл"


# --------------------------------------------------------------------------------------
# Примеры YAML
# --------------------------------------------------------------------------------------


def _examples() -> list[tuple[str, str, str]]:
    """Примеры YAML: где лежит, разметка, текст."""
    found: list[tuple[str, str, str]] = []
    for name, text in TEXTS.items():
        for index, (lang, body) in enumerate(_blocks(text)):
            if lang == "yaml":
                first = body.splitlines()[0] if body else ""
                mark = _MARK.fullmatch(first)
                found.append((f"{name}#{index}", mark.group(1) if mark else first, body))
    return found


EXAMPLES: Final = _examples()


def test_every_yaml_example_is_marked() -> None:
    assert EXAMPLES
    marks = [mark for _, mark, _ in EXAMPLES]
    assert set(marks) <= set(MARKS), [mark for mark in marks if mark not in MARKS]
    assert set(marks) == set(MARKS), "каждая разметка проверяется хотя бы одним примером"


def _merge(base: Any, patch: Any) -> Any:
    """Дописать пример к набору: словари — по ключам, списки — в конец, запись
    списка с тем же `id` — заменяет прежнюю (правка существующего правила)."""
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            merged[key] = _merge(base[key], value) if key in base else value
        return merged
    if isinstance(base, list) and isinstance(patch, list):
        merged = list(base)
        index = {
            item["id"]: number
            for number, item in enumerate(base)
            if isinstance(item, dict) and "id" in item
        }
        for item in patch:
            if isinstance(item, dict) and item.get("id") in index:
                merged[index[item["id"]]] = item
            else:
                merged.append(item)
        return merged
    return patch


def _rule_items(document: dict[str, Any], section: str) -> list[dict[str, Any]]:
    body = document.get(section) or {}
    return list(body.get("rules") or []) + list((body.get("exclude") or {}).get("rules") or [])


def _load_rules(path: Path, document: dict[str, Any], section: str) -> None:
    assert set(document) <= {section}, f"пример секции {section} трогает {sorted(document)}"
    base = yaml.safe_load(RULES_BASE.read_text(encoding="utf-8"))
    path.write_text(
        yaml.safe_dump(_merge(base, document), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    ruleset = load_ruleset(path, section)
    loaded = {rule.id for rule in ruleset.rules} | {rule.id for rule in ruleset.exclude.rules}
    given = {item["id"] for item in _rule_items(document, section)}
    assert given <= loaded, sorted(given - loaded)
    # Правка прежнего правила не переписывает его причину: это слова человека.
    before = {item["id"]: item for item in _rule_items(base, section)}
    for item in _rule_items(document, section):
        if item["id"] in before and "reason" in before[item["id"]]:
            assert item.get("reason") == before[item["id"]]["reason"], item["id"]


def _load_config(path: Path, document: dict[str, Any]) -> None:
    load_config(path)


def _load_pages(path: Path, document: dict[str, Any]) -> None:
    load_overrides(path)


def _load_ownership(path: Path, document: dict[str, Any]) -> None:
    load_ownership(path)


def _load_dotnet(path: Path, document: dict[str, Any]) -> None:
    _load_rules(path, document, "dotnet")


def _load_web(path: Path, document: dict[str, Any]) -> None:
    _load_rules(path, document, "web")


LOADERS: Final[dict[str, Callable[[Path, dict[str, Any]], None]]] = {
    "docpipe.yaml": _load_config,
    "rules.yaml#dotnet": _load_dotnet,
    "rules.yaml#web": _load_web,
    "pages.yaml": _load_pages,
    "ownership.yaml": _load_ownership,
}


def test_positive_rule_examples_carry_evidence_as_comment() -> None:
    """У правила вида поля `reason` нет — доказательство идёт комментарием (S33, п. 6).

    `classify._RULE_KEYS`: `id`, `kind`, `template`, `priority`, `when`;
    лишний ключ роняет загрузку набора, а `setup status` без набора теряет
    `dotnet.undecided` целиком — `unexplained` падает и выглядит успехом
    (abp в прогоне S31: 2249 → 1342). Поэтому над каждым элементом `rules:`
    примера — строка `# доказательство:`, а `reason` в нём нет.
    """
    checked = 0
    for where, mark, body in EXAMPLES:
        if not mark.startswith("rules.yaml#"):
            continue
        section = mark.split("#", 1)[1]
        document = yaml.safe_load(body)
        lines = body.splitlines()
        for item in (document.get(section) or {}).get("rules") or []:
            assert "reason" not in item, f"{where}: у правила вида {item['id']} — reason"
            line = re.compile(rf'^\s*- id: "?{re.escape(item["id"])}"?\s*$')
            [index] = [number for number, text in enumerate(lines) if line.match(text)]
            above = lines[index - 1].strip()
            assert above.startswith("# доказательство:"), f"{where}: {item['id']}"
            checked += 1
    assert checked >= 2  # примеры фаз 10 (`dotnet`) и 20 (`web`)


@pytest.mark.parametrize(
    ("where", "mark", "body"), EXAMPLES, ids=[where for where, _, _ in EXAMPLES]
)
def test_yaml_example_passes_its_loader(where: str, mark: str, body: str, tmp_path: Path) -> None:
    """Пример грузится тем же загрузчиком, что и настоящий файл.

    Агент копирует пример буквально: пример, который не грузится, учит
    отказу загрузки, а агент решит, что сломан инструмент.
    """
    document = yaml.safe_load(body)
    assert isinstance(document, dict), where
    path = tmp_path / mark.split("#")[0]
    path.write_text(body, encoding="utf-8")
    LOADERS[mark](path, document)
