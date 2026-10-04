"""Hidden label sidecar column generation for entity-rows projections."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement

from app.business_query.authorize.capability import visible_dimension_names, visible_members
from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.definitions import DimensionDefinition, primary_key_dimension
from app.business_query.ports import CompilerAdapter
from app.business_query.seal.receipts import RECORD_LABEL_PREFIX


def _resource_look_dimension(
    adapter: CompilerAdapter, key: DimensionDefinition, resource: str
) -> DimensionDefinition | None:
    if not key.display_of:
        return None
    look = next(
        (dim for dim in adapter._bundle.dimensions if dim.name == key.display_of),
        None,
    )
    if look is None or look.owning_resource != resource:
        return None
    return look


def build_record_label_sidecars(
    adapter: CompilerAdapter,
    scoped: ScopedPlan,
    tables: dict[str, sa.Table],
    *,
    effective_resources: Sequence[str],
) -> dict[str, ColumnElement[Any]]:
    """Build label sidecar column expressions for joined tables with an id sidecar.

    Fails closed: when scoped.principal is None, the viewer cannot see the look
    member, or the look column is not declared on the table, no label column is
    created.
    """
    if scoped.principal is None:
        return {}

    joined = set(effective_resources)
    if not joined:
        return {}

    visible = visible_members(scoped.principal, adapter._bundle)
    label_exprs: dict[str, ColumnElement[Any]] = {}

    for resource in joined:
        table = tables.get(resource)
        if table is None or "id" not in table.c:
            continue

        key = primary_key_dimension(adapter._bundle, resource)
        if key is None:
            continue

        look = _resource_look_dimension(adapter, key, resource)
        if look is None:
            continue

        if not visible_dimension_names(adapter._bundle, visible, resolves_to=look.name):
            continue

        col = look.sql_expression.strip()
        if col not in table.c:
            continue

        col_name = f"{RECORD_LABEL_PREFIX}{resource}"
        label_exprs[resource] = table.c[col].label(col_name)

    return label_exprs
