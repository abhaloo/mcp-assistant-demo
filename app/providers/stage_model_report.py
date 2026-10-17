"""Stage Model Report types for production Ask model swapability."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.providers.model_purpose import ModelPurpose
from app.providers.route_policy import ResolvedModelRoute

UNUSED_STAGE_SENTINEL = "unused"
FIXED_RESPONSE_MODEL_SENTINEL = "none"

PRODUCTION_PURPOSES: tuple[ModelPurpose, ...] = (
    ModelPurpose.classify,
    ModelPurpose.rag_answer,
    ModelPurpose.sql_agent,
    ModelPurpose.conversation,
    ModelPurpose.record_reasoning,
    ModelPurpose.coordinator,
)


class StageModelEntry(BaseModel):
    """One production purpose's route identity for a single Ask turn."""

    model_config = ConfigDict(extra="forbid")

    purpose: str
    status: str = Field(description="'used' when the purpose invoked a model; 'unused' otherwise")
    route_id: str
    requested_deployment: str
    reasoning_effort: str | None = None
    attested_or_actual_model: str | None = None


class StageModelReport(BaseModel):
    """Additive per-purpose model report for one Ask answer."""

    model_config = ConfigDict(extra="forbid")

    classify: StageModelEntry
    rag_answer: StageModelEntry
    sql_agent: StageModelEntry
    conversation: StageModelEntry
    record_reasoning: StageModelEntry
    coordinator: StageModelEntry


def _unused_entry(purpose: str) -> StageModelEntry:
    return StageModelEntry(
        purpose=purpose,
        status="unused",
        route_id=UNUSED_STAGE_SENTINEL,
        requested_deployment=UNUSED_STAGE_SENTINEL,
        reasoning_effort=None,
        attested_or_actual_model=None,
    )


def _used_entry(route: ResolvedModelRoute) -> StageModelEntry:
    attested_or_actual = route.attested_azure_model_version or route.deployment
    return StageModelEntry(
        purpose=route.purpose.value,
        status="used",
        route_id=route.route_id,
        requested_deployment=route.deployment,
        reasoning_effort=route.reasoning_effort,
        attested_or_actual_model=attested_or_actual,
    )


def producer_model_from_route(route: ResolvedModelRoute) -> str:
    """Final-producer identity preferring attested/actual over requested deployment."""
    return route.attested_azure_model_version or route.deployment


@dataclass
class StageModelAccumulator:
    """Mutable per-turn capture of routes invoked at model call sites."""

    _routes: dict[str, ResolvedModelRoute] = field(default_factory=dict)

    def record_used(self, route: ResolvedModelRoute) -> None:
        self._routes[route.purpose.value] = route

    def build_report(self) -> StageModelReport:
        entries: dict[str, StageModelEntry] = {}
        for purpose in PRODUCTION_PURPOSES:
            key = purpose.value
            route = self._routes.get(key)
            entries[key] = _used_entry(route) if route is not None else _unused_entry(key)
        return StageModelReport(**entries)

    def producer_model(
        self,
        *,
        purpose: ModelPurpose | None = None,
        fixed: bool = False,
    ) -> str:
        if fixed:
            return FIXED_RESPONSE_MODEL_SENTINEL
        if purpose is None:
            raise ValueError("final producer purpose is required for model-backed answers")
        route = self._routes.get(purpose.value)
        if route is None:
            raise ValueError(f"no captured route for final producer purpose {purpose.value!r}")
        return producer_model_from_route(route)
