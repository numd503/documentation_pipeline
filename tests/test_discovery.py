"""Проверка обхода файловой системы и конфигурации (T04)."""

from pathlib import Path

import pytest
import yaml

from docpipe.config import DocpipeConfig, load_config
from docpipe.discovery import discover, in_scope, matches_glob, normalize_scope
from docpipe.emit import DEFAULT_EXCLUDE, exclude_globs

EXCLUDE = ["**/obj/**", "**/bin/**", "**/*.g.cs"]

# Обход фронта идёт с полным набором умолчаний: без `**/node_modules/**`
# проверять там нечего.
WEB_EXCLUDE = exclude_globs(DocpipeConfig())

# 27 из 29: два файла под node_modules отсекаются обходом.
EXPECTED_TS = [
    "nx-app/apps/widget/src/app/widget.component.ts",
    "nx-app/apps/widget/src/app/widget.service.ts",
    "src/app/app.routes.ts",
    "src/app/cf-api/interceptors/auth.interceptor.ts",
    "src/app/cf-api/interceptors/fix-url.interceptor.ts",
    "src/app/cf-api/resources/model.service.ts",
    "src/app/cf-api/resources/model.ts",
    "src/app/cf-api/services/url-decorator.service.ts",
    "src/app/inner-debt/services/inner-debt.service.ts",
    "src/app/inner-debt/state/debt.actions.ts",
    "src/app/inner-debt/state/debt.state.ts",
    "src/app/legacy/legacy.module.ts",
    "src/app/routes/forecast/forecast.component.ts",
    "src/app/routes/models/detail/detail.component.ts",
    "src/app/routes/models/list/list.component.spec.ts",
    "src/app/routes/models/list/list.component.ts",
    "src/app/routes/models/quiz/quiz.component.ts",
    "src/app/routes/shell.component.ts",
    "src/app/routesPath/lazy.ts",
    "src/app/routesPath/models.ts",
    "src/app/shared/index.ts",
    "src/app/shared/services/audit.service.ts",
    "src/app/shared/services/base-api.service.ts",
    "src/app/shared/services/items.service.ts",
    "src/app/types/global.d.ts",
    "src/environments/environment.ts",
    "src/main.ts",
]

# 11 из 12: Sample.Generated.g.cs отсекается дважды — по obj/ и по *.g.cs.
EXPECTED_CS = [
    "src/Sample.Common/Abstractions/IPricingProvider.cs",
    "src/Sample.Common/Web/BaseApiController.cs",
    "src/Sample.Pricing.Api/Controllers/PricingController.cs",
    "src/Sample.Pricing.Api/Grid/RiskComputeService.cs",
    "src/Sample.Pricing.Api/Models/PriceDto.cs",
    "src/Sample.Pricing.Api/Program.cs",
    "src/Sample.Pricing.Api/Providers/CurveProvider.cs",
    "src/Sample.Pricing.Api/Services/IPricingService.cs",
    "src/Sample.Pricing.Api/Services/PricingService.Calculations.cs",
    "src/Sample.Pricing.Api/Services/PricingService.cs",
    "src/Sample.Pricing.Api/Workflows/ValuationWorkflow.cs",
]


# --------------------------------------------------------------------------------------
# matches_glob: разбор ** — место, где fnmatch ведёт себя неочевидно
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "glob", "expected"),
    [
        ("src/A/obj/x/y.cs", "**/obj/**", True),
        ("src/A/obj/y.cs", "**/obj/**", True),
        # Голый fnmatch здесь даёт False: шаблон требует '/' перед obj.
        ("obj/y.cs", "**/obj/**", True),
        ("src/A/Sample.g.cs", "**/*.g.cs", True),
        # Тот же случай для файла в корне репозитория.
        ("Sample.g.cs", "**/*.g.cs", True),
        # Похожие, но другие имена совпадать не должны.
        ("src/A/objects/y.cs", "**/obj/**", False),
        ("src/A/x.cs", "**/obj/**", False),
        ("src/A/Sample.cs", "**/*.g.cs", False),
        ("src/A/Foo.Designer.cs", "**/*.Designer.cs", True),
    ],
)
def test_matches_glob(path: str, glob: str, expected: bool) -> None:
    assert matches_glob(path, glob) is expected


# --------------------------------------------------------------------------------------
# scope
# --------------------------------------------------------------------------------------


def test_normalize_scope_handles_separators_and_slashes() -> None:
    assert normalize_scope(["src/A/", "/src/B", "src\\C"]) == [
        ("src", "A"),
        ("src", "B"),
        ("src", "C"),
    ]


def test_normalize_scope_treats_root_as_no_limit() -> None:
    assert normalize_scope(None) is None
    assert normalize_scope(["."]) is None
    assert normalize_scope([""]) is None


def test_in_scope_compares_segments_not_substrings() -> None:
    """Сравнение по подстроке захватило бы соседний модуль с похожим именем."""
    scope = normalize_scope(["src/Sample.Common"])
    assert in_scope("src/Sample.Common/Web/X.cs", scope) is True
    assert in_scope("src/Sample.CommonExtras/X.cs", scope) is False
    assert in_scope("src/Sample.Pricing.Api/X.cs", scope) is False


# --------------------------------------------------------------------------------------
# discover на фикстуре
# --------------------------------------------------------------------------------------


def test_discover_cs_files(sample_solution: Path) -> None:
    result = discover(sample_solution, EXCLUDE)
    assert result.cs_files == EXPECTED_CS
    assert len(result.cs_files) == 11
    assert not any("Sample.Generated.g.cs" in path for path in result.cs_files)


def test_discover_project_files(sample_solution: Path) -> None:
    result = discover(sample_solution, EXCLUDE)
    assert result.csproj_files == [
        "src/Sample.Common/Sample.Common.csproj",
        "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
    ]
    assert result.sln_files == ["SampleSolution.sln"]


def test_discover_finds_slnx_alongside_sln(tmp_path: Path) -> None:
    """`.slnx` — новый формат решения; в ABP их 30, а классических `.sln` ноль.

    Обход только по `.sln` не нашёл бы там ни одного решения.
    """
    (tmp_path / "App.sln").write_text("", encoding="utf-8")
    (tmp_path / "New.slnx").write_text("<Solution />", encoding="utf-8")
    (tmp_path / "App.sln.DotSettings").write_text("", encoding="utf-8")

    result = discover(tmp_path, EXCLUDE)
    assert result.sln_files == ["App.sln", "New.slnx"]


def test_discover_is_stable_across_calls(sample_solution: Path) -> None:
    assert discover(sample_solution, EXCLUDE) == discover(sample_solution, EXCLUDE)


def test_discover_with_scope(sample_solution: Path) -> None:
    result = discover(sample_solution, EXCLUDE, scope=["src/Sample.Common"])
    assert result.cs_files == [
        "src/Sample.Common/Abstractions/IPricingProvider.cs",
        "src/Sample.Common/Web/BaseApiController.cs",
    ]
    assert result.csproj_files == ["src/Sample.Common/Sample.Common.csproj"]
    # .sln лежит в корне, вне scope.
    assert result.sln_files == []


def test_discover_scope_does_not_match_prefix_of_sibling(sample_solution: Path) -> None:
    result = discover(sample_solution, EXCLUDE, scope=["src/Sample"])
    assert result.cs_files == []


# --------------------------------------------------------------------------------------
# discover на фронте (F02)
# --------------------------------------------------------------------------------------


def test_discover_ts_files(web_workspace: Path) -> None:
    result = discover(web_workspace, WEB_EXCLUDE)
    assert result.ts_files == EXPECTED_TS
    assert len(result.ts_files) == 27


def test_discover_excludes_node_modules_by_traversal(web_workspace: Path) -> None:
    """`node_modules` вырезается обходом, а не правилом классификации.

    Десятки тысяч файлов: правило разобрало бы их все, чтобы потом отсеять.
    """
    result = discover(web_workspace, WEB_EXCLUDE)
    assert not any(path.startswith("node_modules/") for path in result.ts_files)
    assert not any(path.startswith("node_modules/") for path in result.web_project_files)


def test_discover_without_excludes_sees_node_modules(web_workspace: Path) -> None:
    """Контрольная проверка: без исключений файлы под node_modules находятся.

    Без неё предыдущий тест мог бы проходить просто потому, что каталог пуст
    или выкинут из репозитория `.gitignore`.
    """
    result = discover(web_workspace, [])
    assert "node_modules/junk/index.ts" in result.ts_files
    assert "node_modules/exceljs/dist/exceljs.bare.d.ts" in result.ts_files
    assert len(result.ts_files) == 29


def test_discover_keeps_spec_and_declaration_files(web_workspace: Path) -> None:
    """`*.spec.ts` и `*.d.ts` обходом НЕ исключаются.

    Оба нужны резолву: тестовые базовые классы входят в граф наследования,
    а декларации — в разрешение импортов. Документами они не становятся,
    но отсеивает их правило с причиной, а не обход.
    """
    result = discover(web_workspace, WEB_EXCLUDE)
    assert "src/app/routes/models/list/list.component.spec.ts" in result.ts_files
    assert "src/app/types/global.d.ts" in result.ts_files


def test_discover_html_files(web_workspace: Path) -> None:
    """`.html` нужен не как язык, а как хэш: шаблон входит в `impl_hash` компонента."""
    result = discover(web_workspace, WEB_EXCLUDE)
    assert result.html_files == [
        "src/app/routes/models/list/list.component.html",
        "src/index.html",
    ]


def test_discover_web_project_files(web_workspace: Path) -> None:
    """Обе формы объявления модуля фронта: `angular.json` и nx `project.json`."""
    result = discover(web_workspace, WEB_EXCLUDE)
    assert result.web_project_files == [
        "angular.json",
        "nx-app/apps/widget/project.json",
        "nx-app/nx.json",
        "package.json",
    ]


def test_web_project_files_are_matched_by_whole_name(tmp_path: Path) -> None:
    """Имя сравнивается целиком, а не суффиксом.

    Glob `*nx.json` ловит любой файл, чьё имя кончается на `nx.json`:
    на разведке в раздел про nx первой строкой встал файл тестовых данных
    `DuplicatedRecordsBugEvanx.json`, и раздел выглядел осмысленно.
    """
    (tmp_path / "DuplicatedRecordsBugEvanx.json").write_text("{}", encoding="utf-8")
    (tmp_path / "my-project.json").write_text("{}", encoding="utf-8")
    (tmp_path / "nx.json").write_text("{}", encoding="utf-8")

    assert discover(tmp_path, WEB_EXCLUDE).web_project_files == ["nx.json"]


def test_discover_web_scope_and_roots_apply(web_workspace: Path) -> None:
    """Сужение работает на фронте так же, как на .NET, — теми же полями."""
    result = discover(web_workspace, WEB_EXCLUDE, scope=["nx-app"])
    assert result.ts_files == [
        "nx-app/apps/widget/src/app/widget.component.ts",
        "nx-app/apps/widget/src/app/widget.service.ts",
    ]
    assert result.html_files == []


def test_discover_web_is_stable_across_calls(web_workspace: Path) -> None:
    assert discover(web_workspace, WEB_EXCLUDE) == discover(web_workspace, WEB_EXCLUDE)


@pytest.mark.parametrize("directory", ["dist", "coverage", ".angular"])
def test_web_build_output_directories_are_excluded_by_default(
    tmp_path: Path, directory: str
) -> None:
    """Копии исходников в выходных каталогах дали бы второе объявление символа."""
    (tmp_path / directory).mkdir()
    (tmp_path / directory / "app.ts").write_text("export class A {}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export class A {}", encoding="utf-8")

    assert discover(tmp_path, WEB_EXCLUDE).ts_files == ["src/app.ts"]


# --------------------------------------------------------------------------------------
# roots: ключ конфигурации, который до этого объявлялся, но не читался
# --------------------------------------------------------------------------------------


def test_discover_with_roots(sample_solution: Path) -> None:
    result = discover(sample_solution, EXCLUDE, roots=["src/Sample.Common"])
    assert result.cs_files == [
        "src/Sample.Common/Abstractions/IPricingProvider.cs",
        "src/Sample.Common/Web/BaseApiController.cs",
    ]


def test_discover_roots_default_means_everything(sample_solution: Path) -> None:
    """`roots: ["."]` — значение по умолчанию, и оно обязано ничего не сужать."""
    assert discover(sample_solution, EXCLUDE, roots=["."]).cs_files == EXPECTED_CS
    assert discover(sample_solution, EXCLUDE, roots=None).cs_files == EXPECTED_CS


def test_discover_roots_and_scope_add_up(sample_solution: Path) -> None:
    """Сужения складываются, а не заменяют друг друга.

    Замена дала бы прогон, где `--scope` втихую расширяет обход за пределы
    настроенных `roots` — то есть флаг на один прогон отменял бы конфигурацию.
    """
    result = discover(
        sample_solution, EXCLUDE, scope=["src/Sample.Pricing.Api"], roots=["src/Sample.Common"]
    )
    assert result.cs_files == []


def test_discover_roots_narrows_the_scan_through_config(sample_solution: Path) -> None:
    """Ключ доходит до обхода: до этого он разбирался и молча игнорировался."""
    from docpipe.emit import run as run_scan

    narrowed = run_scan(sample_solution, DocpipeConfig(roots=["src/Sample.Common"]))
    everything = run_scan(sample_solution, DocpipeConfig())
    assert len(narrowed.manifest.modules) == 1
    assert len(everything.manifest.modules) > len(narrowed.manifest.modules)


def test_discover_without_excludes_sees_generated_file(sample_solution: Path) -> None:
    """Контрольная проверка: без исключений файл под obj/ находится.

    Без неё тест на исключения мог бы проходить просто потому, что файла нет.
    """
    result = discover(sample_solution, [])
    assert len(result.cs_files) == 12
    assert "src/Sample.Pricing.Api/obj/Debug/net8.0/Sample.Generated.g.cs" in result.cs_files


def test_pruning_does_not_change_result(sample_solution: Path) -> None:
    """Отсечение каталогов — оптимизация, а не фильтр.

    `**/obj` отсекает каталог целиком, `obj/**` — только файлы под ним.
    Результат обязан совпадать.
    """
    pruned = discover(sample_solution, ["**/obj/**"])
    # Шаблон без '/**' на конце не порождает dir-глобов, обход идёт внутрь obj/.
    unpruned = discover(sample_solution, ["**/obj/*", "**/obj/*/*", "**/obj/*/*/*"])
    assert pruned.cs_files == unpruned.cs_files


def test_discover_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        discover(tmp_path / "nope", EXCLUDE)


def test_discover_ignores_symlinked_directories(tmp_path: Path, sample_solution: Path) -> None:
    """Симлинк на дерево исходников не должен порождать дубли символов."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "real").mkdir()
    (root / "real" / "A.cs").write_text("class A {}", encoding="utf-8")
    (root / "link").symlink_to(sample_solution, target_is_directory=True)

    result = discover(root, EXCLUDE)
    assert result.cs_files == ["real/A.cs"]


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


def test_config_defaults() -> None:
    config = load_config(None)
    assert config == DocpipeConfig()
    assert config.roots == ["."]
    assert config.enrolled == ["**"]
    assert config.domains == {}
    assert config.rules == "rules/rules.yaml"


def test_config_from_file(tmp_path: Path) -> None:
    path = tmp_path / "docpipe.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "roots": ["src"],
                "enrolled": ["src/Cf.Pricing.*"],
                "domains": {"src/Cf.Pricing.*": "pricing"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.roots == ["src"]
    assert config.enrolled == ["src/Cf.Pricing.*"]
    assert config.domains == {"src/Cf.Pricing.*": "pricing"}
    # Не заданные поля остаются дефолтными.
    assert config.cache_dir == ".docpipe/cache"


def test_config_empty_file_equals_defaults(tmp_path: Path) -> None:
    path = tmp_path / "docpipe.yaml"
    path.write_text("", encoding="utf-8")
    assert load_config(path) == DocpipeConfig()


def test_config_rejects_unknown_key(tmp_path: Path) -> None:
    """Опечатка в имени ключа должна падать, а не игнорироваться молча."""
    path = tmp_path / "docpipe.yaml"
    path.write_text(yaml.safe_dump({"rootz": ["src"]}), encoding="utf-8")
    with pytest.raises(Exception, match="rootz"):
        load_config(path)


def test_config_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "docpipe.yaml"
    path.write_text(yaml.safe_dump(["a", "b"]), encoding="utf-8")
    with pytest.raises(ValueError, match="словарём"):
        load_config(path)


def test_config_rejects_absolute_cache_dir() -> None:
    """`cache_dir` склеивается с `--root`, и абсолютное значение выигрывает склейку.

    `Path("/repo") / "/tmp/x"` == `/tmp/x`: без проверки кэш молча уезжает
    за пределы репозитория, и об этом не сообщает ничто.
    """
    with pytest.raises(ValueError, match="cache_dir"):
        DocpipeConfig(cache_dir="/tmp/elsewhere")


def test_config_rejects_absolute_roots() -> None:
    with pytest.raises(ValueError, match="roots"):
        DocpipeConfig(roots=["/abs/src"])


def test_config_roots_normalizes_like_other_repo_relative_keys() -> None:
    assert DocpipeConfig(roots=["./src/", "a//b"]).roots == ["src", "a/b"]


def test_config_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.yaml")


# --------------------------------------------------------------------------------------
# exclude в конфигурации
# --------------------------------------------------------------------------------------


def test_exclude_defaults_to_empty() -> None:
    """Ключ пуст, но встроенные шаблоны действуют — и .NET, и фронта."""
    assert DocpipeConfig().exclude == []
    assert exclude_globs(DocpipeConfig()) == sorted(DEFAULT_EXCLUDE)
    for builtin in [*EXCLUDE, "**/node_modules/**", "**/dist/**", "**/.angular/**"]:
        assert builtin in exclude_globs(DocpipeConfig())


def test_exclude_from_config_adds_to_builtin() -> None:
    """Шаблоны из конфигурации складываются со встроенными, а не замещают их.

    Замещение сделало бы `exclude: ["docs/**"]` тихим включением `obj/` и `bin/`
    обратно в документацию.
    """
    globs = exclude_globs(DocpipeConfig(exclude=["docs/**"]))
    assert "docs/**" in globs
    for builtin in EXCLUDE:
        assert builtin in globs


def test_exclude_is_deterministic_regardless_of_order() -> None:
    first = exclude_globs(DocpipeConfig(exclude=["docs/**", "vendor/**"]))
    second = exclude_globs(DocpipeConfig(exclude=["vendor/**", "docs/**", "docs/**"]))
    assert first == second


def test_excluded_directory_is_not_traversed(tmp_path: Path) -> None:
    """Каталог с самим инструментом внутри сканируемого репозитория.

    Именно этот случай в АС CF: `docs/ml/docspipe` лежит внутри репозитория,
    и в нём есть и `.cs`-фикстуры, и `.csproj`. Без исключения они попали бы
    в манифест как настоящие модули продукта.
    """
    (tmp_path / "src/App").mkdir(parents=True)
    (tmp_path / "src/App/App.csproj").write_text("<Project />", encoding="utf-8")
    (tmp_path / "src/App/Service.cs").write_text("class Service {}", encoding="utf-8")
    (tmp_path / "docs/ml/docspipe/fixtures").mkdir(parents=True)
    (tmp_path / "docs/ml/docspipe/fixtures/Fake.csproj").write_text("<Project />", encoding="utf-8")
    (tmp_path / "docs/ml/docspipe/fixtures/Fake.cs").write_text("class Fake {}", encoding="utf-8")

    without = discover(tmp_path, exclude_globs(DocpipeConfig()))
    assert len(without.cs_files) == 2

    with_exclude = discover(tmp_path, exclude_globs(DocpipeConfig(exclude=["docs/**"])))
    assert with_exclude.cs_files == ["src/App/Service.cs"]
    assert with_exclude.csproj_files == ["src/App/App.csproj"]


def test_exclude_without_double_star_matches_only_the_directory_itself(tmp_path: Path) -> None:
    """`docs` без `/**` не исключает содержимое — шаблон сравнивается с путём файла.

    Ловушка ровно того сорта, что выглядит как успех: команда отработала,
    манифест построился, а каталог как сканировался, так и сканируется.
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/A.cs").write_text("class A {}", encoding="utf-8")

    assert discover(tmp_path, exclude_globs(DocpipeConfig(exclude=["docs"]))).cs_files == [
        "docs/A.cs"
    ]
    assert discover(tmp_path, exclude_globs(DocpipeConfig(exclude=["docs/**"]))).cs_files == []
