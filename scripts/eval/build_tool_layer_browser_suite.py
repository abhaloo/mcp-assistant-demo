"""Build an immutable tool-layer BrowserOS freeze directory (task E1)."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ALLOWED_ACCEPTANCE_IDS = {f"AC{i:02d}" for i in range(1, 16)}
ALLOWED_LAYERS = frozenset({"browseros_live_pair", "paired_deploy"})
REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_case_oracles(
    cases: Sequence[Mapping[str, object]],
    oracles: Mapping[str, object],
) -> None:
    seen: set[str] = set()
    for raw in cases:
        case_id = str(raw.get("id") or "")
        if not case_id:
            raise ValueError("case missing id")
        if case_id in seen:
            raise ValueError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        acceptance = raw.get("acceptance_ids") or []
        if not isinstance(acceptance, list):
            raise ValueError(f"{case_id}: acceptance_ids must be a list")
        for item in acceptance:
            token = str(item)
            if token not in ALLOWED_ACCEPTANCE_IDS:
                raise ValueError(f"unknown acceptance id: {token}")
        oracle_id = str(raw.get("oracle_id") or "")
        required = bool(raw.get("required"))
        if required and oracle_id not in oracles:
            raise ValueError(f"missing oracle: {case_id}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_sha256(source_path: str, source_id: str) -> str:
    path = (REPO_ROOT / source_path).resolve()
    if not path.is_file():
        raise ValueError(f"stale source path: {source_path}")
    payload = path.read_bytes()
    digest = _sha256_bytes(payload)
    if source_path.endswith(".json"):
        parsed = json.loads(payload.decode("utf-8"))
        if isinstance(parsed, list):
            ids = [str(item.get("id")) for item in parsed if isinstance(item, dict)]
            if source_id not in ids and source_id != path.stem:
                # Case-map rows that define new tl-* ids hash the whole file.
                if not source_id.startswith("tl-"):
                    raise ValueError(f"stale source hash: {source_id} missing in {source_path}")
        elif isinstance(parsed, dict) and "cases" in parsed:
            ids = [str(item.get("id")) for item in parsed["cases"] if isinstance(item, dict)]
            if source_id not in ids and not source_id.startswith("tl-"):
                raise ValueError(f"stale source hash: {source_id} missing in {source_path}")
    return digest


def _frozen_record(raw: Mapping[str, object]) -> dict[str, object]:
    source_path = str(raw["source_path"])
    source_id = str(raw["source_id"])
    layer = str(raw["evidence_layer"])
    if layer not in ALLOWED_LAYERS:
        raise ValueError(f"{raw.get('id')}: unknown evidence_layer {layer}")
    record: dict[str, object] = {
        "id": str(raw["id"]),
        "source_path": source_path,
        "source_id": source_id,
        "source_sha256": _source_sha256(source_path, source_id),
        "persona_id": str(raw["persona_id"]),
        "question_steps": list(raw.get("question_steps") or []),
        "prerequisites": list(raw.get("prerequisites") or []),
        "expected_route": str(raw.get("expected_route") or ""),
        "expected_outcome": str(raw.get("expected_outcome") or ""),
        "expected_evidence": list(raw.get("expected_evidence") or []),
        "oracle_id": str(raw["oracle_id"]),
        "acceptance_ids": list(raw.get("acceptance_ids") or []),
        "required": bool(raw.get("required", True)),
        "evidence_layer": layer,
    }
    if raw.get("fault_injection"):
        record["fault_injection"] = True
    return record


def _executor_case(record: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": record["id"],
        "persona_id": record["persona_id"],
        "question_steps": record["question_steps"],
        "prerequisites": record["prerequisites"],
        "expected_route": record["expected_route"],
        "expected_evidence": record["expected_evidence"],
        "oracle_id": record["oracle_id"],
        "acceptance_ids": record["acceptance_ids"],
        "required": record["required"],
        "evidence_layer": record["evidence_layer"],
        "capture": ["before", "during", "after"],
        **({"fault_injection": True} if record.get("fault_injection") else {}),
    }


def build_tool_layer_browser_suite(
    *,
    case_map: Path,
    env_card: Path,
    oracle: Path,
    output_dir: Path,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"refuse overwrite: {output_dir}")
    cases = _load_json(case_map)
    if not isinstance(cases, list):
        raise ValueError("case map must be a JSON array")
    oracles = _load_json(oracle)
    if not isinstance(oracles, dict):
        raise ValueError("oracle file must be a JSON object")
    validate_case_oracles(cases, oracles)
    env = _load_json(env_card)
    frozen = [_frozen_record(item) for item in cases]
    output_dir.mkdir(parents=True)
    frozen_path = output_dir / "frozen-suite.json"
    plan_path = output_dir / "executor-run-plan.json"
    oracle_out = output_dir / "oracle-results.jsonl"
    freeze_path = output_dir / "freeze.json"
    frozen_path.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    plan = {
        "canary_turn_cap": 12,
        "full_run_model_call_cap": 60,
        "max_attempts_per_case": 2,
        "stop_after_consecutive_infra_failures": 3,
        "cases": [_executor_case(item) for item in frozen],
    }
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    with oracle_out.open("w", encoding="utf-8") as handle:
        for oracle_id, body in oracles.items():
            handle.write(json.dumps({"oracle_id": oracle_id, "oracle": body}) + "\n")
    freeze = {
        "env_card": env,
        "case_map_sha256": _sha256_bytes(case_map.read_bytes()),
        "oracle_sha256": _sha256_bytes(oracle.read_bytes()),
        "frozen_suite_sha256": _sha256_bytes(frozen_path.read_bytes()),
        "executor_plan_sha256": _sha256_bytes(plan_path.read_bytes()),
        "canary_turn_cap": 12,
        "full_run_model_call_cap": 60,
    }
    freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-map", type=Path, required=True)
    parser.add_argument("--env-card", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build_tool_layer_browser_suite(
        case_map=args.case_map,
        env_card=args.env_card,
        oracle=args.oracle,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
