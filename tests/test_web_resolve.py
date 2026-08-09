"""Резолв TypeScript: импорты, алиасы, бочки, замыкание (F05).

Ловушка этой задачи в том, что её отказ выглядит как частичная работа: без
алиасов `tsconfig` не разрешается ни один импорт вида `@shared/…`, но `extends`
при этом сохраняется сырой строкой, и правила по базовому типу **срабатывают**.
Часть набора работает, часть нет, причины не видно.
"""

from pathlib import Path

import pytest

from docpipe.config import DocpipeConfig
from docpipe.discovery import discover
from docpipe.emit import exclude_globs
from docpipe.model import FileParseResult, Symbol
from docpipe.web.parser import parse_file, parse_source
from docpipe.web.resolve import (
    ResolveContext,
    build_context,
    build_symbol_index,
    compute_closures,
    declaration_fqn,
    load_tsconfig,
    module_fqn,
    parse_jsonc,
    resolve_name,
    resolve_specifier,
)


@pytest.fixture
def results(web_workspace: Path) -> list[FileParseResult]:
    found = discover(web_workspace, exclude_globs(DocpipeConfig()))
    return [parse_file(web_workspace / relative, web_workspace) for relative in found.ts_files]


@pytest.fixture
def context(web_workspace: Path, results: list[FileParseResult]) -> ResolveContext:
    return build_context(results, load_tsconfig(web_workspace, "tsconfig.json"))


@pytest.fixture
def index(results: list[FileParseResult], context: ResolveContext) -> dict[str, Symbol]:
    file_to_module = {result.path: "angular.json" for result in results}
    return compute_closures(build_symbol_index(results, file_to_module, context))


def _symbol(index: dict[str, Symbol], name: str) -> Symbol:
    found = [symbol for symbol in index.values() if symbol.name == name]
    assert len(found) == 1, f"{name}: найдено {len(found)}"
    return found[0]


# --------------------------------------------------------------------------------------
# tsconfig — это НЕ JSON
# --------------------------------------------------------------------------------------


def test_tsconfig_with_comments_and_trailing_commas_is_read() -> None:
    """И `ng new`, и `nx` генерируют `tsconfig.json` с комментариями.

    `json.loads` на таком файле бросает исключение, то есть без разбора JSONC
    не читается ни один боевой `tsconfig`, а вместе с ним не разрешается
    ни один импорт вида `@shared/…`.
    """
    text = """
    /* To learn more about this file see: https://angular.io/config/tsconfig. */
    {
      "compilerOptions": {
        // база
        "baseUrl": "./src",
        "paths": { "@shared/*": ["app/shared/*"], },
      },
    }
    """
    assert parse_jsonc(text)["compilerOptions"]["baseUrl"] == "./src"


def test_double_slash_inside_a_string_is_not_a_comment() -> None:
    """Регулярное выражение приняло бы `http://` за начало комментария."""
    assert parse_jsonc('{"url": "http://host/x", "a": 1}')["url"] == "http://host/x"


def test_aliases_are_inherited_through_extends(web_workspace: Path) -> None:
    config = load_tsconfig(web_workspace, "tsconfig.json")

    assert config.base_url == "src"
    assert config.paths["@shared/*"] == ["src/app/shared/*"]
    assert config.paths["@env/*"] == ["src/environments/*"]


def test_child_paths_replace_the_parent_table(tmp_path: Path) -> None:
    """Так работает TypeScript: `paths` замещаются целиком, а не дополняются.

    Слияние выглядит логичнее и даёт набор алиасов, которого у компилятора нет,
    — то есть резолв, расходящийся со сборкой.
    """
    (tmp_path / "tsconfig.base.json").write_text(
        '{"compilerOptions": {"baseUrl": "./src", "paths": {"@a/*": ["a/*"], "@b/*": ["b/*"]}}}',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"extends": "./tsconfig.base.json", "compilerOptions": {"paths": {"@a/*": ["other/*"]}}}',
        encoding="utf-8",
    )

    config = load_tsconfig(tmp_path, "tsconfig.json")
    assert config.paths == {"@a/*": ["src/other/*"]}


def test_missing_tsconfig_is_an_empty_table(tmp_path: Path) -> None:
    """Workspace без алиасов — обычное дело, а не отказ."""
    assert load_tsconfig(tmp_path, "tsconfig.json").paths == {}


# --------------------------------------------------------------------------------------
# Спецификатор модуля
# --------------------------------------------------------------------------------------


def test_relative_import_resolves(web_workspace: Path, context: ResolveContext) -> None:
    resolved = resolve_specifier(
        "./model", "src/app/cf-api/resources/model.service.ts", context.config, context.files
    )
    assert resolved.path == "src/app/cf-api/resources/model.ts"


def test_alias_with_wildcard_resolves(context: ResolveContext) -> None:
    resolved = resolve_specifier(
        "@shared/services/audit.service", "src/main.ts", context.config, context.files
    )
    assert resolved.path == "src/app/shared/services/audit.service.ts"


def test_alias_to_a_directory_resolves_through_index(context: ResolveContext) -> None:
    """`@shared` -> каталог -> `index.ts`: три звена подряд."""
    resolved = resolve_specifier("@shared", "src/main.ts", context.config, context.files)
    assert resolved.path == "src/app/shared/index.ts"


def test_bare_specifier_is_external(context: ResolveContext) -> None:
    """`@angular/core`, `rxjs`, `@ngxs/store` — нормальное состояние, а не потеря."""
    assert resolve_specifier("@angular/core", "src/main.ts", context.config, context.files) == (
        resolve_specifier("rxjs", "src/main.ts", context.config, context.files)
    )
    assert resolve_specifier("rxjs", "src/main.ts", context.config, context.files).external


def test_alias_into_node_modules_is_external(web_workspace: Path, context: ResolveContext) -> None:
    """Иначе вырезанное из обхода дерево вернётся в разбор через таблицу алиасов.

    Файл цели в фикстуре **существует**: без него проверка проходила бы просто
    потому, что резолвить нечего.
    """
    assert (web_workspace / "node_modules/exceljs/dist/exceljs.bare.d.ts").is_file()

    resolved = resolve_specifier("exceljs", "src/main.ts", context.config, context.files)
    assert resolved.external is True
    assert resolved.path is None


def test_longest_alias_prefix_wins(tmp_path: Path) -> None:
    """При `@shared` и `@shared/*` импорт `@shared/x` обязан пойти по второму.

    Выбор «первый попавшийся» дал бы резолв, зависящий от порядка ключей в файле.
    """
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@a/*": ["wrong/*"], "@a/b/*": ["right/*"]}}}',
        encoding="utf-8",
    )
    config = load_tsconfig(tmp_path, "tsconfig.json")
    files = frozenset({"wrong/b/x.ts", "right/x.ts"})

    assert resolve_specifier("@a/b/x", "src/main.ts", config, files).path == "right/x.ts"


# --------------------------------------------------------------------------------------
# Разрешение имени: бочка и алиас
# --------------------------------------------------------------------------------------


def test_name_resolves_through_alias_and_barrel(context: ResolveContext) -> None:
    """`@shared` -> `index.ts` -> `export * from './services/base-api.service'`.

    Имя импортируется не оттуда, где объявлено. Резолв, знающий только про
    относительные пути и алиасы, найдёт здесь ноль объявлений и потеряет
    цепочку наследования, ничего не сообщив.
    """
    found = resolve_name(
        "BaseApiService", "src/app/inner-debt/services/inner-debt.service.ts", context
    )
    assert found == "src/app/shared/services/base-api.service.ts"


def test_name_resolves_through_a_direct_alias(context: ResolveContext) -> None:
    found = resolve_name("AuditService", "src/app/routes/models/list/list.component.ts", context)
    assert found == "src/app/shared/services/audit.service.ts"


def test_external_name_does_not_resolve(context: ResolveContext) -> None:
    """`HttpInterceptor` из `@angular/common/http` не резолвится принципиально."""
    assert (
        resolve_name(
            "HttpInterceptor", "src/app/cf-api/interceptors/fix-url.interceptor.ts", context
        )
        is None
    )


def test_own_declaration_wins_over_an_import() -> None:
    """Порядок проверок повторяет правила TypeScript."""
    own = parse_source(b"import { A } from './other';\nexport class A {}\n", "src/a.ts")
    other = parse_source(b"export class A {}\n", "src/other.ts")
    ctx = build_context([own, other], load_tsconfig(Path("/nonexistent"), "tsconfig.json"))

    assert resolve_name("A", "src/a.ts", ctx) == "src/a.ts"


def test_renamed_import_keeps_both_names() -> None:
    """`import { Base as Api }`: здесь имя `Api`, в источнике — `Base`."""
    user = parse_source(b"import { Base as Api } from './base';\nclass X extends Api {}\n", "s.ts")
    base = parse_source(b"export class Base {}\n", "base.ts")
    ctx = build_context([user, base], load_tsconfig(Path("/nonexistent"), "tsconfig.json"))

    assert resolve_name("Api", "s.ts", ctx) == "base.ts"


def test_barrel_cycle_does_not_hang() -> None:
    """Две бочки, переэкспортирующие друг друга, — из битого кода получается."""
    first = parse_source(b"export * from './second';\n", "first.ts")
    second = parse_source(b"export * from './first';\n", "second.ts")
    third = parse_source(
        b"import { Missing } from './first';\nclass X extends Missing {}\n", "t.ts"
    )
    ctx = build_context([first, second, third], load_tsconfig(Path("/nonexistent"), "x.json"))

    assert resolve_name("Missing", "t.ts", ctx) is None


# --------------------------------------------------------------------------------------
# FQN: единица изоляции — файл, а не каталог
# --------------------------------------------------------------------------------------


def test_same_name_in_one_directory_gives_different_fqn() -> None:
    """FQN по каталогу склеил бы два `Helper` в один символ, и один исчез бы."""
    first = parse_source(b"export class Helper {}\n", "src/app/a.ts")
    second = parse_source(b"export class Helper {}\n", "src/app/b.ts")
    ctx = build_context([first, second], load_tsconfig(Path("/nonexistent"), "x.json"))

    index = build_symbol_index([first, second], {"src/app/a.ts": "m", "src/app/b.ts": "m"}, ctx)
    assert len(index) == 2
    assert {symbol.fqn for symbol in index.values()} == {
        "src/app/a.Helper",
        "src/app/b.Helper",
    }


def test_module_fqn_drops_both_typescript_suffixes() -> None:
    assert module_fqn("src/app/x.ts") == "src/app/x"
    assert module_fqn("src/app/x.d.ts") == "src/app/x"


def test_declaration_fqn_includes_the_containing_type() -> None:
    result = parse_source(b"export class Outer {}\n", "src/x.ts")
    assert declaration_fqn("src/x.ts", result.declarations[0]) == "src/x.Outer"


# --------------------------------------------------------------------------------------
# Базовые типы и замыкание
# --------------------------------------------------------------------------------------


def test_unresolved_base_type_stays_raw(index: dict[str, Symbol]) -> None:
    """Исчезнувший базовый тип превратил бы компонент в символ без причины.

    Правила классификации матчатся ровно по таким внешним именам.
    """
    interceptor = _symbol(index, "FixUrlInterceptor")
    assert interceptor.base_types == ["HttpInterceptor"]
    assert interceptor.base_types_raw == ["HttpInterceptor"]


def test_resolved_base_type_becomes_an_fqn(index: dict[str, Symbol]) -> None:
    service = _symbol(index, "InnerDebtService")
    assert service.base_types == ["src/app/shared/services/base-api.service.BaseApiService"]
    assert service.base_types_raw == ["BaseApiService"]


def test_closure_is_transitive_across_files(index: dict[str, Symbol]) -> None:
    """`InnerDebtService -> BaseApiService -> CoreService`, через алиас и бочку."""
    assert _symbol(index, "InnerDebtService").base_type_closure == [
        "src/app/shared/services/base-api.service.BaseApiService",
        "src/app/shared/services/base-api.service.CoreService",
    ]


def test_closure_does_not_loop_on_a_cycle() -> None:
    """`A extends B`, `B extends A` в TypeScript невозможны, из битого кода — да."""
    first = parse_source(b"import { B } from './b';\nexport class A extends B {}\n", "a.ts")
    second = parse_source(b"import { A } from './a';\nexport class B extends A {}\n", "b.ts")
    ctx = build_context([first, second], load_tsconfig(Path("/nonexistent"), "x.json"))

    index = compute_closures(build_symbol_index([first, second], {"a.ts": "m", "b.ts": "m"}, ctx))
    closures = {symbol.name: symbol.base_type_closure for symbol in index.values()}
    assert closures == {"A": ["b.B"], "B": ["a.A"]}


def test_index_is_deterministic(results: list[FileParseResult], context: ResolveContext) -> None:
    """Порядок входа не влияет на результат: обход ФС источником порядка не бывает."""
    file_to_module = {result.path: "angular.json" for result in results}
    straight = build_symbol_index(results, file_to_module, context)
    shuffled = build_symbol_index(list(reversed(results)), file_to_module, context)

    assert list(straight) == list(shuffled)
    assert straight == shuffled
