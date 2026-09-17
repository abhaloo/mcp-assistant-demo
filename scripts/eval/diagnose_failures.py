"""Engine-lite: cluster the failure store and DRAFT diagnoses for human review (A4).

Groups recurring failures by (error bucket x question family) and writes two markdown DRAFTS:
a diagnosis report and a G6 insights-log draft. Each cluster proposes — NOT applies — a likely
lever, the prompt section to inspect, and a routing kind (definitional -> A3D rule / structural
-> A3 exemplar). Never edits prompts/memory: auto-applied self-improvement silently regresses
(~14.8% pass->fail, AgentDevel 2601.04620).

DEV-SCOPED: the sample_sql it reasons over exists only for dev records (the SQL agent's internals
are not in LangSmith — ask_service.py:241-252). Production 👎 records cluster but carry no SQL;
the human recovers it at curation. Running A4 on dev failures (which have gold) also validates
the diagnoser itself.

    python scripts/eval/diagnose_failures.py
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

_FAMILIES = [
    (
        "receivable",
        r"\b(outstanding|unpaid|overdue|aging|ageing|receivable|owed|arrears|past[\s-]?due)\b",
    ),
    ("revenue", r"\b(revenue|income|earnings|turnover)\b"),
    (
        "period_grouping",
        r"\b(per|by)\s+(day|week|month|quarter|year)\b|\bmonthly\b|\bweekly\b|\bquarterly\b",
    ),
    ("status_filter", r"\b(open|pending|in progress|in production|finished|cancelled|status)\b"),
    ("quotation", r"\b(quote|quotation)\b"),
]
_COMPILED = [(n, re.compile(rx, re.IGNORECASE)) for n, rx in _FAMILIES]

_LEVER_HINT = {
    "receivable": (
        "outstanding/unpaid/owed rule + worked example; residual misses are "
        "query-quality (cartesian join) — consider decomposition/timeout."
    ),
    "revenue": (
        "'revenue -> journals' mapping (note: mapping-aware call_get_schema was tried "
        "as Lever 2 and reverted — see insights-log 2026-06-25)."
    ),
    "period_grouping": "'by month / per period' ANSWER SHAPE rule (one row per period).",
    "status_filter": "CANONICAL STATUS / TYPE VALUES block.",
    "quotation": "'quotation -> bills WHERE type=Quotation, no status filter' rule.",
    "other": "No family matched — classify by hand.",
}

_DEFINITIONAL_FAMILIES = {"receivable", "revenue", "quotation", "status_filter"}


def propose_kind(bucket: str, family: str) -> str:
    if bucket in ("invalid_sql", "no_query") or bucket.startswith("error:"):
        return "structural"
    if family in _DEFINITIONAL_FAMILIES:
        return "definitional"
    return "structural"


def family_of(question: str) -> str:
    for name, rx in _COMPILED:
        if rx.search(question or ""):
            return name
    return "other"


def error_bucket(rec: dict) -> str:
    if rec.get("error_category") == "no_query":
        return "no_query"
    if rec.get("valid_sql_rate") is not None and rec["valid_sql_rate"] < 1.0:
        return "invalid_sql"
    if rec.get("error_category"):
        return f"error:{rec['error_category']}"
    return "wrong_result"


def load_failures(path, *, status=None) -> list[dict]:
    out = []
    for fp in sorted(Path(path).glob("*.json")):
        if fp.name.startswith("_") or fp.name == "episodic_exemplars.json":
            continue
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(rec, dict) or "case_id" not in rec:
            continue
        if rec.get("query_type") == "semantic":
            continue  # pure-RAG failure: SQL families/levers don't apply (retained for a RAG arm)
        if status and rec.get("status") != status:
            continue
        out.append(rec)
    return out


def cluster_failures(failures: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in failures:
        groups[(error_bucket(rec), family_of(rec.get("question", "")))].append(rec)
    clusters = [
        {
            "bucket": b,
            "family": f,
            "count": len(recs),
            "case_ids": sorted({r["case_id"] for r in recs}),
            "lever_hint": _LEVER_HINT.get(f, _LEVER_HINT["other"]),
            "kind_hint": propose_kind(b, f),
            "sample_sql": next(
                (r.get("agent_sql_scrubbed") for r in recs if r.get("agent_sql_scrubbed")), None
            ),
            "sample_question": recs[0].get("question"),
        }
        for (b, f), recs in groups.items()
    ]
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def render_report(clusters: list[dict], *, total: int) -> str:
    lines = [
        "# SQL-agent failure diagnosis (DRAFT — human review required)",
        "",
        (
            f"{total} failure record(s); {len(clusters)} cluster(s). "
            "Levers + kinds are HINTS, not fixes."
        ),
        "Confirm a root cause, set status=confirmed, fill failure_kind + lever/root_cause, then",
        "rebuild the channel (structural -> A3 store; definitional -> A3D rules block).",
        "Do NOT auto-apply (AgentDevel 2601.04620).",
        "",
    ]
    for i, c in enumerate(clusters, 1):
        routed = "rule -> A3D" if c["kind_hint"] == "definitional" else "exemplar -> A3"
        lines += [
            f"## Cluster {i}: {c['family']} / {c['bucket']}  (x{c['count']})",
            "",
            f"- **Cases:** {', '.join(c['case_ids'])}",
            f"- **Proposed kind (hint):** {c['kind_hint']} ({routed})",
            f"- **Proposed lever (hint):** {c['lever_hint']}",
            f"- **Sample question:** {c['sample_question']}",
            (
                f"- **Sample SQL (scrubbed):** `{c['sample_sql']}`"
                if c["sample_sql"]
                else "- **Sample SQL:** (none — production record; recover at curation)"
            ),
            "",
        ]
    return "\n".join(lines)


def render_insights_draft(clusters: list[dict], *, total: int) -> str:
    """A G6 insights-log draft (CLAUDE.md): observation / data / hypothesis / follow-up per top
    cluster. The human edits and pastes into docs/insights-log.md — NEVER auto-appended."""
    lines = [f"## DRAFT — {total} SQL-agent failures clustered (review before pasting)", ""]
    for c in clusters[:5]:
        routed = "rule -> A3D" if c["kind_hint"] == "definitional" else "exemplar -> A3"
        lines += [
            f"### {c['family']} / {c['bucket']} (x{c['count']})",
            (
                f"- **Observation:** recurring {c['family']} miss ({c['bucket']}). "
                f"Cases: {', '.join(c['case_ids'])}."
            ),
            (
                f"- **Data:** sample Q: {c.get('sample_question')!r}; "
                f"sample SQL: `{c.get('sample_sql')}`."
            ),
            f"- **Hypothesis:** {c['lever_hint']}",
            (
                f"- **Follow-up:** confirm root cause; set failure_kind={c['kind_hint']} "
                f"({routed}); rebuild the channel."
            ),
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cluster the SQL failure store into a diagnosis draft")
    ap.add_argument("--dir", default="evals/failures")
    ap.add_argument("--status", default=None)
    ap.add_argument("--out", default="evals/failures/_diagnosis.md")
    ap.add_argument("--insights-out", default="evals/failures/_diagnosis-insights.md")
    args = ap.parse_args()
    failures = load_failures(args.dir, status=args.status)
    clusters = cluster_failures(failures)
    Path(args.out).write_text(render_report(clusters, total=len(failures)), encoding="utf-8")
    Path(args.insights_out).write_text(
        render_insights_draft(clusters, total=len(failures)), encoding="utf-8"
    )
    print(
        f"{len(failures)} failure(s) -> {len(clusters)} cluster(s); "
        f"wrote {args.out} + {args.insights_out} (insights DRAFT — review before pasting)"
    )


if __name__ == "__main__":
    main()
