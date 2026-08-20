"""Слияние манифестов для скоуп-режима.

Скоуп-прогон перестраивает только часть дерева, а остальное переносит
из предыдущего манифеста. Здесь описано, что значит «часть».

Честная граница: если тип **вне** скоупа поменял базовый класс, сущности внутри
скоупа могли переклассифицироваться, и скоуп-прогон этого не увидит. Поэтому
результат помечается `partial`, а источником истины в CI остаётся полный прогон.
"""

from docpipe.discovery import in_scope, normalize_scope
from docpipe.model import DiRegistration, DocNode, Manifest, Module, PartialInfo


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
    """
    normalized = normalize_scope(scope)

    nodes: dict[str, DocNode] = {
        node.id: node for node in previous.nodes if not node_in_scope(node, normalized)
    }
    nodes.update({node.id: node for node in partial.nodes})

    modules: dict[str, Module] = {module.id: module for module in previous.modules}
    modules.update({module.id: module for module in partial.modules})

    # Регистрации сливаются по тому же правилу, что и узлы: из старого
    # манифеста берутся только те, чей файл вне скоупа. Без этой строки
    # скоуп-прогон терял бы все регистрации за пределами скоупа — и связывание
    # вызовов через интерфейс (G04) на частичном манифесте молча теряло бы
    # половину рёбер.
    registrations: list[DiRegistration] = [
        registration
        for registration in previous.di_registrations
        if not in_scope(registration.file, normalized)
    ] + list(partial.di_registrations)

    return Manifest(
        ruleset_version=partial.ruleset_version,
        parser=partial.parser,
        partial=PartialInfo(scope=sorted(scope), outside_from_cache=True),
        modules=sorted(modules.values(), key=lambda module: module.id),
        nodes=sorted(nodes.values(), key=lambda node: node.id),
        di_registrations=sorted(
            registrations,
            key=lambda item: (item.file, item.line, item.service_type, item.impl_type or ""),
        ),
    )
