"""Детерминизм шага `web` (F17).

Свойство проверяется не на фикстуре из двадцати семи файлов, а на дереве,
собранном генератором: на маленьком наборе случайный порядок обхода может
совпасть с сортированным, и тест позеленеет, ничего не проверив.

Замер на дереве из 1301 файла записан в журнал реализации; здесь масштаб
меньше намеренно — тест обязан оставаться быстрым.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docpipe.classify import load_ruleset
from docpipe.cli import app
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.web.link import build_report
from docpipe.web.tree import run as run_web

runner = CliRunner()
RULES = Path("rules/rules.yaml")

COMPONENT = """import {{ Component }} from '@angular/core';
import {{ HttpClient }} from '@angular/common/http';

@Component({{ selector: 'app-c{i}', standalone: true, templateUrl: './c{i}.component.html' }})
export class C{i}Component {{
  private readonly baseUrl: string = '/api/ml/area{i}';
  constructor(private http: HttpClient) {{}}
  load(id: string) {{ return this.http.get(`${{this.baseUrl}}/item/${{id}}`); }}
  list() {{ return this.http.get('api/ml/area{i}/list'); }}
}}
"""

SERVICE = """import {{ Injectable }} from '@angular/core';
import {{ HttpClient }} from '@angular/common/http';

@Injectable({{ providedIn: 'root' }})
export class S{i}Service {{
  constructor(private http: HttpClient) {{}}
  save(body: unknown) {{ return this.http.post('api/ml/svc{i}/save', body); }}
}}
"""


def _generate(root: Path, count: int) -> None:
    """Сгенерировать workspace из `count` компонентов и стольких же сервисов."""
    (root / "src/app").mkdir(parents=True)
    (root / "angular.json").write_text(
        json.dumps({"projects": {"bench": {"root": "", "sourceRoot": "src"}}}), encoding="utf-8"
    )
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"@angular/core": "17.3.12"}}), encoding="utf-8"
    )

    for index in range(count):
        directory = root / f"src/app/f{index % 8}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"c{index}.component.ts").write_text(COMPONENT.format(i=index), "utf-8")
        (directory / f"c{index}.component.html").write_text(f"<div>{index}</div>\n", "utf-8")
        (directory / f"s{index}.service.ts").write_text(SERVICE.format(i=index), "utf-8")

    lines = ["import { Routes } from '@angular/router';"]
    lines += [
        f"import {{ C{index}Component }} from './app/f{index % 8}/c{index}.component';"
        for index in range(count)
    ]
    lines.append("export const appRoutes: Routes = [")
    lines += [f"  {{ path: 'p{index}', component: C{index}Component }}," for index in range(count)]
    lines.append("];")
    (root / "src/app.routes.ts").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("generated")
    _generate(root, 120)
    return root


# Манифест, снятый до подмены обхода. Список, а не значение: фикстура модульная,
# и считать его повторно в каждом тесте значило бы разбирать дерево лишний раз.
STRAIGHT: list[Manifest] = []


@pytest.fixture
def shuffled_walk(generated: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Перевернуть порядок, который отдаёт `os.walk`.

    Подменяется сам обход, а не список после сортировки: второе проверило бы
    только сортировку, а не то, что от порядка не зависит ничего дальше.
    """
    if not STRAIGHT:
        STRAIGHT.append(_scan(generated))

    original = os.walk

    def reversed_walk(*args: Any, **kwargs: Any) -> Iterator[Any]:
        for dirpath, dirnames, filenames in original(*args, **kwargs):
            yield dirpath, list(reversed(dirnames)), list(reversed(filenames))

    monkeypatch.setattr(os, "walk", reversed_walk)
    yield


def _scan(root: Path) -> Manifest:
    return run_web(root, DocpipeConfig(), load_ruleset(RULES, "web")).manifest


# --------------------------------------------------------------------------------------
# Побайтовая воспроизводимость
# --------------------------------------------------------------------------------------


def test_two_scans_give_byte_identical_manifests(generated: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        result = runner.invoke(
            app, ["web", "scan", "--root", str(generated), "--out", str(out), "--no-cache"]
        )
        assert result.exit_code == 0, result.output

    assert first.read_bytes() == second.read_bytes()


def test_two_links_give_byte_identical_reports(generated: Path, tmp_path: Path) -> None:
    web = tmp_path / "web.json"
    backend = tmp_path / "net.json"
    runner.invoke(app, ["web", "scan", "--root", str(generated), "--out", str(web), "--no-cache"])
    runner.invoke(
        app, ["scan", "--root", "tests/fixtures/WildSolution", "--out", str(backend), "--no-cache"]
    )

    first, second = tmp_path / "l1.json", tmp_path / "l2.json"
    for out in (first, second):
        result = runner.invoke(
            app, ["web", "link", str(backend), str(web), "--out", str(out), "--format", "json"]
        )
        assert result.exit_code == 0, result.output

    assert first.read_bytes() == second.read_bytes()


def test_cached_run_equals_a_cold_one(generated: Path, tmp_path: Path) -> None:
    """Кэш обязан отдавать ровно то же, что и разбор: иначе он меняет вывод."""
    cold = run_web(
        generated, DocpipeConfig(), load_ruleset(RULES, "web"), cache_dir=tmp_path
    ).manifest
    warm = run_web(
        generated, DocpipeConfig(), load_ruleset(RULES, "web"), cache_dir=tmp_path
    ).manifest

    assert cold == warm
    assert warm == _scan(generated)


# --------------------------------------------------------------------------------------
# Порядок обхода файловой системы
# --------------------------------------------------------------------------------------


def test_listing_order_does_not_change_anything(generated: Path, shuffled_walk: None) -> None:
    """На фикстуре из двадцати семи файлов случайный порядок может совпасть
    с сортированным, и тест позеленеет, ничего не проверив. На генерированном
    дереве из трёхсот шестидесяти — не может.

    Сравнивается манифест целиком, а не список идентификаторов: сортировка
    узлов проверяла бы только сортировку узлов.
    """
    assert _scan(generated) == STRAIGHT[0]


def test_link_does_not_depend_on_node_order(generated: Path) -> None:
    web = _scan(generated)
    backend = Manifest(ruleset_version="x", parser=web.parser)

    straight = build_report(backend, web)
    reversed_nodes = build_report(
        backend, web.model_copy(update={"nodes": list(reversed(web.nodes))})
    )

    assert straight == reversed_nodes


# --------------------------------------------------------------------------------------
# Масштаб
# --------------------------------------------------------------------------------------


def test_every_generated_file_reaches_the_manifest(generated: Path) -> None:
    """Потерянный файл выглядит как «в модуле такого нет» — числа обязаны сойтись."""
    manifest = _scan(generated)

    assert len(manifest.nodes) == 241  # 120 компонентов + 120 сервисов + таблица роутов
    assert sum(len(node.web_calls) for node in manifest.nodes) == 360
    assert sum(len(node.routes) for node in manifest.nodes) == 120
