"""Bounded, redacted detail evidence projections for execution events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from app.business_query.journaling.canonical_json import canonical_json_bytes as _canonical_json
from app.business_query.seal.events.digest import _bounded_digest, _sha256

if TYPE_CHECKING:
    from app.business_query.seal.events.event import BusinessQueryExecutionEvent

_EMPTY_DETAIL_EVIDENCE_DIGEST = _sha256(_canonical_json([]))
_MAX_DETAIL_EVIDENCE_ITEMS = 256
_SAFE_DETAIL_COVERAGE_STATUSES = frozenset(
    {"complete", "verified_complete", "incomplete", "unknown"}
)


class DetailEvidence(BaseModel):
    """Bounded, redacted evidence for one authorized detail fact.

    The detail value and display text are intentionally absent.  Identifiers,
    revision labels, coverage metadata, and provenance are represented only by
    tenant-scoped digests so the operator projection cannot become a second
    result payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: str = Field(min_length=1, max_length=128)
    owner_resource: str | None = Field(default=None, max_length=64)
    owner_ref_digest: str | None = Field(default=None, min_length=64, max_length=64)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)
    definition_digest: str | None = Field(default=None, min_length=64, max_length=64)
    profile_digest: str | None = Field(default=None, min_length=64, max_length=64)
    coverage_status: str | None = Field(default=None, max_length=64)
    coverage_digest: str | None = Field(default=None, min_length=64, max_length=64)
    provenance_digest: str | None = Field(default=None, min_length=64, max_length=64)


def _detail_value(detail: Any, name: str) -> Any:
    if isinstance(detail, Mapping):
        return detail.get(name)
    return getattr(detail, name, None)


def _optional_scoped_detail_digest(
    value: Any,
    *,
    project_id: str,
    family: str,
    owner_resource: str | None,
    kind: str,
) -> str | None:
    if value is None:
        return None
    return _bounded_digest(
        {
            "project_id": project_id,
            "family": family,
            "owner_resource": owner_resource,
            "kind": kind,
            "value": value,
        }
    )


def build_detail_evidence(details: Iterable[Any], *, project_id: str) -> tuple[DetailEvidence, ...]:
    """Convert authorized ``RecordDetail`` values into redacted event facts."""
    if not project_id:
        raise ValueError("detail evidence project is required")
    source = tuple(details)
    if len(source) > _MAX_DETAIL_EVIDENCE_ITEMS:
        raise ValueError("detail evidence exceeds bounded item limit")

    result: list[DetailEvidence] = []
    for detail in source:
        family = _detail_value(detail, "family")
        if not isinstance(family, str) or not family:
            raise ValueError("detail evidence family is required")
        owner_resource = _detail_value(detail, "owner_resource")
        if owner_resource is not None and not isinstance(owner_resource, str):
            raise ValueError("detail evidence owner resource is invalid")
        owner_id = _detail_value(detail, "owner_id")
        revision_hash = _detail_value(detail, "revision_hash")
        observation_version = _detail_value(detail, "observation_version")
        definition_revision = _detail_value(detail, "definition_revision")
        profile_revision_hash = _detail_value(detail, "profile_revision_hash")
        profile_revision = _detail_value(detail, "profile_revision")
        coverage_status = _detail_value(detail, "coverage_status")
        coverage = _detail_value(detail, "coverage")
        validation = _detail_value(detail, "validation")
        provenance = _detail_value(detail, "provenance")
        safe_coverage_status = (
            coverage_status
            if isinstance(coverage_status, str)
            and coverage_status in _SAFE_DETAIL_COVERAGE_STATUSES
            else (
                coverage
                if isinstance(coverage, str) and coverage in _SAFE_DETAIL_COVERAGE_STATUSES
                else None
            )
        )

        revision_digest = None
        if revision_hash is not None or observation_version is not None:
            revision_digest = _optional_scoped_detail_digest(
                {
                    "revision_hash": revision_hash,
                    "observation_version": observation_version,
                },
                project_id=project_id,
                family=family,
                owner_resource=owner_resource,
                kind="revision",
            )
        profile_value = (
            profile_revision_hash if profile_revision_hash is not None else profile_revision
        )
        coverage_value = {
            "coverage": coverage,
            "coverage_status": coverage_status,
            "validation": validation,
        }
        has_coverage = any(value is not None for value in coverage_value.values())
        result.append(
            DetailEvidence(
                family=family,
                owner_resource=owner_resource,
                owner_ref_digest=_optional_scoped_detail_digest(
                    owner_id,
                    project_id=project_id,
                    family=family,
                    owner_resource=owner_resource,
                    kind="owner_ref",
                ),
                revision_digest=revision_digest,
                definition_digest=_optional_scoped_detail_digest(
                    definition_revision,
                    project_id=project_id,
                    family=family,
                    owner_resource=owner_resource,
                    kind="definition",
                ),
                profile_digest=_optional_scoped_detail_digest(
                    profile_value,
                    project_id=project_id,
                    family=family,
                    owner_resource=owner_resource,
                    kind="profile",
                ),
                coverage_status=safe_coverage_status,
                coverage_digest=(
                    _optional_scoped_detail_digest(
                        coverage_value,
                        project_id=project_id,
                        family=family,
                        owner_resource=owner_resource,
                        kind="coverage",
                    )
                    if has_coverage
                    else None
                ),
                provenance_digest=_optional_scoped_detail_digest(
                    provenance,
                    project_id=project_id,
                    family=family,
                    owner_resource=owner_resource,
                    kind="provenance",
                ),
            )
        )
    return tuple(result)


def detail_evidence_digest(details: Iterable[DetailEvidence]) -> str:
    """Return the canonical digest covered by the event integrity seal."""
    return _sha256(_canonical_json([detail.model_dump(mode="json") for detail in details]))


def _detail_evidence_row_values(
    event: BusinessQueryExecutionEvent,
) -> list[dict[str, Any]]:
    return [
        {
            "answer_query_id": event.answer_query_id,
            "project_id": event.project_id,
            "ordinal": ordinal,
            "detail_digest": _bounded_digest(detail.model_dump(mode="json")),
            **detail.model_dump(mode="json"),
        }
        for ordinal, detail in enumerate(event.detail_evidence)
    ]
