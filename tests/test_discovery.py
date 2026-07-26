"""Проверка обхода файловой системы и конфигурации (T04)."""

from pathlib import Path

import pytest
import yaml

from docpipe.config import DocpipeConfig, load_config
from docpipe.discovery import discover, in_scope, matches_glob, normalize_scope

EXCLUDE = ["**/obj/**", "**/bin/**", "**/*.g.cs"]

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
    assert config.rules == "rules/dotnet.yaml"


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


def test_config_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.yaml")
