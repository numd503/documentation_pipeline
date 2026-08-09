"""Общие фикстуры и помощники pytest."""

from pathlib import Path

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.discovery import discover
from docpipe.dotnet.csproj import parse_csproj, resolve_references
from docpipe.dotnet.endpoints import extract_endpoints
from docpipe.dotnet.parser import parse_file
from docpipe.dotnet.resolve import build_symbol_index, compute_closures
from docpipe.model import DocNode, Module, Symbol
from docpipe.tree import build_nodes

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SOLUTION = FIXTURES / "SampleSolution"
WILD_SOLUTION = FIXTURES / "WildSolution"
WEB_WORKSPACE = FIXTURES / "WebWorkspace"

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


@pytest.fixture
def web_workspace() -> Path:
    """Angular-workspace шага `web`.

    Отдельная фикстура, а не расширение .NET-решений: пересечения между
    языками нет ни в обходе, ни в разборе, а числа в критериях приёмки
    F02–F17 завязаны на её состав так же, как T04–T20 — на SampleSolution.
    """
    return WEB_WORKSPACE


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


def build_tree(
    root: Path,
    config: DocpipeConfig | None = None,
    rules: Path | None = None,
) -> tuple[list[Module], list[DocNode]]:
    """Полный проход по решению: обход, разбор, индекс, классификация, дерево.

    Живёт здесь, а не в тесте одной задачи: с T15 и дальше почти каждой проверке
    нужен готовый манифест, а не его половина.
    """
    found = discover(root, EXCLUDE)
    results = [parse_file(root / relative, root) for relative in found.cs_files]
    file_to_module = {
        relative: module
        for relative in found.cs_files
        if (module := module_of(relative, found.csproj_files))
    }

    index = compute_closures(build_symbol_index(results, file_to_module))
    modules = resolve_references([parse_csproj(root / c, root) for c in found.csproj_files])

    return build_nodes(
        index,
        modules,
        load_ruleset(rules or Path("rules/dotnet.yaml")),
        config or DocpipeConfig(),
        [registration for result in results for registration in result.di_registrations],
        {key: extract_endpoints(symbol) for key, symbol in index.items()},
    )
