"""The one entry to the loop. Returns ToolTurnExecution; LangGraph stays inside."""

from __future__ import annotations

import logging
import os

from langgraph.errors import GraphRecursionError

from app.business_query.loop.checkpoint import build_checkpointer
from app.business_query.loop.graph import build_loop_graph, recursion_limit_for
from app.business_query.loop.state import LoopState
from app.business_query.loop.tracing import assert_loop_tracing_off, loop_callbacks
from app.business_query.outcomes import Incomplete
from app.config import settings
from app.core.errors import DeadlineExpiredError
from app.services.business_query_mapping import map_outcome
from app.telemetry.correlation import current_thread_id
from app.tools.turn_contracts import ToolTurnContext, ToolTurnExecution, ToolTurnRequest

logger = logging.getLogger(__name__)


def _timeout_execution() -> ToolTurnExecution:
    return ToolTurnExecution(
        bq=map_outcome(Incomplete(reason_code="timeout"), shadow=False, evidence_sealed=False),
        document=None,
        refusal=None,
    )


async def run_loop_turn(
    request: ToolTurnRequest,
    *,
    context: ToolTurnContext,
) -> ToolTurnExecution:
    assert_loop_tracing_off(settings, os.environ)
    compiled = build_loop_graph().compile(checkpointer=build_checkpointer())
    config = {
        "configurable": {
            "thread_id": current_thread_id() or context.correlation_id,
            "checkpoint_ns": "",
        },
        "recursion_limit": recursion_limit_for(1),
        "callbacks": loop_callbacks(),
    }
    initial: LoopState = {
        "query_type": request.query_type,
        "bq_requested": request.bq_requested,
        "document_call": request.document,
        "bq_call": None,
        "bq_result": None,
        "document_result": None,
        "refusal": None,
        "run_sql": False,
        "run_document": False,
        "execution": None,
        "stop": None,
    }
    execution: ToolTurnExecution | None = None
    try:
        try:
            async for _mode, _chunk in compiled.astream(
                initial,
                config,
                context=context,
                stream_mode=["updates"],
                durability="sync",
            ):
                if _mode == "updates" and isinstance(_chunk, dict):
                    for node_out in _chunk.values():
                        if isinstance(node_out, dict) and node_out.get("execution") is not None:
                            execution = node_out["execution"]
        except GraphRecursionError:
            logger.error(
                "Loop reached recursion_limit=%d; aborting runaway cycle defect",
                config["recursion_limit"],
                exc_info=True,
            )
            return ToolTurnExecution(
                bq=map_outcome(
                    Incomplete(reason_code="adapter_invalid"),
                    shadow=False,
                    evidence_sealed=False,
                ),
                document=None,
                refusal=None,
            )
        except DeadlineExpiredError:
            # Backstop only: each node maps its own deadline to a typed result.
            return _timeout_execution()
        if execution is not None:
            return execution
        return ToolTurnExecution(
            bq=map_outcome(
                Incomplete(reason_code="adapter_invalid"),
                shadow=False,
                evidence_sealed=False,
            ),
            document=None,
            refusal=None,
        )
    finally:
        if context.bq is not None:
            await context.bq.aclose()
