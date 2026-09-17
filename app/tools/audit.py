"""Audit helper for ToolExecution OTel attributes."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import Span

from app.tools.types import ToolAuditEvent


def record_tool_execution_audit(event: ToolAuditEvent, span: Span | None = None) -> None:
    """Stamp tool_execution.* attrs on the active (or given) span when recording."""
    target = span if span is not None else trace.get_current_span()
    if not target.is_recording():
        return
    target.set_attribute("tool_execution.name", event.tool_name)
    target.set_attribute("tool_execution.outcome", event.outcome)
    target.set_attribute("tool_execution.duration_ms", event.duration_ms)
    target.set_attribute("tool_execution.arg_keys", ",".join(event.arg_keys))
