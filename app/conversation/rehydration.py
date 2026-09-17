"""
Typed reference ledger -- READ side: rehydration + re-authorization.

``rehydrate_reference_ledger`` is the pure function that CONSUMES what
``app/conversation/reference_artifact.py`` persists: given a thread's turns
and the CURRENT request's verified principal, it selects the nearest
assistant-turn artifact, drops every reference the current snapshot no
longer authorizes, and re-fetches the survivors' CURRENT row data through
the real ``PolicyScopedRecordExecutor`` -- never a second, hand-rolled
scoping check (security-invariants.md #2/#9). Nothing here writes anything;
``app/conversation/rehydration_service.py`` is the live caller that wires
this into the ask flow.

``get_business_records`` is actually TWO different callables, easy to
conflate:

  - ``PolicyScopedRecordExecutor.get(resource_type, record_ids, *,
    field_set)`` (``app/policy/record_executor.py``) -- the class method.
    ONE ``resource_type`` per call, but a BATCH of ``record_ids`` (a list)
    in that single call -- "single-resource, gated per-call", where
    "per-call" bounds the resource_type, not the id count.
  - ``get_business_records(principal, params, resources=...)``
    (``app/policy/record_tools.py``) -- the module-level TOOL wrapper. It
    does NOT accept an executor: it calls ``build_record_executor`` with
    the container, which re-runs the manifest-hash/last-mile gate on every
    call and then reads one process-lifetime pool.

This module's own signature receives an already-constructed ``executor``
argument (``rehydrate_reference_ledger(turns, principal, executor)``) --
meaning the caller builds ONE ``PolicyScopedRecordExecutor`` per request and
hands it in. Calling the module-level ``get_business_records`` wrapper here
would silently IGNORE that injected executor and rebuild a brand-new one
from ``principal`` for EVERY surviving resource_type group -- N redundant
engine constructions per request instead of one. This module therefore
calls ``executor.get(...)`` directly (the class method), never the
module-level tool wrapper.

Three typed outcomes, never an exception, never a bare list a caller could
misread as "no context":

  - ``NoReferenceContext`` -- no assistant turn in ``turns`` carries a
    ``reference_artifact`` at all. This also covers "didn't even attempt
    rehydration" -- both read as "there is no reference context for this
    turn" from every consumer's point of view; see
    ``rehydration_service.py``.
  - ``ReferenceContextUnavailable(reason)`` -- an artifact existed, but
    EVERY reference in it either failed the snapshot intersection, failed
    the executor's own finer gate, failed record_id normalization, or the
    executor genuinely had no row for it (``reason="revoked_or_missing"``).
    ``rehydration_service.py`` also maps a non-auth infra-fault outcome
    onto this SAME type, at ``reason="transient"`` -- ``reason`` is an
    INTERNAL literal used only to pick between the two pinned,
    source-independent user messages; it is never itself surfaced.
    Deliberately does NOT carry the artifact's ``source``: user messages
    are source-independent by construction -- forbidden == missing
    regardless of which ``ArtifactSource`` produced the now-unavailable
    ledger. This is distinct from ``ReferenceArtifact.source`` on the
    PERSISTED artifact (``app/conversation/reference_artifact.py``), which
    is untouched and still carries provenance for audit.
  - ``ReferenceContextAvailable(records)`` -- at least one reference
    survived: the CURRENT, freshly re-authorized rows (``RecordRow``,
    reused verbatim from ``app/policy/record_executor.py``, never
    re-invented, so the answer flow can render these exactly like any
    other tool-sourced row).

Ambiguity BETWEEN two candidate artifacts ("ask which set") is left to the
condenser, which understands the follow-up's own reference -- this module's
deterministic nearest-turn selection is the base that builds on, not a
competing mechanism.

Identifiers are NEVER authority, throughout: a remembered id that is now
cross-entity, unauthorized, or simply no longer valid returns no row --
exactly like one that never existed (forbidden == missing,
security-invariants.md #3). Nothing in this module distinguishes those
cases from each other in its return shape.

``executor.get()`` is always called with its default ``field_set``
("summary" -- least-privilege by default) -- this module never requests
"detail" on a caller's behalf; a resource granted only "summary" still
rehydrates correctly, a resource whose grant lacks "summary" entirely
denies via the executor's own gate (caught above), same as any other
unauthorized group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.auth import Principal
from app.conversation.reference_artifact import ArtifactReference
from app.conversation.transcript_models import TranscriptTurn
from app.policy.record_executor import PolicyScopedRecordExecutor, RecordAccessDenied, RecordRow


@dataclass(frozen=True)
class NoReferenceContext:
    """No assistant turn carries a ``reference_artifact`` -- nothing to
    rehydrate. Distinct from ``ReferenceContextUnavailable``: this thread
    never had a ledger, rather than having had one that no longer resolves.
    Also the live caller's "not even attempted" state -- see
    ``rehydration_service.py``."""


ReferenceUnavailableReason = Literal["revoked_or_missing", "transient"]


@dataclass(frozen=True)
class ReferenceContextUnavailable:
    """The nearest artifact existed, but every one of its references either
    failed re-authorization or normalized/resolved to no row
    (``reason="revoked_or_missing"``) -- OR a non-auth infra fault
    interrupted the attempt, mapped by the live caller in
    ``rehydration_service.py`` (``reason="transient"``). ``reason`` is an
    INTERNAL literal used only to select between the two pinned,
    source-independent user messages -- never itself surfaced. Deliberately
    carries no ``source``: user messages are forbidden == missing
    regardless of which ``ArtifactSource`` produced the now-unavailable
    ledger."""

    reason: ReferenceUnavailableReason


@dataclass(frozen=True)
class ReferenceContextAvailable:
    """At least one reference survived re-authorization. ``records`` is the
    CURRENT, freshly fetched row data -- ``RecordRow``, reused verbatim
    (never a new shape the answer flow would need to learn)."""

    records: list[RecordRow]


RehydrationResult = NoReferenceContext | ReferenceContextUnavailable | ReferenceContextAvailable


def _nearest_artifact(turns: list[TranscriptTurn]):
    """Walk ``turns`` newest-first -- list position is the ONLY tiebreak
    (``TranscriptTurn`` carries no timestamp field, and none is read here
    even if it did; req 4 bans ``Date.now()``/``time.time()`` for this
    selection). Returns the first assistant turn's ``reference_artifact``,
    or ``None`` if no turn carries one. ``turns`` itself is oldest-first
    (``ConversationStore.load()``'s own return order), so "newest-first" is
    simply ``reversed()``."""
    for turn in reversed(turns):
        if turn.role == "assistant" and turn.reference_artifact is not None:
            return turn.reference_artifact
    return None


def _dedupe_references(references: list[ArtifactReference]) -> list[ArtifactReference]:
    """Collapse duplicate ``(resource_type, record_id)`` pairs --
    ``record_tool``/``global_search`` sources are not guaranteed unique the
    way ``page_context`` already is (upstream
    ``TrustedPageContext._bounded_unique_records``). First occurrence wins;
    order otherwise preserved."""
    seen: set[tuple[str, str]] = set()
    deduped: list[ArtifactReference] = []
    for ref in references:
        key = (ref.resource_type, ref.record_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _intersect_with_granted_resources(
    references: list[ArtifactReference], principal: Principal
) -> list[ArtifactReference]:
    """Drop any reference whose ``resource_type`` is not a granted resource
    in the CURRENT principal's v2 snapshot -- this is where
    ``page_context``/``work_order`` refs correctly disappear (not a
    manifest resource; see ``reference_artifact.py``'s module docstring)
    and where a capability revoked between turns takes effect. Coarse
    membership only (does ``principal.resources`` have this key at all) --
    the FINER per-action/field_set check still happens inside
    ``executor.get()`` itself (caught by the caller below), never
    duplicated here."""
    granted = set((principal.resources or {}).keys())
    return [ref for ref in references if ref.resource_type in granted]


def _normalize_record_id(record_id: str) -> int | None:
    """Reject a non-round-tripping id rather than crash or silently
    coerce/truncate it (security-invariants.md #7) -- ``global_search``-
    sourced ids are not guaranteed numeric, unlike ``record_tool``/
    ``page_context`` ids, which are always plain decimal-string by
    construction (``reference_artifact.py``). Round-trip via
    ``str(int(x)) == x`` also rejects leading zeros/signs/whitespace --
    shapes no real ``PolicyScopedRecordExecutor``-produced id would ever
    have, so nothing genuine is ever lost by this check.

    Also rejects non-positive ids: ``"0"``/``"-5"`` round-trip fine as
    integers but are never a real canonical record id (every manifest
    resource's primary key starts at 1) -- forbidden == missing, dropped
    exactly like any other bad id, never a distinct error shape.
    """
    try:
        value = int(record_id)
    except ValueError:
        return None
    if str(value) != record_id:
        return None
    if value < 1:
        return None
    return value


def _group_by_resource_type(references: list[ArtifactReference]) -> dict[str, list[int]]:
    """Groups surviving references by ``resource_type``, normalizing each
    ``record_id`` to ``int``. A reference whose id does not round-trip is
    DROPPED here (forbidden == missing) -- never crashes the batch, and
    never silently coerces it to a different, wrong id."""
    grouped: dict[str, list[int]] = {}
    for ref in references:
        record_id = _normalize_record_id(ref.record_id)
        if record_id is None:
            continue
        grouped.setdefault(ref.resource_type, []).append(record_id)
    return grouped


def rehydrate_reference_ledger(
    turns: list[TranscriptTurn],
    principal: Principal,
    executor: PolicyScopedRecordExecutor,
) -> RehydrationResult:
    """Pure, request-scoped re-authorization of a remembered reference
    ledger. See the module docstring for the outcome contract."""
    artifact = _nearest_artifact(turns)
    if artifact is None:
        return NoReferenceContext()

    deduped = _dedupe_references(artifact.references)
    survivors = _intersect_with_granted_resources(deduped, principal)
    grouped = _group_by_resource_type(survivors)

    rows: list[RecordRow] = []
    for resource_type, record_ids in grouped.items():
        try:
            rows.extend(executor.get(resource_type, record_ids))
        except RecordAccessDenied:
            # Identifiers are never authority: the coarse snapshot check
            # above only confirmed the resource_type is GRANTED at all --
            # the executor's own finer action/field_set gate can still
            # deny (e.g. granted for "search" but this call needs "read").
            # Treat exactly like a dropped reference; one denied group must
            # never fail the whole rehydration.
            continue

    if rows:
        return ReferenceContextAvailable(records=rows)
    return ReferenceContextUnavailable(reason="revoked_or_missing")
