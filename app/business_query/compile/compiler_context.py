"""CompilerContext — typed compilation state and seam facts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement

from app.business_query.authorize.capability import CapabilityEntry
from app.business_query.authorize.scoping import ForcedPredicate
from app.business_query.definitions import (
    DefinitionBundle,
    DimensionDefinition,
    JoinDefinition,
    MeasureDefinition,
    ResourceBinding,
)


@dataclass(frozen=True, slots=True)
class CompilerContext:
    """Compilation context holding immutable bundle metadata, tables, and seam functions."""

    bundle: DefinitionBundle
    metadata: sa.MetaData
    resources: dict[str, ResourceBinding]
    dimensions: dict[str, DimensionDefinition]
    measures: dict[str, MeasureDefinition]
    capabilities: dict[str, CapabilityEntry]
    joins: list[JoinDefinition]
    table_for: Callable[[str], sa.Table]
    resolve_capability: Callable[[str], tuple[str, MeasureDefinition | DimensionDefinition]]
    forced_predicates: Callable[
        [tuple[ForcedPredicate, ...], dict[str, sa.Table]], list[ColumnElement[bool]]
    ]
    measure_expr: Callable[..., ColumnElement[Any]]
    dim_expr: Callable[..., ColumnElement[Any]]
    due_date_column: Callable[[sa.Table, str], ColumnElement[Any]]
    dialect_name: str = "mysql"
