"""Typed wire-control blocks a catalog route may pin, parsed at the YAML seam.

Foreign shapes normalize ONCE, here (ADR 0023). ``extra="forbid"`` means a
misspelt key fails catalog load -- which runs at startup -- instead of
silently dropping a data-boundary pin on an external provider.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class NoRouteControls(BaseModel):
    """A route on a provider with no wire-level pins. Forbids every key."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class OpenRouterRouteControls(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    zdr: bool | None = None
    data_collection: Literal["allow", "deny"] | None = None
    allow_fallbacks: bool | None = None
    require_parameters: bool | None = None
    order: tuple[str, ...] | None = None
    only: tuple[str, ...] | None = None


class DeepSeekRouteControls(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thinking_enabled: bool = True


RouteControls = OpenRouterRouteControls | DeepSeekRouteControls | NoRouteControls
