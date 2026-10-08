"""`web.not_wrappers`: «это не обёртка» — решением с причиной (S24b).

Кандидат в `web.http_wrappers` (S18) — вызов члена с аргументом, похожим
на адрес. На squidex все 9 кандидатов и на abp все 14 — не HTTP
(`window.open`, `url.startsWith`, `form.patchValue`): объявить их обёрткой
нельзя, и без записи «не обёртка» находка `link.calls_invisible` не закрывалась
ничем. Запись снимает кандидата из `http-wrappers` и из находки, входит
в охват (S24) и в `decisions` у `setup explain` (S23); на разбор не влияет.

Конструкция — файл с `window.open('/api/print')` и `url.startsWith('/api/')`
в копии `SeamWorkspace` в `tmp_path`: сама фикстура не расширяется, на её
точный список кандидатов завязан `tests/test_seam_fixture.py`.
"""

import shutil
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig, NotWrapper, WebConfig, load_config
from docpipe.hashing import stable_json_dumps
from docpipe.setup.candidates import (
    declined_calls,
    format_http_wrappers,
    http_wrapper_candidates,
    http_wrapper_places,
)
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.explain import BUILTIN_FILE, DEFAULT_FILE, explain_path
from docpipe.setup.status import FINDING_CODES, build_status, decision_coverage, status_detail
from docpipe.web.calls import NotWrapperConflict, WrapperConflict, not_wrapper_for
from docpipe.web.tree import run as run_web
from tests.test_setup_link import S19_WEB

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
PRINT: Final = "frontend/src/app/services/print.service.ts"

# Два вызова с аргументом-адресом, которые HTTP не делают: вкладка браузера
# (squidex, `rich-editor.component.ts`) и проверка строки (`url.startsWith`).
PRINT_SERVICE: Final = """import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class PrintService {
  print(): void {
    window.open('/api/print', '_blank');
  }

  isApi(url: string): boolean {
    return url.startsWith('/api/');
  }
}
"""

WINDOW_OPEN: Final[dict[str, str]] = {
    "receiver": "window",
    "method": "open",
    "reason": "открывает вкладку браузера, а не HTTP-вызов",
}
STARTS_WITH: Final[dict[str, str]] = {
    "receiver": "url",
    "method_regex": "(starts|ends)With",
    "reason": "проверка строки адреса, вызова нет",
}
VERSIONED: Final[dict[str, Any]] = S19_WEB["http_wrappers"][0]


def _copy(root: Path, *, wrappers: bool = False, **web: Any) -> Path:
    """Копия шва с файлом `print.service.ts`; `wrappers` — правила S19, `web` — ключи секции."""
    shutil.copytree(SEAM, root, ignore=shutil.ignore_patterns(".docpipe"))
    (root / PRINT).write_text(PRINT_SERVICE, encoding="utf-8")
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(RULES.resolve())
    if wrappers:
        raw["web"].update(S19_WEB)
    raw["web"].update(web)
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return root


def _context(root: Path) -> SetupContext:
    return SetupContext.build(root, root / "docpipe.yaml", use_cache=False)


def _candidates(ctx: SetupContext) -> list[str]:
    web = ctx.web
    report = http_wrapper_candidates(
        web.candidate_calls,
        web.builder_uses,
        web.calls,
        wrappers=ctx.settings.web.http_wrappers,
        not_wrappers=ctx.settings.web.not_wrappers,
        limit=0,
    )
    return [f"{item.receiver}.{item.method}" for item in report.items]


def _invisible(ctx: SetupContext) -> dict[str, int]:
    report = build_status(ctx)
    finding = next((item for item in report.findings if item.code == "link.calls_invisible"), None)
    return {} if finding is None else {cluster.key: cluster.count for cluster in finding.clusters}


# --------------------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------------------


def test_record_loads_with_reason_and_label() -> None:
    config = WebConfig.model_validate({"not_wrappers": [WINDOW_OPEN, STARTS_WITH]})
    assert [item.label for item in config.not_wrappers] == [
        "window.open",
        "url./(starts|ends)With/",
    ]
    assert config.not_wrappers[0].reason == WINDOW_OPEN["reason"]


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"receiver": "window", "method": "open"}, "нет `reason`"),
        ({**WINDOW_OPEN, "reason": "  "}, "`reason` пустой"),
        ("window.open", 'пишите `- receiver: "window"`, `method: "open"`'),
        ({"receiver": "window", "reason": "r"}, "ровно одно из `method` и `method_regex`"),
        (
            {"receiver": "window", "method": "open", "method_regex": "op.*", "reason": "r"},
            "дано оба",
        ),
        ({"receiver": "window", "method_regex": "(open", "reason": "r"}, "не компилируется"),
        ({"receiver": " ", "method": "open", "reason": "r"}, "`receiver` пуст"),
    ],
)
def test_bad_record_is_refused(record: Any, message: str) -> None:
    """Критерий приёмки: пустой `reason` — отказ; и каждая опечатка формы — тоже."""
    with pytest.raises(ValidationError, match=message):
        WebConfig.model_validate({"not_wrappers": [record]})


def test_repeated_record_is_refused() -> None:
    """Одно имя — одна запись: вторая не сработала бы никогда. Получатель — последний сегмент."""
    other = {**WINDOW_OPEN, "receiver": "this.Window", "reason": "другая причина"}
    with pytest.raises(ValidationError, match="назван больше одного раза: window.open"):
        WebConfig.model_validate({"not_wrappers": [WINDOW_OPEN, other]})


@pytest.mark.parametrize(
    ("wrapper", "declined"),
    [
        # Точное против точного; получатель — последний сегмент без регистра.
        (
            {**VERSIONED, "method_regex": "", "method": "getVersioned"},
            {"receiver": "this.http", "method": "getVersioned", "reason": "r"},
        ),
        # Регулярка обёртки накрывает точное имя «не обёртки».
        (VERSIONED, {"receiver": "HTTP", "method": "getVersioned", "reason": "r"}),
        # Регулярка «не обёртки» накрывает точное имя обёртки.
        (
            {**VERSIONED, "method_regex": "", "method": "putVersioned"},
            {"receiver": "HTTP", "method_regex": ".*Versioned", "reason": "r"},
        ),
        # Та же регулярка с обеих сторон.
        (VERSIONED, {"receiver": "HTTP", "method_regex": VERSIONED["method_regex"], "reason": "r"}),
    ],
)
def test_wrapper_and_not_wrapper_on_one_call_are_refused(
    wrapper: dict[str, Any], declined: dict[str, Any]
) -> None:
    """Критерий приёмки: пересечение с `http_wrappers` — отказ загрузки."""
    with pytest.raises(ValidationError, match="и обёрткой, и «не обёрткой»"):
        WebConfig.model_validate({"http_wrappers": [wrapper], "not_wrappers": [declined]})


def test_other_receiver_is_not_a_clash() -> None:
    config = WebConfig.model_validate(
        {
            "http_wrappers": [VERSIONED],
            "not_wrappers": [{"receiver": "cache", "method": "getVersioned", "reason": "r"}],
        }
    )
    assert len(config.not_wrappers) == 1


def test_commented_out_list_is_refused(tmp_path: Path) -> None:
    """Правило S02: ключ-список без элементов — отказ с подсказкой, а не умолчание."""
    config = tmp_path / "docpipe.yaml"
    config.write_text("web:\n  not_wrappers:\n    # - receiver: window\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`web.not_wrappers:` без элементов"):
        load_config(config)


# --------------------------------------------------------------------------------------
# Разбор: запись на него не влияет, а противоречие на вызове — отказ прогона
# --------------------------------------------------------------------------------------


def test_record_does_not_change_the_manifest(tmp_path: Path) -> None:
    """«Не обёртка» — решение об отчёте кандидатов, а не правило разбора."""
    ruleset = load_ruleset(RULES, "web")
    bare = _copy(tmp_path / "bare", wrappers=True)
    declined = _copy(tmp_path / "declined", wrappers=True, not_wrappers=[WINDOW_OPEN])
    manifests = [
        stable_json_dumps(
            run_web(root, load_config(root / "docpipe.yaml"), ruleset).manifest.model_dump(
                mode="json"
            )
        )
        for root in (bare, declined)
    ]
    assert manifests[0] == manifests[1]


def test_regex_clash_on_a_call_refuses_the_run(tmp_path: Path) -> None:
    """Две разные регулярки загрузка не сравнит — их пересечение ловит прогон."""
    root = _copy(
        tmp_path / "ws",
        wrappers=True,
        not_wrappers=[{"receiver": "HTTP", "method_regex": "get.*", "reason": "r"}],
    )
    settings = load_config(root / "docpipe.yaml")
    with pytest.raises(NotWrapperConflict, match="и с «не обёрткой» HTTP./get.\\*/") as failure:
        run_web(root, settings, load_ruleset(RULES, "web"))
    assert isinstance(failure.value, WrapperConflict)

    # `SetupContext.web` переводит его в ошибку настройки, а не ручного состава страниц.
    with pytest.raises(InputError, match="web.not_wrappers"):
        _ = _context(root).web

    result = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(root),
            "--config",
            str(root / "docpipe.yaml"),
            "--out",
            str(tmp_path / "out.json"),
            "--no-cache",
        ],
    )
    assert result.exit_code == 2
    assert "Ошибка конфигурации: вызов HTTP.getVersioned" in result.output


# --------------------------------------------------------------------------------------
# Кандидаты, находка, охват, `setup explain`
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plain(tmp_path_factory: pytest.TempPathFactory) -> SetupContext:
    """Без записей: `window.open` и `url.startsWith` — кандидаты, как на squidex."""
    return _context(_copy(tmp_path_factory.mktemp("plain") / "ws"))


@pytest.fixture(scope="module")
def declined(tmp_path_factory: pytest.TempPathFactory) -> SetupContext:
    return _context(
        _copy(tmp_path_factory.mktemp("declined") / "ws", not_wrappers=[WINDOW_OPEN, STARTS_WITH])
    )


def test_construction_is_a_candidate_without_a_record(plain: SetupContext) -> None:
    """Конструкция на месте: без записи оба вызова — кандидаты и находка."""
    assert {"window.open", "url.startsWith"} <= set(_candidates(plain))
    invisible = _invisible(plain)
    assert invisible["window.open"] == 1 and invisible["url.startsWith"] == 1


def test_record_removes_the_candidate_from_both_lists(declined: SetupContext) -> None:
    """Критерий приёмки: запись на `window.open` — нет ни в `http-wrappers`, ни в находке."""
    shown = _candidates(declined)
    assert "window.open" not in shown and "url.startsWith" not in shown
    assert set(shown) == {"HTTP.getVersioned", "HTTP.requestVersioned", "rest.request"}
    assert set(_invisible(declined)) == set(shown)


def test_candidates_report_counts_declared_groups(declined: SetupContext) -> None:
    web = declined.web
    report = http_wrapper_candidates(
        web.candidate_calls,
        web.builder_uses,
        web.calls,
        not_wrappers=declined.settings.web.not_wrappers,
    )
    assert (report.schema_version, report.declared_not_wrappers, report.total) == ("1.1", 2, 3)
    assert "Снято записями web.not_wrappers («не обёртка»): 2." in format_http_wrappers(report)


def test_with_s19_rules_the_finding_closes(tmp_path: Path) -> None:
    """Обёртки объявлены, остальное — «не обёртки»: `link.calls_invisible` нет вовсе."""
    root = _copy(tmp_path / "ws", wrappers=True, not_wrappers=[WINDOW_OPEN, STARTS_WITH])
    assert _invisible(_context(root)) == {}


def test_record_on_another_receiver_removes_nothing(tmp_path: Path) -> None:
    """Опечатка в получателе — кандидат остаётся находкой, а у записи охват ноль."""
    typo = {**WINDOW_OPEN, "receiver": "windw"}
    context = _context(_copy(tmp_path / "ws", not_wrappers=[typo]))
    assert "window.open" in _invisible(context)
    [entry] = [item for item in decision_coverage(context) if item.key == "web.not_wrappers"]
    assert (entry.value, entry.count, entry.files) == ("windw.open", 0, {})


def test_record_is_in_coverage_with_its_reason(declined: SetupContext) -> None:
    """Охват (S24): вызовы, которые запись сняла, с разбивкой по файлам; причина — у записи."""
    entries = {
        item.value: item for item in decision_coverage(declined) if item.key == "web.not_wrappers"
    }
    assert set(entries) == {"window.open", "url./(starts|ends)With/"}
    opened = entries["window.open"]
    assert (opened.count, opened.files, opened.reason) == (1, {PRINT: 1}, WINDOW_OPEN["reason"])
    report = build_status(declined)
    assert not any(
        item.endswith("::web.not_wrappers::window.open") for item in report.without_reason
    )


def test_record_is_in_explain_decisions(declined: SetupContext) -> None:
    """`decisions` у `setup explain` (S23): запись, снявшая кандидата под целью."""
    report = explain_path(declined, PRINT)
    refs = {item.value: item for item in report.decisions if item.key == "web.not_wrappers"}
    assert set(refs) == {"window.open", "url./(starts|ends)With/"}
    assert refs["window.open"].reason == WINDOW_OPEN["reason"]
    assert refs["window.open"].count == 1
    assert "не вызов HTTP" in refs["window.open"].effect


def test_explain_and_coverage_count_the_same(declined: SetupContext) -> None:
    """Сторож S24: «что запись сняла» одинаково у охвата и у `setup explain .`."""
    explained = {
        (ref.key, ref.value): ref.count
        for ref in explain_path(declined, ".", limit=0).decisions
        if ref.file not in (BUILTIN_FILE, DEFAULT_FILE) and ref.key == "web.not_wrappers"
    }
    covered = {
        (item.key, item.value): item.count
        for item in decision_coverage(declined)
        if item.key == "web.not_wrappers" and item.count
    }
    assert explained == covered
    assert covered == {
        ("web.not_wrappers", "window.open"): 1,
        ("web.not_wrappers", "url./(starts|ends)With/"): 1,
    }


def test_declined_calls_name_the_record_by_its_index(declined: SetupContext) -> None:
    web = declined.web
    pairs = declined_calls(
        web.candidate_calls, web.builder_uses, declined.settings.web.not_wrappers
    )
    assert [(index, call.receiver, call.method) for index, call in pairs] == [
        (0, "window", "open"),
        (1, "url", "startsWith"),
    ]


def test_review_places_skip_declined_calls(declined: SetupContext) -> None:
    """Сторож свода S24b и S25: места `link.calls_invisible` — того же отсева, что число.

    Места ревью (`status_detail`) берутся из `http_wrapper_places`, а число —
    из кандидатов без «не обёрток». Снятая группа, оставшаяся в местах,
    отнесла бы `window.open` в новом файле к находкам ревью, хотя отчёт её
    уже не считает.
    """
    web = declined.web
    located = http_wrapper_places(
        web.candidate_calls, web.builder_uses, not_wrappers=declined.settings.web.not_wrappers
    )
    assert sorted(located) == [
        ("HTTP", "getVersioned"),
        ("HTTP", "requestVersioned"),
        ("rest", "request"),
    ]
    assert not any(file == PRINT for places in located.values() for file, _ in places)

    detail = status_detail(declined)
    [finding] = [item for item in detail.status.findings if item.code == "link.calls_invisible"]
    places = detail.places["link.calls_invisible"]
    assert len(places) == finding.count
    assert all(PRINT not in place.files for place in places)


def test_first_record_in_file_order_decides() -> None:
    """Две «не обёртки» на одном вызове — не отказ: исход один, решает первая в файле."""
    rules = [
        NotWrapper.model_validate({"receiver": "url", "method": "startsWith", "reason": "a"}),
        NotWrapper.model_validate({"receiver": "url", "method_regex": "starts.*", "reason": "b"}),
    ]
    assert not_wrapper_for("url", "startsWith", rules) == (0, rules[0])
    assert not_wrapper_for("this.url", "startsWithin", rules) == (1, rules[1])
    assert not_wrapper_for("url", "endsWith", rules) is None


def test_finding_names_the_new_key_as_its_decision_home() -> None:
    [code] = [item for item in FINDING_CODES if item.code == "link.calls_invisible"]
    assert "web.not_wrappers" in code.decision_home


def test_command_shows_the_counter(tmp_path: Path) -> None:
    root = _copy(tmp_path / "ws", not_wrappers=[WINDOW_OPEN])
    args = ["setup", "candidates", "http-wrappers", "--root", str(root)]
    args += ["--config", str(root / "docpipe.yaml")]
    text = runner.invoke(app, args)
    assert text.exit_code == 0, text.output
    assert "Снято записями web.not_wrappers («не обёртка»): 1." in text.output
    assert "window.open" not in text.output


def test_default_config_has_no_records() -> None:
    assert DocpipeConfig().web.not_wrappers == []
