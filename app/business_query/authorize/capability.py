"""Per-role capability card — member-level auth at generation time (D4)."""

from __future__ import annotations

from collections import defaultdict

from app.auth import Principal
from app.business_query.definitions import (
    CapabilityEntry,
    DefinitionBundle,
    DetailDefinition,
    definition_for,
)
from app.business_query.definitions.allowed_values import canonical_allowed_values
from app.business_query.plan import (
    AttributePredicate,
    BusinessQueryPlan,
    PlanFilter,
    iter_filter_leaves,
)


def _permissions_satisfied(entry: CapabilityEntry, principal: Principal) -> bool:
    if not entry.required_permissions:
        # issubset() on an empty set is vacuously True — an unguarded empty
        # required_permissions would make the capability visible to every
        # authenticated principal the moment capability_state flips enabled.
        return False
    grants = set(principal.permissions or [])
    return set(entry.required_permissions).issubset(grants)


def _visible_entries(principal: Principal, bundle: DefinitionBundle) -> list[CapabilityEntry]:
    return [
        entry
        for entry in bundle.capabilities
        if entry.capability_state == "enabled" and _permissions_satisfied(entry, principal)
    ]


def detail_permissions_satisfied(detail: DetailDefinition, principal: Principal) -> bool:
    """Check one signed detail revision's grants.

    ``required_permissions`` is the authoritative Billing field. The owner
    convention is retained only for the first B1 bundle, whose additive field
    was absent; newer bundles should always export the explicit grants.
    """
    required = detail.required_permissions or (f"view {detail.owner_resource.replace('_', ' ')}",)
    if principal.role == "superadmin":
        return True
    return set(required).issubset(set(principal.permissions or []))


def visible_members(principal: Principal, bundle: DefinitionBundle) -> frozenset[str]:
    """Capability-card names this principal may put on a plan.

    Typed detail families are planner members too.  They are signed by the
    Billing bundle rather than represented as ordinary measure/dimension
    entries, but omitting them from this set makes a detail query impossible:
    the planner is explicitly forbidden to invent names that are absent from
    its card.
    """
    names = {entry.name for entry in _visible_entries(principal, bundle)}
    names.update(
        detail.family_key
        for detail in bundle.detail_definitions
        if detail_permissions_satisfied(detail, principal)
    )
    return frozenset(names)


def allowed_filter_values_valid(plan: BusinessQueryPlan, bundle: DefinitionBundle) -> bool:
    """Validate every declared finite dimension domain before adapter selection."""
    capabilities = {entry.name: entry for entry in bundle.capabilities}
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}

    for group in (plan.filters, plan.having):
        for node in iter_filter_leaves(group):
            if isinstance(node, AttributePredicate):
                continue
            assert isinstance(node, PlanFilter)
            if node.operator in {"in_set", "not_in_set"}:
                continue
            capability = capabilities.get(node.member)
            dimension = (
                dimensions.get(capability.resolves_to)
                if capability is not None and capability.kind == "dimension"
                else None
            )
            if dimension is not None and canonical_allowed_values(dimension, node.values) is None:
                return False
    return True


def owning_resource(bundle: DefinitionBundle, entry: CapabilityEntry) -> str | None:
    resolved = definition_for(bundle, entry.kind, entry.resolves_to)
    return resolved.owning_resource if resolved is not None else None


def _joinable_groups(principal: Principal, bundle: DefinitionBundle) -> list[list[str]]:
    """Connectivity components over resources owning visible members (Cube meta precedent)."""
    visible = _visible_entries(principal, bundle)
    owned: set[str] = set()
    for entry in visible:
        resource = owning_resource(bundle, entry)
        if resource is not None:
            owned.add(resource)

    adjacency: dict[str, set[str]] = defaultdict(set)
    for join in bundle.joins:
        if join.from_resource in owned and join.to_resource in owned:
            adjacency[join.from_resource].add(join.to_resource)
            adjacency[join.to_resource].add(join.from_resource)
    for name in owned:
        adjacency.setdefault(name, set())

    seen: set[str] = set()
    components: list[list[str]] = []
    for start in sorted(owned):
        if start in seen:
            continue
        stack = [start]
        component: list[str] = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            stack.extend(sorted(adjacency[node] - seen))
        components.append(sorted(component))
    return components


def capability_card(principal: Principal, bundle: DefinitionBundle) -> str:
    """Render only enabled entries whose required_permissions ⊆ principal grants.

    Never includes row ids or scope internals. Listed dimensions include their allowed_values.
    decision_pending is always excluded.
    """
    entries = _visible_entries(principal, bundle)
    visible = {entry.name for entry in entries}
    measures = {measure.name: measure for measure in bundle.measures}
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}
    lines: list[str] = []
    for entry in sorted(entries, key=lambda e: e.name):
        examples = " | ".join(entry.example_questions) if entry.example_questions else "none"
        filters = ", ".join(entry.required_filters) if entry.required_filters else "none"
        meta = (
            ", ".join(f"{k}={v}" for k, v in sorted(entry.meta.items())) if entry.meta else "none"
        )
        currency_rule = ""
        owner = owning_resource(bundle, entry)
        semantic_facts = f"\n  owning_resource: {owner}" if owner is not None else ""
        if entry.kind == "measure":
            measure = measures.get(entry.resolves_to)
            if measure is not None and measure.snapshot:
                semantic_facts += "\n  time_axis: snapshot"
            if measure is not None and measure.time_dimension is not None:
                time_members = _visible_dimension_names(
                    bundle, visible, resolves_to=measure.time_dimension
                )
                if time_members:
                    semantic_facts += f"\n  default_time_dimension: {time_members[0]}"
            if measure is not None and measure.allowed_time_axes:
                axis_members = _visible_dimension_names(
                    bundle, visible, resolves_to=measure.allowed_time_axes
                )
                if axis_members:
                    semantic_facts += f"\n  allowed_time_axes: {', '.join(axis_members)}"
            currency = measure.currency_dimension if measure is not None else None
            if currency is not None:
                currency_members = _visible_dimension_names(bundle, visible, resolves_to=currency)
                if currency_members:
                    currency_rule = f"\n  currency_rule: filter or group by {currency_members[0]}"
                else:
                    currency_rule = "\n  currency_rule: unavailable"
        elif entry.kind == "dimension":
            dimension = dimensions.get(entry.resolves_to)
            related = (
                sorted(name for name in dimension.direct_relationships if name in visible)
                if dimension is not None
                else []
            )
            if related:
                semantic_facts += f"\n  direct_relationships: {', '.join(related)}"
            if dimension is not None and dimension.allowed_values:
                semantic_facts += f"\n  allowed_values: {', '.join(dimension.allowed_values)}"
        elif entry.kind == "segment":
            semantic_facts += "\n  usage: filter with operator eq and value true"
        lines.append(
            f"- {entry.name}: title={entry.title}; kind={entry.kind}; meta={meta}\n"
            f"  description: {entry.description}\n"
            f"  example_questions: {examples}\n"
            f"  required_filters: {filters}{semantic_facts}{currency_rule}"
        )
    # Detail families are part of the planner contract even though they do
    # not have a measure/dimension capability row.  Render only definitions
    # authorized by the owning resource permission; this keeps aliases and
    # canonical values out of cards for principals who cannot query them.
    visible_detail_families = set(visible_members(principal, bundle)) - {
        entry.name for entry in entries
    }
    # A family can have several immutable revisions. Render each family once,
    # using the signed current revision for planner guidance. Revision-pinned
    # plans remain accepted by the compiler, but old labels need not bloat the
    # planner card or create duplicate member lines.
    current_details: dict[str, object] = {}
    detail_revisions: defaultdict[str, list[object]] = defaultdict(list)
    for item in bundle.detail_definitions:
        if item.family_key not in visible_detail_families or not detail_permissions_satisfied(
            item, principal
        ):
            continue
        detail_revisions[item.family_key].append(item)
        if item.family_key not in current_details or item.is_current:
            current_details[item.family_key] = item
    for detail in sorted(current_details.values(), key=lambda item: item.family_key):
        revisions = detail_revisions[detail.family_key]
        aliases = sorted({alias for revision in revisions for alias in revision.aliases})
        aliases_text = ", ".join(aliases) if aliases else "none"
        canonical_values = sorted(
            {str(value) for revision in revisions for value in revision.value_mapping.values()}
        )
        values = ", ".join(canonical_values) if canonical_values else "none"
        # The planner wire uses a closed predicate value-kind vocabulary.  An
        # enum/text detail is compared as its canonical stored string.
        planner_value_kind = (
            "string" if detail.value_kind in {"enum", "text"} else detail.value_kind
        )
        revision_history = ""
        if len(revisions) > 1:
            revision_history = "\n  revision_history: " + "; ".join(
                f"{revision.revision_hash} (aliases: "
                f"{', '.join(revision.aliases) if revision.aliases else 'none'})"
                for revision in sorted(revisions, key=lambda item: item.revision_hash)
            )
        lines.append(
            f"- {detail.family_key}: title={detail.family_key}; kind=detail; meta=none\n"
            f"  description: Typed {detail.owner_resource} detail from {detail.logical_source}. "
            f"Use attribute_key={detail.family_key}; value_kind={planner_value_kind}; "
            f"canonical_values={values}; aliases={aliases_text}{revision_history}\n"
            "  example_questions: none\n"
            "  required_filters: none\n"
            f"  owning_resource: {detail.owner_resource}"
        )
    body = "\n".join(lines) if lines else "- none"
    groups = _joinable_groups(principal, bundle)
    if groups:
        group_text = "; ".join("{" + ", ".join(g) + "}" for g in groups)
    else:
        group_text = "none"
    combination_text = _visible_safe_combinations(visible, bundle.safe_combinations)
    card = f"Business-query capability card:\n{body}\nJoinable groups: {group_text}"
    if combination_text is not None:
        card += f"\nSafe combinations: {combination_text}"
    return card


def _visible_dimension_names(
    bundle: DefinitionBundle,
    visible: set[str],
    *,
    resolves_to: str | list[str],
) -> list[str]:
    wanted = {resolves_to} if isinstance(resolves_to, str) else set(resolves_to)
    return sorted(
        candidate.name
        for candidate in bundle.capabilities
        if candidate.kind == "dimension"
        and candidate.resolves_to in wanted
        and candidate.name in visible
    )


def _visible_safe_combinations(visible: set[str], combinations: list[list[str]]) -> str | None:
    shown: list[str] = []
    for combination in combinations:
        members = sorted(member for member in combination if member in visible)
        if len(members) == len(combination) and members:
            shown.append("{" + ", ".join(members) + "}")
    if not shown:
        return None
    return "; ".join(shown)
