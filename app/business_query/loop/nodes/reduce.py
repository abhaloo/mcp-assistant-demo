"""Join branch results into one turn execution."""

from __future__ import annotations

from typing import Any

from app.business_query.loop.state import LoopState
from app.tools.turn_contracts import ToolTurnExecution


def reduce_node(state: LoopState) -> dict[str, Any]:
    execution = ToolTurnExecution(
        bq=state.get("bq_result"),
        document=state.get("document_result"),
        refusal=state.get("refusal"),
    )
    return {"execution": execution, "stop": "terminal"}
