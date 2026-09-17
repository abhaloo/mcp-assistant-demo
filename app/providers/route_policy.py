"""Production RoutePolicy — resolve ModelPurpose (+ context) to ResolvedModelRoute."""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.model_purpose import PURPOSE_CAPABILITY_REQUIREMENTS, ModelPurpose
from app.providers.production_catalog import (
    EXTERNAL_PROVIDERS,
    ProductionCatalog,
    ProductionRoute,
    StructuredOutputMode,
    assert_capabilities_for_purpose,
    assert_external_allowlist,
    load_production_catalog,
    validate_production_catalog,
)
from app.providers.route_controls import RouteControls
from app.rag.model_router import is_hard_financial


class RouteResolutionError(Exception):
    """Raised when a production route cannot be resolved fail-closed."""


@dataclass(frozen=True)
class RouteContext:
    question: str | None = None
    hard_financial: bool | None = None


@dataclass(frozen=True)
class ResolvedModelRoute:
    route_id: str
    purpose: ModelPurpose
    target_id: str
    deployment: str
    provider: str
    structured_output_mode: StructuredOutputMode
    required_capabilities: frozenset[str]
    reasoning_effort: str | None
    verbosity: str | None
    temperature: float | None
    max_output_tokens: int | None
    request_timeout_s: float | None
    max_retries: int | None
    provider_controls: RouteControls | None
    fallback_route_ids: tuple[str, ...]
    route_reason: str | None
    attested_azure_model_version: str | None
    supported_reasoning_efforts: frozenset[str]


class RoutePolicy:
    """Resolve active production routes from the checked-in catalog + env overrides."""

    def __init__(self, catalog: ProductionCatalog) -> None:
        self._catalog = catalog

    def resolve(
        self,
        purpose: ModelPurpose,
        context: RouteContext | None = None,
    ) -> ResolvedModelRoute:
        ctx = context or RouteContext()
        purpose_key = purpose.value

        if purpose is ModelPurpose.eval:
            raise RouteResolutionError(f"no production route for purpose {purpose_key!r}")

        route_reason: str | None = None
        if purpose is ModelPurpose.sql_agent:
            hard = self._is_hard_financial(ctx)
            if hard:
                route_reason = "hard_financial"
                route_id = self._route_id_for_escalation("sql_agent", "hard_financial")
            else:
                route_id = self._route_id_for_purpose(purpose_key)
        else:
            route_id = self._route_id_for_purpose(purpose_key)

        self._assert_external_route_allowlisted(purpose_key, route_id)
        route = self._get_route(route_id)
        # Planner routes are purpose-specific. Older external classify routes are
        # intentionally reusable across production purposes by the allowlist tests,
        # so preserve that compatibility while preventing a planner route from
        # leaking into another purpose or a non-planner route from being used for
        # record_reasoning when it belongs to a different production capability.
        if route.purpose == "record_reasoning" and purpose_key != "record_reasoning":
            raise RouteResolutionError(
                f"route {route.route_id!r} is for purpose {route.purpose!r}, not {purpose_key!r}"
            )
        if purpose_key == "record_reasoning" and route.purpose not in {
            "record_reasoning",
            "classify",
        }:
            raise RouteResolutionError(
                f"route {route.route_id!r} is for purpose {route.purpose!r}, not {purpose_key!r}"
            )
        target = self._catalog.target(route.target_id)
        try:
            assert_capabilities_for_purpose(
                purpose_key, target, context=f"route resolve for purpose {purpose_key!r}"
            )
        except ValueError as exc:
            raise RouteResolutionError(str(exc)) from exc
        required = PURPOSE_CAPABILITY_REQUIREMENTS[purpose]

        return ResolvedModelRoute(
            route_id=route.route_id,
            purpose=purpose,
            target_id=target.target_id,
            deployment=target.model_id,
            provider=target.provider,
            structured_output_mode=route.structured_output_mode,
            required_capabilities=required,
            reasoning_effort=route.reasoning_effort,
            verbosity=route.verbosity,
            temperature=route.temperature,
            max_output_tokens=route.max_output_tokens,
            request_timeout_s=route.request_timeout_s,
            max_retries=route.max_retries,
            provider_controls=route.provider_controls,
            fallback_route_ids=route.fallback_route_ids,
            route_reason=route_reason,
            attested_azure_model_version=None,
            supported_reasoning_efforts=target.supported_reasoning_efforts,
        )

    @staticmethod
    def _is_hard_financial(ctx: RouteContext) -> bool:
        if ctx.hard_financial is True:
            return True
        if ctx.hard_financial is False:
            return False
        return is_hard_financial(ctx.question or "")

    def _route_id_for_purpose(self, purpose: str) -> str:
        from app.providers.route_settings import route_override_for

        override = route_override_for(purpose)
        if override:
            return override
        try:
            return self._catalog.purpose_defaults[purpose]
        except KeyError as exc:
            raise RouteResolutionError(f"no default route for purpose {purpose!r}") from exc

    def _route_id_for_escalation(self, purpose: str, reason: str) -> str:
        from app.providers.route_settings import escalation_override_for

        override = escalation_override_for(purpose, reason)
        if override:
            return override
        try:
            return self._catalog.escalations[purpose][reason]
        except KeyError as exc:
            raise RouteResolutionError(f"no escalation route for {purpose!r} / {reason!r}") from exc

    def _get_route(self, route_id: str) -> ProductionRoute:
        try:
            return self._catalog.routes[route_id]
        except KeyError as exc:
            raise RouteResolutionError(f"unknown route {route_id!r}") from exc

    def _assert_external_route_allowlisted(self, purpose: str, route_id: str) -> None:
        if route_id not in self._catalog.routes:
            return
        target = self._catalog.target(self._catalog.routes[route_id].target_id)
        if target.provider not in EXTERNAL_PROVIDERS:
            return
        try:
            assert_external_allowlist(
                self._catalog,
                purpose,
                route_id,
                context=f"route resolve for purpose {purpose!r}",
            )
        except ValueError as exc:
            raise RouteResolutionError(str(exc)) from exc


_policy: RoutePolicy | None = None


def _build_route_policy() -> RoutePolicy:
    catalog = load_production_catalog()
    validate_production_catalog(catalog)
    return RoutePolicy(catalog)


def get_route_policy(*, reload: bool = False) -> RoutePolicy:
    global _policy
    if _policy is None or reload:
        _policy = _build_route_policy()
    return _policy


def reset_route_policy_for_tests() -> None:
    """Clear the process-local policy singleton (tests only)."""
    global _policy
    _policy = None
