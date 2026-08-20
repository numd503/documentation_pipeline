"""Разбор SQL и его место в графе (G05c).

Разбор намеренно грубый: нужен ответ «какие имена участвуют», а не «что
этот запрос делает». Тесты держат три правила, каждое из которых иначе
ломается молча: временные таблицы узлами не становятся, динамика — отдельная
категория, а процедура, вызванная и не найденная, не исчезает.
"""

from pathlib import Path

from docpipe.graph.data import data_key, from_sql
from docpipe.graph.model import GraphNode
from docpipe.model import Manifest, ParserVersions, SqlObject, SqlUsage
from docpipe.sql import read


def manifest_of(
    objects: list[SqlObject] | None = None, usages: list[SqlUsage] | None = None
) -> Manifest:
    return Manifest(
        schema_version="2.0",
        ruleset_version="тест",
        parser=ParserVersions(tree_sitter="0.0"),
        sql_objects=objects or [],
        sql_usages=usages or [],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Чтение SQL
# ──────────────────────────────────────────────────────────────────────────────


def test_reads_and_writes_are_different_edges() -> None:
    """Складывать чтение и запись в одно значило бы отвечать «трогает» там,
    где спрашивают «портит ли»."""
    facts = read("SELECT * FROM dbo.A JOIN B ON B.Id = A.Id; UPDATE C SET X = 1;")
    assert facts.reads == ("b", "dbo.a")
    assert facts.writes == ("c",)


def test_temporary_tables_are_not_data_nodes() -> None:
    """Иначе граф зарастёт `#tmp`, а веерность потеряет смысл: у временной
    таблицы одной процедуры нет ничего общего с одноимённой в другой."""
    facts = read("INSERT INTO #daily SELECT * FROM dbo.REAL; SELECT * FROM #daily;")
    assert facts.reads == ("dbo.real",)
    assert facts.writes == ()
    assert facts.temporary == 2


def test_dynamic_sql_is_its_own_category() -> None:
    """Это НЕ «не разобрали литерал»: имени нет вовсе до момента исполнения."""
    assert read("EXEC (@sql);").dynamic is True
    assert read("EXEC sp_executesql @sql;").dynamic is True
    assert read("EXEC dbo.WriteLog;").dynamic is False


def test_procedure_declaration_is_recognised() -> None:
    facts = read("CREATE OR ALTER PROCEDURE dbo.CalcInterest AS BEGIN SELECT 1; END")
    assert facts.defines == (("dbo.calcinterest", "procedure"),)


def test_names_are_normalised_like_data_nodes() -> None:
    """Отдельная нормализация разошлась бы с узлами данных, и процедура,
    названная в C# и объявленная в `.sql`, дала бы два узла вместо одного."""
    assert read("SELECT * FROM [dbo].[CONTRACTS]").reads == ("dbo.contracts",)


def test_comments_do_not_contribute_names() -> None:
    facts = read("-- FROM dbo.GHOST\n/* JOIN dbo.PHANTOM */\nSELECT * FROM dbo.REAL")
    assert facts.reads == ("dbo.real",)


# ──────────────────────────────────────────────────────────────────────────────
# Место в графе
# ──────────────────────────────────────────────────────────────────────────────


def procedure(name: str = "dbo.calc", **kwargs) -> SqlObject:
    return SqlObject(name=name, kind="procedure", file="db/calc.sql", line=1, **kwargs)


def test_procedure_body_gives_edges_to_tables() -> None:
    """Без тела процедуры цепочка обрывается на «позвали процедуру X»,
    и всё, что за ней, невидимо."""
    nodes, edges, report = from_sql(
        manifest_of([procedure(reads=["dbo.contracts"], writes=["dbo.audit"])]), ()
    )
    kinds = {(edge.kind, edge.target) for edge in edges}
    assert ("reads", data_key("dbo.contracts")) in kinds
    assert ("writes", data_key("dbo.audit")) in kinds
    assert report["процедур с телом"] == 1


def test_procedure_calling_a_procedure_does_not_stop_at_the_first_level() -> None:
    _, edges, _ = from_sql(manifest_of([procedure(calls=["dbo.writeaudit"])]), ())
    assert [(edge.kind, edge.target) for edge in edges] == [("calls", data_key("dbo.writeaudit"))]


def test_procedure_without_a_body_is_kept_and_counted() -> None:
    """На репозитории, где процедуры живут только в базе, таких будет сто
    процентов — и это нормальный исход, а не пробел разбора."""
    _, _, report = from_sql(manifest_of([procedure(calls=["dbo.ghost"])]), ())
    assert report["процедур вызвано, тело не найдено"] == 1


def test_sql_from_code_starts_at_the_member() -> None:
    member = GraphNode(
        key="src/Repo.cs#Repo.Load",
        kind="member",
        name="Load",
        owner="Repo",
        file="src/Repo.cs",
    )
    usage = SqlUsage(
        member="Load",
        module="src/App.csproj",
        file="src/Repo.cs",
        line=10,
        reads=["dbo.contracts"],
    )
    _, edges, _ = from_sql(manifest_of(usages=[usage]), (member,))
    assert [(edge.kind, edge.source, edge.target) for edge in edges] == [
        ("reads", "src/Repo.cs#Repo.Load", data_key("dbo.contracts"))
    ]


def test_dynamic_sql_is_counted_separately_in_the_report() -> None:
    _, _, report = from_sql(manifest_of([procedure(dynamic=True)]), ())
    assert report["динамический SQL в процедуре"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Фикстура
# ──────────────────────────────────────────────────────────────────────────────


def test_fixture_has_a_procedure_and_a_dynamic_one() -> None:
    """Фикстура воспроизводит обе конструкции: разбираемую процедуру
    и динамический SQL, который не разрешается ничем."""
    files = sorted(Path("tests/fixtures/WildSolution/db").glob("*.sql"))
    assert [path.name for path in files] == ["BuildReport.sql", "CalcInterest.sql"]
    calc = read(files[1].read_text(encoding="utf-8"))
    assert calc.defines == (("dbo.calcinterest", "procedure"),)
    assert "dbo.contract_schedule" in calc.reads
    assert calc.writes == ("dbo.contracts",)
    assert calc.calls == ("dbo.writeaudit",)
    assert calc.temporary > 0
    assert read(files[0].read_text(encoding="utf-8")).dynamic is True
