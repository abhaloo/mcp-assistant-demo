"""Deterministic presentation for period comparison answers."""

from __future__ import annotations

from decimal import Decimal

from app.business_query.outcomes import Answered, comparison_member
from app.business_query.plan import BusinessQueryPlan
from app.business_query.wire.cell_format import (
    MISSING,
    NO_MATCHING_ROWS,
    column_kinds,
    format_cell_value,
    format_percent,
    group_row_label,
)
from app.business_query.wire.presenter import present_answered
from app.business_query.wire.result_presentation import COMPARISON_OMISSION_NOTICE

__all__ = ["present_comparison", "present_for_plan"]


def present_for_plan(plan: BusinessQueryPlan, answered: Answered) -> str:
    """Select the presenter for the plan."""
    if plan.compare_to is not None:
        return present_comparison(plan, answered)
    return present_answered(plan, answered)


def present_comparison(plan: BusinessQueryPlan, answered: Answered) -> str:
    """Format comparison answers with sealed delta columns."""
    rows = answered.rows[: plan.limit]
    if not rows:
        return NO_MATCHING_ROWS

    kind = column_kinds(answered).get(plan.measures[0])
    if plan.grain == "scalar":
        return _present_scalar_comparison(plan, rows[0], kind)
    return _present_grouped_comparison(plan, rows, answered.total_row_count, kind)


def _signed(value: object, kind: str | None) -> str:
    """A change reads with its sign: a rise as "+100", a fall as "-400"."""
    text = format_cell_value(value, kind)
    return f"+{text}" if isinstance(value, (int, float, Decimal)) and value > 0 else text


def _comparison_segment(measure: str, row: dict, *, with_pct: bool, kind: str | None) -> str:
    """The "(previous …, change …)" clause; the percentage only on a scalar answer."""
    previous_key = comparison_member(measure, "previous")
    if row.get(previous_key) is None:
        return f"(previous {MISSING}, change {MISSING})"
    prev = format_cell_value(row[previous_key], kind)
    delta_val = row.get(comparison_member(measure, "delta"))
    delta = _signed(delta_val, kind) if delta_val is not None else MISSING
    pct = row.get(comparison_member(measure, "delta_pct"))
    pct_str = f", {format_percent(pct, signed=True)}" if with_pct and pct is not None else ""
    return f"(previous {prev}, change {delta}{pct_str})"


def _present_scalar_comparison(plan: BusinessQueryPlan, row: dict, kind: str | None) -> str:
    m = plan.measures[0]
    cur_val = row.get(m)
    cur = format_cell_value(cur_val, kind) if cur_val is not None else MISSING
    return f"{m}: {cur} {_comparison_segment(m, row, with_pct=True, kind=kind)}"


def _present_grouped_comparison(
    plan: BusinessQueryPlan, rows: list[dict], total_row_count: int, kind: str | None
) -> str:
    label_members = [*plan.dimensions, *([plan.bucket_set] if plan.bucket_set else [])]
    lines: list[str] = []
    m = plan.measures[0]
    for row in rows:
        labels = group_row_label(row, label_members)
        cur_val = row.get(m)
        cur = format_cell_value(cur_val, kind) if cur_val is not None else MISSING
        lines.append(f"{labels}: {cur} {_comparison_segment(m, row, with_pct=False, kind=kind)}")

    n = len(rows)
    if total_row_count == n:
        lines.append(f"({n} groups)")
    else:
        lines.append(f"(showing {n} of {total_row_count} groups)")
    lines.append(COMPARISON_OMISSION_NOTICE)
    return "\n".join(lines)
