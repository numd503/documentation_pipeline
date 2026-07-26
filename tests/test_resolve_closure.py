"""Проверка замыкания наследования (T11)."""

from pathlib import Path

from docpipe.dotnet.parser import parse_source
from docpipe.dotnet.resolve import (
    base_type_arity,
    build_symbol_index,
    compute_closures,
    symbol_key,
)
from docpipe.model import Symbol
from tests.conftest import by_fqn, index_of


def _closed(sources: dict[str, bytes], module: str = "m/M.csproj") -> dict[str, Symbol]:
    results = [parse_source(text, path) for path, text in sources.items()]
    return compute_closures(build_symbol_index(results, dict.fromkeys(sources, module)))


# --------------------------------------------------------------------------------------
# Критерии приёмки на фикстуре
# --------------------------------------------------------------------------------------


def test_closure_crosses_module_boundary(sample_solution: Path) -> None:
    """Ключевой тест всей архитектуры.

    `PricingController` лежит в `Sample.Pricing.Api`, его база
    `BaseApiController` — в `Sample.Common`, а `ControllerBase` вообще внешний.
    Правило «контроллер — это то, что наследуется от `ControllerBase`» обязано
    сработать через две границы: модуля и индекса.
    """
    index = compute_closures(index_of(sample_solution))
    symbol = by_fqn(index)["Sample.Pricing.Api.Controllers.PricingController"]

    assert symbol.base_type_closure == ["ControllerBase", "Sample.Common.Web.BaseApiController"]


def test_closure_of_unresolved_base(sample_solution: Path) -> None:
    index = compute_closures(index_of(sample_solution))
    symbol = by_fqn(index)["Sample.Pricing.Api.Grid.RiskComputeService"]
    assert symbol.base_type_closure == ["IService"]


def test_symbol_without_bases_has_empty_closure(sample_solution: Path) -> None:
    index = compute_closures(index_of(sample_solution))
    assert by_fqn(index)["Sample.Pricing.Api.Models.PriceDto"].base_type_closure == []


# --------------------------------------------------------------------------------------
# Обход графа
# --------------------------------------------------------------------------------------


def test_deep_chain_is_fully_expanded() -> None:
    index = _closed(
        {
            "a.cs": b"namespace N;\npublic class A : B { }\n",
            "b.cs": b"namespace N;\npublic class B : C { }\n",
            "c.cs": b"namespace N;\npublic class C : D { }\n",
            "d.cs": b"namespace N;\npublic class D { }\n",
        }
    )
    assert by_fqn(index)["N.A"].base_type_closure == ["N.B", "N.C", "N.D"]


def test_diamond_is_deduplicated() -> None:
    index = _closed(
        {
            "a.cs": b"namespace N;\npublic class A : B, C { }\n",
            "b.cs": b"namespace N;\npublic class B : D { }\n",
            "c.cs": b"namespace N;\npublic class C : D { }\n",
            "d.cs": b"namespace N;\npublic class D { }\n",
        }
    )
    assert by_fqn(index)["N.A"].base_type_closure == ["N.B", "N.C", "N.D"]


def test_unresolved_name_is_kept_but_not_expanded() -> None:
    """Внешнее имя в замыкание попадает — по нему матчатся правила, — но раскрыть его нечем."""
    index = _closed(
        {
            "a.cs": b"namespace N;\npublic class A : B { }\n",
            "b.cs": b"namespace N;\npublic class B : ControllerBase { }\n",
        }
    )
    assert by_fqn(index)["N.A"].base_type_closure == ["ControllerBase", "N.B"]


def test_cycle_terminates() -> None:
    """`A : B`, `B : A` в C# невозможно, но получить это из битого кода можно."""
    index = _closed(
        {
            "a.cs": b"namespace N;\npublic class A : B { }\n",
            "b.cs": b"namespace N;\npublic class B : A { }\n",
        }
    )
    assert by_fqn(index)["N.A"].base_type_closure == ["N.B"]
    assert by_fqn(index)["N.B"].base_type_closure == ["N.A"]


def test_self_reference_terminates() -> None:
    index = _closed({"a.cs": b"namespace N;\npublic class A : A { }\n"})
    assert by_fqn(index)["N.A"].base_type_closure == []


def test_own_fqn_is_excluded_from_closure() -> None:
    """`IAccessor : IAccessor<T>` — разные типы с одним FQN.

    Без исключения собственного FQN тип попал бы в своё же замыкание,
    и правило по базовому типу начало бы матчить сам тип. Конструкция реальная:
    в ABP так устроен `Volo.Abp.AI.IChatClientAccessor`.
    """
    source = b"""namespace N;
public interface IAccessor : IAccessor<object> { }
public interface IAccessor<T> : IMarker { }
"""
    index = _closed({"a.cs": source})
    plain = next(s for s in index.values() if not s.type_parameters)

    assert plain.fqn == "N.IAccessor"
    assert plain.base_type_closure == ["IMarker"]


# --------------------------------------------------------------------------------------
# Выбор символа среди одинаковых FQN
# --------------------------------------------------------------------------------------


def test_arity_selects_the_right_base() -> None:
    """Один FQN, три арности — раскрывать нужно ту, что указана в базовом списке.

    На ABP 813 рёбер из 7400 ведут к группе символов с одним FQN и разной
    арностью; переход по одному FQN раскрыл бы произвольный из них.
    """
    source = b"""namespace N;
public interface IService<T> : IOneArg { }
public interface IService<T, K> : ITwoArgs { }
public class User : IService<int, string> { }
"""
    index = _closed({"a.cs": source})
    assert by_fqn(index)["N.User"].base_type_closure == ["ITwoArgs", "N.IService"]


def test_own_module_wins_over_another(wild_solution: Path) -> None:
    """При одинаковых FQN и арности побеждает символ своего модуля."""
    base = b"namespace Lib;\npublic class Base : Marker { }\n"
    results = [
        parse_source(base, "a/Base.cs"),
        parse_source(b"namespace Lib;\npublic class Base : OtherMarker { }\n", "b/Base.cs"),
        parse_source(b"namespace Lib;\npublic class User : Base { }\n", "b/User.cs"),
    ]
    index = compute_closures(
        build_symbol_index(
            results,
            {"a/Base.cs": "a/A.csproj", "b/Base.cs": "b/B.csproj", "b/User.cs": "b/B.csproj"},
        )
    )
    user = index[symbol_key("b/B.csproj", "Lib.User", 0)]
    assert user.base_type_closure == ["Lib.Base", "OtherMarker"]


# --------------------------------------------------------------------------------------
# base_type_arity и параллельность списков
# --------------------------------------------------------------------------------------


def test_base_type_arity() -> None:
    assert base_type_arity("Plain") == 0
    assert base_type_arity("List<int>") == 1
    assert base_type_arity("IService<T, K>") == 2
    assert base_type_arity("Dictionary<string, List<int>>") == 2
    # Базовый тип — последний сегмент: `C` с одним параметром, а не `B`.
    assert base_type_arity("A.B<X>.C<Y>") == 1


def test_base_types_are_parallel_to_raw(sample_solution: Path) -> None:
    """Соответствие raw -> resolved нужно, чтобы восстановить арность.

    Две независимо отсортированные коллекции его теряют.
    """
    symbol = by_fqn(index_of(sample_solution))["Sample.Pricing.Api.Providers.CurveProvider"]

    assert len(symbol.base_types) == len(symbol.base_types_raw)
    assert symbol.base_types_raw == ["IPricingProvider<string>"]
    assert symbol.base_types == ["Sample.Common.Abstractions.IPricingProvider"]


# --------------------------------------------------------------------------------------
# Общие свойства
# --------------------------------------------------------------------------------------


def test_closures_are_sorted_and_deterministic(sample_solution: Path) -> None:
    index = index_of(sample_solution)
    first = compute_closures(index)
    second = compute_closures(index)

    assert first == second
    for symbol in first.values():
        assert symbol.base_type_closure == sorted(symbol.base_type_closure)


def test_compute_closures_does_not_touch_other_fields(sample_solution: Path) -> None:
    index = index_of(sample_solution)
    closed = compute_closures(index)

    for key, symbol in index.items():
        assert closed[key].model_dump(exclude={"base_type_closure"}) == symbol.model_dump(
            exclude={"base_type_closure"}
        )
