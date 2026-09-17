"""Governed application ports the coordinator may call.

Principal, budget, progress sink and correlation id are bound by the composition
root; the coordinator passes business-level questions only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.auth import Principal
from app.business_query.definitions import BundleSelectionError
from app.business_query.plan import PlannedQuerySet
from app.business_query.plan.attempts import AttemptConflictError
from app.business_query.ports import ProgressStage
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.conversation.coordinator.contracts import ActionKind
from app.conversation.followup_context import SelectedSources, restore_selected
from app.conversation.followup_contracts import SourceCandidate, SourceSelection
from app.core.errors import CapabilityUnavailableError
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult
from app.services.ask_frames import AskStage
from app.services.business_query_mapping import (
    BQ_COMPOSITION_ERRORS,
    composition_failed_result,
    denied_capability_result,
    request_conflict_result,
)
from app.services.business_query_runtime import with_turn_result
from app.services.tool_composition import is_tool_layer_enabled  # noqa: F401
from app.tools.contracts import (
    BusinessQueryInput,
    BusinessQueryInvocation,
    DocumentHandler,
    DocumentSearchInput,
    ToolContext,
)

if TYPE_CHECKING:
    from app.business_query.ports import CommittedTable
    from app.conversation.evidence.ports import EvidenceRestoreService
    from app.models.record_context import RecordContext
    from app.services.business_query_operation import PreparedBqOperation


@runtime_checkable
class CoordinatorProgress(Protocol):
    """What a person sees while the coordinator works: one step per tool that
    actually runs, and a thinking phase around each decision."""

    def stage(self, stage: AskStage) -> None: ...

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
class CoordinatorTools(Protocol):
    """Governed application ports available to the conversational coordinator."""

    async def query_business(self, question: str) -> CommittedBqResult | AskBusinessQueryResult: ...

    async def search_documents(self, question: str) -> DocumentSearchResult | DocumentFailure: ...

    async def explain_sources(self, selection: SourceSelection) -> SelectedSources | None: ...

    def available_actions(self) -> frozenset[ActionKind]: ...

    async def aclose(self) -> None: ...


class AskCoordinatorTools:
    """Production implementation of CoordinatorTools binding request-scoped authorities."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        principal: Principal,
        budget: TurnBudget = UNBOUNDED_BUDGET,
        correlation_id: str = "",
        record_context: RecordContext | None = None,
        bq_operation: PreparedBqOperation | None = None,
        bq_operation_factory: Callable[[str], PreparedBqOperation] | None = None,
        document_handler: DocumentHandler | None = None,
        candidates: Sequence[SourceCandidate] = (),
        thread_id: str = "",
        restore_service: EvidenceRestoreService | None = None,
        progress: CoordinatorProgress | None = None,
    ) -> None:
        self._principal = principal
        self._budget = budget
        self._correlation_id = correlation_id
        self._record_context = record_context
        self._operation = bq_operation
        self._bq_operation_factory = bq_operation_factory
        self._document_handler = document_handler
        self._candidates = tuple(candidates)
        self._thread_id = thread_id
        self._restore_service = restore_service
        self._progress = progress
        self._closed: bool = False

    def available_actions(self) -> frozenset[ActionKind]:
        """The tool actions this request can run: only those with a wired
        collaborator, so an absent handler is never offered and mistaken for
        an empty corpus or a stale answer."""
        available: set[ActionKind] = set()
        if self._operation is not None or self._bq_operation_factory is not None:
            available.add("query_business")
        if self._document_handler is not None:
            available.add("search_documents")
        if self._restore_service is not None:
            available.add("explain_sources")
        return frozenset(available)

    async def query_business(self, question: str) -> CommittedBqResult | AskBusinessQueryResult:
        if self._closed:
            raise RuntimeError("AskCoordinatorTools is already closed")

        if self._operation is None and self._bq_operation_factory is None:
            raise RuntimeError("No BQ operation or factory configured")

        # Every failure below maps onto the same result the structured route
        # returns, so the turn finishes honestly instead of dying in the graph.
        try:
            if self._operation is None and self._bq_operation_factory is not None:
                self._operation = self._bq_operation_factory(question)
            assert self._operation is not None
            prepared = await self._operation.prepare()
            if isinstance(prepared, PlannedQuerySet):
                call = BusinessQueryInvocation(
                    name="business_query",
                    version=1,
                    invocation_id="inv-1",
                    arguments=BusinessQueryInput(
                        primary=prepared.primary,
                        companions=prepared.companions,
                    ),
                )
                executed = await self._operation.execute(call)
                # Same reduction the structured route applies, so the answer
                # written over this result retains its evidence.
                return with_turn_result(executed, principal=self._principal)
            return with_turn_result(prepared, principal=self._principal)
        except (CapabilityUnavailableError, BundleSelectionError):
            return denied_capability_result()
        except AttemptConflictError:
            return request_conflict_result(correlation_id=self._correlation_id)
        except BQ_COMPOSITION_ERRORS as exc:
            return composition_failed_result(exc, correlation_id=self._correlation_id)

    async def search_documents(self, question: str) -> DocumentSearchResult | DocumentFailure:
        if self._closed:
            raise RuntimeError("AskCoordinatorTools is already closed")

        if self._document_handler is None:
            return DocumentSearchResult(passages=(), provenance=(), truncated=False)

        if self._progress is not None:
            self._progress.stage("searching_documents")
        ctx = ToolContext(
            principal=self._principal,
            correlation_id=self._correlation_id,
            budget=self._budget,
            record_context=self._record_context,
            origin="ask",
        )
        return await self._document_handler(DocumentSearchInput(query=question), ctx)

    async def explain_sources(self, selection: SourceSelection) -> SelectedSources | None:
        if self._closed:
            raise RuntimeError("AskCoordinatorTools is already closed")

        if self._restore_service is None:
            return None

        return await restore_selected(
            selection,
            self._candidates,
            thread_id=self._thread_id,
            principal=self._principal,
            restore=self._restore_service,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._operation is not None:
            await self._operation.aclose()
