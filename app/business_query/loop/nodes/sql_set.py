"""SQL branch: catalog BQ execute, then publish only a committed result."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import get_runtime

from app.business_query.loop.context import LoopContext
from app.business_query.loop.state import LoopState
from app.business_query.outcomes import Incomplete
from app.business_query.wire.ask_result import CommittedBqResult
from app.core.errors import DeadlineExpiredError
from app.services.business_query_mapping import map_outcome
from app.services.business_query_publication import publish_committed_bq
from app.tools.contracts import ToolContext


async def sql_set_node(state: LoopState) -> dict[str, Any]:
    ctx = get_runtime(LoopContext).context
    if not state.get("run_sql"):
        return {}
    call = state.get("bq_call")
    if call is None:
        return {}
    tool_ctx = ToolContext(
        principal=ctx.principal,
        correlation_id=ctx.correlation_id,
        budget=ctx.budget,
        record_context=ctx.record_context,
        origin=ctx.origin,
    )
    try:
        result = await ctx.catalog.execute_bq(call, tool_ctx)
    except DeadlineExpiredError:
        # The turn deadline ends this branch only; a finished document result stays.
        timeout = map_outcome(
            Incomplete(reason_code="timeout"), shadow=False, evidence_sealed=False
        )
        return {"bq_result": timeout}
    if isinstance(result, CommittedBqResult):
        try:
            await publish_committed_bq(result, ctx.progress, ctx.budget)
        except DeadlineExpiredError:
            return {"bq_result": result}
    return {"bq_result": result}
