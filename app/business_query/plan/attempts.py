"""Append-only planner-attempt lifecycle contracts."""

from __future__ import annotations

import json
from asyncio import Lock
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_query.journaling.append_only import (
    JournalConflictError,
    JournalState,
    check_lease_epoch,
    commit_terminal,
    reclaim_lease,
    require_entry,
    start_entry,
)
from app.business_query.journaling.journal import (
    insert_conflict_digest,
    journal_digest,
    journal_payload,
)
from app.query_records.model import (
    BusinessQueryPlannerAttemptLeaseRow,
    BusinessQueryPlannerAttemptRow,
)


class PlannerTerminalClass(StrEnum):
    COMPLETED = "completed"
    PLANNER_PROTOCOL = "planner_protocol"
    PLANNER_SEMANTIC = "planner_semantic"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    COMPLETION_UNKNOWN = "completion_unknown"


class PlannerTerminalCode(StrEnum):
    PLAN_VALID = "plan_valid"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"
    PLANNER_EMPTY_CONTENT = "planner_empty_content"
    PLANNER_INVALID_JSON = "planner_invalid_json"
    PLANNER_SCHEMA_INVALID = "planner_schema_invalid"
    PLANNER_SHAPE_MISMATCH = "planner_shape_mismatch"
    PLANNER_CAPABILITY_MISMATCH = "planner_capability_mismatch"
    PLANNER_PROVIDER_UNAVAILABLE = "planner_provider_unavailable"
    PLANNER_TIMEOUT = "planner_timeout"
    PLANNER_CANCELLED = "planner_cancelled"
    COMPLETION_UNKNOWN = "completion_unknown"


class PlannerRawShapeClass(StrEnum):
    EMPTY = "empty"
    JSON_OBJECT = "json_object"
    JSON_STRING = "json_string"
    JSON_ARRAY = "json_array"
    JSON_SCALAR = "json_scalar"
    OTHER = "other"


class PlannerValidationResult(StrEnum):
    VALID_PLAN = "valid_plan"
    VALID_CLARIFICATION = "valid_clarification"
    VALID_UNSUPPORTED = "valid_unsupported"
    EMPTY_CONTENT = "empty_content"
    INVALID_JSON = "invalid_json"
    SCHEMA_INVALID = "schema_invalid"


class PlannerAttemptStart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str
    idempotency_key: str
    project_id: str
    run_epoch: str
    case_id: str
    repeat_index: int = Field(ge=0)
    planner_call_index: int = Field(ge=0)
    lease_owner: str
    lease_epoch: int = Field(ge=1)
    lease_expires_at: datetime
    started_at: datetime
    provider: str
    deployment: str
    output_mode: str
    effort: str | None = None
    prompt_adjunct_hash: str | None = None
    schema_hash: str | None = None
    history_digest: str | None = None

    @model_validator(mode="after")
    def _stable_identity_and_live_lease(self) -> PlannerAttemptStart:
        expected = f"{self.run_epoch}:{self.case_id}:{self.repeat_index}:{self.planner_call_index}"
        if self.idempotency_key != expected:
            raise ValueError("planner attempt idempotency key does not match identity")
        if self.lease_expires_at <= self.started_at:
            raise ValueError("planner attempt lease must extend past start")
        return self


PlannerAttemptContext = PlannerAttemptStart


class PlannerAttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response_digest: str = Field(min_length=64, max_length=64)
    raw_shape_class: PlannerRawShapeClass
    validation_result: PlannerValidationResult
    plan_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    repair_hint_code: str | None = None
    committed_at: datetime


class PlannerAttemptTerminal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_class: PlannerTerminalClass
    terminal_code: PlannerTerminalCode
    duration_ms: int = Field(ge=0)
    answer_query_id: str | None = None
    finished_at: datetime

    @model_validator(mode="after")
    def _code_matches_class(self) -> PlannerAttemptTerminal:
        expected_class = {
            PlannerTerminalCode.PLAN_VALID: PlannerTerminalClass.COMPLETED,
            PlannerTerminalCode.CLARIFICATION_REQUIRED: PlannerTerminalClass.COMPLETED,
            PlannerTerminalCode.UNSUPPORTED: PlannerTerminalClass.COMPLETED,
            PlannerTerminalCode.PLANNER_EMPTY_CONTENT: PlannerTerminalClass.PLANNER_PROTOCOL,
            PlannerTerminalCode.PLANNER_INVALID_JSON: PlannerTerminalClass.PLANNER_PROTOCOL,
            PlannerTerminalCode.PLANNER_SCHEMA_INVALID: PlannerTerminalClass.PLANNER_PROTOCOL,
            PlannerTerminalCode.PLANNER_SHAPE_MISMATCH: PlannerTerminalClass.PLANNER_SEMANTIC,
            PlannerTerminalCode.PLANNER_CAPABILITY_MISMATCH: PlannerTerminalClass.PLANNER_PROTOCOL,
            PlannerTerminalCode.PLANNER_PROVIDER_UNAVAILABLE: PlannerTerminalClass.PROVIDER_ERROR,
            PlannerTerminalCode.PLANNER_TIMEOUT: PlannerTerminalClass.TIMEOUT,
            PlannerTerminalCode.PLANNER_CANCELLED: PlannerTerminalClass.CANCELLED,
            PlannerTerminalCode.COMPLETION_UNKNOWN: PlannerTerminalClass.COMPLETION_UNKNOWN,
        }[self.terminal_code]
        if self.terminal_class is not expected_class:
            raise ValueError("planner terminal code does not match terminal class")
        return self


@dataclass(frozen=True)
class PlannerAttemptJournal:
    start: PlannerAttemptStart
    start_digest: str
    response: PlannerAttemptResponse | None = None
    response_digest: str | None = None
    terminal: PlannerAttemptTerminal | None = None
    terminal_digest: str | None = None

    @property
    def event_kinds(self) -> tuple[str, ...]:
        kinds = ["started"]
        if self.response is not None:
            kinds.append("response_committed")
        if self.terminal is not None:
            kinds.append("terminal")
        return tuple(kinds)


class AttemptConflictError(JournalConflictError):
    pass


class InMemoryPlannerAttemptSink:
    """Deterministic adapter with the same CAS rules as the Postgres sink.

    Lease state (owner/epoch/expiry) lives on the `JournalState` the sink
    tracks per attempt, separate from the immutable `PlannerAttemptStart`
    each attempt began with — the same separation the Postgres sink keeps
    between its lease row and its event log. The response stage has no
    equivalent in the shared journal primitive, so it is tracked in its own
    dict and gated by attempts-specific rules layered on top.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, JournalState[PlannerAttemptStart, PlannerAttemptTerminal]] = {}
        self._responses: dict[str, tuple[PlannerAttemptResponse, str]] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = Lock()

    @property
    def attempt_count(self) -> int:
        return len(self._attempts)

    async def start(self, start: PlannerAttemptStart) -> str:
        digest = journal_digest(start)
        candidate = JournalState(
            start=start,
            start_digest=digest,
            lease_epoch=start.lease_epoch,
            lease_owner=start.lease_owner,
            lease_expires_at=start.lease_expires_at,
        )
        async with self._lock:
            prior_id = self._idempotency.get(start.idempotency_key)
            existing_by_idempotency_key = self._attempts.get(prior_id) if prior_id else None
            existing_by_entry_id = self._attempts.get(start.attempt_id)
            if (
                existing_by_idempotency_key is not None
                and existing_by_idempotency_key.terminal is not None
                and existing_by_idempotency_key.terminal.terminal_class
                is PlannerTerminalClass.COMPLETION_UNKNOWN
            ):
                raise AttemptConflictError("uncertain completion; do not retry blindly")
            try:
                start_entry(
                    candidate,
                    existing_by_idempotency_key=existing_by_idempotency_key,
                    existing_by_entry_id=existing_by_entry_id,
                )
            except JournalConflictError as exc:
                raise AttemptConflictError(str(exc)) from exc
            if prior_id is not None:
                return prior_id
            self._attempts[start.attempt_id] = candidate
            self._idempotency[start.idempotency_key] = start.attempt_id
        return start.attempt_id

    async def commit_response(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        response: PlannerAttemptResponse,
    ) -> None:
        digest = journal_digest(response)
        async with self._lock:
            state = self._required(attempt_id)
            try:
                check_lease_epoch(state, expected_lease_epoch)
            except JournalConflictError as exc:
                raise AttemptConflictError(str(exc)) from exc
            if state.terminal is not None:
                raise AttemptConflictError("attempt terminal already committed")
            existing = self._responses.get(attempt_id)
            if existing is not None:
                if existing[1] != digest:
                    raise AttemptConflictError("attempt response digest mismatch")
                return
            self._responses[attempt_id] = (response, digest)

    async def finish(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        terminal: PlannerAttemptTerminal,
    ) -> None:
        digest = journal_digest(terminal)
        async with self._lock:
            state = self._required(attempt_id)
            try:
                check_lease_epoch(state, expected_lease_epoch)
            except JournalConflictError as exc:
                raise AttemptConflictError(str(exc)) from exc
            if state.terminal is None:
                response, _ = self._responses.get(attempt_id, (None, None))
                if terminal.terminal_class is PlannerTerminalClass.COMPLETED and response is None:
                    raise AttemptConflictError("attempt response commit required")
                if (
                    terminal.terminal_class is PlannerTerminalClass.COMPLETION_UNKNOWN
                    and response is not None
                ):
                    raise AttemptConflictError("attempt response already committed")
            try:
                self._attempts[attempt_id] = commit_terminal(
                    state,
                    expected_lease_epoch=expected_lease_epoch,
                    terminal=terminal,
                    terminal_digest=digest,
                )
            except JournalConflictError as exc:
                raise AttemptConflictError(str(exc)) from exc

    async def claim_recovery(
        self,
        attempt_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int:
        async with self._lock:
            state = self._required(attempt_id)
            try:
                new_state = reclaim_lease(
                    state,
                    now=now,
                    new_owner=owner,
                    new_lease_expires_at=lease_expires_at,
                )
            except JournalConflictError as exc:
                raise AttemptConflictError(str(exc)) from exc
            self._attempts[attempt_id] = new_state
            return new_state.lease_epoch

    async def get(self, attempt_id: str) -> PlannerAttemptJournal:
        state = self._required(attempt_id)
        response, response_digest = self._responses.get(attempt_id, (None, None))
        return PlannerAttemptJournal(
            start=state.start,
            start_digest=state.start_digest,
            response=response,
            response_digest=response_digest,
            terminal=state.terminal,
            terminal_digest=state.terminal_digest,
        )

    def _required(
        self, attempt_id: str
    ) -> JournalState[PlannerAttemptStart, PlannerAttemptTerminal]:
        try:
            return require_entry(self._attempts.get(attempt_id), message="attempt not found")
        except JournalConflictError as exc:
            raise AttemptConflictError(str(exc)) from exc


class PostgresPlannerAttemptSink:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, start: PlannerAttemptStart) -> str:
        digest, event_json = journal_payload(start)
        event_stmt = pg_insert(BusinessQueryPlannerAttemptRow).values(
            attempt_id=start.attempt_id,
            idempotency_key=start.idempotency_key,
            project_id=start.project_id,
            run_epoch=start.run_epoch,
            case_id=start.case_id,
            repeat_index=start.repeat_index,
            planner_call_index=start.planner_call_index,
            event_kind="started",
            event_digest=digest,
            event_json=event_json,
            lease_epoch=start.lease_epoch,
        )
        event_stmt = event_stmt.on_conflict_do_nothing(
            index_elements=["idempotency_key", "event_kind"]
        )
        try:
            await self._session.execute(event_stmt)
            committed = (
                await self._session.execute(
                    select(BusinessQueryPlannerAttemptRow)
                    .where(
                        BusinessQueryPlannerAttemptRow.idempotency_key == start.idempotency_key,
                        BusinessQueryPlannerAttemptRow.event_kind == "started",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if committed is None or committed.event_digest != digest:
                raise AttemptConflictError("attempt idempotency digest mismatch")
            lease_stmt = pg_insert(BusinessQueryPlannerAttemptLeaseRow).values(
                attempt_id=committed.attempt_id,
                lease_owner=start.lease_owner,
                lease_epoch=start.lease_epoch,
                lease_expires_at=start.lease_expires_at,
            )
            lease_stmt = lease_stmt.on_conflict_do_nothing(index_elements=["attempt_id"])
            await self._session.execute(lease_stmt)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return committed.attempt_id

    async def commit_response(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        response: PlannerAttemptResponse,
    ) -> None:
        await self._append(
            attempt_id,
            expected_lease_epoch=expected_lease_epoch,
            event_kind="response_committed",
            payload=response,
        )

    async def finish(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        terminal: PlannerAttemptTerminal,
    ) -> None:
        await self._append(
            attempt_id,
            expected_lease_epoch=expected_lease_epoch,
            event_kind="terminal",
            payload=terminal,
        )

    async def claim_recovery(
        self,
        attempt_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int:
        try:
            lease = (
                await self._session.execute(
                    select(BusinessQueryPlannerAttemptLeaseRow)
                    .where(BusinessQueryPlannerAttemptLeaseRow.attempt_id == attempt_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if lease is None:
                raise AttemptConflictError("attempt not found")
            if (await self.get(attempt_id)).terminal is not None:
                raise AttemptConflictError("attempt terminal already committed")
            if now < lease.lease_expires_at:
                raise AttemptConflictError("attempt lease is active")
            epoch = lease.lease_epoch + 1
            await self._session.execute(
                update(BusinessQueryPlannerAttemptLeaseRow)
                .where(BusinessQueryPlannerAttemptLeaseRow.attempt_id == attempt_id)
                .where(BusinessQueryPlannerAttemptLeaseRow.lease_epoch == lease.lease_epoch)
                .values(
                    lease_owner=owner,
                    lease_epoch=epoch,
                    lease_expires_at=lease_expires_at,
                )
            )
            await self._session.commit()
            return epoch
        except Exception:
            await self._session.rollback()
            raise

    async def get(self, attempt_id: str) -> PlannerAttemptJournal:
        rows = (
            (
                await self._session.execute(
                    select(BusinessQueryPlannerAttemptRow)
                    .where(BusinessQueryPlannerAttemptRow.attempt_id == attempt_id)
                    .order_by(BusinessQueryPlannerAttemptRow.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise AttemptConflictError("attempt not found")
        by_kind = {row.event_kind: row for row in rows}
        started = by_kind["started"]
        return PlannerAttemptJournal(
            start=PlannerAttemptStart(**json.loads(started.event_json)),
            start_digest=started.event_digest,
            response=(
                PlannerAttemptResponse(**json.loads(by_kind["response_committed"].event_json))
                if "response_committed" in by_kind
                else None
            ),
            response_digest=(
                by_kind["response_committed"].event_digest
                if "response_committed" in by_kind
                else None
            ),
            terminal=(
                PlannerAttemptTerminal(**json.loads(by_kind["terminal"].event_json))
                if "terminal" in by_kind
                else None
            ),
            terminal_digest=(by_kind["terminal"].event_digest if "terminal" in by_kind else None),
        )

    async def _append(
        self,
        attempt_id: str,
        *,
        expected_lease_epoch: int,
        event_kind: str,
        payload: BaseModel,
    ) -> None:
        lease = (
            await self._session.execute(
                select(BusinessQueryPlannerAttemptLeaseRow)
                .where(BusinessQueryPlannerAttemptLeaseRow.attempt_id == attempt_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if lease is None or lease.lease_epoch != expected_lease_epoch:
            raise AttemptConflictError("attempt stale lease epoch")
        journal = await self.get(attempt_id)
        start = journal.start
        digest, event_json = journal_payload(payload)
        if journal.terminal is not None:
            if event_kind == "terminal" and journal.terminal_digest == digest:
                await self._session.commit()
                return
            raise AttemptConflictError("attempt terminal already committed")
        if event_kind == "response_committed" and journal.response is not None:
            if journal.response_digest == digest:
                await self._session.commit()
                return
            raise AttemptConflictError("attempt response digest mismatch")
        if event_kind == "terminal":
            if not isinstance(payload, PlannerAttemptTerminal):
                raise AttemptConflictError("attempt terminal payload invalid")
            if (
                payload.terminal_class is PlannerTerminalClass.COMPLETED
                and journal.response is None
            ):
                raise AttemptConflictError("attempt response commit required")
            if (
                payload.terminal_class is PlannerTerminalClass.COMPLETION_UNKNOWN
                and journal.response is not None
            ):
                raise AttemptConflictError("attempt response already committed")
        stmt = pg_insert(BusinessQueryPlannerAttemptRow).values(
            attempt_id=attempt_id,
            idempotency_key=start.idempotency_key,
            project_id=start.project_id,
            run_epoch=start.run_epoch,
            case_id=start.case_id,
            repeat_index=start.repeat_index,
            planner_call_index=start.planner_call_index,
            event_kind=event_kind,
            event_digest=digest,
            event_json=event_json,
            lease_epoch=expected_lease_epoch,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["attempt_id", "event_kind"])
        stmt = stmt.returning(BusinessQueryPlannerAttemptRow.event_digest)
        label = (
            "response digest mismatch"
            if event_kind == "response_committed"
            else "terminal already committed"
        )
        await insert_conflict_digest(
            self._session,
            stmt,
            digest=digest,
            existing_stmt=select(BusinessQueryPlannerAttemptRow.event_digest).where(
                BusinessQueryPlannerAttemptRow.attempt_id == attempt_id,
                BusinessQueryPlannerAttemptRow.event_kind == event_kind,
            ),
            mismatch=AttemptConflictError(f"attempt {label}"),
        )
