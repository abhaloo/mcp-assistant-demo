"""Fail-closed operator arming gate for live model spend."""

from __future__ import annotations

import os

from app.experiments.live_errors import (
    LiveAskSmokeError,
    LiveRagEvalError,
    LiveRouteConformanceError,
    LiveSqlEvalError,
    LiveToolSmokeError,
)


class NotArmedError(
    LiveRagEvalError,
    LiveAskSmokeError,
    LiveRouteConformanceError,
    LiveSqlEvalError,
    LiveToolSmokeError,
):
    """The operator did not explicitly arm live spend."""


def require_armed(env_var: str, *, purpose: str) -> None:
    """Refuse unless the operator armed this spend explicitly.

    Exact-match "1": a fail-closed gate must not widen to truthy spellings.
    """
    if os.environ.get(env_var) != "1":
        raise NotArmedError(f"{purpose} refused: set {env_var}=1 to arm spend")
