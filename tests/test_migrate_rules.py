"""Перенос файлов правил в секционный формат (`tools/migrate_rules.py`).

Главное свойство переноса — **сохранность комментариев**. В настроенном наборе
комментарий объясняет, почему решение принято именно такое: круговой прогон
через `yaml.safe_load` + `yaml.dump` уничтожил бы ровно то, ради чего файл
и читают, оставив формально верный результат.
"""

import importlib.util
from pathlib import Path

import pytest

from docpipe.classify import load_ruleset

SCRIPT = Path("tools/migrate_rules.py")

FLAT = """# Заголовок набора: зачем он такой.
version: "1"
ruleset_version: "2026-01-01.1"

exclude:
  # Тесты не документируем: их читают ради резолва.
  require_public: true
  rules:
    - id: tests
      reason: "Тестовый проект"
      priority: 100
      when:
        path_glob: ["**/tests/**"]

rules:
  - id: controller
    kind: controller
    template: controller
    priority: 50
    when:
      attribute: ["ApiController"]
"""


@pytest.fixture(scope="module")
def script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("migrate_rules", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_comments_survive_the_migration(script, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "rules.yaml"
    source.write_text(FLAT, encoding="utf-8")
    out = tmp_path / "out.yaml"

    out.write_text(script.HEADER + "\n" + script.indent(FLAT, "dotnet"), encoding="utf-8")

    text = out.read_text(encoding="utf-8")
    assert "# Заголовок набора: зачем он такой." in text
    assert "# Тесты не документируем: их читают ради резолва." in text
    # Файл остаётся рабочим, а не просто похожим на правильный.
    ruleset = load_ruleset(out, "dotnet")
    assert ruleset.ruleset_version == "2026-01-01.1"
    assert ruleset.exclude.require_public is True
    assert [rule.id for rule in ruleset.rules] == ["controller"]


def test_version_is_hoisted_not_duplicated(script) -> None:  # type: ignore[no-untyped-def]
    """`version` — свойство файла, а не секции: две копии разошлись бы."""
    body = script.indent(FLAT, "dotnet")

    assert not [line for line in body.splitlines() if line.strip().startswith("version:")]
    assert script.HEADER.count('version: "1"') == 1


def test_blank_lines_do_not_grow_trailing_spaces(script) -> None:  # type: ignore[no-untyped-def]
    """Сдвинутая пустая строка дала бы хвостовые пробелы в каждом абзаце,
    и первый же линтер YAML в чужом CI об этом сообщил бы."""
    body = script.indent(FLAT, "dotnet")

    assert not [line for line in body.splitlines() if line != line.rstrip()]


def test_already_migrated_file_is_refused(script, tmp_path: Path) -> None:
    """Повторный запуск иначе завернул бы секции в секции."""
    assert script.already_sectioned(script.indent(FLAT, "dotnet")) == "dotnet"
    assert script.already_sectioned(FLAT) is None


def test_shipped_rules_have_both_sections() -> None:
    """Поставочный набор и эталонный — оба секционные и оба загружаются."""
    for path in (Path("rules/rules.yaml"), Path("deploy/cashflow-docspipe/rules.yaml")):
        for section in ("dotnet", "web"):
            assert load_ruleset(path, section).rules, f"{path}:{section} без правил"
