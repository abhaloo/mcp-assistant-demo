"""Live Azure deployment attestation for frozen RAG/SQL campaigns."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.experiments.live_errors import LiveRagEvalError
from app.experiments.rag_experiment import FrozenRagExperiment


def verify_live_azure_deployment(expected: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless current ARM deployment state matches the frozen identity."""
    if expected.get("source") != "azure_resource_manager":
        raise LiveRagEvalError("Azure deployment attestation must name ARM as its source")
    endpoint_host = urlparse(settings.azure_endpoint or "").hostname
    expected_host = f"{expected['account']}.openai.azure.com"
    if endpoint_host != expected_host:
        raise LiveRagEvalError("Azure endpoint does not match the attested account")
    azure_cli = shutil.which("az")
    if not azure_cli:
        raise LiveRagEvalError("live Azure deployment attestation requires Azure CLI")
    command = [
        azure_cli,
        "cognitiveservices",
        "account",
        "deployment",
        "show",
        "--resource-group",
        str(expected["resource_group"]),
        "--name",
        str(expected["account"]),
        "--deployment-name",
        str(expected["deployment"]),
        "--output",
        "json",
        "--only-show-errors",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise LiveRagEvalError("live Azure deployment attestation could not run") from exc
    if completed.returncode != 0:
        detail = str(completed.stderr or "Azure CLI returned no diagnostic").strip()[:300]
        raise LiveRagEvalError(f"live Azure deployment attestation failed: {detail}")
    try:
        document = json.loads(completed.stdout)
        properties = document["properties"]
        model = properties["model"]
        observed = {
            "deployment": document["name"],
            "model_name": model["name"],
            "model_version": model["version"],
            "deployment_state": properties["deploymentState"],
            "provisioning_state": properties["provisioningState"],
            "sku": document["sku"]["name"],
        }
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LiveRagEvalError("live Azure deployment attestation was malformed") from exc
    drift = {
        field: {"expected": expected.get(field), "observed": value}
        for field, value in observed.items()
        if value != expected.get(field)
    }
    if drift:
        raise LiveRagEvalError(
            "live Azure deployment attestation drifted: " + ", ".join(sorted(drift))
        )
    resource_etag = document.get("etag")
    if not isinstance(resource_etag, str) or not resource_etag.strip():
        raise LiveRagEvalError("live Azure deployment attestation omitted its resource ETag")
    return {
        **expected,
        "verification": "live_azure_cli",
        "verified_at": datetime.now(UTC).isoformat(),
        "endpoint_host": endpoint_host,
        "resource_etag": resource_etag,
    }


def verify_frozen_azure_deployments(
    frozen: FrozenRagExperiment,
    *,
    stage: str,
) -> None:
    """Re-attest Azure deployments and reject revision drift within one run."""
    try:
        attestations = frozen.document["artifact_identity"]["answer_deployment_attestations"]
        judge = frozen.document["judge"]["version"]["deployment_attestation"]
    except (KeyError, TypeError) as exc:
        raise LiveRagEvalError("frozen Azure deployment attestations are incomplete") from exc

    labels_and_expected: list[tuple[str, dict[str, Any]]] = [("judge", judge)]
    for arm in frozen.document.get("arms") or []:
        if not isinstance(arm, dict):
            continue
        arm_name = arm.get("name")
        if arm.get("requested_provider") != "azure" or not isinstance(arm_name, str):
            continue
        expected = attestations.get(arm_name)
        if not isinstance(expected, dict):
            raise LiveRagEvalError(
                f"frozen Azure deployment attestation is missing for answer arm {arm_name!r}"
            )
        labels_and_expected.append((arm_name, expected))

    for label, expected in labels_and_expected:
        frozen_etag = expected.get("resource_etag")
        if not isinstance(frozen_etag, str) or not frozen_etag.strip():
            raise LiveRagEvalError(f"frozen Azure deployment ETag is missing for {label}")
        current = verify_live_azure_deployment(expected)
        if current.get("resource_etag") != frozen_etag:
            raise LiveRagEvalError(f"Azure deployment ETag drifted during {stage}: {label}")
