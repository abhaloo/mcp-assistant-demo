"""Internal AST-node lowering handler for AttributePredicate."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from app.auth import Principal
from app.business_query.definitions import (
    DefinitionBundle,
    detail_source_columns,
)
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import AttributePredicate
from app.business_query.plan.detail_family import resolve_detail_definition

_NUMERIC_VALUE_KINDS = frozenset({"integer", "numeric", "decimal", "currency", "money", "quantity"})


def _get_fact_table(
    view_name: str,
    meta: sa.MetaData,
    *,
    bundle: DefinitionBundle | None = None,
    family_key: str | None = None,
) -> sa.Table:
    """Return a detail projection declared by the selected signed bundle.

    ``view_name`` comes from a resolved signed definition, while the source
    declaration supplies the columns and family binding. The public compiler
    always supplies a bundle and therefore cannot select an arbitrary source
    by name.
    """
    if bundle is None:
        raise PlanRefused("member_not_found")
    source = next(
        (
            candidate
            for candidate in bundle.detail_sources
            if candidate.projection_view == view_name
            and (family_key is None or candidate.family_key == family_key)
        ),
        None,
    )
    if source is None:
        raise PlanRefused("member_not_found")
    source_columns = detail_source_columns(source)
    if not source_columns:
        raise PlanRefused("member_not_found")
    if view_name in meta.tables:
        return meta.tables[view_name]
    return sa.Table(
        view_name,
        meta,
        *[
            sa.Column(
                column,
                (
                    sa.Integer
                    if column in {"id", "entity_id", "department_id"} or column.endswith("_id")
                    else sa.String(255)
                ),
                primary_key=column == "id",
            )
            for column in sorted(source_columns)
        ],
        extend_existing=True,
    )


def operator_clause(column: Any, operator: str, values: list[Any]) -> sa.sql.ColumnElement[bool]:
    """Canonical operator to SQLAlchemy predicate compilation with safe autoescape."""
    op = operator.lower()
    if op == "eq":
        return column == values[0] if len(values) == 1 else sa.or_(*(column == v for v in values))
    if op == "neq":
        return column != values[0] if len(values) == 1 else sa.and_(*(column != v for v in values))
    if op == "in":
        return column.in_(values)
    if op == "not_in":
        return column.notin_(values)
    if op == "gt":
        return column > values[0]
    if op == "gte":
        return column >= values[0]
    if op == "lt":
        return column < values[0]
    if op == "lte":
        return column <= values[0]
    if op == "between":
        return column.between(values[0], values[1])
    if op == "is_null":
        return column.is_(None)
    if op == "not_null":
        return column.is_not(None)
    if op == "contains":
        return column.contains(str(values[0]), autoescape=True)
    if op == "like":
        val = str(values[0])
        escaped = val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return column.like(f"%{escaped}%", escape="\\")
    raise PlanRefused("unsupported_operator")


def lower_attribute_predicate(
    pred: AttributePredicate,
    parent_table: sa.Table,
    *,
    principal: Principal | None = None,
    metadata: sa.MetaData | None = None,
    bundle: DefinitionBundle | None = None,
) -> sa.sql.ColumnElement[bool]:
    """Lower an AttributePredicate to an owner-correlated EXISTS over the fact view.

    Target view is determined strictly from definition metadata (never caller-authored).
    Distinct parent grain is preserved without multiplying rows.
    """
    if (
        pred.revision_hash is not None
        and pred.definition_revision is not None
        and pred.revision_hash != pred.definition_revision
    ):
        raise PlanRefused("member_not_found")
    definition = resolve_detail_definition(pred.family_key, pred.revision_hash, bundle=bundle)
    if definition is None:
        raise PlanRefused("member_not_found")

    meta = metadata if metadata is not None else parent_table.metadata
    fact_table = _get_fact_table(
        definition.physical_source,
        meta,
        bundle=bundle,
        family_key=definition.family_key,
    )

    conditions: list[sa.sql.ColumnElement[bool]] = []

    # 1. Correlate parent owner ID
    parent_pk = parent_table.c.get("id")
    if parent_pk is None:
        parent_pk = parent_table.c.get(definition.owner_resource + "_id")
    if parent_pk is None and len(parent_table.primary_key.columns) == 1:
        # Resource bindings may use a non-``id`` primary key. The signed
        # detail definition owns the fact-side key; the parent table's
        # declared primary key supplies the other side of the correlation.
        parent_pk = next(iter(parent_table.primary_key.columns))

    fact_fk = fact_table.c.get(definition.owner_column)

    if parent_pk is None or fact_fk is None:
        raise PlanRefused("no_join_path")
    conditions.append(fact_fk == parent_pk)

    family_col = fact_table.c.get("family_key")
    if family_col is None:
        raise PlanRefused("member_not_found")
    conditions.append(family_col == definition.family_key)

    # 2. Value condition on value_column
    value_col = fact_table.c.get(definition.value_column)
    if value_col is None:
        raise PlanRefused("member_not_found")

    op = pred.operator
    values = pred.values

    if op in {"gt", "gte", "lt", "lte"} and definition.value_kind not in _NUMERIC_VALUE_KINDS:
        raise PlanRefused("unsupported_operator")

    conditions.append(operator_clause(value_col, op, values))

    # 3. Scope conditions
    if principal is not None:
        if not principal.cross_entity and principal.entity_id is not None:
            entity_col = fact_table.c.get(definition.scope_columns.entity)
            if entity_col is not None:
                conditions.append(entity_col == principal.entity_id)

        scope_values = principal.scope_values
        if scope_values and scope_values.department_id is not None:
            dept_col = fact_table.c.get(definition.scope_columns.department)
            if dept_col is not None:
                conditions.append(dept_col == scope_values.department_id)

    # 4. Revision hash constraint if specified
    if pred.revision_hash is not None:
        revision_col = fact_table.c.get("revision_hash")
        if revision_col is None:
            raise PlanRefused("member_not_found")
        conditions.append(revision_col == pred.revision_hash)

    # Build owner-correlated EXISTS subquery
    subquery = (
        sa.select(1).select_from(fact_table).where(sa.and_(*conditions)).correlate(parent_table)
    )
    return sa.exists(subquery)
