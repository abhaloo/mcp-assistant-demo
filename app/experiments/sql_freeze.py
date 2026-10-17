"""The immutable SQL experiment contract: its vocabulary, its handle, and the freezer.

Split out of ``app/experiments/tasks/sql_task.py`` (F1) so that module is adapter code
only. This mirrors the shape RAG already has -- ``AnswerArm`` / ``FrozenRagExperiment`` /
``freeze_rag_experiment`` live in ``app/experiments/rag_experiment.py`` while
``app/experiments/tasks/rag_task.py`` holds just the adapter -- rather than inventing a
new convention. ``sql_task`` re-exports ``SqlArm``, ``FrozenSqlExperiment`` and
``freeze_sql_experiment`` for ``app/experiments/live_sql_eval.py``, which imports all
three from there.

**The metric is named, never abbreviated.** ``execution_accuracy_single_snapshot``, and
the freeze writes it into ``policy["metric"]`` so a run's own contract records what its
numbers mean. A semantically wrong query can return gold's rows by coincidence on one
frozen database instance; calling that "accuracy" over-claims. Gold shapes that ANY output
can match (empty result, one all-NULL row) are detected at freeze time, carried onto every
row by ``SqlTask.execute`` as ``degenerate_candidate`` / ``coincidental_match_suspected``,
and counted by ``app/experiments/sql_summary.py``.

``METRIC_NAME`` and ``METRIC_NOTE`` live here, at the bottom of the SQL module stack,
because the freeze, the adapter and the summary all need them and this is the only
placement that leaves the three modules acyclic.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.core.errors import RagExperimentError
from app.experiments.core.runtime_identity import RuntimeIdentity
from app.experiments.freeze_projection import merge_profile_projection_onto_arm_blob
from app.experiments.sql_eval_gate import DEFAULT_PASS_THRESHOLD

__all__ = [
    "METRIC_NAME",
    "METRIC_NOTE",
    "FrozenSqlExperiment",
    "SqlArm",
    "freeze_sql_experiment",
]

METRIC_NAME = "execution_accuracy_single_snapshot"
METRIC_NOTE = (
    "Execution match against gold on ONE frozen database snapshot. A semantically wrong "
    "query can return gold's rows by coincidence on a single instance, so this is "
    "diagnosis-grade: see degenerate_candidates / coincidental_match_cases below, and "
    "run a manual false-positive audit of passing cases before any model-comparison "
    "claim. Operational failures - rate limits, a dropped snapshot connection, an agent "
    "that crashed before issuing any SQL - are excluded from this number and reported "
    "separately as operational_failure_counts; they are never scored as misses."
)


@dataclass(frozen=True)
class SqlArm:
    """One requested SQL-agent route.

    ``chat_deployment=None`` means "let the production router decide per question"
    (``run_case`` falls through to ``select_chat_deployment``). The deployment that
    actually ran therefore varies per case and is only knowable after the call.
    """

    name: str
    chat_deployment: str | None
    reasoning_effort: str | None = None
    request_timeout_s: float | None = None
    requested_provider: str | None = None
    provider_order: tuple[str, ...] = ()
    verbosity: str | None = None
    allowed_actual_models: tuple[str, ...] = ()
    profile_id: str | None = None
    harness_output_cap_tokens: int | None = None
    attested_output_cap_tokens: int | None = None
    deployment_attestation: dict[str, object] | None = None
    est_answer_usd: float | None = None
    use_responses_api: bool = False
    api_class: str | None = None


@dataclass(frozen=True)
class FrozenSqlExperiment:
    """In-memory handle to the immutable SQL contract on disk."""

    freeze_hash: str
    output_path: Path
    document: dict[str, Any]


def _sql_arm_blob(arm: SqlArm) -> dict[str, object]:
    return {
        "name": arm.name,
        "chat_deployment": arm.chat_deployment,
        "reasoning_effort": arm.reasoning_effort,
        "request_timeout_s": arm.request_timeout_s,
        "requested_provider": arm.requested_provider,
        "provider_order": list(arm.provider_order),
        "verbosity": arm.verbosity,
        "allowed_actual_models": list(arm.allowed_actual_models),
        "profile_id": arm.profile_id,
        "harness_output_cap_tokens": arm.harness_output_cap_tokens,
        "attested_output_cap_tokens": arm.attested_output_cap_tokens,
        "deployment_attestation": arm.deployment_attestation,
        "est_answer_usd": arm.est_answer_usd,
        "use_responses_api": arm.use_responses_api,
        "api_class": arm.api_class,
    }


def _frozen_sql_arm_blobs(
    arms: tuple[SqlArm, ...],
    arm_profile_projections: dict[str, dict[str, object]] | None,
) -> list[dict[str, object]]:
    blobs: list[dict[str, object]] = []
    for arm in arms:
        blob = _sql_arm_blob(arm)
        projection = (arm_profile_projections or {}).get(arm.name)
        if projection is not None:
            blob = merge_profile_projection_onto_arm_blob(blob, projection)
        blobs.append(blob)
    return blobs


def freeze_sql_experiment(
    *,
    experiment_id: str,
    stage: str,
    cases: list[dict[str, Any]],
    gold_verification: dict[str, dict[str, Any]],
    arms: tuple[SqlArm, ...],
    baseline_arm: str,
    candidate_arm: str,
    runtime: RuntimeIdentity,
    output_path: Path,
    created_at: str,
    snapshot: dict[str, Any],
    repeats: int,
    campaign_cap_usd: float,
    spend_stages: dict[str, Any],
    answer_order_seed: int = 99173,
    max_opportunity_failure_rate: float = 0.02,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    router_policy: dict[str, Any] | None = None,
    allowed_actual_models: dict[str, tuple[str, ...]] | None = None,
    arm_profile_projections: dict[str, dict[str, object]] | None = None,
) -> FrozenSqlExperiment:
    """Freeze cases, gold-verification evidence, arms and budgets before spend."""
    case_ids = [str(case["id"]) for case in cases]
    if not cases or len(case_ids) != len(set(case_ids)):
        raise RagExperimentError("SQL cases must be non-empty with unique ids")
    arm_names = [arm.name for arm in arms]
    if not arms or len(arm_names) != len(set(arm_names)):
        raise RagExperimentError("SQL arms must be non-empty with unique names")
    for required_arm in (baseline_arm, candidate_arm):
        if required_arm not in arm_names:
            raise RagExperimentError(f"gate arm {required_arm!r} is not one of the frozen arms")
    missing_gold = sorted(set(case_ids) - set(gold_verification))
    if missing_gold:
        # Fail closed: a case with no gold-verification receipt cannot be told apart
        # from a case whose gold is degenerate, so its matches could not be trusted.
        raise RagExperimentError(f"gold verification missing for cases: {missing_gold}")
    for field_name in ("answer_reserve_usd", "answer_cell_ceiling_usd", "answer_cell_forecast_usd"):
        if field_name not in spend_stages:
            raise RagExperimentError(f"spend stages missing field: {field_name}")
    for field_name in ("answer_cell_ceiling_usd", "answer_cell_forecast_usd"):
        table = spend_stages[field_name]
        if not isinstance(table, dict) or set(table) != set(arm_names):
            raise RagExperimentError(f"{field_name} must cover exactly the frozen arms")
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in table.values()):
            raise RagExperimentError(f"{field_name} values must be finite and positive")
    if any(
        float(spend_stages["answer_cell_forecast_usd"][name])
        > float(spend_stages["answer_cell_ceiling_usd"][name])
        for name in arm_names
    ):
        raise RagExperimentError("answer cell forecasts cannot exceed hard ceilings")
    document_without_hash: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "stage": stage,
        "created_at": created_at,
        "runtime": {
            "commit_sha": runtime.commit_sha,
            "dirty": runtime.dirty,
            "environment_hash": runtime.environment_hash,
            "dependency_lock_hash": runtime.dependency_lock_hash,
        },
        "arms": _frozen_sql_arm_blobs(arms, arm_profile_projections),
        "policy": {
            "expected_case_ids": case_ids,
            "arms": [arm.name for arm in arms],
            "baseline_arm": baseline_arm,
            "candidate_arm": candidate_arm,
            "repeats": int(repeats),
            "answer_order_seed": int(answer_order_seed),
            "metric": METRIC_NAME,
            "max_opportunity_failure_rate": float(max_opportunity_failure_rate),
            "pass_threshold": float(pass_threshold),
            "router_policy": router_policy,
            "allowed_actual_models": {
                arm.name: list(
                    allowed_actual_models.get(arm.name, arm.allowed_actual_models)
                    if allowed_actual_models is not None
                    else arm.allowed_actual_models
                )
                for arm in arms
            },
        },
        "snapshot": snapshot,
        "gold_verification": gold_verification,
        "campaign_cap_usd": float(campaign_cap_usd),
        "spend_stages": spend_stages,
        "cases": cases,
    }
    payload = json.dumps(
        document_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    freeze_hash = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    document = {**document_without_hash, "freeze_hash": freeze_hash}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("x", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise RagExperimentError(f"frozen SQL experiment already exists: {output_path}") from exc

    return FrozenSqlExperiment(freeze_hash=freeze_hash, output_path=output_path, document=document)
