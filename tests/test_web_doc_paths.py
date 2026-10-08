"""Коллизии `doc_path` у шага `web` (S24b).

У фронта одноимённые классы в разных каталогах одного модуля — обычное дело:
`users.service.ts` в `features/administration/` и в `shared/`, `app.component.ts`
в корне и в `features/apps/pages/` (на squidex 21 такая пара, на abp 13).
Формула пути — (модуль, вид, slug), и такие узлы спорят за один файл; план
шага 2 фронта на них не собирался вовсе (`docs.unavailable`). Разводит их
та же функция, что у шага 1, — `tree.assign_doc_paths`.

Workspace собирается в `tmp_path`: в фикстурах коллизий нет, и золотые
файлы не должны меняться.
"""

import json
import random
from pathlib import Path
from typing import Final

import pytest

from docpipe import tree
from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig, WebConfig
from docpipe.hashing import stable_hash, stable_json_dumps
from docpipe.model import DocNode, Manifest
from docpipe.setup.context import SetupContext
from docpipe.setup.status import build_status
from docpipe.step2 import prepare
from docpipe.tree import assign_doc_paths, doc_path_for
from docpipe.web import tree as web_tree

RULES: Final = Path("rules/rules.yaml")
TEMPLATES: Final = Path("templates")

SERVICE: Final = """import {{ Injectable }} from '@angular/core';
import {{ HttpClient }} from '@angular/common/http';

@Injectable({{ providedIn: 'root' }})
export class {name} {{
  constructor(private http: HttpClient) {{}}
  list() {{ return this.http.get('api/{route}'); }}
}}
"""

COMPONENT: Final = """import {{ Component }} from '@angular/core';

@Component({{ selector: 'app-{selector}', standalone: true, template: '<div></div>' }})
export class AppComponent {{}}
"""

# Два спора и один узел без спора: два `UsersService` и два `AppComponent`
# в разных каталогах одного модуля, `CommentsService` — один.
USERS: Final = ("src/app/admin/users.service.ts", "src/app/shared/users.service.ts")
APPS: Final = ("src/app/app.component.ts", "src/app/apps/pages/app.component.ts")
COMMENTS: Final = "src/app/shared/comments.service.ts"


def _workspace(root: Path) -> Path:
    (root / "src/app").mkdir(parents=True)
    (root / "angular.json").write_text(
        json.dumps({"projects": {"shop": {"root": "", "sourceRoot": "src"}}}), encoding="utf-8"
    )
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"@angular/core": "17.3.12"}}), encoding="utf-8"
    )
    files = {
        USERS[0]: SERVICE.format(name="UsersService", route="admin/users"),
        USERS[1]: SERVICE.format(name="UsersService", route="users"),
        COMMENTS: SERVICE.format(name="CommentsService", route="comments"),
        APPS[0]: COMPONENT.format(selector="root"),
        APPS[1]: COMPONENT.format(selector="app"),
    }
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def _settings(**web: str) -> DocpipeConfig:
    return DocpipeConfig(rules=str(RULES), web=WebConfig(rules=str(RULES), **web))


def _manifest(root: Path, settings: DocpipeConfig | None = None) -> Manifest:
    settings = settings or _settings()
    return web_tree.run(root, settings, load_ruleset(RULES, "web")).manifest


def _formula(node: DocNode, settings: DocpipeConfig) -> str:
    """Путь узла по формуле, без разведения: тот, что был до S24b."""
    return doc_path_for(
        node.module, node.kind, node.title, settings.doc_layout, settings.web_modules_root
    )


def _by_file(manifest: Manifest) -> dict[str, DocNode]:
    return {
        node.symbol.sources[0].path: node
        for node in manifest.nodes
        if node.symbol is not None and node.symbol.sources
    }


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _workspace(tmp_path_factory.mktemp("collisions") / "ws")


def test_workspace_has_the_collisions(workspace: Path) -> None:
    """Конструкция на месте: по формуле пары спорят за один путь, а `CommentsService` — нет."""
    settings = _settings()
    nodes = _by_file(_manifest(workspace))
    for first, second in (USERS, APPS):
        assert _formula(nodes[first], settings) == _formula(nodes[second], settings)
    assert len({_formula(node, settings) for node in nodes.values()}) == 3


def test_colliding_front_nodes_get_distinct_paths_by_hash_of_id(workspace: Path) -> None:
    """Критерий приёмки: разные пути — суффикс по хэшу `id`, тот же, что у шага 1."""
    settings = _settings()
    nodes = _by_file(_manifest(workspace))
    for pair in (USERS, APPS):
        paths = [nodes[file].doc_path for file in pair]
        assert len(set(paths)) == 2
        for file in pair:
            node = nodes[file]
            suffix = stable_hash(node.id).removeprefix("sha256:")[:8]
            assert node.doc_path == _formula(node, settings).removesuffix(".md") + f"-{suffix}.md"


def test_node_without_collision_keeps_its_path_byte_for_byte(workspace: Path) -> None:
    """Ловушка S24b: разведение трогает только споривших — остальные пути прежние."""
    settings = _settings()
    node = _by_file(_manifest(workspace))[COMMENTS]
    assert node.doc_path == _formula(node, settings)
    assert node.doc_path.endswith("/comments-service.md")


def test_resolution_is_deterministic(workspace: Path) -> None:
    """Два прогона — байт в байт; порядок входа функции не меняет ни одного пути."""
    first, second = _manifest(workspace), _manifest(workspace)
    assert stable_json_dumps(first.model_dump(mode="json")) == stable_json_dumps(
        second.model_dump(mode="json")
    )

    settings = _settings()
    unresolved = [
        node.model_copy(update={"doc_path": _formula(node, settings)}) for node in first.nodes
    ]
    expected = {node.id: node.doc_path for node in first.nodes}
    for seed in range(5):
        shuffled = list(unresolved)
        random.Random(seed).shuffle(shuffled)
        assert {node.id: node.doc_path for node in assign_doc_paths(shuffled)} == expected


def test_web_step_uses_the_function_of_step_one() -> None:
    """Одна функция на оба шага: своя копия правила разошлась бы на первой правке."""
    assert web_tree.assign_doc_paths is tree.assign_doc_paths


def test_front_doc_branch_is_resolved_too(tmp_path: Path) -> None:
    """Своя ветка фронта (`web.modules_dir`) — те же суффиксы под своим префиксом."""
    settings = _settings(modules_dir="front")
    nodes = _by_file(_manifest(_workspace(tmp_path / "ws"), settings))
    paths = [nodes[file].doc_path for file in USERS]
    assert len(set(paths)) == 2
    assert all(path.startswith("docs/front/") for path in paths)
    assert nodes[COMMENTS].doc_path == _formula(nodes[COMMENTS], settings)


def test_step2_plan_of_the_front_builds(workspace: Path) -> None:
    """Раньше: «на один путь претендуют узлы …» — и план шага 2 фронта не собирался."""
    inputs = prepare(_manifest(workspace), workspace, _settings(), None, templates_dir=TEMPLATES)
    assert inputs.plan.errors == []
    doc_paths = [document.doc_path for document in inputs.plan.documents]
    assert len(doc_paths) == len(set(doc_paths)) == 5


def test_setup_status_has_no_unavailable_plan(workspace: Path) -> None:
    """Критерий приёмки на синтетике: `docs.unavailable` от коллизий больше нет."""
    report = build_status(SetupContext(workspace, _settings(), use_cache=False))
    assert "docs.unavailable" not in {item.code for item in report.findings if item.count}
