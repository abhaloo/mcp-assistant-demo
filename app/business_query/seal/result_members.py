"""Result members declaration and row normalization for Business Query results (ADR 0053)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.business_query.definitions import (
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


def dimension_value_kind(dimension: DimensionDefinition, sample: Any) -> str:
    if dimension.value_kind is not None:
        return dimension.value_kind
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
    if dimension.type == "number" and dimension.is_primary_key:
        return "integer"
    if dimension.type == "number":
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
            kind = by_name[name].value_kind
            if value is not None and kind in DECIMAL_VALUE_KINDS:
                value = Decimal(str(value))
            elif value is not None and kind == "integer":
                if isinstance(value, bool):
                    raise ValueError("boolean is not an integer result")
                value = int(value)
            elif value is not None and kind == "date" and not isinstance(value, date):
                value = date.fromisoformat(str(value))
            elif value is not None and kind == "datetime" and not isinstance(value, datetime):
                value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            typed[name] = value
        normalized.append(typed)
    return tuple(normalized)


def result_columns(
    plan: BusinessQueryPlan,
    bundle: DefinitionBundle,
    members: Sequence[ResultMember],
) -> tuple[ResultColumn, ...]:
    """Wire column types for sealed members. The bundle declares; nothing infers."""
    capabilities = {entry.name: entry for entry in bundle.capabilities}
    measures = {measure.name: measure for measure in bundle.measures}
    dimensions = {dimension.name: dimension for dimension in bundle.dimensions}

    comp_roles = _comparison_roles(plan)

    columns: list[ResultColumn] = []
    for member in members:
        capability = capabilities.get(member.name)
        currency_key: str | None = None
        is_identifier = False
        is_count = False
        role: ResultColumnRole | None = None

        if member.name in comp_roles:
            base_m, role = comp_roles[member.name]
            base_measure = measure_for_member(bundle, base_m)
            # The previous value and the change keep the base measure's nature: a
            # count's change is a count, a currency's change carries its code.
            if role in {"previous", "delta"} and base_measure is not None:
                is_count = base_measure.counts_rows()
                if member.value_kind == "currency" and base_measure.currency_dimension is not None:
                    currency_key = _sibling_key(
                        members, capabilities, base_measure.currency_dimension
                    )

        if capability is not None and capability.kind == "measure":
            measure = measures[capability.resolves_to]
            is_count = measure.counts_rows()
            if member.value_kind == "currency" and measure.currency_dimension is not None:
                currency_key = _sibling_key(members, capabilities, measure.currency_dimension)
        elif capability is not None and capability.kind == "dimension":
            is_identifier = dimensions[capability.resolves_to].is_primary_key
        columns.append(
            ResultColumn(
                key=member.name,
                value_kind=member.value_kind,  # type: ignore[arg-type]
                currency_key=currency_key,
                is_identifier=is_identifier,
                is_count=is_count,
                role=role,
            )
        )
    return tuple(columns)


def _sibling_key(
    members: Sequence[ResultMember], capabilities: dict[str, Any], dimension: str
) -> str | None:
    """The result key of the selected dimension ``dimension`` resolves to, if selected."""
    for member in members:
        entry = capabilities.get(member.name)
        if entry is not None and entry.kind == "dimension" and entry.resolves_to == dimension:
            return member.name
    return None
