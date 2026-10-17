"""Shared cell formatting for the deterministic answer presenters."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.business_query.outcomes import Answered

NO_MATCHING_ROWS = "No matching rows."
MISSING = "—"


def group_row_label(row: dict, label_members: list[str]) -> str:
    """Join a grouped row's label members with " / ", missing values included."""
    return " / ".join(str(row.get(member, MISSING)) for member in label_members)


def format_cell_value(val: Any, kind: str | None = None) -> str:
    if kind == "percent" and isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
        return format_percent(val)
    if isinstance(val, (float, Decimal)):
        val_float = float(val)
        if len(str(val)) > 8 or (isinstance(val, float) and len(str(val).split(".")[-1]) > 2):
            return f"{val_float:,.2f}"
        return str(val)
    return str(val)


def format_percent(value: int | float | Decimal, *, signed: bool = False) -> str:
    """A stored ratio reads as a percentage with one decimal: 0.0349949 -> 3.5%."""
    sign = "+" if signed else ""
    return f"{float(value) * 100:{sign}.1f}%"


def column_kinds(answered: Answered) -> dict[str, str]:
    return {column.key: column.value_kind for column in answered.columns}
