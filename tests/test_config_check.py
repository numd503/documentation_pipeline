"""`config check` как функция: отчёт структурой, коды возврата по проблемам (S04).

Это первый инструмент ассистента: агент правит настройку и тут же зовёт
проверку снова (Р-1), поэтому ответ обязан быть структурой, а не текстом,
который разбирают регулярками, и отказ — только там, где прогон
действительно сломается. Тексту отчёта посвящены тесты
`test_config_paths.py`; здесь — модель, коды и то, чего раньше проверка
не видела вовсе: корни обхода, входы адаптеров, движок.
"""

import json
import shutil
from pathlib import Path
from typing import get_args

import pytest
from typer.testing import CliRunner

from docpipe.arch.adapters import ADAPTERS
from docpipe.cli import app
from docpipe.config import DocpipeConfig, load_config
from docpipe.configcheck import (
    _PROBLEM_ORDER,
    ADAPTER_INPUTS,
    INPUT_KEYS,
    ConfigReport,
    ProblemCode,
    check_config,
    format_problems,
    format_report,
)

runner = CliRunner()

CONFIG = "docs/ml/cashflow-docpipe/docpipe.yaml"


@pytest.fixture
def nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Та же раскладка, что в `test_config_paths.py`: настройка внутри `docs/`,
    команды зовутся из корня."""
    tools = tmp_path / "docs" / "ml" / "cashflow-docpipe"
    tools.mkdir(parents=True)
    shutil.copytree(Path("templates"), tools / "templates")
    shutil.copy(Path("rules/rules.yaml"), tools / "rules.yaml")
    (tools / "docpipe.yaml").write_text(
        'templates: "templates"\n'
        'rules: "rules.yaml"\n'
        "web:\n"
        '  rules: "rules.yaml"\n'
        'business_root: "docs/ml/cashflow-docpipe/business"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _append(root: Path, text: str) -> None:
    config = root / CONFIG
    config.write_text(config.read_text(encoding="utf-8") + text, encoding="utf-8")


def _check(root: Path) -> ConfigReport:
    return check_config(load_config(Path(CONFIG)), Path(CONFIG), Path("."), root)


def _invoke(*extra: str) -> tuple[int, str]:
    result = runner.invoke(app, ["config", "check", "--config", CONFIG, "--root", ".", *extra])
    return result.exit_code, result.output


# --------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------


def test_json_is_a_valid_report_and_agrees_with_the_text(nested: Path) -> None:
    """Ступень у входа в JSON и в тексте — одно и то же утверждение.

    Два представления строятся из одного отчёта; разойтись они могли бы,
    только если бы текст снова собирался отдельно от функции.
    """
    code, output = _invoke("--format", "json")
    assert code == 0, output

    report = ConfigReport.model_validate_json(output)
    assert report.schema_version == "1.2"
    assert report.problems == []
    by_key = {item.key: item for item in report.inputs}
    assert [item.key for item in report.inputs] == list(INPUT_KEYS)
    assert by_key["rules"].step == "config"
    assert by_key["rules"].found == "docs/ml/cashflow-docpipe/rules.yaml"
    assert by_key["templates"].step == "config"
    assert by_key["web.rules"].step == "config"
    assert by_key["ownership"].value == ""
    assert by_key["ownership"].step is None

    _, text = _invoke()
    lines = {line.split()[0]: line for line in text.splitlines() if line.startswith("  ")}
    for item in report.inputs:
        if item.step == "config":
            assert "(рядом с конфигурацией)" in lines[item.key]
        elif item.step == "cwd":
            assert "(от текущего каталога)" in lines[item.key]
        else:
            assert "не задан" in lines[item.key]


def test_json_ends_with_a_single_newline(nested: Path) -> None:
    """Лишняя пустая строка в конце ломает сравнение вывода с файлом."""
    _, output = _invoke("--format", "json")

    assert output.endswith("}\n")
    assert not output.endswith("\n\n")
    json.loads(output)


def test_report_is_deterministic(nested: Path) -> None:
    _append(nested, 'roots: ["b", "a"]\n')

    first = _check(nested).model_dump_json()
    second = _check(nested).model_dump_json()

    assert first == second
    entries = [item.entry for item in _check(nested).roots if item.key == "roots"]
    assert entries == ["a", "b"]


def test_unknown_format_is_refused(nested: Path) -> None:
    code, output = _invoke("--format", "jsno")

    assert code == 2
    assert "jsno" in output


def test_unreadable_configuration_is_code_two_in_json_too(tmp_path: Path) -> None:
    """Код 2 — отказ инструмента, а не находка проверки; формат его не меняет."""
    broken = tmp_path / "docpipe.yaml"
    broken.write_text("rootz: [1]\n", encoding="utf-8")

    result = runner.invoke(app, ["config", "check", "--config", str(broken), "--format", "json"])

    assert result.exit_code == 2
    assert "не читается" in result.output


# --------------------------------------------------------------------------------------
# Корни обхода
# --------------------------------------------------------------------------------------


def test_missing_root_is_a_problem(nested: Path) -> None:
    """Корень, которого нет, молча даёт ноль файлов — `discover` его просто
    не встречает, и «модулей ноль» неотличимо от репозитория без кода."""
    _append(nested, 'roots: ["нет-такого"]\n')

    report = _check(nested)
    code, output = _invoke()

    assert code == 1
    assert [(item.code, item.key) for item in report.problems] == [("root-missing", "roots")]
    assert "нет-такого" in report.problems[0].message
    assert "КАТАЛОГА НЕТ" in output
    assert "нет-такого" in output


def test_missing_web_root_is_named_by_its_own_key(nested: Path) -> None:
    (nested / "front").mkdir()
    _append(nested, 'roots: ["front"]\n')
    config = nested / CONFIG
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'web:\n  rules: "rules.yaml"\n', 'web:\n  rules: "rules.yaml"\n  roots: ["ui"]\n'
        ),
        encoding="utf-8",
    )

    report = _check(nested)

    assert [(item.code, item.key) for item in report.problems] == [("root-missing", "web.roots")]
    assert {(item.key, item.entry, item.exists) for item in report.roots} == {
        ("roots", "front", True),
        ("web.roots", "ui", False),
    }


def test_roots_are_resolved_from_the_root_not_the_current_directory(
    nested: Path,
) -> None:
    """Корни обхода — от `--root`: проверка от текущего каталога подтвердила
    бы корень, которого обход не увидит."""
    repo = nested / "repo"
    (repo / "src").mkdir(parents=True)
    _append(nested, 'roots: ["src"]\n')

    from_repo = check_config(load_config(Path(CONFIG)), Path(CONFIG), Path("repo"), nested)
    from_here = _check(nested)

    assert [item.exists for item in from_repo.roots if item.key == "roots"] == [True]
    assert [item.exists for item in from_here.roots if item.key == "roots"] == [False]
    assert from_repo.roots[0].resolved == str(repo / "src")


# --------------------------------------------------------------------------------------
# Входы адаптеров
# --------------------------------------------------------------------------------------


def test_every_adapter_declares_its_input(nested: Path) -> None:
    """Новый адаптер обязан назвать свой вход и его базу, иначе проверка
    пропустит его файл молча."""
    assert set(ADAPTER_INPUTS) == set(ADAPTERS)


def test_missing_spec_is_a_problem_with_the_config_base(nested: Path) -> None:
    _append(
        nested,
        'arch_adapters:\n  - {id: regs, adapter: registries, options: {spec: "registries.yaml"}}\n',
    )

    report = _check(nested)
    code, _ = _invoke()

    assert code == 1
    assert [(item.code, item.key) for item in report.problems] == [
        ("adapter-input-missing", "arch_adapters[regs].options.spec")
    ]
    (adapter,) = report.adapter_inputs
    assert (adapter.option, adapter.base, adapter.exists) == ("spec", "config", False)


def test_spec_beside_the_config_is_found_by_the_second_step(nested: Path) -> None:
    """`spec` читается через `resolve_input`, как его читает адаптер: короткое
    имя рядом с `docpipe.yaml` находится, хотя от текущего каталога его нет."""
    (nested / "docs/ml/cashflow-docpipe/registries.yaml").write_text(
        "registries: []\n", encoding="utf-8"
    )
    _append(
        nested,
        'arch_adapters:\n  - {id: regs, adapter: registries, options: {spec: "registries.yaml"}}\n',
    )

    report = _check(nested)

    assert report.problems == []
    (adapter,) = report.adapter_inputs
    assert adapter.exists
    assert adapter.resolved == str(nested / "docs/ml/cashflow-docpipe/registries.yaml")


def test_python_code_path_is_resolved_from_the_root(nested: Path) -> None:
    """Ловушка, ради которой поле `base` обязательно: модуль Python лежит
    в дереве исходников, и рядом с конфигурацией его искать нельзя."""
    (nested / "docs/ml/cashflow-docpipe/registry.py").write_text("X = {}\n", encoding="utf-8")
    _append(
        nested,
        'arch_adapters:\n  - {id: code, adapter: python_code, options: {path: "registry.py"}}\n',
    )

    report = _check(nested)

    (adapter,) = report.adapter_inputs
    assert (adapter.option, adapter.base, adapter.exists) == ("path", "root", False)
    assert adapter.resolved == str(nested / "registry.py")
    assert [item.code for item in report.problems] == ["adapter-input-missing"]
    assert "от корня репозитория" in report.problems[0].message


def test_adapter_without_its_required_option_is_a_problem(nested: Path) -> None:
    """Без `spec` адаптер откажет на сборке; узнать это стоит здесь."""
    _append(nested, "arch_adapters:\n  - {id: regs, adapter: registries}\n")

    report = _check(nested)

    (adapter,) = report.adapter_inputs
    assert adapter.value == ""
    assert [item.key for item in report.problems] == ["arch_adapters[regs].options.spec"]
    assert "не задан" in report.problems[0].message


# --------------------------------------------------------------------------------------
# Движок и цели записи
# --------------------------------------------------------------------------------------


def test_named_missing_engine_is_code_one(nested: Path) -> None:
    _append(nested, 'graph:\n  engine_path: "/нет/такого"\n')

    report = _check(nested)
    code, output = _invoke()

    assert code == 1
    assert [(item.code, item.key) for item in report.problems] == [
        ("engine-missing", "graph.engine_path")
    ]
    assert report.engine.configured
    assert not report.engine.exists
    assert "/нет/такого" in output


def test_engine_not_configured_is_legal(nested: Path) -> None:
    """Шагам 1, 2 и бизнес-слою движок не нужен."""
    report = _check(nested)

    assert report.engine.configured is False
    assert report.problems == []


def test_engine_present_is_found(nested: Path) -> None:
    engine = nested / "bin" / "engine"
    engine.parent.mkdir()
    engine.write_text("", encoding="utf-8")
    _append(nested, f'graph:\n  engine_path: "{engine}"\n')

    report = _check(nested)

    assert report.engine.exists
    assert report.problems == []


def test_missing_target_directory_is_not_a_problem(nested: Path) -> None:
    """Каталоги создают все писатели, а умолчание `graph.cache_dir` даёт
    «каталога нет» на любом свежем репозитории: считай это проблемой —
    проверка была бы красной всегда, и её перестали бы читать."""
    report = _check(nested)
    code, output = _invoke()

    targets = {item.key: item for item in report.targets}
    assert not targets["graph.cache_dir"].parent_exists
    assert targets["out"].resolved == str(nested / "artifacts" / "doc-tree.json")
    assert code == 0, output
    assert "Всё, что настроено, на месте." in output


# --------------------------------------------------------------------------------------
# Плейсхолдеры установщика (S08 плана настройки, строка 21)
# --------------------------------------------------------------------------------------

BUNDLE_CONFIG = Path("deploy/cashflow-docspipe/docpipe.yaml")
# Наборов два (S30), и плейсхолдеры у них в одних и тех же ключах: установщик
# подставляет одно и то же, и ключ, забытый в одном наборе, остался бы
# в установленном файле путём, который выглядит настоящим.
BUNDLE_CONFIGS = [BUNDLE_CONFIG, Path("deploy/generic-docspipe/docpipe.yaml")]


@pytest.mark.parametrize("bundle_config", BUNDLE_CONFIGS, ids=lambda path: path.parent.name)
def test_raw_bundle_reports_every_placeholder(bundle_config: Path) -> None:
    """Файл поставки, позванный мимо установщика, — не «всё на месте».

    `@CONFIG_DIR@/artifacts/doc-tree.json` — законное значение цели записи:
    прогон создаст каталог `@CONFIG_DIR@` и напишет туда. До проверки это
    выглядело обычным путём, а `@ENGINE@` давал только «движка нет».
    Ключи перечислены явно: новый плейсхолдер в поставке обязан попасть
    в этот список осознанно.
    """
    settings = load_config(bundle_config)

    report = check_config(settings, bundle_config, Path("."), Path.cwd())

    placeholders = [item for item in report.problems if item.code == "placeholder-left"]
    assert [item.key for item in placeholders] == [
        "business_root",
        "cache_dir",
        "docs_scan_exclude",
        "graph.cache_dir",
        "graph.engine_path",
        "graph.out",
        "out",
        "web.link_out",
        "web.out",
        "worklist",
    ]
    assert all("незаменённый плейсхолдер @" in item.message for item in placeholders)
    # Плейсхолдер — причина, «движка нет» — её следствие: второй строкой
    # о том же ключе была бы подсказка чинить не то.
    assert "engine-missing" not in {item.code for item in report.problems}
    assert report.problems[0].code == "placeholder-left"


def test_installed_bundle_has_no_placeholder(tmp_path: Path) -> None:
    """Та же поставка после подстановки установщика проверку проходит."""
    text = BUNDLE_CONFIG.read_text(encoding="utf-8")
    for name, value in (
        ("@CONFIG_DIR@", "docs/ml/docpipe"),
        ("@CACHE_DIR@", str(tmp_path / "cache")),
        ("@ENGINE@", str(tmp_path / "engine")),
    ):
        text = text.replace(name, value)
    installed = tmp_path / "docpipe.yaml"
    installed.write_text(text, encoding="utf-8")

    report = check_config(load_config(installed), installed, Path("."), tmp_path)

    assert "placeholder-left" not in {item.code for item in report.problems}
    assert "engine-missing" in {item.code for item in report.problems}


def test_placeholder_in_an_input_and_a_list_is_named_once(nested: Path) -> None:
    """У входа плейсхолдер вместо «не найден», в списке — каждый элемент.

    Две строки о плейсхолдерах одного ключа законны (элементов два), а «не
    найден» у того же входа — нет: чинится всё одной подстановкой.
    """
    config = nested / CONFIG
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'rules: "rules.yaml"\nweb:', 'rules: "@CONFIG_DIR@/rules.yaml"\nweb:'
        )
        + 'docs_scan_exclude: ["@CONFIG_DIR@/**", "@BUSINESS@/**"]\n'
        + 'arch_adapters:\n  - {id: regs, adapter: registries, options: {spec: "@SPEC@"}}\n',
        encoding="utf-8",
    )

    report = _check(nested)
    code, output = _invoke()

    assert code == 1
    assert [(item.code, item.key) for item in report.problems] == [
        ("placeholder-left", "arch_adapters[regs].options.spec"),
        ("placeholder-left", "docs_scan_exclude"),
        ("placeholder-left", "docs_scan_exclude"),
        ("placeholder-left", "rules"),
    ]
    assert "@BUSINESS@" in report.problems[1].message
    assert "@CONFIG_DIR@" in report.problems[2].message
    assert "незаменённый плейсхолдер @CONFIG_DIR@" in output


@pytest.mark.parametrize("value", ["api/@me@/items", "user@host", "a@b@c", "@Engine@"])
def test_lowercase_at_pairs_are_not_placeholders(nested: Path, value: str) -> None:
    """Плейсхолдер установщика — прописное имя в рамке `@`; остальное законно."""
    _append(nested, f'docs_scan_exclude: ["{value}"]\n')

    report = _check(nested)

    assert report.problems == []


# --------------------------------------------------------------------------------------
# Отчёт отвечает за `cwd`, а не за каталог процесса
# --------------------------------------------------------------------------------------


def test_inputs_are_checked_from_the_given_cwd(
    nested: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сервер настройки зовёт функцию за каталог, из которого будут звать
    команду; ответ обязан быть тем, что увидит она, а не процесс сервера."""
    settings = load_config(Path(CONFIG))
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))

    report = check_config(settings, Path(CONFIG), Path("."), nested)

    by_key = {item.key: item for item in report.inputs}
    assert by_key["rules"].found == "docs/ml/cashflow-docpipe/rules.yaml"
    assert report.root == str(nested)
    assert report.problems == []


def test_defaults_without_a_config_file(tmp_path: Path) -> None:
    """Без файла конфигурации действуют умолчания, и отчёт говорит это прямо."""
    report = check_config(DocpipeConfig(), None, Path("."), tmp_path)

    assert report.config is None
    assert [(item.code, item.key) for item in report.problems] == [
        ("input-missing", "rules"),
        ("input-missing", "templates"),
        ("input-missing", "web.rules"),
    ]
    assert "не задана" in format_report(report)


# --------------------------------------------------------------------------------------
# Короткое имя, нашедшееся в каталоге продукта (S36)
# --------------------------------------------------------------------------------------

PRODUCT_CONFIG = Path("cfg/docpipe.yaml")


@pytest.fixture
def product(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Раскладка abp из прогона S31: настройка в `cfg/`, команды зовутся из корня.

    Скелеты и правила лежат рядом с настройкой, в корне их нет: единственное
    столкновение в отчёте — то, которое ставит тест.
    """
    repo = tmp_path / "repo"
    (repo / "cfg" / "templates").mkdir(parents=True)
    shutil.copy(Path("rules/rules.yaml"), repo / "cfg" / "rules.yaml")
    (repo / PRODUCT_CONFIG).write_text(
        'templates: "templates"\nrules: "rules.yaml"\nweb:\n  rules: "rules.yaml"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    return repo


def _product_check(repo: Path, config: Path = PRODUCT_CONFIG) -> ConfigReport:
    return check_config(load_config(repo / PRODUCT_CONFIG), config, Path("."), repo)


def _add_to_product(repo: Path, text: str) -> None:
    config = repo / PRODUCT_CONFIG
    config.write_text(config.read_text(encoding="utf-8") + text, encoding="utf-8")


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_short_name_found_in_the_product_is_shadowed(product: Path, absolute: bool) -> None:
    """Ловушка S31, 22: `templates/` abp — шаблоны стартовых решений, скелетов
    в нём нет, а отчёт отвечал `step: cwd` и `problems: []`.

    Порядок ступеней прежний (первым — текущий каталог), меняется молчание.
    Совет — путь от корня продукта: абсолютный путь к конфигурации даёт
    абсолютного кандидата, но значение для настройки — всё равно от `cwd`.
    """
    (product / "templates").mkdir()
    config = product / PRODUCT_CONFIG if absolute else PRODUCT_CONFIG

    report = _product_check(product, config)

    assert [(item.code, item.key) for item in report.problems] == [("input-shadowed", "templates")]
    item = {item.key: item for item in report.inputs}["templates"]
    assert (item.step, item.found) == ("cwd", "templates")
    assert item.shadowed == str(product / "cfg/templates" if absolute else "cfg/templates")
    message = report.problems[0].message
    assert "(`cfg/templates`)" in message
    assert "прогон возьмёт первый" in message
    # Цена починки названа: путь от корня с каталогом настройки не переедет.
    assert "не переедет" in message


def test_shadowed_input_is_code_one_and_named_in_the_text(product: Path) -> None:
    """Критерий приёмки: код 1. Вход найден, поэтому сводка не говорит «сломано»."""
    (product / "templates").mkdir()

    result = runner.invoke(app, ["config", "check", "--config", str(PRODUCT_CONFIG), "--root", "."])
    report = _product_check(product)

    assert result.exit_code == 1
    line = next(line for line in result.output.splitlines() if line.startswith("  templates "))
    assert "(от текущего каталога)" in line
    assert "рядом с конфигурацией лежит другой" in line
    assert "cfg/templates" in line
    problems = format_problems(report)
    assert "Короткое имя нашлось в каталоге продукта" in problems
    assert "Сломано в настройке" not in problems


def test_only_the_second_candidate_is_not_a_collision(product: Path) -> None:
    """В корне имени нет — нашла вторая ступень, выбора не было."""
    report = _product_check(product)

    assert report.problems == []
    item = {item.key: item for item in report.inputs}["templates"]
    assert (item.step, item.found, item.shadowed) == ("config", "cfg/templates", None)


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_config_in_the_root_has_nothing_to_shadow(tmp_path: Path, absolute: bool) -> None:
    """Конфигурация в корне: кандидат один, а названная абсолютным путём —
    два, но это один и тот же каталог (`resolve()` совпадает)."""
    (tmp_path / "templates").mkdir()
    config = tmp_path / "docpipe.yaml"
    config.write_text('templates: "templates"\n', encoding="utf-8")

    report = check_config(
        load_config(config), config if absolute else Path("docpipe.yaml"), Path("."), tmp_path
    )

    item = {item.key: item for item in report.inputs}["templates"]
    assert len(item.candidates) == (2 if absolute else 1)
    assert (item.step, item.shadowed) == ("cwd", None)
    assert "input-shadowed" not in {problem.code for problem in report.problems}


def test_link_to_the_same_directory_is_not_a_collision(product: Path) -> None:
    """Ссылка из корня на каталог настройки — тот же путь, выбор ни на что не влияет."""
    (product / "templates").symlink_to(product / "cfg/templates", target_is_directory=True)

    report = _product_check(product)

    assert report.problems == []
    assert {item.key: item for item in report.inputs}["templates"].step == "cwd"


def test_pages_directory_in_the_product_is_not_the_pages_file(product: Path) -> None:
    """`pages.yaml` читается файлом (`web/overrides.configured_pages`, `is_file()`):
    каталог с этим именем в корне чтение пропускает — и отчёт тоже.

    До S36 отчёт брал `exists()` и называл найденным каталог, которого прогон
    не прочтёт.
    """
    (product / "pages.yaml").mkdir()
    (product / "cfg/pages.yaml").write_text("version: '1'\n", encoding="utf-8")
    config = product / PRODUCT_CONFIG
    config.write_text(
        config.read_text(encoding="utf-8").replace("web:\n", 'web:\n  pages: "pages.yaml"\n'),
        encoding="utf-8",
    )

    report = _product_check(product)

    pages = {item.key: item for item in report.inputs}["web.pages"]
    assert (pages.step, pages.found, pages.shadowed) == ("config", "cfg/pages.yaml", None)
    assert report.problems == []

    # Файл в корне — уже столкновение: прогон прочтёт его.
    (product / "pages.yaml").rmdir()
    (product / "pages.yaml").write_text("version: '1'\n", encoding="utf-8")

    report = _product_check(product)

    assert [(item.code, item.key) for item in report.problems] == [("input-shadowed", "web.pages")]


def test_adapter_spec_found_in_the_product_is_shadowed(product: Path) -> None:
    """`options.spec` ищется теми же двумя ступенями (`resolve_input`); модуль
    Python (`options.path`) — от `--root`, второй ступени у него нет."""
    (product / "cfg/registries.yaml").write_text("registries: []\n", encoding="utf-8")
    (product / "cfg/registry.py").write_text("X = {}\n", encoding="utf-8")
    (product / "registry.py").write_text("X = {}\n", encoding="utf-8")
    _add_to_product(
        product,
        "arch_adapters:\n"
        '  - {id: regs, adapter: registries, options: {spec: "registries.yaml"}}\n'
        '  - {id: code, adapter: python_code, options: {path: "registry.py"}}\n',
    )

    assert _product_check(product).problems == []

    (product / "registries.yaml").write_text("registries: []\n", encoding="utf-8")
    report = _product_check(product)

    key = "arch_adapters[regs].options.spec"
    assert [(item.code, item.key) for item in report.problems] == [("input-shadowed", key)]
    adapters = {item.adapter_id: item for item in report.adapter_inputs}
    assert adapters["regs"].shadowed == "cfg/registries.yaml"
    assert adapters["regs"].resolved == str(product / "registries.yaml")
    assert adapters["code"].shadowed is None
    assert report.problems[0].message.startswith(f"{key}: ")
    assert "рядом с конфигурацией лежит другой" in format_report(report)


def test_every_problem_code_is_ordered_and_documented() -> None:
    """Код без места в `_PROBLEM_ORDER` роняет сортировку отчёта (`index`),
    а код без строки в таблице `docs/configuration.md` агент не найдёт
    в справочнике — новый код (S36) добавляется в оба места сразу."""
    codes = get_args(ProblemCode)
    table = Path("docs/configuration.md").read_text(encoding="utf-8")

    assert set(codes) == set(_PROBLEM_ORDER)
    assert [code for code in codes if f"| `{code}` |" not in table] == []
