"""Whole-set authorization and the canonical ordinal execution algorithm."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Answered, BusinessQueryOutcome
from app.business_query.ports import (
    BusinessProgressSink,
    ExecuteAndPresentStage,
    ResolveValuesStage,
)
from app.business_query.seal.evidence import BusinessQueryEvidenceContext
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace
from app.core.turn_budget import TurnBudget


@dataclass(frozen=True)
class AuthorizedSetExecution:
    request: BusinessQueryRequest
    scoped_plans: list[ScopedPlan]
    bundle: DefinitionBundle
    progress: BusinessProgressSink | None
    evidence: BusinessQueryEvidenceContext | None
    trace: QueryTrace
    turn_budget: TurnBudget


def compose_answers(answers: Sequence[Answered]) -> Answered:
    """The primary carries its companions in call order."""
    primary = answers[0]
    if len(answers) == 1:
        return primary
    return primary.model_copy(update={"companion_answered": tuple(answers[1:])})


async def execute_authorized_set(
    execution: AuthorizedSetExecution,
    *,
    resolve_values: ResolveValuesStage,
    execute_and_present: ExecuteAndPresentStage,
) -> BusinessQueryOutcome:
    """Run resolve → execute → seal for each authorized member in ordinal order."""
    request = execution.request
    scoped_plans = execution.scoped_plans
    bundle = execution.bundle
    progress = execution.progress
    evidence = execution.evidence
    trace = execution.trace
    turn_budget = execution.turn_budget
    answers: list[Answered] = []
    for ordinal, scoped in enumerate(scoped_plans):
        if ordinal > 0:
            turn_budget.check_not_expired()
        ordinal_trace = QueryTrace(correlation_id=request.correlation_id)
        resolved = await resolve_values(
            scoped.plan,
            scoped,
            request,
            bundle,
            evidence=evidence,
            trace=ordinal_trace,
            progress=progress,
            ordinal=ordinal,
            of=len(scoped_plans),
            turn_budget=turn_budget,
        )
        if not isinstance(resolved, tuple):
            ordinal_trace.copy_into(trace)
            return resolved
        _planned, scoped = resolved
        outcome = await execute_and_present(
            request,
            scoped,
            progress=progress,
            evidence=evidence,
            trace=ordinal_trace,
            ordinal=ordinal,
            of=len(scoped_plans),
            turn_budget=turn_budget,
        )
        if not isinstance(outcome, Answered):
            ordinal_trace.copy_into(trace)
            return outcome
        trace.append_sealed_sub_query(
            ordinal_trace,
            answer_query_id=outcome.receipt.answer_query_id,
            plan_fingerprint=outcome.receipt.plan_fingerprint,
            rows_returned=outcome.receipt.row_count,
        )
        answers.append(outcome)
    return compose_answers(answers)
