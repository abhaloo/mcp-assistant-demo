"""Load frozen suite files and refuse a run that cannot produce evidence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.eval.business_query.contract import (
    FrozenFamilyManifest,
    RunKind,
    validate_business_query_suite,
)
from app.eval.business_query.scorer import CaseScoringSpec, MemberMapping, ValueKind


class EvaluationReadinessError(ValueError):
    """The selected suite cannot produce release-gating evidence."""


def load_jsonl(path: Path, key: str) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        record_key = record[key]
        if record_key in records:
            raise EvaluationReadinessError(
                f"duplicate {key} {record_key!r} in {path} at line {line_number}"
            )
        records[record_key] = record
    return records


def load_scoring_specs(path: Path) -> dict[str, CaseScoringSpec]:
    records = load_jsonl(path, "case_id")
    specs: dict[str, CaseScoringSpec] = {}
    allowed_fields = {
        "case_id",
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "member_mappings",
        "row_key",
        "expected_row_count",
        "expected_total_row_count",
        "expected_truncated",
        "ordered",
        "metadata_allowlist",
    }
    for case_id, record in records.items():
        if set(record) - allowed_fields:
            raise EvaluationReadinessError(f"invalid scoring spec: {case_id}")
        if record.get("review_status") != "approved":
            raise EvaluationReadinessError(f"scoring spec is not approved: {case_id}")
        if not record.get("reviewed_by") or not record.get("reviewed_at"):
            raise EvaluationReadinessError(f"scoring spec lacks review provenance: {case_id}")
        try:
            datetime.fromisoformat(record["reviewed_at"].replace("Z", "+00:00"))
            mappings = tuple(
                MemberMapping(
                    oracle_column=mapping["oracle_column"],
                    result_member=mapping["result_member"],
                    value_kind=ValueKind(mapping["value_kind"]),
                    result_member_alternatives=tuple(mapping.get("result_member_alternatives", ())),
                )
                for mapping in record["member_mappings"]
            )
            specs[case_id] = CaseScoringSpec(
                member_mappings=mappings,
                row_key=tuple(record["row_key"]),
                expected_row_count=record["expected_row_count"],
                expected_total_row_count=record["expected_total_row_count"],
                expected_truncated=record["expected_truncated"],
                ordered=record.get("ordered", False),
                metadata_allowlist=frozenset(record.get("metadata_allowlist", [])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationReadinessError(f"invalid scoring spec: {case_id}") from exc
    return specs


def validate_eval_readiness(
    cases: dict[str, dict],
    oracle: dict[str, dict],
    scoring_specs: dict[str, CaseScoringSpec],
    *,
    require_release_suite: bool,
    run_kind: RunKind | None = None,
    manifest: FrozenFamilyManifest | None = None,
) -> None:
    kind = run_kind or ("gate" if require_release_suite else "diagnostic")
    if require_release_suite and manifest is None:
        raise EvaluationReadinessError("release suite requires a frozen family manifest")
    if manifest is not None:
        suite = validate_business_query_suite(cases, oracle, scoring_specs, manifest, run_kind=kind)
        if not suite.ok:
            raise EvaluationReadinessError("; ".join(suite.refusals))
        return
    blockers: list[str] = []
    for case_id, case in cases.items():
        if case.get("draft_expected") != "answered":
            continue
        if case_id not in oracle:
            blockers.append(f"missing independent oracle: {case_id}")
        if case_id not in scoring_specs:
            blockers.append(f"missing scoring spec: {case_id}")
    if blockers:
        raise EvaluationReadinessError("; ".join(blockers))


def select_run_cases(cases: dict[str, dict], *, only: set[str] | None) -> dict[str, dict]:
    if only is not None:
        unknown = only - set(cases)
        if unknown:
            raise SystemExit(f"unknown case ids: {sorted(unknown)}")
        excluded = [cid for cid in sorted(only) if cases[cid].get("eval_status") == "excluded"]
        if excluded:
            raise SystemExit(f"excluded case ids: {excluded}")
        cases = {k: v for k, v in cases.items() if k in only}
    return {k: v for k, v in cases.items() if v.get("eval_status") != "excluded"}
