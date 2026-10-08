"""Сервер настройки `docpipe setup serve` (S27).

Тесты держат то, что ломается молча: ответ длиннее порога обрезки агента
читается как полный; правка настройки между вызовами не видна, если сервер
помнит прогон; сервер, падающий без `docpipe.yaml`, оставляет агента без
инструментов там, где настройку только начинают; инструмент без CLI-двойника
— вторая реализация. Кэш выключен везде: иначе он лёг бы в `.docpipe/` фикстуры.
"""

import io
import json
import shutil
from pathlib import Path
from typing import Any, Final

import pytest
import typer
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.mcp import serve, tool_text
from docpipe.setup import server as server_module
from docpipe.setup.candidates import candidates
from docpipe.setup.context import SetupContext
from docpipe.setup.server import (
    CLI_TWIN,
    CONFIG_MISSING,
    CONFIG_UNREADABLE,
    MAX_CHARS,
    MAX_LINES,
    SERVER_NAME,
    TOOLS,
    SetupTools,
    fit,
)
from docpipe.setup.status import SetupStatus
from docpipe.web import pages as pages_module

runner = CliRunner()

FIXTURES: Final = Path(__file__).parent / "fixtures"
SAMPLE: Final = FIXTURES / "SampleSolution"
WEB: Final = FIXTURES / "WebWorkspace"
RULES: Final = Path("rules/rules.yaml")

# Правило отсева на `Program` — то же, что у тестов `setup status` (S24).
PROGRAM_RULE: Final[dict[str, Any]] = {
    "id": "entry.program",
    "reason": "точка входа: конфигурация хоста, а не контракт",
    "when": {"name_regex": ["^Program$"]},
}


def rpc(request_id: int, method: str, **params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def call(request_id: int, name: str, **arguments: Any) -> dict[str, Any]:
    return rpc(request_id, "tools/call", name=name, arguments=arguments)


def loop(toolset: SetupTools, *requests: dict[str, Any]) -> list[dict[str, Any]]:
    """Прогнать запросы через цикл stdio и вернуть ответы по строкам."""
    source = io.StringIO("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests))
    sink = io.StringIO()
    serve(toolset, source, sink)
    return [json.loads(line) for line in sink.getvalue().splitlines()]


def text_of(answer: dict[str, Any]) -> dict[str, Any]:
    """Ответ инструмента из `tools/call`: тело текстом JSON."""
    content = answer["result"]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    parsed: dict[str, Any] = json.loads(content[0]["text"])
    return parsed


def sample(**kwargs: Any) -> SetupTools:
    return SetupTools(SAMPLE, None, use_cache=False, **kwargs)


def sample_copy(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Копия `SampleSolution` с `docpipe.yaml` и своей копией набора правил."""
    root = tmp_path / "repo"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".docpipe"))
    rules = tmp_path / "rules.yaml"
    shutil.copyfile(RULES, rules)
    config = tmp_path / "docpipe.yaml"
    config.write_text(yaml.safe_dump({"rules": str(rules)}), encoding="utf-8")
    return root, config, rules


def add_exclusion(rules: Path, rule: dict[str, Any]) -> None:
    raw = yaml.safe_load(rules.read_text(encoding="utf-8"))
    raw["dotnet"]["exclude"].setdefault("rules", []).append(rule)
    rules.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


def finding(report: dict[str, Any], code: str) -> dict[str, Any] | None:
    return next((item for item in report["findings"] if item["code"] == code), None)


def fits(answer: dict[str, Any]) -> bool:
    text = tool_text(answer)
    return len(text) <= MAX_CHARS and text.count("\n") < MAX_LINES


# --------------------------------------------------------------------------------------
# Инструменты и их CLI-двойники
# --------------------------------------------------------------------------------------


def _command(path: str) -> Any:
    """Команда приложения по пути `setup status`; флаги после пути — её параметры.

    Через атрибуты, а не типы click: typer 0.27 несёт свой click внутри,
    и отдельного пакета `click` в окружении нет.
    """
    words = path.split()
    names = [word for word in words if not word.startswith("--")]
    command: Any = typer.main.get_command(app)
    for name in names:
        commands = getattr(command, "commands", None)
        assert commands is not None, f"{path}: «{name}» — не в группе"
        assert name in commands, f"{path}: команды «{name}» нет в приложении"
        command = commands[name]
    assert getattr(command, "commands", None) is None, f"{path}: это группа, а не команда"
    options = {option for param in command.params for option in param.opts}
    for flag in (word for word in words if word.startswith("--")):
        assert flag in options, f"{path}: у команды нет флага {flag}"
    return command


def test_every_tool_has_a_cli_twin_and_every_twin_is_a_command() -> None:
    """Инструмент без двойника — вторая реализация; двойник без команды — опечатка."""
    names = [tool["name"] for tool in sample().tools()]
    assert len(names) == len(set(names))
    assert set(names) == set(CLI_TWIN)
    for twins in CLI_TWIN.values():
        assert twins
        for twin in twins:
            _command(twin)


def test_the_serve_command_is_registered_with_the_flags_of_the_installer() -> None:
    """`install.sh` (S30) прописывает `setup serve --config … --root …`: имена обязаны совпасть."""
    command = _command("setup serve --config --root --no-cache")
    assert command.name == "serve"
    installer = Path("deploy/install.sh").read_text(encoding="utf-8")
    assert '"setup", "serve",' in installer
    assert installer.count('"--config", "$(json_escape "$config_path")"') == 2


def test_tool_schemas_are_closed_and_consistent() -> None:
    for tool in sample().tools():
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert "CLI-двойник" in tool["description"]
        for twin in CLI_TWIN[tool["name"]]:
            assert f"`docpipe {twin}`" in tool["description"]
        for name, prop in schema["properties"].items():
            if "enum" in prop and "default" in prop:
                assert prop["default"] in prop["enum"], (tool["name"], name)


def test_initialize_names_the_setup_server_and_its_rules() -> None:
    answer = loop(sample(), rpc(1, "initialize", protocolVersion="2025-06-18"))[0]["result"]
    assert answer["serverInfo"]["name"] == SERVER_NAME == "docpipe-setup"
    instructions = answer["instructions"]
    for phrase in ("ничего не пишет", "ещё раз", "next_offset", "limit", "offset"):
        assert phrase in instructions
    # Коротко: инструкцию агент читает при каждом подключении.
    assert len(instructions) < 1500


def test_note_codes_cover_every_page_note() -> None:
    """Код новой заметки `web pages` обязан попасть в `NOTE_CODES`, иначе её не отобрать."""
    notes = {
        value
        for name, value in vars(pages_module).items()
        if name.startswith("NOTE_") and name != "NOTE_CODES" and isinstance(value, str)
    }
    assert notes == set(pages_module.NOTE_CODES.values())
    assert len(pages_module.NOTE_CODES) == len(notes)


# --------------------------------------------------------------------------------------
# Бюджет ответа
# --------------------------------------------------------------------------------------


def synthetic(count: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "total": count,
        "items": [
            {"name": f"Item{index:05d}", "members": ["Run", "Stop"], "text": "x" * 40}
            for index in range(count)
        ],
    }


def test_small_answer_is_returned_untouched() -> None:
    answer = synthetic(3)
    assert fit(answer, paged="items") is answer


def test_long_list_is_paged_within_the_budget() -> None:
    first = fit(synthetic(5000), paged="items")
    assert fits(first)
    assert first["truncated"] is True
    assert first["truncated_lists"] == {"items": 5000}
    shown = len(first["items"])
    assert 1 < shown < 5000
    assert first["next_offset"] == shown
    # Вторая страница продолжает первую: тот же ответ со сдвигом.
    rest = synthetic(5000)
    rest["items"] = rest["items"][shown:]
    second = fit(rest, paged="items", offset=shown)
    assert second["items"][0]["name"] == f"Item{shown:05d}"
    assert second["next_offset"] == shown + len(second["items"])
    # Детерминизм: тот же вход — тот же ответ байт в байт.
    assert tool_text(fit(synthetic(5000), paged="items")) == tool_text(first)


def test_page_never_comes_out_empty() -> None:
    """Элемент длиннее бюджета — страница из одного, урезанного изнутри; `next_offset` идёт."""
    huge = {"items": [{"name": "big", "members": [f"m{i:05d}" for i in range(9000)]}] * 2}
    answer = fit(huge, paged="items", offset=7)
    assert fits(answer)
    assert len(answer["items"]) == 1
    assert answer["next_offset"] == 8
    assert answer["truncated_lists"] == {"items": 2, "items.*.members": 9000}


def test_containers_keep_their_items_and_families_share_one_cap() -> None:
    """Урезаются примеры внутри блоков, а не сами блоки; у всех примеров одна граница."""
    blocks = [
        {
            "id": f"block{index}",
            "candidates": [
                {"path": f"file{index}-{item}", "examples": [f"пример {n} " * 4 for n in range(30)]}
                for item in range(12)
            ],
        }
        for index in range(4)
    ]
    answer = fit({"blocks": blocks})
    assert fits(answer)
    assert [block["id"] for block in answer["blocks"]] == [f"block{i}" for i in range(4)]
    assert "blocks" not in answer["truncated_lists"]
    lengths = {
        len(candidate["examples"])
        for block in answer["blocks"]
        for candidate in block["candidates"]
    }
    assert len(lengths) == 1 and 0 < lengths.pop() < 30
    assert answer["next_offset"] is None


def test_lines_are_a_budget_too() -> None:
    """Список коротких строк упирается в порог строк агента раньше, чем в символы."""
    answer = fit({"names": [f"n{index}" for index in range(5000)]})
    text = tool_text(answer)
    assert len(text) < MAX_CHARS // 2
    assert text.count("\n") < MAX_LINES
    assert answer["truncated_lists"] == {"names": 5000}


def test_answer_without_lists_that_does_not_fit_says_so() -> None:
    """Урезать нечего — ответ ошибкой, а не текстом, который агент обрежет молча."""
    answer = fit({"text": "x" * (MAX_CHARS + 1)})
    assert answer["truncated"] is True
    assert "сузьте запрос" in answer["error"]


def test_neighbours_of_the_page_keep_their_share() -> None:
    """Сосед страницы урезается до своей доли, и страница не выходит в один элемент."""
    answer = {
        "pages": [{"title": f"страница {index}", "text": "y" * 200} for index in range(400)],
        "not_pages": [{"title": f"компонент {index}", "text": "z" * 200} for index in range(400)],
    }
    page = fit(answer, paged="pages")
    assert fits(page)
    assert len(page["pages"]) > 10
    assert 0 < len(page["not_pages"]) < 400
    assert page["truncated_lists"] == {"not_pages": 400, "pages": 400}


# --------------------------------------------------------------------------------------
# Синтетика: 5 000 символов C#
# --------------------------------------------------------------------------------------


def synthetic_solution(root: Path, files: int = 50, per_file: int = 100) -> list[str]:
    """Решение из одного проекта на `files * per_file` классов; FQN по порядку."""
    project = root / "src" / "Synthetic"
    project.mkdir(parents=True)
    (project / "Synthetic.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    names = []
    for part in range(files):
        lines = [f"namespace Synthetic.Part{part:02d};", ""]
        for item in range(per_file):
            name = f"Widget{part * per_file + item:05d}"
            lines.append(f"public class {name} {{ public void Run() {{ }} }}")
            names.append(f"Synthetic.Part{part:02d}.{name}")
        (project / f"Part{part:02d}.cs").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(names)


def test_symbols_of_5000_symbols_come_in_pages(tmp_path: Path) -> None:
    names = synthetic_solution(tmp_path)
    assert len(names) == 5000
    tools = SetupTools(tmp_path, None, use_cache=False)

    first = tools.call("setup_symbols", {"state": "any", "limit": 0})
    assert len(tool_text(first)) <= MAX_CHARS
    assert first["truncated"] is True and first["total"] == 5000
    shown = [row["fqn"] for row in first["symbols"]]
    assert first["next_offset"] == len(shown)
    assert shown == names[: len(shown)]

    second = tools.call("setup_symbols", {"state": "any", "limit": 0, "offset": len(shown)})
    assert len(tool_text(second)) <= MAX_CHARS
    following = [row["fqn"] for row in second["symbols"]]
    assert following == names[len(shown) : len(shown) + len(following)]
    assert second["next_offset"] == len(shown) + len(following)


# --------------------------------------------------------------------------------------
# Цикл stdio на SampleSolution
# --------------------------------------------------------------------------------------


def test_stdio_loop_on_sample_solution_gives_a_setup_status() -> None:
    answers = loop(
        sample(),
        rpc(1, "initialize", protocolVersion="2025-06-18"),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        rpc(2, "tools/list"),
        call(3, "setup_status"),
    )
    assert [answer["id"] for answer in answers] == [1, 2, 3]
    names = [tool["name"] for tool in answers[1]["result"]["tools"]]
    assert names == [tool.name for tool in TOOLS]
    assert answers[2]["result"]["isError"] is False
    report = SetupStatus.model_validate(text_of(answers[2]))
    undecided = next(item for item in report.findings if item.code == "dotnet.undecided")
    assert undecided.count == 1 and undecided.previous is None


def test_rule_edit_between_calls_changes_the_answer(tmp_path: Path) -> None:
    """Контекст собирается на каждый вызов; база `previous` — прошлый ответ сервера."""
    root, config, rules = sample_copy(tmp_path)
    tools = SetupTools(root, config, use_cache=False)

    before = tools.call("setup_status", {})
    assert finding(before, "dotnet.undecided")["count"] == 1  # type: ignore[index]

    add_exclusion(rules, PROGRAM_RULE)
    after = tools.call("setup_status", {})
    closed = finding(after, "dotnet.undecided")
    assert closed is not None and closed["count"] == 0 and closed["previous"] == 1
    covered = next(item for item in after["coverage"] if item["value"] == "entry.program")
    assert covered["count"] == 1 and covered["previous"] is None
    assert after["unexplained"] == before["unexplained"] - 1

    # Без базы — сравнения нет, и прошлая находка не возвращается строкой с нулём.
    plain = tools.call("setup_status", {"baseline": "none"})
    assert finding(plain, "dotnet.undecided") is None
    assert all(item["previous"] is None for item in plain["coverage"])


def test_later_pages_of_status_compare_with_the_base_of_the_first(tmp_path: Path) -> None:
    """Разница правки видна на всех страницах ответа, а не только на первой."""
    root, config, rules = sample_copy(tmp_path)
    tools = SetupTools(root, config, use_cache=False)
    tools.call("setup_status", {})
    add_exclusion(rules, PROGRAM_RULE)
    first = tools.call("setup_status", {})
    second = tools.call("setup_status", {"offset": 1})
    assert len(first["findings"]) > 1
    assert second["findings"] == first["findings"][1:]


def test_server_without_docpipe_yaml_starts_and_says_config_missing(tmp_path: Path) -> None:
    for config in (None, tmp_path / "docpipe.yaml"):
        tools = SetupTools(SAMPLE, config, use_cache=False)
        answers = loop(
            tools,
            rpc(1, "initialize"),
            call(2, "setup_config_check"),
            call(3, "setup_status"),
        )
        assert answers[0]["result"]["serverInfo"]["name"] == SERVER_NAME
        check = text_of(answers[1])
        assert check["status"] == CONFIG_MISSING
        assert check["config"] == (config.as_posix() if config else None)
        assert answers[1]["result"]["isError"] is False
        # Остальные — на умолчаниях, как CLI без `--config`.
        assert answers[2]["result"]["isError"] is False
        SetupStatus.model_validate(text_of(answers[2]))


def test_cli_serve_starts_without_docpipe_yaml(tmp_path: Path) -> None:
    lines = "".join(
        json.dumps(item) + "\n" for item in (rpc(1, "initialize"), call(2, "setup_config_check"))
    )
    missing = tmp_path / "docpipe.yaml"
    result = runner.invoke(
        app,
        ["setup", "serve", "--root", str(SAMPLE), "--config", str(missing), "--no-cache"],
        input=lines,
    )
    assert result.exit_code == 0, result.output
    answers = [json.loads(line) for line in result.stdout.splitlines()]
    assert [answer["id"] for answer in answers] == [1, 2]
    assert text_of(answers[1])["status"] == CONFIG_MISSING
    assert "умолчаниях" in result.stderr


def test_unreadable_config_is_a_diagnosis_for_check_and_an_error_elsewhere(
    tmp_path: Path,
) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text("rules: [незакрытый\n", encoding="utf-8")
    tools = SetupTools(SAMPLE, config, use_cache=False)
    answers = loop(tools, call(1, "setup_config_check"), call(2, "setup_status"))
    check = text_of(answers[0])
    assert check["status"] == CONFIG_UNREADABLE and answers[0]["result"]["isError"] is False
    assert answers[1]["result"]["isError"] is True
    assert "YAML" in text_of(answers[1])["error"]


def test_config_check_with_a_config_is_the_cli_report(tmp_path: Path) -> None:
    _, config, _ = sample_copy(tmp_path)
    result = runner.invoke(
        app, ["config", "check", "--config", str(config), "--root", str(SAMPLE), "--format", "json"]
    )
    answer = SetupTools(SAMPLE, config, use_cache=False).call("setup_config_check", {})
    assert answer == json.loads(result.stdout)


# --------------------------------------------------------------------------------------
# Тот же ответ, что у CLI-двойника
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "command"),
    [
        ("setup_status", {"baseline": "none", "limit": 20}, ["setup", "status"]),
        ("setup_stats", {}, ["scan", "--stats"]),
        ("setup_stats", {"lang": "ts"}, ["web", "scan", "--stats"]),
        ("setup_symbols", {"state": "any", "limit": 0}, ["symbols", "--state", "any"]),
        (
            "setup_symbols",
            {"state": "any", "limit": 2, "offset": 3},
            ["symbols", "--state", "any", "--limit", "2", "--offset", "3"],
        ),
        (
            "setup_explain",
            {"path": "src/Sample.Pricing.Api/Services"},
            ["setup", "explain", "src/Sample.Pricing.Api/Services"],
        ),
        ("setup_link", {}, ["setup", "link"]),
    ],
)
def test_tool_answers_what_its_cli_twin_prints(
    tool: str, arguments: dict[str, Any], command: list[str]
) -> None:
    result = runner.invoke(app, [*command, "--root", str(SAMPLE), "--format", "json", "--no-cache"])
    assert result.exit_code == 0, result.output
    assert sample().call(tool, arguments) == json.loads(result.stdout)


def test_candidates_answer_what_the_cli_prints() -> None:
    """У `setup candidates` флага `--no-cache` нет: двойник зовётся на копии без кэша."""
    answer = sample().call("setup_candidates", {"kind": "di-methods"})
    expected = candidates("di-methods", SetupContext.build(SAMPLE, None, use_cache=False))
    assert answer == expected.model_dump(mode="json")


def test_docs_answer_what_docs_status_prints(tmp_path: Path) -> None:
    manifest = tmp_path / "doc-tree.json"
    scanned = runner.invoke(
        app, ["scan", "--root", str(SAMPLE), "--out", str(manifest), "--no-cache"]
    )
    assert scanned.exit_code == 0, scanned.output
    result = runner.invoke(
        app, ["docs", "status", str(manifest), "--root", str(SAMPLE), "--format", "json"]
    )
    assert sample().call("setup_docs", {"limit": 0}) == json.loads(result.stdout)


def test_docs_explain_answers_what_the_cli_prints(tmp_path: Path) -> None:
    manifest = tmp_path / "doc-tree.json"
    runner.invoke(app, ["scan", "--root", str(SAMPLE), "--out", str(manifest), "--no-cache"])
    target = sample().call("setup_docs", {"limit": 1})["documents"][0]["doc_path"]
    result = runner.invoke(
        app,
        ["docs", "explain", str(manifest), target, "--root", str(SAMPLE), "--format", "json"],
    )
    assert sample().call("setup_docs_explain", {"path": target}) == json.loads(result.stdout)


def test_pages_filter_by_note_and_page() -> None:
    tools = SetupTools(WEB, None, use_cache=False)
    every = tools.call("setup_pages", {"limit": 0})
    assert every["pages_total"] == len(every["pages"]) == every["counts"]["pages"]
    window = tools.call("setup_pages", {"limit": 2, "offset": 1})
    assert window["pages"] == every["pages"][1:3]
    assert window["pages_total"] == every["pages_total"]
    for code, note in pages_module.NOTE_CODES.items():
        noted = tools.call("setup_pages", {"note": code, "limit": 0})
        assert noted["pages"] == [page for page in every["pages"] if note in page["notes"]]
        assert noted["counts"] == every["counts"]
    cli = runner.invoke(app, ["web", "pages", "--help"])
    assert "--note" in cli.stdout


def test_docs_filter_by_status() -> None:
    every = sample().call("setup_docs", {"limit": 0})
    statuses = {doc["status"] for doc in every["documents"]}
    assert statuses
    for status in sorted(statuses):
        only = sample().call("setup_docs", {"status": status, "limit": 0})
        assert only["documents"] == [doc for doc in every["documents"] if doc["status"] == status]
        assert only["total"] == len(only["documents"])


def test_recon_gives_the_setup_blocks_and_full_project_lists() -> None:
    answer = sample().call("setup_recon", {})
    assert [block["id"] for block in answer["blocks"]] == [
        "composition",
        "registries",
        "seams",
        "limits",
    ]
    composition = answer["blocks"][0]["data"]
    assert all("paths" not in row for row in composition["build_files"])
    assert (
        "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"
        in (composition["projects"]["dotnet_projects"])
    )


# --------------------------------------------------------------------------------------
# Ошибки: ответ, а не конец сервера
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("setup_symbols", {"limit": "abc"}, "limit — не целое число"),
        ("setup_symbols", {"limit": -1}, "limit не бывает отрицательным"),
        ("setup_symbols", {"lang": "py"}, "lang: 'py'; допустимы: cs, ts"),
        ("setup_symbols", {"ofset": 1}, "неизвестные аргументы ofset"),
        ("setup_candidates", {}, "нет обязательного аргумента kind"),
        ("setup_link", {"category": "almost", "by": "host"}, "у almost допустимы"),
        ("setup_status", {"baseline": "вчера"}, "baseline"),
        ("setup_выдумка", {}, "tools/list"),
    ],
)
def test_bad_arguments_are_error_answers(
    name: str, arguments: dict[str, Any], message: str
) -> None:
    answers = loop(sample(), call(1, name, **arguments), rpc(2, "ping"))
    assert answers[0]["result"]["isError"] is True
    assert message in text_of(answers[0])["error"]
    assert answers[1]["result"] == {}


def test_integer_given_as_a_string_is_accepted() -> None:
    answer = sample().call("setup_symbols", {"state": "any", "limit": "2"})
    assert answer["shown"] == 2


def test_exception_inside_a_tool_is_an_error_and_the_loop_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("сломалось внутри")

    monkeypatch.setattr(server_module, "build_status", broken)
    answers = loop(sample(), call(1, "setup_status"), call(2, "setup_config_check"))
    assert answers[0]["result"]["isError"] is True
    error = text_of(answers[0])["error"]
    assert "внутренняя ошибка инструмента setup_status" in error and "сломалось внутри" in error
    assert answers[1]["result"]["isError"] is False


def test_print_inside_a_tool_does_not_break_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdout — канал протокола: печать функции, общей с CLI, уходит в stderr."""
    original = server_module.build_status

    def noisy(*args: Any, **kwargs: Any) -> Any:
        print("шум из общей с CLI функции")
        return original(*args, **kwargs)

    monkeypatch.setattr(server_module, "build_status", noisy)
    lines = "".join(json.dumps(item) + "\n" for item in (call(1, "setup_status"), rpc(2, "ping")))
    result = runner.invoke(
        app, ["setup", "serve", "--root", str(SAMPLE), "--no-cache"], input=lines
    )
    assert result.exit_code == 0, result.output
    answers = [json.loads(line) for line in result.stdout.splitlines()]
    assert [answer["id"] for answer in answers] == [1, 2]
    assert answers[0]["result"]["isError"] is False
    assert "шум из общей с CLI функции" in result.stderr
    assert "шум" not in result.stdout


def test_serve_refuses_a_missing_root(tmp_path: Path) -> None:
    result = runner.invoke(app, ["setup", "serve", "--root", str(tmp_path / "нет")])
    assert result.exit_code == 2


def test_symbols_cli_pages_with_offset() -> None:
    every = runner.invoke(
        app,
        ["symbols", "--root", str(SAMPLE), "--state", "any", "--format", "json", "--no-cache"],
    )
    window = runner.invoke(
        app,
        [
            "symbols",
            "--root",
            str(SAMPLE),
            "--state",
            "any",
            "--format",
            "json",
            "--no-cache",
            "--offset",
            "2",
            "--limit",
            "3",
        ],
    )
    full, page = json.loads(every.stdout), json.loads(window.stdout)
    assert page["symbols"] == full["symbols"][2:5]
    assert page["total"] == full["total"] and page["shown"] == 3
    negative = runner.invoke(app, ["symbols", "--root", str(SAMPLE), "--offset", "-1"])
    assert negative.exit_code == 2
