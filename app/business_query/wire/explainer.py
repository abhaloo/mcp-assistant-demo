"""Plain-English AST Query Explainer & Filter Narrative (Cycle UX-4A / ADR 0053)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.business_query.plan import AttributePredicate, FilterGroup, iter_filter_leaves
from app.models.schemas import QueryExplanation

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from app.business_query.plan import (
        BusinessPeriod,
        BusinessQueryPlan,
        CompareShift,
        PlanFilter,
    )

SYSTEM_PREDICATE_MEMBERS = frozenset(
    {
        "tenant_id",
        "deleted_at",
        "is_archived",
        "created_by_user_id",
        "company_id",
        "created_by",
        "updated_by",
        "is_deleted",
    }
)

_OP_SYMBOLS = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "eq": "=",
    "neq": "!=",
}


def _format_filter(node: PlanFilter) -> str | None:
    member = node.member.strip()
    if member.casefold() in SYSTEM_PREDICATE_MEMBERS:
        return None

    op = node.operator
    if op in ("in_set", "not_in_set"):
        return None
    values = node.values

    member_lower = member.casefold()
    if "status" in member_lower:
        val = ", ".join(str(v).replace("_", " ").title() for v in values) if values else ""
        if op == "neq":
            return f"Status != {val}"
        return f"Status: {val}"
    if "customer" in member_lower:
        val = str(values[0]) if values else ""
        return f"Customer: {val}" if op in ("eq", "contains") else f"Customer ({op}): {val}"

    if any(k in member_lower for k in ("amount", "total", "subtotal", "balance", "price")):
        val = str(values[0]) if values else ""
        sym = _OP_SYMBOLS.get(op, op)
        return f"Amount {sym} {val}"

    member_label = member.replace("_", " ").title()
    if op in ("eq", "contains") and values:
        return f"{member_label}: {values[0]}"
    if op in _OP_SYMBOLS and values:
        return f"{member_label} {_OP_SYMBOLS[op]} {values[0]}"
    if op in ("in", "not_in") and values:
        return f"{member_label} in ({', '.join(str(v) for v in values)})"
    if op == "is_null":
        return f"{member_label} is not set"
    if op == "not_null":
        return f"{member_label} is set"

    return f"{member_label}: {', '.join(str(v) for v in values)}" if values else member_label


def _collect_applied_filters(group: FilterGroup | None, target: list[str]) -> None:
    if group is None:
        return
    for item in iter_filter_leaves(group):
        if isinstance(item, AttributePredicate):
            val_str = ", ".join(str(v) for v in item.values) if item.values else ""
            label = f"{item.family_key}: {val_str}" if val_str else item.family_key
            if label and label not in target:
                target.append(label)
        else:
            formatted = _format_filter(item)
            if formatted and formatted not in target:
                target.append(formatted)


def _format_period(period: BusinessPeriod | None) -> str | None:
    if period is None:
        return None
    if period.relative:
        return f"Period: {period.relative.replace('_', ' ').title()}"
    if period.since:
        return f"Date >= {period.since}"
    if period.on:
        return f"Date: {period.on}"
    if period.between:
        return f"Date between {period.between[0]} and {period.between[1]}"
    return None


def comparison_target(
    compare_to: BusinessPeriod | CompareShift | None,
    *,
    format_day: Callable[[date], str] = str,
) -> str | None:
    """The comparison target in words. Callers choose the day format and the casing."""
    if compare_to is None:
        return None
    if isinstance(compare_to, str):
        return compare_to.replace("_", " ")
    if compare_to.relative:
        return str(compare_to.relative).replace("_", " ")
    if compare_to.on:
        return format_day(compare_to.on)
    if compare_to.since:
        return f"since {format_day(compare_to.since)}"
    if compare_to.between:
        start, end = compare_to.between
        return f"{format_day(start)} to {format_day(end)}"
    return None


def _format_comparison(plan: BusinessQueryPlan) -> str | None:
    compare_to = plan.compare_to
    label = comparison_target(compare_to)
    if label is None:
        return None
    if isinstance(compare_to, str) or compare_to.relative:
        label = label.title()
    return f"Compared with: {label}"


def explain_business_query_plan(
    plan: BusinessQueryPlan | None,
    *,
    matched_records_count: int = 0,
    execution_time_ms: int | None = None,
) -> QueryExplanation:
    """Generate plain-English summary and sanitize system-level filters from AST."""
    if plan is None:
        return QueryExplanation(
            plain_english="Executed query on business records.",
            applied_filters=[],
            matched_records_count=matched_records_count,
            verified_badge=True,
            execution_time_ms=execution_time_ms,
        )

    applied_filters: list[str] = []
    _collect_applied_filters(plan.filters, applied_filters)
    _collect_applied_filters(plan.having, applied_filters)

    period_str = _format_period(plan.period)
    if period_str and period_str not in applied_filters:
        applied_filters.append(period_str)
    comparison_str = _format_comparison(plan)
    if comparison_str:
        applied_filters.append(comparison_str)

    # Plain English narrative derivation
    customer_filters = [f for f in applied_filters if f.startswith("Customer: ")]
    status_filters = [f for f in applied_filters if f.startswith("Status: ")]

    subject = "records"
    if any("invoice" in d.lower() or "bill" in d.lower() for d in plan.dimensions):
        subject = "invoices"
    elif any("job" in d.lower() for d in plan.dimensions):
        subject = "jobs"
    elif any("customer" in d.lower() for d in plan.dimensions):
        subject = "customers"

    if customer_filters and status_filters:
        cust_name = customer_filters[0].replace("Customer: ", "").strip()
        status_name = status_filters[0].replace("Status: ", "").strip().lower()
        plain_english = f"Showing {status_name} {subject} for customer {cust_name}."
    elif customer_filters:
        cust_name = customer_filters[0].replace("Customer: ", "").strip()
        plain_english = f"Showing {subject} for customer {cust_name}."
    elif status_filters:
        status_name = status_filters[0].replace("Status: ", "").strip().lower()
        plain_english = f"Showing {status_name} {subject} matching your criteria."
    elif plan.grain == "scalar":
        plain_english = "Summary metrics calculated from matching business records."
    else:
        plain_english = f"Showing {subject} matching your specified criteria."

    return QueryExplanation(
        plain_english=plain_english,
        applied_filters=applied_filters,
        matched_records_count=matched_records_count,
        verified_badge=True,
        execution_time_ms=execution_time_ms,
    )
