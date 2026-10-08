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
    review_json,
)
from docpipe.setup.status import build_status, decision_id, status_detail

runner = CliRunner()

SEAM: Final = Path("tests/fixtures/SeamWorkspace")
RULES: Final = Path("rules/rules.yaml")

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


def _rules() -> dict[str, Any]:
    """Правила репозитория без библиотечных правил отсева, которым на фикстуре нечего отсеять."""
    raw: dict[str, Any] = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    raw["dotnet"]["exclude"] = {"require_public": False}
    raw["web"]["exclude"]["rules"] = [
        rule for rule in raw["web"]["exclude"]["rules"] if rule["id"] == "web.environment"
    ]
    return raw


def workspace(root: Path, *, init: bool = True) -> Path:
    """Копия шва с настройкой в `setup/`; `init` — git-репозиторий с первым коммитом."""
    shutil.copytree(SEAM, root, ignore=shutil.ignore_patterns(".docpipe", "docpipe.yaml"))
    setup = root / "setup"
    setup.mkdir()
    rules = setup / "rules.yaml"
    rules.write_text(yaml.safe_dump(_rules(), allow_unicode=True), encoding="utf-8")
    config = {
        "roots": ["backend"],
        "rules": str(rules),
        "web": {"roots": ["frontend"], "rules": str(rules), "url_rewrite": [REWRITE]},
    }
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
    # Сверху — решение, забравшее больше всего нового кода.
    counts = [item.count for item in report.applied]
    assert counts == sorted(counts, reverse=True)
    assert "Настройка изменена и не закоммичена" in format_review(report)

    # Правило закоммичено — база сдвинулась, и тот же код уже не новый;
    # прежняя база — явным `--since`.
    after = commit(root, "правило отсева отчётов", "setup")
    assert review(root).base == after
    assert review(root).applied == []
    explicit = review(root, since=first)
    assert (explicit.base, explicit.since) == (first, first)
    assert rule_id in {item.id for item in explicit.applied}


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
