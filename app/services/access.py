"""Access tier resolution with telemetry — shared by JSON and SSE ask paths."""

from __future__ import annotations

from app.rag.access_tiers import get_access_tiers
from app.telemetry import access_control_span
from app.telemetry.helpers import record_access_tiers


def resolve_access_tiers(role: str, permissions: list[str]) -> list[str]:
    with access_control_span(role=role, permissions=permissions) as span:
        tiers = get_access_tiers(role, permissions)
        record_access_tiers(span, tiers)
        return tiers
