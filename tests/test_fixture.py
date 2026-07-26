"""Проверка целостности тестового .NET-решения (T01).

На состав фикстуры завязаны критерии приёмки почти всех последующих задач,
поэтому здесь фиксируется точный список файлов, а не только их количество.
"""

from pathlib import Path

# Все 12 файлов .cs, включая тот, что должен отсекаться исключениями.
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
    "src/Sample.Pricing.Api/obj/Debug/net8.0/Sample.Generated.g.cs",
]

EXPECTED_CSPROJ = [
    "src/Sample.Common/Sample.Common.csproj",
    "src/Sample.Pricing.Api/Sample.Pricing.Api.csproj",
]

EXPECTED_SLN = ["SampleSolution.sln"]


def _relative(root: Path, pattern: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob(pattern))


def test_cs_files(sample_solution: Path) -> None:
    assert _relative(sample_solution, "*.cs") == EXPECTED_CS
    assert len(EXPECTED_CS) == 12


def test_csproj_files(sample_solution: Path) -> None:
    assert _relative(sample_solution, "*.csproj") == EXPECTED_CSPROJ


def test_sln_files(sample_solution: Path) -> None:
    assert _relative(sample_solution, "*.sln") == EXPECTED_SLN


def test_generated_file_is_present_but_will_be_excluded(sample_solution: Path) -> None:
    """Файл в obj/ обязан существовать: на нём проверяются правила исключения.

    Если .gitignore начнёт отбрасывать obj/ или bin/, фикстура молча потеряет
    этот случай, и тесты discovery станут ничего не проверять.
    """
    generated = sample_solution / "src/Sample.Pricing.Api/obj/Debug/net8.0/Sample.Generated.g.cs"
    assert generated.is_file()
    assert "GeneratedService" in generated.read_text(encoding="utf-8")


def test_partial_class_lives_in_two_files(sample_solution: Path) -> None:
    """Ключевой случай для слияния partial в T10."""
    services = sample_solution / "src/Sample.Pricing.Api/Services"
    halves = sorted(p.name for p in services.glob("PricingService*.cs"))
    assert halves == ["PricingService.Calculations.cs", "PricingService.cs"]
    for half in halves:
        assert "partial class PricingService" in (services / half).read_text(encoding="utf-8")


def test_cross_module_inheritance_chain(sample_solution: Path) -> None:
    """PricingController -> BaseApiController (другой модуль) -> ControllerBase.

    Это главный тест архитектуры: на нём проверяется, что скоуп-режим (T18)
    не теряет наследование через границу модуля.
    """
    controller = sample_solution / "src/Sample.Pricing.Api/Controllers/PricingController.cs"
    base_controller = sample_solution / "src/Sample.Common/Web/BaseApiController.cs"
    assert "class PricingController : BaseApiController" in controller.read_text(encoding="utf-8")
    assert "using Sample.Common.Web;" in controller.read_text(encoding="utf-8")
    assert "class BaseApiController : ControllerBase" in base_controller.read_text(encoding="utf-8")
