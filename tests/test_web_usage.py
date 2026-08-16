"""Обращения к членам зависимостей — рёбра графа вызовов (P02).

Различие, ради которого задача существует: «страница внедрила `ModelService`»
против «страница зовёт `ModelService.byId`». На боевом модуле первое давало
`ForecastComponent` 51 эндпоинт, а 43 эндпоинта из 61 приезжали ровно
на четыре страницы разом — потому что список собирался по внедрению.

Разрешается только однозначное. Ложное ребро втянет в страницу чужой сервис
и породит неверный раздел документа; пропуск всего лишь оставит раздел короче,
и об этом скажет счётчик.
"""

from pathlib import Path

import pytest

from docpipe.classify import load_ruleset
from docpipe.config import DocpipeConfig
from docpipe.model import Manifest
from docpipe.web.parser import parse_tree
from docpipe.web.tree import WebScanResult
from docpipe.web.tree import run as run_web
from docpipe.web.usage import constructor_bindings, extract_usages, injected_fields

RULES = Path("rules/rules.yaml")


@pytest.fixture
def scanned(web_workspace: Path) -> WebScanResult:
    return run_web(web_workspace, DocpipeConfig(), load_ruleset(RULES, "web"))


@pytest.fixture
def manifest(scanned: WebScanResult) -> Manifest:
    return scanned.manifest


def _edges(manifest: Manifest, title: str) -> set[tuple[str, str, str]]:
    node = next(node for node in manifest.nodes if node.title == title)
    return {(usage.target.rpartition(".")[2], usage.member, usage.via) for usage in node.uses}


def _usages(source: str) -> list[tuple[str, str]]:
    tree = parse_tree(source.encode("utf-8"))
    return [(item.receiver, item.method) for item in extract_usages(tree.root_node, "a.ts")]


# --------------------------------------------------------------------------------------
# Что собралось на фикстуре
# --------------------------------------------------------------------------------------


def test_component_edges_name_the_method_not_just_the_service(manifest: Manifest) -> None:
    assert _edges(manifest, "DetailComponent") == {
        ("ModelService", "byId", "reload"),
        ("ModelService", "forUpdate", "refresh"),
    }


def test_injected_by_function_gives_an_edge_too(manifest: Manifest) -> None:
    """`private readonly audit = inject(AuditService)` — тип это аргумент."""
    assert ("AuditService", "log", "track") in _edges(manifest, "QuizComponent")


def test_edge_carries_the_member_it_was_called_from(manifest: Manifest) -> None:
    """`via` связывает цепочку: обращение из метода стейта под `@Action`."""
    assert _edges(manifest, "DebtState") == {
        ("InnerDebtService", "byClient", "load"),
        ("InnerDebtService", "insert", "save"),
    }


def test_decoy_receivers_give_no_edges(manifest: Manifest) -> None:
    """`Map.get` и `FormGroup.get` — те же приманки, что и в счётчике вызовов.

    Ребра у них нет не потому, что имя метода в чёрном списке, а потому,
    что получатель не разрешается в узел документации.
    """
    assert not any(usage.member == "get" for usage in _node_uses(manifest, "ListComponent"))


def _node_uses(manifest: Manifest, title: str) -> list:
    return next(node for node in manifest.nodes if node.title == title).uses


def test_counters_name_all_three_outcomes(scanned: WebScanResult) -> None:
    """Ребро, внешний получатель и неразрешённый — три разных состояния.

    Одно число «рёбер 9» не сказало бы, сколько графа потеряно; `HttpClient`
    и `Store` при этом не пробел разбора, а типы без узла документации.
    """
    stats = scanned.meta.stats

    assert stats["usages"] == 10
    assert stats["usages_external"] > 0
    assert stats["usages_unresolved"] > 0


# --------------------------------------------------------------------------------------
# Формы записи обращения
# --------------------------------------------------------------------------------------


def test_chain_and_optional_access_give_one_edge(manifest: Manifest) -> None:
    """`this.models?.forUpdate(x).subscribe()` — одно ребро, а не два и не ноль."""
    edges = _edges(manifest, "DetailComponent")

    assert ("ModelService", "forUpdate", "refresh") in edges
    assert not any(member == "subscribe" for _, member, _ in edges)


def test_receiver_forms() -> None:
    assert _usages("class A { m() { this.svc.load(); } }") == [("svc", "load")]
    assert _usages("function f(svc: S) { svc.load(); }") == [("svc", "load")]
    # Цепочка через чужое поле: тип `inner` известен только после разбора
    # типа `outer`, которого здесь нет.
    assert _usages("class A { m() { this.outer.inner.load(); } }") == []
    # Собственный метод — не ребро между узлами.
    assert _usages("class A { m() { this.other(); } }") == []


def test_injected_fields_carry_their_line() -> None:
    """Строка нужна, чтобы отнести поле к своему классу.

    Два сервиса в одном файле законно объявляют одноимённое поле с разными
    типами, и таблица на файл подставила бы чужой — молча и правдоподобно.
    """
    tree = parse_tree(
        b"class A { private a = inject(First); }\nclass B { private a = inject(Second); }\n"
    )

    assert injected_fields(tree.root_node) == [(1, "a", "First"), (2, "a", "Second")]


# --------------------------------------------------------------------------------------
# Разбор сигнатуры конструктора
# --------------------------------------------------------------------------------------


def test_bindings_keep_names_and_types() -> None:
    assert constructor_bindings("constructor(private http: HttpClient, route: ActivatedRoute)") == [
        ("http", "HttpClient"),
        ("route", "ActivatedRoute"),
    ]


def test_generic_is_cut_to_its_head() -> None:
    """`Store<AppState>` — зависимость от `Store`, а не от его параметра."""
    assert constructor_bindings("constructor(private store: Store<AppState>)") == [
        ("store", "Store")
    ]


def test_decorated_parameter_keeps_its_name() -> None:
    """Имя параметра — последнее слово до двоеточия, а не первое."""
    assert constructor_bindings("constructor(@Inject(TOKEN) private readonly api: ApiService)") == [
        ("api", "ApiService")
    ]


def test_empty_constructor_binds_nothing() -> None:
    assert constructor_bindings("constructor()") == []


def test_colon_inside_a_decorator_argument_does_not_become_a_type() -> None:
    """`@Inject('http://token')` — двоеточие внутри строки, а не разделитель.

    Наивный `partition(":")` дал бы тип `//token…`: выдуманную зависимость
    с правдоподобным именем, которую потом ищут в индексе и не находят.
    """
    assert constructor_bindings(
        "constructor(@Inject('http://token') private readonly api: ApiService)"
    ) == [("api", "ApiService")]


def test_colon_inside_an_object_argument_does_not_become_a_type() -> None:
    assert constructor_bindings("constructor(@SetApiUrl({ url: 'api/ml' }) private http: Api)") == [
        ("http", "Api")
    ]
