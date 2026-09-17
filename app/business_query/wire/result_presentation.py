"""Receipt title, filter facts and column labels for a structured result.

Everything here is derived from the typed plan. The answer sentence is never an
input, so a copy change in the presenter cannot move the title.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from datetime import date
from decimal import Decimal

from app.business_query.outcomes import Answered
from app.business_query.plan import (
    AttributePredicate,
    BusinessPeriod,
    BusinessQueryPlan,
    iter_filter_leaves,
)
from app.business_query.wire.cell_format import format_percent
from app.business_query.wire.explainer import SYSTEM_PREDICATE_MEMBERS, comparison_target
from app.models.result_presentation import SCOPE_MAX_CHARS, ResultPresentation

logger = logging.getLogger(__name__)

_MAX_FACTS = 8
_MAX_FACT_CHARS = 60

# A grouped comparison keeps only the groups the current period has (LEFT JOIN);
# the receipt says so because a table answer drops its prose.
COMPARISON_OMISSION_NOTICE = "Groups with no rows in the current period are not shown."

PLURAL_SUBJECTS: dict[str, str] = {
    "job": "jobs",
    "invoice": "invoices",
    "bill": "invoices",
    "customer": "customers",
    "order": "orders",
    "customer order": "customer orders",
    "customer_order": "customer orders",
    "inventory": "inventory",
}
_SUBJECTS = PLURAL_SUBJECTS
_TIME_SUFFIX = {"at", "on", "date"}


def _entity(member: str) -> str:
    return member.split(".", 1)[0].casefold() if "." in member else ""


def _last(member: str) -> str:
    return re.split(r"[._]", member.casefold())[-1]


def _is_status(member: str) -> bool:
    return member.casefold().endswith("status")


def _is_customer_name(member: str) -> bool:
    lowered = member.casefold()
    return lowered.endswith("customer_name") or lowered == "customer.name"


def build_result_presentation(
    plan: BusinessQueryPlan,
    *,
    count_measures: Collection[str] = (),
    percent_measures: Collection[str] = (),
    scalar_value: object = None,
    scope: str | None = None,
) -> ResultPresentation:
    """Receipt title, filter facts, and summary for one plan.

    ``count_measures`` names the bundle measures whose agg_type counts rows;
    without it only ``*count``-named measures read as counts. ``scalar_value``
    is the lone cell of a scalar answer, when the caller has it. ``scope`` is
    the caller's already-formatted set-context sentence (ADR 0073); a grouped
    comparison appends its own omission notice after it."""
    receipt_scope = _receipt_scope(plan, scope)
    if plan.grain == "scalar" and plan.measures:
        title = _scalar_title(plan, count_measures)
        return ResultPresentation(
            title=title,
            applied_filters=_facts(plan),
            summary=_scalar_summary(plan, count_measures, percent_measures, scalar_value, title),
            scope=receipt_scope,
        )
    return ResultPresentation(title=_title(plan), applied_filters=_facts(plan), scope=receipt_scope)


def _receipt_scope(plan: BusinessQueryPlan, scope: str | None) -> str | None:
    if plan.compare_to is None or plan.grain == "scalar":
        return scope
    if not scope:
        return COMPARISON_OMISSION_NOTICE
    combined = f"{scope} {COMPARISON_OMISSION_NOTICE}"
    # When both sentences cannot fit the receipt, the omission notice wins: a
    # dropped set sentence narrows the answer, a dropped notice misstates it.
    return combined if len(combined) <= SCOPE_MAX_CHARS else COMPARISON_OMISSION_NOTICE


def present_result(answered: Answered, *, scope: str | None = None) -> Answered:
    """Attach the receipt title and facts for one sealed answer.

    Count measures come from the sealed columns, so no caller needs the
    principal or the bundle. ``scope`` carries the caller's already-formatted
    set-context sentence onto the receipt (ADR 0073), so a consumer that
    renders the structured envelope instead of the prose still sees the
    restriction. Presentation is display copy: any failure leaves it empty
    rather than failing the answer."""
    if answered.plan is None:
        return answered
    count_measures = {_bare(column.key) for column in answered.columns if column.is_count}
    percent_measures = {
        _bare(column.key) for column in answered.columns if column.value_kind == "percent"
    }
    scalar_value = None
    if len(answered.rows) == 1 and len(answered.rows[0]) == 1:
        scalar_value = next(iter(answered.rows[0].values()))
    try:
        presentation = build_result_presentation(
            answered.plan,
            count_measures=count_measures,
            percent_measures=percent_measures,
            scalar_value=scalar_value,
            scope=scope,
        )
    except Exception:
        logger.warning("result presentation failed (fail-open, omitted)", exc_info=True)
        return answered
    return answered.model_copy(update={"presentation": presentation})


def column_label(key: str) -> str:
    """``job.created_at`` → ``Created``, ``invoice.customer_name`` → ``Customer name``,
    ``job.id`` / ``job_id`` → ``Job``. The entity prefix is dropped unless it is all
    that names the column."""
    entity, _, bare = key.partition(".")
    if not bare:
        entity, bare = "", key
    parts = [p for p in bare.split("_") if p]
    if parts and parts[-1].casefold() == "id":
        parts.pop()
    if parts and parts[-1].casefold() in _TIME_SUFFIX:
        parts.pop()
    if not parts:
        parts = [p for p in entity.split("_") if p] or [key]
    return _sentence(" ".join(parts))


def _bare(measure: str) -> str:
    return measure.split(".", 1)[-1]


def _is_count_measure(measure: str, count_measures: Collection[str]) -> bool:
    return _bare(measure) in count_measures or _last(measure) == "count"


def _scalar_title(plan: BusinessQueryPlan, count_measures: Collection[str]) -> str:
    """A count reads as "Number of jobs", not the bare measure label — a lone
    value under a column-style label does not say what was counted."""
    measure = plan.measures[0]
    if not _is_count_measure(measure, count_measures):
        return _humanize(measure)
    prefix = _bare(measure).removesuffix("count").strip("_").replace("_", " ")
    subject = prefix or _SUBJECTS.get(_entity(measure)) or _subject(plan)
    return _sentence(f"number of {subject}")


def _scalar_summary(
    plan: BusinessQueryPlan,
    count_measures: Collection[str],
    percent_measures: Collection[str],
    value: object,
    title: str,
) -> str | None:
    """Count zero needs empty-copy text; other numeric scalars show title + value."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    if _is_count_measure(plan.measures[0], count_measures):
        if value != 0:
            return None
        return "Nothing matched in the records I can query."
    if _bare(plan.measures[0]) in percent_measures:
        return f"{title}: {format_percent(value)}"
    return f"{title}: {value:,.2f}"


def _title(plan: BusinessQueryPlan) -> str:
    subject = _subject(plan)
    status = _status_value(plan)
    customer = _customer_value(plan)
    if status:
        head = _sentence(f"{status} {subject}")
    elif not _has_filters(plan) and _newest_first(plan):
        head = _sentence(f"recent {subject}")
    else:
        head = _sentence(subject)
    return f"{head} · {customer}" if customer else head


def _facts(plan: BusinessQueryPlan) -> list[str]:
    facts: list[str] = []
    for leaf in _plan_filters(plan):
        fact = _fact_for(leaf)
        if fact and fact not in facts:
            facts.append(fact)
    for predicate in plan.attribute_predicates:
        fact = _attribute_fact(predicate)
        if fact and fact not in facts:
            facts.append(fact)
    period = _period_fact(plan.period)
    if period:
        facts.append(period)
    compare = _compare_fact(plan)
    if compare:
        facts.append(compare)
    if not facts and _newest_first(plan):
        facts.append("newest first")
    return [_clip(f) for f in facts[:_MAX_FACTS]]


def _fact_for(leaf) -> str | None:
    if isinstance(leaf, AttributePredicate):
        return _attribute_fact(leaf)
    if leaf.operator in ("in_set", "not_in_set"):
        return None
    member = leaf.member.strip()
    values = [str(v) for v in leaf.values]
    if _is_customer_name(member) and leaf.operator in ("eq", "contains") and values:
        return values[0]
    if _is_status(member) and values:
        return ", ".join(v.replace("_", " ").casefold() for v in values)
    label = _sentence(member.split(".", 1)[-1].replace("_", " "))
    if not values:
        return label
    if leaf.operator == "eq" and leaf.values == [True] and isinstance(leaf.values[0], bool):
        return label
    if leaf.operator in ("eq", "contains"):
        return f"{label}: {values[0]}"
    symbol = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "neq": "≠"}.get(leaf.operator)
    if symbol:
        return f"{label} {symbol} {values[0]}"
    return f"{label}: {', '.join(values)}"


def _attribute_fact(predicate: AttributePredicate) -> str | None:
    values = ", ".join(str(v) for v in predicate.values)
    return f"{predicate.family_key}: {values}" if values else predicate.family_key


def _period_fact(period: BusinessPeriod | None) -> str | None:
    if period is None:
        return None
    if period.relative:
        return str(period.relative).replace("_", " ")
    if period.on:
        return f"on {_day(period.on)}"
    if period.since:
        return f"since {_day(period.since)}"
    if period.between:
        return f"{_day(period.between[0])} to {_day(period.between[1])}"
    return None


def _compare_fact(plan: BusinessQueryPlan) -> str | None:
    label = comparison_target(plan.compare_to, format_day=_day)
    return f"vs {label}" if label is not None else None


def plan_subject(plan: BusinessQueryPlan) -> str | None:
    for member in [*plan.dimensions, *plan.measures]:
        entity = _entity(member)
        if entity in _SUBJECTS:
            return _SUBJECTS[entity]
    return None


def step_subject(plan: BusinessQueryPlan) -> str | None:
    return plan_subject(plan)


def _subject(plan: BusinessQueryPlan) -> str:
    return plan_subject(plan) or "records"


def _is_system(member: str) -> bool:
    """``tenant_id`` and ``job.tenant_id`` are both the system predicate."""
    bare = member.partition(".")[2] or member
    return bare.casefold() in SYSTEM_PREDICATE_MEMBERS


def _plan_filters(plan: BusinessQueryPlan):
    """User-visible predicate leaves: system predicates are not facts."""
    if not plan.derived_sets:
        for group in (plan.filters, plan.having):
            if group is None:
                continue
            for leaf in iter_filter_leaves(group):
                if isinstance(leaf, AttributePredicate) or not _is_system(leaf.member):
                    yield leaf
        return

    for group in (plan.filters, plan.having):
        if group is None:
            continue
        yield from _conjunction_filter_leaves(group)


def _conjunction_filter_leaves(node):
    if node is None:
        return
    if isinstance(node, AttributePredicate):
        yield node
    elif hasattr(node, "member"):
        if not _is_system(node.member) and node.operator not in ("in_set", "not_in_set"):
            yield node
    elif hasattr(node, "all") and node.all:
        for child in node.all:
            yield from _conjunction_filter_leaves(child)


def _status_value(plan: BusinessQueryPlan) -> str | None:
    for leaf in _plan_filters(plan):
        if not isinstance(leaf, AttributePredicate) and _is_status(leaf.member) and leaf.values:
            return str(leaf.values[0]).replace("_", " ").casefold()
    return None


def _customer_value(plan: BusinessQueryPlan) -> str | None:
    for leaf in _plan_filters(plan):
        if (
            not isinstance(leaf, AttributePredicate)
            and _is_customer_name(leaf.member)
            and leaf.operator in ("eq", "contains")
            and leaf.values
        ):
            return str(leaf.values[0])
    return None


def _has_filters(plan: BusinessQueryPlan) -> bool:
    return (
        any(True for _ in _plan_filters(plan))
        or bool(plan.attribute_predicates)
        or plan.period is not None
    )


def _newest_first(plan: BusinessQueryPlan) -> bool:
    if not plan.order or plan.order[0].direction != "desc":
        return False
    member = plan.order[0].member.casefold()
    return _last(member) in _TIME_SUFFIX or "created" in member


def _humanize(member: str) -> str:
    return _sentence(member.split(".")[-1].replace("_", " "))


def _sentence(text: str) -> str:
    words = " ".join(text.split())
    return words[:1].upper() + words[1:].lower() if words else ""


def _day(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def _clip(fact: str) -> str:
    return fact if len(fact) <= _MAX_FACT_CHARS else fact[: _MAX_FACT_CHARS - 1] + "…"
