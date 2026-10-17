"""Wire-level route conformance oracle (offline-pure).

``enumerate_routes`` is pure (no network). ``probe_route`` is pure given a built model;
the live driver owns credentials and spend.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.experiments.core.secret_redaction import redact_secrets
from app.providers.eligibility_policy import allowed_credentials
from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import resolve_model_spec
from app.providers.openrouter_controls import default_eval_controls

if TYPE_CHECKING:
    from app.config import Settings

MINIMAL_PROMPT = "Reply with the single character: K"
# Invoke-time kwarg; matches document_chain's answer ceiling parameter.
PROBE_MAX_TOKENS = 16
# Reasoning models reject max_tokens and need more headroom for a non-empty reply.
REASONING_PROBE_MAX_TOKENS = 256

STATUS_CONFORMANT = "ROUTES-CONFORMANT"
STATUS_BROKEN = "ROUTE-BROKEN"

# The per-route driver's failure vocabulary. Both callers switch on these to decide
# what to charge, so they are named constants rather than repeated string literals
# in two modules.
FAILURE_ENVIRONMENT_MISMATCH = "environment_mismatch"
FAILURE_UNPRICEABLE = "unpriceable"
FAILURE_MODEL_BUILD_FAILED = "model_build_failed"
FAILURE_PROBE_RAISED = "probe_raised"
# Failures before any provider call — do not charge.
UNSPENT_FAILURES = frozenset(
    {FAILURE_ENVIRONMENT_MISMATCH, FAILURE_UNPRICEABLE, FAILURE_MODEL_BUILD_FAILED}
)

_UNATTESTED = {None, "", "unknown", "unavailable"}


@dataclass(frozen=True)
class RouteProbe:
    """One (purpose, deployment) route to exercise against its live provider."""

    purpose: ModelPurpose
    deployment: str
    credential_source: str
    client_kind: str
    environment: str
    # Drives which token-ceiling parameter the wire accepts. See
    # REASONING_PROBE_MAX_TOKENS — reasoning deployments reject `max_tokens`.
    returns_reasoning: bool = False

    @property
    def route_id(self) -> str:
        return f"{self.purpose.value}:{self.deployment}"


@dataclass(frozen=True)
class RouteProbeResult:
    """Outcome of one route probe. ``failures`` is empty iff ``passed``."""

    probe: RouteProbe
    passed: bool
    failures: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class ConformanceReport:
    """Aggregate verdict across every probed route."""

    status: str
    results: tuple[RouteProbeResult, ...] = ()
    broken_routes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == STATUS_CONFORMANT


def enumerate_routes(
    s: Settings,
    *,
    purposes: Iterable[ModelPurpose] | None = None,
    environment: str | None = None,
    extra_deployments: Iterable[str] | None = None,
) -> list[RouteProbe]:
    """Build the eligible (purpose, deployment) matrix. Pure — no network, no keys.

    ``extra_deployments`` covers slugs resolved dynamically rather than from the
    static registry (e.g. the campaign's OpenRouter candidate model), which
    otherwise would never be probed.
    """
    env = environment or s.environment
    wanted = list(purposes) if purposes is not None else list(ModelPurpose)

    deployments: list[str] = []
    if s.azure_endpoint:
        if s.azure_chat_deployment and s.azure_chat_deployment not in deployments:
            deployments.append(s.azure_chat_deployment)
        if (
            s.azure_chat_escalation_deployment
            and s.azure_chat_escalation_deployment not in deployments
        ):
            deployments.append(s.azure_chat_escalation_deployment)
    if s.deepseek_direct_model and s.deepseek_direct_base_url and s.deepseek_direct_api_key:
        if s.deepseek_direct_model not in deployments:
            deployments.append(s.deepseek_direct_model)
    for deployment in extra_deployments or ():
        if deployment not in deployments:
            deployments.append(deployment)

    specs: list[Any] = []
    for deployment in deployments:
        try:
            specs.append(resolve_model_spec(deployment, s))
        except KeyError:
            # An unresolvable deployment is a config problem, not a route to
            # probe. Skipping keeps enumeration total and credential-free.
            continue

    probes: list[RouteProbe] = []
    seen: set[tuple[str, str]] = set()
    for purpose in wanted:
        allowed = allowed_credentials(purpose, env)
        for spec in specs:
            if spec.credential_source not in allowed:
                continue
            key = (purpose.value, spec.name)
            if key in seen:
                continue
            seen.add(key)
            probes.append(
                RouteProbe(
                    purpose=purpose,
                    deployment=spec.name,
                    credential_source=spec.credential_source,
                    client_kind=spec.client_kind,
                    environment=env,
                    returns_reasoning=bool(getattr(spec, "returns_reasoning", False)),
                )
            )
    return probes


def _metadata(response: Any) -> dict[str, Any]:
    meta = getattr(response, "response_metadata", None) or {}
    return meta if isinstance(meta, dict) else {}


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None) or {}
    if isinstance(usage, dict):
        return usage
    return dict(usage) if hasattr(usage, "items") else {}


def _collect_evidence(response: Any, probe: RouteProbe) -> dict[str, Any]:
    meta = _metadata(response)
    usage = _usage(response)
    content = getattr(response, "content", "")
    actual_model = meta.get("model_name") or meta.get("model")
    # OpenRouter may route to different upstream providers.
    actual_provider = (
        meta.get("provider")
        if probe.credential_source == "openrouter"
        else meta.get("provider") or probe.credential_source
    )
    return {
        "route_id": probe.route_id,
        "actual_model": actual_model,
        "actual_provider": actual_provider,
        "finish_reason": meta.get("finish_reason"),
        "request_id": getattr(response, "id", None),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        # Measured after stripping: a whitespace-only body is an empty response,
        # not a short one.
        "content_length": len(content.strip()) if isinstance(content, str) else 0,
    }


def _evidence_failures(evidence: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not evidence["content_length"]:
        # A route that answered but ran out of ceiling is a budget finding, not a
        # dead route. Reporting truncation as "empty" is a misdiagnosis — in the
        # one module whose entire purpose is diagnosing correctly.
        truncated = str(evidence.get("finish_reason") or "").casefold() in {"length", "max_tokens"}
        failures.append("output_truncated" if truncated else "empty_response")
    if evidence["actual_model"] in _UNATTESTED:
        failures.append("actual_model_unattested")
    if evidence["actual_provider"] in _UNATTESTED:
        failures.append("actual_provider_unattested")
    if evidence["input_tokens"] + evidence["output_tokens"] <= 0:
        failures.append("missing_usage")
    if evidence["request_id"] in _UNATTESTED:
        failures.append("missing_provider_request_id")
    return failures


def _ceiling_kwarg(probe: RouteProbe, *, max_tokens: int, reasoning_max_tokens: int) -> dict:
    """Pick the token-ceiling parameter this route's wire actually accepts."""
    if probe.returns_reasoning:
        return {"max_completion_tokens": reasoning_max_tokens}
    return {"max_tokens": max_tokens}


def probe_route(
    llm: Any,
    probe: RouteProbe,
    *,
    prompt: str = MINIMAL_PROMPT,
    max_tokens: int = PROBE_MAX_TOKENS,
    reasoning_max_tokens: int = REASONING_PROBE_MAX_TOKENS,
) -> RouteProbeResult:
    """Send one minimal real request and judge the wire, not the answer.

    The token ceiling is passed as an invoke kwarg deliberately — mirroring the
    production call paths — so a provider that rejects the parameter name fails
    here, cheaply, instead of inside a paid campaign matrix. Which name is
    correct is per-route, not global: the answer path proved OpenRouter needs
    ``max_tokens`` (a rename broke every call on 2026-08-01), and the judge path
    proved Azure reasoning deployments need ``max_completion_tokens`` (400
    otherwise). Sending one convention everywhere fails a healthy route.
    """
    try:
        response = llm.invoke(
            prompt,
            **_ceiling_kwarg(
                probe, max_tokens=max_tokens, reasoning_max_tokens=reasoning_max_tokens
            ),
        )
    except Exception as exc:  # noqa: BLE001 — the provider's text IS the finding
        return RouteProbeResult(
            probe=probe,
            passed=False,
            failures=("route_rejected",),
            evidence={"route_id": probe.route_id},
            # Scrubbed: provider/pydantic errors embed the credential that failed.
            error=redact_secrets(f"{type(exc).__name__}: {exc}"),
        )

    evidence = _collect_evidence(response, probe)
    failures = _evidence_failures(evidence)
    return RouteProbeResult(
        probe=probe,
        passed=not failures,
        failures=tuple(failures),
        evidence=evidence,
        error=None,
    )


def failed_result(probe: RouteProbe, failure: str, error: str | None = None) -> RouteProbeResult:
    """The one row shape for a route that failed before, or instead of, a verdict.

    ``error`` is always scrubbed: a denied model build is the likeliest place for a
    credential to reach disk (``get_chat_model`` raises a pydantic ``ValidationError``
    whose rendering embeds the ``openrouter_api_key`` it failed to validate), and
    both callers persist this row — Campaign to ``answers.jsonl``, the CLI to its
    live evidence JSON.
    """
    return RouteProbeResult(
        probe=probe,
        passed=False,
        failures=(failure,),
        evidence={"route_id": probe.route_id},
        error=redact_secrets(error),
    )


def check_route_preconditions(
    probe: RouteProbe,
    *,
    environment: str,
    price_check_fn: Callable[[str], Any],
) -> RouteProbeResult | None:
    """The two checks that must happen BEFORE any money is reserved.

    Returns the failing result, or ``None`` when the route is clear to spend on.
    Split out from ``probe_one_route`` (rather than folded into it) because the CLI
    driver's ``acquire`` sits between these checks and the model build: reserving
    first would spend on a route we already know is unprobeable, and calling the
    price check twice to avoid that would be worse.
    """
    # get_chat_model binds settings.environment, not the probe's. Probing a route
    # enumerated for a different environment would attest a route that was never
    # exercised — the misdiagnosis this whole module exists to prevent.
    if probe.environment != environment:
        return failed_result(
            probe,
            FAILURE_ENVIRONMENT_MISMATCH,
            f"probe enumerated for {probe.environment!r} but the driver is bound to "
            f"{environment!r}",
        )
    # A model we cannot cost is a model we cannot spend on safely. Fail the route
    # rather than reserve blind; aborting instead would hide every route after it.
    try:
        price_check_fn(probe.deployment)
    except KeyError as exc:
        return failed_result(probe, FAILURE_UNPRICEABLE, f"{type(exc).__name__}: {exc}")
    return None


def probe_built_route(
    probe: RouteProbe,
    *,
    get_model_fn: Callable[..., Any],
    probe_fn: Callable[[Any, RouteProbe], RouteProbeResult],
) -> RouteProbeResult:
    """Build the route's model through the injected factory and probe it.

    Assumes ``check_route_preconditions`` already passed. Never raises for a route
    finding: a denied build and a throwing probe are both outcomes, because one
    broken route must not stop the sweep.
    """
    try:
        llm = get_model_fn(
            purpose=probe.purpose,
            deployment=probe.deployment,
            controls=(default_eval_controls() if probe.credential_source == "openrouter" else None),
        )
    except Exception as exc:  # noqa: BLE001 — a denied build is a route finding
        return failed_result(probe, FAILURE_MODEL_BUILD_FAILED, f"{type(exc).__name__}: {exc}")

    try:
        return probe_fn(llm, probe)
    except Exception as exc:  # noqa: BLE001 — probe_route already fails closed
        # internally; this is the net for an injected probe_fn that does not. The
        # call may have spent before throwing — see UNSPENT_FAILURES.
        return failed_result(probe, FAILURE_PROBE_RAISED, f"{type(exc).__name__}: {exc}")


def probe_one_route(
    probe: RouteProbe,
    *,
    environment: str,
    get_model_fn: Callable[..., Any],
    probe_fn: Callable[[Any, RouteProbe], RouteProbeResult],
    price_check_fn: Callable[[str], Any],
) -> RouteProbeResult:
    """Preconditions → build → probe, for one route. Total: never raises."""
    blocked = check_route_preconditions(
        probe, environment=environment, price_check_fn=price_check_fn
    )
    if blocked is not None:
        return blocked
    return probe_built_route(probe, get_model_fn=get_model_fn, probe_fn=probe_fn)


def evaluate_conformance(results: Sequence[RouteProbeResult]) -> ConformanceReport:
    """Fail-closed aggregate. An empty matrix is BROKEN, never 'all clear'."""
    ordered = tuple(results)
    if not ordered:
        return ConformanceReport(status=STATUS_BROKEN, results=(), broken_routes=())

    broken = tuple(r.probe.route_id for r in ordered if not r.passed)
    status = STATUS_BROKEN if broken else STATUS_CONFORMANT
    return ConformanceReport(status=status, results=ordered, broken_routes=broken)
