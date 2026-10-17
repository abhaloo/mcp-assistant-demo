"""Fail-closed production catalog validation at process startup."""

from __future__ import annotations

from app.providers.production_catalog import (
    ProductionCatalog,
    assert_capabilities_for_purpose,
    assert_external_allowlist,
    load_production_catalog,
    validate_production_catalog,
)
from app.providers.route_policy import get_route_policy
from app.providers.route_settings import escalation_override_for, route_override_for
from app.providers.stage_model_report import PRODUCTION_PURPOSES


def verify_production_catalog_startup() -> None:
    """Validate the checked-in catalog and every active route binding before serving.

    Raises ``ValueError`` on unknown route IDs, purpose capability mismatch,
    unsupported reasoning effort for the target, external routes not on the
    purpose allowlist, or colliding Azure deployment names. Does not call the
    Azure Management API.
    """
    catalog = load_production_catalog()
    validate_production_catalog(catalog)
    _validate_active_route_bindings(catalog)
    _require_every_production_purpose(catalog)
    get_route_policy(reload=True)


def _require_every_production_purpose(catalog: ProductionCatalog) -> None:
    """Every purpose the serving code resolves has a route before the first request."""
    for purpose in PRODUCTION_PURPOSES:
        if purpose.value not in catalog.purpose_defaults and not route_override_for(purpose.value):
            raise ValueError(f"no default route for purpose {purpose.value!r}")


def _validate_active_route_bindings(catalog: ProductionCatalog) -> None:
    seen: set[tuple[str, str]] = set()

    for purpose, route_id in iter_active_purpose_routes(catalog):
        key = ("purpose", purpose)
        if key in seen:
            continue
        seen.add(key)
        _validate_active_route(
            catalog,
            purpose,
            route_id,
            context=f"active route for purpose {purpose!r}",
        )

    for purpose, reason, route_id in iter_active_escalation_routes(catalog):
        key = ("escalation", f"{purpose}:{reason}")
        if key in seen:
            continue
        seen.add(key)
        _validate_active_route(
            catalog,
            purpose,
            route_id,
            context=f"escalation route for {purpose!r} / {reason!r}",
        )


def iter_active_purpose_routes(catalog: ProductionCatalog):
    for purpose in catalog.purpose_defaults:
        override = route_override_for(purpose)
        route_id = override or catalog.purpose_defaults[purpose]
        yield purpose, route_id


def iter_active_escalation_routes(catalog: ProductionCatalog):
    for purpose, reasons in catalog.escalations.items():
        for reason, default_route_id in reasons.items():
            override = escalation_override_for(purpose, reason)
            route_id = override or default_route_id
            yield purpose, reason, route_id


def _validate_active_route(
    catalog: ProductionCatalog,
    purpose: str,
    route_id: str,
    *,
    context: str,
) -> None:
    if route_id not in catalog.routes:
        raise ValueError(f"{context}: unknown route {route_id!r}")

    route = catalog.routes[route_id]
    if route.target_id not in catalog.targets:
        raise ValueError(
            f"{context}: route {route_id!r} references unknown target {route.target_id!r}"
        )

    target = catalog.targets[route.target_id]

    if route.reasoning_effort is not None:
        if route.reasoning_effort not in target.supported_reasoning_efforts:
            raise ValueError(
                f"{context}: route {route_id!r} reasoning_effort "
                f"{route.reasoning_effort!r} not supported by target "
                f"{route.target_id!r} "
                f"(supported: {sorted(target.supported_reasoning_efforts)!r})"
            )

    assert_capabilities_for_purpose(purpose, target, context=context)
    assert_external_allowlist(catalog, purpose, route_id, context=context)
