"""Поставка в репозиторий АС CF (`deploy/`).

Поставка — второй набор файлов, описывающих то же самое: свой `pyproject.toml`,
свой лок, своя конфигурация. Разъезжаются такие пары молча, а обнаруживается
это на чужой машине, где ни тестов, ни быстрой обратной связи нет. Поэтому
согласованность проверяется здесь.
"""

import subprocess
import tomllib
from pathlib import Path

import pytest

from docpipe.classify import condition_values, load_ruleset
from docpipe.config import DocpipeConfig, load_config
from docpipe.materialize.template import load_templates

ROOT = Path(__file__).parent.parent
DEPLOY = ROOT / "deploy"
BUNDLE_CONFIG = DEPLOY / "cashflow-docspipe" / "docpipe.yaml"
BUNDLE_RULES = DEPLOY / "cashflow-docspipe" / "rules.yaml"


def _project(path: Path) -> dict[str, object]:
    return dict(tomllib.loads(path.read_text(encoding="utf-8"))["project"])


# --------------------------------------------------------------------------------------
# Манифест поставки
# --------------------------------------------------------------------------------------


def _requirement_names(project: dict[str, object]) -> set[str]:
    requirements = project["dependencies"]
    assert isinstance(requirements, list)
    return {
        str(r).split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip() for r in requirements
    }


def test_runtime_dependencies_match_the_repository() -> None:
    """Состав зависимостей поставки обязан совпадать с составом разработки.

    Сравниваются **имена**, а не ограничения версий. Ограничения расходятся
    законно: на закрытом контуре набор подбирается под то, что отдаёт внутреннее
    зеркало, и результат возвращается сюда (см. deploy/OFFLINE.md). А вот забытая
    при добавлении зависимость — это отказ на целевой машине, и ловить его нужно
    здесь.
    """
    source = _project(ROOT / "pyproject.toml")
    bundle = _project(DEPLOY / "pyproject.toml")

    assert _requirement_names(bundle) == _requirement_names(source)
    for field in ("name", "version"):
        assert bundle[field] == source[field], f"разошлось поле {field}"


def test_bundle_declares_no_development_dependencies() -> None:
    """Ни ruff, ни mypy, ни pytest на целевой машине не нужны."""
    bundle = tomllib.loads((DEPLOY / "pyproject.toml").read_text(encoding="utf-8"))
    assert "dependency-groups" not in bundle
    assert "tool" in bundle  # hatchling остаётся: пакет всё же собирается
    assert set(bundle["tool"]) == {"hatch"}


@pytest.mark.parametrize("package", ["ruff", "mypy", "pytest", "types-pyyaml"])
def test_lock_contains_no_development_packages(package: str) -> None:
    lock = (DEPLOY / "uv.lock").read_text(encoding="utf-8")
    assert f'name = "{package}"' not in lock


def test_lock_covers_every_runtime_dependency() -> None:
    lock = (DEPLOY / "uv.lock").read_text(encoding="utf-8")
    for requirement in _project(DEPLOY / "pyproject.toml")["dependencies"]:  # type: ignore[union-attr]
        name = str(requirement).split(">")[0].split("<")[0].split("=")[0].strip()
        assert f'name = "{name}"' in lock, f"нет в локе: {name}"


# --------------------------------------------------------------------------------------
# Настройка под АС CF
# --------------------------------------------------------------------------------------


def test_bundle_config_loads() -> None:
    """Конфигурация поставки обязана загружаться: опечатка в ней — это отказ.

    Проверяется сам файл, а не его копия. Конфигурация, которая не читается,
    хуже отсутствующей: она выглядит настроенной.
    """
    config = load_config(BUNDLE_CONFIG)
    assert config.enrolled
    assert config.out.endswith(".json")


def test_bundle_config_excludes_the_documentation_tree() -> None:
    """`docs/**` — каталог, в котором лежит сам инструмент внутри АС CF.

    Шаблон обязан заканчиваться на `/**`: без этого он совпал бы только
    с самим каталогом, но не с файлами под ним.
    """
    assert "docs/**" in load_config(BUNDLE_CONFIG).exclude


def test_bundle_paths_point_inside_the_bundle() -> None:
    """Все пути ведут в каталог инструмента, а не в рабочее дерево продукта."""
    config = load_config(BUNDLE_CONFIG)
    for path in (config.rules, config.out, config.cache_dir, config.templates):
        assert path.startswith("docs/ml/docspipe/"), path


def test_bundle_config_names_the_templates_directory() -> None:
    """Значение по умолчанию здесь не работает, и отказ выглядит как ошибка пути.

    `templates` отсчитывается от **текущего** каталога, как `out` и `rules`,
    а команды по инструкции зовутся из корня репозитория АС CF. Значение
    по умолчанию (`templates`) указывало бы на несуществующий `<корень>/templates`,
    и `materialize` падал бы с «Каталог шаблонов не найден» — сообщением, по
    которому не видно, что виновата конфигурация.
    """
    assert load_config(BUNDLE_CONFIG).templates != DocpipeConfig().templates


def test_bundle_config_hides_the_tool_from_the_documentation_walk() -> None:
    """Инструмент лежит внутри `docs/`, и без исключения обход зашёл бы в `.venv`.

    Отдельно от `exclude`: тот про обход исходников, этот — про обход документов.
    """
    config = load_config(BUNDLE_CONFIG)
    assert config.docs_root == "docs"
    assert "docs/ml/**" in config.docs_scan_exclude


def test_bundle_ruleset_loads() -> None:
    ruleset = load_ruleset(BUNDLE_RULES)
    assert ruleset.ruleset_version.startswith("2026-")
    assert {rule.id for rule in ruleset.rules} >= {"controller.aspnet", "service", "workflow"}


def test_bundle_ruleset_keeps_domain_entities_named_like_tests() -> None:
    """`StressTest`, `BackTest` — предметные сущности финансового моделирования.

    Шаблоны `**/*Test/**` и `**/*Tests/**` из эталонного набора отсекли бы
    любой каталог с таким именем. В наборе для АС CF они сужены до точки перед
    Test — то есть до каталогов тестовых проектов по конвенции .NET.
    """
    # Через `condition_values`, а не по полю: набор может быть записан и краткой
    # формой, и правилами с причинами, и вложенным `any`. Вопрос здесь про смысл
    # («отсекается ли такой каталог»), и от формы записи он зависеть не должен.
    globs = [
        glob
        for rule in load_ruleset(BUNDLE_RULES).exclude.rules
        for glob in condition_values(rule.when, "path_glob")
    ]
    assert "**/*Test/**" not in globs
    assert "**/*Tests/**" not in globs
    assert "**/*.Tests/**" in globs


# --------------------------------------------------------------------------------------
# Установщик
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "install.sh",
        "README.md",
        "OFFLINE.md",
        "gitignore",
        "pyproject.toml",
        "uv.lock",
        "uv.toml.example",
        "cashflow-docspipe/docpipe.yaml",
        "cashflow-docspipe/rules.yaml",
        "cashflow-docspipe/README.md",
    ],
)
def test_installer_inputs_exist(relative: str) -> None:
    """Установщик копирует эти файлы по именам: пропажа любого — отказ на месте."""
    assert (DEPLOY / relative).is_file()


def test_installer_is_executable() -> None:
    assert DEPLOY.joinpath("install.sh").stat().st_mode & 0o111


def _install(target: Path) -> subprocess.CompletedProcess[str]:
    """Прогнать установщик без подъёма окружения. Сеть при этом не нужна."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "App.sln").touch()
    return subprocess.run(
        [str(DEPLOY / "install.sh"), str(target), "--skip-sync"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_installed_tree_holds_exactly_one_ruleset(tmp_path: Path) -> None:
    """Второй набор правил в дереве поставки — молчаливая ошибка, а не удобство.

    Путь `rules/dotnet.yaml` совпадает со значением `rules` по умолчанию, поэтому
    прогон из каталога инструмента **без** `--config` взял бы эталонный набор
    вместо настроенного и завершился бы успешно — с манифестом, построенным
    не теми правилами.

    Эталон при этом ничего не давал: отличия помечены пометками «АС CF» в самом
    наборе, а новая версия приходит при обновлении как `rules.yaml.new`.
    """
    _install(tmp_path / "repo")
    installed = tmp_path / "repo" / "docs/ml/docspipe"

    rulesets = sorted(p.relative_to(installed).as_posix() for p in installed.rglob("*.yaml"))
    assert rulesets == ["cashflow-docspipe/docpipe.yaml", "cashflow-docspipe/rules.yaml"]
    assert not (installed / "rules").exists()


def test_installed_tree_holds_the_templates(tmp_path: Path) -> None:
    """Без шаблонов шаг 2 не запускается вовсе, а причина не видна из сообщения.

    Проверяется получившееся дерево, а не текст скрипта: перестановка строк
    в `install.sh` не должна ронять тест, а пропажа каталога — должна.
    Каталог обязан ещё и **загружаться**: скопировать файлы и получить ноль
    скелетов (например, разложив их по подкаталогам) — тот же отказ.
    """
    _install(tmp_path / "repo")
    templates = tmp_path / "repo" / "docs/ml/docspipe/cashflow-docspipe/templates"

    installed = load_templates(templates)
    declared = {rule.template for rule in load_ruleset(BUNDLE_RULES).rules if rule.template}
    assert declared <= set(installed), declared - set(installed)
    assert sorted(p.name for p in (templates / "examples").glob("*.md"))


def test_installed_templates_are_where_the_configuration_looks(tmp_path: Path) -> None:
    """Путь в `docpipe.yaml` и место установки — две записи об одном и том же.

    Разъедутся они молча: конфигурация останется валидной, установка — успешной,
    а `materialize` откажет на чужой машине.
    """
    _install(tmp_path / "repo")
    configured = load_config(BUNDLE_CONFIG).templates
    assert (tmp_path / "repo" / configured).is_dir(), configured


def test_installer_keeps_edited_templates(tmp_path: Path) -> None:
    """Шаблон правят под проект, как и правила: затирать правку обновлением нельзя."""
    target = tmp_path / "repo"
    _install(target)
    edited = target / "docs/ml/docspipe/cashflow-docspipe/templates/service.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n<!-- под АС CF -->\n", "utf-8")

    _install(target)

    assert "<!-- под АС CF -->" in edited.read_text(encoding="utf-8")
    assert edited.with_suffix(".md.new").is_file()


def test_installer_warns_about_a_ruleset_left_by_older_versions(tmp_path: Path) -> None:
    """Установщик ничего не удаляет, поэтому про остаток обязан сказать.

    Молча оставить его — значит оставить и ловушку: путь совпадает со значением
    по умолчанию, и подхват выглядит как успешный прогон.
    """
    target = tmp_path / "repo"
    stale = target / "docs/ml/docspipe/rules"
    stale.mkdir(parents=True)
    (stale / "dotnet.yaml").write_text("ruleset_version: old\n", encoding="utf-8")

    result = _install(target)

    assert "остался от прежней версии" in result.stderr
    assert "rm -r" in result.stderr
    assert (stale / "dotnet.yaml").is_file()  # сказали, но не удалили


# --------------------------------------------------------------------------------------
# Настройки uv для закрытого контура
# --------------------------------------------------------------------------------------


def _uv_settings() -> dict[str, object]:
    return tomllib.loads((DEPLOY / "uv.toml.example").read_text(encoding="utf-8"))


def test_uv_settings_are_top_level_not_index_fields() -> None:
    """Ключи верхнего уровня обязаны стоять до первой таблицы `[[index]]`.

    TOML относит любой ключ после открытия таблицы к ней. Перенос `native-tls`
    под `[[index]]` не был бы синтаксической ошибкой — настройка просто
    перестала бы действовать, а установка падала бы на сертификате.
    """
    settings = _uv_settings()
    assert settings["native-tls"] is True
    assert settings["python-downloads"] == "never"


def test_uv_settings_replace_pypi_rather_than_add_to_it() -> None:
    """`default = true` — «вместо PyPI», а не «в дополнение к нему».

    Без этого uv на закрытом контуре всё равно ходил бы на pypi.org за тем,
    чего нет в зеркале, и падал бы на сертификате прокси.
    """
    indexes = _uv_settings()["index"]
    assert isinstance(indexes, list)
    assert indexes[0]["default"] is True


def test_installer_substitutes_the_index_placeholder() -> None:
    """Заглушка адреса в шаблоне обязана совпадать с той, что ищет `sed`.

    Разъедься они — установщик молча положил бы `uv.toml` с несуществующим
    хостом, и ошибка выглядела бы как недоступность зеркала.
    """
    placeholder = "https://ЗАПОЛНИТЬ/repository/pypi/simple"
    assert placeholder in (DEPLOY / "uv.toml.example").read_text(encoding="utf-8")
    assert placeholder in (DEPLOY / "install.sh").read_text(encoding="utf-8")


def test_shipped_lock_points_at_pypi() -> None:
    """Установщик отличает свой лок от пересобранного по этой строке.

    Если формат записи источника в `uv.lock` изменится, обновление инструмента
    начнёт затирать лок, пересобранный против внутреннего зеркала, — и рабочая
    установка отправится на недоступный pypi.org.
    """
    marker = 'registry = "https://pypi.org/simple"'
    assert marker in (DEPLOY / "uv.lock").read_text(encoding="utf-8")
    assert marker in (DEPLOY / "install.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    ["README.md", "cashflow-docspipe/README.md", "cashflow-docspipe/docpipe.yaml"],
)
def test_documented_invocation_never_touches_the_network(relative: str) -> None:
    """Инструкция по запуску обязана содержать `--no-sync`.

    `uv run` без него сверяется с индексом на каждом вызове и на машине без доступа
    к нему падает — на команде, которая с зависимостями ничего не делает.

    Раньше это проверялось по `run.sh`; скрипта больше нет, инструкция стала
    единственным носителем требования, и проверять надо её. Иначе флаг тихо
    выпадет при следующей правке документации, а обнаружится на закрытом контуре.
    """
    assert "--no-sync" in (DEPLOY / relative).read_text(encoding="utf-8")


def test_documented_invocation_works_in_both_install_modes() -> None:
    """`python -m docpipe`, а не консольная команда: она есть не во всех режимах.

    При установке с `--no-install-project` пакет не собирается, консольной команды
    нет, и работает только вызов модулем — с `PYTHONPATH` на каталог инструмента.
    """
    text = (DEPLOY / "cashflow-docspipe" / "README.md").read_text(encoding="utf-8")
    assert "python -m docpipe" in text
    assert "PYTHONPATH" in text
