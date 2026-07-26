"""Проверка разбора .csproj и .sln (T05).

Случаи с BOM, наследованием TargetFramework и папками решения найдены
в eShopOnWeb — см. docs/findings-eshoponweb.md. Тесты самодостаточны:
`examples/` в репозиторий не входит, поэтому реальные конструкции
воспроизведены в фикстурах и во временных каталогах.
"""

from pathlib import Path

import pytest

from docpipe.dotnet.csproj import parse_csproj
from docpipe.dotnet.sln import parse_sln

# --------------------------------------------------------------------------------------
# SampleSolution — канонические случаи
# --------------------------------------------------------------------------------------


def test_multi_targeted_project(sample_solution: Path) -> None:
    module = parse_csproj(
        sample_solution / "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj", sample_solution
    )
    assert module.name == "Sample.Pricing.Api"
    assert module.id == "module:Sample.Pricing.Api"
    assert module.csproj == "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj"
    assert module.target_frameworks == ["net8.0", "net9.0"]
    assert module.project_references == ["Sample.Common"]
    assert module.package_references == ["Apache.Ignite"]


def test_single_targeted_project(sample_solution: Path) -> None:
    module = parse_csproj(
        sample_solution / "src/Sample.Common/Sample.Common.csproj", sample_solution
    )
    assert module.target_frameworks == ["net8.0"]
    assert module.project_references == []
    assert module.package_references == []


def test_placeholders_are_filled_later(sample_solution: Path) -> None:
    """domain и enrolled проставляет tree.py, когда известна конфигурация."""
    module = parse_csproj(
        sample_solution / "src/Sample.Common/Sample.Common.csproj", sample_solution
    )
    assert module.domain == ""
    assert module.enrolled is True


def test_sln_lists_both_projects(sample_solution: Path) -> None:
    projects = parse_sln(sample_solution / "SampleSolution.sln", sample_solution)
    assert projects == [
        "src/Sample.Common/Sample.Common.csproj",
        "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
    ]
    for relative in projects:
        assert (sample_solution / relative).is_file()


# --------------------------------------------------------------------------------------
# WildSolution — случаи из реальных репозиториев
# --------------------------------------------------------------------------------------


def test_csproj_with_bom(wild_solution: Path) -> None:
    """Все 10 .csproj в eShopOnWeb сохранены в UTF-8 с BOM.

    Чтение через read_text(encoding='utf-8') оставило бы BOM первым символом
    и разбор упал бы с 'not well-formed'.
    """
    path = wild_solution / "src/Wild.Api/Wild.Api.csproj"
    assert path.read_bytes()[:3] == b"\xef\xbb\xbf", "фикстура потеряла BOM"

    module = parse_csproj(path, wild_solution)
    assert module.name == "Wild.Api"
    assert module.target_frameworks == ["net8.0"]


def test_target_framework_inherited_from_props(wild_solution: Path) -> None:
    """Проект без собственного TargetFramework берёт его из Directory.Build.props.

    В eShopOnWeb так устроены ВСЕ проекты: 0 из 10 объявляют TFM у себя.
    Без обхода вверх target_frameworks был бы пуст у каждого модуля.
    """
    path = wild_solution / "tests/Wild.Tests/Wild.Tests.csproj"
    assert "TargetFramework" not in path.read_text(encoding="utf-8")

    module = parse_csproj(path, wild_solution)
    assert module.target_frameworks == ["net8.0"]


def test_project_reference_with_nested_relative_path(wild_solution: Path) -> None:
    """`..\\..\\src\\Wild.Api\\Wild.Api.csproj` -> имя модуля."""
    module = parse_csproj(wild_solution / "tests/Wild.Tests/Wild.Tests.csproj", wild_solution)
    assert module.project_references == ["Wild.Api"]


def test_package_reference_with_child_elements(wild_solution: Path) -> None:
    """PackageReference с вложенными PrivateAssets/IncludeAssets — обычное дело."""
    module = parse_csproj(wild_solution / "tests/Wild.Tests/Wild.Tests.csproj", wild_solution)
    assert module.package_references == ["Microsoft.NET.Test.Sdk", "xunit"]


def test_wild_sln_lists_both_projects(wild_solution: Path) -> None:
    projects = parse_sln(wild_solution / "WildSolution.sln", wild_solution)
    assert projects == [
        "src/Wild.Api/Wild.Api.csproj",
        "tests/Wild.Tests/Wild.Tests.csproj",
    ]


# --------------------------------------------------------------------------------------
# Конструкции, воспроизведённые во временных каталогах
# --------------------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_legacy_csproj_with_xml_namespace(tmp_path: Path) -> None:
    """Проекты не в формате SDK объявляют xmlns; сравнение по полному тегу их не найдёт."""
    path = _write(
        tmp_path / "Legacy/Legacy.csproj",
        """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <TargetFrameworkVersion>v4.8</TargetFrameworkVersion>
    <TargetFramework>net48</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Shared\\Shared.csproj" />
  </ItemGroup>
</Project>
""",
    )
    module = parse_csproj(path, tmp_path)
    assert module.target_frameworks == ["net48"]
    assert module.project_references == ["Shared"]


def test_sln_skips_solution_folders_and_other_project_types(tmp_path: Path) -> None:
    """Папки решения кладут в поле пути своё имя, а не файл."""
    _write(tmp_path / "src/Web/Web.csproj", "<Project />")
    path = _write(
        tmp_path / "App.sln",
        'Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "src", "src", "{AAA}"\n'
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Web",'
        ' "src\\Web\\Web.csproj", "{BBB}"\n'
        'Project("{E53339B2-1760-4266-BCC7-CA923CBCF16C}") = "docker-compose",'
        ' "docker-compose.dcproj", "{CCC}"\n',
    )
    assert parse_sln(path, tmp_path) == ["src/Web/Web.csproj"]


def test_nearest_props_file_wins(tmp_path: Path) -> None:
    """MSBuild берёт ближайший Directory.Build.props вверх по дереву."""
    _write(
        tmp_path / "Directory.Build.props",
        "<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>",
    )
    _write(
        tmp_path / "src/Directory.Build.props",
        "<Project><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>",
    )
    path = _write(tmp_path / "src/App/App.csproj", "<Project Sdk='Microsoft.NET.Sdk' />")
    assert parse_csproj(path, tmp_path).target_frameworks == ["net9.0"]


def test_own_target_framework_beats_inherited(tmp_path: Path) -> None:
    _write(
        tmp_path / "Directory.Build.props",
        "<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>",
    )
    path = _write(
        tmp_path / "App/App.csproj",
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
    )
    assert parse_csproj(path, tmp_path).target_frameworks == ["net8.0"]


def test_missing_target_framework_yields_empty_list(tmp_path: Path) -> None:
    """Отсутствие TFM — не ошибка: манифест просто беднее."""
    path = _write(tmp_path / "App/App.csproj", "<Project Sdk='Microsoft.NET.Sdk' />")
    assert parse_csproj(path, tmp_path).target_frameworks == []


def test_target_frameworks_are_sorted_and_deduplicated(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "App/App.csproj",
        "<Project><PropertyGroup>"
        "<TargetFrameworks>net9.0;net8.0;net8.0</TargetFrameworks>"
        "</PropertyGroup></Project>",
    )
    assert parse_csproj(path, tmp_path).target_frameworks == ["net8.0", "net9.0"]


def test_malformed_xml_raises(tmp_path: Path) -> None:
    """Битый проект должен падать громко, а не давать пустой модуль."""
    import xml.etree.ElementTree as ET

    path = _write(tmp_path / "App/App.csproj", "<Project><PropertyGroup></Project>")
    with pytest.raises(ET.ParseError):
        parse_csproj(path, tmp_path)
