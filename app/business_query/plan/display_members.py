"""Auto-projection of a key's declared look member for record-row plans."""

from __future__ import annotations

from app.business_query.definitions import CapabilityEntry, DefinitionBundle
from app.business_query.plan import BusinessQueryPlan


def _capability_for_dimension(
    capabilities: dict[str, CapabilityEntry], target: str, projected: set[str]
) -> str | None:
    """The not-yet-projected capability name that resolves to ``target``, if any."""
    return next(
        (
            name
            for name, candidate in capabilities.items()
            if candidate.kind == "dimension"
            and candidate.resolves_to == target
            and name not in projected
        ),
        None,
    )


def with_display_members(
    plan: BusinessQueryPlan,
    bundle: DefinitionBundle,
    visible: frozenset[str],
) -> tuple[BusinessQueryPlan, frozenset[str]]:
    """Append each projected key's declared look or link companion to the plan.

    A key member whose bundle entry declares ``display_of`` reads best with the
    value of another member, the look. A look column whose bundle entry declares
    ``link_via`` needs the sibling member that holds the id. When the viewer can
    see that extra member and the plan does not already project it, the member
    joins the projection so the row carries both values. A viewer without it
    keeps the raw key alone: the projection never widens what the viewer can
    see. Aggregate grains never project a look or a companion.

    Both passes walk the planner's own projection. A member added by one pass
    never grows a further companion of its own.
    """
    if plan.grain != "entity_rows" or not plan.dimensions:
        return plan, frozenset()
    capabilities = {entry.name: entry for entry in bundle.capabilities}
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}
    original = list(plan.dimensions)
    projected = set(original)
    added: list[str] = []
    for pass_target in ("display_of", "link_via"):
        for member in original:
            entry = capabilities.get(member)
            if entry is None or entry.kind != "dimension":
                continue
            dimension = dimensions.get(entry.resolves_to)
            declaration = getattr(dimension, pass_target) if dimension is not None else None
            if declaration is None:
                continue
            extra_name = _capability_for_dimension(capabilities, declaration, projected)
            if extra_name is None or extra_name not in visible:
                continue
            projected.add(extra_name)
            added.append(extra_name)
    if not added:
        return plan, frozenset()
    return plan.model_copy(update={"dimensions": [*plan.dimensions, *added]}), frozenset(added)
