"""
Typed reference ledger -- LIVE caller.

Constructs ONE ``PolicyScopedRecordExecutor`` per request (never the
``record_tools`` module-level tool wrapper), calls
``rehydrate_reference_ledger``, and maps a non-auth infra fault onto
``ReferenceContextUnavailable(reason="transient")``.

Fail-closed gating (security-invariants.md #9 -- a setting alone must never
create access): ``_try_build_executor`` calls the SAME
``build_record_executor(principal, resources=...)`` production factory
``app/policy/record_tools.py``'s tools use -- ``ensure_record_tools_available``
runs BEFORE any ``Engine`` is constructed, so ``MCP_RECORD_DATABASE_URL is
None`` (the prod default today) or a v1/claimless principal both deny with
zero connection construction, not just zero queries. A ``RecordAccessDenied``
here means "the feature is off for this request" -- NOT a user-facing signal;
the caller (``rehydrate_for_followup``) returns ``NoReferenceContext()``,
which every downstream consumer treats exactly like "this thread never had a
ledger" (no message, the condenser/answer flow proceeds normally).

This module's live caller returns the SAME three-state
``app.conversation.rehydration.RehydrationResult`` the pure function
returns -- a non-auth infra fault maps onto the shared
``ReferenceContextUnavailable`` type at ``reason="transient"`` (distinct
from the pure function's own ``reason="revoked_or_missing"`` only by that
one field, never by type), and every "not even attempted" case (gate
closed, empty turns, no ledger in history) maps onto the shared
``NoReferenceContext()``. There is no separate call-site-only outcome space
to reconcile with the core one.
"""

from __future__ import annotations

import logging

from app.auth import Principal
from app.conversation.reference_artifact import MAX_REFERENCES
from app.conversation.rehydration import (
    NoReferenceContext,
    ReferenceContextAvailable,
    ReferenceContextUnavailable,
    RehydrationResult,
    _nearest_artifact,
    rehydrate_reference_ledger,
)
from app.conversation.transcript_models import TranscriptTurn
from app.models.record_context import (
    MAX_FIELD_KEY_LEN,
    MAX_FIELD_VALUE_LEN,
    MAX_LABEL_LEN,
    RecordContext,
    RecordContextRecord,
)
from app.policy.record_executor import (
    PolicyScopedRecordExecutor,
    RecordAccessDenied,
    RecordRow,
    build_record_executor,
)
from app.resources import ProcessResources
from app.telemetry import run_in_thread

logger = logging.getLogger(__name__)

# Fixed, source-independent user-facing messages -- forbidden == missing:
# identical regardless of which ArtifactSource produced the now-unavailable
# ledger, never a count/type/id oracle.
RECORDS_NO_LONGER_AVAILABLE_MESSAGE = "The earlier records are no longer available."
REHYDRATION_UNAVAILABLE_MESSAGE = (
    "I couldn't retrieve the earlier records right now — please try again."
)


def _try_build_executor(
    principal: Principal, *, resources: ProcessResources
) -> PolicyScopedRecordExecutor | None:
    """``None`` whenever the per-request gate is closed -- never raises past
    this point for a gate-closed caller (mirrors app/rag/sql_access.py's
    gating seam: a setting alone can never create access)."""
    try:
        return build_record_executor(principal, resources=resources)
    except RecordAccessDenied:
        return None


def _rehydrate_sync(
    turns: list[TranscriptTurn], principal: Principal, *, resources: ProcessResources
) -> RehydrationResult:
    """Runs inside a worker thread (constructing an ``Engine`` and running a
    query are both blocking I/O). Deliberately does NOT catch its own
    exceptions -- ``rehydrate_for_followup`` (the async boundary below) is
    the single place that converts an infra fault into
    ``ReferenceContextUnavailable(reason="transient")``, whether it
    originates in this function or in the ``run_in_thread``/
    ``asyncio.to_thread`` machinery itself.

    The nearest-artifact check runs FIRST, before any executor is built --
    today (and for the foreseeable v1-only present) this is the overwhelming
    common case (no ledger at all), so it avoids constructing a
    ``PolicyScopedRecordExecutor`` (a live ``Engine`` + manifest load) for a
    thread that never had one.
    """
    if _nearest_artifact(turns) is None:
        return NoReferenceContext()
    executor = _try_build_executor(principal, resources=resources)
    if executor is None:
        return NoReferenceContext()
    return rehydrate_reference_ledger(turns, principal, executor)


async def rehydrate_for_followup(
    turns: list[TranscriptTurn], principal: Principal, *, resources: ProcessResources
) -> RehydrationResult:
    """The live caller: builds ONE ``PolicyScopedRecordExecutor`` per
    request, off the event loop (the executor is synchronous) via the
    existing ``run_in_thread`` pattern.

    Empty ``turns`` skips the attempt entirely without even entering the
    thread hop (mirrors ``condense_question``'s own empty-history
    passthrough) -- rehydration only makes sense on each follow-up; an
    empty thread has nothing to rehydrate.

    Never raises: any exception -- whether from inside ``_rehydrate_sync`` or
    from the threading mechanism itself -- becomes
    ``ReferenceContextUnavailable(reason="transient")``, logged at error
    level. An infra fault must never surface as an unhandled exception, and
    must never be silently read as "no context".
    """
    if not turns:
        return NoReferenceContext()
    try:
        return await run_in_thread(_rehydrate_sync, turns, principal, resources=resources)
    except Exception:
        logger.error(
            "reference-ledger rehydration failed -- treating as transient, "
            "never as records having ceased to exist",
            exc_info=True,
        )
        return ReferenceContextUnavailable(reason="transient")


# --- Rendering rehydrated rows through the existing fenced record-context --
# path (security-invariants.md #1) -- never a second prompt channel. The
# LangSmith masking regex (_RECORD_CONTEXT_FENCE_RE) already covers this
# exact fence; inventing a parallel format here would re-open an egress
# hole. -------------------------------------------------------------------

_LABEL_ELLIPSIS = "…"
_VALUE_ELLIPSIS = "…"


def _bounded_field_value(value: object) -> str:
    text = "" if value is None else str(value)
    if len(text) > MAX_FIELD_VALUE_LEN:
        return text[: MAX_FIELD_VALUE_LEN - len(_VALUE_ELLIPSIS)] + _VALUE_ELLIPSIS
    return text


def _bounded_fields(fields: dict[str, object]) -> dict[str, str]:
    """Mirrors app/models/record_context.py's own per-record caps
    (MAX_FIELD_KEY_LEN/MAX_FIELD_VALUE_LEN/MAX_FIELDS_PER_RECORD) -- applied
    HERE, proactively, rather than relying on RecordContextRecord's strict
    validators to reject an over-cap projected column: a manifest field that
    happens to be long must never crash the answer flow."""
    bounded: dict[str, str] = {}
    for key, value in fields.items():
        if not key or len(key) > MAX_FIELD_KEY_LEN:
            continue
        bounded[key] = _bounded_field_value(value)
    return bounded


def _label_for_row(row: RecordRow) -> str:
    """No ``RecordRow`` carries a display label -- every manifest resource
    projects a different "summary" field set, so there is no single field
    name that reliably means "the" display value across all ten resource
    types. ``resource_type #record_id`` is deterministic and always valid,
    rather than guessing at "title"/"name"/"work_number" per type."""
    label = f"{row.resource_type} #{row.record_id}"
    return label[:MAX_LABEL_LEN]


def record_context_from_rehydrated_rows(records: list[RecordRow]) -> RecordContext | None:
    """Adapt ``RecordRow`` (rehydration's own output shape) into the SAME
    ``RecordContext`` model the prompt + LangSmith-fence path already
    consumes -- reusing an audited fence beats inventing a new one. Never
    raises on a malformed row (defense in depth): a row that
    somehow fails to validate is skipped, logged, rather than crashing the
    whole answer flow over one bad projected value.
    """
    if not records:
        return None
    # Per-row validation via the real strict model (catches/skips a single
    # bad row rather than failing the whole batch), THEN dumped back to
    # plain dicts before handing to RecordContext.model_validate below --
    # RecordContext's own `_bounded_total_bytes` validator json.dumps()s its
    # raw input, which raises TypeError on an embedded model INSTANCE (only
    # plain dicts/primitives are JSON-serializable that way).
    validated: list[dict] = []
    for row in records[:MAX_REFERENCES]:
        try:
            record = RecordContextRecord(
                resource_type=row.resource_type,
                record_id=row.record_id,
                label=_label_for_row(row),
                fields=_bounded_fields(row.fields),
            )
        except Exception:
            logger.error(
                "dropping a rehydrated row that failed record-context validation resource_type=%s",
                row.resource_type,
                exc_info=True,
            )
            continue
        validated.append(record.model_dump(mode="json"))
    if not validated:
        return None
    return RecordContext.model_validate(
        {
            "version": 1,
            "title": "Records from earlier in this conversation",
            "records": validated,
        }
    )


def record_context_from_rehydration(outcome: RehydrationResult) -> RecordContext | None:
    """Convenience wrapper: only ``ReferenceContextAvailable`` ever produces
    a record_context; every other outcome (a message-only outcome, or
    nothing attempted) yields ``None`` -- unaffected. Exhaustive match over
    the three-state model: an outcome shape this function doesn't recognize
    is a bug, never silently treated as "no context"."""
    match outcome:
        case ReferenceContextAvailable(records=records):
            return record_context_from_rehydrated_rows(records)
        case NoReferenceContext() | ReferenceContextUnavailable():
            return None
        case _:
            raise AssertionError(f"unexpected rehydration outcome: {outcome!r}")
