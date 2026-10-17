"""Prepare the BQ plan once, then admit the complete selection."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import get_runtime

from app.business_query.loop.context import LoopContext
from app.business_query.loop.state import LoopState
from app.business_query.outcomes import Incomplete
from app.business_query.plan import PlannedQuerySet
from app.core.errors import DeadlineExpiredError
from app.services.business_query_mapping import map_outcome
from app.tools.contracts import BusinessQueryInput, BusinessQueryInvocation, CatalogRefusal


def _timeout_result() -> Any:
    return map_outcome(Incomplete(reason_code="timeout"), shadow=False, evidence_sealed=False)


async def prepare_bq_node(state: LoopState) -> dict[str, Any]:
    ctx = get_runtime(LoopContext).context
    if not state.get("bq_requested") or ctx.bq is None:
        return {"run_sql": False, "bq_call": None}
    try:
        ctx.budget.check_not_expired()
        prepared = await ctx.bq.prepare()
    except DeadlineExpiredError:
        return {"bq_result": _timeout_result(), "run_sql": False, "bq_call": None}
    if isinstance(prepared, PlannedQuerySet):
        call = BusinessQueryInvocation(
            name="business_query",
            version=1,
            invocation_id="bq-1",
            arguments=BusinessQueryInput(
                primary=prepared.primary,
                companions=prepared.companions,
            ),
        )
        return {"bq_call": call, "run_sql": True}
    return {"bq_result": prepared, "run_sql": False, "bq_call": None}


async def admit_selection_node(state: LoopState) -> dict[str, Any]:
    ctx = get_runtime(LoopContext).context
    raw: list[dict[str, object]] = []
    bq_call = state.get("bq_call")
    document_call = state.get("document_call")
    if bq_call is not None:
        raw.append(bq_call.model_dump())
    if document_call is not None:
        raw.append(document_call.model_dump())
    if not raw:
        return {"run_sql": False, "run_document": False, "refusal": None}
    admitted = ctx.catalog.admit(raw)
    if isinstance(admitted, CatalogRefusal):
        return {
            "refusal": admitted,
            "run_sql": False,
            "run_document": False,
            "bq_call": None,
        }
    names = {call.name for call in admitted.invocations}
    return {
        "refusal": None,
        "run_sql": "business_query" in names and bq_call is not None,
        "run_document": "document_search" in names and document_call is not None,
    }
