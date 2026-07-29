"""Скелеты документов и подстановка (M04).

Самый важный тест здесь — `is_section_empty` по каждой секции каждого скелета.
Подсказка обычным текстом сделала бы каждый новый документ «уже написанным»,
и агент шага 3 обошёл бы всё дерево впустую.
"""

from pathlib import Path

import pytest
import yaml

from docpipe.materialize import is_section_empty, parse_document
from docpipe.materialize.template import (
    ALLOWED_KEYS,
    TemplateError,
    load_templates,
    substitute,
)

TEMPLATES = Path("templates")
EXAMPLES = TEMPLATES / "examples"
RULES = Path("rules/dotnet.yaml")


@pytest.fixture
def templates() -> dict[str, object]:
    return load_templates(TEMPLATES)  # type: ignore[return-value]


# --------------------------------------------------------------------------------------
# Комплект скелетов
# --------------------------------------------------------------------------------------


def test_skeletons_match_rule_templates(templates: dict[str, object]) -> None:
    """Набор правил и комплект шаблонов не должны разъехаться.

    Тест читает YAML и сравнивает множества: добавить вид сущности — значит
    дописать правило И завести скелет, иначе прогон потеряет документы молча.
    """
    declared = {rule["template"] for rule in yaml.safe_load(RULES.read_text("utf-8"))["rules"]}

    assert set(templates) == declared
    assert len(templates) == 7


@pytest.mark.parametrize(
    ("name", "sections"),
    [
        ("controller", ["purpose", "api", "behaviour", "collaboration", "notes"]),
        ("service", ["purpose", "responsibilities", "behaviour", "collaboration", "notes"]),
        ("provider", ["purpose", "data_source", "contract", "failure_modes", "notes"]),
        ("workflow", ["purpose", "steps", "triggers", "compensation", "notes"]),
        ("ignite-service", ["purpose", "deployment", "cluster_contract", "failure_modes", "notes"]),
        ("ignite-compute", ["purpose", "job_contract", "data_affinity", "failure_modes", "notes"]),
        ("repository", ["purpose", "storage", "contract", "transactions", "notes"]),
    ],
)
def test_sections(templates: dict[str, object], name: str, sections: list[str]) -> None:
    assert templates[name].sections == sections  # type: ignore[attr-defined]


def test_every_skeleton_has_notes(templates: dict[str, object]) -> None:
    """При реклассификации должна остаться хотя бы одна секция-приёмник."""
    assert all("notes" in tpl.sections for tpl in templates.values())  # type: ignore[attr-defined]


def test_generated_block_is_present_and_empty(templates: dict[str, object]) -> None:
    """Скелет объявляет место блока, а не содержимое: положенный туда текст
    затрётся при первом же прогоне, и человек решит, что инструмент сломан."""
    for tpl in templates.values():
        parsed = parse_document(tpl.text)  # type: ignore[attr-defined]

        assert parsed.generated is not None
        assert not parsed.generated.body.strip()


def test_every_section_of_every_skeleton_is_empty(templates: dict[str, object]) -> None:
    """Ошибка здесь заставит агента счесть всё дерево написанным."""
    for tpl in templates.values():
        parsed = parse_document(tpl.text)  # type: ignore[attr-defined]
        for name in parsed.section_names:
            section = parsed.section(name)

            assert section is not None
            assert is_section_empty(section.body), f"{tpl.name}: {name}"  # type: ignore[attr-defined]


def test_skeletons_have_no_front_matter(templates: dict[str, object]) -> None:
    for tpl in templates.values():
        assert parse_document(tpl.text).front_matter is None  # type: ignore[attr-defined]


def test_readme_is_not_a_template(templates: dict[str, object]) -> None:
    assert "README" not in templates


# --------------------------------------------------------------------------------------
# Отказы при загрузке
# --------------------------------------------------------------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "controller.md").write_text(text, encoding="utf-8")
    return tmp_path


GOOD = (
    "# {{ title }}\n\n"
    "<!-- docpipe:generated:start -->\n"
    "<!-- docpipe:generated:end -->\n\n"
    "<!-- docpipe:section:start notes -->\n"
    "<!-- подсказка -->\n"
    "<!-- docpipe:section:end notes -->\n"
)


def test_valid_minimal_skeleton(tmp_path: Path) -> None:
    loaded = load_templates(_write(tmp_path, GOOD))

    assert loaded["controller"].sections == ["notes"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (GOOD.replace("<!-- docpipe:generated:start -->\n", ""), "generated:end"),
        (
            GOOD.replace(
                "<!-- docpipe:generated:start -->\n",
                "<!-- docpipe:generated:start -->\nсобрано заранее\n",
            ),
            "обязан быть пустым",
        ),
        (GOOD.replace("{{ title }}", "{{ tema }}"), "неизвестные ключи подстановки"),
        (GOOD.replace("notes", "purpose"), "нет секции `notes`"),
        (
            GOOD.replace("<!-- подсказка -->", "Обычный текст вместо комментария."),
            "не пуста",
        ),
        ("---\nkey: value\n---\n" + GOOD, "front matter"),
        (GOOD.replace("notes", "Notes"), "маркер секции не распознан"),
        (GOOD.replace("notes", "my-notes"), "маркер секции не распознан"),
        # Испорчен только открывающий маркер: ошибку даёт уже разбор документа,
        # и его сообщение точнее — оно указывает на конкретную строку.
        (
            GOOD.replace(
                "<!-- docpipe:section:start notes -->",
                "текст <!-- docpipe:section:start notes -->",
            ),
            "без открытия",
        ),
    ],
)
def test_rejects(tmp_path: Path, text: str, expected: str) -> None:
    with pytest.raises(TemplateError, match=expected):
        load_templates(_write(tmp_path, text))


def test_unknown_key_error_lists_allowed(tmp_path: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        load_templates(_write(tmp_path, GOOD.replace("{{ title }}", "{{ tema }}")))

    assert "tema" in str(exc.value)
    assert "doc_path" in str(exc.value)


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="не найден"):
        load_templates(tmp_path / "нет")


def test_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="ни одного шаблона"):
        load_templates(tmp_path)


# --------------------------------------------------------------------------------------
# Подстановка
# --------------------------------------------------------------------------------------


def test_substitute() -> None:
    assert substitute("# {{ title }} ({{kind}})", {"title": "Foo", "kind": "service"}) == (
        "# Foo (service)"
    )


@pytest.mark.parametrize(
    "text",
    [
        "api/v1/Pricing/{id:guid}",
        '{"a": 1}',
        "{ }",
        "format {0} style",
    ],
)
def test_substitute_leaves_single_braces_alone(text: str) -> None:
    """Не `str.format` и не `string.Template`: одинарные `{}` законно
    встречаются в маршрутах и в примерах JSON внутри подсказок."""
    assert substitute(text, {"title": "Foo"}) == text


def test_substitute_keeps_unknown_key_visible() -> None:
    """Тихая замена на пустую строку прятала бы ошибку вызывающего."""
    assert substitute("{{ team }}", {}) == "{{ team }}"


def test_allowed_keys_are_closed() -> None:
    assert "title" in ALLOWED_KEYS
    assert "signature_hash" not in ALLOWED_KEYS


# --------------------------------------------------------------------------------------
# Образцы
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["controller", "service", "provider", "workflow"])
def test_example_is_a_valid_filled_document(name: str) -> None:
    """Образец показывает агенту глубину и стиль: пустая секция в нём
    учила бы ровно не тому."""
    parsed = parse_document((EXAMPLES / f"{name}.md").read_text(encoding="utf-8"))

    assert parsed.front_matter is not None
    assert parsed.generated is not None
    assert parsed.section_names
    for section in parsed.section_names:
        body = parsed.section(section)

        assert body is not None
        assert not is_section_empty(body.body), f"{name}: {section}"


def test_examples_cover_the_four_templates_from_the_brief() -> None:
    """Постановка требует шаблоны сервиса, провайдера, воркфлоу и контроллера.

    Для Ignite образцов нет намеренно: специфика кластера на боевом репозитории
    не проверена, и придуманный образец врал бы.
    """
    assert {path.stem for path in EXAMPLES.glob("*.md")} == {
        "controller",
        "service",
        "provider",
        "workflow",
    }


@pytest.mark.parametrize("name", ["controller", "service", "provider", "workflow"])
def test_example_sections_match_its_skeleton(templates: dict[str, object], name: str) -> None:
    """Иначе образец учит структуре, которой скелет не создаёт."""
    parsed = parse_document((EXAMPLES / f"{name}.md").read_text(encoding="utf-8"))

    assert parsed.section_names == templates[name].sections  # type: ignore[attr-defined]
