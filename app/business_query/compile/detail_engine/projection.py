"""Detail projection contracts, source metadata validation, and table reflection."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.business_query.definitions import (
    DETAIL_VIEW_COLUMNS,
    DetailDefinition,
    DetailSource,
    detail_source_columns,
)
from app.business_query.outcomes import RecordDetail
from app.business_query.plan import DetailSelection

_MAX_PARENT_IDS = 50
_REVISION_HASH_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")

LOCAL_DETAIL_SOURCE_COLUMNS = {
    name: frozenset(columns) for name, columns in DETAIL_VIEW_COLUMNS.items()
}
_LOCAL_DETAIL_SOURCE_COLUMNS = LOCAL_DETAIL_SOURCE_COLUMNS


class DetailSelectionRefused(ValueError):
    """A detail selection is unknown, stale, hidden, or has no safe source."""


@dataclass(frozen=True)
class CanonicalDetailSelection:
    """One authorized selection paired with its signed definition and source."""

    selection: DetailSelection
    definition: DetailDefinition
    definitions: tuple[DetailDefinition, ...]
    source: DetailSource


@dataclass(frozen=True)
class DetailReadResult:
    """Bounded detail facts plus source failures for response-policy handling."""

    details: tuple[RecordDetail, ...]
    failed_families: tuple[str, ...] = ()


def validate_selection(selection: DetailSelection) -> None:
    if selection.revision_mode == "as_of":
        if selection.as_of is None:
            raise DetailSelectionRefused("invalid_detail_revision")
    elif selection.as_of is not None:
        raise DetailSelectionRefused("invalid_detail_revision")
    if selection.revision_mode == "exact" and selection.revision_hash is None:
        raise DetailSelectionRefused("invalid_detail_revision")
    if selection.revision_mode != "exact" and selection.revision_hash is not None:
        raise DetailSelectionRefused("invalid_detail_revision")
    if selection.revision_hash is not None and not _REVISION_HASH_RE.fullmatch(
        selection.revision_hash
    ):
        raise DetailSelectionRefused("invalid_detail_revision")


def registered_source_columns(
    source: DetailSource, source_contracts: dict[str, frozenset[str]]
) -> frozenset[str]:
    signed = detail_source_columns(source)
    if not signed:
        raise DetailSelectionRefused("detail_source_mismatch")
    registered = source_contracts.get(source.projection_view)
    if registered is None:
        raise DetailSelectionRefused("detail_source_unavailable")
    if not signed.issubset(registered):
        raise DetailSelectionRefused("detail_source_mismatch")
    return signed


def reflect_source_table(
    engine: Engine,
    metadata: sa.MetaData,
    tables: dict[str, sa.Table],
    source: DetailSource,
    source_contracts: dict[str, frozenset[str]],
    *,
    record_sql_fn: Any | None = None,
    trace: object | None = None,
) -> sa.Table:
    columns = registered_source_columns(source, source_contracts)
    table = tables.get(source.projection_view)
    if table is not None:
        return table
    try:
        statement = sa.text(f"SELECT * FROM {source.projection_view} LIMIT 0")
        started = time.perf_counter()
        with engine.connect() as connection:
            result = connection.execute(statement)
            physical_columns = {str(name) for name in result.keys()}
        if record_sql_fn is not None:
            record_sql_fn(statement, started, 0, trace)
    except sa.exc.SQLAlchemyError as exc:
        if record_sql_fn is not None:
            record_sql_fn(statement, started, None, trace)
        raise DetailSelectionRefused("detail_source_unavailable") from exc
    if not physical_columns:
        raise DetailSelectionRefused("detail_source_unavailable")
    columns = columns.intersection(physical_columns)
    required_columns = {
        source.owner_column,
        source.typed_value_column,
        source.display_value_column,
        "family_key",
        *(column for column in source.scope_columns.model_dump().values() if column is not None),
    }
    if not required_columns.issubset(columns):
        raise DetailSelectionRefused("detail_source_mismatch")
    table = sa.Table(
        source.projection_view,
        metadata,
        *[
            sa.Column(
                name,
                sa.Integer if name == "id" or name.endswith("_id") else sa.String(255),
                primary_key=name == "id",
            )
            for name in sorted(columns)
        ],
    )
    tables[source.projection_view] = table
    return table
