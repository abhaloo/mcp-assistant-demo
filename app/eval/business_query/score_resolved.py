"""Score a resolved execution event against the frozen oracle and spec."""

from __future__ import annotations

from app.business_query.seal.events import (
    BusinessQueryExecutionEvent,
    EventAccessContext,
    ExecutionEventResolutionError,
    ExecutionEventResolver,
)
from app.eval.business_query.readiness import EvaluationReadinessError
from app.eval.business_query.scorer import CaseScore, CaseScoringSpec, score_case, score_execution


def _score_answered_total_count(
    case: dict, oracle: dict, event: BusinessQueryExecutionEvent
) -> CaseScore:
    """Documented total-row-count oracle (bq-15): spec matching cannot judge a
    capped list, but the evidence still comes from the integrity-checked event."""
    return score_case(
        case,
        oracle,
        "answered",
        list(event.result_rows),
        total_row_count=event.total_row_count,
        truncated=event.truncated,
        actual_member_schema=tuple(m.name for m in event.result_members),
        scoring_spec=None,
        gate=False,
    )


async def score_resolved_execution(
    case: dict,
    oracle: dict | None,
    scoring_spec: CaseScoringSpec | None,
    run: dict,
    *,
    resolver: ExecutionEventResolver,
    access: EventAccessContext,
) -> CaseScore:
    if oracle is None and case["draft_expected"] != "answered":
        return score_case(
            case,
            None,
            run["outcome"],
            run.get("rows"),
            total_row_count=run.get("total_row_count"),
            truncated=run.get("truncated"),
            actual_member_schema=run.get("result_member_schema"),
            scoring_spec=None,
            gate=False,
        )
    if run.get("outcome") != "answered":
        return score_case(
            case,
            oracle,
            run["outcome"],
            run.get("rows"),
            total_row_count=run.get("total_row_count"),
            truncated=run.get("truncated"),
            actual_member_schema=run.get("result_member_schema"),
            scoring_spec=scoring_spec,
            gate=True,
        )
    receipt = run.get("receipt")
    answer_query_id = receipt.get("answer_query_id") if isinstance(receipt, dict) else None
    if not answer_query_id:
        raise EvaluationReadinessError("answered outcome lacks an Answer Query ID")
    try:
        event = await resolver.resolve(answer_query_id, access)
    except (ExecutionEventResolutionError, ValueError, TypeError) as exc:
        raise EvaluationReadinessError(
            "execution event is structurally invalid or unresolvable"
        ) from exc
    if oracle is None:
        raise EvaluationReadinessError("answered case lacks an independent oracle")
    if case.get("scoring") == "total_count":
        return _score_answered_total_count(case, oracle, event)
    if scoring_spec is None:
        raise EvaluationReadinessError("answered case lacks a frozen scoring spec")
    return score_execution(case, scoring_spec, oracle, event)
