"""Detail row parsing, value coercion, and observation mapping."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from app.business_query.compile.detail_engine.projection import CanonicalDetailSelection
from app.business_query.definitions import DetailSource
from app.business_query.outcomes import RecordDetail


def parse_typed_value(value: Any, value_kind: str) -> Any:
    """Parse JSON strings into Python primitives based on signed value_kind."""
    if not isinstance(value, str):
        return value

    if value_kind in {"string", "text"}:
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value

    if value_kind == "enum":
        return parsed if isinstance(parsed, str) else value
    if value_kind in {"integer", "decimal", "money", "quantity", "boolean"}:
        return parsed
    if value_kind in {"date", "datetime"}:
        return parsed if isinstance(parsed, str) else value
    return value


def build_record_detail(
    row: Mapping[str, Any],
    source: DetailSource,
    selection: CanonicalDetailSelection,
    *,
    family: str,
    revision_column: sa.Column | None,
    optional_columns: dict[str, sa.Column | None],
) -> RecordDetail:
    """Convert one mapping row to a validated RecordDetail instance."""
    raw_value = row[source.typed_value_column]
    row_revision = row.get("revision_hash") if revision_column is not None else None
    row_definition = next(
        (
            definition
            for definition in selection.definitions
            if definition.revision_hash == row_revision
        ),
        selection.definition,
    )
    typed_value = parse_typed_value(raw_value, row_definition.value_kind)
    owner_id = row[source.owner_column]
    if isinstance(owner_id, str) and owner_id.isdecimal():
        owner_id = int(owner_id)

    return RecordDetail(
        family=family,
        typed_value=typed_value,
        display_value=row[source.display_value_column],
        revision_hash=row_revision,
        definition_revision=row_definition.revision_hash,
        observation_version=(
            int(row["revision"])
            if optional_columns["revision"] is not None and str(row.get("revision", "")).isdigit()
            else None
        ),
        validation=(
            row.get("validation_state")
            if optional_columns["validation_state"] is not None
            else None
        ),
        coverage_status="complete",
        coverage="verified_complete",
        provenance={
            "projection_view": source.projection_view,
            **{
                name: row.get(name)
                for name in (
                    "source",
                    "provenance",
                    "source_fingerprint",
                    "source_updated_at",
                    "profile_revision_hash",
                    "unit",
                )
                if optional_columns[name] is not None
            },
        },
        owner_resource=source.owner_resource,
        owner_id=owner_id,
        profile_revision_hash=(
            row.get("profile_revision_hash")
            if optional_columns["profile_revision_hash"] is not None
            else None
        ),
    )
