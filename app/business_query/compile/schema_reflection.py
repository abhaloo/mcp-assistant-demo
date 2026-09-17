"""Schema reflection -- declare SQLAlchemy Table/MetaData from bundle projection views."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import sqlalchemy as sa

from app.business_query.definitions import (
    DETAIL_VIEW_COLUMNS,
    VIEW_COLUMNS,
    DefinitionBundle,
    ResourceBinding,
    detail_source_columns,
)


def build_metadata(bundle: DefinitionBundle) -> sa.MetaData:
    """Declare bundle projection tables from signed column metadata (no reflection)."""
    md = sa.MetaData()
    seen: set[str] = set()
    detail_sources_by_view: dict[str, list[Any]] = defaultdict(list)
    for source in bundle.detail_sources:
        detail_sources_by_view[source.projection_view].append(source)

    def column_type(name: str, sources: list[Any]) -> sa.types.TypeEngine:
        if not sources:
            return sa.Integer()
        numeric_columns = {
            "id",
        }
        for source in sources:
            numeric_columns.update(
                {
                    source.owner_column,
                    source.scope_columns.entity,
                    source.scope_columns.department,
                }
            )
        definitions = [
            detail
            for detail in bundle.detail_definitions
            if any(detail.family_key == source.family_key for source in sources)
        ]
        value_kinds = {detail.value_kind for detail in definitions}
        if value_kinds <= {"integer", "quantity", "decimal", "money", "boolean"}:
            numeric_columns.update(source.typed_value_column for source in sources)
        return sa.Integer() if name in numeric_columns else sa.String(255)

    for resource in bundle.resources:
        view = resource.projection_view
        if view in seen:
            continue
        seen.add(view)
        cols = VIEW_COLUMNS.get(view, DETAIL_VIEW_COLUMNS.get(view, frozenset()))
        sources = detail_sources_by_view.get(view, [])
        for source in sources:
            cols = cols | detail_source_columns(source)
        sa.Table(
            view,
            md,
            *[
                sa.Column(
                    name,
                    column_type(name, sources),
                    primary_key=name == "id",
                )
                for name in sorted(cols)
            ],
        )

    for source in bundle.detail_sources:
        view = source.projection_view
        if view in seen:
            continue
        seen.add(view)
        columns = detail_source_columns(source)
        sa.Table(
            view,
            md,
            *[
                sa.Column(
                    name,
                    column_type(name, [source]),
                    primary_key=name == "id",
                )
                for name in sorted(columns)
            ],
        )
    return md


def table_for(
    resource_name: str, resources: dict[str, ResourceBinding], metadata: sa.MetaData
) -> sa.Table:
    binding = resources[resource_name]
    return metadata.tables[binding.projection_view]
