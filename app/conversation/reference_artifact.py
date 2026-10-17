"""
Typed reference ledger — WRITE side.

``ReferenceArtifact`` persists ONLY identifiers + provenance about a set of
business records a turn's answer was grounded on: a resource_type/record_id
pair per reference, plus which of four sources produced it. It never carries
a tool credential, a permission snapshot, a raw SQL row, or a client-supplied
display field (label/fields/link_key) — those stay exactly where they
already live (``RecordContext``, ``RecordRow``, ``PageRecord``), never
duplicated here. ``app/conversation/rehydration.py`` is the read side that
consumes what this module persists; building/attaching an artifact here does
not by itself change any answer returned to a client (see
``app/conversation/transcript.py::build_exchange_turns`` and
``app/models/schemas.py::TranscriptTurn``).

Four sources (``ArtifactSource``), and why only two build a working
``build_reference_artifact()`` call from live ask-flow data today:

  - ``global_search``: ``RecordContext.records`` — Billing Global Search
    results attached to an ask; see ``references_from_record_context``.
  - ``record_tool``: ``RecordRow`` list from ``PolicyScopedRecordExecutor``
    — the three record tools; see ``references_from_record_rows``.
  - ``sql_provenance``: defined in the enum, deliberately UNPRODUCED. The
    SQL agent path is generic query execution, not an attested, reusable
    record set — inventing a producer here would fabricate provenance the
    agent never actually gave us. Documented, not a gap.
  - ``page_context``: Jobs trusted page records (``TrustedPageContext``),
    either from the records-only path or from an ambient page that supplied a
    semantic answer; see ``references_from_page_records``.

Resource-type typing decision (READ THIS before assuming ``resource_type``
should be the closed ``ResourceType`` Literal from
``app/models/record_context.py``): it is deliberately a bounded plain
``str`` here, NOT that Literal. Two of the three real producers
(``global_search``, ``record_tool``) already only ever supply values from
that closed registry — enforced upstream by their own source models
(``RecordContextRecord.resource_type: ResourceType``, and every
``RecordRow`` is built from a ``ResourceType``-typed tool parameter in
``app/policy/record_tools.py``) — so re-declaring the same closed set here
would just be a second copy of a constraint the inputs already guarantee.
The third (``page_context``) supplies Jobs' ``resource_type="work_order"``,
which is NOT a member of that registry (the SQL-policy manifest has no
``work_order`` resource) — typing this field as the closed Literal would
make it impossible to persist a real ``page_context`` artifact at all. A
plain bounded string lets all three real producers build correctly;
``rehydration.py`` is what intersects ``references[].resource_type``
against the live v2 snapshot's granted resources before calling
``executor.get(...)`` — a ``page_context`` reference's ``"work_order"``
simply never survives that intersection (fail-closed, not an error: those
records were never manifest-backed, so they were never rehydratable
through the SQL-policy executor in the first place).

``record_id`` is likewise a plain bounded string (mirrors ``RecordRow`` and
``RecordContextRecord``). Rehydration converts it to ``int`` for
``PolicyScopedRecordExecutor.get(record_ids: list[int])`` — per
security-invariants.md #7, that conversion must reject a non-round-tripping
value rather than crash or silently coerce; ``global_search``-sourced ids
are NOT guaranteed numeric (Billing may send any non-empty string), unlike
``record_tool``/``page_context`` ids which are always decimal-string by
construction.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.record_context import RecordContext
from app.policy.record_executor import RecordRow

# Mirrors RecordContext.MAX_RECORDS's cap posture — same order of
# magnitude, not a shared constant, since the two caps are free to diverge
# independently in the future.
MAX_REFERENCES = 20
MAX_RESOURCE_TYPE_LEN = 64
MAX_RECORD_ID_LEN = 64
MAX_ARTIFACT_ID_LEN = 64

ArtifactSource = Literal["global_search", "record_tool", "sql_provenance", "page_context"]

# Jobs' page_context carries resource_type="work_order" (see
# references_from_page_records below) -- NOT a member of the SQL-policy
# manifest's resource registry, which calls the same underlying entity "job"
# (app/policy/manifest/policy-manifest.json;
# app/models/record_context.py's ResourceType Literal). Reconciled with a
# ONE-POINT ALIAS here, at the ledger-persistence boundary, rather than a
# wire-format rename: the blade `data-mcp-context-resource` attribute, its JS
# builders, and app/rag/page_context.py's route keys all stay "work_order"
# (renaming risks cached-JS version skew on an already-shipped surface).
#
# Id parity: Billing's JobsPageContextResolver::buildRecord() mints
# `PageRecord::id` as `(string)(int) $workOrder->id` -- the WorkOrder
# primary key. Billing's `ai_v1_job` projection view
# (ProjectionDefinitions::jobView) selects `id: wo.id` -- the SAME primary
# key, same column, no transformation. RAG's executor stringifies with
# `str(row[link_column])` where `link_column` is the manifest's
# `canonical_link_key` ("id") for the "job" resource -- also the same
# primary key. All three sides key off one integer id with no
# prefix/offset/alternate id space anywhere in the chain, so the alias only
# ever needs to translate the resource_type label, never the id value itself.
_RESOURCE_TYPE_ALIASES: dict[str, str] = {"work_order": "job"}


def _aliased_resource_type(resource_type: str) -> str:
    """Single lookup point for the work_order->job reconciliation --
    pre-alias persisted artifacts still carrying "work_order" simply never
    match a "job" grant either, so they keep dropping cleanly at
    rehydration's intersection check -- the 4h thread TTL makes that a
    non-issue in practice."""
    return _RESOURCE_TYPE_ALIASES.get(resource_type, resource_type)


class ArtifactReference(BaseModel):
    """One typed identifier — resource_type + record_id ONLY. No label, no
    fields, no link_key, no credential: identifiers + provenance, nothing a
    client supplied for display."""

    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: str = Field(min_length=1, max_length=MAX_RESOURCE_TYPE_LEN)
    record_id: str = Field(min_length=1, max_length=MAX_RECORD_ID_LEN)


class ReferenceArtifact(BaseModel):
    """Strict v1 typed reference ledger entry attached to an assistant turn.
    See module docstring for the full binding contract."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    artifact_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LEN)
    kind: Literal["record_set"]
    source: ArtifactSource
    references: list[ArtifactReference] = Field(min_length=1, max_length=MAX_REFERENCES)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _reject_non_plain_int_version(cls, value: object) -> object:
        # Literal[1] alone accepts 1.0 (float) via loose "==" equality even
        # under strict=True — bool/float must be rejected before that check
        # runs, or "schema_version": 1.0 would silently pass.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("schema_version must be exactly 1 (int)")
        return value


def new_artifact_id() -> str:
    """Opaque artifact id, minted at call time (never at import time)."""
    return uuid.uuid4().hex


def build_reference_artifact(
    source: ArtifactSource, references: list[ArtifactReference]
) -> ReferenceArtifact | None:
    """Pure builder: None for an empty ref set — no empty artifacts persisted.

    Applies the work_order->job resource_type alias to every reference
    regardless of ``source`` -- "work_order" only ever originates from
    ``references_from_page_records`` (Jobs), so this is a no-op for the
    other two producers, never a behavior change for them.
    """
    if not references:
        return None
    return ReferenceArtifact(
        schema_version=1,
        artifact_id=new_artifact_id(),
        kind="record_set",
        source=source,
        references=[
            ArtifactReference(
                resource_type=_aliased_resource_type(r.resource_type), record_id=r.record_id
            )
            for r in references
        ],
    )


def references_from_record_context(record_context: RecordContext) -> list[ArtifactReference]:
    """``global_search`` source adapter — trusted record_context."""
    return [
        ArtifactReference(resource_type=r.resource_type, record_id=r.record_id)
        for r in record_context.records
    ]


def references_from_record_rows(rows: list[RecordRow]) -> list[ArtifactReference]:
    """``record_tool`` source adapter — PolicyScopedRecordExecutor output."""
    return [
        ArtifactReference(resource_type=row.resource_type, record_id=row.record_id) for row in rows
    ]


def references_from_page_records(
    resource_type: str, record_ids: list[str]
) -> list[ArtifactReference]:
    """``page_context`` source adapter — Jobs trusted page records.

    Takes bare ids (``TrustedPageContext.records[].id``), not ``PageRecord``
    instances: importing ``app.models.schemas`` here would cycle back to this
    module (``TranscriptTurn.reference_artifact`` imports ``ReferenceArtifact``
    from here). ``resource_type`` is passed by the caller (from
    ``TrustedPageContext.resource_type``, e.g. ``"work_order"``) since a page
    record carries no per-record resource_type — the whole page context
    shares one.
    """
    return [ArtifactReference(resource_type=resource_type, record_id=rid) for rid in record_ids]
