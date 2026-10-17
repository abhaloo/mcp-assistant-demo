"""Production Azure route attestation via ARM Management API + Entra.

Eval arm-profile attestation lives in ``app.experiments.azure_attestation`` (ADR 0035).
This module is the Ask/production counterpart — must not import experiment modules.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, fields
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.providers.production_catalog import DeploymentTarget, load_production_catalog

_ARM_API_VERSION = "2023-05-01"
_MANAGEMENT_SCOPE = "https://management.azure.com/.default"


class AttestationError(Exception):
    """Raised when ARM attestation drifts or is unreachable."""


@dataclass(frozen=True)
class AttestationResult:
    deployment: str
    model_name: str
    model_version: str
    deployment_state: str
    provisioning_state: str
    sku: str
    resource_etag: str


# An ARM ETag changes on every resource write, so it is observed and recorded but
# never compared against a frozen allowlist. Every other field is compared.
_UNPINNABLE_FIELDS = frozenset({"resource_etag"})
_ATTESTATION_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(AttestationResult) if f.name not in _UNPINNABLE_FIELDS
)


class ArmClient(Protocol):
    def get_deployment(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        account: str,
        deployment_name: str,
    ) -> dict[str, Any]: ...


class HttpArmClient:
    """Live ARM client using Entra bearer tokens and httpx."""

    def __init__(self, *, token: str, timeout_s: float = 30.0) -> None:
        self._token = token
        self._timeout_s = timeout_s

    def get_deployment(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        account: str,
        deployment_name: str,
    ) -> dict[str, Any]:
        url = (
            "https://management.azure.com/subscriptions/"
            f"{subscription_id}/resourceGroups/{resource_group}/providers/"
            f"Microsoft.CognitiveServices/accounts/{account}/deployments/"
            f"{deployment_name}"
        )
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    params={"api-version": _ARM_API_VERSION},
                )
        except httpx.HTTPError as exc:
            raise AttestationError(
                f"Azure management API unreachable for deployment {deployment_name!r}"
            ) from exc
        if response.status_code >= 400:
            detail = response.text.strip()[:300]
            raise AttestationError(
                f"Azure management API failed for deployment {deployment_name!r}: "
                f"HTTP {response.status_code} {detail}"
            )
        try:
            document = response.json()
        except json.JSONDecodeError as exc:
            raise AttestationError(
                f"Azure management API returned malformed JSON for {deployment_name!r}"
            ) from exc
        if not isinstance(document, dict):
            raise AttestationError(
                f"Azure management API returned unexpected payload for {deployment_name!r}"
            )
        return document


def attestation_settings_configured() -> bool:
    return bool(
        settings.azure_subscription_id
        and settings.azure_openai_resource_group
        and settings.azure_openai_account
        and settings.azure_deployment_attestation_allowlist_json.strip()
    )


def load_attestation_allowlist() -> dict[str, dict[str, str]]:
    raw = settings.azure_deployment_attestation_allowlist_json.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AttestationError(
            "azure_deployment_attestation_allowlist_json is invalid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise AttestationError("azure_deployment_attestation_allowlist_json must be a JSON object")
    allowlist: dict[str, dict[str, str]] = {}
    for deployment, expected in parsed.items():
        if not isinstance(deployment, str) or not isinstance(expected, dict):
            raise AttestationError(
                "azure_deployment_attestation_allowlist_json entries must be deployment -> object"
            )
        allowlist[deployment] = {str(k): str(v) for k, v in expected.items()}
    return allowlist


def parse_arm_deployment(document: dict[str, Any]) -> dict[str, str]:
    try:
        properties = document["properties"]
        model = properties["model"]
        observed = {
            "deployment": str(document["name"]),
            "model_name": str(model["name"]),
            "model_version": str(model["version"]),
            "deployment_state": str(properties["deploymentState"]),
            "provisioning_state": str(properties["provisioningState"]),
            "sku": str(document["sku"]["name"]),
            "resource_etag": str(document.get("etag") or ""),
        }
    except (KeyError, TypeError) as exc:
        raise AttestationError("Azure management API deployment payload was malformed") from exc
    if not observed["resource_etag"].strip():
        raise AttestationError("Azure management API deployment payload omitted resource ETag")
    return observed


def compare_attestation(expected: dict[str, str], observed: dict[str, str]) -> None:
    drift = {
        field: {"expected": expected.get(field), "observed": observed.get(field)}
        for field in _ATTESTATION_FIELDS
        if observed.get(field) != expected.get(field)
    }
    if drift:
        parts = ", ".join(sorted(drift))
        raise AttestationError(f"Azure deployment attestation drifted: {parts}")


def _expected_for_deployment(deployment: str) -> dict[str, str]:
    allowlist = load_attestation_allowlist()
    try:
        expected = allowlist[deployment]
    except KeyError as exc:
        raise AttestationError(
            f"no attestation allowlist entry for active deployment {deployment!r}"
        ) from exc
    merged = {"deployment": deployment, **expected}
    missing = [field for field in _ATTESTATION_FIELDS if not merged.get(field)]
    if missing:
        raise AttestationError(
            f"attestation allowlist for {deployment!r} missing fields: {', '.join(missing)}"
        )
    return merged


def _verify_endpoint_account() -> None:
    endpoint = settings.azure_endpoint
    account = settings.azure_openai_account
    if not endpoint or not account:
        return
    endpoint_host = urlparse(endpoint).hostname
    if not endpoint_host:
        return
    expected_hosts = {
        f"{account}.openai.azure.com",
        f"{account}.services.ai.azure.com",
    }
    if endpoint_host not in expected_hosts:
        raise AttestationError(
            f"azure_endpoint host {endpoint_host!r} does not match attested account "
            f"{account!r} ({sorted(expected_hosts)!r})"
        )


def attest_azure_deployment(
    target: DeploymentTarget,
    *,
    client: ArmClient,
) -> AttestationResult:
    if target.provider != "azure":
        raise AttestationError(
            f"attest_azure_deployment requires an Azure target, got {target.provider!r}"
        )
    deployment = target.model_id
    expected = _expected_for_deployment(deployment)
    _verify_endpoint_account()
    document = client.get_deployment(
        subscription_id=settings.azure_subscription_id,
        resource_group=settings.azure_openai_resource_group,
        account=settings.azure_openai_account,
        deployment_name=deployment,
    )
    observed = parse_arm_deployment(document)
    compare_attestation(expected, observed)
    return AttestationResult(**observed)


def iter_active_azure_deployments() -> list[tuple[str, DeploymentTarget]]:
    from app.providers.catalog_startup import (
        iter_active_escalation_routes,
        iter_active_purpose_routes,
    )

    catalog = load_production_catalog()
    seen: set[str] = set()
    active: list[tuple[str, DeploymentTarget]] = []

    for _purpose, route_id in iter_active_purpose_routes(catalog):
        route = catalog.routes[route_id]
        target = catalog.targets[route.target_id]
        if target.provider != "azure":
            continue
        if target.model_id in seen:
            continue
        seen.add(target.model_id)
        active.append((target.model_id, target))

    for _purpose, _reason, route_id in iter_active_escalation_routes(catalog):
        route = catalog.routes[route_id]
        target = catalog.targets[route.target_id]
        if target.provider != "azure":
            continue
        if target.model_id in seen:
            continue
        seen.add(target.model_id)
        active.append((target.model_id, target))

    return sorted(active, key=lambda item: item[0])


def build_arm_client() -> ArmClient:
    from app.providers.azure_credential import get_token_provider

    token = get_token_provider(_MANAGEMENT_SCOPE)()
    return HttpArmClient(token=token)


class AzureRouteAttestationCheck:
    """Readiness: active Azure catalog routes match ARM-attested model identity."""

    name = "azure_route_attestation"

    async def run(self) -> bool:
        def _attest_all() -> bool:
            if not attestation_settings_configured():
                return False
            client = build_arm_client()
            for _deployment, target in iter_active_azure_deployments():
                attest_azure_deployment(target, client=client)
            return True

        return await asyncio.to_thread(_attest_all)
