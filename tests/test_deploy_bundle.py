"""Поставка в репозиторий АС CF (`deploy/`).

Поставка — второй набор файлов, описывающих то же самое: свой `pyproject.toml`,
свой лок, своя конфигурация. Разъезжаются такие пары молча, а обнаруживается
это на чужой машине, где ни тестов, ни быстрой обратной связи нет. Поэтому
согласованность проверяется здесь.
"""

import tomllib
from pathlib import Path

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import load_config

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
    for path in (config.rules, config.out, config.cache_dir):
        assert path.startswith("docs/ml/docspipe/"), path


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
    globs = load_ruleset(BUNDLE_RULES).exclude.path_glob
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
        "cashflow-docspipe/run.sh",
        "cashflow-docspipe/README.md",
    ],
)
def test_installer_inputs_exist(relative: str) -> None:
    """Установщик копирует эти файлы по именам: пропажа любого — отказ на месте."""
    assert (DEPLOY / relative).is_file()


def test_installer_is_executable() -> None:
    assert DEPLOY.joinpath("install.sh").stat().st_mode & 0o111


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


def test_run_script_never_touches_the_network() -> None:
    """Запуск не должен требовать доступа к индексу пакетов.

    `uv run` без `--no-sync` сверяется с индексом на каждом вызове и на машине
    без доступа к нему падает на команде, которая с зависимостями ничего
    не делает.
    """
    assert "--no-sync" in (DEPLOY / "cashflow-docspipe" / "run.sh").read_text(encoding="utf-8")
