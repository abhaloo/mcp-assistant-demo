"""SQL-backed AuthorizedValueResolver over the semantic lookup views (D-S1-LOOKUP).

View, column and scope come from the bundle, never from a hardcoded map: a
resolvable dimension names its owning resource, and that resource's binding
carries the projection view, the scope columns, and the record predicates.
An exact rebind REPLACES the plan's contains filter, so the search must
reproduce the compiled query's population exactly — every forced predicate for
that resource, record predicates included. Searching wider is how an "exact"
rebind binds a value the query returns no rows for. Candidates never leave this
class except as the single exact canonical; nothing here logs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from app.business_query.plan.value_resolver.contract import (
    RESOLVER_CANDIDATE_CAP,
    ResolverLookup,
    ResolverResult,
)
from app.business_query.plan.value_resolver.matching import (
    lookup_tokens,
    token_match,
)
from app.models.schemas import DisambiguationCandidate

if TYPE_CHECKING:
    from app.business_query.authorize.scoping import ScopedPlan
    from app.business_query.definitions import DefinitionBundle


class SqlValueResolver:
    def __init__(
        self,
        engine: sa.engine.Engine,
        *,
        statement_timeout_seconds: float = 10.0,
        sql_recorder: Any | None = None,
        ledger_scope: str | None = None,
    ) -> None:
        self._engine = engine
        self._statement_timeout_seconds = statement_timeout_seconds
        self._view_columns: dict[str, set[str]] = {}
        self._sql_recorder = sql_recorder
        self._ledger_scope = ledger_scope

    def _apply_statement_timeout(
        self, conn: sa.engine.Connection, *, trace: object | None = None
    ) -> None:
        """Server-side statement kill, mirroring
        ``InternalCompilerAdapter._apply_statement_timeout``.
        """
        if self._engine.dialect.name in {"mysql", "mariadb"}:
            statement = sa.text(
                f"SET SESSION max_statement_time = {float(self._statement_timeout_seconds)}"
            )
            started = time.perf_counter()
            try:
                conn.exec_driver_sql(str(statement))
            except Exception:
                self._record_sql(statement, started, None, trace)
                raise
            self._record_sql(statement, started, None, trace)

    def resolve(
        self,
        lookup: ResolverLookup,
        scoped: ScopedPlan,
        bundle: DefinitionBundle,
        *,
        trace: object | None = None,
    ) -> ResolverResult:
        dimension = next((d for d in bundle.dimensions if d.name == lookup.member), None)
        if dimension is None or dimension.resolvable_as != lookup.value_type:
            raise ValueError("lookup member is not a resolvable dimension of this bundle")
        binding = next((r for r in bundle.resources if r.name == dimension.owning_resource), None)
        if binding is None:
            raise ValueError("lookup member has no resource binding")

        forced = [f for f in scoped.forced if f.resource == binding.name]
        principal = scoped.principal
        cross_entity = principal is not None and principal.cross_entity
        entity_column = binding.scope_columns.entity
        if not cross_entity and not any(f.column == entity_column for f in forced):
            raise ValueError("lookup scope unavailable")

        column = dimension.sql_expression.strip()
        id_col_name = binding.primary_key

        cols_needed = {column, id_col_name, *(f.column for f in forced)}

        table = sa.table(
            binding.projection_view,
            *(sa.column(name) for name in sorted(cols_needed)),
        )
        name_column = table.c[column]
        id_column = table.c[id_col_name]

        scope_predicates: list[sa.ColumnElement[bool]] = []
        for predicate in forced:
            target = table.c[predicate.column]
            scope_predicates.append(
                target == predicate.values[0]
                if predicate.operator == "eq"
                else target.in_(predicate.values)
            )

        cols = [id_column, name_column]

        def search(
            conn: sa.engine.Connection, name_conditions: list[sa.ColumnElement[bool]]
        ) -> list[tuple[Any, ...]]:
            stmt = (
                sa.select(*cols)
                .where(*name_conditions, *scope_predicates)
                .distinct()
                .limit(RESOLVER_CANDIDATE_CAP + 1)
            )
            started = time.perf_counter()
            try:
                result = [tuple(row) for row in conn.execute(stmt)]
            except Exception:
                self._record_sql(stmt, started, None, trace)
                raise
            self._record_sql(stmt, started, len(result), trace)
            return result

        with self._engine.connect() as conn:
            self._apply_statement_timeout(conn, trace=trace)
            values = search(conn, [name_column.contains(lookup.normalized_value, autoescape=True)])
            # Tokens are a FALLBACK, never a widening of a search that already
            # found something: matching them eagerly would turn working exact
            # binds into ambiguities. One token searches the same rows the
            # phrase just did, so only a multi-token value is worth a retry.
            if not values:
                tokens = lookup_tokens(lookup.normalized_value)
                # A single token searches exactly the rows the phrase just did.
                if len(tokens) > 1:
                    candidates = search(
                        conn, [name_column.contains(token, autoescape=True) for token in tokens]
                    )
                    # SQL can only ask for the tokens somewhere in the name. It
                    # cannot ask for them in order, nor for a number to be that
                    # number rather than the front of a longer one, so a hit
                    # here is a proposal and token_match is the decision.
                    values = [row for row in candidates if token_match(str(row[1]), tokens)]

        if not values:
            return ResolverResult(
                disposition="none", value_type=lookup.value_type, member=lookup.member
            )
        if len(values) == 1:
            canonical = str(values[0][1]) if len(values[0]) > 1 else str(values[0][0])
            return ResolverResult(
                disposition="exact",
                value_type=lookup.value_type,
                member=lookup.member,
                canonical_values=(canonical,),
                match_count=1,
            )

        match_count = min(len(values), RESOLVER_CANDIDATE_CAP)
        disambig_candidates: list[DisambiguationCandidate] = []
        if 2 <= len(values) <= 5:
            res_type = lookup.value_type
            for row in values:
                cand_id = str(row[0])[:32]
                cand_label = str(row[1])[:128]
                suggested_q = (
                    f"Show invoices for {cand_label} (ID: {cand_id})"
                    if cand_id != cand_label
                    else f"Show invoices for {cand_label}"
                )[:200]
                disambig_candidates.append(
                    DisambiguationCandidate(
                        id=cand_id,
                        resource_type=res_type,
                        label=cand_label,
                        suggested_query=suggested_q,
                    )
                )

        return ResolverResult(
            disposition="ambiguous",
            value_type=lookup.value_type,
            member=lookup.member,
            match_count=match_count,
            candidates=tuple(disambig_candidates),
        )

    def _record_sql(
        self,
        stmt: sa.sql.Select,
        started: float,
        rows: int | None,
        trace: object | None,
    ) -> None:
        """Write resolver SQL through the existing full-fidelity ledger seam."""
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            statement = str(
                stmt.compile(
                    dialect=self._engine.dialect,
                    compile_kwargs={"literal_binds": True},
                )
            )
        except Exception:  # noqa: BLE001
            statement = str(stmt.compile(dialect=self._engine.dialect))
        if self._sql_recorder is not None:
            self._sql_recorder(
                statement=statement,
                elapsed_ms=elapsed_ms,
                row_count=rows,
                receipt_query_id=getattr(trace, "answer_query_id", None),
                scope=self._ledger_scope,
                correlation_id=getattr(trace, "correlation_id", None),
            )
