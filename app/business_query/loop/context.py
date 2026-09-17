"""Per-request objects the nodes need and the checkpoint must never hold."""

from __future__ import annotations

from app.tools.turn_contracts import ToolTurnContext as LoopContext

__all__ = ["LoopContext"]
