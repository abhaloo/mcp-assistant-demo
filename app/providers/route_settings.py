"""Provider-neutral model routing by purpose."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.providers.model_purpose import ModelPurpose
from app.providers.route_policy import RouteContext, get_route_policy


@dataclass(frozen=True)
class RouteTarget:
    """Resolved deployment name before provider dispatch."""

    deployment: str


def resolve_model_route(
    purpose: ModelPurpose,
    question: str | None = None,
) -> RouteTarget:
    """Pick a deployment for ``purpose`` without binding credentials."""
    resolved = get_route_policy().resolve(purpose, RouteContext(question=question))
    return RouteTarget(deployment=resolved.deployment)


def route_override_for(purpose: str) -> str | None:
    """Active route id override for ``purpose``, or None to use the catalog default."""
    try:
        ModelPurpose(purpose)
    except ValueError:
        return None
    return getattr(settings, f"ask_model_route_{purpose}", "") or None


def escalation_override_for(purpose: str, reason: str) -> str | None:
    return getattr(settings, f"ask_model_route_{purpose}_{reason}", "") or None
