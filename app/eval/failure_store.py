"""PII-hardened structured failure store for the SQL agent (CLAUDE.md G4).

Writes one scrubbed JSON record per low-scoring case to
``evals/failures/<run-id>_<case-id>_<mode>.json``. Records are the curated substrate for
A3 (runtime few-shot, production-sourced only), A4 (clustering), and B1/B2.

PII is enforced HERE, at the write seam — not assumed upstream:
  - the free-text ``question`` is run through the fail-closed Presidio scrubber
    (langsmith_capture.scrub), so a production-harvested question with residual PII the
    masker missed cannot land verbatim;
  - agent SQL is literal-masked (sql_diagnostics.scrub), re-applied defensively rather
    than trusting the producer;
  - ``gold_sql`` is null for production captures (no gold exists for a raw trace) until a
    human writes it during confirmation — human review is the gold-field control.

A dev miss with VALID SQL and a non-empty-but-wrong result is a *confident-wrong* case — the
class production heuristics can't catch. In dev it is captured for free: it scores
pass_rate < 1.0, so should_capture() already keeps it (error_category is None, valid_sql_rate
is 1.0). ``failure_kind`` is set by a human at confirmation (definitional -> A3D rules;
structural -> A3 exemplars); A4 proposes it for dev records.

Only ``status == "confirmed"`` records are consumed downstream (curated, not dumped).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.eval.sql.diagnostics import scrub
from app.telemetry.langsmith_capture import scrub as scrub_pii

_FIELD_SEP = "_"
_SOURCES = ("eval", "production")
_HUMAN_TRIAGE_FIELDS = ("failure_kind", "lever", "root_cause", "rule", "gold_sql")


def should_capture(case_summary: dict) -> bool:
    """Low-scoring, non-degenerate case worth storing. Degenerate gold is skipped (any
    query can match it, so its pass_rate is meaningless)."""
    if case_summary.get("degenerate_candidate"):
        return False
    return case_summary.get("pass_rate", 0.0) < 1.0


def failure_record(
    case_summary: dict, case: dict, runs: list[dict], *, run_id: str, mode: str, source: str
) -> dict:
    """Build one scrubbed failure record. ``source`` must be 'eval' or 'production'."""
    if source not in _SOURCES:
        raise ValueError(f"source must be one of {_SOURCES}, got {source!r}")
    misses = [r for r in runs if not r.get("match")]
    rep = (misses or runs or [{}])[0]
    return {
        "schema_version": 3,
        "status": "candidate",
        "source": source,
        "query_type": case.get("query_type"),
        "case_id": case["id"],
        "role": case.get("role"),
        "mode": mode,
        "run_id": run_id,
        "question": scrub_pii(case["question"]),
        "gold_sql": case.get("gold_sql"),
        "agent_sql_scrubbed": scrub(rep.get("generated_sql_scrubbed")),
        "error_category": rep.get("error_category"),
        "reason": rep.get("reason"),
        "valid_sql_rate": case_summary.get("valid_sql_rate"),
        "pass_rate": case_summary.get("pass_rate"),
        "flaky": case_summary.get("flaky", False),
        "chat_deployment": rep.get("chat_deployment"),
        "failure_kind": None,
        "lever": None,
        "root_cause": None,
        "rule": None,
    }


def _safe_segment(text: str) -> str:
    return str(text).replace(_FIELD_SEP, "-")


def failure_path_for(out_dir: Path, *, run_id: str, case_id: str, mode: str, source: str) -> Path:
    """Default path for a new failure record."""
    del source  # reserved for future source-specific layouts
    return out_dir / (
        f"{_safe_segment(run_id)}{_FIELD_SEP}{_safe_segment(case_id)}"
        f"{_FIELD_SEP}{_safe_segment(mode)}.json"
    )


def production_path_for(out_dir: Path, *, case_id: str, mode: str) -> Path:
    """Stable production filename keyed by case_id (dedupe across harvest runs)."""
    return (
        out_dir / f"prod{_FIELD_SEP}{_safe_segment(case_id)}{_FIELD_SEP}{_safe_segment(mode)}.json"
    )


def find_existing_record(
    out_dir: Path, *, case_id: str, mode: str, source: str
) -> tuple[Path, dict] | None:
    """Find an existing record for the same case_id/mode/source."""
    out = Path(out_dir)
    if not out.is_dir():
        return None

    if source == "production":
        stable = production_path_for(out, case_id=case_id, mode=mode)
        if stable.exists():
            try:
                return stable, json.loads(stable.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None

    for fp in sorted(out.glob("*.json")):
        if fp.name.startswith("_") or fp.name == "episodic_exemplars.json":
            continue
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (
            rec.get("case_id") == case_id
            and rec.get("mode") == mode
            and rec.get("source") == source
        ):
            return fp, rec
    return None


def has_human_triage(record: dict) -> bool:
    """True when a human has edited any triage field."""
    for field in _HUMAN_TRIAGE_FIELDS:
        val = record.get(field)
        if val is not None and val != "":
            return True
    return False


def merge_candidate_record(existing: dict, incoming: dict) -> dict:
    """Preserve human triage; refresh machine-observed fields from incoming."""
    merged = dict(existing)
    preserve = has_human_triage(existing)
    machine_fields = (
        "run_id",
        "question",
        "agent_sql_scrubbed",
        "error_category",
        "reason",
        "valid_sql_rate",
        "pass_rate",
        "flaky",
        "chat_deployment",
        "query_type",
        "role",
    )
    for field in machine_fields:
        if field in incoming:
            merged[field] = incoming[field]
    if not preserve:
        for field in _HUMAN_TRIAGE_FIELDS:
            if field in incoming:
                merged[field] = incoming[field]
    merged["status"] = existing.get("status", "candidate")
    return merged


def _load_existing_at_path(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_write_path(
    out_dir: Path,
    *,
    run_id: str,
    case_id: str,
    mode: str,
    source: str,
) -> tuple[Path, dict | None]:
    existing = find_existing_record(out_dir, case_id=case_id, mode=mode, source=source)
    if existing is not None:
        return existing

    if source == "production":
        stable = production_path_for(out_dir, case_id=case_id, mode=mode)
        rec = _load_existing_at_path(stable)
        return stable, rec

    default = failure_path_for(out_dir, run_id=run_id, case_id=case_id, mode=mode, source=source)
    return default, _load_existing_at_path(default)


def write_failures(
    summary: dict,
    cases: list[dict],
    *,
    out_dir: str | Path,
    run_id: str,
    mode: str,
    source: str = "eval",
) -> list[Path]:
    """Write a record per low-scoring case. Idempotent: never clobbers confirmed records."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    case_by_id = {c["id"]: c for c in cases}
    runs_by_id: dict[str, list[dict]] = {}
    for r in summary.get("runs", []):
        runs_by_id.setdefault(r["case_id"], []).append(r)

    written: list[Path] = []
    for cs in summary.get("cases", []):
        if not should_capture(cs):
            continue
        case = case_by_id.get(cs["case_id"])
        if case is None:
            continue

        path, existing = _resolve_write_path(
            out,
            run_id=run_id,
            case_id=cs["case_id"],
            mode=mode,
            source=source,
        )

        if existing is not None and existing.get("status") == "confirmed":
            continue

        rec = failure_record(
            cs, case, runs_by_id.get(cs["case_id"], []), run_id=run_id, mode=mode, source=source
        )

        if existing is not None:
            if has_human_triage(existing):
                rec = merge_candidate_record(existing, rec)
            elif existing.get("run_id") == run_id:
                rec = {**existing, **rec, "status": existing.get("status", "candidate")}

        path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        written.append(path)
    return written
