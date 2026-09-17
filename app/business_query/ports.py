"""Business-facing ports whose implementations are supplied by composition."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import date, datetime

    import sqlalchemy as sa
    from sqlalchemy.sql import ColumnElement, Select

    from app.business_query.authorize.forced_predicate import ForcedPredicate
    from app.business_query.authorize.scoping import ScopedDerivedSet, ScopedPlan
    from app.business_query.case_repeats import CaseRepeatStart, CaseRepeatTerminal
    from app.business_query.compile.join_paths import ResolvedJoin
    from app.business_query.compile.pagination.plan_store import StoredPlan
    from app.business_query.definitions import (
        BucketSetDefinition,
        DefinitionBundle,
        DimensionDefinition,
        MeasureDefinition,
    )
    from app.business_query.outcomes import (
        Answered,
        BusinessQueryOutcome,
        ClarificationRequired,
        Denied,
        Incomplete,
        ResultColumn,
        Unsupported,
    )
    from app.business_query.plan import BusinessQueryPlan, FilterGroup, PlannedQuerySet
    from app.business_query.plan.attempts import (
        PlannerAttemptResponse,
        PlannerAttemptStart,
        PlannerAttemptTerminal,
    )
    from app.business_query.plan.value_resolver import (
        ResolvedValue,
        ResolverLookup,
    )
    from app.business_query.seal.events.event import (
        BusinessQueryExecutionEvent,
        EventAccessContext,
        ResolverQueryStart,
        StoredExecutionEvent,
    )
    from app.business_query.seal.evidence import (
        BusinessQueryEvidenceContext,
        UnsealedAdapterAnswer,
    )
    from app.business_query.wire.request import BusinessQueryRequest
    from app.business_query.wire.trace import QueryTrace
    from app.core.turn_budget import TurnBudget
    from app.models.result_presentation import ResultPresentation
    from app.query_records.types import QueryRecordData

ProgressStage = Literal[
    "understand",
    "planning",
    "authorize",
    "authorized",
    "finding_record",
    "querying",
    "sealing",
    "answering",
]


@dataclass(frozen=True)
class CommittedTable:
    """One sealed ordinal, ready to paint. Built after commit, never before."""

    ordinal: int
    tool_kind: str
    answer_query_id: str
    columns: tuple[ResultColumn, ...]
    rows: tuple[dict[str, Any], ...]
    total_row_count: int
    presentation: ResultPresentation | None


@runtime_checkable
class QueryRecordWritePort(Protocol):
    """Synchronous terminal projection port; State owns its implementation."""

    async def bind_sql_receipt(self, correlation_id: str, answer_query_id: str) -> None: ...

    async def write(self, record: QueryRecordData) -> None: ...

    def record_failure(self) -> None: ...


@runtime_checkable
class BusinessProgressSink(Protocol):
    def emit(
        self,
        stage: ProgressStage,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> None: ...

    def table(self, section: CommittedTable) -> None: ...

    def emit_thought_delta(self, chunk: str) -> None: ...

    def finish_thought(self) -> None: ...


@runtime_checkable
class PlannerAdapter(Protocol):
    async def plan(
        self,
        question: str,
        card: str,
        *,
        business_date: date | None = None,
        retry_hint: str | None = None,
        trace: QueryTrace | None = None,
        clarification_exchange: tuple[str, str] | None = None,
        dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None = None,
        progress: BusinessProgressSink | None = None,
    ) -> (
        BusinessQueryPlan
        | PlannedQuerySet
        | ClarificationRequired
        | Unsupported
        | Incomplete
        | Denied
    ): ...


@runtime_checkable
class ExecutionAdapter(Protocol):
    def execute(
        self, scoped: ScopedPlan, *, trace: QueryTrace | None = None
    ) -> Answered | Incomplete | Denied | Unsupported: ...


@runtime_checkable
class EvidenceExecutionAdapter(Protocol):
    def execute_with_evidence(
        self, scoped: ScopedPlan, *, trace: QueryTrace | None = None
    ) -> UnsealedAdapterAnswer | Incomplete | Denied | Unsupported: ...


@runtime_checkable
class AuthorizedValueResolver(Protocol):
    def resolve(
        self,
        lookup: ResolverLookup,
        scoped: ScopedPlan,
        bundle: DefinitionBundle,
        *,
        trace: QueryTrace | None = None,
    ) -> ResolvedValue: ...


@runtime_checkable
class ExecutionEventStore(Protocol):
    async def append(self, event: BusinessQueryExecutionEvent) -> str: ...

    async def append_resolver_started(self, event: ResolverQueryStart) -> str: ...

    async def load(self, answer_query_id: str) -> StoredExecutionEvent | None: ...

    async def tombstone(
        self,
        answer_query_id: str,
        access: EventAccessContext,
        *,
        reason_digest: str,
        tombstoned_at: datetime,
    ) -> None: ...


@runtime_checkable
class ExecutionEventResolver(Protocol):
    async def resolve(
        self,
        answer_query_id: str,
        access: EventAccessContext,
        *,
        now: datetime | None = None,
    ) -> BusinessQueryExecutionEvent: ...


@runtime_checkable
class PlanStore(Protocol):
    async def save_plan(self, stored: StoredPlan) -> StoredPlan: ...

    async def get_plan(
        self, answer_query_id: str, *, now: datetime | None = None
    ) -> StoredPlan | None: ...


@runtime_checkable
class PlannerAttemptSink(Protocol):
    async def start(self, start: PlannerAttemptStart) -> str: ...

    async def commit_response(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        response: PlannerAttemptResponse,
    ) -> None: ...

    async def finish(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        terminal: PlannerAttemptTerminal,
    ) -> None: ...


@runtime_checkable
class CaseRepeatSink(Protocol):
    async def start(self, start: CaseRepeatStart) -> str: ...

    async def finish(
        self,
        case_repeat_id: str,
        *,
        expected_lease_epoch: int,
        terminal: CaseRepeatTerminal,
    ) -> None: ...


@runtime_checkable
class DimensionExprFn(Protocol):
    """Compile one dimension into its SQL expression (the adapter supplies the bound method)."""

    def __call__(
        self,
        dimension: DimensionDefinition,
        table: sa.Table,
        label: str,
        *,
        business_date: date | None,
    ) -> ColumnElement[Any]: ...


@runtime_checkable
class CompilerAdapter(Protocol):
    """Structural contract the compile layer needs from a plan-to-SQL
    adapter (e.g. ``InternalCompilerAdapter``). Consolidated here (ADR 0054)
    rather than declared in compile/statement_builder.py, so that module and
    its siblings (derived_sets.py, filter_compiler.py) never import back into
    the adapter that composes them."""

    _bundle: DefinitionBundle
    _dimensions: dict[str, DimensionDefinition]
    _metadata: sa.MetaData

    def _assert_authorized(self, scoped: ScopedPlan) -> None: ...
    def _resolve_capability(self, name: str) -> tuple[str, Any]: ...
    def _resolve_join_tree(self, resources: tuple[str, ...]) -> list[ResolvedJoin]: ...
    def _table_for(self, resource_name: str) -> sa.Table: ...
    def _forced_predicates(
        self, forced: tuple[ForcedPredicate, ...], tables: dict[str, sa.Table]
    ) -> list[ColumnElement[bool]]: ...
    def _measure_expr(
        self,
        measure: MeasureDefinition,
        table: sa.Table,
        label: str,
        *,
        business_date: date | None = None,
    ) -> ColumnElement[Any]: ...
    def _dimension_expr(
        self,
        dimension: DimensionDefinition,
        table: sa.Table,
        label: str,
        *,
        business_date: date | None,
    ) -> ColumnElement[Any]: ...
    def _bucket_case(
        self,
        bucket: BucketSetDefinition,
        table: sa.Table,
        *,
        label: str,
        business_date: date | None,
    ) -> ColumnElement[Any]: ...
    def _filter_sql_predicate(
        self, filter_sql: str | None, table: sa.Table
    ) -> ColumnElement[bool] | None: ...
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
    ) -> ColumnElement[bool] | None: ...


@runtime_checkable
class ResolveValuesStage(Protocol):
    async def __call__(  # noqa: PLR0913
        self,
        planned: BusinessQueryPlan,
        scoped: ScopedPlan,
        request: BusinessQueryRequest,
        bundle: DefinitionBundle,
        *,
        evidence: BusinessQueryEvidenceContext | None,
        trace: QueryTrace,
        progress: BusinessProgressSink | None = None,
        ordinal: int = 0,
        of: int = 1,
        turn_budget: TurnBudget,
    ) -> (
        tuple[BusinessQueryPlan, ScopedPlan]
        | ClarificationRequired
        | Denied
        | Incomplete
        | Unsupported
    ): ...


@runtime_checkable
class ExecuteAndPresentStage(Protocol):
    async def __call__(  # noqa: PLR0913
        self,
        request: BusinessQueryRequest,
        scoped: ScopedPlan,
        *,
        progress: BusinessProgressSink | None,
        evidence: BusinessQueryEvidenceContext | None,
        trace: QueryTrace,
        ordinal: int = 0,
        of: int = 1,
        turn_budget: TurnBudget,
    ) -> BusinessQueryOutcome: ...


# No ScopedDerivedSetLike/StoredPlanLike protocol lives here for
# authorize/set_keys.py or compile/pagination/plan_payload.py: both already
# receive a real edge from an import-cycle-baseline SCC member (scoping.py,
# plan_store.py), and this module is itself a member of that same SCC via
# ScopedPlan/StoredPlan above -- either file importing a protocol from here
# would close a new cycle back through it. Both take that one parameter as
# `Any` instead; see the docstrings on assert_set_key_compatible and
# stored_plan_fingerprint.
