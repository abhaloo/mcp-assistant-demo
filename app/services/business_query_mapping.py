"""Pure mapping helpers translating BusinessQueryOutcome into AskBusinessQueryResult."""

from __future__ import annotations

import logging

from app.business_query.definitions import BundleValidationError, InvalidBundleIndexError
from app.business_query.outcomes import (
    Answered,
    BusinessQueryOutcome,
    ClarificationRequired,
    Denied,
    Incomplete,
    Unsupported,
    serialize_business_query_outcome,
)
from app.business_query.wire.ask_result import AskBusinessQueryResult
from app.business_query.wire.module import PLANNER_TIMEOUT_CONTINUATION
from app.core.ask_errors import (
    EVIDENCE_UNAVAILABLE_MESSAGE,
    INCOMPLETE_ANSWER_MESSAGE,
)
from app.rag.provenance.record_links import extract_record_links_from_record_refs
from app.services.ask_v2_reason_copy import copy_for_reason
from app.services.retained_evidence import RetentionMismatchError, retained_members

logger = logging.getLogger(__name__)

_UNSUPPORTED_ANSWER = "I can't answer that from the business records I can look up."

# Failures of composing or running the module that every Business Query
# caller turns into the same incomplete answer instead of a crash.
BQ_COMPOSITION_ERRORS: tuple[type[Exception], ...] = (
    InvalidBundleIndexError,
    BundleValidationError,
    OSError,
    ValueError,
    TypeError,
    RuntimeError,
)
UNSUPPORTED_MESSAGE = "that isn't available to ask here"
_RECORD_DATABASE_IDENTITY = "mcp_record"


def denied_capability_result() -> AskBusinessQueryResult:
    return AskBusinessQueryResult(
        disposition="denied",
        answer_text="",
        completion_status=None,
        sql_stop_reason=None,
        model=None,
        input_tokens=None,
        output_tokens=None,
        raise_capability_unavailable=True,
        business_query=serialize_business_query_outcome(Denied()),
    )


def evidence_unavailable_result() -> AskBusinessQueryResult:
    return AskBusinessQueryResult(
        disposition="incomplete",
        answer_text=EVIDENCE_UNAVAILABLE_MESSAGE,
        completion_status="incomplete",
        sql_stop_reason="evidence_unavailable",
        model=None,
        input_tokens=None,
        output_tokens=None,
        raise_capability_unavailable=False,
        business_query=serialize_business_query_outcome(
            Incomplete(reason_code="evidence_unavailable")
        ),
    )


def request_conflict_result(*, correlation_id: str) -> AskBusinessQueryResult:
    """The attempt journal refused a replayed or still-in-flight id. That is
    the dedupe working, so the refusal must not read as a planning failure."""
    logger.warning("business query attempt conflict correlation_id=%s", correlation_id)
    return AskBusinessQueryResult(
        disposition="incomplete",
        answer_text=copy_for_reason("request_conflict"),
        completion_status="incomplete",
        sql_stop_reason="request_conflict",
        model=None,
        input_tokens=None,
        output_tokens=None,
        raise_capability_unavailable=False,
        business_query=serialize_business_query_outcome(Incomplete(reason_code="request_conflict")),
    )


def composition_failed_result(exc: Exception, *, correlation_id: str) -> AskBusinessQueryResult:
    """The module could not be composed or run. Only the exception TYPE NAME
    reaches the log: the message can carry bundle content or file paths that
    must never leave this process."""
    logger.warning(
        "business query composition failed correlation_id=%s error=%s",
        correlation_id,
        type(exc).__name__,
    )
    return AskBusinessQueryResult(
        disposition="incomplete",
        answer_text=INCOMPLETE_ANSWER_MESSAGE,
        completion_status="incomplete",
        sql_stop_reason="adapter_invalid",
        model=None,
        input_tokens=None,
        output_tokens=None,
        raise_capability_unavailable=False,
        business_query=serialize_business_query_outcome(Incomplete(reason_code="adapter_invalid")),
    )


_denied_capability_result = denied_capability_result
_evidence_unavailable_result = evidence_unavailable_result


def map_outcome(
    outcome: BusinessQueryOutcome,
    *,
    shadow: bool,
    evidence_sealed: bool,
) -> AskBusinessQueryResult:
    if isinstance(outcome, Answered):
        retained = retained_members(outcome)
        wire_bq = (
            serialize_business_query_outcome(outcome, evidence_sealed=True)
            if evidence_sealed
            else None
        )
        if wire_bq is not None and retained:
            if len(retained) != len(wire_bq.envelopes):
                raise RetentionMismatchError(
                    f"member count mismatch: {len(retained)} != {len(wire_bq.envelopes)}"
                )
            for m, env in zip(retained, wire_bq.envelopes):
                if m.answer_query_id != env.answer_query_id:
                    raise RetentionMismatchError(
                        f"receipt AQID mismatch: {m.answer_query_id} != {env.answer_query_id}"
                    )
        result = AskBusinessQueryResult(
            disposition="answered",
            answer_text=outcome.answer_text,
            completion_status="complete",
            sql_stop_reason=None,
            model=None,
            input_tokens=None,
            output_tokens=None,
            raise_capability_unavailable=False,
            record_links=extract_record_links_from_record_refs(outcome.record_refs),
            plan=outcome.plan,
            scope_fingerprint=outcome.scope_fingerprint,
            business_query=wire_bq,
        )
        object.__setattr__(result, "retained_members", retained)
    elif isinstance(outcome, ClarificationRequired):
        result = AskBusinessQueryResult(
            disposition="clarification_required",
            answer_text=outcome.question,
            completion_status="complete",
            # A stall-born clarification interrupted its planner; keep the timeout signal.
            sql_stop_reason=(
                "timeout" if outcome.continuation == PLANNER_TIMEOUT_CONTINUATION else None
            ),
            model=None,
            input_tokens=None,
            output_tokens=None,
            raise_capability_unavailable=False,
            disambiguation=outcome.disambiguation,
            business_query=serialize_business_query_outcome(outcome),
        )
    elif isinstance(outcome, Unsupported):
        result = AskBusinessQueryResult(
            disposition="unsupported",
            answer_text=(
                outcome.message
                if outcome.reason_code
                in {"member_not_found", "value_not_found", "unsupported_operator"}
                else _UNSUPPORTED_ANSWER
            ),
            completion_status="complete",
            sql_stop_reason=None,
            model=None,
            input_tokens=None,
            output_tokens=None,
            raise_capability_unavailable=False,
            business_query=serialize_business_query_outcome(outcome),
        )
    elif isinstance(outcome, Incomplete):
        result = AskBusinessQueryResult(
            disposition="incomplete",
            answer_text=INCOMPLETE_ANSWER_MESSAGE,
            completion_status="incomplete",
            sql_stop_reason=outcome.reason_code,
            model=None,
            input_tokens=None,
            output_tokens=None,
            raise_capability_unavailable=False,
            business_query=serialize_business_query_outcome(outcome),
        )
    elif isinstance(outcome, Denied):
        result = AskBusinessQueryResult(
            disposition="denied",
            answer_text="",
            completion_status=None,
            sql_stop_reason=outcome.reason_code,
            model=None,
            input_tokens=None,
            output_tokens=None,
            raise_capability_unavailable=True,
            business_query=serialize_business_query_outcome(outcome),
        )
    else:
        raise TypeError(f"unexpected BusinessQueryOutcome: {type(outcome)!r}")

    if shadow:
        shadow_result = AskBusinessQueryResult(
            disposition="shadowed",
            answer_text="",
            completion_status=None,
            # Answered and ClarificationRequired are valid shadow outcomes but
            # intentionally do not carry a terminal reason code. Preserve the
            # reason for terminal incomplete/unsupported/denied outcomes
            # without making the union pretend every variant has that field.
            sql_stop_reason=getattr(outcome, "reason_code", None),
            model=result.model,
            provider=result.provider,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            raise_capability_unavailable=True,
            # Shadow never exposes the sealed deterministic result. Keep the
            # normalized plan available for the existing PII audit without
            # invoking a second presentation model over result rows.
            plan=result.plan,
        )
        object.__setattr__(shadow_result, "retained_members", ())
        return shadow_result
    return result


_map_outcome = map_outcome
