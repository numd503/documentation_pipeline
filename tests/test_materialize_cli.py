"""Запись на диск и команда `docpipe materialize` (M08).

Эталонное дерево `tests/golden/docs/**` сравнивается **байт в байт**: оно же
служит живой документацией формата. Обновлять его осознанно, а не по факту
падения теста.
"""

import ast
import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docpipe.cli import app
from docpipe.materialize.apply import DIRECTORY_LIMIT, _directory_section, directory_counts
from docpipe.materialize.template import DEFAULT_TEMPLATE

GOLDEN_MANIFEST = Path("tests/golden/doc-tree.json")
GOLDEN_DOCS = Path("tests/golden/docs")
CONTROLLER = "docs/modules/controllers/Sample.Pricing.Api/pricing-controller.md"
runner = CliRunner()


def _run(root: Path, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["materialize", str(GOLDEN_MANIFEST), "--root", str(root), *extra])


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.md"))
    }


# --------------------------------------------------------------------------------------
# Золотое дерево
# --------------------------------------------------------------------------------------


def test_creates_the_golden_tree(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.exit_code == 0
    assert _tree(tmp_path / "docs") == _tree(GOLDEN_DOCS)
    assert len(_tree(tmp_path / "docs")) == 6


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    """Повторный прогон не меняет ни байта и не трогает `mtime`.

    Запись одинакового содержимого — это всё равно тысячи строк в `git status`
    при включённом фильтре переводов строк.
    """
    _run(tmp_path)
    before = {path: path.stat().st_mtime_ns for path in sorted((tmp_path / "docs").rglob("*.md"))}
    snapshot = _tree(tmp_path / "docs")

    result = _run(tmp_path)

    assert result.exit_code == 0
    assert _tree(tmp_path / "docs") == snapshot
    assert {path: path.stat().st_mtime_ns for path in before} == before


def test_build_is_deterministic_under_shuffled_manifest(tmp_path: Path) -> None:
    """Тот же манифест с перевёрнутым порядком узлов даёт то же дерево."""
    import json

    payload = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    payload["nodes"] = list(reversed(payload["nodes"]))
    shuffled = tmp_path / "shuffled.json"
    shuffled.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    straight = tmp_path / "a"
    reversed_root = tmp_path / "b"
    _run(straight)
    runner.invoke(app, ["materialize", str(shuffled), "--root", str(reversed_root)])

    assert _tree(straight / "docs") == _tree(reversed_root / "docs")


# --------------------------------------------------------------------------------------
# Сохранность авторского текста
# --------------------------------------------------------------------------------------


def _write_section(root: Path, doc_path: str, name: str, text: str) -> None:
    path = root / doc_path
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"<!-- docpipe:section:end {name} -->",
            f"{text}\n<!-- docpipe:section:end {name} -->",
        ),
        encoding="utf-8",
    )


def test_authored_text_survives_everything(tmp_path: Path) -> None:
    """Текст агента переживает пересборку генерируемого блока, смену владельца,
    смену обоих хэшей и добавление секции шаблона."""
    import json

    _run(tmp_path)
    _write_section(tmp_path, CONTROLLER, "purpose", "Авторский текст.")

    payload = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        node["domain"] = "Другой домен"
        node["signature_hash"] = "sha256:changed"
        node["impl_hash"] = "sha256:changed"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = runner.invoke(app, ["materialize", str(changed), "--root", str(tmp_path)])
    text = (tmp_path / CONTROLLER).read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert "Авторский текст." in text
    assert "domain: Другой домен" in text
    assert "sha256:changed" in text


def test_materialize_never_deletes(tmp_path: Path) -> None:
    """Ни один сценарий не удаляет файл: узел исчез из манифеста, `--team`
    сузил множество. Число `.md` до не меньше числа после."""
    import json

    _run(tmp_path)
    before = len(_tree(tmp_path / "docs"))

    payload = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    payload["nodes"] = payload["nodes"][:2]
    shrunk = tmp_path / "shrunk.json"
    shrunk.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    runner.invoke(app, ["materialize", str(shrunk), "--root", str(tmp_path)])

    assert len(_tree(tmp_path / "docs")) >= before


def test_crlf_document_is_not_rewritten_forever(tmp_path: Path) -> None:
    """Документ с CRLF, отличающийся только переводами строк, остаётся
    без изменений: сравнение идёт по нормализованному тексту."""
    _run(tmp_path)
    path = tmp_path / CONTROLLER
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    before = path.stat().st_mtime_ns

    result = _run(tmp_path)

    assert result.exit_code == 0
    assert path.stat().st_mtime_ns == before
    assert b"\r\n" in path.read_bytes()


# --------------------------------------------------------------------------------------
# Отказы
# --------------------------------------------------------------------------------------


def _break(root: Path, doc_path: str, kind: str) -> None:
    path = root / doc_path
    text = path.read_text(encoding="utf-8")
    if kind == "yaml":
        text = text.replace("docpipe:", "docpipe:\n  - [битый", 1)
    elif kind == "front-matter":
        text = text.replace("---\n\n# ", "\n\n# ", 1)
    elif kind == "section":
        text = text.replace("<!-- docpipe:section:end notes -->", "")
    elif kind == "generated":
        text = text.replace("<!-- docpipe:generated:start -->\n", "", 1)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("kind", ["yaml", "front-matter", "section", "generated"])
def test_broken_document_is_not_touched(tmp_path: Path, kind: str) -> None:
    """Испорченный документ не изменяется ни на байт, код 1.

    Единственный экземпляр принятого состояния живёт в этом файле, поэтому
    всё, что мешает надёжно его прочитать, — отказ, а не самолечение.
    """
    _run(tmp_path)
    _break(tmp_path, CONTROLLER, kind)
    before = (tmp_path / CONTROLLER).read_bytes()

    result = _run(tmp_path)

    assert result.exit_code == 1
    assert (tmp_path / CONTROLLER).read_bytes() == before
    assert "pricing-controller.md" in result.stdout


def test_force_recreates_and_keeps_a_copy(tmp_path: Path) -> None:
    _run(tmp_path)
    _break(tmp_path, CONTROLLER, "section")
    before = (tmp_path / CONTROLLER).read_bytes()

    result = _run(tmp_path, "--force")
    backup = tmp_path / CONTROLLER.replace(".md", ".md.broken")

    assert result.exit_code == 0
    assert backup.read_bytes() == before
    assert (tmp_path / CONTROLLER).read_bytes() != before
    assert "docpipe:section:end notes" in (tmp_path / CONTROLLER).read_text(encoding="utf-8")


def test_read_only_file_does_not_stop_the_others(tmp_path: Path) -> None:
    """Прерывание на первой ошибке оставило бы дерево наполовину обновлённым."""
    import json

    _run(tmp_path)

    payload = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        node["domain"] = "Новый домен"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    blocked = (tmp_path / CONTROLLER).parent
    os.chmod(blocked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = runner.invoke(app, ["materialize", str(changed), "--root", str(tmp_path)])
    finally:
        os.chmod(blocked, stat.S_IRWXU)

    updated = [
        path
        for path in (tmp_path / "docs").rglob("*.md")
        if "Новый домен" in path.read_text(encoding="utf-8")
    ]

    assert result.exit_code == 1
    assert "Ошибки записи:" in result.stdout
    assert len(updated) == 5
    assert len(_tree(tmp_path / "docs")) == 6


# --------------------------------------------------------------------------------------
# Флаги
# --------------------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = _run(tmp_path, "--dry-run")

    assert result.exit_code == 0
    assert _tree(tmp_path) == {}
    assert "Что было бы сделано:" in result.stdout
    assert "создано:      6" in result.stdout


def _manifest_with_unknown_template(tmp_path: Path) -> Path:
    """Золотой манифест, в котором у одного узла вид сущности без своего скелета."""
    manifest = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    manifest["nodes"][0]["template"] = "нет-такого"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_unknown_template_does_not_cancel_the_run(tmp_path: Path) -> None:
    """Раньше один неизвестный `template` отменял прогон целиком: ни одного файла.

    На чужом репозитории это худший из ответов — первое же своё правило
    классификации приносит новый вид сущности.
    """
    root = tmp_path / "repo"
    result = runner.invoke(
        app,
        ["materialize", str(_manifest_with_unknown_template(tmp_path)), "--root", str(root)],
    )

    assert result.exit_code == 0
    assert len(_tree(root / "docs")) == 6
    assert "Своего скелета нет, применён `default`:" in result.stdout
    assert "нет-такого" in result.stdout


def test_document_built_from_the_default_stays_current(tmp_path: Path) -> None:
    """Самая дорогая из ловушек: разойдись разрешение шаблона в `plan` и в `build`,

    документ оказался бы `stale` навсегда, и агент шага 3 переписывал бы его
    на каждом прогоне. Проверяется прогоном, а не чтением кода.
    """
    root = tmp_path / "repo"
    manifest = _manifest_with_unknown_template(tmp_path)
    runner.invoke(app, ["materialize", str(manifest), "--root", str(root)])
    before = _tree(root / "docs")

    again = runner.invoke(app, ["materialize", str(manifest), "--root", str(root)])
    status = runner.invoke(app, ["docs", "status", str(manifest), "--root", str(root)])

    assert _tree(root / "docs") == before
    assert "без изменений:  6" in again.stdout
    assert "stale" not in status.stdout


def test_run_is_cancelled_when_there_is_nothing_to_fall_back_to(tmp_path: Path) -> None:
    """Без `default.md` прежнее поведение сохраняется: не записано ничего."""
    templates = tmp_path / "templates"
    templates.mkdir()
    for skeleton in Path("templates").glob("*.md"):
        if skeleton.stem != DEFAULT_TEMPLATE:
            (templates / skeleton.name).write_bytes(skeleton.read_bytes())

    root = tmp_path / "repo"
    result = runner.invoke(
        app,
        [
            "materialize",
            str(_manifest_with_unknown_template(tmp_path)),
            "--root",
            str(root),
            "--templates",
            str(templates),
        ],
    )

    assert result.exit_code == 1
    assert "нет шаблонов: нет-такого" in result.stdout
    assert not root.exists()


def test_dry_run_says_where_the_documents_would_land(tmp_path: Path) -> None:
    """Одна цифра «создано: 4820» не отвечает на первый вопрос настройщика — где.

    Каталог показывается настоящий, тот самый, в который потом лягут файлы,
    поэтому он и сверяется с деревом реального прогона, а не с константой.
    """
    planned = _run(tmp_path, "--dry-run")
    _run(tmp_path)

    assert "Где было бы создано:" in planned.stdout
    for path in _tree(tmp_path / "docs"):
        assert f"docs/{path.rpartition('/')[0]}" in planned.stdout


def test_directory_breakdown_is_ordered_by_size_then_name() -> None:
    """Порядок — явный ключ, а не порядок вставки: отчёт сравнивают между прогонами."""
    counts = directory_counts(["b/x.md", "a/y.md", "a/z.md", "c/q.md"])

    assert counts == [("a", 2), ("b", 1), ("c", 1)]


def test_large_directory_breakdown_is_cut_but_still_totals(tmp_path: Path) -> None:
    """На боевом репозитории полный список — тысячи строк, и он утопил бы отчёт.

    Остаток обязан оставаться посчитанным: обрезка, теряющая документы, врёт
    об объёме, а ради оценки объёма всё и печатается.
    """
    paths = [f"docs/m{index:03d}/doc.md" for index in range(DIRECTORY_LIMIT + 5)]

    text = "\n".join(_directory_section("Где было бы создано:", paths))

    assert text.count("\n  docs/") == DIRECTORY_LIMIT
    assert "и ещё каталогов: 5, документов в них: 5" in text


def test_unknown_team_is_a_user_error(tmp_path: Path) -> None:
    ownership = tmp_path / "ownership.yaml"
    ownership.write_text(
        'version: "1"\nteams: [{id: pricing, title: P}]\n'
        "rules:\n  - {id: p, team: pricing, priority: 10, when: {kind: [controller]}}\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "--ownership", str(ownership), "--team", "нетакой")

    assert result.exit_code == 2
    assert "известны: pricing" in result.stderr


def test_team_filter_writes_only_its_own(tmp_path: Path) -> None:
    ownership = tmp_path / "ownership.yaml"
    ownership.write_text(
        'version: "1"\nteams: [{id: pricing, title: P}]\n'
        "rules:\n  - {id: p, team: pricing, priority: 10, when: {kind: [controller]}}\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "--ownership", str(ownership), "--team", "pricing")

    assert result.exit_code == 0
    assert len(_tree(tmp_path / "docs")) == 2
    assert "team: pricing" in (tmp_path / CONTROLLER).read_text(encoding="utf-8")


def test_missing_template_directory_is_a_user_error(tmp_path: Path) -> None:
    result = _run(tmp_path, "--templates", str(tmp_path / "нет"))

    assert result.exit_code == 2


# --------------------------------------------------------------------------------------
# Граница пакета
# --------------------------------------------------------------------------------------


def _imports_of(package: str, forbidden: str) -> list[str]:
    """Кто в пакете импортирует запрещённое. Обходом AST, а не импортом:
    импорт проверил бы только то, что модуль загружается, а не то, что
    зависимости нет."""
    offenders: list[str] = []
    for path in sorted(Path(package).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(forbidden):
                offenders.append(f"{path.name}: from {node.module}")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith(forbidden)
                ]
    return offenders


def test_materialize_does_not_import_dotnet() -> None:
    """Граница нужна, чтобы шаг 2 не пришлось переписывать при появлении парсера
    Python или TypeScript. На ней же стоит бизнес-слой.
    """
    assert _imports_of("docpipe/materialize", "docpipe.dotnet") == []


def test_materialize_does_not_import_business() -> None:
    """Шаг 2 обязан оставаться самостоятельным: бизнес-слой необязателен,
    и обратный индекс приходит в `BuildContext` как данные, а не импортом.

    Стрелка тут та же, что во всей конструкции: техника знает о бизнесе ровно
    столько, сколько ей передали снаружи.
    """
    assert _imports_of("docpipe/materialize", "docpipe.business") == []


# --------------------------------------------------------------------------------------
# Документ, которого обход не увидел
# --------------------------------------------------------------------------------------


def test_bom_document_is_not_recreated(tmp_path: Path) -> None:
    """Документ, пересохранённый Блокнотом, остаётся своим.

    Отсев по первым байтам шёл до `utf-8-sig`: файл считался чужим, узел
    получал `missing`, а `missing` — это `create` поверх написанного.
    """
    _run(tmp_path)
    _write_section(tmp_path, CONTROLLER, "purpose", "Авторский текст.")
    path = tmp_path / CONTROLLER
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    result = _run(tmp_path)

    assert result.exit_code == 0
    assert "missing" not in result.stdout
    assert "Авторский текст." in path.read_text(encoding="utf-8")


def test_unrecognised_document_is_refused_not_overwritten(tmp_path: Path) -> None:
    """Файл на пути узла есть, но обход его не вернул — отказ, а не перезапись.

    Здесь у документа убран `docpipe.schema`; способов стать невидимым больше
    (симлинк, `docs_scan_exclude`, права), и проверка одна на все: файл на месте
    узла есть — значит, содержимое неизвестно и трогать его нельзя.
    """
    _run(tmp_path)
    _write_section(tmp_path, CONTROLLER, "purpose", "Авторский текст.")
    path = tmp_path / CONTROLLER
    path.write_text(
        path.read_text(encoding="utf-8").replace("  schema: materialize/1\n", ""),
        encoding="utf-8",
    )
    before = path.read_bytes()

    result = _run(tmp_path)

    assert result.exit_code == 1
    assert "Отказ, файл не тронут:" in result.stdout
    assert path.read_bytes() == before


def test_unrecognised_document_is_not_overwritten_even_with_force(tmp_path: Path) -> None:
    """`--force` пересоздаёт испорченный документ, сохранив копию, но здесь
    пересоздавать не из чего: что лежит в файле, инструмент не знает."""
    _run(tmp_path)
    path = tmp_path / CONTROLLER
    path.write_text("--- \nчужой файл на месте документа\n", encoding="utf-8")

    result = _run(tmp_path, "--force")

    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "--- \nчужой файл на месте документа\n"
    assert not list(tmp_path.rglob("*.md.broken"))


def test_docs_scan_exclude_over_the_docs_tree_stops_the_run(tmp_path: Path) -> None:
    """Шаблон обхода, накрывший само дерево документов, роняет прогон.

    Иначе `materialize` пишет туда, куда `docs status` не заходит: все документы
    навсегда `missing` и переписываются каждым прогоном, молча.
    """
    _run(tmp_path)
    config = tmp_path / "docpipe.yaml"
    config.write_text('docs_scan_exclude:\n  - "docs/modules/**"\n', encoding="utf-8")
    _write_section(tmp_path, CONTROLLER, "purpose", "Авторский текст.")
    snapshot = _tree(tmp_path / "docs")

    result = _run(tmp_path, "--config", str(config))

    assert result.exit_code == 1
    assert "docs_scan_exclude" in result.stdout
    assert _tree(tmp_path / "docs") == snapshot


def test_apply_refuses_to_create_over_an_existing_file(tmp_path: Path) -> None:
    """Структурный запрет в самой записи, независимо от того, кто собрал план.

    `create` означает «файла не было». Если он есть, картина дерева была
    неполной, и запись затёрла бы чужой текст без копии.
    """
    from docpipe.materialize.apply import apply_plan
    from docpipe.materialize.plan import MaterializePlan, PlannedDoc

    path = tmp_path / "docs" / "x.md"
    path.parent.mkdir(parents=True)
    path.write_text("старое", encoding="utf-8")
    plan = MaterializePlan(
        documents=[
            PlannedDoc(
                doc_path="docs/x.md",
                node_id="type:x",
                file_action="create",
                status="missing",
                agent_action="write",
                content="новое",
            )
        ]
    )

    result = apply_plan(plan, tmp_path)

    assert result.created == []
    assert result.errors and "уже существует" in result.errors[0]
    assert path.read_text(encoding="utf-8") == "старое"
