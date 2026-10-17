"""Document branch: dispatch the admitted catalog handler."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import get_runtime

from app.business_query.loop.context import LoopContext
from app.business_query.loop.state import LoopState
from app.core.errors import DeadlineExpiredError
from app.tools.contracts import ToolContext, ToolFailure


async def document_search_node(state: LoopState) -> dict[str, Any]:
    ctx = get_runtime(LoopContext).context
    if not state.get("run_document"):
        return {}
    call = state.get("document_call")
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
        result = await ctx.catalog.execute_document(call, tool_ctx)
    except DeadlineExpiredError:
        # The turn deadline ends this branch only; a committed BQ result stays.
        result = ToolFailure(
            invocation_id=call.invocation_id, status="timeout", code="stage_timeout"
        )
    return {"document_result": result}
