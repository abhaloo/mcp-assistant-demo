"""Fail-closed OpenRouter provider routing controls for openai_compat requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.providers.model_registry import PolicyViolationError
from app.providers.reasoning_effort_policy import OPENROUTER_EFFORTS

# Resolved-status vocabulary for the OpenRouter SQL estimand -- the actual
# guard logic that reads these lives in app.experiments.estimand_guards;
# these two constants and the error class below stay here because they describe
# this provider's own wire-level estimand vocabulary, not eval-campaign
# orchestration.
OPENROUTER_SQL_ESTIMAND_SUPPORTED = "SUPPORTED"
OPENROUTER_SQL_ESTIMAND_UNSUPPORTED = "UNSUPPORTED_FOR_SQL"

# Eval default: pin DeepInfra for deepseek-v4-flash-0731.
EVAL_DEFAULT_ORDER: list[str] = ["DeepInfra"]
ReasoningEffortInput = str
ReasoningEffortWire = str
EVAL_DEFAULT_REASONING_EFFORT: ReasoningEffortWire = "low"


def normalize_reasoning_effort(effort: ReasoningEffortInput) -> ReasoningEffortWire:
    """Map legacy ``max`` to OpenRouter wire value ``xhigh``."""
    if effort == "max":
        return "xhigh"
    return effort


@dataclass(frozen=True)
class OpenRouterControls:
    """Wire-level OpenRouter provider routing constraints."""

    zdr: bool | None
    data_collection: Literal["allow", "deny"]
    allow_fallbacks: bool
    require_parameters: bool
    order: list[str]
    only: list[str] | None
    profile: Literal["production", "eval"]
    reasoning_effort: ReasoningEffortWire | None = None
    catalog_declared_effort: ReasoningEffortWire | None = None
    catalog_supported_efforts: frozenset[str] | None = None


def default_eval_controls(
    *,
    reasoning_effort: ReasoningEffortInput = EVAL_DEFAULT_REASONING_EFFORT,
    provider_order: list[str] | None = None,
) -> OpenRouterControls:
    """Fail-closed eval profile for B0–B2 (ZDR optional; collection denied; pin + no fallbacks).

    ``provider_order`` defaults to EVAL_DEFAULT_ORDER (DeepInfra) -- pass the arm
    profile's own ``provider_order`` for a candidate hosted elsewhere.
    """
    resolved_order = provider_order if provider_order is not None else EVAL_DEFAULT_ORDER
    return OpenRouterControls(
        zdr=None,
        data_collection="deny",
        allow_fallbacks=False,
        require_parameters=True,
        order=list(resolved_order),
        only=list(resolved_order),
        profile="eval",
        reasoning_effort=normalize_reasoning_effort(reasoning_effort),
    )


def _policy_error(controls: OpenRouterControls, detail: str) -> PolicyViolationError:
    return PolicyViolationError(
        purpose="openrouter_wire",
        credential_source="openrouter",
        environment=controls.profile,
        model="",
        detail=detail,
    )


def _has_provider_pin(controls: OpenRouterControls) -> bool:
    if controls.order:
        return True
    return bool(controls.only)


def assert_openrouter_controls(controls: OpenRouterControls) -> None:
    """Validate controls for the configured profile. Raises before any network call."""
    if controls.profile == "production":
        if controls.zdr is not True:
            raise _policy_error(controls, "production OpenRouter controls require zdr=True")
        if controls.data_collection != "deny":
            raise _policy_error(
                controls,
                'production OpenRouter controls require data_collection="deny"',
            )
        if controls.allow_fallbacks is not False:
            raise _policy_error(
                controls,
                "production OpenRouter controls require allow_fallbacks=False",
            )
        if controls.require_parameters is not True:
            raise _policy_error(
                controls,
                "production OpenRouter controls require require_parameters=True",
            )
        if not _has_provider_pin(controls):
            raise _policy_error(
                controls,
                "production OpenRouter controls require non-empty order or only",
            )
        if controls.reasoning_effort is not None:
            if controls.catalog_declared_effort is None:
                raise _policy_error(
                    controls,
                    "production OpenRouter controls require reasoning_effort=None",
                )
            if controls.reasoning_effort != controls.catalog_declared_effort:
                raise _policy_error(
                    controls,
                    "production OpenRouter reasoning_effort must match catalog_declared_effort "
                    f"({controls.catalog_declared_effort!r})",
                )
            supported = controls.catalog_supported_efforts
            if supported is None or controls.reasoning_effort not in supported:
                raise _policy_error(
                    controls,
                    "production OpenRouter reasoning_effort must be in catalog_supported_efforts",
                )
        return

    if controls.profile == "eval":
        if controls.allow_fallbacks is not False:
            raise _policy_error(controls, "eval OpenRouter controls require allow_fallbacks=False")
        if controls.require_parameters is not True:
            raise _policy_error(
                controls,
                "eval OpenRouter controls require require_parameters=True",
            )
        if controls.data_collection != "deny":
            raise _policy_error(
                controls,
                'eval OpenRouter controls require data_collection="deny"',
            )
        if not _has_provider_pin(controls):
            raise _policy_error(
                controls,
                "eval OpenRouter controls require non-empty order or only",
            )
        if controls.reasoning_effort is None or controls.reasoning_effort not in OPENROUTER_EFFORTS:
            raise _policy_error(
                controls,
                "eval OpenRouter controls require reasoning_effort in "
                f"{sorted(OPENROUTER_EFFORTS)}",
            )
        return

    raise _policy_error(controls, f"unknown OpenRouter controls profile: {controls.profile!r}")


def build_provider_extra(controls: OpenRouterControls) -> dict:
    """Build the OpenRouter ``extra_body`` payload for ChatOpenAI."""
    assert_openrouter_controls(controls)

    provider: dict[str, object] = {
        "data_collection": controls.data_collection,
        "allow_fallbacks": controls.allow_fallbacks,
        "require_parameters": controls.require_parameters,
    }
    if controls.zdr is not None:
        provider["zdr"] = controls.zdr
    if controls.order:
        provider["order"] = list(controls.order)
    if controls.only:
        provider["only"] = list(controls.only)

    extra: dict[str, object] = {"provider": provider}
    if controls.reasoning_effort is not None:
        extra["reasoning"] = {"effort": controls.reasoning_effort}
    return extra


class OpenRouterSqlEstimandError(PolicyViolationError):
    """Frozen contract declares the OpenRouter SQL pair unsupported or unresolved."""


def redacted_route_evidence(
    *,
    model: str,
    provider_header: str | None,
    controls: OpenRouterControls,
) -> dict[str, str | bool | None]:
    """Return log-safe route evidence: model, provider header, control booleans only."""
    return {
        "model": model,
        "provider": provider_header or "",
        "zdr": controls.zdr,
        "data_collection_deny": controls.data_collection == "deny",
        "allow_fallbacks": controls.allow_fallbacks,
        "require_parameters": controls.require_parameters,
        "has_order": bool(controls.order),
        "has_only": bool(controls.only),
        "reasoning_effort": controls.reasoning_effort,
    }
