"""Dialect time primitives: the age in days for overdue filters, and calendar buckets."""

from __future__ import annotations

from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement

from app.business_query.outcomes import PlanRefused
from app.business_query.plan.time_groups import TimeGranularity


def age_in_days(
    due_date_col: ColumnElement,
    anchor: date,
    dialect_name: str,
) -> ColumnElement:
    """Whole days from due_date to the request business date."""
    if dialect_name == "sqlite":
        return sa.cast(
            sa.func.julianday(sa.func.date(sa.literal(anchor.isoformat())))
            - sa.func.julianday(sa.func.date(due_date_col)),
            sa.Integer,
        )
    if dialect_name in {"mysql", "mariadb"}:
        return sa.func.datediff(sa.literal(anchor.isoformat()), due_date_col)
    raise PlanRefused("grain_unexpressible", check_site="bucket_unsupported_dialect")


def overdue_filter_sql(
    *,
    column_name: str,
    anchor: date,
    after_days: int,
    dialect_name: str,
) -> str:
    """Compile overdue SQL from the same age primitive. Column name is from the definition."""
    age_sql = str(
        age_in_days(sa.literal_column(column_name), anchor, dialect_name).compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    return f"{column_name} IS NOT NULL AND {age_sql} > {int(after_days)}"


def time_bucket(
    column: ColumnElement[Any], granularity: TimeGranularity, dialect_name: str
) -> ColumnElement[Any]:
    """The first day of the calendar bucket that holds the column's value.

    The column holds business-local wall time, so the bucket needs no time zone
    conversion. A week starts on Monday.
    """
    if dialect_name in {"mysql", "mariadb"}:
        return _mysql_bucket(column, granularity)
    if dialect_name == "sqlite":
        return _sqlite_bucket(column, granularity)
    raise PlanRefused("grain_unexpressible", check_site="bucket_unsupported_dialect")


def _mysql_bucket(column: ColumnElement[Any], granularity: TimeGranularity) -> ColumnElement[Any]:
    year_start = sa.func.makedate(sa.func.year(column), 1)
    if granularity == "day":
        return sa.func.date(column)
    if granularity == "week":
        return sa.func.subdate(sa.func.date(column), sa.func.weekday(column))
    if granularity == "month":
        return sa.func.timestampadd(
            sa.literal_column("MONTH"), sa.func.month(column) - 1, year_start
        )
    if granularity == "quarter":
        return sa.func.timestampadd(
            sa.literal_column("QUARTER"), sa.func.quarter(column) - 1, year_start
        )
    return year_start


def _sqlite_bucket(column: ColumnElement[Any], granularity: TimeGranularity) -> ColumnElement[Any]:
    if granularity == "day":
        return sa.func.date(column)
    if granularity == "week":
        return sa.func.date(column, "-6 days", "weekday 1")
    if granularity == "month":
        return sa.func.date(column, "start of month")
    if granularity == "quarter":
        months_in = (sa.cast(sa.func.strftime("%m", column), sa.Integer) - 1) % 3
        return sa.func.date(column, "start of month", sa.func.printf("-%d months", months_in))
    return sa.func.date(column, "start of year")
