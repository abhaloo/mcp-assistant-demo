"""Join-graph pathfinding and multiplication classification over bundle joins."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.business_query.definitions import JoinDefinition
from app.business_query.outcomes import PlanRefused, RecordRef, RowIdentity

Relationship = Literal["one_to_one", "one_to_many", "many_to_one"]
_MAX_JOIN_HOPS = 2


@dataclass(frozen=True, slots=True)
class ResolvedJoin:
    """One edge in the resolved join tree (Cube classification walk input)."""

    from_resource: str
    to_resource: str
    relationship: Relationship
    on_sql: str
    optional: bool = False


def is_multiplied(resource: str, join_tree: list[ResolvedJoin]) -> bool:
    """Cube ``findMultiplicationFactorFor`` — fresh visited-set per top-level call."""
    visited: set[str] = set()

    def walk(current: str) -> bool:
        if current in visited:
            return False
        visited.add(current)
        edges = [j for j in join_tree if current in (j.from_resource, j.to_resource)]
        for j in edges:
            other = j.to_resource if j.from_resource == current else j.from_resource
            on_one_side = (j.from_resource == current and j.relationship == "one_to_many") or (
                j.to_resource == current and j.relationship == "many_to_one"
            )
            if on_one_side and other not in visited:
                return True
            if other not in visited and walk(other):
                return True
        return False

    return walk(resource)


def anchor_resource(
    resources: tuple[str, ...],
    join_tree: list[ResolvedJoin],
    *,
    declared: str | None = None,
) -> str:
    """The one resource whose rows a statement counts or lists.

    Without a declaration it is the join tree's single leaf, so every join is a
    lookup toward a parent and anchor rows are never dropped or repeated.

    A declared anchor may instead be the parent of exactly one other projected
    resource. The statement then roots at the parent and LEFT JOINs the child:
    rows multiply by design, and the sealed answer states the anchor count and
    the row count separately. Two children still multiply each other and refuse.
    """
    if not join_tree:
        return resources[0]
    leaves = [name for name in resources if not is_multiplied(name, join_tree)]
    if declared is None:
        if len(leaves) != 1:
            raise PlanRefused("fanout_unsafe")
        return leaves[0]
    if len(leaves) != 1:
        raise PlanRefused("fanout_unsafe")
    leaf = leaves[0]
    if declared == leaf:
        return declared
    # ``is_multiplied`` is True for the one-side of an edge: joining the child splits
    # the parent's rows. So a declared parent is the multiplied resource and the
    # child is the leaf.
    if is_multiplied(declared, join_tree) and _is_parent_of(declared, leaf, join_tree):
        return declared
    raise PlanRefused("anchor_not_root")


def _is_parent_of(parent: str, child: str, join_tree: list[ResolvedJoin]) -> bool:
    return any(
        (j.from_resource == parent and j.to_resource == child and j.relationship == "one_to_many")
        or (
            j.to_resource == parent and j.from_resource == child and j.relationship == "many_to_one"
        )
        for j in join_tree
    )


def row_identity_for(
    record_refs: Sequence[RecordRef],
    *,
    anchor: str,
    expanded: str | None,
    row_count: int,
    requested_anchor_count: int | None = None,
) -> RowIdentity:
    """Count anchors and childless anchors from the sealed record refs.

    Every row carries one ref per joined resource that has a record id, whether
    or not the plan projects that id, so the count does not depend on the
    visible columns. A row whose child side is NULL has no ref for ``expanded``."""
    anchor_by_row = {ref.row_index: ref.record_id for ref in record_refs if ref.resource == anchor}
    anchors = set(anchor_by_row.values())
    childless = 0
    if expanded is not None:
        rows_with_child = {ref.row_index for ref in record_refs if ref.resource == expanded}
        with_child = {anchor_by_row[index] for index in rows_with_child if index in anchor_by_row}
        childless = len(anchors - with_child)
    return RowIdentity(
        anchor=anchor,
        expanded=expanded,
        anchor_count=len(anchors),
        requested_anchor_count=requested_anchor_count,
        row_count=row_count,
        anchors_without_children=childless,
    )


def join_adjacency(joins: list[JoinDefinition]) -> dict[str, list[JoinDefinition]]:
    adjacency: dict[str, list[JoinDefinition]] = defaultdict(list)
    for join in joins:
        adjacency[join.from_resource].append(join)
        adjacency[join.to_resource].append(join)
    return adjacency


def shortest_join_path(
    joins: list[JoinDefinition], start: str, goal: str
) -> list[ResolvedJoin] | None:
    if start == goal:
        return []
    adjacency = join_adjacency(joins)
    parent: dict[str, tuple[str, JoinDefinition] | None] = {start: None}
    queue: deque[str] = deque([start])
    depth = {start: 0}
    while queue:
        current = queue.popleft()
        if depth[current] >= _MAX_JOIN_HOPS:
            continue
        for join in adjacency.get(current, ()):
            neighbor = join.to_resource if join.from_resource == current else join.from_resource
            if neighbor in parent:
                continue
            parent[neighbor] = (current, join)
            depth[neighbor] = depth[current] + 1
            if neighbor == goal:
                path: list[ResolvedJoin] = []
                node: str | None = goal
                while node != start:
                    assert node is not None
                    prev, edge = parent[node]  # type: ignore[misc]
                    path.append(
                        ResolvedJoin(
                            from_resource=edge.from_resource,
                            to_resource=edge.to_resource,
                            relationship=edge.relationship,
                            on_sql=edge.on_sql,
                            optional=edge.optional,
                        )
                    )
                    node = prev
                path.reverse()
                return path
            queue.append(neighbor)
    return None


def resolve_join_tree(
    joins: list[JoinDefinition], resources: tuple[str, ...]
) -> list[ResolvedJoin]:
    if len(resources) <= 1:
        return []
    ordered = sorted(resources)
    edges: list[ResolvedJoin] = []
    seen: set[tuple[str, str, str]] = set()
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            path = shortest_join_path(joins, left, right)
            if path is None:
                raise PlanRefused("no_join_path")
            for edge in path:
                key = (edge.from_resource, edge.to_resource, edge.on_sql)
                if key not in seen:
                    seen.add(key)
                    edges.append(edge)
    return edges


# Backwards compatibility aliases
_join_adjacency = join_adjacency
_shortest_join_path = shortest_join_path
_resolve_join_tree = resolve_join_tree
