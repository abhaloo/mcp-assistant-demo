"""Internal compiler adapter — plan→SQL over definition bundles.

Owns joins, date columns, statuses, and money math so the planner cannot.
Fan-out guard v1 = detect + refuse; never guess multiplied measures.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.sql import ColumnElement, Select, Selectable

from app.auth import Principal
from app.business_query.authorize.capability import allowed_filter_values_valid, visible_members
from app.business_query.authorize.preconditions import (
    check_business_date_requirement,
    check_member_visibility,
    check_native_currency_for_measure,
)
from app.business_query.authorize.scoping import (
    ForcedPredicate,
    ScopedDerivedSet,
    ScopeDenied,
    ScopedPlan,
)
from app.business_query.compile.adapter_execution import execute_internal_plan
from app.business_query.compile.bundle_expression import qualify_bundle_expression
from app.business_query.compile.compiler_context import CompilerContext
from app.business_query.compile.derived_sets import compile_set_relation
from app.business_query.compile.detail_engine import LOCAL_DETAIL_SOURCE_COLUMNS, DetailEngine
from app.business_query.compile.detail_predicates import operator_clause
from app.business_query.compile.dialect_time import age_in_days, overdue_filter_sql
from app.business_query.compile.filter_compiler import compile_filter_group
from app.business_query.compile.join_paths import (
    ResolvedJoin,
    join_adjacency,
    resolve_join_tree,
    shortest_join_path,
)
from app.business_query.compile.period_comparison import select_for_plan
from app.business_query.compile.schema_reflection import build_metadata, table_for
from app.business_query.compile.statement_compiler import StatementCompiler
from app.business_query.definitions import (
    BucketSetDefinition,
    DefinitionBundle,
    DimensionDefinition,
    JoinDefinition,
    MeasureDefinition,
    detail_source_columns,
    resolve_member,
)
from app.business_query.outcomes import (
    Answered,
    Denied,
    Incomplete,
    PlanRefused,
    RecordRef,
)
from app.business_query.plan import (
    BusinessQueryPlan,
    FilterGroup,
    FilterOperator,
    PlanFilter,
    SetOperator,
)
from app.business_query.seal.evidence import (
    UnsealedAdapterAnswer,
    seal_adapter_result,
)
from app.business_query.seal.receipts import (
    split_record_refs as _split_record_refs_fn,
)
from app.business_query.wire.trace import QueryTrace
from app.telemetry.invocation_ledger import get_ledger_scope, record_sql_execution

logger = logging.getLogger(__name__)

_DENY_MESSAGE = "business query tools are currently unavailable"

_AGGREGATE_CALL_RE = re.compile(
    r"^(?P<fn>[A-Za-z_]+)\s*\(\s*(?P<distinct>DISTINCT\s+)?(?P<inner>.*?)\s*\)$",
    re.IGNORECASE,
)


def compile_measure_filter_into_aggregate(sql_expression: str, filter_sql: str) -> str:
    """Scope a measure's filter_sql to ITS OWN aggregate instead of the shared WHERE.

    COUNT(*) -> COUNT(CASE WHEN (filter) THEN 1 END)
    COUNT(DISTINCT x) -> COUNT(DISTINCT CASE WHEN (filter) THEN x END)
    FN(inner) -> FN(CASE WHEN (filter) THEN (inner) END)   # covers COUNT(col) too
    """
    match = _AGGREGATE_CALL_RE.match(sql_expression.strip())
    if match is None:
        raise PlanRefused("measure_filter_unsupported")
    fn = match.group("fn").upper()
    inner = match.group("inner").strip()
    distinct = match.group("distinct") is not None
    if fn == "COUNT" and not distinct and inner == "*":
        return f"COUNT(CASE WHEN ({filter_sql}) THEN 1 END)"
    if distinct:
        return f"{fn}(DISTINCT CASE WHEN ({filter_sql}) THEN {inner} END)"
    return f"{fn}(CASE WHEN ({filter_sql}) THEN ({inner}) END)"


class InternalCompilerAdapter:
    """Compile a ScopedPlan to SQLAlchemy Core and execute with a receipt."""

    def __init__(
        self,
        principal: Principal,
        engine: Engine,
        bundle: DefinitionBundle,
        *,
        statement_timeout_seconds: float = 10.0,
        trace: QueryTrace | None = None,
        database_identity: str | None = None,
    ) -> None:
        self._principal = principal
        self._engine = engine
        self._dialect = engine.dialect
        self._bundle = bundle
        self._trace = trace
        self._resources = {r.name: r for r in bundle.resources}
        self._measures = {m.name: m for m in bundle.measures}
        self._dimensions = {d.name: d for d in bundle.dimensions}
        self._capabilities = {c.name: c for c in bundle.capabilities}
        self._joins = list(bundle.joins)
        self._statement_timeout_seconds = statement_timeout_seconds
        self._database_identity = database_identity
        self._metadata = build_metadata(bundle)

        source_contracts = dict(LOCAL_DETAIL_SOURCE_COLUMNS)
        for s in bundle.detail_sources:
            cols = detail_source_columns(s)
            if cols:
                source_contracts.setdefault(s.projection_view, cols)
        # Captured HERE (construction always runs on the loop thread, before
        # any executor dispatch) because `_record_sql` runs on a
        # `ThreadPoolExecutor` worker thread via module.py's
        # `loop.run_in_executor` -- `ContextVar.get()` does NOT propagate
        # into that thread (unlike `asyncio.to_thread`), so reading
        # `get_ledger_scope()` there would silently always see "prod",
        # even for an eval run. Capture once, pass explicitly.
        self._ledger_scope = get_ledger_scope()
        self._detail_adapter = DetailEngine(
            engine,
            bundle=bundle,
            principal=principal,
            source_contracts=source_contracts,
            sql_recorder=record_sql_execution,
            ledger_scope=self._ledger_scope,
        )
        self._compiler_context = CompilerContext(
            bundle=bundle,
            metadata=self._metadata,
            resources=self._resources,
            dimensions=self._dimensions,
            measures=self._measures,
            capabilities=self._capabilities,
            joins=self._joins,
            table_for=self._table_for,
            resolve_capability=self._resolve_capability,
            forced_predicates=self._forced_predicates,
            measure_expr=self._measure_expr,
            dim_expr=self._dimension_expr,
            due_date_column=self._due_date_column,
            dialect_name=self._dialect.name,
        )
        self._statement_compiler = StatementCompiler(
            lambda scoped: select_for_plan(self, scoped),
            build_set=lambda derived: compile_set_relation(self, derived),
        )

    @property
    def dialect_name(self) -> str:
        return self._dialect.name

    def _writer(self, trace: QueryTrace | None = None) -> QueryTrace | None:
        return trace if trace is not None else self._trace

    def _record_sql(
        self,
        stmt: Selectable,
        started: float,
        rows: int | None,
        trace: QueryTrace | None = None,
        *,
        receipt_query_id: str | None = None,
    ) -> None:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        full_statement = self._compile_with_values(stmt)
        writer = self._writer(trace)
        if writer is not None:
            writer.record_sql(
                str(stmt.compile(dialect=self._dialect)),
                elapsed_ms=elapsed_ms,
                rows=rows,
                full_statement=full_statement,
            )
        record_sql_execution(
            statement=full_statement,
            elapsed_ms=elapsed_ms,
            row_count=rows,
            scope=self._ledger_scope,
            correlation_id=writer.correlation_id if writer is not None else None,
            receipt_query_id=receipt_query_id,
        )

    def _compile_with_values(self, stmt: Selectable) -> str:
        try:
            return str(stmt.compile(dialect=self._dialect, compile_kwargs={"literal_binds": True}))
        except Exception:  # noqa: BLE001 â€” some param types cannot literal-render
            return str(stmt.compile(dialect=self._dialect))

    def _table_for(self, resource_name: str) -> sa.Table:
        return table_for(resource_name, self._resources, self._metadata)

    def _resolve_capability(self, name: str) -> tuple[str, Any]:
        resolved = resolve_member(self._bundle, name)
        if resolved is None:
            raise PlanRefused("member_not_found")
        return resolved

    def _assert_authorized(self, scoped: ScopedPlan) -> None:
        if scoped.principal is not None and scoped.principal != self._principal:
            raise ScopeDenied(_DENY_MESSAGE)
        if scoped.bundle_hash is not None and scoped.bundle_hash != self._bundle.content_hash:
            raise ScopeDenied(_DENY_MESSAGE)
        visibility_violation = check_member_visibility(scoped.plan, self._principal, self._bundle)
        if visibility_violation is not None:
            # Unknown-to-bundle vs unauthorized: unknown â†’ PlanRefused; else ScopeDenied.
            if visibility_violation.kind == "unknown":
                raise PlanRefused("member_not_found")
            raise ScopeDenied(_DENY_MESSAGE)
        if not allowed_filter_values_valid(scoped.plan, self._bundle):
            raise PlanRefused("grain_unexpressible", check_site="filter_value_allowlist")
        if (
            check_business_date_requirement(scoped.plan, self._bundle, scoped.business_date)
            is not None
        ):
            raise PlanRefused("grain_unexpressible", check_site="business_date_required")
        allowed = visible_members(self._principal, self._bundle)
        self._assert_native_currency(scoped.plan, allowed)

    def _assert_native_currency(self, plan: BusinessQueryPlan, allowed: set[str]) -> None:
        for member in plan.measures:
            entry = self._capabilities.get(member)
            measure = self._measures.get(entry.resolves_to) if entry is not None else None
            if measure is None:
                continue
            if measure.currency_dimension is None and measure.format != "currency":
                continue
            violation = check_native_currency_for_measure(
                measure_member=member,
                currency_dimension=measure.currency_dimension,
                plan=plan,
                allowed=allowed,
                bundle=self._bundle,
            )
            if violation is None:
                continue
            if violation.kind == "not_declared":
                raise PlanRefused("capability_disabled")
            if violation.kind == "alias_not_visible":
                raise ScopeDenied(_DENY_MESSAGE)
            raise PlanRefused("grain_unexpressible", check_site="native_currency_unconstrained")

    def _join_adjacency(self) -> dict[str, list[JoinDefinition]]:
        return join_adjacency(self._joins)

    def _shortest_join_path(self, start: str, goal: str) -> list[ResolvedJoin] | None:
        return shortest_join_path(self._joins, start, goal)

    def _resolve_join_tree(self, resources: tuple[str, ...]) -> list[ResolvedJoin]:
        return resolve_join_tree(self._joins, resources)

    def _measure_expr(
        self,
        measure: MeasureDefinition,
        table: sa.Table,
        label: str,
        *,
        business_date: date | None = None,
    ) -> ColumnElement[Any]:
        # Bundle sql_expression already includes the aggregate (COUNT/SUM/â€¦).
        expression = measure.sql_expression
        filter_sql = measure.filter_sql
        overdue = self._overdue_filter_sql(measure, business_date, table)
        if overdue:
            filter_sql = f"({filter_sql}) AND ({overdue})" if filter_sql else overdue
        if filter_sql:
            expression = compile_measure_filter_into_aggregate(expression, filter_sql)
        qualified = qualify_bundle_expression(expression, table)
        return sa.literal_column(qualified).label(label)

    def _overdue_column_name(self, measure: MeasureDefinition, table: sa.Table) -> str:
        if measure.time_dimension:
            dimension = self._dimensions.get(measure.time_dimension)
            if dimension is not None:
                column_name = dimension.sql_expression.strip()
                if column_name in table.c:
                    return column_name
        return self._due_date_column(table, measure.owning_resource).name

    def _overdue_filter_sql(
        self,
        measure: MeasureDefinition,
        business_date: date | None,
        table: sa.Table,
    ) -> str | None:
        if not measure.relative_to_business_date or measure.overdue_after_days is None:
            return None
        if business_date is None:
            raise PlanRefused("grain_unexpressible", check_site="business_date_required")
        column_name = self._overdue_column_name(measure, table)
        return overdue_filter_sql(
            column_name=column_name,
            anchor=business_date,
            after_days=measure.overdue_after_days,
            dialect_name=self.dialect_name,
        )

    def _age_in_days(self, due_date_col: ColumnElement[Any], anchor: date) -> ColumnElement[Any]:
        return age_in_days(due_date_col, anchor, self.dialect_name)

    def _due_date_column(self, table: sa.Table, owning_resource: str) -> ColumnElement[Any]:
        defined = [
            dimension.sql_expression.strip()
            for dimension in self._dimensions.values()
            if dimension.owning_resource == owning_resource
            and dimension.sql_expression.strip() in table.c
            and dimension.sql_expression.strip() != "days_past_due"
        ]
        if not defined:
            raise PlanRefused("grain_unexpressible", check_site="business_date_required")
        column_name = "due_date" if "due_date" in defined else defined[0]
        return table.c[column_name]

    def _dimension_expr(
        self,
        dimension: DimensionDefinition,
        table: sa.Table,
        label: str,
        *,
        business_date: date | None,
    ) -> ColumnElement[Any]:
        col = dimension.sql_expression.strip()
        if col == "days_past_due":
            if business_date is None:
                raise PlanRefused("grain_unexpressible", check_site="business_date_required")
            return self._age_in_days(
                self._due_date_column(table, dimension.owning_resource), business_date
            ).label(label)
        if col in table.c:
            return table.c[col].label(label)
        qualified = qualify_bundle_expression(col, table)
        return sa.literal_column(qualified).label(label)

    def _bucket_case(
        self,
        bucket: BucketSetDefinition,
        table: sa.Table,
        *,
        label: str,
        business_date: date | None,
    ) -> ColumnElement[Any]:
        if bucket.bucket_predicates:
            # Categorical: one bucket per predicate, qualified the same way as
            # applies_filter_sql. Rows matching no predicate fall out as NULL.
            predicate_whens: list[tuple[ColumnElement[bool], str]] = [
                (sa.text(qualify_bundle_expression(predicate, table)), bucket_label)
                for bucket_label, predicate in bucket.bucket_predicates
            ]
            return sa.case(*predicate_whens).label(label)
        dim_col = table.c[bucket.dimension.strip()]
        if bucket.relative_to_business_date:
            if business_date is None:
                raise PlanRefused("grain_unexpressible", check_site="bucket_missing_business_date")
            dim_col = self._age_in_days(dim_col, business_date)
        whens: list[tuple[ColumnElement[bool], str]] = []
        prev_bound: int | None = None
        for bucket_label, bound in bucket.buckets:
            if bound is None:
                # Catch-all â€” must not match NULL (applies_filter already excludes NULL).
                whens.append((dim_col.is_not(None), bucket_label))
            elif prev_bound is None:
                whens.append((dim_col <= bound, bucket_label))
            else:
                whens.append((sa.and_(dim_col > prev_bound, dim_col <= bound), bucket_label))
            prev_bound = bound
        return sa.case(*whens).label(label)

    def _forced_predicates(
        self, forced: tuple[ForcedPredicate, ...], tables: dict[str, sa.Table]
    ) -> list[ColumnElement[bool]]:
        preds: list[ColumnElement[bool]] = []
        for pred in forced:
            table = tables[pred.resource]
            column = table.c[pred.column]
            if pred.operator == "eq":
                if len(pred.values) != 1:
                    raise ScopeDenied(_DENY_MESSAGE)
                preds.append(column == pred.values[0])
            elif pred.operator == "in":
                preds.append(column.in_(list(pred.values)))
            else:
                raise ScopeDenied(_DENY_MESSAGE)
        return preds

    def _filter_sql_predicate(
        self, filter_sql: str | None, table: sa.Table
    ) -> ColumnElement[bool] | None:
        if not filter_sql:
            return None
        qualified = qualify_bundle_expression(filter_sql, table)
        return sa.text(qualified)

    def _operator_clause(
        self, column: ColumnElement[Any], operator: FilterOperator | SetOperator, values: list[Any]
    ) -> ColumnElement[bool]:
        # `filter_compiler.walk()`'s `kind == "measure"` branch (unlike its
        # `kind == "dimension"` branch) does not intercept in_set/not_in_set
        # before calling `operator_clause` -- a measure-targeted set filter
        # reaches here structurally, so the annotation must admit SetOperator
        # too. `operator_clause` itself fails closed with PlanRefused
        # (unsupported_operator) for both; see
        # test_measure_targeted_set_operator_refuses_closed.
        return operator_clause(column, operator, values)

    @staticmethod
    def _validate_filter_arity(filter_: PlanFilter) -> None:
        if filter_.operator in {"is_null", "not_null"}:
            if filter_.values:
                raise PlanRefused("grain_unexpressible", check_site="filter_arity")
        elif not filter_.values:
            raise PlanRefused("grain_unexpressible", check_site="filter_arity")

    def _compile_filter_group(
        self,
        group: FilterGroup | None,
        *,
        allow_measures: bool,
        measure_labels: dict[str, ColumnElement[Any]],
        dim_exprs: dict[str, ColumnElement[Any]],
        tables: dict[str, sa.Table],
        business_date: date | None,
        root_resource: str | None = None,
        isolated_resources: frozenset[str] = frozenset(),
        isolated_forced: tuple[ForcedPredicate, ...] = (),
        derived_sets: dict[str, ScopedDerivedSet] | None = None,
        compile_set: Callable[[ScopedDerivedSet], Select[Any]] | None = None,
    ) -> ColumnElement[bool] | None:
        return compile_filter_group(
            group,
            allow_measures=allow_measures,
            measure_labels=measure_labels,
            dim_exprs=dim_exprs,
            tables=tables,
            business_date=business_date,
            bundle=self._bundle,
            metadata=self._metadata,
            principal=self._principal,
            resources=self._resources,
            joins=self._bundle.joins,
            dimension_expr_fn=self._dimension_expr,
            root_resource=root_resource,
            isolated_resources=isolated_resources,
            isolated_forced=isolated_forced,
            derived_sets=derived_sets,
            compile_set=(
                compile_set if compile_set is not None else self._statement_compiler.compile_set
            ),
        )

    def compile_sql(self, scoped: ScopedPlan) -> str:
        """Render the compiled SELECT as SQL text (declaration-identity tests)."""
        stmt = self._build_select(scoped)
        return str(
            stmt.compile(
                dialect=self._dialect,
                compile_kwargs={"literal_binds": False},
            )
        )

    def _build_select(self, scoped: ScopedPlan) -> Selectable:
        """Delegates to statement_builder.build_select -- kept as a bound method
        (not removed like the other eleven moved functions) because it is the one
        member of this cluster called from OUTSIDE the cluster: `compile_sql` and
        `_execute` below call `self._build_select(scoped)`,
        `app/experiments/bq_query_performance/mariadb_baseline.py::_explain_plan` calls
        `adapter._build_select(scoped)` directly (delegates to StatementCompiler), and
        `tests/business_query/test_internal_adapter.py` monkeypatches
        `adapter._build_select` at the instance level -- all three need a genuine
        bound method on the instance, not a bare module-level function."""
        return self._statement_compiler.compile(scoped)

    @staticmethod
    def _split_record_refs(rows: list[dict[str, Any]]) -> tuple[RecordRef, ...]:
        return _split_record_refs_fn(rows)

    def execute(
        self, scoped: ScopedPlan, *, trace: QueryTrace | None = None
    ) -> Answered | Incomplete | Denied:
        """Execute a ScopedPlan; never accepts a bare BusinessQueryPlan."""
        return seal_adapter_result(
            self._execute(scoped, trace=trace),
            scoped,
            principal=self._principal,
            bundle=self._bundle,
        )

    def execute_with_evidence(
        self, scoped: ScopedPlan, *, trace: QueryTrace | None = None
    ) -> UnsealedAdapterAnswer | Incomplete | Denied:
        """Execute and return the factual payload needed for durable sealing."""
        return self._execute(scoped, trace=trace)

    def _execute(
        self, scoped: ScopedPlan, *, trace: QueryTrace | None = None
    ) -> UnsealedAdapterAnswer | Incomplete | Denied:
        return execute_internal_plan(
            scoped,
            engine=self._engine,
            dialect=self._dialect,
            dialect_name=self.dialect_name,
            bundle=self._bundle,
            principal=self._principal,
            database_identity=self._database_identity,
            detail_adapter=self._detail_adapter,
            build_select_fn=self._build_select,
            record_sql_fn=self._record_sql,
            trace=trace,
            writer_fn=self._writer,
        )
