"""Result members declaration and row normalization for Business Query results (ADR 0053)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.auth.principal import Principal
from app.business_query.definitions import (
    CapabilityEntry,
    DefinitionBundle,
    DimensionDefinition,
    MeasureDefinition,
    measure_for_member,
)
from app.business_query.outcomes import (
    DECIMAL_VALUE_KINDS,
    ResultColumn,
    ResultColumnRole,
    comparison_keys,
)
from app.business_query.plan import BusinessQueryPlan
from app.business_query.seal.events import ResultMember
from app.rag.provenance.record_links import column_link_resource, record_href_template


def measure_value_kind(measure: MeasureDefinition) -> str:
    if measure.value_kind is not None:
        return measure.value_kind
    if measure.format == "currency":
        return "currency"
    if measure.counts_rows():
        return "integer"
    if measure.format == "percent":
        return "percent"
    if measure.time_dimension is not None and measure.format is None:
        return "datetime"
    return "decimal"


_FORMAT_TO_KIND = {
    "id": "integer",
    "number": "decimal",
    "currency": "currency",
    "percent": "percent",
    "date": "date",
    "datetime": "datetime",
}


def dimension_value_kind(dimension: DimensionDefinition, sample: Any) -> str:
    if dimension.value_kind is not None:
        return dimension.value_kind
    if dimension.format is not None:
        return _FORMAT_TO_KIND[dimension.format]
    if dimension.type == "time":
        if isinstance(sample, datetime):
            return "datetime"
        if isinstance(sample, date):
            return "date"
        if isinstance(sample, str) and "T" not in sample and ":" not in sample:
            return "date"
        return "datetime"
    if dimension.type == "boolean":
        return "boolean"
    if dimension.type == "number":
        # A number with no declaration keeps its value; only a declared id or a key is an integer.
        return "decimal"
    return "string"


def _comparison_roles(plan: BusinessQueryPlan) -> dict[str, tuple[str, ResultColumnRole]]:
    """Map every comparison result key to its base measure and role."""
    if plan.compare_to is None:
        return {}
    return {
        key: (measure, role)
        for measure in plan.measures
        for key, role in comparison_keys(measure).items()
    }


def declared_result_members(
    plan: BusinessQueryPlan,
    bundle: DefinitionBundle,
    result_keys: Sequence[str],
    result_rows: Sequence[dict[str, Any]] = (),
) -> tuple[ResultMember, ...]:
    """Build result types from the Billing-owned bundle, including empty results."""
    capabilities = {entry.name: entry for entry in bundle.capabilities}
    measures = {measure.name: measure for measure in bundle.measures}
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}
    selected = set(plan.measures) | set(plan.dimensions)
    if plan.bucket_set is not None:
        selected.add(plan.bucket_set)
    roles = _comparison_roles(plan)
    selected.update(roles)
    cmp_derived = {key: entry for key, entry in roles.items() if key != entry[0]}

    members: list[ResultMember] = []
    for name in result_keys:
        if name not in selected:
            raise ValueError("executor returned an undeclared result member")
        if name in cmp_derived:
            base_m, role = cmp_derived[name]
            if role == "delta_pct":
                kind = "decimal"
            else:
                base_measure = measure_for_member(bundle, base_m)
                if base_measure is None:
                    raise ValueError("executor returned an unknown measure")
                kind = measure_value_kind(base_measure)
            members.append(ResultMember(name=name, value_kind=kind, nullable=True))
            continue

        capability = capabilities.get(name)
        if capability is None:
            raise ValueError("executor returned an unknown result member")
        if capability.kind == "measure":
            measure = measures.get(capability.resolves_to)
            if measure is None:
                raise ValueError("executor returned an unknown measure")
            kind = measure_value_kind(measure)
            nullable = not measure.counts_rows()
        elif capability.kind == "dimension":
            dimension = dimensions.get(capability.resolves_to)
            if dimension is None:
                raise ValueError("executor returned an unknown dimension")
            sample = None
            if dimension.value_kind is None:
                sample = next(
                    (row.get(name) for row in result_rows if row.get(name) is not None), None
                )
            kind = dimension_value_kind(dimension, sample)
            nullable = True
        else:
            kind = "string"
            nullable = False
        members.append(ResultMember(name=name, value_kind=kind, nullable=nullable))
    return tuple(members)


def _coerce(value: Any, kind: str) -> Any:
    if value is not None and kind in DECIMAL_VALUE_KINDS:
        return Decimal(str(value))
    if value is not None and kind == "integer":
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer result")
        return int(value)
    if value is not None and kind == "date" and not isinstance(value, date):
        return date.fromisoformat(str(value))
    if value is not None and kind == "datetime" and not isinstance(value, datetime):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value


def normalize_event_rows(
    rows: Sequence[dict[str, Any]], members: Sequence[ResultMember]
) -> tuple[dict[str, Any], ...]:
    """Normalize numeric payloads to their declared durable value kinds."""
    by_name = {member.name: member for member in members}
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if set(row) != set(by_name):
            raise ValueError("executor row does not match declared result members")
        typed: dict[str, Any] = {}
        for name, value in row.items():
            member = by_name[name]
            kind = member.value_kind
            try:
                value = _coerce(value, kind)
            except (ValueError, TypeError, InvalidOperation) as exc:
                raise ValueError(
                    f"{member.name}: cannot represent {type(value).__name__} as {kind}"
                ) from exc
            typed[name] = value
        normalized.append(typed)
    return tuple(normalized)


def _declared_look_dimensions(
    capabilities: dict[str, CapabilityEntry],
    dimensions: dict[str, DimensionDefinition],
    projected_members: set[str],
) -> set[str]:
    """Look members whose key is projected: a display column whoever projected them."""
    looks: set[str] = set()
    for name in projected_members:
        capability = capabilities.get(name)
        if capability is None or capability.kind != "dimension":
            continue
        look = dimensions[capability.resolves_to].display_of
        if look is not None:
            looks.add(look)
    return looks


def _href_template_for(
    bundle: DefinitionBundle,
    principal: Principal | None,
    dimension: DimensionDefinition,
    member_key: str,
    sibling_key: str | None,
) -> str | None:
    """The sealed link template for one dimension column, or None.

    A column with no resource, a viewer with no principal, or a companion
    whose id member is absent from the row mints nothing."""
    if principal is None:
        return None
    resource = column_link_resource(bundle, dimension.name)
    if resource is None:
        return None
    if (
        dimension.link_via
        and not dimension.is_primary_key
        and not (dimension.format == "id" and dimension.link_key)
    ):
        if sibling_key is None:
            return None
        return record_href_template(bundle, principal, resource, sibling_key)
    return record_href_template(bundle, principal, resource, member_key)


def result_columns(
    plan: BusinessQueryPlan,
    bundle: DefinitionBundle,
    members: Sequence[ResultMember],
    *,
    display_members_added: frozenset[str] = frozenset(),
    principal: Principal | None = None,
) -> tuple[ResultColumn, ...]:
    """Wire column types for sealed members. The bundle declares; nothing infers.

    A column whose resource the bundle gives a route to, and whose viewer holds
    that route's link grant, carries the href template for its row key: the
    column itself for a key or foreign key, the link companion for a look.
    Members the display projection appended are display columns; a companion
    never demotes the look it serves."""
    capabilities = {entry.name: entry for entry in bundle.capabilities}
    measures = {measure.name: measure for measure in bundle.measures}
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}

    comp_roles = _comparison_roles(plan)
    projected_members = set(plan.dimensions)
    projected_dimensions = {
        capabilities[name].resolves_to
        for name in projected_members
        if capabilities.get(name) is not None and capabilities[name].kind == "dimension"
    }
    # A member the display projection appended is a servant column, never a
    # look owner: subtracting it keeps the look it serves visible and linking.
    look_dimensions = _declared_look_dimensions(
        capabilities, dimensions, projected_members - display_members_added
    )

    columns: list[ResultColumn] = []
    for member in members:
        capability = capabilities.get(member.name)
        currency_key: str | None = None
        is_identifier = False
        is_count = False
        role: ResultColumnRole | None = None
        label: str | None = None
        link_key: str | None = None
        display_key: str | None = None
        href_template: str | None = None

        if member.name in comp_roles:
            base_m, role = comp_roles[member.name]
            base_measure = measure_for_member(bundle, base_m)
            # The previous value and the change keep the base measure's nature: a
            # count's change is a count, a currency's change carries its code.
            if role in {"previous", "delta"} and base_measure is not None:
                is_count = base_measure.counts_rows()
                currency_key = _currency_key(
                    members, capabilities, member.value_kind, base_measure.currency_dimension
                )

        if capability is not None and capability.kind == "measure":
            measure = measures[capability.resolves_to]
            is_count = measure.counts_rows()
            currency_key = _currency_key(
                members, capabilities, member.value_kind, measure.currency_dimension
            )
        elif capability is not None and capability.kind == "dimension":
            dimension = dimensions[capability.resolves_to]
            is_identifier = dimension.is_primary_key or dimension.format == "id"
            label = _bucket_label(plan, member.name) or dimension.label
            link_key = dimension.link_key
            # A key whose declared look is projected names that look as its display value.
            if dimension.display_of in projected_dimensions:
                display_key = _sibling_key(members, capabilities, dimension.display_of)
            if capability.resolves_to in look_dimensions:
                role = "display"
            else:
                href_template = _href_template_for(
                    bundle,
                    principal,
                    dimension,
                    member.name,
                    _sibling_key(members, capabilities, dimension.link_via)
                    if dimension.link_via
                    else None,
                )
        if role is None and member.name in display_members_added:
            role = "display"
        columns.append(
            ResultColumn(
                key=member.name,
                value_kind=member.value_kind,  # type: ignore[arg-type]
                currency_key=currency_key,
                is_identifier=is_identifier,
                is_count=is_count,
                role=role,
                label=label,
                link_key=link_key,
                display_key=display_key,
                href_template=href_template,
            )
        )
    return tuple(columns)


def _bucket_label(plan: BusinessQueryPlan, name: str) -> str | None:
    """A grouped plan's time dimension is a calendar bucket: the column says which."""
    period = plan.period
    if plan.grain != "grouped" or period is None or period.granularity is None:
        return None
    return period.granularity.capitalize() if period.time_dimension == name else None


def _currency_key(
    members: Sequence[ResultMember],
    capabilities: dict[str, Any],
    value_kind: str,
    currency_dimension: str | None,
) -> str | None:
    """The currency column matching a member's currency value, if there is one."""
    if value_kind != "currency" or currency_dimension is None:
        return None
    return _sibling_key(members, capabilities, currency_dimension)


def _sibling_key(
    members: Sequence[ResultMember], capabilities: dict[str, Any], dimension: str
) -> str | None:
    """The result key of the selected dimension ``dimension`` resolves to, if selected."""
    for member in members:
        entry = capabilities.get(member.name)
        if entry is not None and entry.kind == "dimension" and entry.resolves_to == dimension:
            return member.name
    return None
