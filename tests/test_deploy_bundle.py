"""Поставка на целевую машину (`deploy/`).

Поставка — второй набор файлов, описывающих ту же настройку, и разъезжаются
такие пары молча: обнаруживается это на чужой машине, где ни тестов, ни быстрой
обратной связи нет. Поэтому согласованность проверяется здесь.

Модель, которую эти тесты и защищают: **инструмент на машине, настройка
в репозитории продукта**. Кода в репозитории продукта нет вовсе, каталог
настройки задаётся при деплое, кэши лежат снаружи.

Наборов настройки два (S30): нейтральный `generic` — умолчание, его ведёт
настройка с ассистентом, — и `cashflow`, настроенный под АС CF. Состав
каталога у них один, поэтому тесты состава идут по обоим.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from docpipe.classify import condition_values, load_ruleset
from docpipe.config import DocpipeConfig, GraphConfig, WebConfig, load_config
from docpipe.configcheck import check_config
from docpipe.materialize.ownership import load_ownership
from docpipe.materialize.template import load_templates
from docpipe.registry import load_registries

ROOT = Path(__file__).parent.parent
DEPLOY = ROOT / "deploy"
GENERIC = DEPLOY / "generic-docspipe"
CASHFLOW = DEPLOY / "cashflow-docspipe"
BUNDLES = {"generic": GENERIC, "cashflow": CASHFLOW}
CASHFLOW_CONFIG = CASHFLOW / "docpipe.yaml"
CASHFLOW_RULES = CASHFLOW / "rules.yaml"

# Файлы, которые установщик берёт из набора по именам. Состав у наборов один:
# набор, в котором файла нет, уронил бы установку на `cp`.
BUNDLE_FILES = (
    "docpipe.yaml",
    "rules.yaml",
    "ownership.yaml",
    "pages.yaml",
    "registries.yaml",
    "arch-registry.yaml",
    "README.md",
)

both_bundles = pytest.mark.parametrize("bundle", sorted(BUNDLES))

# Каталог настройки — параметр деплоя, поэтому в тестах он свой и НЕ совпадает
# с тем, что написан в документации: путь, зашитый где-нибудь в конфигурации,
# обязан от этого сломаться.
CONFIG_DIR = "docs/ml/docpipe"


def _install(
    target: Path,
    *,
    bundle: str | None = None,
    config_dir: str = CONFIG_DIR,
    cache: Path | None = None,
    default_cache: bool = False,
    engine: str = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Разложить настройку, не ставя инструмент. Сеть при этом не нужна.

    `bundle=None` — без флага `--bundle`, то есть умолчание установщика.
    `default_cache` — без флага `--cache-dir`.
    """
    target.mkdir(parents=True, exist_ok=True)
    (target / "App.sln").touch()
    command = [
        str(DEPLOY / "install.sh"),
        "--repo",
        str(target),
        "--config-dir",
        config_dir,
        "--no-tool",
    ]
    if not default_cache:
        command += ["--cache-dir", str(cache or target.parent / "cache")]
    if bundle is not None:
        command += ["--bundle", bundle]
    if engine:
        command += ["--engine", engine]
    return subprocess.run(command, capture_output=True, text=True, check=True, env=env)


def _installed_config(target: Path, config_dir: str = CONFIG_DIR) -> DocpipeConfig:
    return load_config(target / config_dir / "docpipe.yaml")


# --------------------------------------------------------------------------------------
# Что попадает в репозиторий продукта — и что не должно
# --------------------------------------------------------------------------------------


@both_bundles
def test_installed_tree_holds_no_code(tmp_path: Path, bundle: str) -> None:
    """Кода инструмента в репозитории продукта нет. Это и есть смысл поставки.

    Пока пакет лежал внутри, из этого росло всё остальное: `docs_scan_exclude`,
    чтобы обход документов не заходил в `.venv`, полдюжины строк `.gitignore`
    и режим `--no-install-project` на случай, когда пакет нечем собрать.
    Возврат кода в дерево вернёт и их — молча.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    installed = repo / CONFIG_DIR

    assert list(installed.rglob("*.py")) == []
    for forbidden in ("pyproject.toml", "uv.lock", "uv.toml", ".venv", "docpipe"):
        assert not (installed / forbidden).exists(), forbidden


@both_bundles
def test_installed_tree_is_configuration_and_templates_only(tmp_path: Path, bundle: str) -> None:
    """Полный список того, что уезжает в репозиторий продукта.

    Список положительный намеренно: файл, добавленный в поставку «заодно»,
    обязан попасть сюда осознанно, а не просочиться.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    installed = repo / CONFIG_DIR

    top = sorted(p.name for p in installed.iterdir())
    assert top == [
        ".gitignore",
        "README.md",
        "arch-registry.yaml",
        "artifacts",
        "docpipe.yaml",
        "ownership.yaml",
        "pages.yaml",
        "registries.yaml",
        "rules.yaml",
        "templates",
    ]


@both_bundles
def test_installed_tree_holds_exactly_one_ruleset(tmp_path: Path, bundle: str) -> None:
    """Второй набор правил в дереве — молчаливая ошибка, а не удобство.

    Путь `rules/rules.yaml` совпадает со значением `rules` по умолчанию, поэтому
    прогон без `--config` взял бы эталонный набор вместо настроенного
    и завершился бы успешно — с манифестом, построенным не теми правилами.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    installed = repo / CONFIG_DIR

    assert sorted(p.relative_to(installed).as_posix() for p in installed.rglob("*.yaml")) == [
        "arch-registry.yaml",
        "docpipe.yaml",
        "ownership.yaml",
        "pages.yaml",
        "registries.yaml",
        "rules.yaml",
    ]


# --------------------------------------------------------------------------------------
# Выбор набора
# --------------------------------------------------------------------------------------


def test_default_bundle_is_generic(tmp_path: Path) -> None:
    """По умолчанию — нейтральный набор (П-5 плана настройки).

    Настроенный под АС CF набор на чужом репозитории — это чужие решения:
    `web.roots` в несуществующий каталог, реестры по чужим путям, правила
    с пометками «АС CF». Ставить его надо названным, а не по забывчивости.
    """
    cache = tmp_path / "cache"
    _install(tmp_path / "implicit", cache=cache)
    _install(tmp_path / "named", bundle="generic", cache=cache)

    for name in BUNDLE_FILES:
        implicit = (tmp_path / "implicit" / CONFIG_DIR / name).read_bytes()
        assert implicit == (tmp_path / "named" / CONFIG_DIR / name).read_bytes(), name
    readme = (tmp_path / "implicit" / CONFIG_DIR / "README.md").read_bytes()
    assert readme == (GENERIC / "README.md").read_bytes()


def test_cashflow_bundle_installs_as_before(tmp_path: Path) -> None:
    """`--bundle cashflow` даёт прежний набор байт в байт.

    Копия этой настройки уже лежит на АС CF: расхождение при обновлении легло
    бы рядом пачкой `.new`, в которой настоящую правку никто бы не нашёл.
    Отличаться от файлов набора может только подстановка плейсхолдеров.
    """
    repo = tmp_path / "repo"
    cache = tmp_path / "cache"
    _install(repo, bundle="cashflow", cache=cache, engine="/opt/cbm/codebase-memory-mcp")
    installed = repo / CONFIG_DIR

    for name in BUNDLE_FILES:
        expected = (CASHFLOW / name).read_text(encoding="utf-8")
        if name == "docpipe.yaml":
            expected = (
                expected.replace("@CONFIG_DIR@", CONFIG_DIR)
                .replace("@CACHE_DIR@", str(cache))
                .replace("@ENGINE@", "/opt/cbm/codebase-memory-mcp")
            )
        assert (installed / name).read_bytes() == expected.encode("utf-8"), name
    gitignore = (installed / ".gitignore").read_bytes()
    assert gitignore == (DEPLOY / "gitignore").read_bytes()


def test_unknown_bundle_is_refused(tmp_path: Path) -> None:
    """Опечатка в имени набора — отказ с перечнем, а не молчаливое умолчание."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "App.sln").touch()

    result = subprocess.run(
        [str(DEPLOY / "install.sh"), "--repo", str(repo), "--config-dir", CONFIG_DIR]
        + ["--bundle", "cashflw", "--no-tool"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "generic" in result.stderr and "cashflow" in result.stderr
    assert not (repo / CONFIG_DIR).exists()


def test_update_without_the_bundle_names_it(tmp_path: Path) -> None:
    """Каталог АС CF, обновлённый прежней командой без `--bundle`, получил бы
    `.new` нейтрального набора и нейтральный README — снаружи это выглядит
    удачным обновлением. Установщик обязан назвать набор вслух."""
    repo = tmp_path / "repo"
    _install(repo, bundle="cashflow")

    implicit = _install(repo)
    assert "--bundle cashflow" in implicit.stderr
    assert (repo / CONFIG_DIR / "docpipe.yaml.new").is_file()

    # Набор назван — подсказки нет: правленая нейтральная настройка законна.
    assert "--bundle" not in _install(repo, bundle="cashflow").stderr
    assert "--bundle" not in _install(repo, bundle="generic").stderr


# --------------------------------------------------------------------------------------
# Подстановка при деплое
# --------------------------------------------------------------------------------------


@both_bundles
def test_no_placeholder_survives_installation(tmp_path: Path, bundle: str) -> None:
    """Незаменённый плейсхолдер даёт путь, который выглядит настоящим.

    `@CONFIG_DIR@/artifacts/doc-tree.json` — валидное значение: прогон создаст
    каталог с таким именем и напишет туда, а человек будет искать манифест
    там, где его нет.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle, engine="/opt/cbm/codebase-memory-mcp")
    text = (repo / CONFIG_DIR / "docpipe.yaml").read_text(encoding="utf-8")

    assert "@" not in text.replace("@CONFIG", ""), "остался плейсхолдер"
    assert "@CONFIG_DIR@" not in text
    assert "@CACHE_DIR@" not in text
    assert "@ENGINE@" not in text


@both_bundles
def test_special_characters_in_paths_survive_substitution(tmp_path: Path, bundle: str) -> None:
    """`&` и `|` в правой части `sed "s|…|$X|g"` исказили бы путь молча.

    `&` подставляет найденный плейсхолдер, `|` — разделитель выражения;
    пробел ломает разбивку по словам, если где-то забыта кавычка. Кавычка
    в пути закрыла бы строку YAML.
    """
    config_dir = "cfg/a&b|c d"
    cache = tmp_path / "ca&c|he dir"
    engine = '/opt/e&n|g "ine"/cbm'
    repo = tmp_path / "re&po|x y"
    _install(repo, bundle=bundle, config_dir=config_dir, cache=cache, engine=engine)
    config = _installed_config(repo, config_dir)

    assert config.out == f"{config_dir}/artifacts/doc-tree.json"
    assert config.business_root == f"{config_dir}/business"
    assert f"{config_dir}/**" in config.docs_scan_exclude
    assert config.cache_dir == f"{cache}/parse"
    assert config.graph.cache_dir == f"{cache}/engine"
    assert config.graph.engine_path == engine


@both_bundles
def test_write_targets_follow_the_chosen_config_dir(tmp_path: Path, bundle: str) -> None:
    """Каталог настройки — параметр, и цели записи обязаны за ним ехать.

    Второй ступени поиска у них нет намеренно: угадывать, куда писать,
    инструмент не должен. Значит подставить путь обязан установщик.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle, config_dir="tools/docs-pipeline")
    config = _installed_config(repo, "tools/docs-pipeline")

    for value in (config.out, config.worklist, config.web.out, config.web.link_out):
        assert value.startswith("tools/docs-pipeline/artifacts/"), value
    assert config.graph.out.startswith("tools/docs-pipeline/artifacts/")
    assert config.business_root.startswith("tools/docs-pipeline/")
    # Обход документов обязан накрывать каталог настройки: иначе он зайдёт
    # в templates/ и примет скелеты за написанные документы.
    assert "tools/docs-pipeline/**" in config.docs_scan_exclude


@both_bundles
def test_inputs_are_written_relative_to_the_configuration(tmp_path: Path, bundle: str) -> None:
    """Входы записаны короткими именами и подстановки НЕ требуют.

    Их разрешает вторая ступень `resolve_input` — каталог самого `docpipe.yaml`.
    Ради этого она и заводилась: путь от корня пришлось бы править при каждом
    переносе каталога настройки, а он теперь задаётся при деплое.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle, config_dir="cfg")
    config = _installed_config(repo, "cfg")

    assert config.rules == "rules.yaml"
    assert config.templates == "templates"
    assert config.ownership == "ownership.yaml"
    assert config.arch == "arch-registry.yaml"
    assert config.web.rules == "rules.yaml"
    assert config.web.pages == "pages.yaml"
    # У нейтрального набора ключ ждёт первого реестра (см. тест ниже).
    assert config.registries == {"generic": None, "cashflow": "registries.yaml"}[bundle]

    for name in ("rules.yaml", "ownership.yaml", "registries.yaml", "arch-registry.yaml"):
        assert (repo / "cfg" / name).is_file(), name
    assert (repo / "cfg" / "templates").is_dir()


@both_bundles
def test_caches_live_outside_the_repository(tmp_path: Path, bundle: str) -> None:
    """Кэши — вне дерева продукта, и это структурно, а не через `.gitignore`.

    Строка в `.gitignore` защищает от случайного коммита хуже, чем отсутствие
    файлов, а речь о гигабайтах машинного мусора.
    """
    repo = tmp_path / "repo"
    cache = tmp_path / "elsewhere"
    _install(repo, bundle=bundle, cache=cache)
    config = _installed_config(repo)

    assert Path(config.cache_dir).is_absolute()
    assert Path(config.graph.cache_dir).is_absolute()
    assert not Path(config.cache_dir).is_relative_to(repo)
    assert not Path(config.graph.cache_dir).is_relative_to(repo)


@pytest.mark.parametrize("work", [True, False], ids=["work", "home"])
def test_default_cache_is_per_repository(tmp_path: Path, work: bool) -> None:
    """Умолчание `--cache-dir` — своё у каждого репозитория, а не одно на машину.

    Кэш движка мост удаляет целиком перед каждой сборкой: общий каталог двух
    репозиториев — сборка одного по чужому кэшу или удаление кэша соседа
    посреди его сборки. Кэш разбора делится тоже не во всём — полный прогон
    вычищает записи чужих путей.
    """
    env = {key: value for key, value in os.environ.items() if key != "WORK"}
    env["HOME"] = str(tmp_path / "home")
    base = tmp_path / "home" / ".cache" / "docpipe"
    if work:
        env["WORK"] = str(tmp_path / "work")
        base = tmp_path / "work" / ".docpipe" / "cache"

    caches = []
    for name in ("first", "second"):
        repo = tmp_path / "repos" / name
        _install(repo, default_cache=True, env=env)
        config = _installed_config(repo)
        assert config.cache_dir == str(base / name / "parse")
        assert config.graph.cache_dir == str(base / name / "engine")
        caches.append(config.graph.cache_dir)
    assert caches[0] != caches[1]


def test_relative_cache_dir_is_refused(tmp_path: Path) -> None:
    """Относительный кэш склеится с `--root` и уедет в дерево продукта —
    ровно то, ради ухода от чего каталог и вынесен наружу."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "App.sln").touch()

    result = subprocess.run(
        [
            str(DEPLOY / "install.sh"),
            "--repo",
            str(repo),
            "--config-dir",
            CONFIG_DIR,
            "--cache-dir",
            ".cache",
            "--no-tool",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "абсолютным" in result.stderr


def test_absolute_config_dir_is_refused(tmp_path: Path) -> None:
    """Значение уходит в `docpipe.yaml`, который читают и на других машинах:
    абсолютный путь сделал бы конфигурацию личной."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "App.sln").touch()

    result = subprocess.run(
        [str(DEPLOY / "install.sh"), "--repo", str(repo), "--config-dir", "/abs/cfg", "--no-tool"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "относительный путь" in result.stderr


def test_old_positional_form_is_refused_with_the_new_one(tmp_path: Path) -> None:
    """Прежняя форма клала код внутрь репозитория в жёстко зашитый каталог.

    Принять её молча — значит разложить поставку не туда, где её будут искать,
    и узнать об этом на первом прогоне.
    """
    result = subprocess.run(
        [str(DEPLOY / "install.sh"), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "--config-dir" in result.stderr


# --------------------------------------------------------------------------------------
# Оба набора: загрузка и заготовки
# --------------------------------------------------------------------------------------


@both_bundles
def test_bundle_config_loads(bundle: str) -> None:
    """Конфигурация поставки обязана загружаться и с плейсхолдерами.

    Опечатка в ней — отказ; конфигурация, которая не читается, хуже
    отсутствующей: она выглядит настроенной.
    """
    config = load_config(BUNDLES[bundle] / "docpipe.yaml")
    assert config.enrolled
    assert config.out.endswith(".json")


@both_bundles
def test_bundle_ownership_is_an_empty_starter(bundle: str) -> None:
    """Заготовка обязана грузиться и обязана быть пустой.

    Выдуманные команды хуже отсутствующих: правило с несуществующим
    `module_glob` молча не срабатывает, и «ничьих узлов 4820» уже не отличить
    от «правило написано с опечаткой».
    """
    ownership = load_ownership(BUNDLES[bundle] / "ownership.yaml")
    assert ownership.teams == []
    assert ownership.rules == []


@both_bundles
def test_bundle_pages_file_loads_and_is_empty(bundle: str) -> None:
    """Состав страниц — решение настройщика, но грузиться файл обязан сразу."""
    from docpipe.web.overrides import load_overrides

    overrides = load_overrides(BUNDLES[bundle] / "pages.yaml")
    assert overrides.add == []
    assert overrides.remove == []
    assert overrides.features == []


@both_bundles
def test_bundle_arch_registry_is_valid_and_empty(bundle: str) -> None:
    """Реестр приезжает пустым, и это рабочее состояние.

    Заполнять его догадками из другого репозитория нельзя: неверная запись
    отсюда выходит уверенным неправильным ребром в ответе на вопрос, и отличить
    её от факта, извлечённого из XML, уже нельзя.
    """
    from docpipe.arch import load_arch_registry

    assert load_arch_registry(BUNDLES[bundle] / "arch-registry.yaml").records == ()


@both_bundles
def test_bundle_web_ruleset_does_not_require_public(bundle: str) -> None:
    """`require_public: true`, скопированный из секции `dotnet`, отсеял бы весь
    фронт: у TypeScript модификатора `public` на уровне объявления нет вовсе."""
    assert load_ruleset(BUNDLES[bundle] / "rules.yaml", "web").exclude.require_public is False


@both_bundles
def test_bundle_configures_the_graph_step(tmp_path: Path, bundle: str) -> None:
    """Секции `graph` и `arch` в поставке не было вовсе, и весь готовый механизм
    разведки и связей доехать на целевую машину не мог физически."""
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle, engine="/opt/cbm/codebase-memory-mcp")
    config = _installed_config(repo)

    assert config.graph.engine_path == "/opt/cbm/codebase-memory-mcp"
    # Пусто — «взять закреплённую сумму из моста»: ключ существует, чтобы
    # закрепить свою сборку, а не чтобы не проверять.
    assert config.graph.engine_sha256 == ""
    assert config.graph.mode == "fast"
    assert config.arch == "arch-registry.yaml"


@both_bundles
def test_engine_stays_empty_without_the_flag(tmp_path: Path, bundle: str) -> None:
    """Умолчания у `engine_path` нет намеренно: запуск того, что нашлось в PATH,
    означает числа от другой версии движка. Пусто — отказ с указанием, что
    заполнить, и установщик про это говорит."""
    repo = tmp_path / "repo"
    result = _install(repo, bundle=bundle)

    assert _installed_config(repo).graph.engine_path == ""
    assert "--engine" in result.stdout


# --------------------------------------------------------------------------------------
# Нейтральный набор
# --------------------------------------------------------------------------------------

# Имена АС CF. Нейтральный набор ставится на любой репозиторий, и чужое имя
# в нём — это чужое решение: путь, маршрут или правило, которые на этом
# репозитории ни на что не лягут и молча не сработают.
CUSTOMER_NAMES = ("sbt", "Sbt.", "cashflow", "Cashflow", "АС CF")


@pytest.mark.parametrize("name", BUNDLE_FILES)
def test_generic_bundle_names_no_customer(name: str) -> None:
    text = (GENERIC / name).read_text(encoding="utf-8")
    assert [word for word in CUSTOMER_NAMES if word in text] == []


def test_generic_bundle_holds_only_the_installed_files() -> None:
    """Сторож выше перечисляет файлы, а не обходит каталог: файл, положенный
    в набор «заодно», прошёл бы мимо него. Поэтому состав — отдельно."""
    assert sorted(p.name for p in GENERIC.iterdir()) == sorted(BUNDLE_FILES)


def test_generic_rules_are_the_reference_ruleset() -> None:
    """Нейтральные правила — эталонный набор, копией, без правок.

    Правка «под репозиторий» здесь — то же чужое решение; её место
    в установленном `rules.yaml`, куда её пишет настройка с ассистентом.
    """
    assert (GENERIC / "rules.yaml").read_bytes() == (ROOT / "rules" / "rules.yaml").read_bytes()


def _section_block(text: str, name: str) -> str:
    """Строки секции `name:` верхнего уровня — до следующего ключа верхнего уровня."""
    lines = text.splitlines()
    start = lines.index(f"{name}:")
    block = []
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "#")):
            break
        block.append(line)
    return "\n".join(block)


def test_generic_config_mentions_every_key() -> None:
    """Каждый ключ `docpipe.yaml` записан — значением или комментарием.

    Агент настройки правит файл, а не пишет его заново: ключ, которого в файле
    нет, он не увидит и не предложит. Ссылки на разделы карты настройки
    обязаны вести на существующие заголовки.
    """
    text = (GENERIC / "docpipe.yaml").read_text(encoding="utf-8")

    def missing(model: type[BaseModel], where: str, indent: str) -> list[str]:
        return [
            name
            for name in model.model_fields
            if not re.search(rf"^{indent}(# )?{name}:", where, re.MULTILINE)
        ]

    assert missing(DocpipeConfig, text, "") == []
    assert missing(WebConfig, _section_block(text, "web"), "  ") == []
    assert missing(GraphConfig, _section_block(text, "graph"), "  ") == []

    headings = {
        line.lstrip("#").strip()
        for line in (ROOT / "docs" / "setup-map.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    }
    for name in BUNDLE_FILES:
        links = re.findall(r"setup-map\.md[^«\n]*«([^»]+)»", (GENERIC / name).read_text("utf-8"))
        assert [link for link in links if link not in headings] == [], name
        if name.endswith(".yaml") and name != "rules.yaml":
            assert links, f"{name}: нет ссылки на карту настройки"


def test_generic_config_comments_hold_the_defaults() -> None:
    """Ключ, закомментированный одной строкой, — умолчание, и значение в
    комментарии обязано им быть: агент, раскомментировавший строку, не должен
    менять поведение. Исключение — `registries`: умолчания у него нет."""
    defaults: dict[str, dict[str, Any]] = {}
    for section, model in (("", DocpipeConfig), ("web", WebConfig), ("graph", GraphConfig)):
        defaults[section] = {
            name: field.get_default(call_default_factory=True)
            for name, field in model.model_fields.items()
        }

    section, checked = "", 0
    for line in (GENERIC / "docpipe.yaml").read_text(encoding="utf-8").splitlines():
        if re.match(r"^[a-z_]+:", line):
            section = line.split(":")[0] if line.split(":")[0] in ("web", "graph") else ""
        match = re.match(r"^(  )?# ([a-z_]+): (.+)$", line)
        if not match or match.group(2) == "registries":
            continue
        where = section if match.group(1) else ""
        assert yaml.safe_load(match.group(3)) == defaults[where][match.group(2)], line
        checked += 1
    assert checked >= 10


def test_generic_registries_key_waits_for_a_registry() -> None:
    """Описание без единого реестра загрузчик отвергает, и это правильно (П-4).

    Поэтому заготовка лежит, а ключ в `docpipe.yaml` закомментирован: иначе
    шаг 2 на каждом прогоне печатал бы ошибку бизнес-ссылок, а `anchors`
    и `business` отказывали бы с сообщением про чужой файл.
    """
    assert load_config(GENERIC / "docpipe.yaml").registries is None
    assert '# registries: "registries.yaml"' in (GENERIC / "docpipe.yaml").read_text("utf-8")
    with pytest.raises(ValueError, match="непустым списком"):
        load_registries(GENERIC / "registries.yaml")


def test_generic_install_passes_config_check(tmp_path: Path) -> None:
    """На голом репозитории нейтральная настройка проверку проходит целиком.

    Набор, который краснеет на `config check` сразу после установки, приучил бы
    к красному: проблемы, которые появятся потом, было бы не отличить.
    """
    repo = tmp_path / "repo"
    engine = tmp_path / "engine" / "codebase-memory-mcp"
    engine.parent.mkdir()
    engine.touch()
    _install(repo, engine=str(engine))
    config = repo / CONFIG_DIR / "docpipe.yaml"

    report = check_config(load_config(config), config, Path("."), repo)

    assert [problem.code for problem in report.problems] == []


# --------------------------------------------------------------------------------------
# Настройка под АС CF
# --------------------------------------------------------------------------------------


def test_bundle_config_excludes_the_documentation_tree() -> None:
    """`docs/**` — дерево документации и каталог настройки внутри него.

    Шаблон обязан заканчиваться на `/**`: без этого он совпал бы только
    с самим каталогом, но не с файлами под ним.
    """
    assert "docs/**" in load_config(CASHFLOW_CONFIG).exclude


def test_bundle_config_names_the_registries() -> None:
    """Без `registries` бизнес-слой отказывается работать целиком: `anchors`
    и `business` искать точки входа негде, а значения по умолчанию у ключа нет.
    """
    config = load_config(CASHFLOW_CONFIG)
    assert config.registries
    assert (CASHFLOW / config.registries).is_file()


def test_bundle_registries_mirror_the_example() -> None:
    """Отличаются только пути. Всё остальное — `item_xpath`, поля, вложенные
    записи — обязано совпадать с примером: разъедься они, и настройщик
    на боевом репозитории окажется единственным, кто это заметит.
    """
    bundled = load_registries(CASHFLOW / "registries.yaml")
    example = load_registries(ROOT / "registries.example.yaml")

    assert [spec.id for spec in bundled] == [spec.id for spec in example]
    for mine, theirs in zip(bundled, example, strict=True):
        assert mine.item_xpath == theirs.item_xpath
        assert mine.fields == theirs.fields
        assert mine.children == theirs.children
        assert mine.follow == theirs.follow


def test_bundle_ruleset_loads() -> None:
    ruleset = load_ruleset(CASHFLOW_RULES, "dotnet")
    assert ruleset.ruleset_version.startswith("2026-")
    assert {rule.id for rule in ruleset.rules} >= {"controller.aspnet", "service", "workflow"}


def test_bundle_ruleset_keeps_domain_entities_named_like_tests() -> None:
    """`StressTest`, `BackTest` — предметные сущности финансового моделирования.

    Шаблоны `**/*Test/**` и `**/*Tests/**` из эталонного набора отсекли бы
    любой каталог с таким именем.
    """
    globs = [
        glob
        for rule in load_ruleset(CASHFLOW_RULES, "dotnet").exclude.rules
        for glob in condition_values(rule.when, "path_glob")
    ]
    assert "**/*Test/**" not in globs
    assert "**/*Tests/**" not in globs
    assert "**/*.Tests/**" in globs


def test_bundle_config_configures_the_web_step(tmp_path: Path) -> None:
    """Без секции `web` шаг не запускается, а причина не видна из сообщения."""
    repo = tmp_path / "repo"
    _install(repo, bundle="cashflow")
    config = _installed_config(repo)

    assert config.web.rules == "rules.yaml"
    assert config.web.out.startswith(f"{CONFIG_DIR}/artifacts/")
    assert config.web.link_out.startswith(f"{CONFIG_DIR}/artifacts/")
    assert config.web.roots and config.web.roots != ["."]


def test_bundle_puts_the_front_documents_in_their_own_branch(tmp_path: Path) -> None:
    """Две ветки дерева документации: бэкенд и фронт."""
    repo = tmp_path / "repo"
    _install(repo, bundle="cashflow")
    config = _installed_config(repo)

    assert config.web.modules_dir
    assert config.web.modules_dir != config.modules_dir
    assert config.web_modules_root != config.modules_root


# --------------------------------------------------------------------------------------
# Шаблоны
# --------------------------------------------------------------------------------------


@both_bundles
def test_installed_tree_holds_the_templates(tmp_path: Path, bundle: str) -> None:
    """Без шаблонов шаг 2 не запускается вовсе, а причина не видна из сообщения.

    Каталог обязан ещё и **загружаться**: скопировать файлы и получить ноль
    скелетов (например, разложив их по подкаталогам) — тот же отказ.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    templates = repo / CONFIG_DIR / "templates"

    installed = load_templates(templates)
    declared = {
        rule.template
        for rule in load_ruleset(BUNDLES[bundle] / "rules.yaml", "dotnet").rules
        if rule.template
    }
    assert declared <= set(installed), declared - set(installed)
    assert sorted(p.name for p in (templates / "examples").glob("*.md"))

    # Скелеты бизнес-документов кладутся тем же циклом, но обход каталога
    # скелетов шага 2 не рекурсивный: подкаталог в его набор не попадает.
    assert sorted(p.stem for p in (templates / "business").glob("*.md")) == [
        "README",
        "capability",
        "entity",
        "process",
    ]
    assert (templates / "business" / "examples" / "process.md").is_file()
    assert "process" not in installed


@both_bundles
def test_installed_templates_are_where_the_configuration_looks(tmp_path: Path, bundle: str) -> None:
    """Путь в `docpipe.yaml` и место установки — две записи об одном и том же.

    Разъедутся они молча: конфигурация останется валидной, установка — успешной,
    а `materialize` откажет на чужой машине.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    configured = _installed_config(repo).templates

    assert (repo / CONFIG_DIR / configured).is_dir(), configured


def test_installer_keeps_edited_templates(tmp_path: Path) -> None:
    """Шаблон правят под проект, как и правила: затирать правку обновлением нельзя."""
    repo = tmp_path / "repo"
    _install(repo)
    edited = repo / CONFIG_DIR / "templates" / "service.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n<!-- под проект -->\n", "utf-8")

    _install(repo)

    assert "<!-- под проект -->" in edited.read_text(encoding="utf-8")
    assert edited.with_suffix(".md.new").is_file()


# --------------------------------------------------------------------------------------
# Установщик
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    ["install.sh", "README.md", "OFFLINE.md", "gitignore", "uv.toml.example"]
    + [f"{bundle.name}/{name}" for bundle in (GENERIC, CASHFLOW) for name in BUNDLE_FILES],
)
def test_installer_inputs_exist(relative: str) -> None:
    """Установщик копирует эти файлы по именам: пропажа любого — отказ на месте."""
    assert (DEPLOY / relative).is_file()


def test_installer_is_executable() -> None:
    assert DEPLOY.joinpath("install.sh").stat().st_mode & 0o111


@both_bundles
def test_installer_is_idempotent(tmp_path: Path, bundle: str) -> None:
    """Повторный запуск с теми же параметрами не создаёт ни одного `.new`.

    Иначе каждое обновление инструмента заваливало бы каталог настройки
    файлами, которые надо читать глазами, — и настоящую расходящуюся правку
    в этой куче никто бы не заметил.
    """
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    result = _install(repo, bundle=bundle)

    assert "СОХРАНЁН" not in result.stderr
    assert "установлен:" not in result.stdout
    assert list((repo / CONFIG_DIR).rglob("*.new")) == []


@both_bundles
def test_installer_keeps_configured_files(tmp_path: Path, bundle: str) -> None:
    """Правка правил классификации — недели работы, и молча заменить её
    обновлением инструмента недопустимо."""
    repo = tmp_path / "repo"
    _install(repo, bundle=bundle)
    rules = repo / CONFIG_DIR / "rules.yaml"
    rules.write_text("# моя правка\n" + rules.read_text(encoding="utf-8"), encoding="utf-8")

    result = _install(repo, bundle=bundle)

    assert "СОХРАНЁН" in result.stderr
    assert rules.read_text(encoding="utf-8").startswith("# моя правка")
    assert (repo / CONFIG_DIR / "rules.yaml.new").is_file()


def test_installer_warns_about_a_flat_ruleset(tmp_path: Path) -> None:
    """Сохранённый набор старого формата после обновления не загрузится.

    Сказать об этом обязан установщик, а не первый упавший прогон в CI:
    `keep_configured` файл не трогает, и снаружи обновление выглядит удачным.
    """
    repo = tmp_path / "repo"
    _install(repo)
    rules = repo / CONFIG_DIR / "rules.yaml"
    rules.write_text("ruleset_version: old\nrules: []\n", encoding="utf-8")

    result = _install(repo)

    assert "старом плоском формате" in result.stderr
    assert "migrate_rules.py" in result.stderr
    assert rules.read_text(encoding="utf-8").startswith("ruleset_version: old")


def test_installer_creates_the_artifacts_directory(tmp_path: Path) -> None:
    """Цели записи второй ступени не получают, и в несуществующий каталог
    прогон не запишет. Узнать об этом в конце длинной работы — дорого."""
    repo = tmp_path / "repo"
    _install(repo)

    assert (repo / CONFIG_DIR / "artifacts").is_dir()


def test_installed_gitignore_covers_run_artifacts(tmp_path: Path) -> None:
    """Манифест — несколько мегабайт на прогон, и коммитить его по умолчанию
    не надо. Кэши здесь не перечислены намеренно: они лежат вне репозитория."""
    repo = tmp_path / "repo"
    _install(repo)
    text = (repo / CONFIG_DIR / ".gitignore").read_text(encoding="utf-8")

    assert "artifacts/" in text
    assert "*.new" in text
    # Сидкар — своей строкой: комментарий разрешает убрать `artifacts/`, чтобы
    # коммитить манифест, а время и хост сидкара коммитить нельзя никогда.
    assert "*.run.json" in text.splitlines()


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
    """`default = true` — «вместо PyPI», а не «в дополнение к нему»."""
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


def test_installer_pins_versions_from_the_lock() -> None:
    """`uv tool install` разрешает зависимости заново и лок сам не читает.

    Без явного снятия версий установка подобрала бы свежие релизы, и окружение
    разошлось бы с тем, на котором гонялись тесты, — молча и в удобный момент.
    """
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "uv export" in text and "--frozen" in text
    assert "--constraints" in text


def test_installer_does_not_write_inside_home_by_default() -> None:
    """На целевой системе работа идёт вне `$HOME`, и умолчание кэша обязано
    это учитывать, а инструкция — называть UV_TOOL_DIR."""
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "WORK" in text
    assert "UV_TOOL_DIR" in text


# Заглушка uv: пишет строку «кэш<TAB>аргументы» на каждый вызов, на
# `uv export -o ФАЙЛ` создаёт файл — установщик дальше передаёт его ограничениями,
# — на `uv tool dir --bin` называет каталог запускалок, на `uv tool dir` —
# каталог окружений инструментов: там установщик ищет интерпретатор
# окружения docpipe.
_UV_STUB = """#!/usr/bin/env bash
printf '%s\\t%s\\n' "${UV_CACHE_DIR:-}" "$*" >> "$UV_STUB_LOG"
if [ "$*" = "tool dir --bin" ]; then echo "$UV_STUB_TOOL_BIN"; fi
if [ "$*" = "tool dir" ]; then echo "$UV_STUB_TOOL_DIR"; fi
prev=""
for arg in "$@"; do
    if [ "$prev" = "-o" ]; then : > "$arg"; fi
    prev="$arg"
done
"""

# `python3` и `python` в PATH, которые падают: слияние настроек агента обязано
# идти интерпретатором окружения инструмента, а не первым попавшимся.
_BROKEN_PYTHON = "#!/bin/sh\necho 'позван python из PATH' >&2\nexit 97\n"


class _Clone:
    """Копия клона с заглушкой вместо uv.

    Установщик пишет `uv.toml` и `.gigacode/settings.json` в СВОЙ клон, поэтому
    гоняется копия: в настоящем клоне тест затёр бы рабочие настройки.
    Окружение инструмента — `<tools>/docpipe/bin/python`, ссылка на
    интерпретатор тестов: так установщик находит его тем же путём, что
    и после настоящего `uv tool install`, без отдельной лазейки для тестов.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        uv_toml: str | None = None,
        agent_settings: str | None = None,
        legacy_settings: str | None = None,
        tool_python: bool = True,
        broken_path_python: bool = False,
    ) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "clone"
        shutil.copytree(DEPLOY, self.root / "deploy")
        shutil.copytree(ROOT / "templates", self.root / "templates")
        if uv_toml is not None:
            (self.root / "uv.toml").write_text(uv_toml, encoding="utf-8")
        if agent_settings is not None:
            self.settings.parent.mkdir()
            self.settings.write_text(agent_settings, encoding="utf-8")
        if legacy_settings is not None:
            (self.root / ".qwen").mkdir()
            (self.root / ".qwen" / "settings.json").write_text(legacy_settings, encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "uv"
        stub.write_text(_UV_STUB, encoding="utf-8")
        stub.chmod(0o755)
        if broken_path_python:
            for name in ("python3", "python"):
                (bin_dir / name).write_text(_BROKEN_PYTHON, encoding="utf-8")
                (bin_dir / name).chmod(0o755)
        tools = tmp_path / "tools"
        if tool_python:
            python = tools / "docpipe" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)

        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        (self.repo / "App.sln").touch()
        self.log = tmp_path / "uv.log"
        # Переменные uv из окружения разработчика заслонили бы то, что ставит установщик.
        env = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["UV_STUB_LOG"] = str(self.log)
        env["UV_STUB_TOOL_BIN"] = str(tmp_path / "tools-bin")
        env["UV_STUB_TOOL_DIR"] = str(tools)
        self.env = env

    @property
    def settings(self) -> Path:
        return self.root / ".gigacode" / "settings.json"

    def install(self, *, index: str = "") -> subprocess.CompletedProcess[str]:
        command = [
            str(self.root / "deploy" / "install.sh"),
            "--repo",
            str(self.repo),
            "--config-dir",
            CONFIG_DIR,
            "--cache-dir",
            str(self.tmp / "cache"),
        ]
        if index:
            command += ["--index", index]
        return subprocess.run(command, capture_output=True, text=True, check=True, env=self.env)

    def calls(self) -> list[tuple[str, str]]:
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [(cache, args) for cache, args in (line.split("\t", 1) for line in lines)]

    def servers(self) -> dict[str, Any]:
        """Записи MCP-серверов, которые установщик обязан положить."""
        launcher = str(self.tmp / "tools-bin" / "docpipe")
        config = str(self.repo / CONFIG_DIR / "docpipe.yaml")
        tail = ["--config", config, "--root", str(self.repo)]
        return {
            "docpipe": {
                "command": launcher,
                "args": ["graph", "serve", *tail],
                "cwd": str(self.repo),
            },
            "docpipe-setup": {
                "command": launcher,
                "args": ["setup", "serve", *tail],
                "cwd": str(self.repo),
            },
        }


def _install_tool(
    tmp_path: Path,
    *,
    index: str = "",
    uv_toml: str | None = None,
    legacy_settings: str | None = None,
    stderr: list[str] | None = None,
) -> tuple[Path, list[tuple[str, str]]]:
    """Один прогон установки инструмента. Возвращает копию клона и вызовы uv
    парами (UV_CACHE_DIR, аргументы); вывод в stderr — в `stderr`, если передан.
    `legacy_settings` — прежнее место записи (`.qwen/settings.json`), которого
    агент контура не читает.
    """
    clone = _Clone(tmp_path, uv_toml=uv_toml, legacy_settings=legacy_settings)
    result = clone.install(index=index)
    if stderr is not None:
        stderr.append(result.stderr)
    return clone.root, clone.calls()


def _tool_install_args(calls: list[tuple[str, str]]) -> str:
    [args] = [args for _, args in calls if args.startswith("tool install")]
    return args


def test_uv_cache_lives_in_the_clone_not_in_home(tmp_path: Path) -> None:
    """Кэш uv — в клоне, и один и тот же для установки и для `uv run` оттуда.

    Строка `cache-dir` в uv.toml нужна проектным командам, переменная
    UV_CACHE_DIR — установке: `uv tool install` настроек проекта не читает.
    Разные значения у них значили бы два кэша, один из которых в `$HOME`.
    """
    clone, calls = _install_tool(tmp_path)

    expected = str(clone / ".uv-cache")
    assert tomllib.loads((clone / "uv.toml").read_text(encoding="utf-8"))["cache-dir"] == expected
    assert calls and all(cache == expected for cache, _ in calls)


def test_settings_without_a_mirror_do_not_replace_the_user_config(tmp_path: Path) -> None:
    """`--config-file` замещает пользовательский конфиг uv, а не дополняет его.

    Файл с одним `cache-dir` отнял бы у установки зеркало, настроенное на машине
    в `~/.config/uv/uv.toml`, и она ушла бы на pypi.org. Проверено на uv 0.11.
    """
    _, calls = _install_tool(tmp_path)

    assert "--config-file" not in _tool_install_args(calls)


def test_tool_install_gets_the_mirror_from_the_clone(tmp_path: Path) -> None:
    """`uv tool install` не читает uv.toml проекта — файл передаётся явно.

    Без этого `--index` установщика действовал на `uv export`, но не на саму
    установку: зеркало, `native-tls`, `find-links` и `offline` из клона
    пропускались, и запрос уходил на pypi.org. Проверено на uv 0.11.
    """
    clone, calls = _install_tool(tmp_path, index="https://mirror.example/simple")

    settings = tomllib.loads((clone / "uv.toml").read_text(encoding="utf-8"))
    assert settings["index"] == [
        {"name": "corp", "url": "https://mirror.example/simple", "default": True}
    ]
    # Ключ верхнего уровня, а не поле зеркала: после `[[index]]` TOML отнёс бы
    # его к таблице, и кэш молча остался бы в `$HOME`.
    assert settings["cache-dir"] == str(clone / ".uv-cache")
    assert settings["native-tls"] is True
    assert f"--config-file {clone / 'uv.toml'}" in _tool_install_args(calls)


def test_installed_templates_are_not_gitignored() -> None:
    """Всё, что копирует установщик из `templates/`, обязано доехать до свежего клона.

    Строка `examples/` в `.gitignore` (каталог проверочных репозиториев) без
    ведущего `/` закрывала и `templates/examples/`: локально образцы лежали,
    а клон на целевой машине падал на `cp` установщика. Проверка идёт по правилам
    игнорирования, а не по индексу, — так она ловит и файл, добавленный завтра.
    """
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("нужен git-клон")
    templates = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "templates").rglob("*.md"))
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *templates],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout.split() == []


def test_bundles_are_not_gitignored() -> None:
    """Та же ловушка для наборов: шаблон без ведущего `/` в `.gitignore` клона
    (`*.yaml` машинных файлов, `README.md` черновиков) закрыл бы файл набора,
    и свежий клон упал бы на `cp` установщика."""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("нужен git-клон")
    files = sorted(
        str(path.relative_to(ROOT)) for bundle in (GENERIC, CASHFLOW) for path in bundle.iterdir()
    )
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *files], cwd=ROOT, capture_output=True, text=True
    )
    assert result.stdout.split() == []


def test_existing_uv_settings_are_kept(tmp_path: Path) -> None:
    """Перенесённый руками кэш и собранный внутри контура uv.toml не трогаются.

    Так устроен `OFFLINE.md`: файл с `find-links` и `offline` пишут руками,
    и установщик обязан взять его как есть, в том числе и кэш.
    """
    own = 'cache-dir = "/elsewhere/uv"\nfind-links = ["/wheels"]\noffline = true\n'
    clone, calls = _install_tool(tmp_path, uv_toml=own)

    assert (clone / "uv.toml").read_text(encoding="utf-8") == own
    assert all(cache == "/elsewhere/uv" for cache, _ in calls)
    assert "--config-file" in _tool_install_args(calls)


@pytest.mark.parametrize(
    "relative",
    ["README.md", "OFFLINE.md", "cashflow-docspipe/README.md", "generic-docspipe/README.md"],
)
def test_documentation_leads_with_the_configuration_check(relative: str) -> None:
    """Пути разрешаются от трёх разных баз, и по имени ключа базу не угадать.

    Проверить их за секунду дешевле, чем узнать на двадцатой минуте разбора,
    и инструкция обязана звать проверку раньше прогона.
    """
    assert "config check" in (DEPLOY / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# MCP-серверы для агента контура (gigacode, форк qwen code со своими каталогами)
# --------------------------------------------------------------------------------------


def test_mcp_servers_are_registered_in_the_clone_not_in_home(tmp_path: Path) -> None:
    """Проектный `.gigacode/settings.json` клона, а не `~/.gigacode` и не репозиторий продукта.

    Серверов два: граф (`graph serve`) и настройка (`setup serve`). Агента
    запускают из корня клона, там же лежат скиллы. Запускалка — полным путём:
    агент поднимает сервер со своим PATH. `cwd` — корень продукта, потому что
    `graph.out` отсчитывается от текущего каталога, как у `graph build`.
    """
    clone = _Clone(tmp_path)
    clone.install()

    settings = json.loads(clone.settings.read_text(encoding="utf-8"))
    assert settings == {"mcpServers": clone.servers()}
    # Путь указывает на настоящую установленную конфигурацию, а не на выдуманную.
    assert (clone.repo / CONFIG_DIR / "docpipe.yaml").is_file()
    assert not (clone.repo / ".gigacode").exists()
    # gigacode не читает `.qwen/`: запись туда была бы невидима на контуре.
    assert not (clone.root / ".qwen").exists()


def test_agent_settings_are_merged_not_shelved(tmp_path: Path) -> None:
    """В `.gigacode/settings.json` клона могли дописать своё — затирать нельзя,
    но и откладывать наши записи в `.new` тоже: агент молча остался бы без них."""
    own = {"mcpServers": {"чужой": {"command": "/opt/other"}}, "model": {"name": "своя"}}
    clone = _Clone(tmp_path, agent_settings=json.dumps(own, ensure_ascii=False))

    result = clone.install()

    settings = json.loads(clone.settings.read_text(encoding="utf-8"))
    assert settings == {
        "mcpServers": {"чужой": {"command": "/opt/other"}, **clone.servers()},
        "model": {"name": "своя"},
    }
    assert not clone.settings.with_name("settings.json.new").exists()
    assert "обновлён: .gigacode/settings.json" in result.stdout

    merged = clone.settings.read_bytes()
    again = clone.install()
    assert "без изменений: .gigacode/settings.json" in again.stdout
    assert clone.settings.read_bytes() == merged


def test_an_earlier_graph_entry_does_not_shelve_the_setup_server(tmp_path: Path) -> None:
    """Машина, где запись `docpipe` уже лежит (установка S01), — главный случай.

    Сравнение файла целиком отправило бы вторую запись в `.new`, и агент молча
    не получил бы инструментов настройки.
    """
    clone = _Clone(tmp_path)
    clone.install()
    earlier = {"mcpServers": {"docpipe": clone.servers()["docpipe"]}}
    clone.settings.write_text(json.dumps(earlier, indent=2) + "\n", encoding="utf-8")

    clone.install()

    assert json.loads(clone.settings.read_text(encoding="utf-8")) == {"mcpServers": clone.servers()}
    assert not clone.settings.with_name("settings.json.new").exists()


def test_a_stale_new_file_is_removed(tmp_path: Path) -> None:
    """`.new` прежних установок описывает то, что теперь лежит в самом файле;
    оставленный, он выглядел бы ждущей переноса правкой."""
    clone = _Clone(tmp_path, agent_settings='{"model": {"name": "своя"}}\n')
    stale = clone.settings.with_name("settings.json.new")
    stale.write_text('{"mcpServers": {"docpipe": {"command": "старая"}}}\n', encoding="utf-8")

    result = clone.install()

    assert not stale.exists()
    assert "удалён устаревший settings.json.new" in result.stdout
    assert json.loads(clone.settings.read_text(encoding="utf-8"))["model"] == {"name": "своя"}


@pytest.mark.parametrize(
    "own",
    [
        '{\n  // gigacode читает комментарии, JSON — нет\n  "mcpServers": {}\n}\n',
        "[]\n",
        '{"mcpServers": []}\n',
    ],
    ids=["comment", "not-an-object", "servers-not-an-object"],
)
def test_unreadable_agent_settings_are_kept(tmp_path: Path, own: str) -> None:
    """Файл, который не читается как JSON-объект, не трогается: рядом `.new`,
    строка в stderr. Переписать его — значит потерять то, что в нём было."""
    clone = _Clone(tmp_path, agent_settings=own)

    result = clone.install()

    assert clone.settings.read_text(encoding="utf-8") == own
    proposed = json.loads(clone.settings.with_name("settings.json.new").read_text("utf-8"))
    assert proposed == {"mcpServers": clone.servers()}
    assert "settings.json.new" in result.stderr


def test_merge_uses_the_tool_interpreter_not_path_python(tmp_path: Path) -> None:
    """`python3` в PATH на контуре может не быть или оказаться древним;
    интерпретатор окружения инструмента есть заведомо — его поставила
    установка минутой раньше."""
    own = '{"model": {"name": "своя"}}\n'
    clone = _Clone(tmp_path, agent_settings=own, broken_path_python=True)

    result = clone.install()

    assert "позван python из PATH" not in result.stderr
    assert json.loads(clone.settings.read_text(encoding="utf-8"))["mcpServers"] == clone.servers()


def test_without_the_tool_interpreter_the_file_is_compared_whole(tmp_path: Path) -> None:
    """Окружения на ожидаемом месте нет — слить нечем. Тогда прежнее поведение:
    файл не трогается, предложение рядом, и об этом сказано вслух."""
    own = '{"model": {"name": "своя"}}\n'
    clone = _Clone(tmp_path, agent_settings=own, tool_python=False)

    result = clone.install()

    assert clone.settings.read_text(encoding="utf-8") == own
    assert clone.settings.with_name("settings.json.new").is_file()
    assert "интерпретатора окружения инструмента" in result.stderr


def test_legacy_agent_settings_are_named_not_removed(tmp_path: Path) -> None:
    """Запись прежних установок лежит в `.qwen/settings.json`, которого gigacode не читает.

    Удалять файл нельзя — в нём могут быть чужие серверы, — но и молчать нельзя:
    человек, увидевший там `docpipe`, будет чинить не тот файл.
    """
    legacy = '{"mcpServers": {"docpipe": {"command": "старая"}}}\n'
    errors: list[str] = []
    clone, _ = _install_tool(tmp_path, legacy_settings=legacy, stderr=errors)

    assert (clone / ".qwen" / "settings.json").read_text(encoding="utf-8") == legacy
    assert (clone / ".gigacode" / "settings.json").is_file()
    assert ".qwen/settings.json" in errors[0]
    assert ".gigacode/settings.json" in errors[0]


def test_agent_settings_stay_out_of_git_and_skills_stay_in() -> None:
    """Пути в настройках агента — этой машины; скиллы рядом — общие."""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("нужен git-клон")

    def ignored(path: str) -> bool:
        check = ["git", "check-ignore", "--no-index", "-q", path]
        return subprocess.run(check, cwd=ROOT).returncode == 0

    assert ignored(".gigacode/settings.json")
    assert ignored(".gigacode/settings.json.new")
    assert not ignored(".gigacode/skills/recon/SKILL.md")
