"""Rows for the semantic SQL bundle: dimensions, measures, bucket sets, and detail families."""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.business_query.definitions.schema import BucketSetDefinition, DefinitionBundle
from app.coverage.model import (
    Binding,
    SurfaceRow,
    UnresolvedBinding,
)
from app.coverage.schema_inventory import SchemaInventory
from app.coverage.view_lineage import DIALECT, ViewLineage, resolve_projection_column

SemanticBundle = DefinitionBundle


def expression_columns(sql_expression: str) -> list[str]:
    """Extract table column identifiers from a SQL expression."""
    try:
        tree = sqlglot.parse_one(sql_expression, read=DIALECT)
    except ParseError:
        return []
    return sorted({c.name for c in tree.find_all(exp.Column)})


def _bind(
    source: str | None,
    columns: list[str],
    lineage: ViewLineage,
    inventory: SchemaInventory,
    absent_reason: str,
) -> list[Binding]:
    """Resolve the projection columns a bundle member reads.

    absent_reason names why the member has no projection source at all. A member
    keeps every base column it resolves; when none resolve it carries the reasons
    that stopped each column.
    """
    if not source:
        return [UnresolvedBinding(reason=absent_reason)]
    resolved: list[Binding] = []
    reasons: list[Binding] = []
    for column in columns:
        for binding in resolve_projection_column(source, column, lineage, inventory):
            target = reasons if isinstance(binding, UnresolvedBinding) else resolved
            if binding not in target:
                target.append(binding)
    if resolved:
        return resolved
    if reasons:
        return reasons
    return [UnresolvedBinding(reason=f"view_column_missing:{source}.?")]


def _bucket_set_columns(bucket_set: BucketSetDefinition) -> list[str]:
    """Collect the projection columns a bucket set reads."""
    columns = [bucket_set.dimension] if bucket_set.dimension else []
    columns += expression_columns(bucket_set.applies_filter_sql)
    for _label, predicate in bucket_set.bucket_predicates:
        columns += expression_columns(predicate)
    return sorted(set(columns))


def _sql_bundle_cube_rows(
    bundle: Any,
    lineage: ViewLineage,
    inventory: SchemaInventory,
) -> list[SurfaceRow]:
    """Build surface rows from a cube-structured bundle."""
    missing_source = "cube_source_table_missing"
    rows: list[SurfaceRow] = []
    for cube in bundle.cubes:
        source_table = getattr(cube, "source_table", "")
        view = source_table if source_table in inventory.views else None
        cube_perms = getattr(cube, "required_permissions", None) or [f"view {cube.name}"]

        for measure in getattr(cube, "measures", []):
            col = getattr(measure, "column", None)
            expr = getattr(measure, "sql_expression", col or "")
            cols = [col] if col else expression_columns(expr)
            rows.append(
                SurfaceRow(
                    surface="sql_bundle",
                    resource=cube.name,
                    field=measure.name,
                    member=f"measure:{cube.name}.{measure.name}",
                    view=view,
                    bindings=_bind(source_table, cols, lineage, inventory, missing_source),
                    permissions=list(getattr(measure, "required_permissions", cube_perms)),
                    evidence=f"bundle cube {cube.name}",
                )
            )

        for dim in getattr(cube, "dimensions", []):
            col = getattr(dim, "column", None)
            expr = getattr(dim, "sql_expression", col or "")
            cols = [col] if col else expression_columns(expr)
            rows.append(
                SurfaceRow(
                    surface="sql_bundle",
                    resource=cube.name,
                    field=dim.name,
                    member=f"dimension:{cube.name}.{dim.name}",
                    view=view,
                    bindings=_bind(source_table, cols, lineage, inventory, missing_source),
                    permissions=list(getattr(dim, "required_permissions", cube_perms)),
                    evidence=f"bundle cube {cube.name}",
                )
            )

        for bs in getattr(cube, "bucket_sets", []):
            col = getattr(bs, "column", getattr(bs, "dimension", ""))
            cols = [col] if col else []
            rows.append(
                SurfaceRow(
                    surface="sql_bundle",
                    resource=cube.name,
                    field=bs.name,
                    member=f"bucket_set:{cube.name}.{bs.name}",
                    view=view,
                    bindings=_bind(source_table, cols, lineage, inventory, missing_source),
                    permissions=list(getattr(bs, "required_permissions", cube_perms)),
                    evidence=f"bundle cube {cube.name}",
                )
            )

        for fam in getattr(cube, "detail_families", []):
            for col in getattr(fam, "columns", []):
                rows.append(
                    SurfaceRow(
                        surface="sql_bundle",
                        resource=cube.name,
                        field=col,
                        member=f"detail:{cube.name}.{fam.name}.{col}",
                        view=view,
                        bindings=_bind(source_table, [col], lineage, inventory, missing_source),
                        permissions=list(getattr(fam, "required_permissions", cube_perms)),
                        evidence=f"bundle cube {cube.name}",
                    )
                )
    return rows


def sql_bundle_rows(
    bundle: DefinitionBundle | Any,
    lineage: ViewLineage,
    inventory: SchemaInventory,
) -> list[SurfaceRow]:
    """Generate surface rows for all semantic bundle members."""
    if hasattr(bundle, "cubes"):
        return _sql_bundle_cube_rows(bundle, lineage, inventory)

    views = {r.name: r.projection_view for r in bundle.resources}
    perms = {c.resolves_to: c.required_permissions for c in bundle.capabilities}
    rows: list[SurfaceRow] = []

    for dim in bundle.dimensions:
        view = views.get(dim.owning_resource)
        field = dim.name.split(".", 1)[1] if "." in dim.name else dim.name
        rows.append(
            SurfaceRow(
                surface="sql_bundle",
                resource=dim.owning_resource,
                field=field,
                member=f"dimension:{dim.name}",
                view=view,
                bindings=_bind(
                    view,
                    expression_columns(dim.sql_expression),
                    lineage,
                    inventory,
                    f"resource_not_in_bundle:{dim.owning_resource}",
                ),
                permissions=list(perms.get(dim.name, [])),
                evidence=f"bundle dimension sql_expression={dim.sql_expression!r} over {view}",
            )
        )

    for m in bundle.measures:
        view = views.get(m.owning_resource)
        cols = expression_columns(m.sql_expression) + (
            expression_columns(m.filter_sql) if m.filter_sql else []
        )
        rows.append(
            SurfaceRow(
                surface="sql_bundle",
                resource=m.owning_resource,
                field=m.name,
                member=f"measure:{m.name}",
                view=view,
                bindings=_bind(
                    view,
                    sorted(set(cols)),
                    lineage,
                    inventory,
                    f"resource_not_in_bundle:{m.owning_resource}",
                ),
                permissions=list(perms.get(m.name, [])),
                evidence=(
                    f"bundle measure {m.agg_type}({m.sql_expression}) "
                    f"filter={m.filter_sql!r} over {view}"
                ),
            )
        )

    for bs in bundle.bucket_sets:
        view = views.get(bs.owning_resource)
        rows.append(
            SurfaceRow(
                surface="sql_bundle",
                resource=bs.owning_resource,
                field=bs.name,
                member=f"bucket_set:{bs.name}",
                view=view,
                bindings=_bind(
                    view,
                    _bucket_set_columns(bs),
                    lineage,
                    inventory,
                    f"resource_not_in_bundle:{bs.owning_resource}",
                ),
                permissions=list(perms.get(bs.name, [])),
                evidence=(
                    f"bundle bucket_set dimension={bs.dimension!r} "
                    f"filter={bs.applies_filter_sql!r} over {view}"
                ),
            )
        )

    for d in bundle.detail_definitions:
        rows.append(
            SurfaceRow(
                surface="sql_bundle",
                resource=d.owner_resource,
                field=d.family_key,
                member=f"family:{d.family_key}",
                view=d.physical_source,
                bindings=_bind(
                    d.physical_source,
                    [d.value_column],
                    lineage,
                    inventory,
                    f"detail_source_missing:{d.family_key}",
                ),
                permissions=list(d.required_permissions),
                evidence=(
                    f"bundle detail_definition physical_source={d.physical_source} "
                    f"value_column={d.value_column}"
                ),
            )
        )

    return rows
