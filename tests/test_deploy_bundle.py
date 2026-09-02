"""Поставка на целевую машину (`deploy/`).

Поставка — второй набор файлов, описывающих ту же настройку, и разъезжаются
такие пары молча: обнаруживается это на чужой машине, где ни тестов, ни быстрой
обратной связи нет. Поэтому согласованность проверяется здесь.

Модель, которую эти тесты и защищают: **инструмент на машине, настройка
в репозитории продукта**. Кода в репозитории продукта нет вовсе, каталог
настройки задаётся при деплое, кэши лежат снаружи.
"""

import subprocess
import tomllib
from pathlib import Path

import pytest

from docpipe.classify import condition_values, load_ruleset
from docpipe.config import DocpipeConfig, load_config
from docpipe.materialize.ownership import load_ownership
from docpipe.materialize.template import load_templates
from docpipe.registry import load_registries

ROOT = Path(__file__).parent.parent
DEPLOY = ROOT / "deploy"
BUNDLE = DEPLOY / "cashflow-docspipe"
BUNDLE_CONFIG = BUNDLE / "docpipe.yaml"
BUNDLE_RULES = BUNDLE / "rules.yaml"

# Каталог настройки — параметр деплоя, поэтому в тестах он свой и НЕ совпадает
# с тем, что написан в документации: путь, зашитый где-нибудь в конфигурации,
# обязан от этого сломаться.
CONFIG_DIR = "docs/ml/docpipe"


def _install(
    target: Path,
    *,
    config_dir: str = CONFIG_DIR,
    cache: Path | None = None,
    engine: str = "",
) -> subprocess.CompletedProcess[str]:
    """Разложить настройку, не ставя инструмент. Сеть при этом не нужна."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "App.sln").touch()
    command = [
        str(DEPLOY / "install.sh"),
        "--repo",
        str(target),
        "--config-dir",
        config_dir,
        "--cache-dir",
        str(cache or target.parent / "cache"),
        "--no-tool",
    ]
    if engine:
        command += ["--engine", engine]
    return subprocess.run(command, capture_output=True, text=True, check=True)


def _installed_config(target: Path, config_dir: str = CONFIG_DIR) -> DocpipeConfig:
    return load_config(target / config_dir / "docpipe.yaml")


# --------------------------------------------------------------------------------------
# Что попадает в репозиторий продукта — и что не должно
# --------------------------------------------------------------------------------------


def test_installed_tree_holds_no_code(tmp_path: Path) -> None:
    """Кода инструмента в репозитории продукта нет. Это и есть смысл поставки.

    Пока пакет лежал внутри, из этого росло всё остальное: `docs_scan_exclude`,
    чтобы обход документов не заходил в `.venv`, полдюжины строк `.gitignore`
    и режим `--no-install-project` на случай, когда пакет нечем собрать.
    Возврат кода в дерево вернёт и их — молча.
    """
    repo = tmp_path / "repo"
    _install(repo)
    installed = repo / CONFIG_DIR

    assert list(installed.rglob("*.py")) == []
    for forbidden in ("pyproject.toml", "uv.lock", "uv.toml", ".venv", "docpipe"):
        assert not (installed / forbidden).exists(), forbidden


def test_installed_tree_is_configuration_and_templates_only(tmp_path: Path) -> None:
    """Полный список того, что уезжает в репозиторий продукта.

    Список положительный намеренно: файл, добавленный в поставку «заодно»,
    обязан попасть сюда осознанно, а не просочиться.
    """
    repo = tmp_path / "repo"
    _install(repo)
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


def test_installed_tree_holds_exactly_one_ruleset(tmp_path: Path) -> None:
    """Второй набор правил в дереве — молчаливая ошибка, а не удобство.

    Путь `rules/rules.yaml` совпадает со значением `rules` по умолчанию, поэтому
    прогон без `--config` взял бы эталонный набор вместо настроенного
    и завершился бы успешно — с манифестом, построенным не теми правилами.
    """
    repo = tmp_path / "repo"
    _install(repo)
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
# Подстановка при деплое
# --------------------------------------------------------------------------------------


def test_no_placeholder_survives_installation(tmp_path: Path) -> None:
    """Незаменённый плейсхолдер даёт путь, который выглядит настоящим.

    `@CONFIG_DIR@/artifacts/doc-tree.json` — валидное значение: прогон создаст
    каталог с таким именем и напишет туда, а человек будет искать манифест
    там, где его нет.
    """
    repo = tmp_path / "repo"
    _install(repo, engine="/opt/cbm/codebase-memory-mcp")
    text = (repo / CONFIG_DIR / "docpipe.yaml").read_text(encoding="utf-8")

    assert "@" not in text.replace("@CONFIG", ""), "остался плейсхолдер"
    assert "@CONFIG_DIR@" not in text
    assert "@CACHE_DIR@" not in text
    assert "@ENGINE@" not in text


def test_write_targets_follow_the_chosen_config_dir(tmp_path: Path) -> None:
    """Каталог настройки — параметр, и цели записи обязаны за ним ехать.

    Второй ступени поиска у них нет намеренно: угадывать, куда писать,
    инструмент не должен. Значит подставить путь обязан установщик.
    """
    repo = tmp_path / "repo"
    _install(repo, config_dir="tools/docs-pipeline")
    config = _installed_config(repo, "tools/docs-pipeline")

    for value in (config.out, config.worklist, config.web.out, config.web.link_out):
        assert value.startswith("tools/docs-pipeline/artifacts/"), value
    assert config.graph.out.startswith("tools/docs-pipeline/artifacts/")
    assert config.business_root.startswith("tools/docs-pipeline/")
    # Обход документов обязан накрывать каталог настройки: иначе он зайдёт
    # в templates/ и примет скелеты за написанные документы.
    assert "tools/docs-pipeline/**" in config.docs_scan_exclude


def test_inputs_are_written_relative_to_the_configuration(tmp_path: Path) -> None:
    """Входы записаны короткими именами и подстановки НЕ требуют.

    Их разрешает вторая ступень `resolve_input` — каталог самого `docpipe.yaml`.
    Ради этого она и заводилась: путь от корня пришлось бы править при каждом
    переносе каталога настройки, а он теперь задаётся при деплое.
    """
    repo = tmp_path / "repo"
    _install(repo, config_dir="cfg")
    config = _installed_config(repo, "cfg")

    assert config.rules == "rules.yaml"
    assert config.templates == "templates"
    assert config.ownership == "ownership.yaml"
    assert config.registries == "registries.yaml"
    assert config.arch == "arch-registry.yaml"
    assert config.web.rules == "rules.yaml"
    assert config.web.pages == "pages.yaml"

    for name in ("rules.yaml", "ownership.yaml", "registries.yaml", "arch-registry.yaml"):
        assert (repo / "cfg" / name).is_file(), name
    assert (repo / "cfg" / "templates").is_dir()


def test_caches_live_outside_the_repository(tmp_path: Path) -> None:
    """Кэши — вне дерева продукта, и это структурно, а не через `.gitignore`.

    Строка в `.gitignore` защищает от случайного коммита хуже, чем отсутствие
    файлов, а речь о гигабайтах машинного мусора.
    """
    repo = tmp_path / "repo"
    cache = tmp_path / "elsewhere"
    _install(repo, cache=cache)
    config = _installed_config(repo)

    assert Path(config.cache_dir).is_absolute()
    assert Path(config.graph.cache_dir).is_absolute()
    assert not Path(config.cache_dir).is_relative_to(repo)
    assert not Path(config.graph.cache_dir).is_relative_to(repo)


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
# Настройка под АС CF
# --------------------------------------------------------------------------------------


def test_bundle_config_loads() -> None:
    """Конфигурация поставки обязана загружаться и с плейсхолдерами.

    Опечатка в ней — отказ; конфигурация, которая не читается, хуже
    отсутствующей: она выглядит настроенной.
    """
    config = load_config(BUNDLE_CONFIG)
    assert config.enrolled
    assert config.out.endswith(".json")


def test_bundle_config_excludes_the_documentation_tree() -> None:
    """`docs/**` — дерево документации и каталог настройки внутри него.

    Шаблон обязан заканчиваться на `/**`: без этого он совпал бы только
    с самим каталогом, но не с файлами под ним.
    """
    assert "docs/**" in load_config(BUNDLE_CONFIG).exclude


def test_bundle_config_names_the_registries() -> None:
    """Без `registries` бизнес-слой отказывается работать целиком: `anchors`
    и `business` искать точки входа негде, а значения по умолчанию у ключа нет.
    """
    config = load_config(BUNDLE_CONFIG)
    assert config.registries
    assert (BUNDLE / config.registries).is_file()


def test_bundle_registries_mirror_the_example() -> None:
    """Отличаются только пути. Всё остальное — `item_xpath`, поля, вложенные
    записи — обязано совпадать с примером: разъедься они, и настройщик
    на боевом репозитории окажется единственным, кто это заметит.
    """
    bundled = load_registries(BUNDLE / "registries.yaml")
    example = load_registries(ROOT / "registries.example.yaml")

    assert [spec.id for spec in bundled] == [spec.id for spec in example]
    for mine, theirs in zip(bundled, example, strict=True):
        assert mine.item_xpath == theirs.item_xpath
        assert mine.fields == theirs.fields
        assert mine.children == theirs.children
        assert mine.follow == theirs.follow


def test_bundle_ruleset_loads() -> None:
    ruleset = load_ruleset(BUNDLE_RULES, "dotnet")
    assert ruleset.ruleset_version.startswith("2026-")
    assert {rule.id for rule in ruleset.rules} >= {"controller.aspnet", "service", "workflow"}


def test_bundle_ruleset_keeps_domain_entities_named_like_tests() -> None:
    """`StressTest`, `BackTest` — предметные сущности финансового моделирования.

    Шаблоны `**/*Test/**` и `**/*Tests/**` из эталонного набора отсекли бы
    любой каталог с таким именем.
    """
    globs = [
        glob
        for rule in load_ruleset(BUNDLE_RULES, "dotnet").exclude.rules
        for glob in condition_values(rule.when, "path_glob")
    ]
    assert "**/*Test/**" not in globs
    assert "**/*Tests/**" not in globs
    assert "**/*.Tests/**" in globs


def test_bundle_ownership_is_an_empty_starter() -> None:
    """Заготовка обязана грузиться и обязана быть пустой.

    Выдуманные команды хуже отсутствующих: правило с несуществующим
    `module_glob` молча не срабатывает, и «ничьих узлов 4820» уже не отличить
    от «правило написано с опечаткой».
    """
    ownership = load_ownership(BUNDLE / "ownership.yaml")
    assert ownership.teams == []
    assert ownership.rules == []


def test_bundle_pages_file_loads_and_is_empty() -> None:
    """Состав страниц — решение настройщика, но грузиться файл обязан сразу."""
    from docpipe.web.overrides import load_overrides

    overrides = load_overrides(BUNDLE / "pages.yaml")
    assert overrides.add == []
    assert overrides.remove == []
    assert overrides.features == []


def test_bundle_arch_registry_is_valid_and_empty() -> None:
    """Реестр приезжает пустым, и это рабочее состояние.

    Заполнять его догадками из другого репозитория нельзя: неверная запись
    отсюда выходит уверенным неправильным ребром в ответе на вопрос, и отличить
    её от факта, извлечённого из XML, уже нельзя.
    """
    from docpipe.arch import load_arch_registry

    assert load_arch_registry(BUNDLE / "arch-registry.yaml").records == ()


def test_bundle_config_configures_the_web_step(tmp_path: Path) -> None:
    """Без секции `web` шаг не запускается, а причина не видна из сообщения."""
    repo = tmp_path / "repo"
    _install(repo)
    config = _installed_config(repo)

    assert config.web.rules == "rules.yaml"
    assert config.web.out.startswith(f"{CONFIG_DIR}/artifacts/")
    assert config.web.link_out.startswith(f"{CONFIG_DIR}/artifacts/")
    assert config.web.roots and config.web.roots != ["."]


def test_bundle_web_ruleset_does_not_require_public() -> None:
    """`require_public: true`, скопированный из секции `dotnet`, отсеял бы весь
    фронт: у TypeScript модификатора `public` на уровне объявления нет вовсе."""
    assert load_ruleset(BUNDLE_RULES, "web").exclude.require_public is False


def test_bundle_puts_the_front_documents_in_their_own_branch(tmp_path: Path) -> None:
    """Две ветки дерева документации: бэкенд и фронт."""
    repo = tmp_path / "repo"
    _install(repo)
    config = _installed_config(repo)

    assert config.web.modules_dir
    assert config.web.modules_dir != config.modules_dir
    assert config.web_modules_root != config.modules_root


def test_bundle_configures_the_graph_step(tmp_path: Path) -> None:
    """Секции `graph` и `arch` в поставке не было вовсе, и весь готовый механизм
    разведки и связей доехать на целевую машину не мог физически."""
    repo = tmp_path / "repo"
    _install(repo, engine="/opt/cbm/codebase-memory-mcp")
    config = _installed_config(repo)

    assert config.graph.engine_path == "/opt/cbm/codebase-memory-mcp"
    # Пусто — «взять закреплённую сумму из моста»: ключ существует, чтобы
    # закрепить свою сборку, а не чтобы не проверять.
    assert config.graph.engine_sha256 == ""
    assert config.graph.mode == "fast"
    assert config.arch == "arch-registry.yaml"


def test_engine_stays_empty_without_the_flag(tmp_path: Path) -> None:
    """Умолчания у `engine_path` нет намеренно: запуск того, что нашлось в PATH,
    означает числа от другой версии движка. Пусто — отказ с указанием, что
    заполнить, и установщик про это говорит."""
    repo = tmp_path / "repo"
    result = _install(repo)

    assert _installed_config(repo).graph.engine_path == ""
    assert "--engine" in result.stdout


# --------------------------------------------------------------------------------------
# Шаблоны
# --------------------------------------------------------------------------------------


def test_installed_tree_holds_the_templates(tmp_path: Path) -> None:
    """Без шаблонов шаг 2 не запускается вовсе, а причина не видна из сообщения.

    Каталог обязан ещё и **загружаться**: скопировать файлы и получить ноль
    скелетов (например, разложив их по подкаталогам) — тот же отказ.
    """
    repo = tmp_path / "repo"
    _install(repo)
    templates = repo / CONFIG_DIR / "templates"

    installed = load_templates(templates)
    declared = {
        rule.template for rule in load_ruleset(BUNDLE_RULES, "dotnet").rules if rule.template
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


def test_installed_templates_are_where_the_configuration_looks(tmp_path: Path) -> None:
    """Путь в `docpipe.yaml` и место установки — две записи об одном и том же.

    Разъедутся они молча: конфигурация останется валидной, установка — успешной,
    а `materialize` откажет на чужой машине.
    """
    repo = tmp_path / "repo"
    _install(repo)
    configured = _installed_config(repo).templates

    assert (repo / CONFIG_DIR / configured).is_dir(), configured


def test_installer_keeps_edited_templates(tmp_path: Path) -> None:
    """Шаблон правят под проект, как и правила: затирать правку обновлением нельзя."""
    repo = tmp_path / "repo"
    _install(repo)
    edited = repo / CONFIG_DIR / "templates" / "service.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n<!-- под АС CF -->\n", "utf-8")

    _install(repo)

    assert "<!-- под АС CF -->" in edited.read_text(encoding="utf-8")
    assert edited.with_suffix(".md.new").is_file()


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
        "uv.toml.example",
        "cashflow-docspipe/docpipe.yaml",
        "cashflow-docspipe/rules.yaml",
        "cashflow-docspipe/ownership.yaml",
        "cashflow-docspipe/pages.yaml",
        "cashflow-docspipe/registries.yaml",
        "cashflow-docspipe/arch-registry.yaml",
        "cashflow-docspipe/README.md",
    ],
)
def test_installer_inputs_exist(relative: str) -> None:
    """Установщик копирует эти файлы по именам: пропажа любого — отказ на месте."""
    assert (DEPLOY / relative).is_file()


def test_installer_is_executable() -> None:
    assert DEPLOY.joinpath("install.sh").stat().st_mode & 0o111


def test_installer_is_idempotent(tmp_path: Path) -> None:
    """Повторный запуск с теми же параметрами не создаёт ни одного `.new`.

    Иначе каждое обновление инструмента заваливало бы каталог настройки
    файлами, которые надо читать глазами, — и настоящую расходящуюся правку
    в этой куче никто бы не заметил.
    """
    repo = tmp_path / "repo"
    _install(repo)
    result = _install(repo)

    assert "СОХРАНЁН" not in result.stderr
    assert "установлен:" not in result.stdout
    assert list((repo / CONFIG_DIR).rglob("*.new")) == []


def test_installer_keeps_configured_files(tmp_path: Path) -> None:
    """Правка правил классификации — недели работы, и молча заменить её
    обновлением инструмента недопустимо."""
    repo = tmp_path / "repo"
    _install(repo)
    rules = repo / CONFIG_DIR / "rules.yaml"
    rules.write_text("# моя правка\n" + rules.read_text(encoding="utf-8"), encoding="utf-8")

    result = _install(repo)

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


@pytest.mark.parametrize("relative", ["README.md", "OFFLINE.md", "cashflow-docspipe/README.md"])
def test_documentation_leads_with_the_configuration_check(relative: str) -> None:
    """Пути разрешаются от трёх разных баз, и по имени ключа базу не угадать.

    Проверить их за секунду дешевле, чем узнать на двадцатой минуте разбора,
    и инструкция обязана звать проверку раньше прогона.
    """
    assert "config check" in (DEPLOY / relative).read_text(encoding="utf-8")
