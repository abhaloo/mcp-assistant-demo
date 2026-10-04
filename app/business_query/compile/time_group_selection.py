"""Resolve time-group sets before the answer statement runs.

Each time-group set's inner plan is grouped by its calendar bucket, ordered by its
measure with NULLs last and then by the period ascending, and read with one extra
row. The kept periods become half-open bounds the answer statement compares with
the stored column. The caller runs this on the connection and transaction it uses
for the answer statement, so both reads see the same data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.sql import Select

from app.business_query.authorize.scoping import ScopedDerivedSet, ScopedPlan
from app.business_query.compile.business_time import bucket_bounds, business_datetime_bounds
from app.business_query.compile.derived_sets import apply_pick_order
from app.business_query.compile.statement_builder import build_relation, projection_for_grain
from app.business_query.definitions import DimensionDefinition
from app.business_query.plan.query_plan import time_group_for
from app.business_query.plan.time_groups import ResolvedTimeGroup, SelectedPeriod, TimeGroupKey
from app.business_query.ports import CompilerAdapter


@dataclass(frozen=True)
class SelectionRead:
    """One executed selection: the bound set, its statement, and the rows that show why."""

    derived: ScopedDerivedSet
    statement: Select[Any]
    rows: list[dict[str, Any]]


def selection_statement(adapter: CompilerAdapter, derived: ScopedDerivedSet) -> Select[Any]:
    """The set's ranked periods plus one, so a tie past the limit is visible."""
    plan = derived.scoped.plan
    relational = build_relation(adapter, derived.scoped, projection_factory=projection_for_grain)
    # A record with no date belongs to no period (the non-NULL-key rule of ADR 0073).
    dated = relational.statement.where(relational.projection.dim_exprs[derived.key].is_not(None))
    ranked = apply_pick_order(dated, relational.projection, plan, derived.key)
    return ranked.limit(plan.limit + 1)


def resolve_time_groups(
    conn: Connection, adapter: CompilerAdapter, scoped: ScopedPlan
) -> tuple[ScopedPlan, tuple[SelectionRead, ...]]:
    """Resolve every time-group set of the answer plan; entity sets stay as they are."""
    bound: list[ScopedDerivedSet] = []
    reads: list[SelectionRead] = []
    for derived in scoped.derived:
        key = time_group_for(derived.key, derived.scoped.plan)
        if key is None:
            bound.append(derived)
            continue
        statement = selection_statement(adapter, derived)
        result = conn.execute(statement)
        names = list(result.keys())
        rows = [dict(zip(names, row, strict=True)) for row in result.fetchall()]
        resolved = _resolved(adapter, derived, key, rows)
        bound_set = derived.model_copy(update={"resolved": resolved})
        bound.append(bound_set)
        shown = rows if resolved.tie_beyond_limit else rows[: derived.scoped.plan.limit]
        reads.append(SelectionRead(derived=bound_set, statement=statement, rows=shown))
    return scoped.model_copy(update={"derived": tuple(bound)}), tuple(reads)


def _resolved(
    adapter: CompilerAdapter,
    derived: ScopedDerivedSet,
    key: TimeGroupKey,
    rows: list[dict[str, Any]],
) -> ResolvedTimeGroup:
    plan = derived.scoped.plan
    measure = plan.measures[0]
    kept = rows[: plan.limit]
    tie = len(rows) > plan.limit and rows[plan.limit][measure] == kept[-1][measure]
    _kind, definition = adapter._resolve_capability(derived.key)
    if not isinstance(definition, DimensionDefinition):
        raise TypeError(f"expected DimensionDefinition for {derived.key}")
    column_name = definition.sql_expression.strip()
    timezone = adapter._bundle.business_timezone
    periods = []
    for row in kept:
        start = _bucket_start(row[derived.key])
        first, after = bucket_bounds(start, key.granularity)
        lower, upper = business_datetime_bounds(column_name, first, after, timezone)
        periods.append(SelectedPeriod(start=start, lower=lower, upper=upper))
    return ResolvedTimeGroup(
        set_id=derived.id, key=key, periods=tuple(periods), tie_beyond_limit=tie
    )


def _bucket_start(value: object) -> date:
    """A bucket value as a date: MariaDB returns a date or datetime, SQLite a string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise ValueError(f"time bucket value of type {type(value).__name__}")
