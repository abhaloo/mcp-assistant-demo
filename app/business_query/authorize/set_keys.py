"""Entity-key identity proof and structural scope checks for derived sets."""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING, Any

from app.business_query.outcomes import PlanRefused
from app.business_query.plan.filter_tree import guaranteed_single_equality
from app.business_query.plan.query_plan import time_group_for

if TYPE_CHECKING:
    from app.business_query.definitions import DefinitionBundle, DimensionDefinition

# `derived` below is typed `Any`, not `ports.ScopedDerivedSetLike`: this
# module is reached by authorize/scoping.py (an import-cycle-baseline SCC
# member), and ports.py is itself an SCC member (via ScopedPlan/StoredPlan),
# so importing a protocol from ports.py here would close a new cycle back
# through it. Callers always pass a real ``ScopedDerivedSet``
# (id/key/mode/scoped.plan), which is all `.scoped.plan`, `.key`, `.mode`
# below actually read.

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_KEY_HOPS = 2


def _resolve_dimension(
    member: str,
    bundle: DefinitionBundle,
) -> DimensionDefinition:
    """Resolve a capability member name to its dimension definition."""
    entry = next((c for c in bundle.capabilities if c.name == member), None)
    if entry is None:
        raise PlanRefused("member_not_found")
    if entry.kind != "dimension":
        raise PlanRefused("grain_unexpressible", check_site="set_key_identity")

    dimension = next((d for d in bundle.dimensions if d.name == entry.resolves_to), None)
    if dimension is None:
        raise PlanRefused("grain_unexpressible", check_site="set_key_identity")
    return dimension


def _resolve_pk_identity(
    resource_name: str,
    column_name: str,
    bundle: DefinitionBundle,
) -> tuple[str, str]:
    """Find the unambiguous declared primary key reached by a physical column."""
    if not _SQL_IDENTIFIER_RE.fullmatch(column_name):
        raise PlanRefused("grain_unexpressible", check_site="set_key_identity")

    resources = {r.name: r for r in bundle.resources}
    resource = resources.get(resource_name)
    if resource is None:
        raise PlanRefused("grain_unexpressible", check_site="set_key_identity")

    if resource.primary_key == column_name:
        return (resource_name, column_name)

    reached_pks: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = {(resource_name, column_name)}
    queue: deque[tuple[str, str, int]] = deque([(resource_name, column_name, 0)])

    while queue:
        curr_res, curr_col, hops = queue.popleft()
        if hops >= _MAX_KEY_HOPS:
            continue

        for join in bundle.joins:
            if "=" not in join.on_sql:
                continue
            parts = [p.strip() for p in join.on_sql.split("=", 1)]
            if len(parts) != 2:
                continue
            from_col, to_col = parts[0], parts[1]
            valid_from = _SQL_IDENTIFIER_RE.fullmatch(from_col)
            valid_to = _SQL_IDENTIFIER_RE.fullmatch(to_col)
            if not valid_from or not valid_to:
                continue

            next_hop: tuple[str, str] | None = None
            if join.relationship in {"one_to_many", "one_to_one"}:
                if join.to_resource == curr_res and to_col == curr_col:
                    next_hop = (join.from_resource, from_col)
            if join.relationship in {"many_to_one", "one_to_one"}:
                if join.from_resource == curr_res and from_col == curr_col:
                    next_hop = (join.to_resource, to_col)

            if next_hop is not None and next_hop not in visited:
                visited.add(next_hop)
                next_res, next_c = next_hop
                parent_res = resources.get(next_res)
                if parent_res is not None and parent_res.primary_key == next_c:
                    reached_pks.add(next_hop)
                else:
                    queue.append((next_res, next_c, hops + 1))

    if len(reached_pks) != 1:
        raise PlanRefused("grain_unexpressible", check_site="set_key_identity")

    return next(iter(reached_pks))


def _assert_monetary_currency_partition(
    derived: Any,
    bundle: DefinitionBundle,
) -> None:
    """Enforce single currency equality for monetary derived sets."""
    inner_plan = derived.scoped.plan
    if not inner_plan.measures:
        return

    capabilities = {c.name: c for c in bundle.capabilities}
    measures = {m.name: m for m in bundle.measures}

    for measure_name in inner_plan.measures:
        entry = capabilities.get(measure_name)
        resolves_to = entry.resolves_to if entry is not None else measure_name
        measure = measures.get(resolves_to)
        if measure is None:
            continue

        currency = measure.currency_dimension
        if currency is None and measure.format != "currency":
            continue

        if currency is None:
            raise PlanRefused("capability_disabled")

        visible_aliases = [
            c.name
            for c in bundle.capabilities
            if c.kind == "dimension" and c.resolves_to == currency
        ]
        has_equality = any(
            guaranteed_single_equality(inner_plan.filters, alias) is not None
            for alias in visible_aliases
        )
        if not has_equality:
            raise PlanRefused("grain_unexpressible", check_site="set_currency_partition")


def assert_set_key_compatible(
    outer_member: str,
    derived: Any,
    bundle: DefinitionBundle,
    *,
    exclude: bool = False,
) -> None:
    """Verify the outer filter may use the set: an entity identity or the same time bucket."""
    inner_plan = derived.scoped.plan
    if inner_plan.dimensions != [derived.key]:
        raise PlanRefused("grain_unexpressible", check_site="set_grain")
    if inner_plan.grain not in {"grouped", "entity_rows"}:
        raise PlanRefused("grain_unexpressible", check_site="set_grain")
    if inner_plan.derived_sets:
        raise PlanRefused("grain_unexpressible", check_site="set_grain")
    if time_group_for(derived.key, inner_plan) is not None:
        _assert_time_group_compatible(outer_member, derived, bundle, exclude=exclude)
        return
    _assert_entity_key_compatible(outer_member, derived, bundle)


def _assert_time_group_compatible(
    outer_member: str, derived: Any, bundle: DefinitionBundle, *, exclude: bool
) -> None:
    """A time group selects calendar periods of one time member: the outer filter keeps
    rows of that same member inside them. Excluding periods is not supported."""
    if exclude:
        raise PlanRefused("grain_unexpressible", check_site="time_group_exclusion")
    if outer_member != derived.key:
        raise PlanRefused("grain_unexpressible", check_site="time_group_member_mismatch")
    if _resolve_dimension(outer_member, bundle).type != "time":
        raise PlanRefused("grain_unexpressible", check_site="set_key_type_mismatch")
    if derived.mode == "pick":
        _assert_pick_shape(derived, bundle)
    _assert_monetary_currency_partition(derived, bundle)


def _assert_entity_key_compatible(
    outer_member: str,
    derived: Any,
    bundle: DefinitionBundle,
) -> None:
    """Verify entity-key compatibility and structural rules between outer and inner set."""
    inner_plan = derived.scoped.plan
    if derived.mode == "ranked":
        if inner_plan.grain != "grouped" or len(inner_plan.measures) != 1:
            raise PlanRefused("grain_unexpressible", check_site="set_grain")
    elif derived.mode == "pick":
        _assert_pick_shape(derived, bundle)
    elif derived.mode == "complete" and inner_plan.grain == "entity_rows" and inner_plan.measures:
        raise PlanRefused("grain_unexpressible", check_site="set_grain")

    outer_dim = _resolve_dimension(outer_member, bundle)
    inner_dim = _resolve_dimension(derived.key, bundle)

    if outer_dim.type != inner_dim.type:
        raise PlanRefused("grain_unexpressible", check_site="set_key_type_mismatch")
    if (
        outer_dim.value_kind is not None
        and inner_dim.value_kind is not None
        and outer_dim.value_kind != inner_dim.value_kind
    ):
        raise PlanRefused("grain_unexpressible", check_site="set_key_type_mismatch")

    _assert_monetary_currency_partition(derived, bundle)

    outer_pk = _resolve_pk_identity(outer_dim.owning_resource, outer_dim.sql_expression, bundle)
    inner_pk = _resolve_pk_identity(inner_dim.owning_resource, inner_dim.sql_expression, bundle)

    if outer_pk != inner_pk:
        raise PlanRefused("grain_unexpressible", check_site="set_key_identity")


def _assert_pick_shape(derived: Any, bundle: DefinitionBundle) -> None:
    """A pick over rows orders by a dimension of the key's own resource; a pick over
    groups orders by its single measure."""
    inner_plan = derived.scoped.plan
    if not inner_plan.order:
        raise PlanRefused("grain_unexpressible", check_site="set_grain")
    basis = inner_plan.order[0].member
    if inner_plan.grain == "grouped":
        if len(inner_plan.measures) != 1 or basis != inner_plan.measures[0]:
            raise PlanRefused("grain_unexpressible", check_site="set_grain")
        return
    if inner_plan.measures:
        raise PlanRefused("grain_unexpressible", check_site="set_grain")
    key_dim = _resolve_dimension(derived.key, bundle)
    basis_dim = _resolve_dimension(basis, bundle)
    if basis_dim.owning_resource != key_dim.owning_resource:
        raise PlanRefused("grain_unexpressible", check_site="pick_basis_resource")
