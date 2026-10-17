"""Frozen-context RAG answer path, OpenRouter metadata, and effort probe."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from threading import Lock
from time import sleep
from typing import Any, Protocol
from urllib.parse import urlparse

import tiktoken
from langchain_core.documents import Document

from app.config import settings
from app.experiments.core.money import estimate_cost_usd
from app.experiments.core.work_matrix import (
    DEFAULT_MAX_WORKERS,
    EFFORT_PROBE_MAX_WORKERS,
)
from app.experiments.effort_probe import (
    MIN_SAMPLES_PER_EFFORT,
    EffortProbeResult,
    evaluate_effort_separation,
)
from app.experiments.live_errors import LiveRagEvalError
from app.experiments.rag_experiment import AnswerArm, reasoning_tokens_spent
from app.experiments.rag_generation_cost import (
    RagGenerationCostError,
    derive_cost_usd,
    require_generation_fields,
)
from app.experiments.spend_coordinator import (
    acquire,
    commit,
    release,
)
from app.providers.azure_reasoning import needs_azure_reasoning_completion_args
from app.providers.http_clients import get_sync_http_client
from app.providers.model_registry import resolve_openrouter_spec
from app.providers.openrouter_controls import (
    OpenRouterControls,
    normalize_reasoning_effort,
)
from app.rag.access_tiers import get_access_tiers
from app.rag.chains.document_chain import (
    RagTurnInput,
    generation_prompt_for_rag_turn,
    invoke_rag_generation_with_usage,
    retrieve_for_rag_turn,
)
from app.resources import current_process_resources

LIVE_ANSWER_MAX_WORKERS = DEFAULT_MAX_WORKERS
EXPERIMENT_RETRIEVAL_TOP_K = 10
ANSWER_INPUT_CEILING_TOKENS = 16_384
CONTEXT_TOKEN_CEILING = ANSWER_INPUT_CEILING_TOKENS - 2_048
EFFORT_PROBE_RESERVE_USD = 0.5
PHASE = "rag_repair"


def validated_openrouter_base_url() -> str:
    parsed = urlparse(settings.openrouter_base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "openrouter.ai"
        or parsed.port is not None
        or parsed.path.rstrip("/") != "/api/v1"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise LiveRagEvalError("OpenRouter base URL must be exactly https://openrouter.ai/api/v1")
    return "https://openrouter.ai/api/v1"


def retrieve_case_snapshot(
    case: dict[str, Any],
    *,
    retrieve_fn: Callable[..., list[Document]] = retrieve_for_rag_turn,
    retrieval_identity: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve exactly once and return the complete JSON-safe comparison input."""
    allowed_tiers = get_access_tiers(case["role"], case.get("permissions") or [])
    docs = retrieve_fn(
        RagTurnInput.single_shot(case["question"]),
        access_tiers=allowed_tiers,
        top_k=EXPERIMENT_RETRIEVAL_TOP_K,
    )
    if not docs:
        raise LiveRagEvalError(f"retrieval returned no contexts for {case['id']!r}")
    unauthorized_sources = [
        {
            "source": document.metadata.get("source"),
            "access_tier": document.metadata.get("access_tier"),
        }
        for document in docs
        if not document.metadata.get("access_tier")
        or document.metadata.get("access_tier") not in allowed_tiers
    ]
    if unauthorized_sources:
        raise LiveRagEvalError(
            f"retrieval crossed access tiers for {case['id']!r}: {unauthorized_sources!r}"
        )
    tokenizer = tiktoken.get_encoding("cl100k_base")
    context_token_estimate = sum(len(tokenizer.encode(document.page_content)) for document in docs)
    if context_token_estimate > CONTEXT_TOKEN_CEILING:
        raise LiveRagEvalError(
            f"retrieval context exceeds frozen token ceiling for {case['id']!r}: "
            f"{context_token_estimate} > {CONTEXT_TOKEN_CEILING}"
        )
    return {
        "contexts": [document.page_content for document in docs],
        "sources": [dict(document.metadata) for document in docs],
        "retrieval_identity": retrieval_identity,
        "context_token_estimate": context_token_estimate,
        "context_tokenizer": "cl100k_base",
    }


def _validate_route_metadata(
    route_metadata: dict[str, Any],
    request_id: str,
    estimated_cost_usd: float,
) -> tuple[dict[str, Any], float, float | None]:
    try:
        require_generation_fields(route_metadata)
    except RagGenerationCostError as exc:
        raise LiveRagEvalError(str(exc)) from exc
    if str(route_metadata.get("generation_id") or "") != str(request_id or ""):
        raise LiveRagEvalError("generation metadata ID does not match answer request ID")
    try:
        cost_usd, provider_reported_cost_usd = derive_cost_usd(
            provider_reported_cost=route_metadata.get("total_cost"),
            estimated_cost_usd=estimated_cost_usd,
        )
    except RagGenerationCostError as exc:
        raise LiveRagEvalError(str(exc)) from exc
    return route_metadata, cost_usd, provider_reported_cost_usd


def _apply_openrouter_route_metadata(row: dict[str, Any], route_metadata: dict[str, Any]) -> None:
    """Patch one OpenRouter answer row with generation metadata (mutates in place)."""
    meta, cost_usd, provider_reported_cost_usd = _validate_route_metadata(
        route_metadata,
        str(row.get("request_id") or ""),
        float(row.get("estimated_cost_usd") or 0.0),
    )
    row.update(
        {
            "actual_provider": meta.get("provider_name"),
            "actual_provider_source": "route_metadata",
            "actual_model": meta.get("model"),
            "actual_model_source": "route_metadata",
            "provider_reported_cost_usd": provider_reported_cost_usd,
            "cost_usd": cost_usd,
            "route_metadata_status": "verified",
            "route_metadata_error": None,
            "upstream_request_id": meta.get("upstream_id"),
            "native_tokens_reasoning": meta.get("native_tokens_reasoning"),
            "native_tokens_prompt": meta.get("native_tokens_prompt"),
            "native_tokens_completion": meta.get("native_tokens_completion"),
        }
    )


def enrich_openrouter_answer_rows(
    rows: list[dict[str, Any]],
    *,
    route_metadata_fn: Callable[[str], dict[str, Any]] | None = None,
    max_workers: int = LIVE_ANSWER_MAX_WORKERS,
    progress_fn: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Fetch OpenRouter /generation metadata after the chat matrix finishes."""
    pending = [
        row
        for row in rows
        if row.get("requested_provider") == "openrouter"
        and row.get("route_metadata_status") == "pending"
        # Still enrich failed rows so native token fields are attested.
    ]
    if not pending:
        return rows
    fetch = route_metadata_fn or fetch_openrouter_generation_metadata
    report = progress_fn or (lambda _progress: None)
    report({"stage": "route_metadata", "completed": 0, "total": len(pending)})
    completed = 0
    completed_lock = Lock()

    def enrich_one(row: dict[str, Any]) -> None:
        nonlocal completed
        try:
            metadata = fetch(str(row.get("request_id") or ""))
            _apply_openrouter_route_metadata(row, metadata)
        except Exception as exc:  # noqa: BLE001 - preserve answer, gate closed
            row["route_metadata_status"] = "error"
            row["route_metadata_error"] = f"{type(exc).__name__}: {exc}"
            row["actual_provider"] = "unknown"
            row["actual_provider_source"] = "unavailable"
            row["actual_model"] = "unknown"
            row["actual_model_source"] = "unavailable"
        finally:
            with completed_lock:
                completed += 1
                done = completed
            report(
                {
                    "stage": "route_metadata",
                    "completed": done,
                    "total": len(pending),
                }
            )

    workers = max(1, min(max_workers, len(pending)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(enrich_one, pending))
    return rows


def _is_reasoning_model(arm: AnswerArm) -> bool:
    """Vendor-agnostic reasoning-model check, reusing each provider's own spec logic."""
    if arm.requested_provider == "openrouter":
        return resolve_openrouter_spec(arm.requested_model, settings).returns_reasoning
    if arm.requested_provider == "azure":
        return needs_azure_reasoning_completion_args(arm.requested_model)
    return False


class AnswerProvider(Protocol):
    def generation_kwargs(
        self,
        arm: AnswerArm,
        *,
        max_output_tokens: int,
        route_controls_by_arm: dict[str, OpenRouterControls] | None = None,
        openrouter_controls: OpenRouterControls | None = None,
    ) -> dict[str, Any]: ...

    def settle_route_metadata(
        self,
        raw: dict[str, Any],
        *,
        estimated_cost_usd: float,
        defer_route_metadata: bool,
        route_metadata_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str, str | None, float, float | None]: ...

    def resolve_provenance(
        self,
        raw: dict[str, Any],
        *,
        route_metadata: dict[str, Any],
        route_metadata_status: str,
    ) -> tuple[str, str, str, str]: ...


class AzureAnswerProvider:
    def generation_kwargs(
        self,
        arm: AnswerArm,
        *,
        max_output_tokens: int,
        route_controls_by_arm: dict[str, OpenRouterControls] | None = None,
        openrouter_controls: OpenRouterControls | None = None,
    ) -> dict[str, Any]:
        del route_controls_by_arm, openrouter_controls
        return {
            "chat_deployment": arm.requested_model,
            "max_output_tokens": max_output_tokens,
            "apply_reasoning_discipline": arm.apply_reasoning_discipline,
            "reasoning_effort": arm.reasoning_effort,
            "verbosity": arm.verbosity,
            "request_timeout_s": arm.request_timeout_s,
        }

    def settle_route_metadata(
        self,
        raw: dict[str, Any],
        *,
        estimated_cost_usd: float,
        defer_route_metadata: bool,
        route_metadata_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str, str | None, float, float | None]:
        del raw, defer_route_metadata, route_metadata_fn
        try:
            cost_usd, provider_reported = derive_cost_usd(
                provider_reported_cost=None,
                estimated_cost_usd=estimated_cost_usd,
            )
        except RagGenerationCostError as exc:
            raise LiveRagEvalError(str(exc)) from exc
        return {}, "not_applicable", None, cost_usd, provider_reported

    def resolve_provenance(
        self,
        raw: dict[str, Any],
        *,
        route_metadata: dict[str, Any],
        route_metadata_status: str,
    ) -> tuple[str, str, str, str]:
        del route_metadata, route_metadata_status
        actual_provider = str(raw.get("actual_provider") or "unknown")
        actual_provider_source = str(raw.get("actual_provider_source") or "unavailable")
        actual_model = str(raw.get("actual_model") or "unknown")
        actual_model_source = str(raw.get("actual_model_source") or "unavailable")
        return actual_provider, actual_provider_source, actual_model, actual_model_source


class OpenRouterAnswerProvider:
    def generation_kwargs(
        self,
        arm: AnswerArm,
        *,
        max_output_tokens: int,
        route_controls_by_arm: dict[str, OpenRouterControls] | None = None,
        openrouter_controls: OpenRouterControls | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "chat_deployment": arm.requested_model,
            "max_output_tokens": max_output_tokens,
            "apply_reasoning_discipline": arm.apply_reasoning_discipline,
            "reasoning_effort": arm.reasoning_effort,
            "verbosity": arm.verbosity,
            "request_timeout_s": arm.request_timeout_s,
        }
        arm_controls = (route_controls_by_arm or {}).get(arm.name)
        if openrouter_controls is not None:
            kwargs["openrouter_controls"] = openrouter_controls
        elif arm_controls is not None:
            kwargs["openrouter_controls"] = arm_controls
        else:
            raise LiveRagEvalError(f"no OpenRouter route controls configured for arm {arm.name!r}")
        return kwargs

    def settle_route_metadata(
        self,
        raw: dict[str, Any],
        *,
        estimated_cost_usd: float,
        defer_route_metadata: bool,
        route_metadata_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str, str | None, float, float | None]:
        route_metadata: dict[str, Any] = {}
        route_metadata_status = "not_applicable"
        route_metadata_error: str | None = None
        cost_usd = estimated_cost_usd
        provider_reported = None

        if defer_route_metadata:
            route_metadata_status = "pending"
            try:
                cost_usd, provider_reported = derive_cost_usd(
                    provider_reported_cost=None,
                    estimated_cost_usd=estimated_cost_usd,
                )
            except RagGenerationCostError as exc:
                raise LiveRagEvalError(str(exc)) from exc
        else:
            try:
                fetcher = route_metadata_fn or fetch_openrouter_generation_metadata
                raw_meta = fetcher(str(raw.get("request_id") or ""))
                route_metadata, cost_usd, provider_reported = _validate_route_metadata(
                    raw_meta,
                    str(raw.get("request_id") or ""),
                    estimated_cost_usd,
                )
                route_metadata_status = "verified"
            except Exception as exc:  # noqa: BLE001 - preserve answer, gate route evidence closed
                route_metadata_status = "error"
                route_metadata_error = f"{type(exc).__name__}: {exc}"
                route_metadata = {}
                try:
                    cost_usd, provider_reported = derive_cost_usd(
                        provider_reported_cost=None,
                        estimated_cost_usd=estimated_cost_usd,
                    )
                except RagGenerationCostError as err:
                    raise LiveRagEvalError(str(err)) from err

        return (
            route_metadata,
            route_metadata_status,
            route_metadata_error,
            cost_usd,
            provider_reported,
        )

    def resolve_provenance(
        self,
        raw: dict[str, Any],
        *,
        route_metadata: dict[str, Any],
        route_metadata_status: str,
    ) -> tuple[str, str, str, str]:
        del raw
        if route_metadata_status == "verified":
            return (
                str(route_metadata.get("provider_name") or "unknown"),
                "route_metadata",
                str(route_metadata.get("model") or "unknown"),
                "route_metadata",
            )
        return "unknown", "unavailable", "unknown", "unavailable"


def answer_provider_for(arm: AnswerArm) -> AnswerProvider:
    if arm.requested_provider == "azure":
        return AzureAnswerProvider()
    if arm.requested_provider == "openrouter":
        return OpenRouterAnswerProvider()
    raise LiveRagEvalError(f"unsupported answer provider {arm.requested_provider!r}")


def answer_frozen_case(
    frozen_case: dict[str, Any],
    arm: AnswerArm,
    *,
    harness_output_cap_tokens_by_arm: dict[str, int] | None = None,
    est_answer_usd_by_arm: dict[str, float] | None = None,
    route_controls_by_arm: dict[str, OpenRouterControls] | None = None,
    generation_fn: Callable[..., dict[str, Any]] = invoke_rag_generation_with_usage,
    route_metadata_fn: Callable[[str], dict[str, Any]] | None = None,
    max_output_tokens: int | None = None,
    defer_route_metadata: bool = True,
    openrouter_controls: OpenRouterControls | None = None,
) -> dict[str, Any]:
    """Generate one answer from the frozen context; never invoke retrieval."""
    provider = answer_provider_for(arm)
    resolved_harness_caps = harness_output_cap_tokens_by_arm or {}
    resolved_est_answer = est_answer_usd_by_arm or {}
    if max_output_tokens is None:
        max_output_tokens = resolved_harness_caps.get(arm.name)
        if max_output_tokens is None:
            max_output_tokens = arm.harness_output_cap_tokens
        if max_output_tokens is None:
            raise LiveRagEvalError(f"no output ceiling configured for arm {arm.name!r}")
    retrieval = frozen_case["retrieval"]
    sources = retrieval["sources"]
    docs = [
        Document(page_content=context, metadata=dict(sources[index]))
        for index, context in enumerate(retrieval["contexts"])
    ]
    generation_kwargs = provider.generation_kwargs(
        arm,
        max_output_tokens=max_output_tokens,
        route_controls_by_arm=route_controls_by_arm,
        openrouter_controls=openrouter_controls,
    )
    raw = generation_fn(
        RagTurnInput.single_shot(frozen_case["case"]["question"]),
        docs=docs,
        **generation_kwargs,
    )
    input_tokens = int(raw.get("input_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    if input_tokens < 0 or output_tokens < 0:
        raise LiveRagEvalError("provider returned negative token usage")
    est_fallback = resolved_est_answer.get(arm.name, 0.0)
    estimated_cost_usd = (
        est_fallback
        if input_tokens == 0 and output_tokens == 0
        else estimate_cost_usd(
            arm.requested_model,
            input_tokens,
            output_tokens,
        )
    )
    (
        route_metadata,
        route_metadata_status,
        route_metadata_error,
        cost_usd,
        provider_reported_cost_usd,
    ) = provider.settle_route_metadata(
        raw,
        estimated_cost_usd=estimated_cost_usd,
        defer_route_metadata=defer_route_metadata,
        route_metadata_fn=route_metadata_fn,
    )
    actual_provider, actual_provider_source, actual_model, actual_model_source = (
        provider.resolve_provenance(
            raw,
            route_metadata=route_metadata,
            route_metadata_status=route_metadata_status,
        )
    )
    return {
        "answer": raw.get("answer") or "",
        "requested_provider": raw.get("requested_provider") or arm.requested_provider,
        "requested_model": raw.get("requested_model") or arm.requested_model,
        "actual_provider": actual_provider,
        "actual_provider_source": actual_provider_source,
        "actual_provider_endpoint": raw.get("actual_provider_endpoint"),
        "actual_model": actual_model,
        "actual_model_source": actual_model_source,
        "request_id": raw.get("request_id") or "unavailable",
        "request_id_source": raw.get("request_id_source") or "callback",
        "finish_reason": raw.get("finish_reason") or "unknown",
        "latency_ms": int(raw.get("latency_ms") or 0),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "max_output_tokens": max_output_tokens,
        "harness_output_cap_tokens": arm.harness_output_cap_tokens or max_output_tokens,
        "attested_output_cap_tokens": arm.attested_output_cap_tokens,
        "cost_usd": cost_usd,
        "estimated_cost_usd": estimated_cost_usd,
        "provider_reported_cost_usd": provider_reported_cost_usd,
        "route_metadata_status": route_metadata_status,
        "route_metadata_error": route_metadata_error,
        "upstream_request_id": route_metadata.get("upstream_id"),
        "error": raw.get("error"),
        "reasoning_text": raw.get("reasoning_text"),
        "reasoning_evidence": raw.get("reasoning_evidence"),
        "response_metadata": raw.get("response_metadata"),
        "native_tokens_reasoning": route_metadata.get("native_tokens_reasoning"),
        "native_tokens_prompt": route_metadata.get("native_tokens_prompt"),
        "native_tokens_completion": route_metadata.get("native_tokens_completion"),
    }


def rendered_prompt_for_hash(arm: AnswerArm, frozen_case: dict[str, Any]) -> str:
    """Render the prompt string the answer path would build, for freeze attestation."""
    retrieval = frozen_case["retrieval"]
    sources = retrieval["sources"]
    docs = [
        Document(page_content=context, metadata=dict(sources[index]))
        for index, context in enumerate(retrieval["contexts"])
    ]
    prompt_vars = generation_prompt_for_rag_turn(
        RagTurnInput.single_shot(frozen_case["case"]["question"]),
        docs,
    )
    if arm.apply_reasoning_discipline:
        from app.prompts.reasoning_discipline import REASONING_FINAL_ANSWER_DISCIPLINE

        context = str(prompt_vars.get("context") or "")
        prompt_vars = {
            **prompt_vars,
            "context": f"{context.rstrip()}\n\n{REASONING_FINAL_ANSWER_DISCIPLINE}".strip(),
        }
    from app.rag.chains.document_chain import _select_prompt

    return str(_select_prompt(prompt_vars))


def effort_probe_controls(
    base: OpenRouterControls | None,
    *,
    effort: str,
) -> OpenRouterControls | None:
    """Per-effort OpenRouter controls for the pre-spend probe.

    Wire effort travels via ``controls`` (``extra_body["reasoning"]``), not
    ``AnswerArm.reasoning_effort`` — see ``factory.get_chat_model`` Azure-only note.
    """
    if base is None:
        return None
    return replace(base, reasoning_effort=normalize_reasoning_effort(effort))


def run_effort_probe(
    frozen_cases: list[dict[str, Any]],
    *,
    arm: AnswerArm,
    low_effort: str,
    high_effort: str,
    reserve_usd: float,
    low_controls: OpenRouterControls | None = None,
    high_controls: OpenRouterControls | None = None,
    answer_fn: Callable[..., dict[str, Any]] = answer_frozen_case,
    reserve_fn: Callable[[float], str] | None = None,
    commit_fn: Callable[[str, float], None] | None = None,
    release_fn: Callable[[str], None] | None = None,
    ledger_path: Path | None = None,
    min_samples: int = MIN_SAMPLES_PER_EFFORT,
) -> EffortProbeResult:
    """Probe whether reasoning_effort moves the model, on either provider."""
    if len(frozen_cases) < min_samples:
        raise LiveRagEvalError(
            f"effort probe needs >={min_samples} frozen cases; got {len(frozen_cases)}"
        )
    ordered = sorted(
        frozen_cases,
        key=lambda frozen_case: hashlib.sha256(
            str(frozen_case["case"]["id"]).encode("utf-8")
        ).hexdigest(),
    )
    probe_cases = ordered[:min_samples]

    _reserve = reserve_fn or (
        lambda amount: acquire(PHASE, arm.requested_model, amount, ledger_path=ledger_path)
    )
    _commit = commit_fn or (
        lambda reservation_id, actual: commit(reservation_id, actual, ledger_path=ledger_path)
    )
    _release = release_fn or (
        lambda reservation_id: release(reservation_id, ledger_path=ledger_path)
    )

    reservation_id = _reserve(reserve_usd)
    spent_usd = 0.0
    low_tokens: list[int | None] = []
    high_tokens: list[int | None] = []
    spent_lock = Lock()
    # Frozen submission order: low samples first, then high — pairs run concurrently.
    jobs: list[tuple[str, OpenRouterControls | None, dict[str, Any], int]] = []
    for effort, controls, bucket_index in (
        (low_effort, low_controls, 0),
        (high_effort, high_controls, 1),
    ):
        for frozen_case in probe_cases:
            jobs.append((effort, controls, frozen_case, bucket_index))

    def run_probe_sample(
        effort: str,
        controls: OpenRouterControls | None,
        frozen_case: dict[str, Any],
        bucket_index: int,
    ) -> tuple[int, int | None, float]:
        probe_arm = replace(arm, reasoning_effort=effort)
        kwargs: dict[str, Any] = {"defer_route_metadata": False}
        if controls is not None:
            kwargs["openrouter_controls"] = controls
        response = answer_fn(frozen_case, probe_arm, **kwargs)
        return (
            bucket_index,
            reasoning_tokens_spent(response),
            float(response.get("cost_usd") or 0.0),
        )

    try:
        workers = max(1, min(EFFORT_PROBE_MAX_WORKERS, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(run_probe_sample, effort, controls, frozen_case, bucket_index)
                for effort, controls, frozen_case, bucket_index in jobs
            ]
            for future in as_completed(futures):
                bucket_index, token_count, cost = future.result()
                with spent_lock:
                    spent_usd += cost
                if bucket_index == 0:
                    low_tokens.append(token_count)
                else:
                    high_tokens.append(token_count)
    except Exception:
        _release(reservation_id)
        raise
    _commit(reservation_id, spent_usd)
    return evaluate_effort_separation(low_tokens, high_tokens, min_samples=min_samples)


def require_effort_separated(result: EffortProbeResult, *, model: str) -> None:
    """Raise if the effort probe did not show sufficient separation."""
    if not result.passed:
        raise LiveRagEvalError(
            f"effort probe refused to proceed for {model!r}: {result.verdict} - {result.detail}"
        )


def fetch_openrouter_generation_metadata(request_id: str) -> dict[str, Any]:
    """Resolve the OpenRouter generation id to its actual upstream provider and cost."""
    if not request_id or request_id == "unavailable":
        raise LiveRagEvalError("OpenRouter response did not expose a generation id")
    if not settings.openrouter_api_key:
        raise LiveRagEvalError("OPENROUTER_API_KEY is unset")
    url = f"{validated_openrouter_base_url()}/generation"
    last_error: Exception | None = None
    for delay_seconds in (0.0, 1.0, 2.0, 4.0, 8.0):
        if delay_seconds:
            sleep(delay_seconds)
        try:
            response = get_sync_http_client(current_process_resources()).get(
                url,
                params={"id": request_id},
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            response.raise_for_status()
            if response.url.host != "openrouter.ai" or response.url.path != "/api/v1/generation":
                raise LiveRagEvalError("OpenRouter metadata response URL drifted")
            data = response.json().get("data") or {}
            required = ("id", "provider_name", "model")
            missing = [field for field in required if not data.get(field)]
            if missing:
                raise LiveRagEvalError(
                    f"generation metadata omitted required fields: {', '.join(missing)}"
                )
            if str(data["id"]) != request_id:
                raise LiveRagEvalError("generation metadata ID does not match requested generation")
            return {
                "generation_id": data.get("id"),
                "provider_name": data.get("provider_name"),
                "model": data.get("model"),
                "total_cost": data.get("total_cost"),
                "upstream_id": data.get("upstream_id"),
                "native_tokens_prompt": data.get("native_tokens_prompt"),
                "native_tokens_completion": data.get("native_tokens_completion"),
                "native_tokens_reasoning": data.get("native_tokens_reasoning"),
            }
        except Exception as exc:  # noqa: BLE001 - bounded metadata availability retry
            last_error = exc
    raise LiveRagEvalError(
        f"OpenRouter generation metadata unavailable for {request_id}: {last_error}"
    )
