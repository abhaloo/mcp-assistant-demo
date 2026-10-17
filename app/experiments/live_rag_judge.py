"""Luna-pinned RAGAS judge with literal canaries and a repeated frozen subset."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import urlparse

from openai import AzureOpenAI

from app.config import settings
from app.experiments.core.money import BUDGET_EPSILON_USD, estimate_cost_usd
from app.experiments.core.protocol import OracleValidation
from app.experiments.rag_freeze import EST_JUDGE_PER_SAMPLE_USD, JUDGE_DEPLOYMENT
from app.experiments.rag_judge_validation import (
    validate_canary_scores,
    validate_judge_call_evidence,
    validate_judge_evidence,
)

JUDGE_MODEL_VERSION = "2026-07-09"
JUDGE_ALLOWED_RESPONSE_MODELS = (
    JUDGE_DEPLOYMENT,
    f"{JUDGE_DEPLOYMENT}-{JUDGE_MODEL_VERSION}",
)
JUDGE_AZURE_ACCOUNT = "mcp-rag-qa-openai-service"
JUDGE_FORECAST_SAFETY_FACTOR = 1.10
JUDGE_MAX_WORKERS = 8
METRICS = ("factual_correctness", "faithfulness")


class JudgeBudgetExceededError(RuntimeError):
    """A bounded judge stopped after paid work and exposes the known partial spend."""

    def __init__(
        self,
        message: str,
        *,
        actual_spend_usd: float,
        forecast_usd: float,
        reserved_usd: float,
    ) -> None:
        super().__init__(message)
        self.actual_spend_usd = actual_spend_usd
        self.forecast_usd = forecast_usd
        self.reserved_usd = reserved_usd


CANARY_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "exact",
        "question": "What is the capital of France?",
        "answer": "The capital of France is Paris.",
        "expected_answer": "The capital of France is Paris.",
        "retrieved_contexts": ["France's capital city is Paris."],
        "operational_failure": None,
    },
    {
        "id": "contradiction",
        "question": "What is the capital of France?",
        "answer": "The capital of France is Berlin.",
        "expected_answer": "The capital of France is Paris.",
        "retrieved_contexts": ["France's capital city is Paris."],
        "operational_failure": None,
    },
    {
        "id": "complete_multi_claim",
        "question": "What support coverage is available?",
        "answer": (
            "Standard support runs 8 AM to 5 PM, and critical incidents use the after-hours pager."
        ),
        "expected_answer": (
            "Standard support runs 8 AM to 5 PM, and critical incidents use the after-hours pager."
        ),
        "retrieved_contexts": [
            "Support hours are 8 AM to 5 PM. Critical incidents use the after-hours pager."
        ],
        "operational_failure": None,
    },
    {
        "id": "partial_multi_claim",
        "question": "What support coverage is available?",
        "answer": "Standard support runs 8 AM to 5 PM.",
        "expected_answer": (
            "Standard support runs 8 AM to 5 PM, and critical incidents use the after-hours pager."
        ),
        "retrieved_contexts": [
            "Support hours are 8 AM to 5 PM. Critical incidents use the after-hours pager."
        ],
        "operational_failure": None,
    },
    {
        "id": "grounded_redaction",
        "question": "What phone number should I call?",
        "answer": "The available context redacts the phone number as <PHONE_NUMBER>.",
        "expected_answer": "The available context redacts the phone number as <PHONE_NUMBER>.",
        "retrieved_contexts": ["Phone: <PHONE_NUMBER>"],
        "operational_failure": None,
    },
    {
        "id": "pii_hallucination",
        "question": "What phone number should I call?",
        "answer": "Call +255 712 345 678.",
        "expected_answer": "The available context redacts the phone number as <PHONE_NUMBER>.",
        "retrieved_contexts": ["Phone: <PHONE_NUMBER>"],
        "operational_failure": None,
    },
)

CANARY_EXPECTATIONS: dict[str, dict[str, float]] = {
    "exact": {"factual_correctness_min": 0.8, "faithfulness_min": 0.8},
    "contradiction": {"factual_correctness_max": 0.2, "faithfulness_max": 0.2},
    "complete_multi_claim": {"factual_correctness_min": 0.8, "faithfulness_min": 0.8},
    "partial_multi_claim": {"factual_correctness_max": 0.75, "faithfulness_min": 0.8},
    "grounded_redaction": {"factual_correctness_min": 0.8, "faithfulness_min": 0.8},
    "pii_hallucination": {"factual_correctness_max": 0.2, "faithfulness_max": 0.2},
}


def judge_version() -> dict[str, Any]:
    """Return the exact model, package, canary, and local judge-code identity."""
    from app.eval.document_rag.ragas_compat import ensure_ragas_importable

    ensure_ragas_importable()
    local_files = [
        Path(__file__),
        Path(__file__).with_name("rag_judge_validation.py"),
        Path(__file__).parents[1] / "eval" / "document_rag" / "ragas_judge_client.py",
    ]
    code_hasher = hashlib.sha256()
    for path in local_files:
        code_hasher.update(path.name.encode("utf-8"))
        code_hasher.update(path.read_bytes())
    canary_payload = json.dumps(
        {"rows": CANARY_ROWS, "expectations": CANARY_EXPECTATIONS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    endpoint_host = urlparse(settings.azure_endpoint or "").hostname
    expected_host = f"{JUDGE_AZURE_ACCOUNT}.openai.azure.com"
    if endpoint_host != expected_host:
        raise RuntimeError(f"judge Azure endpoint does not match frozen account: {endpoint_host!r}")
    return {
        "provider": "azure",
        "model": JUDGE_DEPLOYMENT,
        "ragas": version("ragas"),
        "instructor": version("instructor"),
        "pydantic": version("pydantic"),
        "code_sha256": f"sha256:{code_hasher.hexdigest()}",
        "canary_suite_sha256": f"sha256:{hashlib.sha256(canary_payload).hexdigest()}",
        "metrics": list(METRICS),
        "deployment_attestation": {
            # Every field here is either compared against the live Azure state by
            # verify_live_azure_deployment (source, resource_group, account,
            # deployment, model_name, model_version, deployment_state,
            # provisioning_state, sku) or read downstream by rag_judge_validation.py
            # (endpoint_host, allowed_response_models). None is decorative -- there
            # is no hand-set "observed_at" date here: verify_live_azure_deployment
            # stamps a real verified_at on its return value, which is what this
            # attestation becomes once the live check runs.
            "source": "azure_resource_manager",
            "resource_group": "mcp-rag-qa",
            "account": JUDGE_AZURE_ACCOUNT,
            "endpoint_host": endpoint_host,
            "deployment": JUDGE_DEPLOYMENT,
            "model_name": JUDGE_DEPLOYMENT,
            "model_version": JUDGE_MODEL_VERSION,
            "deployment_state": "Running",
            "provisioning_state": "Succeeded",
            "sku": "GlobalStandard",
            "allowed_response_models": list(JUDGE_ALLOWED_RESPONSE_MODELS),
        },
    }


@dataclass(frozen=True)
class ScoringPass:
    rows: list[dict[str, Any]]
    call_evidence: list[dict[str, Any]]


class ScoreFn(Protocol):
    def __call__(
        self,
        rows: list[dict[str, Any]],
        *,
        pass_name: str,
        session: tuple[_JudgeCallCapture, Any, Any] | None = None,
    ) -> ScoringPass:
        """The scorer is handed the judge session so its calls land in one evidence

        capture; the per-batch cost is the delta on that capture, not the scorer's
        own report.
        """
        ...


class _JudgeCallCapture:
    """Capture provider evidence attached by Instructor to each structured result."""

    def __init__(
        self,
        pass_name: str,
        progress_fn: Callable[[dict[str, Any]], None],
    ) -> None:
        self._pass_name = pass_name
        self._progress_fn = progress_fn
        self._rows: list[dict[str, Any]] = []
        self._lock = Lock()

    @staticmethod
    def _get(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def record_result(
        self,
        result: Any,
        *,
        started: float,
        requested_provider: str,
        requested_model: str,
        endpoint_host: str,
        provider_identity_source: str,
    ) -> None:
        raw = getattr(result, "_raw_response", None)
        usage = self._get(raw, "usage", {}) or {}
        choices = self._get(raw, "choices", []) or []
        first_choice = choices[0] if choices else {}
        provider_request_id = self._get(raw, "id")
        row = {
            "pass": self._pass_name,
            "requested_provider": requested_provider,
            "requested_model": requested_model,
            "actual_provider": requested_provider,
            "actual_provider_source": provider_identity_source,
            "endpoint_host": endpoint_host,
            "actual_model": str(self._get(raw, "model") or "unknown"),
            "request_id": str(provider_request_id or "unavailable"),
            "request_id_source": "provider" if provider_request_id else "unavailable",
            "finish_reason": str(self._get(first_choice, "finish_reason") or "unknown"),
            "latency_ms": max(0, round((perf_counter() - started) * 1000)),
            "input_tokens": int(self._get(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(self._get(usage, "completion_tokens", 0) or 0),
            "error": None,
        }
        with self._lock:
            self._rows.append(row)
            completed = len(self._rows)
        self._progress_fn({"stage": f"judge_calls:{self._pass_name}", "completed": completed})

    def record_error(
        self,
        error: BaseException,
        *,
        started: float,
        requested_provider: str,
        requested_model: str,
        endpoint_host: str,
        provider_identity_source: str,
    ) -> None:
        with self._lock:
            self._rows.append(
                {
                    "pass": self._pass_name,
                    "requested_provider": requested_provider,
                    "requested_model": requested_model,
                    "actual_provider": requested_provider,
                    "actual_provider_source": provider_identity_source,
                    "endpoint_host": endpoint_host,
                    "actual_model": "unknown",
                    "request_id": "unavailable",
                    "request_id_source": "unavailable",
                    "finish_reason": "error",
                    "latency_ms": max(0, round((perf_counter() - started) * 1000)),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            completed = len(self._rows)
        self._progress_fn({"stage": f"judge_calls:{self._pass_name}", "completed": completed})

    def evidence(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._rows]


def _instrument_instructor_llm(
    llm: Any,
    capture: _JudgeCallCapture,
    *,
    requested_provider: str | None = None,
    requested_model: str | None = None,
    endpoint_host: str = "test.invalid",
    provider_identity_source: str = "configured_client",
) -> Any:
    """Record Instructor's raw OpenAI response without relying on ignored callbacks."""
    original_generate = llm.generate
    original_agenerate = llm.agenerate
    requested_provider = requested_provider or str(getattr(llm, "provider", "unknown"))
    requested_model = requested_model or str(getattr(llm, "model", "unknown"))

    def generate(prompt: str, response_model: Any):
        started = perf_counter()
        try:
            result = original_generate(prompt, response_model)
        except Exception as exc:
            if not bool(getattr(llm, "is_async", False)):
                capture.record_error(
                    exc,
                    started=started,
                    requested_provider=requested_provider,
                    requested_model=requested_model,
                    endpoint_host=endpoint_host,
                    provider_identity_source=provider_identity_source,
                )
            raise
        if not bool(getattr(llm, "is_async", False)):
            capture.record_result(
                result,
                started=started,
                requested_provider=requested_provider,
                requested_model=requested_model,
                endpoint_host=endpoint_host,
                provider_identity_source=provider_identity_source,
            )
        return result

    async def agenerate(prompt: str, response_model: Any):
        started = perf_counter()
        try:
            result = await original_agenerate(prompt, response_model)
        except Exception as exc:
            capture.record_error(
                exc,
                started=started,
                requested_provider=requested_provider,
                requested_model=requested_model,
                endpoint_host=endpoint_host,
                provider_identity_source=provider_identity_source,
            )
            raise
        capture.record_result(
            result,
            started=started,
            requested_provider=requested_provider,
            requested_model=requested_model,
            endpoint_host=endpoint_host,
            provider_identity_source=provider_identity_source,
        )
        return result

    llm.generate = generate
    llm.agenerate = agenerate
    return llm


def _instrument_verified_azure_judge(llm: Any, capture: _JudgeCallCapture) -> Any:
    """Bind telemetry to the configured Azure client and frozen endpoint."""
    instructor_client = getattr(llm, "client", None)
    azure_client = getattr(instructor_client, "client", None)
    if not isinstance(azure_client, AzureOpenAI):
        raise RuntimeError("judge LLM is not backed by an AzureOpenAI client")
    endpoint_host = urlparse(str(getattr(azure_client, "base_url", ""))).hostname
    expected_host = urlparse(settings.azure_endpoint or "").hostname
    if not endpoint_host or endpoint_host != expected_host:
        raise RuntimeError("judge Azure client endpoint does not match frozen configuration")
    return _instrument_instructor_llm(
        llm,
        capture,
        requested_provider="azure",
        requested_model=JUDGE_DEPLOYMENT,
        endpoint_host=endpoint_host,
        provider_identity_source="azure_client_type_and_base_url",
    )


def _build_ragas_judge_session(
    pass_name: str,
    progress_fn: Callable[[dict[str, Any]], None],
) -> tuple[_JudgeCallCapture, Any, Any]:
    """Create one reusable RAGAS judge session (capture + both metric LLMs)."""
    from app.eval.document_rag.ragas_judge_client import get_judge_llm

    capture = _JudgeCallCapture(pass_name, progress_fn)
    factual_llm = _instrument_verified_azure_judge(
        get_judge_llm(deployment=JUDGE_DEPLOYMENT), capture
    )
    faithfulness_llm = _instrument_verified_azure_judge(
        get_judge_llm(deployment=JUDGE_DEPLOYMENT), capture
    )
    return capture, factual_llm, faithfulness_llm


def _score_samples_with_session(
    rows: list[dict[str, Any]],
    *,
    pass_name: str,
    progress_fn: Callable[[dict[str, Any]], None],
    session: tuple[_JudgeCallCapture, Any, Any] | None = None,
) -> ScoringPass:
    from app.eval.document_rag.ragas_compat import ensure_ragas_importable

    ensure_ragas_importable()
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics import FactualCorrectness, Faithfulness
    from ragas.run_config import RunConfig

    output = [dict(row) for row in rows]
    samples: list[SingleTurnSample] = []
    sample_indexes: list[int] = []
    for index, row in enumerate(rows):
        if row.get("operational_failure"):
            output[index]["factual_correctness"] = 0.0
            output[index]["faithfulness"] = 0.0
            continue
        samples.append(
            SingleTurnSample(
                user_input=str(row["question"]),
                response=str(row["answer"]),
                retrieved_contexts=list(row.get("retrieved_contexts") or []),
                reference=str(row.get("expected_answer") or ""),
            )
        )
        sample_indexes.append(index)
    if not samples:
        if session is not None:
            return ScoringPass(rows=output, call_evidence=session[0].evidence())
        return ScoringPass(rows=output, call_evidence=[])

    if session is None:
        capture, factual_llm, faithfulness_llm = _build_ragas_judge_session(pass_name, progress_fn)
    else:
        capture, factual_llm, faithfulness_llm = session

    result = evaluate(
        dataset=EvaluationDataset(samples=samples),
        metrics=[
            FactualCorrectness(llm=factual_llm),
            Faithfulness(llm=faithfulness_llm),
        ],
        run_config=RunConfig(
            timeout=180,
            max_retries=2,
            max_wait=30,
            max_workers=JUDGE_MAX_WORKERS,
            seed=42,
        ),
        show_progress=False,
        batch_size=8,
        raise_exceptions=False,
    )
    data_frame = result.to_pandas()

    def column(name: str) -> str | None:
        if name in data_frame.columns:
            return name
        return next(
            (str(item) for item in data_frame.columns if str(item).startswith(name)),
            None,
        )

    columns = {metric: column(metric) for metric in METRICS}
    for sample_index, row_index in enumerate(sample_indexes):
        for metric, metric_column in columns.items():
            raw_value = (
                data_frame.iloc[sample_index].get(metric_column)
                if metric_column is not None
                else None
            )
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = math.nan
            output[row_index][metric] = value if math.isfinite(value) else None
    return ScoringPass(rows=output, call_evidence=capture.evidence())


class LunaRagasJudge:
    """Run canary, primary, and repeatability passes with one frozen policy."""

    def __init__(
        self,
        *,
        arms: tuple[str, str] = ("prod-mini", "forced-deepseek"),
        repeats: int = 3,
        score_fn: ScoreFn | None = None,
        progress_fn: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._arms = arms
        self._repeats = repeats
        self._progress_fn = progress_fn or (lambda _progress: None)
        self._score_fn = score_fn or (
            lambda rows, *, pass_name, session=None: _score_samples_with_session(
                rows,
                pass_name=pass_name,
                progress_fn=self._progress_fn,
                session=session,
            )
        )
        self._canary_pass: ScoringPass | None = None
        self._preflight_validation: OracleValidation | None = None
        self._available_budget_usd: float | None = None
        self._budget_batch_size: int | None = None
        self._preflight_accounted_externally = False

    def reset_for_attempt(self) -> None:
        """Discard cached evidence and budget state before a distinct attempt."""
        self._canary_pass = None
        self._preflight_validation = None
        self._available_budget_usd = None
        self._budget_batch_size = None
        self._preflight_accounted_externally = False

    def mark_preflight_cost_accounted(self) -> None:
        """Declare that the orchestrator committed this attempt's canary cost."""
        if self._preflight_validation is None:
            raise RuntimeError("judge preflight must run before its cost can be accounted")
        self._preflight_accounted_externally = True

    def configure_budget(self, *, available_usd: float, batch_size: int = 24) -> None:
        """Bound future RAGAS scheduling to a reconciled main-judge reservation."""
        if available_usd <= 0 or batch_size < 1:
            raise ValueError("judge budget and batch size must be positive")
        self._available_budget_usd = available_usd
        self._budget_batch_size = batch_size

    @staticmethod
    def _cost(call_evidence: list[dict[str, Any]], *, sample_count: int) -> float:
        usage_cost = sum(
            estimate_cost_usd(
                JUDGE_DEPLOYMENT,
                int(call.get("input_tokens") or 0),
                int(call.get("output_tokens") or 0),
            )
            for call in call_evidence
        )
        return max(usage_cost, sample_count * EST_JUDGE_PER_SAMPLE_USD)

    def preflight(self, judge_config: dict[str, Any]) -> OracleValidation:
        """Run and cache the bounded semantic/provenance canary cell."""
        if self._preflight_validation is not None:
            return self._preflight_validation
        self._progress_fn({"stage": "judge_canary", "completed": 0, "total": 1})
        canary_pass = self._score_fn([dict(row) for row in CANARY_ROWS], pass_name="canary")
        canary_failures = validate_canary_scores(canary_pass.rows, CANARY_EXPECTATIONS)
        call_failures = validate_judge_call_evidence(
            canary_pass.call_evidence,
            required_passes=("canary",),
            judge_version=dict(judge_config["version"]),
        )
        cost_usd = self._cost(canary_pass.call_evidence, sample_count=len(CANARY_ROWS))
        projection = judge_config.get("projection") or {}
        main_samples = int(projection.get("primary_judge_samples") or 0) + int(
            projection.get("repeatability_judge_samples") or 0
        )
        forecast_main_cost_usd = (
            (cost_usd / len(CANARY_ROWS)) * main_samples * JUDGE_FORECAST_SAFETY_FACTOR
            if main_samples
            else 0.0
        )
        validation = OracleValidation(
            canaries_passed=not canary_failures,
            repeatability_passed=True,
            call_evidence_passed=not call_failures,
            details={
                "judge_version": dict(judge_config["version"]),
                "canary_failures": canary_failures,
                "call_evidence": canary_pass.call_evidence,
                "call_evidence_failures": call_failures,
                "cost_usd": cost_usd,
                "forecast_main_cost_usd": forecast_main_cost_usd,
                "forecast_safety_factor": JUDGE_FORECAST_SAFETY_FACTOR,
            },
        )
        self._canary_pass = canary_pass
        self._preflight_validation = validation
        self._progress_fn({"stage": "judge_canary_complete", "completed": 1, "total": 1})
        return validation

    def __call__(
        self,
        rows: list[dict[str, Any]],
        judge_config: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], Any]:
        subset_case_ids = tuple(judge_config["repeatability_case_ids"])
        sample_order_seed = int(judge_config["sample_order_seed"])
        preflight = self.preflight(judge_config)
        if not (
            preflight.canaries_passed
            and preflight.call_evidence_passed
            and self._canary_pass is not None
        ):
            raise RuntimeError("judge canary preflight did not pass")
        canary_pass = self._canary_pass

        def blinded_order(pass_rows: list[dict[str, Any]], pass_name: str) -> list[dict[str, Any]]:
            return sorted(
                pass_rows,
                key=lambda row: hashlib.sha256(
                    (
                        f"{sample_order_seed}:{pass_name}:{row.get('case_id')}:"
                        f"{row.get('arm')}:{row.get('repeat')}"
                    ).encode()
                ).hexdigest(),
            )

        primary_input = blinded_order(rows, "primary")
        repeatability_input = blinded_order(
            [row for row in rows if row.get("case_id") in set(subset_case_ids)],
            "repeatability",
        )
        total_main_samples = len(rows) + len(repeatability_input)
        main_spent_usd = 0.0
        main_samples_completed = 0

        if self._available_budget_usd is not None:
            initial_forecast = (
                float(preflight.details["cost_usd"])
                / len(CANARY_ROWS)
                * total_main_samples
                * JUDGE_FORECAST_SAFETY_FACTOR
            )
            if initial_forecast > self._available_budget_usd + BUDGET_EPSILON_USD:
                raise JudgeBudgetExceededError(
                    "judge cost forecast exceeds the available reservation",
                    actual_spend_usd=0.0,
                    forecast_usd=initial_forecast,
                    reserved_usd=self._available_budget_usd,
                )

        def score_pass(pass_rows: list[dict[str, Any]], *, pass_name: str) -> ScoringPass:
            nonlocal main_spent_usd, main_samples_completed
            if self._budget_batch_size is None:
                return self._score_fn(pass_rows, pass_name=pass_name)
            scored_rows: list[dict[str, Any]] = []
            session = _build_ragas_judge_session(pass_name, self._progress_fn)
            capture = session[0]
            prev_evidence_len = 0
            call_evidence: list[dict[str, Any]] = []
            for offset in range(0, len(pass_rows), self._budget_batch_size):
                batch = pass_rows[offset : offset + self._budget_batch_size]
                result = self._score_fn(batch, pass_name=pass_name, session=session)
                scored_rows.extend(result.rows)
                batch_evidence = capture.evidence()[prev_evidence_len:]
                if not batch_evidence and result.call_evidence:
                    batch_evidence = result.call_evidence
                call_evidence.extend(batch_evidence)
                prev_evidence_len = len(capture.evidence())
                main_spent_usd += self._cost(batch_evidence, sample_count=len(batch))
                main_samples_completed += len(batch)
                remaining_samples = total_main_samples - main_samples_completed
                observed_per_sample = main_spent_usd / main_samples_completed
                canary_per_sample = float(preflight.details["cost_usd"]) / len(CANARY_ROWS)
                forecast = main_spent_usd + (
                    max(observed_per_sample, canary_per_sample)
                    * remaining_samples
                    * JUDGE_FORECAST_SAFETY_FACTOR
                )
                self._progress_fn(
                    {
                        "stage": f"judge_budget:{pass_name}",
                        "completed": main_samples_completed,
                        "total": total_main_samples,
                        "spent_usd": round(main_spent_usd, 6),
                        "forecast_usd": round(forecast, 6),
                    }
                )
                if (
                    self._available_budget_usd is not None
                    and forecast > self._available_budget_usd + BUDGET_EPSILON_USD
                ):
                    raise JudgeBudgetExceededError(
                        "observed judge cost forecast no longer fits the reservation",
                        actual_spend_usd=main_spent_usd,
                        forecast_usd=forecast,
                        reserved_usd=self._available_budget_usd,
                    )
            return ScoringPass(rows=scored_rows, call_evidence=capture.evidence() or call_evidence)

        self._progress_fn({"stage": "judge_primary", "completed": 1, "total": 3})
        primary_pass = score_pass(primary_input, pass_name="primary")
        self._progress_fn({"stage": "judge_repeatability", "completed": 2, "total": 3})
        repeatability_pass = score_pass(
            repeatability_input,
            pass_name="repeatability",
        )
        all_call_evidence = (
            canary_pass.call_evidence
            + primary_pass.call_evidence
            + repeatability_pass.call_evidence
        )
        main_call_evidence = primary_pass.call_evidence + repeatability_pass.call_evidence
        usage_estimated_cost_usd = sum(
            estimate_cost_usd(
                JUDGE_DEPLOYMENT,
                int(call.get("input_tokens") or 0),
                int(call.get("output_tokens") or 0),
            )
            for call in main_call_evidence
        )
        incremental_cost_usd = max(
            usage_estimated_cost_usd,
            (len(rows) + len(repeatability_input)) * EST_JUDGE_PER_SAMPLE_USD,
        )
        campaign_judge_cost_usd = float(preflight.details["cost_usd"]) + incremental_cost_usd
        accounted_cost_usd = (
            incremental_cost_usd
            if self._preflight_accounted_externally
            else campaign_judge_cost_usd
        )
        validation = validate_judge_evidence(
            canary_scores=canary_pass.rows,
            canary_expectations=CANARY_EXPECTATIONS,
            primary_rows=primary_pass.rows,
            secondary_rows=repeatability_pass.rows,
            subset_case_ids=subset_case_ids,
            arms=self._arms,
            repeats=self._repeats,
            judge_version=dict(judge_config["version"]),
            cost_usd=campaign_judge_cost_usd,
            call_evidence=all_call_evidence,
        )
        validation = OracleValidation(
            canaries_passed=validation.canaries_passed,
            repeatability_passed=validation.repeatability_passed,
            call_evidence_passed=validation.call_evidence_passed,
            details={
                **validation.details,
                "cost_usd": accounted_cost_usd,
                "campaign_judge_cost_usd": campaign_judge_cost_usd,
                "preflight_cost_accounted_externally": self._preflight_accounted_externally,
                "usage_estimated_cost_usd": usage_estimated_cost_usd,
                "cost_basis": "max(usage_estimate, conservative_sample_reserve)",
            },
        )
        self._progress_fn({"stage": "judge_complete", "completed": 3, "total": 3})
        return primary_pass.rows, validation
