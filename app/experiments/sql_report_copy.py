"""Report prose the SQL summary artifact embeds as data, not as inline strings."""

from __future__ import annotations

__all__ = ["PASS_RATE_SENSITIVITY_NOTE"]

PASS_RATE_SENSITIVITY_NOTE = (
    "Same paired bootstrap the gate runs, recomputed with degenerate-gold "
    "cases removed. The gate itself keeps them (dropping them silently would "
    "redefine the comparison), but their ~0 delta pulls the paired mean "
    "toward zero and makes the regression check MORE permissive - so this is "
    "the number to check before trusting a narrow pass. "
    "scripts/eval/analyze_sql_runs.py carves the same cases out of its headline."
)
