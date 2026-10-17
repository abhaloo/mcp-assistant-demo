"""Blinded, append-only human calibration gate for automated RAG judge scores."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Any

MANUAL_AUDIT_CASE_IDS: tuple[str, ...] = (
    "admin-01",
    "all-02",
    "all-03",
    "all-pii-01",
    "all-pii-06",
    "finance-04",
    "graphic-design-02",
    "messy-multipart-01",
    "printing-03",
    "sales-07",
    "sales-pii-01",
    "warehouse-02",
)
AUDIT_ARMS = ("prod-mini", "forced-deepseek")
AUDIT_REPEAT = 0
AUDIT_MAX_MEAN_ABS_DELTA = 0.25
MAX_LARGE_DELTA_RATE = 0.25
LARGE_DELTA = 0.40
MAX_ARM_BIAS_DELTA = 0.15
SENTINEL_CASE_IDS = ("all-02",)


class ManualRagAuditError(RuntimeError):
    """Manual audit evidence is incomplete, stale, or outside the frozen rubric."""


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json_exclusive(path: Path, document: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _finite_score(value: Any, opaque_id: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ManualRagAuditError(f"invalid manual calibration score: {opaque_id}") from exc
    if not math.isfinite(number):
        raise ManualRagAuditError(f"invalid manual calibration score: {opaque_id}")
    return number


def generate_manual_audit_bundle(
    *,
    scores_path: Path,
    output_dir: Path,
    freeze_hash: str,
) -> None:
    """Freeze a 12-stratum, two-arm, repeat-zero audit without revealing arm labels."""
    rows = _load_jsonl(scores_path)
    by_cell = {
        (str(row.get("case_id")), str(row.get("arm")), int(row.get("repeat", -1))): row
        for row in rows
    }
    expected_cells = {
        (case_id, arm, AUDIT_REPEAT) for case_id in MANUAL_AUDIT_CASE_IDS for arm in AUDIT_ARMS
    }
    missing = sorted(expected_cells - by_cell.keys())
    if missing:
        raise ManualRagAuditError(f"scores lack manual-audit cells: {missing!r}")

    input_rows: list[dict[str, Any]] = []
    mapping: dict[str, dict[str, Any]] = {}
    for case_id, arm, repeat in sorted(expected_cells):
        row = by_cell[(case_id, arm, repeat)]
        opaque_id = secrets.token_hex(10)
        input_rows.append(
            {
                "opaque_id": opaque_id,
                "case_id": case_id,
                "repeat": repeat,
                "question": row["question"],
                "expected_answer": row["expected_answer"],
                "answer": row["answer"],
                "retrieved_contexts": row["retrieved_contexts"],
                "sources": row.get("sources") or [],
                "rubric": {
                    "manual_factual_correctness": "0, 0.5, or 1 against the literal oracle",
                    "manual_faithfulness": "0, 0.5, or 1 against only the frozen contexts",
                },
            }
        )
        mapping[opaque_id] = {
            "case_id": case_id,
            "arm": arm,
            "repeat": repeat,
            "factual_correctness": row["factual_correctness"],
            "faithfulness": row["faithfulness"],
        }

    input_rows.sort(key=lambda row: row["opaque_id"])

    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "manual-audit-input.jsonl"
    # The unblinding key (arm labels + automated scores) lives away from the
    # file the human labels from; only its hash sits beside the inputs.
    sealed_dir = output_dir / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    map_path = sealed_dir / "manual-audit-map.json"
    _write_jsonl_exclusive(input_path, input_rows)
    _write_json_exclusive(map_path, mapping)
    _write_json_exclusive(
        output_dir / "manual-audit-policy.json",
        {
            "schema_version": 1,
            "status": "PENDING",
            "freeze_hash": freeze_hash,
            "source_scores_path": str(scores_path.relative_to(output_dir)).replace("\\", "/"),
            "source_scores_sha256": _sha256(scores_path),
            "input_sha256": _sha256(input_path),
            "map_sha256": _sha256(map_path),
            "case_ids": list(MANUAL_AUDIT_CASE_IDS),
            "arms": list(AUDIT_ARMS),
            "repeat": AUDIT_REPEAT,
            "expected_rows": len(expected_cells),
            "thresholds": {
                "max_mean_abs_delta": AUDIT_MAX_MEAN_ABS_DELTA,
                "large_delta": LARGE_DELTA,
                "max_large_delta_rate": MAX_LARGE_DELTA_RATE,
                "max_arm_bias_delta": MAX_ARM_BIAS_DELTA,
                "sentinel_max_abs_delta": LARGE_DELTA,
            },
            "sentinel_case_ids": list(SENTINEL_CASE_IDS),
        },
    )


def finalize_manual_audit(
    *,
    bundle_dir: Path,
    audit_path: Path,
    automated_status: str,
) -> dict[str, Any]:
    """Validate human labels against hidden arm mappings and append a final verdict."""
    policy = json.loads((bundle_dir / "manual-audit-policy.json").read_text("utf-8"))
    input_path = bundle_dir / "manual-audit-input.jsonl"
    map_path = bundle_dir / "sealed" / "manual-audit-map.json"
    source_scores_path = bundle_dir / policy["source_scores_path"]
    if _sha256(source_scores_path) != policy["source_scores_sha256"]:
        raise ManualRagAuditError("source scores changed after manual audit freeze")
    if _sha256(input_path) != policy["input_sha256"] or _sha256(map_path) != policy["map_sha256"]:
        raise ManualRagAuditError("manual audit bundle hash mismatch")
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    audit_rows = _load_jsonl(audit_path)
    by_id = {str(row.get("opaque_id")): row for row in audit_rows}
    if len(by_id) != len(audit_rows) or set(by_id) != set(mapping):
        raise ManualRagAuditError("manual audit must contain each opaque ID exactly once")

    metrics: dict[str, list[float]] = defaultdict(list)
    signed_by_arm: dict[tuple[str, str], list[float]] = defaultdict(list)
    sentinel_failures: list[dict[str, Any]] = []
    allowed = {0.0, 0.5, 1.0}
    for opaque_id, human in by_id.items():
        automated = mapping[opaque_id]
        if not str(human.get("notes") or "").strip():
            raise ManualRagAuditError(f"manual audit note is missing: {opaque_id}")
        for metric in ("factual_correctness", "faithfulness"):
            manual_value = _finite_score(human.get(f"manual_{metric}"), opaque_id)
            automated_value = _finite_score(automated.get(metric), opaque_id)
            if manual_value not in allowed:
                raise ManualRagAuditError(f"invalid manual calibration score: {opaque_id}")
            delta = automated_value - manual_value
            metrics[metric].append(abs(delta))
            signed_by_arm[(metric, automated["arm"])].append(delta)
            if automated["case_id"] in SENTINEL_CASE_IDS and abs(delta) > LARGE_DELTA:
                sentinel_failures.append(
                    {
                        "case_id": automated["case_id"],
                        "arm": automated["arm"],
                        "metric": metric,
                        "absolute_delta": abs(delta),
                    }
                )

    calibration: dict[str, Any] = {}
    passed = not sentinel_failures
    for metric, deltas in metrics.items():
        mean_abs_delta = sum(deltas) / len(deltas)
        large_delta_rate = sum(delta > LARGE_DELTA for delta in deltas) / len(deltas)
        base_errors = signed_by_arm[(metric, AUDIT_ARMS[0])]
        candidate_errors = signed_by_arm[(metric, AUDIT_ARMS[1])]
        arm_bias_delta = abs(
            sum(base_errors) / len(base_errors) - sum(candidate_errors) / len(candidate_errors)
        )
        metric_passed = (
            mean_abs_delta <= AUDIT_MAX_MEAN_ABS_DELTA
            and large_delta_rate <= MAX_LARGE_DELTA_RATE
            and arm_bias_delta <= MAX_ARM_BIAS_DELTA
        )
        passed = passed and metric_passed
        calibration[metric] = {
            "mean_abs_delta": round(mean_abs_delta, 6),
            "large_delta_rate": round(large_delta_rate, 6),
            "arm_bias_delta": round(arm_bias_delta, 6),
            "passed": metric_passed,
        }

    copied_audit_path = bundle_dir / "manual-audit.jsonl"
    _write_jsonl_exclusive(copied_audit_path, audit_rows)
    status = automated_status
    if automated_status == "RAG-CANDIDATE-DEV" and not passed:
        status = "HOLD"
    verdict = {
        "schema_version": 1,
        "status": status,
        "automated_status": automated_status,
        "manual_calibration_passed": passed,
        "calibration": calibration,
        "sentinel_failures": sentinel_failures,
        "audit_sha256": _sha256(copied_audit_path),
        "policy_sha256": _sha256(bundle_dir / "manual-audit-policy.json"),
    }
    _write_json_exclusive(bundle_dir / "manual-audit-verdict.json", verdict)
    return verdict
