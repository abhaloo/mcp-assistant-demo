"""Plan inspection, candidate finding, coverage checking, and filter rebinding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.business_query.plan.filter_tree import (
    AttributePredicate,
    FilterGroup,
    PlanFilter,
    iter_filter_leaves,
    map_filter_tree,
)
from app.business_query.plan.value_resolver.contract import (
    ResolvableType,
    ResolverLookup,
)
from app.business_query.plan.value_resolver.matching import normalize_lookup_value

if TYPE_CHECKING:
    from app.business_query.definitions import DefinitionBundle
    from app.business_query.plan.plan_tree import PlanPath
    from app.business_query.plan.query_plan import BusinessQueryPlan


def find_resolver_lookups(
    plan: BusinessQueryPlan, bundle: DefinitionBundle
) -> list[ResolverLookup]:
    from app.business_query.plan.plan_tree import iter_plan_nodes

    resolvable = _resolvable_members(bundle)
    found: list[ResolverLookup] = []
    for path, node in iter_plan_nodes(plan):
        for leaf in iter_filter_leaves(node.filters):
            if not isinstance(leaf, PlanFilter):
                continue
            if leaf.operator != "contains" or leaf.member not in resolvable:
                continue
            if len(leaf.values) != 1 or not isinstance(leaf.values[0], str):
                continue
            raw = str(leaf.values[0])
            found.append(
                ResolverLookup(
                    value_type=resolvable[leaf.member],
                    member=leaf.member,
                    raw_value=raw,
                    normalized_value=normalize_lookup_value(raw),
                    path=path,
                )
            )
    return found


def resolver_coverage(bundle: DefinitionBundle) -> frozenset[str]:
    """Resolvable capability members whose bundle metadata can drive SqlValueResolver."""
    return frozenset(
        member
        for member, value_type in _resolvable_members(bundle).items()
        if _member_has_resolver_metadata(bundle, member, value_type)
    )


def assert_resolver_coverage(bundle: DefinitionBundle) -> None:
    """Fail closed when a resolvable member lacks lookup metadata."""
    resolvable = _resolvable_members(bundle)
    uncovered = sorted(
        member
        for member, value_type in resolvable.items()
        if not _member_has_resolver_metadata(bundle, member, value_type)
    )
    if uncovered:
        raise ValueError(f"resolvable members lack lookup metadata: {uncovered}")


def _member_has_resolver_metadata(
    bundle: DefinitionBundle, member: str, value_type: ResolvableType
) -> bool:
    dimension = next((d for d in bundle.dimensions if d.name == member), None)
    if dimension is None or dimension.resolvable_as != value_type:
        return False
    if not dimension.sql_expression.strip():
        return False
    binding = next((r for r in bundle.resources if r.name == dimension.owning_resource), None)
    if binding is None or not binding.projection_view.strip():
        return False
    if binding.scope_columns.entity is None:
        return False
    return True


def bind_exact_filter(
    plan: BusinessQueryPlan,
    member: str,
    canonical: str,
    *,
    path: PlanPath = (),
) -> BusinessQueryPlan:
    from app.business_query.plan.plan_tree import iter_plan_nodes, replace_plan_at_path

    if path == ():
        if plan.filters is None:
            return plan

        def bind_leaf(node: PlanFilter | AttributePredicate) -> PlanFilter | AttributePredicate:
            if (
                isinstance(node, PlanFilter)
                and node.member == member
                and node.operator == "contains"
            ):
                return PlanFilter(member=member, operator="eq", values=[canonical])
            return node

        bound = map_filter_tree(plan.filters, bind_leaf)
        if not isinstance(bound, FilterGroup):
            raise ValueError("filter binding must preserve the filter group")
        return plan.model_copy(update={"filters": bound})

    nodes = dict(iter_plan_nodes(plan))
    if path not in nodes:
        raise KeyError(f"Unknown plan path: {path}")
    target_subplan = nodes[path]
    updated_subplan = bind_exact_filter(target_subplan, member, canonical, path=())
    return replace_plan_at_path(plan, path, updated_subplan)


def _resolvable_members(bundle: DefinitionBundle) -> dict[str, ResolvableType]:
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}
    mapping: dict[str, ResolvableType] = {}
    for entry in bundle.capabilities:
        if entry.kind != "dimension":
            continue
        dimension = dimensions.get(entry.resolves_to)
        if dimension is None or dimension.resolvable_as is None:
            continue
        mapping[entry.name] = dimension.resolvable_as
    return mapping
