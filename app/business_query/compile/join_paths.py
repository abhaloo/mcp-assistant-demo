"""Join-graph pathfinding and multiplication classification over bundle joins."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from app.business_query.definitions import JoinDefinition
from app.business_query.outcomes import PlanRefused

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


def is_optional_join_child(resource: str, join_tree: list[ResolvedJoin]) -> bool:
    """True when `resource` is reached as the nullable-FK ("to") side of a
    traversed optional edge. A row on that side can exist with no parent match
    (e.g. a bill-less journal entry), so an INNER join silently excludes it —
    a measure owned by `resource` must refuse rather than answer undercounted."""
    return any(edge.optional and edge.to_resource == resource for edge in join_tree)


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
