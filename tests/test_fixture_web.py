"""Целостность Angular-фикстуры `WebWorkspace` (F02).

Проверяется **наличие конструкций**, а не файлов: иначе «упрощение» фикстуры
оставит зелёными и бессмысленными все тесты, которые на ней стоят. Поэтому
каждая проверка ищет форму по всему корпусу исходников, а не в конкретном файле:
перенести конструкцию в соседний файл можно, удалить — нельзя.

Каждый пункт — воспроизведение факта из `docs/findings-cashflow-frontend.md`.
"""

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------------------
# Корпус: все исходники фикстуры одним словарём путь → текст
# ---------------------------------------------------------------------------------------


@pytest.fixture
def sources(web_workspace: Path) -> dict[str, str]:
    """Все `.ts` фикстуры, включая те, что вырезаются обходом.

    Файлы под `node_modules` тоже здесь: их отсутствие означало бы, что проверка
    исключения ничего не проверяет.
    """
    return {
        path.relative_to(web_workspace).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(web_workspace.rglob("*.ts"))
    }


def _files_matching(sources: dict[str, str], pattern: str) -> list[str]:
    """Файлы, в которых встретилась форма. Возвращает список ради сообщения об ошибке."""
    compiled = re.compile(pattern, re.MULTILINE)
    return [path for path, text in sources.items() if compiled.search(text)]


# ---------------------------------------------------------------------------------------
# 1–2. Декоратор: сосед объявления у экспортируемого класса, потомок у неэкспортируемого
# ---------------------------------------------------------------------------------------


def test_decorated_exported_class_exists(sources: dict[str, str]) -> None:
    assert _files_matching(sources, r"^\}\)\nexport class \w+")


def test_decorated_class_without_export_exists(sources: dict[str, str]) -> None:
    """Форма, на которой ломается реализация, перенесённая с .NET буквально.

    У экспортируемого класса декоратор лежит в `export_statement` рядом
    с `class_declaration`, у неэкспортируемого — внутри него. Сбор декораторов
    с одного уровня даёт ноль классифицированных компонентов при нуле ошибок.
    """
    assert _files_matching(sources, r"^\}\)\nclass \w+")


# ---------------------------------------------------------------------------------------
# 3. Маршруты: спред импортированного массива и обе ленивые формы
# ---------------------------------------------------------------------------------------


def test_routes_are_assembled_by_spread(sources: dict[str, str]) -> None:
    """В боевом модуле `loadChildren` — ноль, а дерево собрано спредами."""
    assert _files_matching(sources, r"children: \[\.\.\.\w+\]")


def test_routes_have_load_children_and_both_load_component_forms(
    sources: dict[str, str],
) -> None:
    assert _files_matching(sources, r"loadChildren: \(\) => import\(")
    assert _files_matching(sources, r"loadComponent: \(\) => import\(")
    # Короткая форма: уже импортированный класс, без import().
    assert _files_matching(sources, r"loadComponent: \(\) => (?!import)\w+")


def test_routes_have_parameter_wildcard_and_redirect(sources: dict[str, str]) -> None:
    assert _files_matching(sources, r"path: ':id'")
    assert _files_matching(sources, r"path: '\*\*'")
    assert _files_matching(sources, r"redirectTo:")


def test_routes_have_a_segment_built_by_expression(sources: dict[str, str]) -> None:
    """Ветка, до которой дойти можно, а путь собрать нельзя, — `route_unresolved`.

    Отсутствие узла неотличимо от «страницы нет», и покрытие посчиталось бы
    по заниженному знаменателю.
    """
    assert _files_matching(sources, r"path: (?!['\"])\w+,")


# ---------------------------------------------------------------------------------------
# 4–5. HTTP-вызовы: база в поле, константа модуля, приманки без получателя
# ---------------------------------------------------------------------------------------


def test_call_with_base_url_in_a_field(sources: dict[str, str]) -> None:
    """Подстановка в начале шаблона — база, а не сегмент пути."""
    assert _files_matching(sources, r"private readonly baseUrl: string = '")
    assert _files_matching(sources, r"`\$\{this\.baseUrl\}/")


def test_module_level_url_constant_and_calls_through_it(sources: dict[str, str]) -> None:
    """`export const auditUrl` и не меньше трёх вызовов через него."""
    assert _files_matching(sources, r"export const auditUrl = '")
    calls = sum(len(re.findall(r"\.\w+<[^>]*>\(auditUrl", text)) for text in sources.values())
    assert calls >= 3


def test_call_forms_cover_literal_template_and_concatenation(sources: dict[str, str]) -> None:
    assert _files_matching(sources, r"this\.http\.get<[^>]*>\('api/")
    assert _files_matching(sources, r"this\.http\.get<[^>]*>\(`api/")
    assert _files_matching(sources, r"'api/[^']*' \+ \w+")
    # Первый аргумент — параметр метода: восстановить нечем.
    assert _files_matching(sources, r"this\.http\.get\(url,")


def test_decoy_calls_without_an_http_receiver(sources: dict[str, str]) -> None:
    """`Map.get` и `FormGroup.get`: 327 против 79 настоящих на боевом модуле."""
    assert _files_matching(sources, r"this\.cache\.get\(")
    assert _files_matching(sources, r"this\.form\.get\('search'\)")


# ---------------------------------------------------------------------------------------
# 6. Обращение к реестру: различитель в теле и в query-строке
# ---------------------------------------------------------------------------------------


def test_registry_call_with_discriminator_in_body(sources: dict[str, str]) -> None:
    """Один маршрут на много смыслов: ключ (метод, маршрут) склеил бы их в один."""
    bodies = [
        match
        for text in sources.values()
        for match in re.findall(r"'api/items/query'[\s\S]{0,80}?listInnerName: '(\w+)'", text)
    ]
    assert len(set(bodies)) >= 2, bodies


def test_registry_call_with_discriminator_in_query(sources: dict[str, str]) -> None:
    """Тот же платформенный API, но различитель уехал в query-строку."""
    assert _files_matching(sources, r"'api/items\?listInnerName=\w+'")
    # И та же форма с подстановкой: маршрут известен, смысл — нет.
    assert _files_matching(sources, r"`api/items\?listInnerName=\$\{\w+\}`")


# ---------------------------------------------------------------------------------------
# 7. Функциональный интерцептор — символ без декоратора
# ---------------------------------------------------------------------------------------


def test_functional_interceptor_exists(sources: dict[str, str]) -> None:
    """В АС CF так объявлены интерцепторы и guard'ы: класса нет, декоратора нет."""
    assert _files_matching(sources, r"export const \w+: HttpInterceptorFn = ")


# ---------------------------------------------------------------------------------------
# 8. NGXS: цепочка целиком и дженерик у декоратора стейта
# ---------------------------------------------------------------------------------------


def test_ngxs_chain_is_complete(sources: dict[str, str]) -> None:
    """Компонент диспатчит экшен → стейт его обрабатывает → зовёт сервис → HTTP."""
    assert _files_matching(sources, r"static readonly type = '\[")
    assert _files_matching(sources, r"@Action\(\w+\)")
    assert _files_matching(sources, r"\.dispatch\(new \w+\(")


def test_state_decorator_is_written_with_a_generic(sources: dict[str, str]) -> None:
    """Поиск по подстроке `@State(` даёт ноль при существующем стейте, и молча."""
    assert _files_matching(sources, r"@State<\w+>\(\{")
    assert not _files_matching(sources, r"@State\(")


def test_class_carries_two_decorators(sources: dict[str, str]) -> None:
    """`@State` и `@Injectable` на одном классе: собрать обязательно оба."""
    assert _files_matching(sources, r"@State<\w+>\(\{[\s\S]*?\n\}\)\n@Injectable\(\)")


# ---------------------------------------------------------------------------------------
# 9. Компоненты: standalone с внешним шаблоном и с внутренним
# ---------------------------------------------------------------------------------------


def test_standalone_component_with_external_template(web_workspace: Path) -> None:
    """Шаблон входит в `impl_hash`, стили — нет, поэтому нужны оба файла."""
    assert _files_matching(
        {"": (web_workspace / "src/app/routes/models/list/list.component.ts").read_text("utf-8")},
        r"standalone: true",
    )
    assert (web_workspace / "src/app/routes/models/list/list.component.html").is_file()
    assert (web_workspace / "src/app/routes/models/list/list.component.scss").is_file()


def test_component_with_inline_template_exists(sources: dict[str, str]) -> None:
    """У такого компонента внешнего `.html` нет вовсе — `impl_hash` обязан собраться."""
    assert _files_matching(sources, r"template: '<")


# ---------------------------------------------------------------------------------------
# 10. Импорты: алиас, бочка, относительный путь, цель в node_modules
# ---------------------------------------------------------------------------------------


def test_import_forms_cover_alias_barrel_and_relative(sources: dict[str, str]) -> None:
    assert _files_matching(sources, r"^import \{[^}]*\} from '@shared';")
    assert _files_matching(sources, r"^import \{[^}]*\} from '@shared/services/")
    assert _files_matching(sources, r"^import \{[^}]*\} from '@env/environment';")
    assert _files_matching(sources, r"^import \{[^}]*\} from '\./model';")
    assert _files_matching(sources, r"^export \* from '\./services/")


def test_inheritance_chain_crosses_a_file_boundary(sources: dict[str, str]) -> None:
    """`InnerDebtService -> BaseApiService -> CoreService`: замыкание транзитивно."""
    assert _files_matching(sources, r"class BaseApiService extends CoreService")
    assert _files_matching(sources, r"class InnerDebtService extends BaseApiService")


def test_tsconfig_aliases_inherited_through_extends(web_workspace: Path) -> None:
    """Алиасы объявлены в базовом `tsconfig`, а читается всегда дочерний.

    Резолв, не идущий по `extends`, не разрешит **ни одного** импорта `@shared/…`,
    при этом `extends` в TypeScript-коде останется сырой строкой и правила
    по базовому типу сработают: часть набора работает, часть нет, без причины.
    """
    child = json.loads((web_workspace / "tsconfig.json").read_text(encoding="utf-8"))
    base = json.loads((web_workspace / "tsconfig.base.json").read_text(encoding="utf-8"))

    assert child["extends"] == "./tsconfig.base.json"
    assert "paths" not in child["compilerOptions"]

    paths = base["compilerOptions"]["paths"]
    assert {"@cf-api/*", "@shared", "@shared/*", "@env/*"} <= set(paths)
    # Алиас, ведущий в node_modules: цель обязана быть помечена внешней,
    # иначе вырезанный из обхода каталог вернётся туда через алиас.
    assert paths["exceljs"] == ["../node_modules/exceljs/dist/exceljs.bare"]
    assert (web_workspace / "node_modules/exceljs/dist/exceljs.bare.d.ts").is_file()


# ---------------------------------------------------------------------------------------
# 11. Окружение и прокси: выключенный конвейер и отсутствие pathRewrite
# ---------------------------------------------------------------------------------------


def test_environment_has_api_root_but_no_api_url(web_workspace: Path) -> None:
    """Условие интерцептора читает отсутствующее поле — ветка мертва в любой сборке."""
    text = (web_workspace / "src/environments/environment.ts").read_text(encoding="utf-8")
    assert "apiRoot:" in text
    assert "apiUrl:" not in text


def test_interceptor_reads_the_absent_field(sources: dict[str, str]) -> None:
    assert _files_matching(sources, r"if \(environment\?\.apiUrl\)")


def test_proxy_conf_has_no_path_rewrite(web_workspace: Path) -> None:
    """Пустое преобразование — проверенное значение, а не забытая настройка."""
    text = (web_workspace / "proxy.conf.js").read_text(encoding="utf-8")
    assert "context: ['/**']" in text
    assert "pathRewrite" not in text


def test_second_module_calls_with_an_application_prefix(sources: dict[str, str]) -> None:
    """Модуль без записи в `url_rewrite`: литерал с префиксом `/pm`."""
    assert _files_matching(sources, r"'/pm/api/")
