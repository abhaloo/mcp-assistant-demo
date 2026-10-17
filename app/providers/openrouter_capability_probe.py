"""Read-only OpenRouter /models capability pre-flight.

resolve_openrouter_spec hard-codes supports_tools/supports_json_schema and never
checks them against the live API. This probes the one piece this plan's verified
ground truth confirms the shape of -- reasoning.supported_efforts -- against the
candidate profile's declared reasoning_effort, and HOLDs on a confirmed mismatch.

supported_parameters is recorded on the result but not compared against
supports_tools/supports_json_schema: OpenRouter's exact vocabulary for those two
flags is not in this plan's verified ground truth, and asserting a specific mapping
without it risks a wrong HOLD. Left for a follow-up once that mapping is confirmed.

Never auto-corrects the profile -- only surfaces agreement, disagreement, or
"could not check" (unreachable endpoint, unknown slug: UNVERIFIED, not a silent pass).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

from app.config import settings
from app.providers.http_clients import get_sync_http_client
from app.resources import current_process_resources

STATUS_VERIFIED = "VERIFIED"
STATUS_HOLD = "HOLD"
STATUS_UNVERIFIED = "UNVERIFIED"

# OpenRouter catalog may list ``max`` while profiles/wire use ``xhigh`` (see
# openrouter_controls.normalize_reasoning_effort). Treat as one ceiling for probe only.
_MAX_XHIGH_ALIASES = frozenset({"max", "xhigh"})


def _catalog_supports_effort(supported_efforts: list[str], expected: str) -> bool:
    """True when ``expected`` is listed, or max/xhigh alias matches either side."""
    if expected in supported_efforts:
        return True
    if expected in _MAX_XHIGH_ALIASES:
        return bool(_MAX_XHIGH_ALIASES.intersection(supported_efforts))
    return False


@dataclass(frozen=True)
class CapabilityProbeResult:
    status: str
    detail: str
    supported_parameters: tuple[str, ...] = ()
    waivable: bool = True


@cache
def _fetch_models(base_url: str) -> tuple[dict[str, Any], ...] | None:
    """GET {base_url}/models. None if unreachable or malformed. Cached per process."""
    try:
        response = get_sync_http_client(current_process_resources()).get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        )
        response.raise_for_status()
        data = response.json().get("data")
    except Exception:  # noqa: BLE001 - unreachable/malformed both mean "cannot verify"
        return None
    if not isinstance(data, list):
        return None
    return tuple(data)


def probe_candidate_capabilities(
    *, base_url: str, slug: str, expected_reasoning_effort: str | None
) -> CapabilityProbeResult:
    """HOLD only on a positively-confirmed mismatch; UNVERIFIED when it cannot check."""
    models = _fetch_models(base_url)
    if models is None:
        return CapabilityProbeResult(STATUS_UNVERIFIED, f"{base_url}/models unreachable")
    entry = next((m for m in models if isinstance(m, dict) and m.get("id") == slug), None)
    if entry is None:
        return CapabilityProbeResult(STATUS_UNVERIFIED, f"{slug!r} not present in /models response")

    reasoning = entry.get("reasoning")
    supported_efforts = reasoning.get("supported_efforts") if isinstance(reasoning, dict) else None
    raw_parameters = entry.get("supported_parameters")
    parameters = tuple(raw_parameters) if isinstance(raw_parameters, list) else ()

    if expected_reasoning_effort is None:
        return CapabilityProbeResult(
            STATUS_VERIFIED,
            f"{slug!r} has no declared reasoning_effort to verify",
            parameters,
        )

    if not isinstance(supported_efforts, list) or not supported_efforts:
        return CapabilityProbeResult(
            STATUS_UNVERIFIED,
            f"{slug!r} live /models response missing or empty reasoning.supported_efforts "
            f"while profile declares reasoning_effort={expected_reasoning_effort!r}",
            parameters,
            waivable=False,
        )

    if not _catalog_supports_effort(supported_efforts, expected_reasoning_effort):
        return CapabilityProbeResult(
            STATUS_HOLD,
            f"{slug!r} profile declares reasoning_effort={expected_reasoning_effort!r}, "
            f"but live OpenRouter supported_efforts={supported_efforts!r} does not include it",
            parameters,
        )
    return CapabilityProbeResult(
        STATUS_VERIFIED,
        f"{slug!r} reasoning_effort={expected_reasoning_effort!r} matches live data",
        parameters,
    )
