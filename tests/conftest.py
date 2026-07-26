"""Общие фикстуры pytest."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SOLUTION = FIXTURES / "SampleSolution"


@pytest.fixture
def sample_solution() -> Path:
    """Корень тестового .NET-решения."""
    return SAMPLE_SOLUTION
