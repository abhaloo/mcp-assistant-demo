"""Purpose/environment eligibility gate before credential binding."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import ModelSpec, PolicyViolationError
from app.providers.openrouter_controls import (
    OpenRouterControls,
    assert_openrouter_controls,
    default_eval_controls,
)
from app.providers.production_catalog import load_production_catalog

Environment = Literal["development", "production"]

_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "model_eligibility_policy.yaml"


def resolve_openrouter_controls_for_env(
    controls: OpenRouterControls | None,
    environment: Environment,
) -> OpenRouterControls:
    """Require wire-level OpenRouter controls in every environment.

    Production: explicit production-profile controls (ZDR HOLD may still block
    live eligibility separately). Non-production: default eval profile when omitted.
    """
    if controls is None:
        if environment == "production":
            raise PolicyViolationError(
                purpose="openrouter_wire",
                credential_source="openrouter",
                environment=environment,
                model="",
                detail="OpenRouter production requires explicit wire-level controls",
            )
        controls = default_eval_controls()

    if environment == "production" and controls.profile != "production":
        raise PolicyViolationError(
            purpose="openrouter_wire",
            credential_source="openrouter",
            environment=environment,
            model="",
            detail="OpenRouter production requires profile='production'",
        )

    assert_openrouter_controls(controls)
    return controls


@lru_cache(maxsize=1)
def _load_policy() -> dict[str, Any]:
    with _POLICY_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def allowed_credentials(purpose: ModelPurpose, environment: Environment) -> frozenset[str]:
    """Return credential sources permitted for ``purpose`` in ``environment``."""
    policy = _load_policy()
    purpose_row = policy["purposes"].get(purpose.value, {})
    explicit = purpose_row.get("credentials")
    if explicit is not None:
        return frozenset(explicit)

    creds = set(policy["base_credentials"])
    env_row = policy["environments"][environment]
    creds.update(env_row.get("additional_credentials", []))
    if environment == "production":
        catalog = load_production_catalog()
        for route_id in catalog.external_purpose_allowlist.get(purpose.value, ()):
            route = catalog.routes.get(route_id)
            if route is None:
                continue
            target = catalog.targets.get(route.target_id)
            if target is not None:
                creds.add(target.credential_source)
    return frozenset(creds)


def assert_eligible(
    *,
    purpose: ModelPurpose,
    spec: ModelSpec,
    environment: Environment,
    controls: OpenRouterControls | None = None,
) -> None:
    """Raise ``PolicyViolationError`` when ``spec`` is not eligible for ``purpose``."""
    allowed = allowed_credentials(purpose, environment)
    if spec.credential_source not in allowed:
        raise PolicyViolationError(
            purpose=purpose.value,
            credential_source=spec.credential_source,
            environment=environment,
            model=spec.name,
        )

    if spec.credential_source == "openrouter":
        # Always require validated controls (eval default in non-prod).
        resolve_openrouter_controls_for_env(controls, environment)


__all__ = [
    "OpenRouterControls",
    "allowed_credentials",
    "assert_eligible",
    "resolve_openrouter_controls_for_env",
]
