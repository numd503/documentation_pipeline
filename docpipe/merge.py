"""Слияние манифестов для скоуп-режима.

Скоуп-прогон перестраивает только часть дерева, а остальное переносит
из предыдущего манифеста. Здесь описано, что значит «часть».

Честная граница: если тип **вне** скоупа поменял базовый класс, сущности внутри
скоупа могли переклассифицироваться, и скоуп-прогон этого не увидит. Поэтому
результат помечается `partial`, а источником истины в CI остаётся полный прогон.
"""

from collections.abc import Callable
from typing import Any

from docpipe.discovery import in_scope, normalize_scope
from docpipe.model import (
    DiRegistration,
    DispatchDeclaration,
    DispatchSend,
    DocNode,
    Manifest,
    Module,
    PartialInfo,
    SqlObject,
    SqlUsage,
    TableLiteral,
)

# Порядок каждого списка фактов — один на весь проект: его держит и `emit`,
# и слияние ниже. Слияние пересортировывает объединение, и ключ, разошедшийся
# с `emit`, дал бы скоуп-прогону другой порядок, чем полному, — побайтовую
# разницу манифестов при одинаковом коде.


def registration_order(item: DiRegistration) -> tuple[str, int, str, str]:
    return (item.file, item.line, item.service_type, item.impl_type or "")


def handler_order(item: DispatchDeclaration) -> tuple[str, int, str, str]:
    return (item.file, item.line, item.handler_fqn, item.request_type)


def send_order(item: DispatchSend) -> tuple[str, int, str]:
    return (item.file, item.line, item.request_type)


def table_order(item: TableLiteral) -> tuple[str, int, str, str]:
    return (item.file, item.line, item.method, item.name)


def sql_usage_order(item: SqlUsage) -> tuple[str, int]:
    return (item.file, item.line)


def sql_object_order(item: SqlObject) -> tuple[str, str]:
    return (item.file, item.name)


def node_in_scope(node: DocNode, scope: list[tuple[str, ...]] | None) -> bool:
    """Попадает ли узел в скоуп.

    Достаточно **любого** источника: `partial class`, у которого одна половина
    в скоупе, а вторая вне, перестраивается целиком — иначе половина изменений
    потерялась бы.
    """
    if node.symbol is None:
        return False
    return any(in_scope(source.path, scope) for source in node.symbol.sources)


def merge_manifests(previous: Manifest, partial: Manifest, scope: list[str]) -> Manifest:
    """Собрать полный манифест из старого и перестроенной части.

    Узлы вне скоупа берутся из `previous` как есть, узлы в скоупе — только новые.
    Старый узел, попадающий в скоуп, отбрасывается **до** добавления новых:
    иначе удалённый или переименованный тип остался бы в дереве навсегда.
    Факты о коде для графа (регистрации, диспетчеризация, таблицы, SQL)
    сливаются по тому же правилу, только по файлу факта — см. `_merge_facts`.
    """
    normalized = normalize_scope(scope)

    nodes: dict[str, DocNode] = {
        node.id: node for node in previous.nodes if not node_in_scope(node, normalized)
    }
    nodes.update({node.id: node for node in partial.nodes})

    modules: dict[str, Module] = {module.id: module for module in previous.modules}
    modules.update({module.id: module for module in partial.modules})

    return Manifest(
        ruleset_version=partial.ruleset_version,
        parser=partial.parser,
        partial=PartialInfo(scope=sorted(scope), outside_from_cache=True),
        modules=sorted(modules.values(), key=lambda module: module.id),
        nodes=sorted(nodes.values(), key=lambda node: node.id),
        di_registrations=_merge_facts(
            previous.di_registrations, partial.di_registrations, normalized, registration_order
        ),
        dispatch_handlers=_merge_facts(
            previous.dispatch_handlers, partial.dispatch_handlers, normalized, handler_order
        ),
        dispatch_sends=_merge_facts(
            previous.dispatch_sends, partial.dispatch_sends, normalized, send_order
        ),
        table_literals=_merge_facts(
            previous.table_literals, partial.table_literals, normalized, table_order
        ),
        sql_usages=_merge_facts(
            previous.sql_usages, partial.sql_usages, normalized, sql_usage_order
        ),
        sql_objects=_merge_facts(
            previous.sql_objects, partial.sql_objects, normalized, sql_object_order
        ),
    )


def _merge_facts[
    Fact: (DiRegistration, DispatchDeclaration, DispatchSend, TableLiteral, SqlUsage, SqlObject)
](
    previous: list[Fact],
    partial: list[Fact],
    scope: list[tuple[str, ...]] | None,
    order: Callable[[Fact], Any],
) -> list[Fact]:
    """Слить список фактов о коде по тому же правилу, что и узлы.

    Из старого манифеста — факты, чей файл вне скоупа; из нового — только те,
    чей файл в скоупе. Отбирать нужно с **обеих** сторон. Новый манифест
    собран по всем разобранным файлам, а файлы вне скоупа приходят в него
    из кэша разбора: сложить его целиком со старым — значит удвоить каждый
    факт вне скоупа. Взять же его целиком вместо старого нельзя: при холодном
    кэше файлов вне скоупа в нём нет, а исходники SQL (`sql_objects`) обход
    вне скоупа не находит вовсе — их знает только старый манифест.

    До этого слияния скоуп-прогон оставлял от фактов одни регистрации, да ещё
    с дублями, — а сборка графа по такому манифесту молча теряла
    диспетчеризацию, таблицы и SQL.
    """
    kept = [item for item in previous if not in_scope(item.file, scope)]
    fresh = [item for item in partial if in_scope(item.file, scope)]
    return sorted(kept + fresh, key=order)
