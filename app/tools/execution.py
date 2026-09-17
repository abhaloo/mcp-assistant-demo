"""ToolExecution choke point: validate → transform → authorize → run → audit."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.tools.types import ToolAuditEvent, ToolDenial, ToolRejection

ValidateFn = Callable[[dict], dict | ToolRejection]
TransformFn = Callable[[dict, Any], dict | ToolRejection]
RunFn = Callable[[dict], str | ToolRejection | ToolDenial]
AuditFn = Callable[[ToolAuditEvent], None]
AuthorizeFn = Callable[[Any, str, dict], bool]


def execute_tool(
    *,
    name: str,
    args: dict,
    principal: Any,
    validate: ValidateFn,
    transform: TransformFn,
    run: RunFn,
    audit: AuditFn,
    authorize: AuthorizeFn | None = None,
    reraise_errors: bool = False,
) -> str:
    """Run one tool through the shared security/observability seam.

    ``validate`` and ``transform`` may return ``ToolRejection`` — either
    short-circuits before ``run``. Optional ``authorize`` denial audits
    ``denied`` and never calls ``run``. If ``run`` returns ``ToolRejection``
    (e.g. post-transform unsafety), audit outcome is ``rejected``, not ``ok``.
    ``ToolDenial`` from ``run`` audits ``denied``. Unexpected exceptions audit
    ``error``; when ``reraise_errors`` is True they propagate after audit, for
    a caller that needs the raw exception once the audit record is written.
    """
    start = time.perf_counter()
    arg_keys = sorted(str(k) for k in args)

    def _emit(outcome: str) -> None:
        audit(
            ToolAuditEvent(
                tool_name=name,
                outcome=outcome,
                duration_ms=(time.perf_counter() - start) * 1000,
                arg_keys=arg_keys,
            )
        )

    try:
        validated = validate(args)
        if isinstance(validated, ToolRejection):
            _emit("rejected")
            return f"Error: {validated.reason}"

        transformed = transform(validated, principal)
        if isinstance(transformed, ToolRejection):
            _emit("rejected")
            return f"Error: {transformed.reason}"

        if authorize is not None and not authorize(principal, name, transformed):
            _emit("denied")
            return "Error: access denied"

        result = run(transformed)
        if isinstance(result, ToolRejection):
            _emit("rejected")
            return f"Error: {result.reason}"
        if isinstance(result, ToolDenial):
            _emit("denied")
            return result.message

        _emit("ok")
        return result
    except Exception as exc:  # noqa: BLE001 — surfaced to model as ToolMessage
        _emit("error")
        if reraise_errors:
            raise
        return f"Error: {exc}"
