"""Scale Ask-turn clocks by ``settings.ask_budget_multiplier``.

Base literals stay on Settings (25 s, 18 s, …). Consumption sites call these
helpers so a live overlay can raise every Ask cap in one step. Token and
health-probe budgets are out of scope.
"""

from __future__ import annotations

from app.config import EffectiveRouteSettings, effective_route_settings, settings


def ask_budget_multiplier() -> float:
    value = float(settings.ask_budget_multiplier)
    if value <= 0:
        raise ValueError("ask_budget_multiplier must be greater than 0")
    return value


def scale_ask_seconds(seconds: float) -> float:
    return seconds * ask_budget_multiplier()


def scale_ask_ms(ms: int) -> int:
    return int(round(ms * ask_budget_multiplier()))


def scale_ask_optional_seconds(seconds: float | None) -> float | None:
    if seconds is None:
        return None
    return scale_ask_seconds(seconds)


def scaled_route_settings() -> EffectiveRouteSettings:
    """The route snapshot with the Ask clocks scaled as a turn receives them."""
    snapshot = effective_route_settings()
    return snapshot.model_copy(
        update={
            "ask_max_deadline_ms": scale_ask_ms(snapshot.ask_max_deadline_ms),
            "ask_planner_step_ceiling_seconds": scale_ask_optional_seconds(
                snapshot.ask_planner_step_ceiling_seconds
            ),
        }
    )
