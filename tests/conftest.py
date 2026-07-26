"""Общие фикстуры и помощники pytest."""

from pathlib import Path

import pytest

from docpipe.discovery import discover
from docpipe.dotnet.parser import parse_file
from docpipe.dotnet.resolve import build_symbol_index
from docpipe.model import Symbol

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SOLUTION = FIXTURES / "SampleSolution"
WILD_SOLUTION = FIXTURES / "WildSolution"

EXCLUDE = ["**/obj/**", "**/bin/**", "**/*.g.cs"]


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


def module_of(relative: str, csproj_files: list[str]) -> str | None:
    """Ближайший вверх по дереву `.csproj` — так же, как это будет делать `tree.py`."""
    directories = {str(Path(c).parent): c for c in csproj_files}
    current = Path(relative).parent
    while True:
        if str(current) in directories:
            return directories[str(current)]
        if current == Path("."):
            return None
        current = current.parent


def index_of(root: Path) -> dict[str, Symbol]:
    """Индекс символов решения: обход, разбор, привязка файлов к проектам.

    Живёт здесь, а не в тесте одной задачи: индекс нужен всем задачам от T10
    и дальше, а импорт помощника из соседнего тестового модуля связывает их
    порядком исполнения.
    """
    found = discover(root, EXCLUDE)
    results = [parse_file(root / relative, root) for relative in found.cs_files]
    file_to_module = {
        relative: module
        for relative in found.cs_files
        if (module := module_of(relative, found.csproj_files))
    }
    return build_symbol_index(results, file_to_module)


def by_fqn(index: dict[str, Symbol]) -> dict[str, Symbol]:
    """Индекс, переложенный на FQN. Только для тестов, где FQN заведомо уникален."""
    return {symbol.fqn: symbol for symbol in index.values()}
