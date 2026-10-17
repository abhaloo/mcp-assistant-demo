"""Q1 5+30 candidate protocol and D-Q1-BUDGET verdict."""

from __future__ import annotations

import hashlib
import json
import os
import random
import statistics
import time
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from app.auth import Principal
from app.auth.record_access import ScopeValues
from app.business_query.authorize.scoping import apply_role_scope
from app.business_query.compile.adapter import InternalCompilerAdapter
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Incomplete
from app.business_query.plan import BusinessQueryPlan
from app.business_query.seal.evidence import seal_adapter_result
from app.business_query.wire.module import UnsealedAdapterAnswer
from app.experiments.bq_query_performance.benchmark import (
    REPO_ROOT,
    BenchmarkCorrectnessFailure,
    BenchmarkNotRunnable,
    BenchmarkPreflightError,
    _as_mapping,
    _assert_frozen_execute_identity,
    load_benchmark_contract,
    nearest_rank_p95,
    write_benchmark_artifact,
)
from app.experiments.bq_query_performance.mariadb_baseline import (
    _explain_plan,
    _overlay_mariadb_principal,
    _rows_examined,
    _write_once_path,
    read_matched_dataset_identity,
)

TASK2_EASY_CONTROL_SECONDS = 0.011
TASK2_BASELINE_SOURCE = "task2"
SET_BASED_VIEW_SHA256 = "6eb8dc649721c226a376c0d6e5df5bbd5aa21424e4066de9963cee5d753cafda"
CORRELATED_VIEW_SHA256 = "7150e1dc1ddc2210de70c8c490f5afeaadab158218009ccceca1779f3cdb924d"
DEFAULT_VERDICT_DIR = REPO_ROOT / "data" / "bq-query-performance" / "candidate-5-30"
_PRODUCT_TIMEOUT_SECONDS = 10.0
_MARIADB_ENTITY_ID = 1
_HOT_ROLE = "hot"
_EASY_ROLE = "easy_control"


def interleaved_arm_slots(protocol: Mapping[str, Any]) -> list[str]:
    """Fixed-seeded baseline/candidate order. Baseline slots are not live-executed."""
    interleave = protocol["interleave"]
    measured = int(protocol["measured_executions_per_shape_arm"])
    arms = list(interleave["arms"])
    slots = [arm for arm in arms for _ in range(measured)]
    rng = random.Random(int(interleave["seed"]))
    rng.shuffle(slots)
    return slots


def judge_q1_verdict(contract: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
    """Score a warm candidate 5+30 observation. Does not retune D-Q1-BUDGET."""
    mapping = _as_mapping(contract)
    observed = dict(observation)
    _assert_protocol_observation(mapping, observed)
    budget = mapping["budget"]
    hot_limit = float(budget["hot_shape_p95_seconds"])
    easy_limit = TASK2_EASY_CONTROL_SECONDS * (1.0 + float(budget["easy_control_p95_regression"]))
    shapes_out: dict[str, Any] = {}
    failures: list[str] = []
    for shape in mapping["shapes"]:
        shape_id = shape["id"]
        payload = (observed.get("shapes") or {}).get(shape_id) or {}
        scored = _score_shape(shape, payload, hot_limit=hot_limit, easy_limit=easy_limit)
        shapes_out[shape_id] = scored
        if scored["failure"]:
            failures.append(scored["failure"])
    verdict = "FAIL" if failures else "MET"
    return {
        "budget_verdict": verdict,
        "reason": "; ".join(failures) if failures else "D-Q1-BUDGET met",
        "cache": "warm",
        "baseline_source": TASK2_BASELINE_SOURCE,
        "baseline_live_5_plus_30": False,
        "shapes": shapes_out,
    }


def _assert_protocol_observation(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    protocol = contract["protocol"]
    load = contract["load"]
    if observation.get("kind") != "performance":
        raise BenchmarkPreflightError("unknown or missing observation kind")
    if observation.get("backend") != "mariadb":
        raise BenchmarkPreflightError("SQLite timings are not performance evidence")
    if observation.get("cache") != "warm":
        raise BenchmarkPreflightError("warm-cache gate required; cold-start is diagnostic only")
    if observation.get("untimed_warmups") != protocol["untimed_warmups"]:
        raise BenchmarkPreflightError("warmup count drifted")
    if (
        observation.get("measured_executions_per_shape_arm")
        != protocol["measured_executions_per_shape_arm"]
    ):
        raise BenchmarkPreflightError("measured execution count drifted")
    if observation.get("p95_estimator") != protocol["p95_estimator"]:
        raise BenchmarkPreflightError("p95 estimator drifted")
    if observation.get("interleave") != protocol["interleave"]:
        raise BenchmarkPreflightError("interleaving protocol drifted")
    if observation.get("load") != load:
        raise BenchmarkPreflightError("load or pool settings drifted")
    if observation.get("baseline_live_5_plus_30") is not False:
        raise BenchmarkPreflightError(
            "R8 forbids a live correlated-view 5+30 baseline; use Task 2 evidence"
        )
    if observation.get("baseline_source") != TASK2_BASELINE_SOURCE:
        raise BenchmarkPreflightError("baseline arm must be labeled Task 2 evidence")
    identity = observation.get("matched_dataset_identity")
    frozen_identity = contract.get("matched_dataset_identity")
    if identity != frozen_identity:
        raise BenchmarkPreflightError("matched MariaDB identity drifted")
    if identity and f"view_sha256={CORRELATED_VIEW_SHA256}" in str(identity):
        raise BenchmarkPreflightError(
            "R8 forbids re-applying the correlated bill-fact view for 5+30"
        )
    if identity and f"view_sha256={SET_BASED_VIEW_SHA256}" not in str(identity):
        raise BenchmarkPreflightError(
            "candidate 5+30 requires the current set-based bill-fact view"
        )


def _score_shape(
    shape: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    hot_limit: float,
    easy_limit: float,
) -> dict[str, Any]:
    role = shape["role"]
    if payload.get("role") != role:
        raise BenchmarkPreflightError(f"shape role drifted: {shape['id']}")
    candidate = dict(payload.get("candidate") or {})
    baseline = dict(payload.get("baseline") or {})
    durations = list(candidate.get("durations") or [])
    timeouts = int(candidate.get("timeouts") or 0)
    p95 = nearest_rank_p95(durations)
    median = round(float(statistics.median(durations)), 4)
    failure = None
    if timeouts:
        failure = f"{shape['id']} had {timeouts} product timeouts; D-Q1-BUDGET requires zero"
    elif role == _HOT_ROLE and p95 > hot_limit:
        failure = f"{shape['id']} p95 {p95} exceeded budget {hot_limit} seconds"
    elif role == _EASY_ROLE and p95 > easy_limit:
        failure = (
            f"{shape['id']} p95 {p95} exceeded Task 2 {TASK2_EASY_CONTROL_SECONDS}s "
            f"by more than 20% (limit {round(easy_limit, 4)})"
        )
    _assert_task2_baseline(shape["id"], role, baseline)
    return {
        "role": role,
        "candidate": {
            "arm": "candidate",
            "median_seconds": median,
            "p95_seconds": p95,
            "timeouts": timeouts,
            "samples": len(durations),
            "explain_select_types": candidate.get("explain_select_types"),
            "rows_examined": candidate.get("rows_examined"),
            "member_schema": candidate.get("member_schema"),
            "result_identity": candidate.get("result_identity"),
            "compiled_query_digest": candidate.get("compiled_query_digest"),
            "correctness": candidate.get("correctness"),
        },
        "baseline": baseline,
        "failure": failure,
    }


def _assert_task2_baseline(shape_id: str, role: str, baseline: Mapping[str, Any]) -> None:
    if baseline.get("arm") != "baseline":
        raise BenchmarkPreflightError(f"{shape_id} baseline arm unlabeled")
    if role == _HOT_ROLE:
        if baseline.get("source") != "task2_product_timeout":
            raise BenchmarkPreflightError(f"{shape_id} baseline must be Task 2 product timeout")
        if baseline.get("product_timeout") is not True:
            raise BenchmarkPreflightError(f"{shape_id} Task 2 baseline was a product timeout")
        return
    if baseline.get("source") != "task2_easy_control":
        raise BenchmarkPreflightError(f"{shape_id} baseline must be Task 2 easy-control duration")
    if float(baseline.get("duration_seconds")) != TASK2_EASY_CONTROL_SECONDS:
        raise BenchmarkPreflightError(
            f"{shape_id} Task 2 easy-control duration drifted from {TASK2_EASY_CONTROL_SECONDS}"
        )


def task2_baseline_arm(role: str) -> dict[str, Any]:
    """Historical product-timeout / 0.011s evidence. Not a live 5+30 baseline."""
    if role == _HOT_ROLE:
        return {
            "arm": "baseline",
            "source": "task2_product_timeout",
            "product_timeout": True,
            "duration_seconds": None,
        }
    return {
        "arm": "baseline",
        "source": "task2_easy_control",
        "product_timeout": False,
        "duration_seconds": TASK2_EASY_CONTROL_SECONDS,
    }


def run_candidate_5_plus_30(
    *,
    artifact_dir: Path | None = None,
    adapter: InternalCompilerAdapter | None = None,
    principal: Principal | None = None,
    bundle: DefinitionBundle | None = None,
    contract: Mapping[str, Any] | None = None,
    business_date: date | None = None,
) -> dict[str, Any]:
    """Warm 5+30 on the current set-based view. Baseline arm is benchmark baseline, not live."""
    owned_engine: sa.Engine | None = None
    mapping = _as_mapping(contract or load_benchmark_contract())
    if adapter is None:
        url = os.environ.get("MCP_RECORD_DATABASE_URL")
        if not url:
            raise BenchmarkNotRunnable(
                "matched MariaDB URL is required; SQLite timings are not performance evidence"
            )
        owned_engine = sa.create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=0)
        try:
            with owned_engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except Exception:
            owned_engine.dispose()
            raise BenchmarkNotRunnable("matched MariaDB is unreachable") from None
        fixture = REPO_ROOT / mapping["bundle"]["fixture"]
        bundle = DefinitionBundle.model_validate_json(fixture.read_text(encoding="utf-8"))
        frozen = mapping["principal"]
        principal = Principal(
            user_id="q1-benchmark",
            role="sales",
            permissions=list(frozen["permissions"]),
            entity_id=_MARIADB_ENTITY_ID,
            cross_entity=bool(frozen.get("cross_entity")),
            department_id=frozen.get("department_id"),
            scope_values=ScopeValues(department_id=frozen.get("department_id")),
        )
        adapter = InternalCompilerAdapter(
            principal=principal,
            engine=owned_engine,
            bundle=bundle,
            statement_timeout_seconds=_PRODUCT_TIMEOUT_SECONDS,
            database_identity="pending-matched-mariadb",
        )
    assert adapter is not None and principal is not None and bundle is not None
    try:
        return _run_candidate_protocol(
            adapter,
            principal,
            bundle,
            mapping,
            business_date=business_date,
            artifact_dir=artifact_dir,
        )
    finally:
        if owned_engine is not None:
            owned_engine.dispose()


def _run_candidate_protocol(
    adapter: InternalCompilerAdapter,
    principal: Principal,
    bundle: DefinitionBundle,
    contract: dict[str, Any],
    *,
    business_date: date | None,
    artifact_dir: Path | None,
) -> dict[str, Any]:
    if adapter.dialect_name not in {"mysql", "mariadb"}:
        raise BenchmarkPreflightError("SQLite timings are not performance evidence")
    if adapter._statement_timeout_seconds != _PRODUCT_TIMEOUT_SECONDS:
        raise BenchmarkPreflightError("candidate 5+30 must use the product 10s statement cap")
    engine = adapter._engine
    identity = read_matched_dataset_identity(engine)
    if identity["view_sha256"] == CORRELATED_VIEW_SHA256:
        raise BenchmarkPreflightError(
            "R8 forbids re-applying the correlated bill-fact view for 5+30"
        )
    if identity["view_sha256"] != SET_BASED_VIEW_SHA256:
        raise BenchmarkPreflightError(
            "candidate 5+30 requires the current set-based bill-fact view"
        )
    adapter._database_identity = identity["matched_dataset_identity"]
    frozen_arm = (contract.get("arms") or {}).get("baseline") or {}
    for field in (
        "environment_fingerprint",
        "index_fingerprint",
        "mariadb_version",
        "mariadb_config",
    ):
        if frozen_arm.get(field) is not None and identity.get(field) != frozen_arm.get(field):
            raise BenchmarkPreflightError(f"matched MariaDB freeze drifted: {field}")
    execute_contract = _overlay_mariadb_principal(contract, principal)
    frozen_date = _assert_frozen_execute_identity(
        principal, bundle, execute_contract, business_date
    )
    protocol = execute_contract["protocol"]
    warmups = int(protocol["untimed_warmups"])
    slots = interleaved_arm_slots(protocol)
    observation_shapes: dict[str, Any] = {}
    shape_artifacts: dict[str, Any] = {}
    for shape in execute_contract["shapes"]:
        captured = _run_shape_candidate(
            adapter,
            principal,
            bundle,
            execute_contract,
            shape,
            frozen_date=frozen_date,
            warmups=warmups,
            slots=slots,
            identity=identity,
        )
        observation_shapes[shape["id"]] = captured["observation"]
        shape_artifacts[shape["id"]] = captured["artifact"]
    observation = {
        "kind": "performance",
        "backend": "mariadb",
        "cache": "warm",
        "untimed_warmups": protocol["untimed_warmups"],
        "measured_executions_per_shape_arm": protocol["measured_executions_per_shape_arm"],
        "p95_estimator": protocol["p95_estimator"],
        "interleave": protocol["interleave"],
        "load": execute_contract["load"],
        "declared_causal_difference": execute_contract.get("declared_causal_difference"),
        "baseline_source": TASK2_BASELINE_SOURCE,
        "baseline_live_5_plus_30": False,
        "matched_dataset_identity": identity["matched_dataset_identity"],
        "shapes": observation_shapes,
    }
    verdict = judge_q1_verdict(execute_contract, observation)
    payload = {
        **verdict,
        "matched_dataset_identity": identity["matched_dataset_identity"],
        "mariadb_version": identity["mariadb_version"],
        "environment_fingerprint": identity["environment_fingerprint"],
        "index_fingerprint": identity["index_fingerprint"],
        "declared_causal_difference": execute_contract.get("declared_causal_difference"),
        "budget": execute_contract["budget"],
        "protocol": protocol,
        "load": execute_contract["load"],
        "interleave_slots": slots,
        "shape_artifacts": {
            shape_id: {
                "explain_select_types": item["explain_select_types"],
                "rows_examined": item["rows_examined"],
                "compiled_query_digest": item["compiled_query_digest"],
                "result_identity": item["result_identity"],
                "member_schema": item["member_schema"],
                "answer_query_ids": item["answer_query_ids"],
            }
            for shape_id, item in shape_artifacts.items()
        },
    }
    if artifact_dir is not None:
        directory = artifact_dir
        write_benchmark_artifact(_write_once_path(directory, "verdict"), payload)
        for shape_id, item in shape_artifacts.items():
            write_benchmark_artifact(_write_once_path(directory, shape_id), item)
    return payload


def _run_shape_candidate(
    adapter: InternalCompilerAdapter,
    principal: Principal,
    bundle: DefinitionBundle,
    contract: dict[str, Any],
    shape: Mapping[str, Any],
    *,
    frozen_date: date,
    warmups: int,
    slots: list[str],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    shape_id = str(shape["id"])
    role = str(shape["role"])
    plan = BusinessQueryPlan.model_validate(shape["plan"])
    scoped = apply_role_scope(plan, principal, bundle, business_date=frozen_date)
    compiled_sql = adapter.compile_sql(scoped)
    digest = hashlib.sha256(compiled_sql.encode()).hexdigest()
    frozen_digest = (contract.get("shape_compiled_query_digests") or {}).get(shape_id)
    if frozen_digest is not None and digest != frozen_digest:
        raise BenchmarkPreflightError(f"compiled-query digest drifted: {shape_id}")
    explain_json, select_types = _explain_plan(adapter._engine, adapter, scoped)
    frozen_result = (contract.get("mariadb_result_identity") or {}).get(shape_id) or {}
    answer_query_ids: list[str] = []
    last_members = None
    last_identity = None
    for _ in range(warmups):
        sample = _timed_execute(adapter, scoped, principal, bundle)
        _assert_candidate_sample(shape_id, sample, frozen_result)
        if sample["answer_query_id"]:
            answer_query_ids.append(sample["answer_query_id"])
        last_members = sample["member_schema"]
        last_identity = sample["result_identity"]
    durations: list[float] = []
    timeouts = 0
    for arm in slots:
        if arm != "candidate":
            continue
        sample = _timed_execute(adapter, scoped, principal, bundle)
        _assert_candidate_sample(shape_id, sample, frozen_result)
        durations.append(sample["duration_seconds"])
        if sample["timeout"]:
            timeouts += 1
        if sample["answer_query_id"]:
            answer_query_ids.append(sample["answer_query_id"])
        last_members = sample["member_schema"]
        last_identity = sample["result_identity"]
    observation = {
        "role": role,
        "candidate": {
            "arm": "candidate",
            "durations": durations,
            "timeouts": timeouts,
            "untimed_warmups": warmups,
            "compiled_query_digest": digest,
            "member_schema": last_members,
            "result_identity": last_identity,
            "explain_select_types": select_types,
            "rows_examined": _rows_examined(explain_json),
            "correctness": "PASS",
        },
        "baseline": task2_baseline_arm(role),
    }
    artifact = {
        "shape_id": shape_id,
        "arm": "candidate",
        "backend": "mariadb",
        "cache": "warm",
        "matched_dataset_identity": identity["matched_dataset_identity"],
        "compiled_query_digest": digest,
        "member_schema": last_members,
        "result_identity": last_identity,
        "explain_select_types": select_types,
        "rows_examined": _rows_examined(explain_json),
        "explain_json": explain_json,
        "durations": durations,
        "timeouts": timeouts,
        "untimed_warmups": warmups,
        "answer_query_ids": answer_query_ids,
        "baseline": task2_baseline_arm(role),
    }
    return {"observation": observation, "artifact": artifact}


def _timed_execute(
    adapter: InternalCompilerAdapter,
    scoped: Any,
    principal: Principal,
    bundle: DefinitionBundle,
) -> dict[str, Any]:
    started = time.perf_counter()
    raw = adapter.execute_with_evidence(scoped)
    duration = round(time.perf_counter() - started, 4)
    timed_out = isinstance(raw, Incomplete) and raw.reason_code == "timeout"
    member_schema = None
    result_identity = None
    answer_query_id = None
    if isinstance(raw, UnsealedAdapterAnswer):
        sealed = seal_adapter_result(raw, scoped, principal=principal, bundle=bundle)
        answer_query_id = sealed.receipt.answer_query_id
        member_schema = [
            {"name": item.name, "value_kind": item.value_kind, "nullable": item.nullable}
            for item in raw.evidence.result_members
        ]
        result_identity = hashlib.sha256(
            json.dumps(
                list(sealed.rows), sort_keys=True, default=str, separators=(",", ":")
            ).encode()
        ).hexdigest()
    elif not timed_out:
        raise BenchmarkNotRunnable("candidate 5+30 shape did not answer")
    return {
        "duration_seconds": duration,
        "timeout": timed_out,
        "member_schema": member_schema,
        "result_identity": result_identity,
        "answer_query_id": answer_query_id,
    }


def _assert_candidate_sample(
    shape_id: str,
    sample: Mapping[str, Any],
    frozen_result: Mapping[str, Any],
) -> None:
    if sample["timeout"]:
        return
    expected_members = frozen_result.get("member_schema")
    if expected_members is not None and sample["member_schema"] != expected_members:
        raise BenchmarkCorrectnessFailure(f"{shape_id} member schema drifted from freeze")
    expected_hash = frozen_result.get("row_sha256")
    if expected_hash is not None and sample["result_identity"] != expected_hash:
        raise BenchmarkCorrectnessFailure(f"{shape_id} result identity drifted from freeze")
