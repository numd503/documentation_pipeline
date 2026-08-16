"""Классификация символов фронта (F12).

Про каждый символ должно быть принято решение, и «не документируем» — тоже
решение. Пока «не решено» смешивалось с «решено не документировать», отчёт
не мог отличить «ещё не смотрели» от «посмотрели и решили», и появление нового
типа в репозитории в большой цифре не выделялось.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.stats import UNDECIDED, collect_stats
from docpipe.web.tree import run as run_web
from tests.conftest import sectioned

runner = CliRunner()
RULES = Path("rules/rules.yaml")


@pytest.fixture
def result(web_workspace: Path) -> Manifest:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


def _stats(web_workspace: Path, rules: Path = RULES) -> tuple[dict[str, int], dict[str, str]]:
    ruleset = load_ruleset(rules, "web")
    scan = run_web(web_workspace, DocpipeConfig(), ruleset)
    enrolled = {
        module.id.removeprefix("module:") for module in scan.manifest.modules if module.enrolled
    }
    statistics = collect_stats(scan.index, scan.manifest.nodes, ruleset, enrolled)
    return dict(statistics.counts), {rule: reason for rule, reason, _ in statistics.skipped}


# --------------------------------------------------------------------------------------
# Решение принято про каждый символ
# --------------------------------------------------------------------------------------


def test_nothing_is_undecided_on_the_fixture(web_workspace: Path) -> None:
    """Единственное число, которое обязано идти к нулю."""
    counts, _ = _stats(web_workspace)
    assert counts.get(UNDECIDED, 0) == 0


def test_every_exclusion_carries_a_reason(web_workspace: Path) -> None:
    """Иначе «не документируем 4820» — снова безымянное число."""
    counts, reasons = _stats(web_workspace)

    assert set(reasons) == {"web.environment", "web.module-constant", "web.spec"}
    assert all(reason.strip() for reason in reasons.values())


def test_exclusion_without_a_reason_is_refused_at_load(tmp_path: Path) -> None:
    """Причина обязательна на уровне загрузки, а не на уровне ревью."""
    path = tmp_path / "web.yaml"
    path.write_text(
        sectioned(
            {
                "ruleset_version": "x",
                "exclude": {"rules": [{"id": "no.reason", "when": {"type_kind": ["const"]}}]},
                "rules": [],
            },
            "web",
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="reason"):
        load_ruleset(path, "web")


# --------------------------------------------------------------------------------------
# Виды
# --------------------------------------------------------------------------------------


def test_kinds_on_the_fixture(result: Manifest) -> None:
    kinds = sorted({node.kind for node in result.nodes})
    assert kinds == [
        "action",
        "api-service",
        "component",
        "dto",
        "interceptor",
        "ng-module",
        "page",
        "routes",
        "service",
        "state",
    ]


def test_interceptor_wins_over_service(result: Manifest) -> None:
    """У класса-интерцептора есть и `@Injectable`, и `implements HttpInterceptor`.

    Без приоритета он стал бы обычным сервисом, и правило по интерцепторам
    выглядело бы работающим — оно просто никогда не побеждало бы.
    """
    by_title = {node.title: node for node in result.nodes}
    assert by_title["FixUrlInterceptor"].kind == "interceptor"
    assert by_title["authInterceptor"].kind == "interceptor"


def test_functional_interceptor_survives_the_constant_exclusion(result: Manifest) -> None:
    """Отсев констант проверяется ДО правил и съел бы его вместе с ними.

    В АС CF интерцепторы и guard'ы объявлены именно константами, так что
    без выреза в дереве не осталось бы ни одного.
    """
    assert any(node.title == "authInterceptor" for node in result.nodes)


def test_component_with_a_route_becomes_a_page(result: Manifest) -> None:
    """Вид решается не правилом: таблица роутов межфайловая, символ о ней не знает."""
    by_title = {node.title: node for node in result.nodes}

    assert by_title["QuizComponent"].kind == "page"
    assert by_title["QuizComponent"].routes[0].path == "models/loader/quiz"
    # Компонент, до которого по таблице не дойти, — переиспользуемый.
    assert by_title["LegacyBannerComponent"].kind == "component"


def test_service_with_http_calls_becomes_an_api_service(result: Manifest) -> None:
    by_title = {node.title: node for node in result.nodes}

    assert by_title["ItemsService"].kind == "api-service"
    assert by_title["UrlDecoratorService"].kind == "service"


def test_promotion_keeps_the_rule_that_matched(result: Manifest) -> None:
    """В `matched_rules` записано, что сработало, а не что получилось.

    Иначе по отчёту нельзя понять, какое правило настраивать.
    """
    page = next(node for node in result.nodes if node.kind == "page")
    assert page.matched_rules == ["web.component"]


# --------------------------------------------------------------------------------------
# Атрибуция решения
# --------------------------------------------------------------------------------------


def test_rule_order_in_the_file_does_not_change_the_numbers(
    web_workspace: Path, tmp_path: Path, result: Manifest
) -> None:
    """Атрибуция — по `priority`, затем по `id`, никогда по порядку в файле.

    Иначе перестановка двух правил в YAML меняет цифры отчёта, а они идут
    в журнал и в CI.
    """
    document = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    raw = document["web"]
    raw["rules"] = list(reversed(raw["rules"]))
    raw["exclude"]["rules"] = list(reversed(raw["exclude"]["rules"]))
    raw = {"version": "1", "web": raw}

    shuffled = tmp_path / "web.yaml"
    shuffled.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    counts, reasons = _stats(web_workspace, shuffled)
    original_counts, original_reasons = _stats(web_workspace)

    assert counts == original_counts
    assert reasons == original_reasons

    reordered = run_web(web_workspace, DocpipeConfig(), load_ruleset(shuffled, "web")).manifest
    assert [(node.id, node.kind) for node in reordered.nodes] == [
        (node.id, node.kind) for node in result.nodes
    ]


def test_public_requirement_is_off_for_typescript(web_workspace: Path) -> None:
    """`require_public: true` отсеял бы ВЕСЬ фронт, и молча.

    У TypeScript модификатора `public` на уровне объявления нет вовсе:
    видимость задаётся словом `export`.
    """
    assert load_ruleset(RULES, "web").exclude.require_public is False


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def test_stats_flag_writes_nothing(web_workspace: Path, tmp_path: Path) -> None:
    out = tmp_path / "w.json"
    invocation = runner.invoke(
        app, ["web", "scan", "--root", str(web_workspace), "--out", str(out), "--stats"]
    )

    assert invocation.exit_code == 0, invocation.output
    assert "решение не принято" in invocation.output.lower()
    assert not out.exists()


def test_fail_on_undecided_is_a_separate_flag(web_workspace: Path, tmp_path: Path) -> None:
    """Свой флаг, а не общий с шагом 1.

    Общий означал бы, что настройка фронта роняет CI бэкенда: на старте
    нерешённых много, красный CI выключат на второй день, и вместе с ним
    пропадут проверки, которые уже работают.
    """
    invocation = runner.invoke(
        app,
        [
            "web",
            "scan",
            "--root",
            str(web_workspace),
            "--out",
            str(tmp_path / "w.json"),
            "--fail-on-undecided",
        ],
    )
    assert invocation.exit_code == 0, invocation.output
