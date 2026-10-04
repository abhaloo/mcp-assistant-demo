"""Wire column types for streamed tables: one closed table over the seal's declaration."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from app.business_query.wire.result_presentation import column_label
from app.models.ask_v2_events import TableColumn

WIRE_VALUE_TYPES = MappingProxyType(
    {
        "integer": "integer",
        "decimal": "decimal",
        "percent": "percent",
        "currency": "currency",
        "date": "date",
        "datetime": "datetime",
        "boolean": "boolean",
        "string": "text",
    }
)


def wire_table_columns(raw_columns: list[dict[str, Any]]) -> list[TableColumn]:
    """Map sealed ``ResultColumn`` dumps to ``TableColumn``. Unknown kinds are an error.

    A sealed ``href_template`` is copied, never minted: the seal alone decides
    resolvability under the viewer's link grant, so a column without one is
    plain text whatever its key looks like."""
    columns: list[TableColumn] = []
    for raw in raw_columns:
        key = raw["key"]
        href_template: str | None = raw.get("href_template")
        if raw.get("is_identifier"):
            value_type = "id"
        else:
            kind = raw.get("value_kind")
            if kind not in WIRE_VALUE_TYPES:
                raise ValueError(f"result column {key!r} has no wire type for kind {kind!r}")
            value_type = WIRE_VALUE_TYPES[kind]
        columns.append(
            TableColumn(
                key=key,
                label=column_label(key, label=raw.get("label")),
                value_type=value_type,
                currency_key=raw.get("currency_key"),
                href_template=href_template,
                role=raw.get("role"),
                display_key=raw.get("display_key"),
            )
        )
    return columns
