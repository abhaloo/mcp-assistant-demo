"""Transport-free ports supplied to readiness checks by the composition root."""

from __future__ import annotations

from typing import Protocol


class QueryRecordSchemaProbe(Protocol):
    """Probe the Query Record State store without exposing its adapter."""

    async def check(self) -> bool: ...
