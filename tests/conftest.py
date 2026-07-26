"""Общие фикстуры pytest."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SOLUTION = FIXTURES / "SampleSolution"
WILD_SOLUTION = FIXTURES / "WildSolution"


@pytest.fixture
def sample_solution() -> Path:
    """Основное тестовое решение: канонические случаи, выверенные числа."""
    return SAMPLE_SOLUTION


@pytest.fixture
def wild_solution() -> Path:
    """Решение с конструкциями, пойманными в реальных репозиториях.

    Отделено от `sample_solution` намеренно: критерии приёмки в плане завязаны
    на точные количества в SampleSolution, и расширение той фикстуры
    потребовало бы перенумеровать их все.
    """
    return WILD_SOLUTION
