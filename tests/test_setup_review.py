"""Ревью настройки: новое и прежние решения (`setup review`, S25).

База — последний коммит файлов настройки (П-1), новый код — изменённое
после неё. Критерии приёмки — на git-репозитории в `tmp_path` с копией
`SeamWorkspace`: коммит настройки, затем новый контроллер и новый сервис
фронта; новое правило отсева; удалённый модуль с `url_rewrite`;
незакоммиченная правка; настройка без коммитов; не git; неглубокий клон.

Настройка копии лежит в `setup/` — как каталог `install.sh --config-dir`.
Набор правил — копия правил репозитория без библиотечных правил отсева,
которым на фикстуре нечего отсеять: иначе `dead_decisions` был бы непуст
с первого коммита и критерий «удалённый модуль — в `dead_decisions`» ничего
бы не различал. Пути в `docpipe.yaml` — абсолютные (см. docstring
`tests/test_seam_fixture.py` о первой ступени `resolve_input`). Коммиты —
с фиксированными датой и автором, как в `tests/test_recon.py`. Кэш выключен:
иначе он лёг бы в `.docpipe/` копии и стал бы «новым кодом».
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.setup.context import InputError, SetupContext
from docpipe.setup.review import (
    NOT_DEAD_KEYS,
    NOTE_DEFECTS,
    NOTE_OUTSIDE,
    NOTE_SUBMODULES,
    NOTE_UNCOMMITTED,
    HistoryError,
    Review,
    build_review,
    format_review,
    has_changes,
    is_refusal,
    review_json,
)
from docpipe.setup.status import build_status, decision_id, status_detail

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")
# Нейтральный набор, который кладёт установщик (равен `rules/rules.yaml` байт в байт).
BUNDLE_RULES: Final = Path("deploy/generic-docspipe/rules.yaml")

CONTROLLER: Final = "backend/Seam.Api/Controllers/ReportsController.cs"
SERVICE: Final = "frontend/src/app/services/reports.service.ts"

# Новый контроллер: `daily` фронт зовёт, `weekly` — нет (эндпоинт без вызывающего).
CONTROLLER_CODE: Final = """using Microsoft.AspNetCore.Mvc;
using Seam.Api.Web;

namespace Seam.Api.Controllers;

public sealed class ReportsController : ApiController
{
    [HttpGet("reports/daily")]
    public IActionResult GetDaily() => Ok();

    [HttpGet("reports/weekly")]
    public IActionResult GetWeekly() => Ok();
}
"""

# Новый сервис фронта: `daily` ложится на эндпоинт, `monthly` — нет (вызов без эндпоинта).
SERVICE_CODE: Final = """import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class ReportsService {
  constructor(private http: HttpClient) {}

  daily(): Observable<unknown> {
    return this.http.get('api/reports/daily');
  }

  monthly(): Observable<unknown> {
    return this.http.get('api/reports/monthly');
  }
}
"""

# Новый класс .NET, которого правило отсева отчётов (`Reports.*`) не берёт.
DIGEST: Final = "backend/Seam.Api/Services/DigestService.cs"
DIGEST_CODE: Final = """namespace Seam.Api.Services;

public sealed class DigestService
{
    public string Build() => "digest";
}
"""

REWRITE: Final[dict[str, str]] = {
    "module": "seam-web",
    "reason": "проверено: фронт и эндпоинты говорят api/… одинаково",
}

REPORTS_RULE: Final[dict[str, Any]] = {
    "id": "reports.internal",
    "reason": "отчёты — внутренняя кухня, контракта у них нет",
    "priority": 90,
    # `name_regex` сверяется целиком (`re.fullmatch`): `^Reports` не взял бы ничего.
    "when": {"name_regex": ["Reports.*"]},
}

# Окружение git тестовых коммитов: без HOME и системного конфига — дата
# и автор те же на любой машине, хэши коммитов воспроизводимы.
GIT_ENV: Final[dict[str, str]] = {
    "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Тестовый Автор",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Тестовый Автор",
    "GIT_COMMITTER_EMAIL": "author@example.invalid",
    "GIT_AUTHOR_DATE": "2020-03-01T12:00:00+00:00",
    "GIT_COMMITTER_DATE": "2020-03-01T12:00:00+00:00",
}


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=GIT_ENV
    )
    return proc.stdout.strip()


def commit(root: Path, message: str, *paths: str) -> str:
    """Закоммитить `paths` (по умолчанию — всё) и вернуть хэш."""
    git(root, "add", "-A", *(["--", *paths] if paths else []))
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _rules(web_exclude: tuple[str, ...] = ("web.environment",)) -> dict[str, Any]:
    """Правила репозитория без библиотечных правил отсева, которым на фикстуре нечего отсеять.

    `web_exclude` — какие правила отсева фронта оставить (по `id`).
    """
    raw: dict[str, Any] = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    raw["dotnet"]["exclude"] = {"require_public": False}
    raw["web"]["exclude"]["rules"] = [
        rule for rule in raw["web"]["exclude"]["rules"] if rule["id"] in web_exclude
    ]
    return raw


# Ключа нет — `web.roots` в настройке не пишется (умолчание `["."]`).
NO_KEY: Final = object()


def workspace(
    root: Path,
    *,
    init: bool = True,
    front: bool = True,
    web_roots: Any = ("frontend",),
    rules: dict[str, Any] | None = None,
) -> Path:
    """Копия шва с настройкой в `setup/`; `init` — git-репозиторий с первым коммитом.

    `front` — с каталогом `frontend`; `web_roots` — значение `web.roots`
    (`NO_KEY` — ключа нет); `rules` — набор правил вместо `_rules()`.
    """
    ignored = () if front else ("frontend",)
    shutil.copytree(SEAM, root, ignore=shutil.ignore_patterns(".docpipe", "docpipe.yaml", *ignored))
    setup = root / "setup"
    setup.mkdir()
    rules_file = setup / "rules.yaml"
    rules_file.write_text(
        yaml.safe_dump(rules if rules is not None else _rules(), allow_unicode=True),
        encoding="utf-8",
    )
    web: dict[str, Any] = {"rules": str(rules_file), "url_rewrite": [REWRITE]}
    if web_roots is not NO_KEY:
        web["roots"] = list(web_roots)
    config = {"roots": ["backend"], "rules": str(rules_file), "web": web}
    (setup / "docpipe.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
    )
    if init:
        git(root, "init", "-q", "-b", "main")
        commit(root, "настройка и код")
    return root


def context(root: Path, config: Path | None = None) -> SetupContext:
    return SetupContext.build(root, config or root / "setup" / "docpipe.yaml", use_cache=False)


def review(root: Path, **kwargs: Any) -> Review:
    return build_review(context(root), **kwargs)


def add_reports(root: Path) -> None:
    write(root, CONTROLLER, CONTROLLER_CODE)
    write(root, SERVICE, SERVICE_CODE)


def rules_label(root: Path) -> str:
    return (root / "setup" / "rules.yaml").as_posix()


def config_label(root: Path) -> str:
    return (root / "setup" / "docpipe.yaml").as_posix()


def edit_rules(root: Path, *exclusions: dict[str, Any]) -> None:
    path = root / "setup" / "rules.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["dotnet"]["exclude"].setdefault("rules", []).extend(exclusions)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


# --------------------------------------------------------------------------------------
# База и новый код
# --------------------------------------------------------------------------------------


def test_clean_commit_has_nothing_new_and_nothing_dead(tmp_path: Path) -> None:
    """Сразу после коммита настройки ревью пусто: и `--fail-on-changes` — код 0."""
    root = workspace(tmp_path / "repo")
    base = git(root, "rev-parse", "HEAD")
    report = review(root)
    assert (report.base, report.since, report.status) == (base, None, None)
    assert report.config_files == [config_label(root), rules_label(root)]
    assert (report.config_dirty, report.config_changes) == (False, [])
    assert (report.new_files, report.new_files_total) == ([], 0)
    assert (report.applied, report.new_findings, report.dead_decisions) == ([], [], [])
    assert not has_changes(report)
    assert "Нового кода после базы нет." in format_review(report)

    args = ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"]
    assert runner.invoke(app, [*args, "--fail-on-changes"]).exit_code == 0


def test_new_controller_and_service_name_rules_and_findings(tmp_path: Path) -> None:
    """Критерий приёмки: новый контроллер и сервис фронта после коммита настройки.

    `applied` называет правила, решившие их символы, `new_findings` — эндпоинт
    нового контроллера без вызывающего и новый вызов без эндпоинта.
    """
    root = workspace(tmp_path / "repo")
    base = git(root, "rev-parse", "HEAD")
    add_reports(root)
    commit(root, "новый код")

    report = review(root)
    assert report.base == base  # коммит кода базу не двигает
    assert report.new_files == [CONTROLLER, SERVICE]
    assert (report.outside_area, report.config_dirty) == ([], False)

    applied = {item.id: item for item in report.applied}
    controller = applied[decision_id(rules_label(root), "dotnet.rules", "controller.aspnet")]
    assert (controller.count, controller.files, controller.files_total) == (1, [CONTROLLER], 1)
    assert controller.total > controller.count  # во всём репозитории контроллеров больше
    service = applied[decision_id(rules_label(root), "web.rules", "web.service")]
    assert service.files == [SERVICE]
    # Корни и запись `url_rewrite` тоже применились: новые файлы — в их охвате.
    assert decision_id(config_label(root), "roots", "backend") in applied
    rewrite = applied[decision_id(config_label(root), "web.url_rewrite", "seam-web")]
    assert (rewrite.files, rewrite.reason) == ([SERVICE], REWRITE["reason"])

    found = {item.code: item for item in report.new_findings}
    without_caller = found["link.endpoints_without_caller"]
    assert without_caller.places == 1 and without_caller.count > 1
    [example] = without_caller.examples
    assert example.startswith(f"{CONTROLLER}:") and "api/reports/weekly" in example
    without_endpoint = found["link.calls_without_endpoint"]
    assert [item.split("  ", 1)[1] for item in without_endpoint.examples] == [
        "GET api/reports/monthly"
    ]
    assert without_endpoint.examples[0].startswith(f"{SERVICE}:")
    # Невосстановленные вызовы старых файлов — не новые находки.
    assert "link.calls_unresolved" not in found
    assert has_changes(report)


def test_new_exclusion_rule_that_took_new_code_is_applied_with_reason(tmp_path: Path) -> None:
    """Критерий приёмки: новое правило отсева, забравшее новый код, — в `applied` с причиной."""
    root = workspace(tmp_path / "repo")
    first = git(root, "rev-parse", "HEAD")
    add_reports(root)
    commit(root, "новый код")
    edit_rules(root, REPORTS_RULE)

    report = review(root)
    assert report.base == first  # незакоммиченная правка настройки базу не двигает
    assert (report.config_dirty, report.config_changes) == (True, [rules_label(root)])
    rule_id = decision_id(rules_label(root), "dotnet.exclude", "reports.internal")
    [rule] = [item for item in report.applied if item.id == rule_id]
    assert (rule.reason, rule.count, rule.files, rule.total) == (
        REPORTS_RULE["reason"],
        1,
        [CONTROLLER],
        1,
    )
    # Сверху — записи «не берём», внутри обеих групп — по числу нового кода.
    assert report.applied[0].id == rule_id
    order = [(not is_refusal(item.key), -item.count) for item in report.applied]
    assert order == sorted(order)
    assert "Настройка изменена и не закоммичена" in format_review(report)

    # Правило закоммичено — база сдвинулась, и тот же код уже не новый;
    # прежняя база — явным `--since`.
    after = commit(root, "правило отсева отчётов", "setup")
    assert review(root).base == after
    assert review(root).applied == []
    explicit = review(root, since=first)
    assert (explicit.base, explicit.since) == (first, first)
    assert rule_id in {item.id for item in explicit.applied}


def test_refusal_that_took_new_code_comes_before_bigger_positive_decisions(tmp_path: Path) -> None:
    """Запись «не берём» в `applied` — первой, даже когда у корней новых файлов больше.

    Прогон S31 на squidex после S33: отсев `data.contracts`, забравший новый
    DTO (1 файл), стоял седьмым — за `roots`, правилами владения и `web.roots`
    (по 2 файла), а фаза 90 велит показывать его первым. Страница `applied`
    (`offset`, бюджет сервера) срезала бы его раньше корней.
    """
    root = workspace(tmp_path / "repo")
    add_reports(root)
    # Второй новый файл .NET, которого правило отсева отчётов не берёт: у корня — 2.
    write(root, DIGEST, DIGEST_CODE)
    commit(root, "новый код")
    edit_rules(root, REPORTS_RULE)

    report = review(root)
    rule_id = decision_id(rules_label(root), "dotnet.exclude", "reports.internal")
    roots = decision_id(config_label(root), "roots", "backend")
    applied = {item.id: item for item in report.applied}
    assert (applied[rule_id].count, applied[roots].count) == (1, 2)
    assert [item.id for item in report.applied][:1] == [rule_id]
    assert [is_refusal(item.key) for item in report.applied] == [True] + [False] * (
        len(report.applied) - 1
    )
    positive = [item.count for item in report.applied[1:]]
    assert positive == sorted(positive, reverse=True)
    # Текст печатает в том же порядке: первая строка раздела — отсев.
    text = format_review(report)
    section = text.split("Решения, применившиеся к новому коду:\n", 1)[1]
    assert section.startswith(f"  {rule_id} — 1")


def test_refusal_keys_cover_every_negative_record_of_the_coverage() -> None:
    """Записи «не берём»: отсев, область, «не обёртка», `remove`, секция `link`; прочие — нет."""
    for key in (
        "exclude",
        "not_enrolled",
        "dotnet.exclude",
        "web.exclude",
        "web.not_wrappers",
        "remove",
        "link.unresolvable",
        "link.external_targets",
        "link.external_callers",
    ):
        assert is_refusal(key), key
    for key in (
        "roots",
        "enrolled",
        "web.roots",
        "dotnet.rules",
        "web.rules",
        "di_methods",
        "dispatch_interfaces",
        "web.url_rewrite",
        "web.http_wrappers",
        "web.url_builders",
        "web.registry_calls",
        "add",
        "features",
        "rules",
    ):
        assert not is_refusal(key), key


def test_removed_module_with_url_rewrite_is_a_dead_decision(tmp_path: Path) -> None:
    """Критерий приёмки: удалить модуль, на который есть `url_rewrite`, — в `dead_decisions`."""
    root = workspace(tmp_path / "repo")
    rewrite = decision_id(config_label(root), "web.url_rewrite", "seam-web")
    assert review(root).dead_decisions == []

    git(root, "rm", "-rq", "frontend")
    commit(root, "фронт уехал в свой репозиторий")
    report = review(root)
    dead = {item.id: item for item in report.dead_decisions}
    assert rewrite in dead and dead[rewrite].reason == REWRITE["reason"]
    assert decision_id(config_label(root), "web.roots", "frontend") in dead
    assert has_changes(report)
    assert f"  {rewrite} — {REWRITE['reason']}" in format_review(report)


def test_classification_rules_without_wins_are_not_dead(tmp_path: Path) -> None:
    """Правило классификации без побед — запас набора, а не мёртвое решение."""
    root = workspace(tmp_path / "repo")
    idle = [
        item
        for item in status_detail(context(root)).coverage
        if item.count == 0 and item.key in NOT_DEAD_KEYS
    ]
    assert idle  # на фикстуре их много: `web.guard`, `ignite.compute`, …
    assert review(root).dead_decisions == []


# --------------------------------------------------------------------------------------
# Записи без охвата: мёртвые, неприменимые, удержанные (S35)
# --------------------------------------------------------------------------------------

# Второй модуль фронта, у которого единственный вызов невосстановим: адрес
# приходит в ответе сервера (abp: модуль `components`, S31, ловушка 20).
WIDGETS_DIR: Final = "frontend/projects/widgets"
WIDGETS_FILE: Final = f"{WIDGETS_DIR}/src/lib/widget-links.service.ts"
WIDGETS_CODE: Final = """import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

// Гипермедиа: адрес — из ответа сервера, статически не восстановить.
@Injectable({ providedIn: 'root' })
export class WidgetLinksService {
  constructor(private http: HttpClient) {}

  follow(link: { href: string }): Observable<unknown> {
    return this.http.get(link.href);
  }
}
"""
WIDGETS_REWRITE: Final[dict[str, str]] = {
    "module": "widgets",
    "reason": "проверено: библиотека шлёт адрес из ответа как есть, префикса нет",
}
WIDGETS_UNRESOLVABLE: Final[dict[str, str]] = {
    "path": "**/widget-links.service.ts",
    "reason": "гипермедиа: адрес из ответа сервера",
}


def add_widgets(root: Path) -> None:
    """Проект `widgets` в `angular.json` фронта под `root` и его единственный файл."""
    angular = root / "frontend" / "angular.json"
    data = json.loads(angular.read_text(encoding="utf-8"))
    data["projects"]["widgets"] = {
        "projectType": "library",
        "root": "projects/widgets",
        "sourceRoot": "projects/widgets/src",
        "prefix": "lib",
    }
    angular.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    write(root, WIDGETS_FILE, WIDGETS_CODE)


def edit_config(root: Path, **sections: Any) -> None:
    """Дописать в `setup/docpipe.yaml`: `web` — в секцию `web`, остальное — наверх."""
    path = root / "setup" / "docpipe.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["web"].update(sections.pop("web", {}))
    raw.update(sections)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


def edit_web_rules(root: Path, rule_id: str, **fields: Any) -> None:
    """Дописать поля правилу отсева фронта `rule_id` в `setup/rules.yaml`."""
    path = root / "setup" / "rules.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    [rule] = [rule for rule in raw["web"]["exclude"]["rules"] if rule["id"] == rule_id]
    rule.update(fields)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


def test_url_rewrite_of_a_module_with_only_unresolved_calls_is_not_dead(tmp_path: Path) -> None:
    """Ловушка S31 (20): охват `url_rewrite` — все вызовы модуля, и невосстановленные тоже.

    Находка `link.module_without_rewrite` требует записи у модуля с любыми
    вызовами, а охват записи считал только восстановленные: пустая запись
    модуля, чей единственный вызов объявлен `link.unresolvable`, была мёртвой,
    а без неё возвращалась находка.
    """
    root = workspace(tmp_path / "repo", init=False)
    add_widgets(root)
    edit_config(
        root,
        web={"url_rewrite": [REWRITE, WIDGETS_REWRITE]},
        link={"unresolvable": [WIDGETS_UNRESOLVABLE]},
    )
    git(root, "init", "-q", "-b", "main")
    commit(root, "настройка, код и библиотека виджетов")

    rewrite = decision_id(config_label(root), "web.url_rewrite", "widgets")
    detail = status_detail(context(root))
    [covered] = [item for item in detail.coverage if item.id == rewrite]
    assert (covered.count, covered.files) == (1, {WIDGETS_FILE: 1})
    assert "link.module_without_rewrite" not in {item.code for item in detail.status.findings}
    # Вызов виджетов решён `link.unresolvable`; невосстановленные модуля `seam-web` — нет.
    unresolved = detail.places.get("link.calls_unresolved", ())
    assert not any(WIDGETS_FILE in place.files for place in unresolved)

    report = review(root)
    assert rewrite not in {item.id for item in report.dead_decisions}
    assert rewrite not in {item.id for item in report.inapplicable}
    assert not has_changes(report)


@pytest.mark.parametrize("web_roots", [NO_KEY, []], ids=["no-key", "empty"])
def test_repository_without_a_front_puts_front_records_into_inapplicable(
    tmp_path: Path, web_roots: Any
) -> None:
    """Фронта нет — записям фронта нечего решать: `no_front`, ревью зелёное.

    `web.roots` не задан (умолчание `["."]`) или задан `[]`, а шаг `web`
    не дал ни одного модуля: правила `web.exclude` набора, `url_rewrite`
    и записи секции `link` о вызовах фронта — в `inapplicable`.
    """
    root = workspace(tmp_path / "repo", init=False, front=False, web_roots=web_roots)
    edit_config(root, link={"unresolvable": [WIDGETS_UNRESOLVABLE]})
    git(root, "init", "-q", "-b", "main")
    commit(root, "настройка и код без фронта")

    report = review(root)
    assert report.dead_decisions == []
    by_id = {item.id: item for item in report.inapplicable}
    for key, value in (
        ("web.url_rewrite", "seam-web"),
        ("link.unresolvable", WIDGETS_UNRESOLVABLE["path"]),
    ):
        assert by_id[decision_id(config_label(root), key, value)].why == "no_front"
    environment = by_id[decision_id(rules_label(root), "web.exclude", "web.environment")]
    assert (environment.why, environment.detail, environment.reason) == (
        "no_front",
        [],
        _rules()["web"]["exclude"]["rules"][0]["reason"],
    )
    assert not has_changes(report)
    assert "Неприменимо по построению: 3 (фронта нет — 3, под `exclude` — 0" in format_review(
        report
    )

    args = ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"]
    assert runner.invoke(app, [*args, "--fail-on-changes"]).exit_code == 0


def test_without_a_front_the_neutral_set_is_green_once_idle_dotnet_rules_are_kept(
    tmp_path: Path,
) -> None:
    """Критерий приёмки: копия шва без фронта, нейтральный набор правил — ревью зелёное.

    Секция `web` набора и `require_public` — неприменимы по построению.
    Четыре библиотечных отсева `dotnet` на бэке фикстуры не решают ничего
    и законно мертвы: тестов, генерата, `*Dto` и перечислений там нет.
    Адрес каждого — `dotnet.exclude.rules[].unused_reason`; с причиной
    человека они в `kept_unused`, и `--fail-on-changes` — код 0.
    """
    neutral: dict[str, Any] = yaml.safe_load(BUNDLE_RULES.read_text(encoding="utf-8"))
    assert neutral["dotnet"]["exclude"]["require_public"] is True  # набор как есть
    root = workspace(tmp_path / "repo", front=False, web_roots=NO_KEY, rules=neutral)
    args = ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"]

    report = review(root)
    library = {"data.contracts", "enums", "generated.code", "tests"}
    assert {item.value for item in report.dead_decisions} == library
    assert {item.keep_key for item in report.dead_decisions} == {
        "dotnet.exclude.rules[].unused_reason"
    }
    why = {item.value: item.why for item in report.inapplicable}
    web_rules = {rule["id"] for rule in neutral["web"]["exclude"]["rules"]}
    assert why == {"exclude.require_public": "switch"} | dict.fromkeys(
        [*web_rules, "seam-web"], "no_front"
    )
    assert runner.invoke(app, [*args, "--fail-on-changes"]).exit_code == 1

    for rule in neutral["dotnet"]["exclude"]["rules"]:
        rule["unused_reason"] = "набор поставки: держим на случай тестов и генерата"
    (root / "setup" / "rules.yaml").write_text(
        yaml.safe_dump(neutral, allow_unicode=True), encoding="utf-8"
    )
    commit(root, "причины держать отсевы набора без охвата")
    kept = review(root)
    assert kept.dead_decisions == []
    assert {item.value for item in kept.kept_unused} == library
    assert {item.unused_reason for item in kept.kept_unused} == {
        "набор поставки: держим на случай тестов и генерата"
    }
    assert "Держатся без охвата с причиной (`unused_reason`): 4." in format_review(kept)
    assert runner.invoke(app, [*args, "--fail-on-changes"]).exit_code == 0


def test_exclusion_rule_whose_files_exclude_took_is_under_exclude(tmp_path: Path) -> None:
    """`exclude` спеков отсёк всё, что решал `web.spec`: правило — `under_exclude`.

    По файлам, а не по глобам: без `exclude` тот же спек читается и правило
    его выигрывает; с `exclude` прочитанных спеков нет, отсечённый — есть.
    """
    spec = "frontend/src/app/x.spec.ts"
    root = workspace(tmp_path / "repo", init=False, rules=_rules(("web.environment", "web.spec")))
    write(root, spec, "export class XSpecHarness {}\n")
    git(root, "init", "-q", "-b", "main")
    commit(root, "спек")
    rule = decision_id(rules_label(root), "web.exclude", "web.spec")
    [read] = [item for item in status_detail(context(root)).coverage if item.id == rule]
    assert read.count == 1  # без `exclude` спек читается, правило его выигрывает

    edit_config(root, exclude=[{"glob": "frontend/**/*.spec.ts", "reason": "тесты не продукт"}])
    commit(root, "спеки исключены обходом")
    report = review(root)
    [cut] = [item for item in report.inapplicable if item.id == rule]
    assert (cut.why, cut.detail) == ("under_exclude", ["frontend/**/*.spec.ts"])
    assert rule not in {item.id for item in report.dead_decisions}


def test_require_public_without_coverage_is_a_switch(tmp_path: Path) -> None:
    """`exclude.require_public` — переключатель секции, а не запись о группе типов."""
    rules = _rules()
    rules["dotnet"]["exclude"]["require_public"] = True
    root = workspace(tmp_path / "repo", rules=rules)  # все типы бэка фикстуры — public
    report = review(root)
    [switch] = report.inapplicable
    assert (switch.key, switch.value, switch.why) == (
        "dotnet.exclude",
        "exclude.require_public",
        "switch",
    )
    assert report.dead_decisions == [] and not has_changes(report)


def test_unused_reason_keeps_an_idle_rule_and_dead_ones_have_an_address(tmp_path: Path) -> None:
    """Мёртвое правило отсева — с адресом причины, удержанное — в `kept_unused`.

    У мёртвой записи `docpipe.yaml` адреса причины нет: шаблон, который не
    отсекает ничего, правят или удаляют.
    """
    root = workspace(tmp_path / "repo", init=False, rules=_rules(("web.environment", "web.mock")))
    edit_config(root, exclude=[{"glob": "nothing/**", "reason": "на всякий случай"}])
    git(root, "init", "-q", "-b", "main")
    commit(root, "настройка")
    mock = decision_id(rules_label(root), "web.exclude", "web.mock")
    pattern = decision_id(config_label(root), "exclude", "nothing/**")

    report = review(root)
    dead = {item.id: item for item in report.dead_decisions}
    assert dead[mock].keep_key == "web.exclude.rules[].unused_reason"
    assert dead[pattern].keep_key is None
    text = format_review(report)
    assert f"удалить запись: {rules_label(root)} → web.exclude, web.mock; или держать" in text
    assert f"удалить запись: {config_label(root)} → exclude, nothing/**\n" in text

    edit_web_rules(root, "web.mock", unused_reason="моки появятся вместе с тестами фронта")
    commit(root, "моки держим")
    kept = review(root)
    assert mock not in {item.id for item in kept.dead_decisions}
    [item] = kept.kept_unused
    assert (item.id, item.unused_reason) == (mock, "моки появятся вместе с тестами фронта")
    # Запись `docpipe.yaml` по-прежнему мертва: ревью красное, пока её не уберут.
    assert has_changes(kept)


def test_uncommitted_config_edit_is_config_dirty(tmp_path: Path) -> None:
    """Критерий приёмки: незакоммиченная правка настройки — `config_dirty`, не новый код."""
    root = workspace(tmp_path / "repo")
    config = root / "setup" / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["exclude"] = [{"glob": "**/environment.ts", "reason": "значения сборки"}]
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    report = review(root)
    assert (report.config_dirty, report.config_changes) == (True, [config_label(root)])
    assert report.new_files == []
    text = format_review(report)
    assert f"Настройка изменена и не закоммичена: {config_label(root)}" in text


def test_config_at_repository_root_does_not_move_base_with_code(tmp_path: Path) -> None:
    """Ловушка: `docpipe.yaml` в корне — каталог настройки равен всему репозиторию.

    База — коммит **файлов** настройки, а не каталога: иначе коммит кода
    сдвигал бы её, а любая правка исходника давала бы `config_dirty`.
    """
    root = tmp_path / "repo"
    shutil.copytree(SEAM, root, ignore=shutil.ignore_patterns(".docpipe"))
    config = root / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(RULES.resolve())
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    base = commit(root, "настройка и код")
    write(root, CONTROLLER, CONTROLLER_CODE)
    commit(root, "новый контроллер")
    write(root, SERVICE, SERVICE_CODE)  # неотслеживаемый — тоже новый код

    report = build_review(SetupContext.build(root, config, use_cache=False))
    assert report.base == base
    # Набор правил вне репозитория в историю этого репозитория не входит.
    assert report.config_files == [config.as_posix()]
    assert (report.config_dirty, report.new_files) == (False, [CONTROLLER, SERVICE])


def test_root_inside_a_larger_repository(tmp_path: Path) -> None:
    """Ловушка: `--root` — подкаталог репозитория; пути — от `--root` и только под ним."""
    mono = tmp_path / "mono"
    root = workspace(mono / "app", init=False)
    write(mono, "other/readme.md", "соседний проект\n")
    git(mono, "init", "-q", "-b", "main")
    base = commit(mono, "монорепозиторий")
    write(root, CONTROLLER, CONTROLLER_CODE)  # неотслеживаемый
    write(root, SERVICE, SERVICE_CODE)
    git(mono, "add", "--", f"app/{SERVICE}")  # в индексе, не в коммите
    write(mono, "other/new.md", "чужое\n")

    report = review(root)
    assert report.base == base
    assert report.new_files == [CONTROLLER, SERVICE]
    assert report.config_files == [config_label(root), rules_label(root)]


def test_new_files_outside_roots_are_named(tmp_path: Path) -> None:
    """Файл вне `roots`/`web.roots` — новый, но отмечен: обход его не читает."""
    root = workspace(tmp_path / "repo")
    write(root, "tools/deploy.sh", "echo deploy\n")
    write(root, CONTROLLER, CONTROLLER_CODE)
    report = review(root)
    assert report.new_files == [CONTROLLER, "tools/deploy.sh"]
    assert (report.outside_area, report.outside_area_total) == (["tools/deploy.sh"], 1)


def test_limit_cuts_lists_but_keeps_totals(tmp_path: Path) -> None:
    root = workspace(tmp_path / "repo")
    add_reports(root)
    audit = CONTROLLER.replace("Reports", "Audit")
    write(root, audit, CONTROLLER_CODE.replace("Reports", "Audit").replace("reports", "audit"))
    report = review(root, limit=1)
    assert (report.new_files, report.new_files_total) == ([audit], 3)
    controller = next(item for item in report.applied if item.value == "controller.aspnet")
    assert (controller.files, controller.files_total, controller.count) == ([audit], 2, 2)
    without_caller = next(
        item for item in report.new_findings if item.code == "link.endpoints_without_caller"
    )
    assert (len(without_caller.examples), without_caller.places) == (1, 3)
    assert "… и ещё 2" in format_review(report)
    with pytest.raises(InputError):
        review(root, limit=-1)


# --------------------------------------------------------------------------------------
# Без истории
# --------------------------------------------------------------------------------------


def test_config_never_committed_gives_status_with_note(tmp_path: Path) -> None:
    """Критерий приёмки: настройка без коммитов — отчёт равен `setup status` с пометкой."""
    root = workspace(tmp_path / "repo", init=False)
    git(root, "init", "-q", "-b", "main")
    commit(root, "только код", "backend", "frontend")

    report = review(root)
    ctx = context(root)
    assert report.base is None
    assert report.status == build_status(ctx)
    assert [note.code for note in report.notes] == [NOTE_UNCOMMITTED]
    # Неотслеживаемая настройка — тоже несохранённая.
    assert report.config_dirty and report.config_changes == [config_label(root), rules_label(root)]
    assert (report.applied, report.new_findings, report.dead_decisions) == ([], [], [])
    assert (report.unexplained, report.defects) == (
        report.status.unexplained,
        report.status.defects,
    )
    assert has_changes(report) == bool(report.status.unexplained or report.status.defects)

    text = format_review(report)
    assert text.startswith("Настройка ещё не закоммичена")
    assert "\n\nВ области: " in text


def test_repository_without_commits_is_uncommitted(tmp_path: Path) -> None:
    root = workspace(tmp_path / "repo", init=False)
    git(root, "init", "-q", "-b", "main")
    report = review(root)
    assert report.base is None and report.status is not None
    assert [note.code for note in report.notes] == [NOTE_UNCOMMITTED]


def test_config_outside_the_repository_has_no_base(tmp_path: Path) -> None:
    """Настройка не в этом репозитории — базы нет; явный `--since` работает."""
    root = workspace(tmp_path / "repo")
    outside = tmp_path / "elsewhere"
    shutil.copytree(root / "setup", outside)
    config = outside / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rules"] = raw["web"]["rules"] = str(outside / "rules.yaml")
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    report = build_review(context(root, config))
    assert report.base is None and report.config_files == []
    assert [note.code for note in report.notes] == [NOTE_OUTSIDE]
    head = git(root, "rev-parse", "HEAD")
    assert build_review(context(root, config), since="HEAD").base == head
    # Набор правил названный из чужой настройки, но лежащий в репозитории, — его история.
    raw["rules"] = raw["web"]["rules"] = rules_label(root)
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    inside = build_review(context(root, config))
    assert (inside.base, inside.config_files) == (head, [rules_label(root)])


def test_not_a_git_repository_is_refused(tmp_path: Path) -> None:
    """Не git — код 2: «ревью опирается на git»."""
    root = workspace(tmp_path / "repo", init=False)
    with pytest.raises(HistoryError, match="ревью опирается на git"):
        review(root)
    result = runner.invoke(
        app,
        ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"],
    )
    assert result.exit_code == 2
    assert "Ревью не построено: ревью опирается на git" in result.output


def test_unknown_since_is_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path / "repo")
    for revision in ("no-such-branch", "--all"):
        with pytest.raises(HistoryError, match="--since"):
            review(root, since=revision)
    args = ["setup", "review", "--root", str(root), "--config", config_label(root)]
    result = runner.invoke(app, [*args, "--since", "no-such-branch", "--no-cache"])
    assert result.exit_code == 2 and "--since" in result.output


def test_shallow_clone_refuses_the_base_and_takes_since(tmp_path: Path) -> None:
    """Ловушка: на границе неглубокого клона `git log -- <настройка>` даёт границу.

    Граница для git — корневой коммит: он «добавляет» все файлы, и база
    съехала бы на него молча. Отказ — с подсказкой `--since`.
    """
    source = workspace(tmp_path / "source")
    add_reports(source)
    commit(source, "новый код")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", source.resolve().as_uri(), str(clone)],
        check=True,
        capture_output=True,
        env=GIT_ENV,
    )
    with pytest.raises(HistoryError, match="--since"):
        review(clone)
    assert review(clone, since="HEAD").base == git(clone, "rev-parse", "HEAD")


def test_submodule_in_area_is_noted(tmp_path: Path) -> None:
    """Ловушка: изменения внутри подмодуля git diff родителя не видит — строка отчёта."""
    root = workspace(tmp_path / "repo")
    head = git(root, "rev-parse", "HEAD")
    # Запись подмодуля в индексе без клона внутри: `git diff` видит у подмодуля
    # только ссылку, и ровно это ревью обязано сказать.
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{head},backend/External")
    report = review(root)
    [note] = [note for note in report.notes if note.code == NOTE_SUBMODULES]
    assert "backend/External" in note.message
    assert "backend/External" not in report.new_files


def test_defects_are_noted_and_fail(tmp_path: Path) -> None:
    """Дефект прогона — пометка и код 1: ревью по прогону, который не собрался, неполно."""
    root = workspace(tmp_path / "repo")
    config = root / "setup" / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["web"]["pages"] = str(root / "setup" / "pages.yaml")  # названный файл, которого нет
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    commit(root, "pages.yaml назван")

    report = review(root)
    assert report.defects > 0 and report.new_findings == []
    assert NOTE_DEFECTS in {note.code for note in report.notes}
    assert has_changes(report)


# --------------------------------------------------------------------------------------
# Места находок — вход ревью
# --------------------------------------------------------------------------------------

# Находки без места в файле: ревью к новому коду их не относит.
PLACELESS: Final = frozenset(
    {"config.problems", "load.errors", "docs.unavailable", "pages.stale_overrides"}
)


def test_every_finding_with_a_file_has_all_its_places(tmp_path: Path) -> None:
    """Сторож: у находки с файлом мест столько же, сколько у неё самой.

    Находка, которая не отдала мест, ревью не увидит никогда — молча.
    У `link.module_without_rewrite` число — модулей, а места — вызовы.
    """
    root = workspace(tmp_path / "repo", init=False)
    config = root / "setup" / "docpipe.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    del raw["web"]["url_rewrite"]
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    detail = status_detail(context(root))
    codes = {item.code for item in detail.status.findings}
    assert {"dotnet.undecided", "link.endpoints_without_caller", "link.calls_invisible"} <= codes
    assert "link.module_without_rewrite" in codes
    for finding in detail.status.findings:
        places = detail.places.get(finding.code, ())
        if finding.code in PLACELESS:
            assert places == (), finding.code
        elif finding.code == "link.module_without_rewrite":
            assert len(places) == sum(cluster.count for cluster in finding.clusters)
        else:
            assert len(places) == finding.count, finding.code
            assert all(place.files for place in places), finding.code


# --------------------------------------------------------------------------------------
# Детерминизм и команда
# --------------------------------------------------------------------------------------


def test_two_runs_are_byte_identical_and_command_equals_function(tmp_path: Path) -> None:
    root = workspace(tmp_path / "repo")
    add_reports(root)
    commit(root, "новый код")
    first, second = (review_json(review(root)) for _ in range(2))
    assert first == second

    args = ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"]
    outputs = [runner.invoke(app, [*args, "--format", "json"]) for _ in range(2)]
    assert outputs[0].exit_code == 0, outputs[0].output
    assert outputs[0].stdout == outputs[1].stdout
    assert outputs[0].stdout.rstrip("\n") == first.rstrip("\n")
    assert Review.model_validate_json(outputs[0].stdout).base == git(root, "rev-parse", "HEAD~1")

    text = runner.invoke(app, args)
    assert text.exit_code == 0
    assert text.stdout.startswith(f"Ревью от {git(root, 'rev-parse', 'HEAD~1')[:12]} ")
    assert runner.invoke(app, [*args, "--fail-on-changes"]).exit_code == 1


def test_command_does_not_write_into_the_repository(tmp_path: Path) -> None:
    """С `--no-cache` команда не пишет ничего — ни в дерево, ни в индекс git."""
    root = workspace(tmp_path / "repo")
    add_reports(root)
    index = root / ".git" / "index"
    before = (
        sorted(path.relative_to(root) for path in root.rglob("*") if ".git" not in path.parts),
        index.read_bytes(),
    )
    args = ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"]
    assert runner.invoke(app, args).exit_code == 0
    after = (
        sorted(path.relative_to(root) for path in root.rglob("*") if ".git" not in path.parts),
        index.read_bytes(),
    )
    assert after == before


@pytest.mark.parametrize("extra", [["--format", "jsno"], ["--limit", "-1"]])
def test_bad_arguments_are_code_2(tmp_path: Path, extra: list[str]) -> None:
    root = workspace(tmp_path / "repo")
    args = ["setup", "review", "--root", str(root), "--config", config_label(root), "--no-cache"]
    assert runner.invoke(app, [*args, *extra]).exit_code == 2
