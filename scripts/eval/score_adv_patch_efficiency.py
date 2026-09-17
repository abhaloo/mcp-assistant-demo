#!/usr/bin/env python3
"""Score historical plan-patch reopeners against plan-docs Done-when checklist.

Docs/skills process gate only — does NOT measure RAG/SQL product accuracy.
Corpus is hand-coded from 2026-08-05 Vanna-gap review-logs (SAME_CLASS themes).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

# Plan-docs Done-when items (adversarial-patch SKILL plan-docs mode).
CHECKLIST_ITEMS: dict[str, str] = {
    "1_live_cite": "Re-read every cited file:line before claiming fix",
    "2_ac_producer": "Each AC: oracle layer + producer test (not leaf helper)",
    "3_dg_xor": "Decision Gate XOR matrix (meetable/REJECTED/deferred-owner)",
    "4_no_honor_flag": "Ban honor-system flags unless fail-closed+invoke+test",
    "5_binding_safe": "Never bare `is` on partial/closure callables",
    "6_adr_0030": "ADR 0030 consumer table on public signature changes",
    "7_sole_attach": "Shared seams: sole-attachment ownership",
    "8_self_sim": "Self-sim: skip wiring → still green-fake? → not CLOSED",
}

# Thin Composer P2 rules BEFORE this change (PROMPTS.md § Plan patcher old).
OLD_THIN_RULES: frozenset[str] = frozenset(
    {
        "status_draft",
        "no_invent_dstar",
        "preserve_tdd_structure",
        "cross_link_deps",
    }
)


@dataclass(frozen=True)
class HistoricalFinding:
    """One SAME_CLASS reopen theme from the 2026-08-05 review-log corpus."""

    id: str
    vector: str
    theme: str
    source_log: str
    evidence: str
    # Checklist items that would have blocked cosmetic CLOSED under NEW rules.
    new_blocking_items: tuple[str, ...]
    # Whether OLD thin Composer rules would have blocked CLOSED (almost never).
    old_would_block_closed: bool


CORPUS: tuple[HistoricalFinding, ...] = (
    HistoricalFinding(
        id="A2-AC6-wrong-layer",
        vector="A2",
        theme="AC6 wrong-layer / gameable kwargs helper ≠ live evaluate submit",
        source_log="2026-08-05-agent-memory-feedback-flywheel-tdd.review-log.md",
        evidence=(
            "Pass 3–4: on_arm_run_case_kwargs / for_eval_case spy without live "
            "evaluate_sql_agent.submit_run_cases → run_case wiring"
        ),
        new_blocking_items=("2_ac_producer", "8_self_sim", "6_adr_0030"),
        old_would_block_closed=False,
    ),
    HistoricalFinding(
        id="A2-ask-handler-principal",
        vector="A2",
        theme="ask→handler principal: leaf spy while _ask_traced omits principal",
        source_log="2026-08-05-agent-memory-feedback-flywheel-tdd.review-log.md",
        evidence=(
            "Pass 4–5: handler-only spies green while ask_service._ask_traced "
            "still omits principal= (producer miss)"
        ),
        new_blocking_items=("1_live_cite", "2_ac_producer", "8_self_sim"),
        old_would_block_closed=False,
    ),
    HistoricalFinding(
        id="S2-READY-honor-system",
        vector="S2",
        theme="ORACLE_B_READY / plant honor-system unlocks cutover",
        source_log="2026-08-05-azure-access-tier-filter-probe-tdd.review-log.md",
        evidence=(
            "Pass 4–5: ACCESS_TIER_PROBE_ORACLE_B_READY honor-system; plant "
            "default/assert True; READY without same-commit throwaway invoke"
        ),
        new_blocking_items=("4_no_honor_flag", "8_self_sim"),
        old_would_block_closed=False,
    ),
    HistoricalFinding(
        id="S2-early-degraded-CFG",
        vector="S2",
        theme="Live CFG early-return: schema-only still cutover-eligible",
        source_log="2026-08-05-azure-access-tier-filter-probe-tdd.review-log.md",
        evidence=(
            "Pass 4: reindex.py main() pass/degraded → cutover before Oracle B; "
            "live cite required to see early unlock"
        ),
        new_blocking_items=("1_live_cite", "3_dg_xor", "4_no_honor_flag"),
        old_would_block_closed=False,
    ),
    HistoricalFinding(
        id="C1-D4-header-XOR",
        vector="C1",
        theme="Decision Gate XOR: D4=header makes AC1 unmeetable on JSON",
        source_log="2026-08-05-prod-sql-cost-ceiling-incomplete-ux-tdd.review-log.md",
        evidence=(
            "Pass 2/4: D4=header/SSE-only left no router header carrier; "
            "AC1 JSON oracle unmeetable until D4 locked to fields"
        ),
        new_blocking_items=("3_dg_xor", "2_ac_producer"),
        old_would_block_closed=False,
    ),
    HistoricalFinding(
        id="C1-D2-done-XOR",
        vector="C1",
        theme="Decision Gate XOR: D2=done left AC8/stream incomplete unwired",
        source_log="2026-08-05-prod-sql-cost-ceiling-incomplete-ux-tdd.review-log.md",
        evidence=(
            "Pass 4: D2=done without replacing ask_stream ok path; AC still claimed meetable"
        ),
        new_blocking_items=("3_dg_xor", "2_ac_producer", "8_self_sim"),
        old_would_block_closed=False,
    ),
    HistoricalFinding(
        id="S1X1-bare-transform-is",
        vector="S1/X1",
        theme="Bare `transform is` vs partial/closure binding",
        source_log=(
            "2026-08-05-sql-row-rls-transform-args-tdd.review-log.md + "
            "2026-08-05-tool-registry-sse-hybrid-tdd.review-log.md"
        ),
        evidence=(
            "Pass 2→3: AC2 `transform is rls_transform_args` unmeetable under "
            "partial; dual rls_or_noop; needs binding-safe oracle + sole-attach"
        ),
        new_blocking_items=("5_binding_safe", "7_sole_attach", "8_self_sim"),
        old_would_block_closed=False,
    ),
)


def new_would_block_closed(finding: HistoricalFinding) -> bool:
    """True if any plan-docs Done-when item would refuse CLOSED."""
    return bool(finding.new_blocking_items) and all(
        item in CHECKLIST_ITEMS for item in finding.new_blocking_items
    )


def score_corpus(
    corpus: tuple[HistoricalFinding, ...] = CORPUS,
) -> dict[str, object]:
    """Return before/after metrics for the historical reopen corpus."""
    rows: list[dict[str, object]] = []
    new_blocks = 0
    old_blocks = 0
    for finding in corpus:
        blocked_new = new_would_block_closed(finding)
        blocked_old = finding.old_would_block_closed
        if blocked_new:
            new_blocks += 1
        if blocked_old:
            old_blocks += 1
        rows.append(
            {
                "id": finding.id,
                "vector": finding.vector,
                "theme": finding.theme,
                "old_would_block_closed": blocked_old,
                "new_would_block_closed": blocked_new,
                "blocking_checklist_items": list(finding.new_blocking_items),
                "blocking_item_labels": [CHECKLIST_ITEMS[i] for i in finding.new_blocking_items],
                "source_log": finding.source_log,
                "evidence": finding.evidence,
            }
        )
    n = len(corpus)
    return {
        "n": n,
        "old_blocked": old_blocks,
        "new_blocked": new_blocks,
        "old_block_rate": old_blocks / n if n else 0.0,
        "new_block_rate": new_blocks / n if n else 0.0,
        "delta_blocked": new_blocks - old_blocks,
        "old_thin_rules": sorted(OLD_THIN_RULES),
        "checklist_items": CHECKLIST_ITEMS,
        "rows": rows,
    }


def format_markdown_table(result: dict[str, object]) -> str:
    """Render a compact markdown metrics table."""
    lines = [
        "| finding | old block CLOSED? | new block CLOSED? | checklist items |",
        "|---------|-------------------|-------------------|-----------------|",
    ]
    for row in result["rows"]:  # type: ignore[index]
        items = ", ".join(row["blocking_checklist_items"])  # type: ignore[index]
        lines.append(
            f"| {row['id']} | "
            f"{'Y' if row['old_would_block_closed'] else 'N'} | "
            f"{'Y' if row['new_would_block_closed'] else 'N'} | "
            f"{items} |"
        )
    n = result["n"]
    lines.extend(
        [
            "",
            f"**Summary:** old blocked {result['old_blocked']}/{n} "
            f"({result['old_block_rate']:.0%}); "
            f"new blocked {result['new_blocked']}/{n} "
            f"({result['new_block_rate']:.0%}); "
            f"delta +{result['delta_blocked']}.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full JSON score result",
    )
    args = parser.parse_args()
    result = score_corpus()
    if args.json:
        # dataclasses in nested form already plain via score_corpus
        print(json.dumps(result, indent=2))
    else:
        print(format_markdown_table(result))
        print()
        print(
            "NOTE: Checklist replay is not live future loop proof. SQL/RAG accuracy not measured."
        )
    # Sanity: corpus rows are serializable
    _ = [asdict(f) for f in CORPUS]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
