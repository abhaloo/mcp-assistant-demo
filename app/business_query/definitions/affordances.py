"""Validation of definition bundle affordances and cross-definition references."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business_query.definitions.schema import DefinitionBundle, MeasureDefinition


def _affordance_violations(bundle: DefinitionBundle) -> list[str]:
    """Fail closed when additive references point at undeclared definitions."""
    measures_by_name = {measure.name: measure for measure in bundle.measures}
    capability_names = {entry.name for entry in bundle.capabilities}

    violations = _duplicate_name_violations(bundle)
    violations.extend(_measure_violations(bundle, measures_by_name))
    violations.extend(_segment_violations(bundle))
    violations.extend(_capability_violations(bundle))
    violations.extend(_dimension_violations(bundle, capability_names))
    violations.extend(_safe_combination_violations(bundle, capability_names))
    return violations


def _duplicate_name_violations(bundle: DefinitionBundle) -> list[str]:
    violations: list[str] = []
    all_names = (
        [measure.name for measure in bundle.measures]
        + [dimension.name for dimension in bundle.dimensions]
        + [bucket.name for bucket in bundle.bucket_sets]
        + [segment.name for segment in bundle.segments]
    )
    seen_names: set[str] = set()
    for name in all_names:
        if name in seen_names:
            violations.append(
                f"duplicate definition name {name!r} across "
                "measures/dimensions/bucket_sets/segments"
            )
        seen_names.add(name)
    return violations


def _measure_violations(
    bundle: DefinitionBundle, measures_by_name: dict[str, MeasureDefinition]
) -> list[str]:
    violations: list[str] = []
    time_dimensions = {
        dimension.name for dimension in bundle.dimensions if dimension.type == "time"
    }
    for measure in bundle.measures:
        for axis in measure.allowed_time_axes:
            if axis not in time_dimensions:
                violations.append(
                    f"measure {measure.name!r}: allowed_time_axes {axis!r} "
                    "is not a declared time dimension"
                )
        if measure.overdue_after_days is not None and not measure.relative_to_business_date:
            violations.append(
                f"measure {measure.name!r}: overdue_after_days requires relative_to_business_date"
            )
        if measure.overdue_after_days is not None and measure.overdue_after_days < 0:
            violations.append(f"measure {measure.name!r}: overdue_after_days must be >= 0")

        if measure.agg_type == "derived":
            violations.extend(_derived_measure_violations(measure, measures_by_name))
        elif measure.expression_kind is not None:
            violations.append(
                f"measure {measure.name!r}: expression_kind requires agg_type 'derived'"
            )
    return violations


def _segment_violations(bundle: DefinitionBundle) -> list[str]:
    violations: list[str] = []
    resource_names = {resource.name for resource in bundle.resources}
    for segment in bundle.segments:
        if segment.owning_resource not in resource_names:
            violations.append(
                f"segment {segment.name!r}: owning_resource "
                f"{segment.owning_resource!r} not declared"
            )
    return violations


def _capability_violations(bundle: DefinitionBundle) -> list[str]:
    violations: list[str] = []
    segment_names = {segment.name for segment in bundle.segments}
    for capability in bundle.capabilities:
        if capability.kind == "segment" and capability.resolves_to not in segment_names:
            violations.append(
                f"capability {capability.name!r}: resolves_to {capability.resolves_to!r} "
                "is not a declared segment"
            )
    return violations


def _dimension_violations(bundle: DefinitionBundle, capability_names: set[str]) -> list[str]:
    violations: list[str] = []
    for dimension in bundle.dimensions:
        for related in dimension.direct_relationships:
            if related not in capability_names:
                violations.append(
                    f"dimension {dimension.name!r}: direct_relationships {related!r} "
                    "is not a declared capability"
                )
    return violations


def _safe_combination_violations(bundle: DefinitionBundle, capability_names: set[str]) -> list[str]:
    violations: list[str] = []
    for combination in bundle.safe_combinations:
        for member in combination:
            if member not in capability_names:
                violations.append(
                    f"safe_combinations member {member!r} is not a declared capability"
                )
    return violations


def _derived_measure_violations(
    measure: MeasureDefinition, measures_by_name: dict[str, MeasureDefinition]
) -> list[str]:
    violations: list[str] = []
    if measure.expression_kind is None:
        violations.append(f"measure {measure.name!r}: agg_type 'derived' requires expression_kind")
    if measure.format not in {"percent", "number"}:
        violations.append(f"measure {measure.name!r}: derived format must be 'percent' or 'number'")
    if measure.sql_expression != "":
        violations.append(f"measure {measure.name!r}: derived measure sql_expression must be empty")
    if measure.filter_sql is not None:
        violations.append(f"measure {measure.name!r}: derived measure filter_sql must be None")

    if measure.expression_kind == "ratio":
        if len(measure.derived_from) != 2:
            violations.append(
                f"measure {measure.name!r}: ratio requires exactly two derived_from measures"
            )
    elif measure.expression_kind == "share":
        if len(measure.derived_from) != 1:
            violations.append(
                f"measure {measure.name!r}: share requires exactly one derived_from measure"
            )

    for comp_name in measure.derived_from:
        comp = measures_by_name.get(comp_name)
        if comp is None:
            violations.append(
                f"measure {measure.name!r}: derived_from component {comp_name!r} "
                "is not a declared measure"
            )
            continue
        if comp.agg_type == "derived":
            violations.append(
                f"measure {measure.name!r}: derived component {comp_name!r} "
                "may not itself be derived"
            )
        if comp.owning_resource != measure.owning_resource:
            violations.append(
                f"measure {measure.name!r}: derived component {comp_name!r} "
                f"owning_resource {comp.owning_resource!r} does not match "
                f"{measure.owning_resource!r}"
            )
    return violations
