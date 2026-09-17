"""Filter compilation — lowers FilterGroup nodes into SQLAlchemy boolean expressions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement, Select

from app.auth import Principal
from app.business_query.authorize.scoping import ForcedPredicate, ScopedDerivedSet
from app.business_query.compile.bundle_expression import qualify_bundle_expression
from app.business_query.compile.derived_sets import membership_clause
from app.business_query.compile.detail_predicates import lower_attribute_predicate, operator_clause
from app.business_query.compile.statement_builder import parse_on_sql
from app.business_query.definitions import (
    DefinitionBundle,
    DimensionDefinition,
    JoinDefinition,
    MeasureDefinition,
    MemberDefinition,
    ResourceBinding,
    SegmentDefinition,
    resolve_member,
)
from app.business_query.definitions.allowed_values import canonical_allowed_values
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import AttributePredicate, FilterGroup, PlanFilter
from app.business_query.plan.detail_family import resolve_detail_definition
from app.business_query.ports import DimensionExprFn

_DENY_MESSAGE = "business query tools are currently unavailable"


def validate_filter_arity(filter_: PlanFilter) -> None:
    if filter_.operator in {"is_null", "not_null"}:
        if filter_.values:
            raise PlanRefused("grain_unexpressible", check_site="filter_arity")
    elif not filter_.values:
        raise PlanRefused("grain_unexpressible", check_site="filter_arity")


def compile_filter_group(
    group: FilterGroup | None,
    *,
    allow_measures: bool,
    measure_labels: dict[str, ColumnElement[Any]],
    dim_exprs: dict[str, ColumnElement[Any]],
    tables: dict[str, sa.Table],
    business_date: date | None,
    bundle: DefinitionBundle,
    metadata: sa.MetaData,
    principal: Principal,
    resources: dict[str, ResourceBinding],
    joins: list[JoinDefinition],
    dimension_expr_fn: DimensionExprFn,
    root_resource: str | None = None,
    isolated_resources: frozenset[str] = frozenset(),
    isolated_forced: tuple[ForcedPredicate, ...] = (),
    derived_sets: dict[str, ScopedDerivedSet] | None = None,
    compile_set: Callable[[ScopedDerivedSet], Select[Any]] | None = None,
) -> ColumnElement[bool] | None:
    if group is None:
        return None

    def resolve_capability(member: str) -> tuple[str, MemberDefinition]:
        resolved = resolve_member(bundle, member)
        if resolved is None:
            raise PlanRefused("member_not_found")
        return resolved

    def walk(node: PlanFilter | AttributePredicate | FilterGroup) -> ColumnElement[bool]:
        if isinstance(node, AttributePredicate):
            defn = resolve_detail_definition(node.family_key, node.revision_hash, bundle=bundle)
            if defn is None:
                raise PlanRefused("member_not_found")
            table = tables.get(defn.owner_resource)
            if table is None:
                raise PlanRefused("no_join_path")
            return lower_attribute_predicate(
                node,
                table,
                principal=principal,
                metadata=metadata,
                bundle=bundle,
            )

        if isinstance(node, PlanFilter):
            validate_filter_arity(node)
            kind, definition = resolve_capability(node.member)
            if kind == "segment":
                assert isinstance(definition, SegmentDefinition)
                if allow_measures:
                    # A segment names row columns; a grouped HAVING only sees aggregates.
                    raise PlanRefused("grain_unexpressible", check_site="segment_in_having")
                if (
                    node.operator != "eq"
                    or node.values != [True]
                    or not isinstance(node.values[0], bool)
                ):
                    raise PlanRefused("grain_unexpressible", check_site="segment_operator")
                table = tables.get(definition.owning_resource)
                if table is None:
                    raise PlanRefused("no_join_path")
                return sa.text(qualify_bundle_expression(definition.predicate_sql, table))
            if kind == "measure":
                assert isinstance(definition, MeasureDefinition)
                if not allow_measures:
                    raise PlanRefused("grain_unexpressible", check_site="measure_filter_disallowed")
                if definition.expression_kind == "share":
                    raise PlanRefused("grain_unexpressible", check_site="share_in_having")
                column = measure_labels.get(node.member)
                if column is None:
                    raise PlanRefused("member_not_found")
                return operator_clause(column, node.operator, node.values)
            if kind == "dimension":
                assert isinstance(definition, DimensionDefinition)
                if node.operator in {"in_set", "not_in_set"}:
                    if allow_measures:
                        raise PlanRefused("grain_unexpressible", check_site="set_filter_in_having")
                    binding = resources[definition.owning_resource]
                    reserved = {
                        c
                        for c in (
                            binding.scope_columns.entity,
                            binding.scope_columns.department,
                        )
                        if c is not None
                    } | set(binding.record_predicates)
                    if definition.sql_expression.strip() in reserved:
                        from app.business_query.authorize.scoping import ScopeDenied

                        raise ScopeDenied(_DENY_MESSAGE)
                    if not node.values or len(node.values) != 1:
                        raise PlanRefused("grain_unexpressible", check_site="filter_arity")
                    set_id = node.values[0]
                    if derived_sets is None or set_id not in derived_sets or compile_set is None:
                        raise PlanRefused("member_not_found")
                    derived = derived_sets[set_id]
                    relation = compile_set(derived)
                    if derived.key not in relation.selected_columns.keys():
                        raise AssertionError(
                            f"derived set key '{derived.key}' missing from compiled "
                            "selected columns"
                        )
                    column = dim_exprs.get(node.member)
                    if column is None:
                        table = tables[definition.owning_resource]
                        column = dimension_expr_fn(
                            definition, table, node.member, business_date=business_date
                        )
                    return membership_clause(
                        column,
                        relation,
                        key=derived.key,
                        set_id=derived.id,
                        exclude=node.operator == "not_in_set",
                    )
                values = canonical_allowed_values(definition, node.values)
                if values is None:
                    raise PlanRefused("grain_unexpressible", check_site="dimension_allowed_values")
                binding = resources[definition.owning_resource]
                reserved = {
                    c
                    for c in (
                        binding.scope_columns.entity,
                        binding.scope_columns.department,
                    )
                    if c is not None
                } | set(binding.record_predicates)
                if definition.sql_expression.strip() in reserved:
                    from app.business_query.authorize.scoping import ScopeDenied

                    raise ScopeDenied(_DENY_MESSAGE)
                column = dim_exprs.get(node.member)
                if column is None:
                    table = tables[definition.owning_resource]
                    column = dimension_expr_fn(
                        definition, table, node.member, business_date=business_date
                    )
                parent_child_join = next(
                    (
                        j
                        for j in joins
                        if (
                            j.from_resource == root_resource
                            and j.to_resource == definition.owning_resource
                        )
                        or (
                            j.to_resource == root_resource
                            and j.from_resource == definition.owning_resource
                        )
                    ),
                    None,
                )
                if (
                    definition.owning_resource in isolated_resources
                    and root_resource is not None
                    and definition.owning_resource != root_resource
                    and parent_child_join is not None
                ):
                    table = tables[definition.owning_resource]
                    op_clause = operator_clause(column, node.operator, values)
                    join_cond = parse_on_sql(parent_child_join, tables)
                    item_preds = [
                        join_cond,
                        op_clause,
                    ]
                    for pred in isolated_forced:
                        if pred.resource == definition.owning_resource:
                            forced_col = table.c[pred.column]
                            if pred.operator == "eq":
                                item_preds.append(forced_col == pred.values[0])
                            elif pred.operator == "in":
                                item_preds.append(forced_col.in_(list(pred.values)))
                    for k, v in binding.record_predicates.items():
                        item_preds.append(table.c[k] == v)
                    return (
                        sa.select(sa.literal(1))
                        .select_from(table)
                        .where(sa.and_(*item_preds))
                        .exists()
                    )
                return operator_clause(column, node.operator, values)
            raise PlanRefused("member_not_found")

        all_clauses = [walk(child) for child in node.all]
        any_clauses = [walk(child) for child in node.any]
        if all_clauses and any_clauses:
            return sa.and_(sa.and_(*all_clauses), sa.or_(*any_clauses))
        if all_clauses:
            return sa.and_(*all_clauses)
        if any_clauses:
            return sa.or_(*any_clauses)
        return sa.true()

    return walk(group)
