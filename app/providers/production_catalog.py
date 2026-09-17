"""Load and validate the checked-in production model route catalog."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

from app.core.process_state import register_resettable
from app.providers.context_budget import ContextProfile
from app.providers.model_purpose import PURPOSE_CAPABILITY_REQUIREMENTS, ModelPurpose
from app.providers.route_controls import (
    DeepSeekRouteControls,
    NoRouteControls,
    OpenRouterRouteControls,
    RouteControls,
)

ProviderKind = Literal["azure", "openrouter", "deepseek_direct", "openai"]
DataBoundary = Literal["azure", "external"]
StructuredOutputMode = Literal["json_schema", "json_object", "none"]

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "production_model_catalog.yaml"

_PROVIDER_CREDENTIAL: dict[str, str] = {
    "azure": "entra",
    "openrouter": "openrouter",
    "deepseek_direct": "deepseek_direct",
    "openai": "openai",
}

_PROVIDER_BOUNDARY: dict[str, DataBoundary] = {
    "azure": "azure",
    "openrouter": "external",
    "deepseek_direct": "external",
    "openai": "external",
}

EXTERNAL_PROVIDERS = frozenset({"openrouter", "deepseek_direct", "openai"})


@dataclass(frozen=True)
class DeploymentTarget:
    target_id: str
    provider: ProviderKind
    model_id: str
    credential_source: str
    capabilities: frozenset[str]
    supported_reasoning_efforts: frozenset[str]
    supports_streaming: bool
    supports_streamed_usage: bool
    data_boundary: DataBoundary
    allowed_actual_model_ids: frozenset[str]
    context_profile: ContextProfile | None = None


@dataclass(frozen=True)
class ProductionRoute:
    route_id: str
    target_id: str
    purpose: str
    structured_output_mode: StructuredOutputMode
    reasoning_effort: str | None
    verbosity: str | None
    temperature: float | None
    max_output_tokens: int | None
    request_timeout_s: float | None
    max_retries: int | None
    provider_controls: RouteControls | None
    fallback_route_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductionCatalog:
    targets: dict[str, DeploymentTarget]
    routes: dict[str, ProductionRoute]
    purpose_defaults: dict[str, str]
    escalations: dict[str, dict[str, str]]
    external_purpose_allowlist: dict[str, tuple[str, ...]]

    def target(self, target_id: str) -> DeploymentTarget:
        try:
            return self.targets[target_id]
        except KeyError as exc:
            raise KeyError(f"unknown target_id: {target_id!r}") from exc

    def purpose_default(self, purpose: str) -> ProductionRoute:
        route_id = self.purpose_defaults[purpose]
        route = self.routes[route_id]
        return replace(route, purpose=purpose)

    def escalation_route(self, purpose: str, reason: str) -> ProductionRoute:
        route_id = self.escalations[purpose][reason]
        route = self.routes[route_id]
        return replace(route, purpose=purpose)


@lru_cache(maxsize=4)
def load_production_catalog(path: Path | None = None) -> ProductionCatalog:
    """Load catalog YAML without validation."""
    catalog_path = path or _CATALOG_PATH
    with catalog_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    targets: dict[str, DeploymentTarget] = {}
    for target_id, row in (raw.get("targets") or {}).items():
        context_profile = None
        if "context_window_tokens" in row and "counting_profile" in row:
            framing = row.get("framing_tokens_per_message", 4)
            context_profile = ContextProfile(
                context_window_tokens=int(row["context_window_tokens"]),
                counting_profile=row["counting_profile"],
                framing_tokens_per_message=int(framing),
            )

        targets[target_id] = DeploymentTarget(
            target_id=target_id,
            provider=row["provider"],
            model_id=row["model_id"],
            credential_source=row["credential_source"],
            capabilities=frozenset(row.get("capabilities") or []),
            supported_reasoning_efforts=frozenset(row.get("supported_reasoning_efforts") or []),
            supports_streaming=bool(row.get("supports_streaming", False)),
            supports_streamed_usage=bool(row.get("supports_streamed_usage", False)),
            data_boundary=row["data_boundary"],
            allowed_actual_model_ids=frozenset(row.get("allowed_actual_model_ids") or []),
            context_profile=context_profile,
        )

    routes: dict[str, ProductionRoute] = {}
    for route_id, row in (raw.get("routes") or {}).items():
        fallback = tuple(row.get("fallback_route_ids") or [])
        if "structured_output_mode" not in row:
            raise ValueError(f"route {route_id!r}: structured_output_mode is required")
        target_id = row["target_id"]
        target = targets.get(target_id)
        # A route naming an unknown target_id is a cross-reference error, not a
        # parse error -- _validate_routes (validate_production_catalog) is the
        # single place that rejects it, with its own message. Loading tolerates
        # it here so a not-yet-validated catalog still loads; the raw dict
        # passes through untyped in that case since there is no provider to
        # resolve a control shape against.
        provider_controls = (
            _parse_route_controls(route_id, row.get("provider_controls"), target.provider)
            if target is not None
            else row.get("provider_controls")
        )
        routes[route_id] = ProductionRoute(
            route_id=route_id,
            target_id=target_id,
            purpose=row["purpose"],
            structured_output_mode=row["structured_output_mode"],
            reasoning_effort=row.get("reasoning_effort"),
            verbosity=row.get("verbosity"),
            temperature=row.get("temperature"),
            max_output_tokens=row.get("max_output_tokens"),
            request_timeout_s=row.get("request_timeout_s"),
            max_retries=row.get("max_retries"),
            provider_controls=provider_controls,
            fallback_route_ids=fallback,
        )

    allowlist_raw = raw.get("external_purpose_allowlist") or {}
    external_purpose_allowlist = {
        purpose: tuple(route_ids) for purpose, route_ids in allowlist_raw.items()
    }

    return ProductionCatalog(
        targets=targets,
        routes=routes,
        purpose_defaults=dict(raw.get("purpose_defaults") or {}),
        escalations={
            purpose: dict(reasons) for purpose, reasons in (raw.get("escalations") or {}).items()
        },
        external_purpose_allowlist=external_purpose_allowlist,
    )


register_resettable(load_production_catalog.cache_clear)


_CONTROLS_BY_PROVIDER: dict[str, type[BaseModel]] = {
    "openrouter": OpenRouterRouteControls,
    "deepseek_direct": DeepSeekRouteControls,
    "azure": NoRouteControls,
    "openai": NoRouteControls,
}


def _parse_route_controls(
    route_id: str, raw: dict[str, object] | None, provider: str
) -> RouteControls | None:
    if raw is None:
        return None
    try:
        return _CONTROLS_BY_PROVIDER[provider].model_validate(raw)
    except KeyError as exc:
        raise ValueError(f"route {route_id!r}: unknown provider {provider!r}") from exc
    except ValidationError as exc:
        raise ValueError(f"route {route_id!r}: invalid provider_controls: {exc}") from exc


def validate_production_catalog(catalog: ProductionCatalog) -> None:
    """Fail closed on unknown references, v1 fallback ban, and capability mismatches."""
    _validate_no_duplicate_ids(catalog)
    _validate_targets(catalog)
    _validate_routes(catalog)
    _validate_purpose_defaults(catalog)
    _validate_escalations(catalog)


def _validate_no_duplicate_ids(catalog: ProductionCatalog) -> None:
    if len(catalog.targets) != len({t.target_id for t in catalog.targets.values()}):
        raise ValueError("duplicate target_id in catalog")
    if len(catalog.routes) != len({r.route_id for r in catalog.routes.values()}):
        raise ValueError("duplicate route_id in catalog")


def _validate_targets(catalog: ProductionCatalog) -> None:
    azure_model_ids: dict[str, str] = {}
    for target in catalog.targets.values():
        expected_cred = _PROVIDER_CREDENTIAL.get(target.provider)
        if expected_cred is None:
            raise ValueError(
                f"unknown provider on target {target.target_id!r}: {target.provider!r}"
            )
        if target.credential_source != expected_cred:
            raise ValueError(
                f"target {target.target_id!r}: credential_source {target.credential_source!r} "
                f"does not match provider {target.provider!r} (expected {expected_cred!r})"
            )
        expected_boundary = _PROVIDER_BOUNDARY[target.provider]
        if target.data_boundary != expected_boundary:
            raise ValueError(
                f"target {target.target_id!r}: data_boundary {target.data_boundary!r} "
                f"does not match provider {target.provider!r} (expected {expected_boundary!r})"
            )
        if target.provider == "azure":
            if target.model_id in azure_model_ids:
                prior = azure_model_ids[target.model_id]
                if prior != target.target_id:
                    raise ValueError(
                        f"colliding Azure model_id {target.model_id!r} on targets "
                        f"{prior!r} and {target.target_id!r}"
                    )
            azure_model_ids[target.model_id] = target.target_id


def _validate_routes(catalog: ProductionCatalog) -> None:
    for route in catalog.routes.values():
        if route.target_id not in catalog.targets:
            raise ValueError(
                f"route {route.route_id!r} references unknown target_id {route.target_id!r}"
            )
        if route.fallback_route_ids:
            raise ValueError(
                f"route {route.route_id!r}: fallback_route_ids must be empty in v1 "
                f"(got {list(route.fallback_route_ids)!r})"
            )

        target = catalog.targets[route.target_id]
        if route.structured_output_mode not in {"json_schema", "json_object", "none"}:
            raise ValueError(
                f"route {route.route_id!r}: unknown structured_output_mode "
                f"{route.structured_output_mode!r}"
            )
        if (
            route.structured_output_mode == "json_schema"
            and "structured_output" not in target.capabilities
        ):
            raise ValueError(
                f"route {route.route_id!r}: json_schema requires target structured_output"
            )
        if route.purpose == "record_reasoning" and route.structured_output_mode == "none":
            raise ValueError(
                f"route {route.route_id!r}: record_reasoning requires structured output"
            )
        if target.provider in EXTERNAL_PROVIDERS and route.provider_controls is None:
            raise ValueError(
                f"route {route.route_id!r}: external provider {target.provider!r} "
                "requires provider_controls dict"
            )

        if route.reasoning_effort is not None:
            if route.reasoning_effort not in target.supported_reasoning_efforts:
                raise ValueError(
                    f"route {route.route_id!r}: reasoning_effort {route.reasoning_effort!r} "
                    f"not supported by target {route.target_id!r} "
                    f"(supported: {sorted(target.supported_reasoning_efforts)!r})"
                )


def _validate_purpose_defaults(catalog: ProductionCatalog) -> None:
    for purpose, route_id in catalog.purpose_defaults.items():
        if route_id not in catalog.routes:
            raise ValueError(f"purpose_defaults[{purpose!r}] references unknown route {route_id!r}")
        route = catalog.routes[route_id]
        target = catalog.targets[route.target_id]
        assert_capabilities_for_purpose(purpose, target, context=f"purpose_defaults[{purpose!r}]")
        assert_external_allowlist(
            catalog, purpose, route_id, context=f"purpose_defaults[{purpose!r}]"
        )


def _validate_escalations(catalog: ProductionCatalog) -> None:
    for purpose, reasons in catalog.escalations.items():
        for reason, route_id in reasons.items():
            if route_id not in catalog.routes:
                raise ValueError(
                    f"escalations[{purpose!r}][{reason!r}] references unknown route {route_id!r}"
                )
            route = catalog.routes[route_id]
            target = catalog.targets[route.target_id]
            assert_capabilities_for_purpose(
                purpose,
                target,
                context=f"escalations[{purpose!r}][{reason!r}]",
            )
            assert_external_allowlist(
                catalog,
                purpose,
                route_id,
                context=f"escalations[{purpose!r}][{reason!r}]",
            )


def assert_capabilities_for_purpose(
    purpose: str,
    target: DeploymentTarget,
    *,
    context: str,
) -> None:
    try:
        required = PURPOSE_CAPABILITY_REQUIREMENTS[ModelPurpose(purpose)]
    except (ValueError, KeyError) as exc:
        raise ValueError(f"{context}: unknown purpose {purpose!r}") from exc

    if ModelPurpose(purpose) is ModelPurpose.record_reasoning:
        # tools AND/OR structured_output -- a planner target may satisfy either.
        if not ({"tools", "structured_output"} & target.capabilities):
            raise ValueError(
                f"{context}: target {target.target_id!r} must declare tools and/or "
                f"structured_output for record_reasoning (has {sorted(target.capabilities)!r})"
            )
        return

    missing = required - target.capabilities
    if missing:
        raise ValueError(
            f"{context}: target {target.target_id!r} missing required capabilities "
            f"{sorted(missing)!r} for purpose {purpose!r}"
        )

    if ModelPurpose(purpose) is ModelPurpose.coordinator:
        if target.context_profile is None:
            raise ValueError(
                f"{context}: target {target.target_id!r} missing context_profile for coordinator"
            )
        if not target.supports_streamed_usage:
            raise ValueError(
                f"{context}: target {target.target_id!r} must support "
                "streamed usage for coordinator"
            )


def assert_external_allowlist(
    catalog: ProductionCatalog,
    purpose: str,
    route_id: str,
    *,
    context: str,
) -> None:
    route = catalog.routes[route_id]
    target = catalog.targets[route.target_id]
    if target.provider not in EXTERNAL_PROVIDERS:
        return
    allowed = catalog.external_purpose_allowlist.get(purpose, ())
    if route_id not in allowed:
        raise ValueError(
            f"{context}: external route {route_id!r} is not on external_purpose_allowlist "
            f"for purpose {purpose!r}"
        )
