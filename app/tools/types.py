"""Types for the ToolExecution seam."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolRejection:
    """Fail-closed reject from validate or transform (or post-transform run)."""

    reason: str


@dataclass(frozen=True)
class ToolDenial:
    """Access denial from authorize or mapped RecordAccessDenied (audit: denied)."""

    message: str = "access denied"


@dataclass
class ToolAuditEvent:
    """Scrubbed audit record for one tool invocation."""

    tool_name: str
    outcome: str  # ok | denied | rejected | error
    duration_ms: float
    arg_keys: list[str] = field(default_factory=list)
