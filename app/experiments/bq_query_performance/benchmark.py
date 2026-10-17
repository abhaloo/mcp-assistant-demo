"""Fail-closed Business Query performance benchmark contract and preflight."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.auth import Principal
from app.business_query.authorize.scoping import apply_role_scope
from app.business_query.compile.adapter import InternalCompilerAdapter
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Answered
from app.business_query.plan import BusinessQueryPlan
from app.business_query.seal.evidence import declared_result_members, seal_adapter_result
from app.business_query.wire.module import UnsealedAdapterAnswer

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "evals" / "experiments" / "bq-query-performance" / "manifest.json"

ALLOWED_CAUSAL_DIFFERENCES = frozenset({"semantic_view_shape", "named_migration_index"})
_MARIADB_IDENTITY_FIELDS = (
    "compiled_query_digest",
    "environment_fingerprint",
    "index_fingerprint",
    "mariadb_version",
    "mariadb_config",
)


class BenchmarkPreflightError(ValueError):
    """Benchmark inputs drifted or are missing; comparison is invalid."""


class BenchmarkCorrectnessFailure(ValueError):
    """Result members or values drifted; performance is not comparable."""


class BenchmarkPerformanceFailure(ValueError):
    """Matched-MariaDB timing missed the frozen budget."""


class BenchmarkNotRunnable(ValueError):
    """Required matched-MariaDB evidence is absent. Not a pass."""


class _BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkArm(_BenchmarkModel):
    compiled_query_digest: str | None = None
    environment_fingerprint: str | None = None
    index_fingerprint: str | None = None
    mariadb_version: str | None = None
    mariadb_config: str | None = None


class BenchmarkInterleave(_BenchmarkModel):
    mode: str
    seed: int
    arms: list[str]


class BenchmarkProtocol(_BenchmarkModel):
    untimed_warmups: int
    measured_executions_per_shape_arm: int
    p95_estimator: str
    interleave: BenchmarkInterleave
    cache: dict[str, Any]


class BenchmarkLoad(_BenchmarkModel):
    pool_size: int
    overflow: bool
    concurrent_target_requests: int
    rounds: int
    product_timeout_seconds: float
    concurrent_p95_seconds: float


class BenchmarkBudget(_BenchmarkModel):
    id: str
    hot_shape_p95_seconds: float
    zero_product_timeouts: bool
    easy_control_p95_regression: float


class BenchmarkShape(_BenchmarkModel):
    id: str
    role: str
    plan: dict[str, Any]
    member_schema: list[dict[str, Any]]
    oracle: dict[str, Any]


class BenchmarkContract(_BenchmarkModel):
    id: str
    schema_version: int
    status: str
    budget: BenchmarkBudget
    bundle: dict[str, Any]
    business_date: str
    business_timezone: str
    principal: dict[str, Any]
    matched_dataset_identity: str | None = None
    mariadb_execute_entity_id: int | None = None
    shape_compiled_query_digests: dict[str, str] | None = None
    mariadb_result_identity: dict[str, Any] | None = None
    protocol: BenchmarkProtocol
    load: BenchmarkLoad
    allowed_causal_differences: list[str]
    declared_causal_difference: str | None = None
    arms: dict[str, BenchmarkArm]
    shapes: list[BenchmarkShape]


def _as_mapping(contract: BenchmarkContract | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(contract, BenchmarkContract):
        return contract.model_dump(mode="json")
    return dict(contract)


def load_benchmark_contract(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or MANIFEST_PATH
    if not manifest_path.is_file():
        raise BenchmarkPreflightError(f"benchmark manifest missing: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        contract = BenchmarkContract.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise BenchmarkPreflightError(f"benchmark manifest is invalid: {exc}") from exc
    mapping = contract.model_dump(mode="json")
    _validate_frozen_contract(mapping)
    return mapping


def _validate_frozen_contract(contract: dict[str, Any]) -> None:
    if contract.get("budget", {}).get("id") != "D-Q1-BUDGET":
        raise BenchmarkPreflightError("budget id drifted from D-Q1-BUDGET")
    differences = contract.get("allowed_causal_differences")
    if set(differences or []) != ALLOWED_CAUSAL_DIFFERENCES:
        raise BenchmarkPreflightError("allowed causal-difference set drifted")
    for shape in contract.get("shapes", []):
        BusinessQueryPlan.model_validate(shape["plan"])
    bundle = contract.get("bundle") or {}
    fixture = REPO_ROOT / bundle.get("fixture", "")
    if not fixture.is_file():
        raise BenchmarkPreflightError("bundle fixture missing")
    try:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BenchmarkPreflightError("bundle fixture is invalid") from exc
    if payload.get("content_hash") != bundle.get("content_hash"):
        raise BenchmarkPreflightError("bundle content hash drifted")


def _missing_mariadb_identity(contract: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if contract.get("matched_dataset_identity") is None:
        missing.append("matched_dataset_identity")
    for arm_name, arm in (contract.get("arms") or {}).items():
        for field in _MARIADB_IDENTITY_FIELDS:
            if arm.get(field) is None:
                missing.append(f"arms.{arm_name}.{field}")
    return missing


def _shape(contract: dict[str, Any], shape_id: str) -> dict[str, Any]:
    for shape in contract.get("shapes") or []:
        if shape.get("id") == shape_id:
            return shape
    raise BenchmarkPreflightError(f"unknown shape {shape_id}")


def _normalize_money(value: Any) -> str:
    text = str(value)
    if text.endswith(".0"):
        return f"{text}000"
    if "." in text:
        whole, frac = text.split(".", 1)
        return f"{whole}.{frac.ljust(4, '0')[:4]}"
    return f"{text}.0000"


def _assert_member_schema(shape: dict[str, Any], members: list[dict[str, Any]]) -> None:
    expected = [
        (item["name"], item["value_kind"], bool(item["nullable"]))
        for item in shape["member_schema"]
    ]
    observed = [(item["name"], item["value_kind"], bool(item["nullable"])) for item in members]
    if observed != expected:
        raise BenchmarkCorrectnessFailure(
            f"result member schema drifted: expected {expected}, got {observed}"
        )


def _assert_expected_rows(shape: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    oracle = shape.get("oracle") or {}
    expected_rows = oracle.get("expected_rows")
    if expected_rows is None:
        return
    if len(rows) != len(expected_rows):
        raise BenchmarkCorrectnessFailure(
            f"result row count drifted: expected {len(expected_rows)}, got {len(rows)}"
        )
    for expected, observed in zip(expected_rows, rows, strict=True):
        for key, value in expected.items():
            if _normalize_money(observed.get(key)) != _normalize_money(value):
                raise BenchmarkCorrectnessFailure(
                    f"result literal drifted for {key}: expected {value}, got {observed.get(key)}"
                )


def _assert_expected_buckets(shape: dict[str, Any], buckets: dict[str, Any]) -> None:
    expected = (shape.get("oracle") or {}).get("expected_buckets")
    if expected is None:
        return
    if set(buckets) != set(expected):
        raise BenchmarkCorrectnessFailure(
            f"aging bucket labels drifted: expected {sorted(expected)}, got {sorted(buckets)}"
        )
    for label, wanted in expected.items():
        observed = buckets[label]
        if int(observed[0]) != int(wanted[0]) or _normalize_money(observed[1]) != _normalize_money(
            wanted[1]
        ):
            raise BenchmarkCorrectnessFailure(
                f"aging bucket literal drifted for {label}: expected {wanted}, got {observed}"
            )


def preflight_benchmark(
    contract: Mapping[str, Any] | BenchmarkContract,
    observation: dict[str, Any] | None = None,
) -> None:
    """Refuse missing/stale identity before any performance comparison."""
    contract = _as_mapping(contract)
    observation = observation or {}
    kind = observation.get("kind")
    if kind not in {"performance", "correctness"}:
        raise BenchmarkPreflightError("unknown or missing observation kind")
    if kind == "performance":
        _assert_performance_preflight(contract, observation)
        return
    if not observation.get("shape_id"):
        raise BenchmarkPreflightError("correctness observation missing shape_id")
    _assert_correctness_inputs(contract, observation)


def _assert_performance_preflight(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    if observation.get("backend") == "sqlite":
        raise BenchmarkPreflightError("SQLite timings are not performance evidence")
    missing = _missing_mariadb_identity(contract)
    if missing:
        raise BenchmarkPreflightError(
            "MariaDB fingerprint and matched dataset identity are required: " + ", ".join(missing)
        )
    if observation.get("backend") != "mariadb":
        raise BenchmarkPreflightError("performance observation requires backend mariadb")
    required = (
        "untimed_warmups",
        "measured_executions_per_shape_arm",
        "p95_estimator",
        "interleave",
        "load",
        "baseline_digest",
        "candidate_digest",
        "within_arm_digests",
        "within_arm_fingerprints",
    )
    absent = [key for key in required if observation.get(key) in (None, {}, [])]
    if absent:
        raise BenchmarkPreflightError(f"performance observation missing {absent}")
    interleave = observation["interleave"]
    if (
        not isinstance(interleave, dict)
        or interleave.get("seed") is None
        or not interleave.get("mode")
    ):
        raise BenchmarkPreflightError("interleaving protocol missing seed")
    _assert_protocol_and_load(contract, observation)
    _assert_arm_identities(contract, observation)


def _assert_arm_identities(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    declared = observation.get("declared_causal_difference")
    allowed = set(contract.get("allowed_causal_differences") or [])
    if declared is not None and declared not in allowed:
        raise BenchmarkPreflightError(f"undeclared causal difference {declared}")
    baseline = contract["arms"]["baseline"]
    candidate = contract["arms"]["candidate"]
    if observation["baseline_digest"] != baseline["compiled_query_digest"]:
        raise BenchmarkPreflightError("baseline digest drifted from preregistered slot")
    if observation["candidate_digest"] != candidate["compiled_query_digest"]:
        raise BenchmarkPreflightError("candidate digest drifted from preregistered slot")
    if observation["baseline_digest"] != observation["candidate_digest"]:
        if declared != "semantic_view_shape":
            raise BenchmarkPreflightError(
                "undeclared causal difference: compiled-query digests differ"
            )
    if baseline["environment_fingerprint"] != candidate["environment_fingerprint"]:
        raise BenchmarkPreflightError("cross-arm environment fingerprint drifted")
    if baseline["mariadb_version"] != candidate["mariadb_version"]:
        raise BenchmarkPreflightError("cross-arm MariaDB version drifted")
    if baseline["mariadb_config"] != candidate["mariadb_config"]:
        raise BenchmarkPreflightError("cross-arm MariaDB config drifted")
    if baseline["index_fingerprint"] != candidate["index_fingerprint"]:
        if declared != "named_migration_index":
            raise BenchmarkPreflightError("undeclared causal difference: index fingerprint")
    _assert_within_arm_stability(contract, observation)


def _assert_within_arm_stability(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    within = observation["within_arm_digests"]
    fingerprints = observation["within_arm_fingerprints"]
    measured = int(observation["measured_executions_per_shape_arm"])
    for arm_name, arm in contract["arms"].items():
        digests = within.get(arm_name) or []
        if len(digests) != measured:
            raise BenchmarkPreflightError(
                f"within-arm digest count drifted from measured executions: {arm_name}"
            )
        unique_digests = {item for item in digests if item}
        if len(unique_digests) != 1:
            raise BenchmarkPreflightError(f"unstable within-arm compiled-query digest: {arm_name}")
        if unique_digests != {arm["compiled_query_digest"]}:
            raise BenchmarkPreflightError(
                f"within-arm digest drifted from preregistered {arm_name}"
            )
        arm_fps = fingerprints.get(arm_name) or {}
        for field in ("environment_fingerprint", "index_fingerprint"):
            values = [item for item in (arm_fps.get(field) or []) if item]
            if len(values) != measured:
                raise BenchmarkPreflightError(
                    f"within-arm {field} count drifted from measured executions: {arm_name}"
                )
            unique = set(values)
            if len(unique) != 1:
                raise BenchmarkPreflightError(f"unstable within-arm {field}: {arm_name}")
            observed = next(iter(unique))
            if observed != arm[field]:
                raise BenchmarkPreflightError(
                    f"within-arm {field} drifted from preregistered {arm_name}"
                )


def _assert_correctness_inputs(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    shape = _shape(contract, observation["shape_id"])
    if "members" not in observation:
        raise BenchmarkPreflightError("correctness observation missing members")
    oracle = shape.get("oracle") or {}
    if oracle.get("expected_rows") is not None and "rows" not in observation:
        raise BenchmarkPreflightError("correctness observation missing rows")
    if oracle.get("expected_buckets") is not None and "buckets" not in observation:
        raise BenchmarkPreflightError("correctness observation missing buckets")


def _assert_protocol_and_load(contract: dict[str, Any], observation: dict[str, Any]) -> None:
    protocol = contract["protocol"]
    load = contract["load"]
    measured = observation.get("measured_executions_per_shape_arm")
    if measured is not None and measured != protocol["measured_executions_per_shape_arm"]:
        raise BenchmarkPreflightError("measured execution count drifted")
    estimator = observation.get("p95_estimator")
    if estimator is not None and estimator != protocol["p95_estimator"]:
        raise BenchmarkPreflightError("p95 estimator drifted")
    interleave = observation.get("interleave")
    if interleave is not None and interleave != protocol["interleave"]:
        raise BenchmarkPreflightError("interleaving protocol drifted")
    observed_load = observation.get("load")
    if observed_load is not None and observed_load != load:
        raise BenchmarkPreflightError("load or pool settings drifted")
    warmups = observation.get("untimed_warmups")
    if warmups is not None and warmups != protocol["untimed_warmups"]:
        raise BenchmarkPreflightError("warmup count drifted")


def judge_benchmark(
    contract: Mapping[str, Any] | BenchmarkContract, observation: dict[str, Any]
) -> None:
    """Separate correctness failure from performance refusal."""
    contract = _as_mapping(contract)
    preflight_benchmark(contract, observation)
    if observation.get("kind") != "correctness":
        return
    shape_id = observation.get("shape_id")
    if not shape_id:
        raise BenchmarkPreflightError("correctness observation missing shape_id")
    shape = _shape(contract, shape_id)
    if "members" in observation:
        _assert_member_schema(shape, list(observation["members"]))
    if "rows" in observation:
        _assert_expected_rows(shape, list(observation["rows"]))
    if "buckets" in observation:
        _assert_expected_buckets(shape, dict(observation["buckets"]))


def judge_performance_budget(
    contract: Mapping[str, Any] | BenchmarkContract, samples: list[float]
) -> float:
    """Score nearest-rank p95 against D-Q1-BUDGET using synthetic or matched samples."""
    mapping = _as_mapping(contract)
    p95 = nearest_rank_p95(samples)
    budget = mapping["budget"]["hot_shape_p95_seconds"]
    if p95 > budget:
        raise BenchmarkPerformanceFailure(f"hot-shape p95 {p95} exceeded budget {budget} seconds")
    return p95


def nearest_rank_p95(samples: list[float]) -> float:
    """Nearest-rank p95. Independent of MariaDB; used only on matched timings."""
    if len(samples) < 30:
        raise BenchmarkPreflightError("too few measured executions for nearest-rank p95")
    ordered = sorted(samples)
    rank = math.ceil(0.95 * len(ordered))
    index = min(max(rank, 1), len(ordered)) - 1
    return ordered[index]


def _assert_frozen_execute_identity(
    principal: Principal,
    bundle: DefinitionBundle,
    contract: dict[str, Any],
    business_date: date | None,
) -> date:
    frozen = contract.get("principal") or {}
    department_id = principal.department_id
    if principal.scope_values is not None:
        department_id = principal.scope_values.department_id
    if (
        list(principal.permissions) != list(frozen.get("permissions") or [])
        or principal.entity_id != frozen.get("entity_id")
        or bool(principal.cross_entity) != bool(frozen.get("cross_entity"))
        or department_id != frozen.get("department_id")
    ):
        raise BenchmarkPreflightError("principal drifted from freeze")
    frozen_hash = (contract.get("bundle") or {}).get("content_hash")
    if bundle.content_hash != frozen_hash:
        raise BenchmarkPreflightError("bundle content hash drifted from freeze")
    frozen_date = date.fromisoformat(str(contract["business_date"]))
    if business_date is not None and business_date != frozen_date:
        raise BenchmarkPreflightError("business_date drifted from freeze")
    return frozen_date


def run_benchmark_shape(
    adapter: InternalCompilerAdapter,
    principal: Principal,
    bundle: DefinitionBundle,
    contract: Mapping[str, Any] | BenchmarkContract,
    shape_id: str,
    *,
    business_date: date | None = None,
) -> dict[str, Any]:
    """Execute one frozen plan through the production adapter."""
    contract = _as_mapping(contract)
    frozen_date = _assert_frozen_execute_identity(principal, bundle, contract, business_date)
    shape = _shape(contract, shape_id)
    plan = BusinessQueryPlan.model_validate(shape["plan"])
    scoped = apply_role_scope(plan, principal, bundle, business_date=frozen_date)
    raw = adapter.execute_with_evidence(scoped)
    if isinstance(raw, UnsealedAdapterAnswer):
        result = seal_adapter_result(raw, scoped, principal=principal, bundle=bundle)
        digest = raw.evidence.compiled_query_digest
        backend = raw.evidence.backend
    else:
        result = raw
        compiled_sql = adapter.compile_sql(scoped)
        digest = hashlib.sha256(compiled_sql.encode()).hexdigest()
        backend = adapter.dialect_name
    if not isinstance(result, Answered):
        raise BenchmarkNotRunnable(f"benchmark shape {shape_id} did not answer")
    members = ()
    if result.rows:
        members = declared_result_members(plan, bundle, list(result.rows[0]), result.rows)
    else:
        members = declared_result_members(
            plan, bundle, [item["name"] for item in shape["member_schema"]], result.rows
        )
    return {
        "shape_id": shape_id,
        "result": result,
        "members": members,
        "compiled_query_digest": digest,
        "answer_query_id": result.receipt.answer_query_id,
        "backend": backend,
    }


def write_benchmark_artifact(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Write an immutable benchmark receipt. Refuse overwrite via exclusive create."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    sealed = dict(payload)
    sealed["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(sealed, indent=2, default=str) + "\n")
    except FileExistsError as exc:
        raise BenchmarkPreflightError(
            f"benchmark artifact cannot overwrite {path.as_posix()}"
        ) from exc
    return sealed
