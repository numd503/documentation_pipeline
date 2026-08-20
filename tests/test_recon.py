"""Разведочный скрипт R01: `tools/recon.py`.

Скрипт стои́т снаружи пакета `docpipe` намеренно — его запускают там, где
`pip install` не проходит, поэтому у него не может быть ни одной зависимости,
кроме стандартной библиотеки и `git`. Тесты держат ровно это свойство, а не
только поведение: зависимость, добавленная «на минутку», ломает не тест,
а единственную среду, ради которой скрипт написан.
"""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

RECON_PATH = Path(__file__).resolve().parent.parent / "tools" / "recon.py"


@pytest.fixture(scope="module")
def recon() -> ModuleType:
    spec = importlib.util.spec_from_file_location("recon_under_test", RECON_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_AUTHOR_NAME": "Тестовый Автор",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Тестовый Автор",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
            "GIT_AUTHOR_DATE": "2020-03-01T12:00:00+00:00",
            "GIT_COMMITTER_DATE": "2020-03-01T12:00:00+00:00",
        },
    )


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "первый коммит")
    return root


# ──────────────────────────────────────────────────────────────────────────────
# Условия среды: без них скрипт не запустится там, где он нужен
# ──────────────────────────────────────────────────────────────────────────────


def test_imports_only_stdlib() -> None:
    """Сверх `git` и `python` ставить нечего — критерий приёмки R01 п. 4."""
    tree = ast.parse(RECON_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    outside = imported - set(sys.stdlib_module_names)
    assert not outside, f"внешние зависимости в разведочном скрипте: {sorted(outside)}"


def test_no_target_repository_specifics() -> None:
    """Ни одного имени, пути или префикса целевой системы — критерий R01 п. 3.

    Проверка структурная, потому что нарушают её не злым умыслом, а по одной
    мелочи за раз: «здесь пока захардкодим путь» — самый вероятный и самый
    тихий способ прирастить инструмент к одному репозиторию (риск Р-13).
    """
    text = RECON_PATH.read_text(encoding="utf-8").lower()
    for marker in (
        "cashflow",
        "sbt.",
        "jobtitle",
        "structure.xml",
        "tornado",
        "ignite",
        "grid-service",
    ):
        assert marker not in text, f"в разведочном скрипте встречается «{marker}»"


def test_no_network_calls() -> None:
    """Ноль исходящих соединений — критерий R01 п. 6.

    Проверяется по составу вызовов: `subprocess` зовётся только для `git`,
    сетевых модулей нет вовсе (это же следует из теста на стандартную
    библиотеку, но там разрешены `urllib` и `socket`).
    """
    text = RECON_PATH.read_text(encoding="utf-8")
    for marker in ("urllib", "socket", "http.client", "requests", "curl", "wget"):
        assert marker not in text, f"похоже на сетевой вызов: «{marker}»"


# ──────────────────────────────────────────────────────────────────────────────
# Форма отчёта
# ──────────────────────────────────────────────────────────────────────────────


def test_every_block_is_named_by_a_question(recon: ModuleType, tmp_path: Path) -> None:
    """Блок, для которого нельзя назвать вопрос, из скрипта удаляется (R01 п. 7).

    Тест держит это структурно: у каждого блока есть непустой `question`,
    и каждый вопрос виден в человеческой форме. Иначе правило продержится
    ровно до первого блока «а ещё мы собрали вот такое».
    """
    make_repo(tmp_path, {"app.py": "def run():\n    return 1\n"})
    report = recon.build_report(tmp_path, 12, 5, [])
    assert [block["id"] for block in report["blocks"]] == [
        block_id for block_id, _ in recon.QUESTIONS
    ]
    text = recon.render_text(report)
    for block in report["blocks"]:
        assert block["question"].endswith("?")
        assert block["question"] in text


def test_json_has_no_absolute_paths(recon: ModuleType, tmp_path: Path) -> None:
    """Абсолютных путей в машинной форме нет: иначе два одинаковых репозитория
    на разных машинах дают разные отчёты, и сравнить их нельзя."""
    make_repo(tmp_path, {"app.py": "x = 1\n"})
    report = recon.build_report(tmp_path, 12, 5, [])
    dumped = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in dumped
    assert report["repo"] == tmp_path.resolve().name


def test_two_runs_are_identical(recon: ModuleType, tmp_path: Path) -> None:
    """Один вход — один выход, обе формы (R01 п. 1)."""
    make_repo(
        tmp_path,
        {
            "src/a.py": "from src.b import helper\n\n\ndef run():\n    return helper()\n",
            "src/b.py": "def helper():\n    return 'api/things'\n",
            "web/app.ts": "export const url = 'api/things';\n",
        },
    )
    first = recon.build_report(tmp_path, 12, 5, [])
    second = recon.build_report(tmp_path, 12, 5, [])
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    assert recon.render_text(first) == recon.render_text(second)


def test_archaeology_window_counts_from_head_not_today(recon: ModuleType, tmp_path: Path) -> None:
    """Окно отсчитывается от даты HEAD.

    Коммит фикстуры сделан в 2020 году. Если бы окно считалось от «сегодня»,
    в него не попало бы ничего, и отчёт о том же репозитории менялся бы
    от запуска к запуску — то есть детерминизм держался бы календарём.
    """
    make_repo(tmp_path, {"app.py": "x = 1\n"})
    report = recon.build_report(tmp_path, 12, 5, [])
    archaeology = next(b for b in report["blocks"] if b["id"] == "archaeology")["data"]
    assert archaeology["available"] is True
    assert archaeology["since"].startswith("2019-")
    assert archaeology["commits_in_window"] == 1


def test_without_git_archaeology_says_so(recon: ModuleType, tmp_path: Path) -> None:
    """Отсутствие находки произносится вслух, а не молчит (Р7)."""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    report = recon.build_report(tmp_path, 12, 5, [])
    archaeology = next(b for b in report["blocks"] if b["id"] == "archaeology")["data"]
    assert archaeology["available"] is False
    assert "git" in archaeology["reason"]
    assert archaeology["reason"] in recon.render_text(report)


# ──────────────────────────────────────────────────────────────────────────────
# Что скрипт находит
# ──────────────────────────────────────────────────────────────────────────────


def test_repository_without_dotnet_and_angular(recon: ModuleType, tmp_path: Path) -> None:
    """Прогон на репозитории без .NET и без Angular даёт непустой осмысленный
    результат (R01 п. 2) — это первая проверка Р11 и её нельзя обойти
    рассуждением."""
    make_repo(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname = 'thing'\n",
            "pkg/__init__.py": "",
            "pkg/core.py": "def run():\n    return 1\n",
            "pkg/api.py": "from pkg.core import run\n\n\ndef handler():\n    return run()\n",
            "pkg/tasks.py": "from pkg.core import run\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 5, [])
    blocks = {block["id"]: block["data"] for block in report["blocks"]}
    languages = {row["language"] for row in blocks["composition"]["languages"]}
    assert "python" in languages
    assert "python" in blocks["composition"]["stacks"]
    assert blocks["structure"]["edges"] >= 2
    assert blocks["structure"]["center"], "центр не посчитан на питоновском репозитории"
    assert blocks["archaeology"]["available"] is True


def test_registry_candidate_by_literals(recon: ModuleType, tmp_path: Path) -> None:
    """Реестр — это пересечение, а не форма файла.

    Идентификаторы файла данных встречаются в коде строковыми литералами,
    а объявлений с такими именами нет: файл управляет кодом, а код о нём
    не знает по имени.
    """
    make_repo(
        tmp_path,
        {
            "config/triggers.xml": (
                "<Root>\n"
                '  <Trigger name="nightly-reindex" />\n'
                '  <Trigger name="hourly-sync" />\n'
                '  <Trigger name="weekly-report" />\n'
                "</Root>\n"
            ),
            "src/runner.py": (
                "TRIGGERS = ['nightly-reindex', 'hourly-sync', 'weekly-report']\n"
                "\n\ndef run(name):\n    return name in TRIGGERS\n"
            ),
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    registries = next(b for b in report["blocks"] if b["id"] == "registries")["data"]
    found = [row for row in registries["candidates"] if "triggers.xml" in row["path"]]
    assert found, "кандидат в реестры не найден"
    assert found[0]["kind"] == "реестр"
    assert found[0]["group"] == "Trigger/@name"
    assert found[0]["matched_in_code"] == 3


def test_registry_candidate_by_type_names(recon: ModuleType, tmp_path: Path) -> None:
    """Второй вид реестра: записи названы именами классов.

    Литерала в коде при этом может не быть ни одного — имя задачи существует
    только в XML и в имени класса. Детектор, требующий литерала, объявил бы,
    что реестров нет; проверено на открытом репозитории с 756 файлами workflow.
    """
    make_repo(
        tmp_path,
        {
            "workflows/one.xml": (
                "<Workflow>\n"
                '  <Task name="LoadFiles" />\n'
                '  <Task name="SendMail" />\n'
                '  <Task name="MakeReport" />\n'
                "</Workflow>\n"
            ),
            "src/LoadFiles.cs": "namespace App;\n\npublic class LoadFiles { }\n",
            "src/SendMail.cs": "namespace App;\n\npublic class SendMail { }\n",
            "src/MakeReport.cs": "namespace App;\n\npublic class MakeReport { }\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    registries = next(b for b in report["blocks"] if b["id"] == "registries")["data"]
    found = [row for row in registries["candidates"] if row["group"] == "Task/@name"]
    assert found, "реестр по именам типов не найден"
    assert found[0]["kind"] == "реестр по именам типов"
    assert found[0]["declared_in_code"] == 3
    declared_in = {path for row in found[0]["examples"] for path in row["declared_in"]}
    assert "src/LoadFiles.cs" in declared_in


def test_no_registries_is_a_valid_answer(recon: ModuleType, tmp_path: Path) -> None:
    """На большинстве репозиториев реестра нет, и правильный ответ — сказать
    это, а не назначить реестром первый попавшийся файл данных (ловушка R02)."""
    make_repo(tmp_path, {"src/app.py": "def run():\n    return 1\n"})
    report = recon.build_report(tmp_path, 12, 5, [])
    registries = next(b for b in report["blocks"] if b["id"] == "registries")["data"]
    assert registries["candidates"] == []
    assert "Кандидатов в реестры нет" in recon.render_text(report)


def test_seam_between_two_languages(recon: ModuleType, tmp_path: Path) -> None:
    """Шов — совпадение литерала по обе стороны языковой границы (Р2)."""
    make_repo(
        tmp_path,
        {
            "back/Controller.cs": (
                'namespace App;\n\npublic class C { const string R = "api/ml/forecast"; }\n'
            ),
            "front/service.ts": "export const url = 'api/ml/forecast';\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    seams = next(b for b in report["blocks"] if b["id"] == "seams")["data"]
    hit = [row for row in seams["candidates"] if row["literal"] == "api/ml/forecast"]
    assert hit, "шов между C# и TypeScript не найден"
    assert hit[0]["languages"] == ["csharp", "typescript"]
    assert hit[0]["kind"] == "маршрут"


def test_minified_bundle_is_not_a_registry(recon: ModuleType, tmp_path: Path) -> None:
    """Минифицированный бандл — артефакт сборки, а не код.

    Без этого правила он занимает первое место в блоке реестров: тысяча
    «идентификаторов» в одну строку выглядит убедительнее любого настоящего
    реестра. Проверено на открытом репозитории.
    """
    bundle = "var a=" + ",".join(f'"tok{index}"' for index in range(400)) + ";\n"
    make_repo(
        tmp_path,
        {
            "web/bundle.js": bundle,
            "src/app.py": "x = 'tok1'\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    blocks = {block["id"]: block["data"] for block in report["blocks"]}
    assert "web/bundle.js" in blocks["limits"]["minified"]["examples"]
    assert all("bundle.js" not in row["path"] for row in blocks["registries"]["candidates"])


def test_excludes_obj_directory_in_repository_root(recon: ModuleType, tmp_path: Path) -> None:
    """Ловушка `**`: отсев по сегментам пути, а не глобом.

    `**/obj/**` не ловит `obj/x.cs` в корне — это записано в правилах
    репозитория и стоило захода. Здесь оно проверяется на разведке.
    """
    make_repo(
        tmp_path,
        {
            "obj/Generated.cs": "public class Generated { }\n",
            "src/Real.cs": "public class Real { }\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 5, [])
    limits = next(b for b in report["blocks"] if b["id"] == "limits")["data"]
    assert limits["excluded_files"] == 1
    assert limits["excluded_by_reason"][0]["reason"] == "каталог obj/"


def test_cli_writes_both_forms(tmp_path: Path) -> None:
    """Вывод в двух формах, и обе пишутся одной командой (R01 п. 1)."""
    make_repo(tmp_path, {"src/app.py": "x = 1\n"})
    json_out = tmp_path / "recon.json"
    text_out = tmp_path / "recon.txt"
    result = subprocess.run(
        [
            sys.executable,
            str(RECON_PATH),
            "--root",
            str(tmp_path),
            "--json",
            str(json_out),
            "--text",
            str(text_out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["schema"] == "docpipe.recon/1"
    assert "РАЗВЕДКА РЕПОЗИТОРИЯ" in text_out.read_text(encoding="utf-8")


def test_same_language_family_is_not_a_seam(recon: ModuleType, tmp_path: Path) -> None:
    """`.ts` и `.js` живут в одном рантайме и делят модули.

    Общий литерал у них — переиспользование, а не сообщение через строку.
    Без этого правила первые места в блоке швов занимали `root`, `name`
    и `value` — проверено на открытом репозитории.
    """
    make_repo(
        tmp_path,
        {
            "web/a.ts": "export const mode = 'compact-mode';\n",
            "web/b.js": "const mode = 'compact-mode';\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    seams = next(b for b in report["blocks"] if b["id"] == "seams")["data"]
    assert all(row["literal"] != "compact-mode" for row in seams["candidates"])


def test_route_inside_one_language_is_still_a_seam(recon: ModuleType, tmp_path: Path) -> None:
    """Фронт и бэк одного языка говорят по HTTP ровно так же, как разные
    языки, и терять эту границу нельзя: её сводит шов web ↔ backend."""
    make_repo(
        tmp_path,
        {
            "web/service.ts": "export const url = '/api/v1/projects/terms';\n",
            "api/controller.ts": "export const route = '/api/v1/projects/terms';\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    seams = next(b for b in report["blocks"] if b["id"] == "seams")["data"]
    hit = [row for row in seams["candidates"] if row["literal"] == "/api/v1/projects/terms"]
    assert hit and hit[0]["kind"] == "маршрут"


def test_import_specifier_is_not_a_seam(recon: ModuleType, tmp_path: Path) -> None:
    """Строка, по которой модуль импортирует другой модуль, — зависимость.

    Косая черта в `@angular/core` есть, маршрутом он от этого не становится;
    без правила такие имена занимали первые пять мест блока.
    """
    make_repo(
        tmp_path,
        {
            "web/a.ts": "import { Component } from '@angular/core';\n",
            "srv/b.py": "PACKAGE = '@angular/core'\n",
        },
    )
    report = recon.build_report(tmp_path, 12, 10, [])
    seams = next(b for b in report["blocks"] if b["id"] == "seams")["data"]
    assert all(row["literal"] != "@angular/core" for row in seams["candidates"])
