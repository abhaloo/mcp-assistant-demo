"""Generate the Cube model from the vendored business definition bundle.

The bundle stays the source of meaning; this file only translates. Scope is
compiled into each cube's relation so joins see already-scoped rows: entity
scope and record predicates in the cube SQL, department scope through the
runtime's query rewrite (deploy/cube/cube.py)."""

from __future__ import annotations

import re

from app.business_query.cube.model import (
    CubeDef,
    CubeDimension,
    CubeJoin,
    CubeMeasure,
    CubeModel,
    revision_for,
)
from app.business_query.definitions import (
    DefinitionBundle,
    JoinDefinition,
    MeasureDefinition,
    ResourceBinding,
)

GENERATOR_VERSION = "1"
VIEW_NAME = "business"
# The bundle names the aggregate twice: `agg_type` and the outer function of `sql_expression`.
# The argument may be an expression (`SUM(credit - debit)`), so it is captured whole.
_AGGREGATE = re.compile(r"^(COUNT|SUM|AVG|MIN|MAX)\((.+)\)$", re.DOTALL)
_ENTITY_SCOPE = "{{ COMPILE_CONTEXT.securityContext.entity_id }}"
# Cube prefixes a measure or dimension `sql` only when it is one bare identifier; every
# other fragment goes into the statement as written, where a shared column name is
# ambiguous once cubes are joined. The generator qualifies known columns itself.
_SQL_WORDS = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "IN",
        "IS",
        "NULL",
        "LIKE",
        "BETWEEN",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "TRUE",
        "FALSE",
        "DISTINCT",
        "AS",
        "COALESCE",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "COUNT",
    }
)
_TOKEN = re.compile(r"'(?:[^']|'')*'|\b[A-Za-z_][A-Za-z0-9_]*\b")


def model_revision(bundle: DefinitionBundle) -> str:
    return revision_for(bundle.content_hash, GENERATOR_VERSION)


def generate_cube_model(bundle: DefinitionBundle) -> CubeModel:
    cubes = tuple(
        _cube_for(resource, bundle) for resource in sorted(bundle.resources, key=lambda r: r.name)
    )
    return CubeModel(
        cubes=cubes,
        view_name=VIEW_NAME,
        bundle_hash=bundle.content_hash,
        generator_version=GENERATOR_VERSION,
    )


def _cube_for(resource: ResourceBinding, bundle: DefinitionBundle) -> CubeDef:
    if resource.department_scope_mode not in {"none", "filter_when_present"}:
        # Only the mode the runtime rewrite implements may be generated; anything else
        # would compile a cube the rewrite cannot scope.
        raise ValueError(f"unsupported department scope mode on {resource.name}")
    dimensions = [
        CubeDimension(
            name=d.name.split(".", 1)[1],
            sql=d.sql_expression,
            type=d.type,
            primary_key=d.is_primary_key,
        )
        for d in bundle.dimensions
        # A dotless dimension is a legacy alias of a `resource.name` member on the same
        # resource; generating it would give two view members one name.
        if d.owning_resource == resource.name and "." in d.name
    ]
    department = (
        resource.scope_columns.department if resource.department_scope_mode != "none" else None
    )
    if department is not None and all(d.name != department for d in dimensions):
        # The rewrite filters on this member, so the cube must expose it.
        dimensions.append(CubeDimension(name=department, sql=department, type="number"))
    # Qualify by physical column, not member name: five bundle dimensions alias a column
    # (job.product_title → title, job.ordered_quantity → ordered_qty, ...).
    columns = {d.sql for d in dimensions} | {resource.primary_key} | set(resource.record_predicates)
    if resource.scope_columns.entity:
        columns.add(resource.scope_columns.entity)
    return CubeDef(
        name=resource.name,
        sql=_relation_sql(resource),
        dimensions=tuple(dimensions),
        measures=tuple(
            _measure_for(m, columns)
            for m in bundle.measures
            if m.owning_resource == resource.name and m.agg_type != "derived"
        ),
        joins=_joins_for(resource, bundle),
        relation_columns=tuple(
            sorted(set(resource.record_predicates) | ({resource.scope_columns.entity} - {None}))
        ),
        department_column=department,
    )


def _literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + value.replace("'", "''") + "'"


def _relation_sql(resource: ResourceBinding) -> str:
    predicates = [
        f"{column} = {_literal(value)}"
        for column, value in sorted(resource.record_predicates.items())
    ]
    entity_column = resource.scope_columns.entity
    if entity_column is not None:
        # Rendered once per compiled tenant; a cross-entity context compiles with no predicate.
        predicates.append(
            "{% if COMPILE_CONTEXT.securityContext.cross_entity %}1 = 1{% else %}"
            f"{entity_column} = {_ENTITY_SCOPE}"
            "{% endif %}"
        )
    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    # projection_view is a signed bundle identifier, not request input.
    return " ".join(("SELECT", "*", "FROM", resource.projection_view)) + where


def _qualify(sql: str, columns: set[str]) -> str:
    """Prefix every known column with {CUBE}; leave strings, keywords and unknown names."""

    def swap(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("'") or token.upper() in _SQL_WORDS or token not in columns:
            return token
        return "{CUBE}." + token

    return _TOKEN.sub(swap, sql)


def _measure_for(measure: MeasureDefinition, columns: set[str]) -> CubeMeasure:
    match = _AGGREGATE.match(measure.sql_expression.strip())
    if match is None or match.group(1).lower() != measure.agg_type:
        raise ValueError(f"measure {measure.name}: sql_expression does not match agg_type")
    argument = match.group(2).strip()
    # A bare argument that is not a bundle dimension (cash_received) stays bare: Cube
    # prefixes a lone identifier itself. Known columns are qualified for expressions.
    return CubeMeasure(
        name=measure.name,
        type=measure.agg_type,
        sql=_qualify(argument, columns) if argument in columns or " " in argument else argument,
        filters=(_qualify(measure.filter_sql, columns),) if measure.filter_sql else (),
    )


def _joins_for(resource: ResourceBinding, bundle: DefinitionBundle) -> tuple[CubeJoin, ...]:
    # The child declares every join toward its parent, so child rows without a parent
    # survive a lookup. Two facts under one parent are joined by the view (multi-fact),
    # not by a join declared on the parent.
    joins = [
        CubeJoin(name=join.from_resource, relationship="many_to_one", sql=_child_join_sql(join))
        for join in bundle.joins
        if join.to_resource == resource.name
    ]
    return tuple(sorted(joins, key=lambda j: j.name))


def _child_join_sql(join: JoinDefinition) -> str:
    parent_column, child_column = (part.strip() for part in join.on_sql.split("=", 1))
    return f"{{CUBE}}.{child_column} = {{{join.from_resource}}}.{parent_column}"
