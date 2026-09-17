"""One dialect age primitive for days-past-due and overdue filters."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement

from app.business_query.outcomes import PlanRefused


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
