"""Кандидаты в `web.registry_calls` (S13): вопрос строится из литералов вызовов.

`api/items/query` с `listInnerName: 'users'` и `'models'` — один эндпоинт
платформы на много смыслов, и ключ «метод + маршрут» склеивает такие
обращения в одну точку (CLAUDE.md, «Один маршрут на много смыслов»).
Имя поля-различителя у каждой платформы своё, умолчанием его не задать;
предложить его обязан не агент по догадке, а инструмент по фактам: какое
поле у одного маршрута меняется от вызова к вызову.

Основная проверка — на `WebWorkspace` (`items.service.ts` с обеими формами,
`audit.service.ts` с повтором маршрута без литералов). Формы, которых там
нет, — инлайном через `scan_calls`: дополнять фикстуру нельзя, на её числа
завязаны тесты страниц.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.config import DocpipeConfig, RegistryCallConfig
from docpipe.setup.candidates import (
    KINDS,
    REGISTRY_LIMITS,
    CandidateInputs,
    RegistryCallCandidate,
    RegistryCallCandidates,
    candidates,
    candidates_json,
    format_candidates,
    registry_call_candidates,
    registry_rule_text,
)
from docpipe.web.calls import CallScan, build_calls, query_parameters, scan_calls
from docpipe.web.tree import WebScanResult, run

runner = CliRunner()

ITEMS = "src/app/shared/services/items.service.ts"
AUDIT = "src/app/shared/services/audit.service.ts"

BODY_RULE = {"route": "api/items/query", "discriminator": {"in": "body", "name": "listInnerName"}}
QUERY_RULE = {"route": "api/items", "discriminator": {"in": "query", "name": "listInnerName"}}


def _settings(**web: Any) -> DocpipeConfig:
    return DocpipeConfig.model_validate({"web": web})


def _report(scan: WebScanResult | CallScan, settings: DocpipeConfig) -> RegistryCallCandidates:
    calls = scan.calls if isinstance(scan, WebScanResult) else scan
    return registry_call_candidates(calls, settings, limit=0)


def _keys(report: RegistryCallCandidates) -> list[tuple[str, str, str, str]]:
    return [(c.http_method, c.route, c.where, c.name) for c in report.items]


def _one(report: RegistryCallCandidates, route: str) -> RegistryCallCandidate:
    [found] = [item for item in report.items if item.route == route]
    return found


def _inline(source: str, module: str = "app") -> CallScan:
    """Вызовы одного файла, построенные без настройки."""
    return build_calls(scan_calls(source.encode(), "src/a.service.ts"), module=module)


def _copy(web_workspace: Path, tmp_path: Path) -> Path:
    """Копия фикстуры: команда пишет кэш разбора под `--root`."""
    root = tmp_path / "ws"
    shutil.copytree(web_workspace, root)
    return root


# --------------------------------------------------------------------------------------
# Пара «факт → ключ»
# --------------------------------------------------------------------------------------


def test_resolved_pairs_each_call_with_its_fact(web_workspace: Path) -> None:
    """Query-строка и поля тела живут только в факте: ключ их уже потерял."""
    scan = run(web_workspace, DocpipeConfig()).calls
    assert len(scan.resolved) == len(scan.calls)
    # Тот же объект, а не копия до приписки члена: иначе один вызов в двух
    # списках читался бы как два.
    assert all(item.call is call for item, call in zip(scan.resolved, scan.calls, strict=True))
    assert all(item.call.member for item in scan.resolved if item.raw.file == ITEMS)

    by_line = {item.raw.line: item for item in scan.resolved if item.raw.file == ITEMS}
    assert by_line[31].raw.url == "api/items?listInnerName=dictionaries"
    assert by_line[31].call.key.route == "api/items"
    assert by_line[15].raw.body_fields == {"listInnerName": "users"}
    assert by_line[15].raw.body_nonliteral == ("fields",)
    assert {item.module for item in by_line.values()} == {"tr-p"}


def test_body_nonliteral_names_fields_with_an_expression() -> None:
    [call] = scan_calls(
        b"""
export class A {
  run(name: string, k: string, rest: object) {
    this.http.post('api/x', { a: 'lit', b: name, c, 'd': 1, [k]: 'z', ...rest, e() {} });
  }
}
""",
        "src/a.ts",
    )
    assert call.body_fields == {"a": "lit", "[k]": "z"}
    # Вычисляемый ключ, спред и метод имени поля не дают.
    assert call.body_nonliteral == ("b", "c", "d")


def test_query_parameters_split_literals_and_substitutions() -> None:
    literal, substituted = query_parameters("api/x?a=1&b={}&c=&d&{}=2&a=3&e={}&e=5")
    # Первый литерал — как у различителя прогона; имя с литералом из
    # подстановок выпадает.
    assert literal == {"a": "1", "e": "5"}
    assert substituted == frozenset({"b"})
    assert query_parameters("api/x") == ({}, frozenset())


# --------------------------------------------------------------------------------------
# Кандидаты на фикстуре
# --------------------------------------------------------------------------------------


def test_items_service_gives_body_and_query_candidates(web_workspace: Path) -> None:
    report = _report(run(web_workspace, DocpipeConfig()), DocpipeConfig())
    assert _keys(report) == [
        ("POST", "api/items/query", "body", "listInnerName"),
        ("GET", "api/items", "query", "listInnerName"),
    ]

    body = _one(report, "api/items/query")
    assert (body.values, body.values_total, body.nonliteral, body.calls) == (
        ["models", "users"],
        2,
        0,
        2,
    )
    # Значение-подстановка (`byType(type)`) — второе значение рядом
    # с литералом: обёртка над реестром и есть главная его форма.
    query = _one(report, "api/items")
    assert (query.values, query.values_total, query.nonliteral, query.calls) == (
        ["dictionaries"],
        1,
        1,
        2,
    )
    assert all(item.modules == ["tr-p"] for item in report.items)
    assert all(item.configured is False for item in report.items)
    assert query.examples == [f"{ITEMS}:31", f"{ITEMS}:37"]


def test_repeated_route_without_literals_is_not_a_candidate(web_workspace: Path) -> None:
    """Повтор ключа — не признак: в `audit.service.ts` шесть вызовов одного маршрута."""
    result = run(web_workspace, DocpipeConfig())
    audit = [item for item in result.calls.resolved if item.raw.file == AUDIT]
    assert len({item.call.key.route for item in audit}) == 1
    assert len(audit) == 6

    report = _report(result, DocpipeConfig())
    assert not any(AUDIT in example for item in report.items for example in item.examples)


def test_field_with_one_value_everywhere_is_not_a_candidate(web_workspace: Path) -> None:
    """`fields: [...]` в обоих вызовах `api/items/query` — выражение, а не разные значения."""
    report = _report(run(web_workspace, DocpipeConfig()), DocpipeConfig())
    assert "fields" not in {item.name for item in report.items}

    same = _inline(
        """
export class A {
  a() { return this.http.post('api/x', { kind: 'a' }); }
  b() { return this.http.post('api/x', { kind: 'a' }); }
}
"""
    )
    assert _report(same, DocpipeConfig()).total == 0


def test_single_call_is_not_a_candidate() -> None:
    one = _inline("export class A { a() { return this.http.get('api/x?kind=a&kind=b'); } }")
    assert _report(one, DocpipeConfig()).total == 0


# --------------------------------------------------------------------------------------
# Отметка «уже в настройке» и следствие записи правила
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "route", "unresolved"),
    [(BODY_RULE, "api/items/query", 0), (QUERY_RULE, "api/items", 1)],
)
def test_configured_rule_is_marked_and_predicts_registry_unresolved(
    web_workspace: Path, rule: dict[str, Any], route: str, unresolved: int
) -> None:
    """Число «без различителя» — то, что покажет прогон после записи правила."""
    settings = _settings(registry_calls=[rule])
    result = run(web_workspace, settings)
    report = _report(result, settings)

    # Кандидат не пропадает с настройкой: группа — ключ без различителя.
    assert len(report.items) == 2
    marked = _one(report, route)
    assert marked.configured is True
    assert marked.unresolved_when_configured == unresolved
    assert len(result.calls.registry_unresolved) == unresolved
    assert [item.configured for item in report.items if item.route != route] == [False]


def test_rule_on_another_field_is_not_configured(web_workspace: Path) -> None:
    settings = _settings(
        registry_calls=[
            {"route": "api/items/query", "discriminator": {"in": "body", "name": "listName"}}
        ]
    )
    report = _report(run(web_workspace, settings), settings)
    assert _one(report, "api/items/query").configured is False


def test_route_is_printed_after_url_rewrite(web_workspace: Path) -> None:
    """Правило сверяется с маршрутом после `url_rewrite`, а не с тем, что в коде."""
    rewrite = [{"module": "tr-p", "add_prefix": "/gw"}]
    report = _report(run(web_workspace, _settings(url_rewrite=rewrite)), DocpipeConfig())
    assert {item.route for item in report.items} == {"gw/api/items/query", "gw/api/items"}

    # Маршрут, как он написан в коде, при преобразовании не срабатывает —
    # и молча: вызовы остаются без различителя.
    as_written = _settings(url_rewrite=rewrite, registry_calls=[BODY_RULE])
    result = run(web_workspace, as_written)
    assert _one(_report(result, as_written), "gw/api/items/query").configured is False
    assert {call.key.discriminator for call in result.calls.calls} == {""}

    printed = _settings(
        url_rewrite=rewrite, registry_calls=[{**BODY_RULE, "route": "gw/api/items/query"}]
    )
    result = run(web_workspace, printed)
    assert _one(_report(result, printed), "gw/api/items/query").configured is True
    assert {call.key.discriminator for call in result.calls.calls} == {"", "models", "users"}


def test_printed_rule_round_trips_into_config(web_workspace: Path) -> None:
    """Строка «в web.registry_calls» — запись, которую загрузка принимает как есть."""
    report = _report(run(web_workspace, DocpipeConfig()), DocpipeConfig())
    rules = [yaml.safe_load(registry_rule_text(item)) for item in report.items]
    assert all(RegistryCallConfig.model_validate(rule) for rule in rules)

    settings = _settings(registry_calls=rules)
    configured = _report(run(web_workspace, settings), settings)
    assert [item.configured for item in configured.items] == [True, True]


def test_route_with_a_parameter_segment_round_trips() -> None:
    """`{}` в маршруте внутри потоковой записи YAML без кавычек — вложенный словарь."""
    scan = _inline(
        """
export class A {
  a(id: string) { return this.http.get(`api/lists/${id}/items?name=orders`); }
  b(id: string) { return this.http.get(`api/lists/${id}/items?name=users`); }
}
"""
    )
    [item] = _report(scan, DocpipeConfig()).items
    assert item.route == "api/lists/{}/items"
    rule = yaml.safe_load(registry_rule_text(item))
    assert rule == {
        "route": "api/lists/{}/items",
        "discriminator": {"in": "query", "name": "name"},
    }
    settings = _settings(registry_calls=[rule])
    assert _report(scan, settings).items[0].configured is True


# --------------------------------------------------------------------------------------
# Формы, которых нет в фикстуре
# --------------------------------------------------------------------------------------


def test_body_value_from_an_expression_counts_as_another_value() -> None:
    """`{ listInnerName: name }` и `{ listInnerName }` видны наравне с `${type}` в query."""
    scan = _inline(
        """
export class A {
  users() { return this.http.post('api/items/query', { listInnerName: 'users' }); }
  byName(name: string) { return this.http.post('api/items/query', { listInnerName: name }); }
  short(listInnerName: string) { return this.http.post('api/items/query', { listInnerName }); }
}
"""
    )
    [item] = _report(scan, DocpipeConfig()).items
    assert (item.where, item.values, item.nonliteral, item.calls) == ("body", ["users"], 2, 3)
    assert item.unresolved_when_configured == 2
    # Сначала вызов с литералом: по нему видно, какие имена зовут.
    assert item.examples[0] == "src/a.service.ts:3"


def test_route_shared_by_two_verbs_counts_every_call_the_rule_covers() -> None:
    """Правило глагола не знает: `DELETE` того же маршрута тоже останется без различителя."""
    scan = _inline(
        """
export class A {
  a() { return this.http.get('api/lists?name=a'); }
  b() { return this.http.get('api/lists?name=b'); }
  c() { return this.http.delete('api/lists'); }
}
"""
    )
    [item] = _report(scan, DocpipeConfig()).items
    assert (item.http_method, item.calls, item.route_calls) == ("GET", 2, 3)
    assert item.unresolved_when_configured == 1
    text = format_candidates(_report(scan, DocpipeConfig()))
    assert "у маршрута всего вызовов 3" in text


def test_one_route_in_two_modules_is_one_question() -> None:
    """Правило пишется на маршрут, а не на модуль: платформенный эндпоинт — один вопрос."""
    first = _inline("export class A { a() { return this.http.get('api/lists?name=a'); } }", "pm")
    second = _inline("export class B { b() { return this.http.get('api/lists?name=b'); } }", "ml")
    merged = CallScan(
        calls=[*first.calls, *second.calls], resolved=[*first.resolved, *second.resolved]
    )
    [item] = _report(merged, DocpipeConfig()).items
    assert (item.values, item.modules) == (["a", "b"], ["ml", "pm"])


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def test_report_names_what_the_scan_does_not_see(web_workspace: Path) -> None:
    report = _report(run(web_workspace, DocpipeConfig()), DocpipeConfig())
    assert report.limits == list(REGISTRY_LIMITS)
    assert any("HttpParams" in text for text in report.limits)
    text = format_candidates(report)
    assert "Ограничения разбора:" in text
    assert all(limit in text for limit in REGISTRY_LIMITS)
    assert (
        'в web.registry_calls: {route: "api/items/query", '
        'discriminator: {in: body, name: "listInnerName"}}'
    ) in text


def test_report_carries_its_base(web_workspace: Path) -> None:
    """Ноль кандидатов при одном восстановленном вызове из ста — «не видно», а не «нет»."""
    result = run(web_workspace, DocpipeConfig())
    report = _report(result, DocpipeConfig())
    assert (report.calls_resolved, report.calls_unresolved) == (
        len(result.calls.calls),
        len(result.calls.unresolved),
    )
    assert report.calls_unresolved == 1
    assert (
        f"Вызовов восстановлено {report.calls_resolved}, не восстановлено 1"
        in format_candidates(report)
    )


def test_empty_report_still_names_the_limits() -> None:
    report = _report(CallScan(), DocpipeConfig())
    assert (report.total, report.items) == (0, [])
    text = format_candidates(report)
    assert "Кандидатов нет." in text
    assert "Ограничения разбора:" in text


def test_page_keeps_total_and_offset(web_workspace: Path) -> None:
    calls = run(web_workspace, DocpipeConfig()).calls
    page = registry_call_candidates(calls, DocpipeConfig(), limit=1, offset=1)
    assert (page.total, page.offset, _keys(page)) == (
        2,
        1,
        [("GET", "api/items", "query", "listInnerName")],
    )
    assert "Показаны 2–2 из 2." in format_candidates(page)


def test_kind_is_known_and_two_runs_give_the_same_bytes(
    web_workspace: Path, tmp_path: Path
) -> None:
    """Второй прогон идёт по тёплому кэшу разбора и обязан дать те же байты."""
    assert "registry-calls" in KINDS
    inputs = CandidateInputs(_copy(web_workspace, tmp_path), DocpipeConfig())
    cold = candidates("registry-calls", inputs, limit=0)
    assert isinstance(cold, RegistryCallCandidates)
    assert candidates_json(cold) == candidates_json(candidates("registry-calls", inputs, limit=0))


# --------------------------------------------------------------------------------------
# Команда
# --------------------------------------------------------------------------------------


def test_command_prints_json_report(web_workspace: Path, tmp_path: Path) -> None:
    root = _copy(web_workspace, tmp_path)
    result = runner.invoke(
        app, ["setup", "candidates", "registry-calls", "--root", str(root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert not result.output.endswith("\n\n")
    report = json.loads(result.output)
    assert report["schema_version"] == "1.0"
    assert [(item["route"], item["where"]) for item in report["items"]] == [
        ("api/items/query", "body"),
        ("api/items", "query"),
    ]
    assert report["limits"] == list(REGISTRY_LIMITS)


def test_command_reads_registry_calls_from_config(web_workspace: Path, tmp_path: Path) -> None:
    root = _copy(web_workspace, tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text(
        "web:\n  registry_calls:\n"
        '    - {route: "api/items", discriminator: {in: query, name: "listInnerName"}}\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["setup", "candidates", "registry-calls", "--root", str(root), "--config", str(config)],
    )
    assert result.exit_code == 0, result.output
    assert "GET api/items — query.listInnerName  [уже в registry_calls]" in result.output
    assert "с правилом без различителя останется 1" in result.output
    assert "Показаны 1–2 из 2." in result.output


def test_unreadable_web_rules_are_a_config_error(web_workspace: Path, tmp_path: Path) -> None:
    root = _copy(web_workspace, tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text("web:\n  rules: nowhere.yaml\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["setup", "candidates", "registry-calls", "--root", str(root), "--config", str(config)],
    )
    assert result.exit_code == 2
    assert "набор правил фронта не читается" in result.output
    assert "nowhere.yaml" in result.output
