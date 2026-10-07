"""Загрузка настройки без молчания (S02).

Настройку правит ассистент и тут же перезапускает инструмент (Р-1), поэтому
опечатка в файле настройки обязана стоить одного прогона: отказ с адресом —
файл, номер правила, перечень допустимого, — а не правдоподобный пустой
результат. Каждый случай ниже до S02 проходил загрузку молча.
"""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.classify import RULE_SECTIONS, load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, load_config
from docpipe.materialize.ownership import load_ownership
from docpipe.ruleset import load_rule_items
from docpipe.web.overrides import load_overrides
from tests.conftest import sectioned

runner = CliRunner()

BUNDLE = Path("deploy/cashflow-docspipe")
GENERIC = Path("deploy/generic-docspipe")
LIST = "src/app/routes/models/list/list.component.ListComponent"
AUDIT = "src/app/shared/services/audit.service.AuditService"

RULE = {"id": "r", "kind": "k", "template": "t", "priority": 1, "when": {"name_suffix": ["X"]}}
EXCLUDE_RULE = {"id": "e", "reason": "так решили", "when": {"name_suffix": ["Y"]}}


def _rules_file(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def _section(**overrides: object) -> dict[str, object]:
    return {"ruleset_version": "t", "exclude": {"rules": [EXCLUDE_RULE]}, "rules": [RULE]} | dict(
        overrides
    )


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# 1. Неизвестные ключи: девять мест таблицы
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "where", "allowed"),
    [
        pytest.param(
            {"version": "1", "dotnet": _section(exclude={"rules": [EXCLUDE_RULE | {"unles": {}}]})},
            r"rules\.yaml:dotnet:exclude: правило #0 \(e\): неизвестный ключ 'unles'",
            "id, priority, reason, unless, when",
            id="exclude.rules[]",
        ),
        pytest.param(
            {"version": "1", "dotnet": _section(rules=[RULE, RULE | {"id": "q", "priorty": 2}])},
            r"rules\.yaml:dotnet: правило #1 \(q\): неизвестный ключ 'priorty'",
            "id, kind, priority, template, when",
            id="rules[]",
        ),
        pytest.param(
            {"version": "1", "dotnet": _section(rule=[])},
            r"rules\.yaml:dotnet: неизвестный ключ 'rule'",
            "exclude, rules, ruleset_version",
            id="section",
        ),
        pytest.param(
            {"version": "1", "dotnet": _section(), "dotnett": _section()},
            r"rules\.yaml: неизвестный ключ 'dotnett'",
            "dotnet, version, web",
            id="file-top",
        ),
    ],
)
def test_unknown_key_in_the_rules_file_is_refused_with_its_address(
    tmp_path: Path, document: dict[str, object], where: str, allowed: str
) -> None:
    with pytest.raises(ValueError, match=where) as failure:
        load_ruleset(_rules_file(tmp_path, document), "dotnet")
    assert f"допустимы: {allowed}" in str(failure.value)


def test_unless_in_a_classification_rule_names_where_it_belongs(tmp_path: Path) -> None:
    """`unless` раньше молча игнорировался: правило совпадало шире задуманного."""
    path = _rules_file(
        tmp_path,
        {"version": "1", "dotnet": _section(rules=[RULE | {"unless": {"name_suffix": ["Z"]}}])},
    )

    with pytest.raises(ValueError, match="неизвестный ключ 'unless'") as failure:
        load_ruleset(path, "dotnet")
    assert "`unless` есть только у правил отсева (`exclude.rules`)" in str(failure.value)


def test_version_inside_a_section_is_refused_with_a_hint(tmp_path: Path) -> None:
    """Внутри секции `version` молча перекрывал версию файла."""
    path = _rules_file(tmp_path, {"version": "1", "web": _section(version="2")})

    with pytest.raises(ValueError, match="снаружи секций"):
        load_ruleset(path, "web")


def test_flat_file_still_gets_the_migration_command(tmp_path: Path) -> None:
    """Строгий верх файла не перебивает распознавание плоского формата:
    его `ruleset_version` и `rules` наверху иначе дали бы «неизвестный ключ»."""
    path = _rules_file(tmp_path, {"version": "1", **_section()})

    with pytest.raises(ValueError, match="migrate_rules.py"):
        load_ruleset(path, "dotnet")


def test_unknown_key_is_reported_before_missing_required(tmp_path: Path) -> None:
    """Опечатка `idd` иначе дала бы «правило без полей ['id']»."""
    broken = {key: value for key, value in RULE.items() if key != "id"} | {"idd": "r"}
    path = _rules_file(tmp_path, {"version": "1", "dotnet": _section(rules=[broken])})

    with pytest.raises(ValueError, match=r"правило #0: неизвестный ключ 'idd'"):
        load_ruleset(path, "dotnet")


def test_rule_items_without_allowed_keep_the_old_contract() -> None:
    """Параметр ключевой и с умолчанием: позиционный вызов с тремя аргументами
    не проверяет лишних ключей, как и раньше."""
    raw = [{"id": "a", "team": "x", "extra": 1}]

    assert load_rule_items(raw, "f.yaml", {"id", "team"}) == raw
    with pytest.raises(ValueError, match="неизвестный ключ 'extra'"):
        load_rule_items(raw, "f.yaml", {"id", "team"}, allowed=frozenset({"id", "team"}))


def test_section_list_is_one_constant_with_the_migration_tool() -> None:
    """Секцию нового языка добавляют в `RULE_SECTIONS`; инструмент переноса
    обязан знать те же секции, иначе он распознает перенесённый файл как плоский."""
    spec = importlib.util.spec_from_file_location("migrate_rules", "tools/migrate_rules.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SECTIONS == RULE_SECTIONS


def test_unknown_section_requested_by_the_caller_is_refused(tmp_path: Path) -> None:
    path = _rules_file(tmp_path, {"version": "1", "dotnet": _section()})

    with pytest.raises(ValueError, match="секции правил `python` нет"):
        load_ruleset(path, "python")


OWNERSHIP_RULE = "{id: r, team: a, priority: 1, when: {module: [x]}}"


@pytest.mark.parametrize(
    ("body", "where", "allowed"),
    [
        pytest.param(
            f"teams: [{{id: a}}]\nrules: [{OWNERSHIP_RULE}]\nteam: []\n",
            r"ownership\.yaml: неизвестный ключ 'team'",
            "ownership_version, rules, teams, version",
            id="ownership-top",
        ),
        pytest.param(
            "teams: [{id: a}, {id: b, titel: B}]\n",
            r"ownership\.yaml: команда #1 \(b\): неизвестный ключ 'titel'",
            "id, title",
            id="teams[]",
        ),
        pytest.param(
            "teams: [{id: a}]\nrules: [{id: r, team: a, priorty: 1, when: {module: [x]}}]\n",
            r"ownership\.yaml: правило #0 \(r\): неизвестный ключ 'priorty'",
            "id, priority, team, when",
            id="ownership.rules[]",
        ),
    ],
)
def test_unknown_key_in_ownership_is_refused_with_its_address(
    tmp_path: Path, body: str, where: str, allowed: str
) -> None:
    path = _write(tmp_path, "ownership.yaml", f'version: "1"\n{body}')

    with pytest.raises(ValueError, match=where) as failure:
        load_ownership(path)
    assert f"допустимы: {allowed}" in str(failure.value)


@pytest.mark.parametrize(
    ("text", "where", "allowed"),
    [
        pytest.param(
            'version: "1"\npages:\n  add: []\nremov: []\n',
            r"pages\.yaml: неизвестный ключ 'remov'",
            "add, features, pages, remove, version",
            id="pages-top",
        ),
        pytest.param(
            'version: "1"\npages:\n  add: []\n  remov: []\n',
            r"pages\.yaml: pages: неизвестный ключ 'remov'",
            "add, features, remove",
            id="pages-body",
        ),
    ],
)
def test_unknown_key_in_pages_is_refused_with_its_address(
    tmp_path: Path, text: str, where: str, allowed: str
) -> None:
    with pytest.raises(ValueError, match=where) as failure:
        load_overrides(_write(tmp_path, "pages.yaml", text))
    assert f"допустимы: {allowed}" in str(failure.value)


ADD = '    - {route: "/x", component: "a.B", reason: "печать"}\n'
FEATURE = '    - {name: "debt", path: "src/app/debt", reason: "без маршрута"}\n'


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f'version: "1"\npages:\n  add:\n{ADD}features:\n{FEATURE[2:]}', id="body"),
        pytest.param(f'version: "1"\nadd:\n{ADD[2:]}features:\n{FEATURE[2:]}', id="flat"),
        pytest.param(f'version: "1"\npages:\n  add:\n{ADD}  features:\n{FEATURE}', id="inner"),
    ],
)
def test_both_forms_of_pages_still_load(tmp_path: Path, text: str) -> None:
    """Строгая проверка обязана принять обе формы файла и `features` в обоих местах."""
    overrides = load_overrides(_write(tmp_path, "pages.yaml", text))

    assert [rule.component for rule in overrides.add] == ["a.B"]
    assert [feature.name for feature in overrides.features] == ["debt"]


def test_rule_beside_the_pages_body_is_refused(tmp_path: Path) -> None:
    """`add` рядом с `pages:` раньше не читался вовсе — страница молча не добавлялась."""
    text = f'version: "1"\npages:\n  remove: []\nadd:\n{ADD[2:]}'

    with pytest.raises(ValueError, match="рядом с `pages:`"):
        load_overrides(_write(tmp_path, "pages.yaml", text))


def test_features_in_both_places_are_refused(tmp_path: Path) -> None:
    """Раньше побеждали верхние, и разделы из `pages:` терялись молча."""
    text = f'version: "1"\npages:\n  features:\n{FEATURE}features:\n{FEATURE[2:]}'

    with pytest.raises(ValueError, match="и в `pages:`, и наверху"):
        load_overrides(_write(tmp_path, "pages.yaml", text))


def test_commented_out_page_list_is_an_empty_list(tmp_path: Path) -> None:
    """Умолчание здесь пустое, поэтому `add:` без элементов значит `[]`;
    раньше это был `TypeError` с трейсбеком."""
    text = 'version: "1"\npages:\n  add:\n    # - {route: "/x", component: "a.B"}\n'

    assert load_overrides(_write(tmp_path, "pages.yaml", text)).empty


# --------------------------------------------------------------------------------------
# 2. Названный `web.pages` без файла; `symbols --lang ts` читает тот же файл
# --------------------------------------------------------------------------------------


def test_named_pages_file_that_does_not_exist_stops_web_scan(
    web_workspace: Path, tmp_path: Path
) -> None:
    config = _write(tmp_path, "cfg/docpipe.yaml", "web:\n  pages: no-such-pages.yaml\n")

    result = runner.invoke(
        app,
        ["web", "scan", "--root", str(web_workspace), "--config", str(config)]
        + ["--out", str(tmp_path / "w.json")],
    )

    assert result.exit_code == 2, result.output
    assert "`web.pages`" in result.output
    # Обе ступени `resolve_input`: «не найден» с одним путём заставляет гадать,
    # искался ли второй.
    assert "no-such-pages.yaml" in result.output
    assert str(tmp_path / "cfg" / "no-such-pages.yaml") in result.output
    assert not (tmp_path / "w.json").exists()


def test_empty_pages_key_still_means_no_manual_rules(web_workspace: Path, tmp_path: Path) -> None:
    config = _write(tmp_path, "docpipe.yaml", 'web:\n  pages: ""\n')

    result = runner.invoke(
        app,
        ["web", "scan", "--root", str(web_workspace), "--config", str(config)]
        + ["--out", str(tmp_path / "w.json")],
    )

    assert result.exit_code == 0, result.output


def _symbols(web_workspace: Path, *extra: str) -> dict[str, str]:
    result = runner.invoke(
        app,
        ["symbols", "--root", str(web_workspace), "--lang", "ts", "--state", "any"]
        + ["--format", "json", "--no-cache", *extra],
    )
    assert result.exit_code == 0, result.output
    return {item["fqn"]: item["state"] for item in json.loads(result.stdout)["symbols"]}


def test_symbols_for_the_front_reads_the_same_pages_file(
    web_workspace: Path, tmp_path: Path
) -> None:
    """Два прогона фронта обязаны считать по одному составу страниц.

    Снятая страница `ListComponent` перестаёт делить `AuditService` с `QuizComponent`,
    и сервис уходит под документ оставшейся страницы. Без правил `pages.yaml`
    `symbols` показывал бы его отдельным документом, а `web scan` — нет.
    """
    _write(
        tmp_path,
        "pages.yaml",
        f'version: "1"\npages:\n  remove:\n    - component: "{LIST}"\n      reason: "проверка"\n',
    )
    config = _write(tmp_path, "docpipe.yaml", "web:\n  pages: pages.yaml\n")

    assert _symbols(web_workspace)[AUDIT] == "documented"
    assert _symbols(web_workspace, "--config", str(config))[AUDIT] == "page_covered"


def test_symbols_for_the_front_refuses_a_missing_pages_file(
    web_workspace: Path, tmp_path: Path
) -> None:
    config = _write(tmp_path, "docpipe.yaml", "web:\n  pages: no-such-pages.yaml\n")

    result = runner.invoke(
        app,
        ["symbols", "--root", str(web_workspace), "--lang", "ts", "--config", str(config)],
    )

    assert result.exit_code == 2
    assert "no-such-pages.yaml" in result.output


# --------------------------------------------------------------------------------------
# 3. Пустой список
# --------------------------------------------------------------------------------------


def test_commented_out_enrolled_is_refused_not_turned_into_everything(tmp_path: Path) -> None:
    """Ловушка S02: `enrolled:` с одними комментариями — это `None`, и ключ
    получал умолчание `["**"]`: каждый модуль включён, без единого сообщения."""
    config = _write(tmp_path, "docpipe.yaml", 'enrolled:\n  # - "src/**"\n')

    with pytest.raises(ValueError) as failure:
        load_config(config)

    message = str(failure.value)
    assert "`enrolled:` без элементов" in message
    assert "`enrolled: []`" in message
    assert '["**"]' in message
    assert str(config) in message


@pytest.mark.parametrize(
    "key",
    [
        "roots",
        "enrolled",
        "not_enrolled",
        "exclude",
        "docs_scan_exclude",
        "dispatch_interfaces",
        "di_methods",
        "arch_adapters",
    ],
)
def test_every_list_key_refuses_none(tmp_path: Path, key: str) -> None:
    config = _write(tmp_path, "docpipe.yaml", f"{key}:\n  # закомментировано\n")

    with pytest.raises(ValueError, match=f"`{key}:` без элементов"):
        load_config(config)


def test_list_keys_are_taken_from_the_model_not_from_a_hand_list() -> None:
    """Список строится по аннотациям: новый ключ-список проверяется сам."""
    from docpipe.config import _list_fields

    assert set(_list_fields(DocpipeConfig)) == {
        "roots",
        "enrolled",
        "not_enrolled",
        "exclude",
        "docs_scan_exclude",
        "dispatch_interfaces",
        "di_methods",
        "arch_adapters",
    }


@pytest.mark.parametrize("key", ["roots", "url_rewrite", "registry_calls"])
def test_nested_web_list_refuses_none_with_the_same_message(tmp_path: Path, key: str) -> None:
    """Раньше здесь был `ValidationError` без подсказки."""
    config = _write(tmp_path, "docpipe.yaml", f"web:\n  {key}:\n    # - закомментировано\n")

    with pytest.raises(ValueError, match=f"`web.{key}:` без элементов") as failure:
        load_config(config)
    assert f"`{key}: []` в секции `web`" in str(failure.value)


def test_explicit_empty_list_and_commented_out_dicts_still_load(tmp_path: Path) -> None:
    """`[]` — честная запись пустого списка; словарь без записей — по-прежнему умолчание."""
    config = _write(
        tmp_path,
        "docpipe.yaml",
        'enrolled: []\ndomains:\n  # "src/**": x\n'
        "web:\n  url_rewrite: []\ngraph:\n  # mode: full\n",
    )

    settings = load_config(config)

    assert settings.enrolled == []
    assert settings.domains == {}
    assert settings.web.url_rewrite == []


# --------------------------------------------------------------------------------------
# 4. Валидаторы
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["../x", "a/../../x", "a\\b", ".", ""])
def test_graph_cache_dir_is_checked(value: str) -> None:
    """`..` и `\\` — как у `cache_dir`; `.` и пустое — потому что мост удаляет
    этот каталог целиком перед каждой сборкой."""
    with pytest.raises(ValueError, match="graph.cache_dir"):
        DocpipeConfig.model_validate({"graph": {"cache_dir": value}})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/var/cache/docpipe/engine", "/var/cache/docpipe/engine"),
        ("./.docpipe//engine-cache/", ".docpipe/engine-cache"),
    ],
)
def test_graph_cache_dir_accepts_absolute_and_normalizes_relative(
    value: str, expected: str
) -> None:
    config = DocpipeConfig.model_validate({"graph": {"cache_dir": value}})

    assert config.graph.cache_dir == expected


def test_adapter_ids_are_unique() -> None:
    adapter = {"id": "реестры", "adapter": "registries"}

    with pytest.raises(ValueError, match="arch_adapters: повтор id 'реестры'"):
        DocpipeConfig.model_validate({"arch_adapters": [adapter, adapter | {"options": {}}]})


def test_url_rewrite_names_a_module_once() -> None:
    """`rewrite_for` молча брал первую запись, вторая не применялась никогда."""
    rules = [{"module": "pm", "strip_prefix": "/pm"}, {"module": "pm", "strip_prefix": ""}]

    with pytest.raises(ValueError, match="модуль назван больше одного раза: pm"):
        DocpipeConfig.model_validate({"web": {"url_rewrite": rules}})


# --------------------------------------------------------------------------------------
# 5. `--format` проверяется
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["symbols", "--root", "."], id="symbols"),
        pytest.param(["diff", "a.json", "b.json"], id="diff"),
        pytest.param(["docs", "status", "m.json"], id="docs-status"),
        pytest.param(["web", "link", "a.json", "b.json"], id="web-link"),
        pytest.param(["web", "pages", "m.json"], id="web-pages"),
        pytest.param(["anchors", "list", "m.json", "--registries", "r.yaml"], id="anchors-list"),
        pytest.param(["anchors", "which", "m.json", "X", "--registries", "r.yaml"], id="which"),
        pytest.param(["business", "status", "m.json"], id="business-status"),
    ],
)
def test_unknown_format_is_refused_before_any_work(arguments: list[str]) -> None:
    """Опечатка `jsno` молча давала текст, и скрипт, ждущий JSON, падал не здесь.

    Файлов в аргументах нет намеренно: проверка формата идёт до чтения входа,
    и код 2 здесь — от неё, а не от «файл не найден» (его сообщение другое).
    """
    result = runner.invoke(app, [*arguments, "--format", "jsno"])

    assert result.exit_code == 2, result.output
    assert "'jsno'" in result.output
    assert "допустимы: text, json" in result.output


# --------------------------------------------------------------------------------------
# Существующая настройка загружается без изменений
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [Path("docpipe.example.yaml"), BUNDLE / "docpipe.yaml", GENERIC / "docpipe.yaml"]
)
def test_shipped_configs_load(path: Path) -> None:
    load_config(path)


@pytest.mark.parametrize(
    "path", [Path("rules/rules.yaml"), BUNDLE / "rules.yaml", GENERIC / "rules.yaml"]
)
@pytest.mark.parametrize("section", RULE_SECTIONS)
def test_shipped_rules_load(path: Path, section: str) -> None:
    load_ruleset(path, section)


@pytest.mark.parametrize(
    "path", [Path("ownership.example.yaml"), BUNDLE / "ownership.yaml", GENERIC / "ownership.yaml"]
)
def test_shipped_ownership_loads(path: Path) -> None:
    load_ownership(path)


@pytest.mark.parametrize(
    "path", [Path("pages.example.yaml"), BUNDLE / "pages.yaml", GENERIC / "pages.yaml"]
)
def test_shipped_pages_load(path: Path) -> None:
    load_overrides(path)


def test_sectioned_helper_of_the_tests_writes_version_outside(tmp_path: Path) -> None:
    """Тестовая обёртка — тоже настройка: `version` внутри секции был бы отказом."""
    path = _write(tmp_path, "r.yaml", sectioned({"ruleset_version": "t", "rules": [RULE]}))

    assert load_ruleset(path, "dotnet").version == "1"
