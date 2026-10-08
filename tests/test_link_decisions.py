"""Концы шва без пары: секция `link` (S20).

У каждого конца шва без пары — место для решения человека с причиной:
вызов во внешний адрес (`external_targets`), эндпоинт, который зовут извне
(`external_callers`), вызов, который статически не восстановить
(`unresolvable`). Решение уводит конец из «без эндпоинта», «без вызывающего»
и `calls_unresolved` в свою категорию — и только там, где пары и правда нет.

Прогоны — на копии `SeamWorkspace` в `tmp_path`: команда `web link` читает
секцию из `docpipe.yaml`, а писать его в саму фикстуру нельзя. Правила
передаются явно — см. docstring `tests/test_seam_fixture.py` о первой
ступени `resolve_input` в git worktree. Секция `link` на разбор не влияет,
поэтому оба прогона — по одному на модуль, а разные настройки секции
сводятся с ними функцией.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, LinkConfig, load_config, route_pattern
from docpipe.emit import ScanResult
from docpipe.emit import run as run_dotnet
from docpipe.model import Manifest, ParserVersions
from docpipe.setup.context import SetupContext
from docpipe.setup.explain import DecisionRef, PathExplain, explain_path
from docpipe.web.link import LinkReport, build_report, format_report, report_for_settings
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
SERVICES: Final = "frontend/src/app/services"
FEED: Final = f"{SERVICES}/feed.service.ts"
LINKS: Final = f"{SERVICES}/links.service.ts"
INFO: Final = f"{SERVICES}/info.service.ts"
CONTENT: Final = "backend/Seam.Api/Controllers/ContentController.cs"

# Критерии приёмки S20 одним набором: по записи на каждый вид конца.
SECTION: Final[dict[str, Any]] = {
    "external_targets": [{"host": "ext.example.org", "reason": "лента партнёра, не наш бэк"}],
    "external_callers": [{"route": "content/**", "reason": "публичный API для SDK"}],
    "unresolvable": [{"path": "**/links.service.ts", "reason": "гипермедиа: адрес из ответа"}],
}


def _copy_seam(root: Path, section: dict[str, Any] | None = None) -> Path:
    """Копия фикстуры: абсолютный путь к правилам и, если дана, секция `link`."""
    shutil.copytree(SEAM, root)
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(RULES.resolve())
    if section is not None:
        raw["link"] = section
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _copy_seam(tmp_path_factory.mktemp("seam") / "ws", SECTION)


@pytest.fixture(scope="module")
def settings(workspace: Path) -> DocpipeConfig:
    return load_config(workspace / "docpipe.yaml")


@pytest.fixture(scope="module")
def backend(workspace: Path, settings: DocpipeConfig) -> ScanResult:
    return run_dotnet(workspace, settings, load_ruleset(RULES, "dotnet"), None)


@pytest.fixture(scope="module")
def frontend(workspace: Path, settings: DocpipeConfig) -> WebScanResult:
    return run_web(workspace, settings, load_ruleset(RULES, "web"), None)


def _report(backend: ScanResult, frontend: WebScanResult, **section: Any) -> LinkReport:
    link = LinkConfig.model_validate(section)
    return build_report(backend.manifest, frontend.manifest, link=link)


@pytest.fixture(scope="module")
def baseline(backend: ScanResult, frontend: WebScanResult) -> LinkReport:
    """Без секции: точка отсчёта, числа — `tests/test_seam_fixture.LINK_COUNTS`."""
    return build_report(backend.manifest, frontend.manifest)


@pytest.fixture(scope="module")
def decided(backend: ScanResult, frontend: WebScanResult, settings: DocpipeConfig) -> LinkReport:
    """Секция критериев приёмки — тем путём, что у `web link`: от настройки."""
    return report_for_settings(backend.manifest, frontend.manifest, settings)


# --------------------------------------------------------------------------------------
# Критерии приёмки
# --------------------------------------------------------------------------------------


def test_external_host_takes_the_feed_call_out_of_calls_without_endpoint(
    baseline: LinkReport, decided: LinkReport
) -> None:
    [external] = decided.external_targets
    assert (external.file, external.host, external.route) == (FEED, "ext.example.org", "feed.json")
    assert (external.decision.index, external.decision.rule) == (0, "ext.example.org")
    assert external.decision.reason == "лента партнёра, не наш бэк"
    assert external.document is False  # умолчание: внешний адресат — не наш контракт
    assert decided.counts["calls_without_endpoint"] == baseline.counts["calls_without_endpoint"] - 1
    assert FEED not in {item.file for item in decided.calls_without_endpoint}
    assert decided.counts["external_targets"] == 1


def test_external_callers_take_content_endpoints_out_of_without_caller(
    baseline: LinkReport, decided: LinkReport
) -> None:
    def content(items: list[Any]) -> list[tuple[str, str]]:
        return sorted(
            (item.http_method, item.route) for item in items if "ContentController" in item.node
        )

    expected = [("GET", "content/{}/{}"), ("GET", "content/{}/{}/{}"), ("POST", "content/{}/{}")]
    assert content(baseline.endpoints_without_caller) == expected
    assert content(decided.endpoints_without_caller) == []
    assert content(decided.external_callers) == expected
    assert {item.decision.rule for item in decided.external_callers} == {"content/**"}
    assert all(item.document for item in decided.external_callers)  # публичный API — описываем
    assert decided.counts["endpoints_without_caller"] == (
        baseline.counts["endpoints_without_caller"] - 3
    )


def test_hypermedia_is_declared_unresolvable(baseline: LinkReport, decided: LinkReport) -> None:
    """Гипермедиа `fetch` — в `declared_unresolvable`; `follow` разбор не видит вовсе."""
    [declared] = decided.declared_unresolvable
    assert (declared.file, declared.member, declared.expression) == (LINKS, "fetch", "link.href")
    assert declared.decision.reason == "гипермедиа: адрес из ответа"
    assert decided.counts["declared_unresolvable"] == 1
    # `calls_unresolved` — только без решения: вместе с объявленными — все из манифеста.
    assert decided.counts["calls_unresolved"] == baseline.counts["calls_unresolved"] - 1


@pytest.mark.parametrize(
    ("key", "entry"),
    [
        ("external_targets", {"host": "ext.example.org", "reason": ""}),
        ("external_targets", {"route": "feed.json", "reason": "   "}),
        ("external_callers", {"route": "content/**", "reason": ""}),
        ("unresolvable", {"path": "**/links.service.ts", "reason": ""}),
    ],
)
def test_empty_reason_refuses_loading(tmp_path: Path, key: str, entry: dict[str, str]) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text(yaml.safe_dump({"link": {key: [entry]}}), encoding="utf-8")
    with pytest.raises(ValueError, match=f"link.{key}: `reason` пустой"):
        load_config(config)


@pytest.mark.parametrize(
    ("key", "entry", "hint"),
    [
        ("external_targets", {"host": "ext.example.org"}, "нет `reason`"),
        ("external_callers", {"route": "content/**"}, "нет `reason`"),
        # Строка — короткая форма соседних ключей; здесь её нет, и подсказка про запись.
        ("unresolvable", "**/links.service.ts", 'пишите `- path: "**/links.service.ts"`'),
    ],
)
def test_missing_reason_or_short_form_refuses_with_a_hint(
    tmp_path: Path, key: str, entry: Any, hint: str
) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text(yaml.safe_dump({"link": {key: [entry]}}), encoding="utf-8")
    with pytest.raises(ValueError, match=f"link.{key}") as failure:
        load_config(config)
    assert hint in str(failure.value)


# --------------------------------------------------------------------------------------
# Загрузка: опечатка — отказ, а не правило, которое не совпадёт ни с чем
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "message"),
    [
        (
            {"external_targets": [{"host": "x.org", "route": "a", "reason": "r"}]},
            "ровно одно из `host` и `route`",
        ),
        ({"external_targets": [{"reason": "r"}]}, "ровно одно из `host` и `route`"),
        # `WebCall.host` — без схемы и порта: такая маска не совпала бы ни с чем.
        (
            {"external_targets": [{"host": "https://ext.example.org", "reason": "r"}]},
            "только имя хоста",
        ),
        ({"external_targets": [{"host": "ext.example.org:8080", "reason": "r"}]}, "без схемы"),
        ({"external_targets": [{"route": "/", "reason": "r"}]}, "пуст после нормализации"),
        (
            {"external_callers": [{"route": "content/**", "http_method": "GTE", "reason": "r"}]},
            "неизвестный `http_method`",
        ),
        ({"external_callers": [{"route": "", "reason": "r"}]}, "`route` пустой"),
        ({"unresolvable": [{"path": "/abs/x.ts", "reason": "r"}]}, "относительным"),
        ({"unresolvable": [{"path": "a\\b.ts", "reason": "r"}]}, "разделитель"),
        ({"unresolvable": [{"path": "", "reason": "r"}]}, "`path` пустой"),
        ({"unknown": []}, "Extra inputs are not permitted"),
        (
            {"external_callers": [{"route": "content/**", "reason": "r", "documnet": True}]},
            "Extra inputs are not permitted",
        ),
    ],
)
def test_bad_entries_refuse_loading(section: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DocpipeConfig.model_validate({"link": section})


@pytest.mark.parametrize(
    "section",
    [
        {"unresolvable": [{"path": "a/*.ts", "reason": "r"}, {"path": "a/*.ts", "reason": "q"}]},
        # Тот же хост в другом регистре и тот же маршрут в форме атрибута — повтор.
        {
            "external_targets": [
                {"host": "Ext.Example.org", "reason": "r"},
                {"host": "ext.example.org", "reason": "q"},
            ]
        },
        {
            "external_callers": [
                {"route": "content/{app}/**", "http_method": "get", "reason": "r"},
                {"route": "/Content/{x}/**", "http_method": "GET", "reason": "q"},
            ]
        },
    ],
)
def test_repeated_condition_refuses_loading(section: dict[str, Any]) -> None:
    """Сработала бы только первая запись, и правка во второй выглядела бы сделанной."""
    with pytest.raises(ValueError, match="записано дважды"):
        DocpipeConfig.model_validate({"link": section})


def test_same_route_with_other_method_is_not_a_repeat() -> None:
    LinkConfig.model_validate(
        {
            "external_callers": [
                {"route": "content/**", "http_method": "GET", "reason": "r"},
                {"route": "content/**", "http_method": "POST", "reason": "q"},
            ]
        }
    )


def test_commented_out_list_refuses_like_other_sections(tmp_path: Path) -> None:
    """Вложенная секция проверяется на `None` тем же `_reject_empty_lists`, что `web`."""
    config = tmp_path / "docpipe.yaml"
    config.write_text("link:\n  unresolvable:\n    # - path: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`link.unresolvable:` без элементов"):
        load_config(config)


def test_commented_out_section_is_the_default(tmp_path: Path) -> None:
    config = tmp_path / "docpipe.yaml"
    config.write_text("link:\n  # unresolvable: []\n", encoding="utf-8")
    assert load_config(config).link == LinkConfig()


def test_route_pattern_follows_the_route_key() -> None:
    """Маску пишут так, как маршрут в `[Route]`; `?` — знак глоба, а не начало query."""
    assert route_pattern("/Content/{app}/{schema:guid}/**") == "content/{}/{}/**"
    assert route_pattern("api/:id/x") == "api/{}/x"
    assert route_pattern("api//v?/**") == "api/v?/**"


# --------------------------------------------------------------------------------------
# Сопоставление
# --------------------------------------------------------------------------------------


def test_rule_never_touches_a_linked_call(
    backend: ScanResult, frontend: WebScanResult, baseline: LinkReport
) -> None:
    """Связь остаётся связью: правило действует только на конец без пары."""
    report = _report(
        backend,
        frontend,
        external_targets=[{"route": "api/**", "reason": "шлюз"}],
        external_callers=[{"route": "api/info", "reason": "зовёт и мобильный клиент"}],
    )
    assert [(link.route, link.match) for link in report.links] == [("api/info", "exact")]
    assert all(item.route != "api/info" for item in report.external_callers)
    # Без эндпоинта из `api/**` остались `archived` и `search` — они и ушли.
    assert sorted(item.route for item in report.external_targets) == [
        "api/apps/archived",
        "api/apps/search",
    ]
    assert report.counts["linked"] == baseline.counts["linked"]


def test_host_rule_does_not_cover_a_relative_address(
    backend: ScanResult, frontend: WebScanResult
) -> None:
    report = _report(backend, frontend, external_targets=[{"host": "*", "reason": "любой хост"}])
    assert [item.file for item in report.external_targets] == [FEED]


def test_route_mask_is_written_like_the_attribute_and_method_narrows_it(
    backend: ScanResult, frontend: WebScanResult
) -> None:
    report = _report(
        backend,
        frontend,
        external_callers=[
            {"route": "/Content/{app}/{schema}", "http_method": "get", "reason": "SDK читает"}
        ],
    )
    assert [(item.http_method, item.route) for item in report.external_callers] == [
        ("GET", "content/{}/{}")
    ]
    assert report.external_callers[0].decision.rule == "GET /Content/{app}/{schema}"


def test_any_method_endpoint_matches_a_rule_with_a_method(
    backend: ScanResult, frontend: WebScanResult
) -> None:
    """`[Route]` без глагола принимает любой метод — и правило любого метода тоже."""
    report = _report(
        backend,
        frontend,
        external_callers=[{"route": "api/comments/**", "http_method": "GET", "reason": "виджет"}],
    )
    assert [(item.http_method, item.route) for item in report.external_callers] == [
        ("*", "api/comments/{}")
    ]


def test_first_matching_rule_decides(backend: ScanResult, frontend: WebScanResult) -> None:
    report = _report(
        backend,
        frontend,
        external_callers=[
            {"route": "content/{app}/{schema}", "reason": "узкое"},
            {"route": "content/**", "reason": "широкое"},
        ],
    )
    decisions = {(item.route, item.http_method): item.decision for item in report.external_callers}
    assert decisions[("content/{}/{}", "GET")].index == 0
    assert decisions[("content/{}/{}/{}", "GET")].index == 1
    assert decisions[("content/{}/{}/{}", "GET")].reason == "широкое"


def test_decisions_only_move_ends_between_categories(
    baseline: LinkReport, decided: LinkReport, frontend: WebScanResult
) -> None:
    """Решение уводит конец из категории «без пары» в свою — не теряет и не удваивает."""
    counts = decided.counts
    calls = ("linked", "almost", "calls_without_endpoint", "external_targets")
    assert sum(counts[name] for name in calls) == counts["calls_total"]
    assert counts["calls_unresolved"] + counts["declared_unresolvable"] == len(
        frontend.manifest.unresolved_calls
    )

    def places(items: list[Any]) -> list[tuple[str, int]]:
        return sorted((item.file, item.line) for item in items)

    def endpoints(items: list[Any]) -> list[tuple[str, str, str, str]]:
        return sorted((item.node, item.member, item.http_method, item.route) for item in items)

    assert places(decided.calls_without_endpoint + decided.external_targets) == places(
        baseline.calls_without_endpoint
    )
    assert endpoints(decided.endpoints_without_caller + decided.external_callers) == endpoints(
        baseline.endpoints_without_caller
    )
    assert decided.links == baseline.links


def test_report_is_deterministic(
    backend: ScanResult, frontend: WebScanResult, settings: DocpipeConfig, decided: LinkReport
) -> None:
    shuffled_backend = backend.manifest.model_copy(
        update={"nodes": list(reversed(backend.manifest.nodes))}
    )
    shuffled_web = frontend.manifest.model_copy(
        update={
            "nodes": list(reversed(frontend.manifest.nodes)),
            "unresolved_calls": list(reversed(frontend.manifest.unresolved_calls)),
        }
    )
    assert report_for_settings(shuffled_backend, shuffled_web, settings) == decided


def test_text_report_names_the_decided_categories(decided: LinkReport) -> None:
    text = format_report(decided)
    # Первое число — все невосстановленные: решение его не уменьшает, а называет.
    assert "не восстановлено и в связь не идёт ещё 9, из них решено «не восстановить» 1" in text
    assert "Решено секцией `link`:" in text
    assert "      1  вызов во внешний адрес (`link.external_targets`)" in text
    assert "      3  эндпоинт зовут извне (`link.external_callers`)" in text


def test_call_without_endpoint_carries_its_host(baseline: LinkReport) -> None:
    """Без хоста в отчёте правило внешнего адресата не написать, не открыв код."""
    hosts = {item.file: item.host for item in baseline.calls_without_endpoint}
    assert hosts[FEED] == "ext.example.org"
    assert "(хост ext.example.org)" in format_report(baseline)


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_web_link_reads_the_section_from_the_config(
    workspace: Path, decided: LinkReport, tmp_path: Path
) -> None:
    back, web, out = tmp_path / "dt.json", tmp_path / "dt.web.json", tmp_path / "link.json"
    config = workspace / "docpipe.yaml"
    common = ["--root", str(workspace), "--config", str(config), "--no-cache"]
    assert runner.invoke(app, ["scan", *common, "--out", str(back)]).exit_code == 0
    assert runner.invoke(app, ["web", "scan", *common, "--out", str(web)]).exit_code == 0

    result = runner.invoke(
        app, ["web", "link", str(back), str(web), "--config", str(config), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.4"
    assert payload["counts"] == decided.counts
    assert [item["decision"]["rule"] for item in payload["declared_unresolvable"]] == [
        "**/links.service.ts"
    ]


def test_decided_categories_are_not_fail_on_categories(tmp_path: Path) -> None:
    """Решение человека — не находка: ронять прогон за него нечего."""
    empty = Manifest(ruleset_version="1", parser=ParserVersions(tree_sitter="0"))
    manifest = tmp_path / "empty.json"
    manifest.write_text(empty.model_dump_json(), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "web",
            "link",
            str(manifest),
            str(manifest),
            "--out",
            str(tmp_path / "link.json"),
            "--fail-on",
            "external_targets",
        ],
    )
    assert result.exit_code == 2
    assert "Неизвестные категории в --fail-on: external_targets" in result.output


# --------------------------------------------------------------------------------------
# `setup explain`: правила `link`, которые касаются цели
# --------------------------------------------------------------------------------------


def _explain(workspace: Path, settings: DocpipeConfig, target: str) -> tuple[PathExplain, Any]:
    context = SetupContext(workspace, settings, use_cache=False)
    return explain_path(context, target), vars(context)


def _link_decisions(report: PathExplain) -> list[DecisionRef]:
    return [ref for ref in report.decisions if ref.key.startswith("link.")]


def test_explain_names_unresolvable_without_running_step_1(
    workspace: Path, settings: DocpipeConfig
) -> None:
    """Невосстановленному вызову сведение не нужно — и шагу 1 незачем идти."""
    report, cached = _explain(workspace, settings, LINKS)
    [ref] = _link_decisions(report)
    assert (ref.key, ref.value, ref.reason, ref.count) == (
        "link.unresolvable",
        "**/links.service.ts",
        "гипермедиа: адрес из ответа",
        1,
    )
    assert "scan" not in cached and "link" not in cached


def test_explain_names_the_external_target_of_the_file(
    workspace: Path, settings: DocpipeConfig
) -> None:
    report, _ = _explain(workspace, settings, FEED)
    [ref] = _link_decisions(report)
    assert (ref.key, ref.value, ref.count) == ("link.external_targets", "ext.example.org", 1)
    assert ref.effect == "вызов во внешний адрес; документировать: нет"


def test_explain_names_external_callers_of_the_controller(
    workspace: Path, settings: DocpipeConfig
) -> None:
    report, _ = _explain(workspace, settings, CONTENT)
    [ref] = _link_decisions(report)
    assert (ref.key, ref.value, ref.reason, ref.count) == (
        "link.external_callers",
        "content/**",
        "публичный API для SDK",
        3,
    )


def test_explain_skips_a_rule_that_decided_nothing_here(
    workspace: Path, settings: DocpipeConfig
) -> None:
    """Совпало — не значит решило: связанный `api/info` правилом не тронут."""
    wide = settings.model_copy(
        update={
            "link": LinkConfig.model_validate(
                {"external_targets": [{"route": "api/**", "reason": "шлюз"}]}
            )
        }
    )
    report, cached = _explain(workspace, wide, INFO)
    assert _link_decisions(report) == []
    # Правило совпало с вызовом — значит, сведение было нужно, чтобы это понять.
    assert "link" in cached


def test_explain_without_link_rules_does_not_build_the_link(
    workspace: Path, settings: DocpipeConfig
) -> None:
    bare = settings.model_copy(update={"link": LinkConfig()})
    report, cached = _explain(workspace, bare, FEED)
    assert _link_decisions(report) == []
    assert "scan" not in cached and "link" not in cached
