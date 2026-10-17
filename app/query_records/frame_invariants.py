"""Pure invariants over an Ask v2 frame log: every streamed column is typed by the wire."""

from __future__ import annotations

from typing import Any

WIRE_VALUE_TYPES = frozenset(
    {"integer", "decimal", "percent", "currency", "date", "datetime", "boolean", "text", "id"}
)


def check_table_frames(frames: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    rows_by_table: dict[str, int] = {}
    total_row_count_by_table: dict[str, int] = {}
    for frame in frames:
        if frame.get("event_type") == "table_rows":
            table_id = str(frame.get("table_id"))
            rows_by_table[table_id] = rows_by_table.get(table_id, 0) + len(frame.get("rows") or [])
        elif frame.get("event_type") == "table_end":
            table_id = str(frame.get("table_id"))
            if "total_row_count" in frame and frame["total_row_count"] is not None:
                total_row_count_by_table[table_id] = int(frame["total_row_count"])
    for frame in frames:
        if frame.get("event_type") != "table_start":
            continue
        table_id = str(frame.get("table_id"))
        columns = frame.get("columns") or []
        keys = {c.get("key") for c in columns}
        for column in columns:
            col_key = column.get("key")
            value_type = column.get("value_type")
            if value_type not in WIRE_VALUE_TYPES:
                findings.append(f"{table_id}: column {col_key!r} value_type {value_type!r}")
            if value_type == "currency" and column.get("currency_key") not in keys:
                findings.append(
                    f"{table_id}: currency column {col_key!r} has no sibling currency_key"
                )
        presentation = frame.get("presentation")
        has_rows = rows_by_table.get(table_id, 0) > 0
        is_zero_row = total_row_count_by_table.get(table_id) == 0
        if (presentation is not None or has_rows or is_zero_row) and not (
            isinstance(presentation, dict) and bool(presentation.get("title"))
        ):
            findings.append(f"{table_id}: invalid or missing presentation title")
    return findings
