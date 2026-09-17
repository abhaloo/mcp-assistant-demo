"""Deterministic extraction of explicit record references from questions.

Record references are an authorization input, not planner prose.  This module
only recognizes a resource alias declared by the selected signed bundle (or a
capability entry owned by that resource) followed by one exact decimal id.  It
never searches arbitrary numbers, fuzzy-matches ids, or resolves through an
untrusted page label.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.business_query.definitions import DefinitionBundle, ResourceBinding

_RESOURCE_ALIAS_META_KEYS = ("resource_aliases", "aliases")
_RESOURCE_ALIASES_CACHE: dict[str, dict[str, tuple[str, ...]]] = {}
_REFERENCE_PATTERN_CACHE: dict[str, re.Pattern[str] | None] = {}


def _normalize_alias(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("_", " ").split())


@dataclass(frozen=True)
class ExplicitRecordReference:
    """One exact, bundle-resolved resource/id pair."""

    resource: str
    record_id: str
    binding_member: str | None
    alias: str
    start: int
    end: int


@dataclass(frozen=True)
class ExplicitRecordResolution:
    """The closed result of parsing record references in one question."""

    references: tuple[ExplicitRecordReference, ...] = ()

    @property
    def reference(self) -> ExplicitRecordReference | None:
        if len(self.references) != 1:
            return None
        return self.references[0]

    @property
    def ambiguous(self) -> bool:
        return len(self.references) > 1


def _aliases_for_resource(resource: ResourceBinding, bundle: DefinitionBundle) -> set[str]:
    aliases = {resource.name, resource.name.replace("_", " ")}
    aliases.update(resource.aliases)

    # Capability metadata is signed as part of the bundle.  A capability may
    # carry producer-owned aliases for a resource whose public name differs
    # from the internal resource key; unrelated capability metadata is ignored.
    for entry in bundle.capabilities:
        from app.business_query.authorize.capability import owning_resource  # noqa: PLC0415

        if owning_resource(bundle, entry) != resource.name:
            continue
        for key in _RESOURCE_ALIAS_META_KEYS:
            values = entry.meta.get(key)
            if isinstance(values, str):
                aliases.add(values)
            elif isinstance(values, list):
                aliases.update(value for value in values if isinstance(value, str))
    return {_normalize_alias(alias) for alias in aliases if _normalize_alias(alias)}


def _resource_aliases(bundle: DefinitionBundle) -> dict[str, tuple[str, ...]]:
    cached = _RESOURCE_ALIASES_CACHE.get(bundle.content_hash)
    if cached is not None:
        return cached
    by_alias: dict[str, set[str]] = {}
    for resource in bundle.resources:
        for alias in _aliases_for_resource(resource, bundle):
            by_alias.setdefault(alias, set()).add(resource.name)
    result = {alias: tuple(sorted(resources)) for alias, resources in by_alias.items()}
    if len(_RESOURCE_ALIASES_CACHE) >= 16:
        _RESOURCE_ALIASES_CACHE.clear()
    _RESOURCE_ALIASES_CACHE[bundle.content_hash] = result
    return result


def _reference_pattern(bundle: DefinitionBundle) -> re.Pattern[str] | None:
    if bundle.content_hash in _REFERENCE_PATTERN_CACHE:
        return _REFERENCE_PATTERN_CACHE[bundle.content_hash]
    aliases = _resource_aliases(bundle)
    if not aliases:
        if len(_REFERENCE_PATTERN_CACHE) >= 16:
            _REFERENCE_PATTERN_CACHE.clear()
        _REFERENCE_PATTERN_CACHE[bundle.content_hash] = None
        return None
    alternatives = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    pattern = re.compile(
        rf"(?<!\w)(?P<alias>{alternatives})(?:\s+(?:(?:id|number|no\.?)\s*)?|#\s*)(?P<record_id>[0-9]+)(?!\w)",
        re.IGNORECASE,
    )
    if len(_REFERENCE_PATTERN_CACHE) >= 16:
        _REFERENCE_PATTERN_CACHE.clear()
    _REFERENCE_PATTERN_CACHE[bundle.content_hash] = pattern
    return pattern


def _continues_a_signed_detail_alias(
    question: str, match: re.Match[str], bundle: DefinitionBundle
) -> bool:
    """Avoid treating a numeric detail value as a record id.

    ``job 1 side ptg`` is a detail question, not a reference to record ``1``.
    Billing signs the detail aliases, so this narrow exclusion is deterministic
    and does not become a general-purpose fuzzy-number heuristic.
    """

    record_id = match.group("record_id")
    remainder = _normalize_alias(question[match.end() :])
    if not remainder:
        return False
    for detail in bundle.detail_definitions:
        aliases = [*detail.aliases, *detail.value_mapping]
        for alias in aliases:
            normalized = _normalize_alias(alias)
            parts = normalized.split()
            if len(parts) < 2 or parts[0] != record_id:
                continue
            suffix = " ".join(parts[1:])
            if remainder == suffix or remainder.startswith(f"{suffix} "):
                return True
    return False


def resolve_explicit_record_references(
    question: str, bundle: DefinitionBundle
) -> ExplicitRecordResolution:
    """Resolve every exact resource/id phrase in ``question``.

    One unique reference is usable.  Multiple references are deliberately
    ambiguous, even when they use the same resource, because silently picking
    one would widen or change the requested record set.
    """

    pattern = _reference_pattern(bundle)
    if pattern is None:
        return ExplicitRecordResolution()
    aliases = _resource_aliases(bundle)
    resources_by_name = {resource.name: resource for resource in bundle.resources}
    found: list[ExplicitRecordReference] = []
    for match in pattern.finditer(question):
        if _continues_a_signed_detail_alias(question, match, bundle):
            continue
        alias = _normalize_alias(match.group("alias"))
        resources = aliases.get(alias, ())
        if len(resources) != 1:
            # An alias shared by two signed resources is not unambiguous.
            for resource in resources:
                found.append(
                    ExplicitRecordReference(
                        resource=resource,
                        record_id=match.group("record_id"),
                        binding_member=resources_by_name[resource].reference_dimension,
                        alias=alias,
                        start=match.start(),
                        end=match.end(),
                    )
                )
            continue
        found.append(
            ExplicitRecordReference(
                resource=resources[0],
                record_id=match.group("record_id"),
                binding_member=resources_by_name[resources[0]].reference_dimension,
                alias=alias,
                start=match.start(),
                end=match.end(),
            )
        )

    # Multiple signed aliases can describe the same span; those aliases are
    # not separate references.  Distinct spans or resource/id pairs remain
    # ambiguous and are not collapsed.
    unique: dict[tuple[str, str, int, int], ExplicitRecordReference] = {}
    for reference in found:
        key = (reference.resource, reference.record_id, reference.start, reference.end)
        unique[key] = reference
    return ExplicitRecordResolution(references=tuple(unique.values()))


def extract_explicit_record_reference(
    question: str, bundle: DefinitionBundle
) -> ExplicitRecordReference | None:
    """Return one unique explicit reference, otherwise ``None``."""

    return resolve_explicit_record_references(question, bundle).reference


def redact_explicit_record_references(
    question: str, bundle: DefinitionBundle
) -> tuple[str, ExplicitRecordReference | None]:
    """Remove the exact id phrase before planner generation.

    The planner still sees the resource noun and all business attributes, but
    cannot reinterpret an id as a capability member or invent a broader
    numeric filter.  Ambiguous/no-reference questions are returned unchanged.
    """

    resolution = resolve_explicit_record_references(question, bundle)
    reference = resolution.reference
    if reference is None:
        return question, None
    redacted = (
        f"{question[: reference.start]}the specified {reference.resource}"
        f"{question[reference.end :]}"
    )
    return redacted, reference
