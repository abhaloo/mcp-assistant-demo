"""User-facing copy for every Ask AI v2 terminal reason code.

One refusal sentence for many different reasons tells the person nothing. A
locked door, a capability the deployment does not have, and a field that is not
recorded all need different words, because each one implies a different next
step. The module already computes a closed reason code for every refusal; this
is the single place that turns one into copy.

Codes come from the Business Query outcome vocabularies in
``app.business_query.outcomes`` (``Unsupported``, ``Incomplete``, ``Denied``)
plus the capability outcome raised by the answer ladder.
"""

from __future__ import annotations

from app.core.ask_errors import DOCUMENT_UNAVAILABLE_MESSAGE
from app.core.errors import CAPABILITY_UNAVAILABLE_MESSAGE

__all__ = ["REASON_FAMILY", "copy_for_reason", "family_for_reason"]

# What the person should understand from the refusal. The panel uses this to
# choose presentation: a denial is not an error and must not be styled as one.
_PERMISSION = "permission"
_CAPABILITY = "capability"
_COVERAGE = "coverage"
_TRANSIENT = "transient"

REASON_FAMILY: dict[str, str] = {
    # Not permitted for this account.
    "policy_denied": _PERMISSION,
    "cursor_scope_mismatch": _PERMISSION,
    # The deployment cannot express the question at all.
    "capability_disabled": _CAPABILITY,
    "unsupported_operator": _CAPABILITY,
    "measure_filter_unsupported": _CAPABILITY,
    "optional_join_unsupported": _CAPABILITY,
    "fanout_unsafe": _CAPABILITY,
    "no_join_path": _CAPABILITY,
    "unsupported_relative_period": _CAPABILITY,
    "invalid_business_timezone": _CAPABILITY,
    "capability_unavailable": _CAPABILITY,
    "document_unavailable": _CAPABILITY,
    "result_page_disabled": _CAPABILITY,
    # The question names a field or grouping this system has no member for.
    # A vocabulary gap, not a missing record — the person should rephrase,
    # not re-check a spelling.
    "member_not_found": _CAPABILITY,
    # The data or definition needed is not recorded here.
    "value_not_found": _COVERAGE,
    "grain_unexpressible": _COVERAGE,
    "period_dimension_missing": _COVERAGE,
    "detail_unavailable": _COVERAGE,
    # The turn stopped part-way and may succeed on a retry.
    "budget": _TRANSIENT,
    "timeout": _TRANSIENT,
    "no_progress": _TRANSIENT,
    "adapter_invalid": _TRANSIENT,
    "request_conflict": _TRANSIENT,
    "cursor_expired": _TRANSIENT,
    "cursor_invalid": _TRANSIENT,
    "continuation_unavailable": _TRANSIENT,
    "continuation_rejected": _TRANSIENT,
    "continuation_expired": _TRANSIENT,
    "model_route_unavailable": _TRANSIENT,
    "upstream_circuit_open": _TRANSIENT,
    "conversation_store_unavailable": _TRANSIENT,
    "transcript_store_busy": _TRANSIENT,
}

_REASON_COPY: dict[str, str] = {
    "policy_denied": (
        "Your account does not have access to that. Ask an administrator if you need it."
    ),
    "cursor_scope_mismatch": (
        "Those results belong to a different question. Ask again to get a fresh set."
    ),
    "capability_disabled": (
        "That kind of question is switched off in this system. It is not a permission problem."
    ),
    "unsupported_operator": (
        "I cannot compare that field the way the question asks. "
        "It is stored as text here, so I cannot treat it as a number or a range."
    ),
    "measure_filter_unsupported": "I cannot filter on a calculated total in this system yet.",
    "optional_join_unsupported": "I cannot combine those two record types in this system yet.",
    "fanout_unsafe": (
        "That question would join records in a way that double-counts, so I did not run it."
    ),
    "no_join_path": "Those two record types are not connected in a way I can query.",
    "unsupported_relative_period": "I cannot work out that date range. Give exact dates instead.",
    "invalid_business_timezone": "The business time zone is not configured, so I cannot use dates.",
    "capability_unavailable": CAPABILITY_UNAVAILABLE_MESSAGE,
    "document_unavailable": DOCUMENT_UNAVAILABLE_MESSAGE,
    "result_page_disabled": "Paging through results is not available in Ask AI.",
    # The producing layer words this one itself; its own message is passed as
    # the fallback and wins unless a deployment overrides it here.
    "continuation_unavailable": "I lost track of that earlier answer. Please ask again.",
    "continuation_rejected": (
        "That choice can no longer be used. Ask the question again to get fresh choices."
    ),
    "continuation_expired": "That question has expired. Ask it again to get fresh choices.",
    "model_route_unavailable": "The answering model is not available right now. Please try again.",
    "upstream_circuit_open": "The answering service is recovering. Please try again shortly.",
    "conversation_store_unavailable": (
        "I could not reach the conversation history. Please try again."
    ),
    "transcript_store_busy": "The conversation history is busy. Please try again.",
    "member_not_found": (
        "I do not have a field or grouping by that name. "
        "Try asking with one of the measures or fields this system records."
    ),
    "value_not_found": (
        "I can search that field, but nothing matches that name or number. "
        "Check the spelling, or it may not be recorded here."
    ),
    "grain_unexpressible": ("I cannot report that at the level of detail the question asks for."),
    "period_dimension_missing": "Those records carry no date I can group or filter by.",
    "detail_unavailable": (
        "That detail is not recorded in this system, so I have nothing to report for it."
    ),
    "budget": "That question needed more work than one turn allows. Try narrowing it.",
    "timeout": "That took too long to finish. Try again in a moment, or narrow the question.",
    "no_progress": "I could not make progress on that question.",
    "adapter_invalid": "I could not build a safe query for that question.",
    "request_conflict": (
        "That request clashed with one already handled or still running. "
        "Ask the question again for a fresh answer."
    ),
    "cursor_expired": "Those results have expired. Ask the question again.",
    "cursor_invalid": "I cannot continue from that page of results. Ask the question again.",
}

# Says only what is known: the turn did not finish and no answer is claimed.
_FALLBACK_COPY = "I could not complete that question, and I have no answer to give for it."


def family_for_reason(reason_code: str | None) -> str:
    """Presentation family for a reason code. Unknown codes read as transient."""
    if not reason_code:
        return _TRANSIENT
    return REASON_FAMILY.get(reason_code, _TRANSIENT)


def copy_for_reason(reason_code: str | None, fallback: str | None = None) -> str:
    """Copy for a reason code.

    ``fallback`` carries a message the producing layer already wrote. It is used
    only when the code has no entry here, so a new code added upstream degrades
    to that layer's own words rather than to silence.
    """
    if reason_code and reason_code in _REASON_COPY:
        return _REASON_COPY[reason_code]
    return fallback or _FALLBACK_COPY
