"""Resolve a plan member name to the bundle definition that backs it."""

from __future__ import annotations

from app.business_query.definitions.schema import (
    BucketSetDefinition,
    DefinitionBundle,
    DimensionDefinition,
    MeasureDefinition,
    SegmentDefinition,
)

MemberDefinition = MeasureDefinition | DimensionDefinition | BucketSetDefinition | SegmentDefinition


def resolve_member(bundle: DefinitionBundle, member: str) -> tuple[str, MemberDefinition] | None:
    """Resolve a capability member to its definition.

    Returns None when the member is not a declared capability, or when the entry's
    target definition is absent. A bare definition name is not a capability.
    """
    entry = next((entry for entry in bundle.capabilities if entry.name == member), None)
    if entry is None:
        return None
    definition = definition_for(bundle, entry.kind, entry.resolves_to)
    return (entry.kind, definition) if definition is not None else None


def measure_for_member(bundle: DefinitionBundle, member: str) -> MeasureDefinition | None:
    """The measure a member names, through its capability or as a declared measure name."""
    entry = next((entry for entry in bundle.capabilities if entry.name == member), None)
    target = entry.resolves_to if entry is not None else member
    return next((measure for measure in bundle.measures if measure.name == target), None)


def definition_for(bundle: DefinitionBundle, kind: str, name: str) -> MemberDefinition | None:
    """Resolve a capability entry's target to its declared definition."""
    if kind == "measure":
        return next((measure for measure in bundle.measures if measure.name == name), None)
    if kind == "dimension":
        return next((dimension for dimension in bundle.dimensions if dimension.name == name), None)
    if kind == "bucket_set":
        return next((bucket for bucket in bundle.bucket_sets if bucket.name == name), None)
    if kind == "segment":
        return next((segment for segment in bundle.segments if segment.name == name), None)
    return None
