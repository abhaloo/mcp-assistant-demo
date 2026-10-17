"""Append-only evaluation case-repeat lifecycle contracts."""

from __future__ import annotations

import hashlib
import json
from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_query.journaling.append_only import (
    JournalConflictError,
    JournalState,
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
from app.business_query.ports import CaseRepeatSink
from app.query_records.model import (
    BusinessQueryCaseRepeatLeaseRow,
    BusinessQueryCaseRepeatRow,
)


class CaseTerminalClass(StrEnum):
    ANSWERED = "answered"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"
    DENIED = "denied"
    INCOMPLETE = "incomplete"
    SCORER_ERROR = "scorer_error"
    EVALUATOR_ERROR = "evaluator_error"
    BUDGET_STOP = "budget_stop"
    CANCELLED = "cancelled"
    UNEXPECTED_ERROR = "unexpected_error"
    COMPLETION_UNKNOWN = "completion_unknown"


class CaseRepeatStart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_repeat_id: str
    idempotency_key: str
    project_id: str
    run_epoch: str
    case_id: str
    repeat_index: int = Field(ge=0)
    lease_owner: str
    lease_epoch: int = Field(ge=1)
    lease_expires_at: datetime
    started_at: datetime


class CaseRepeatTerminal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_class: CaseTerminalClass
    terminal_code: str
    final_planner_attempt_id: str | None = None
    answer_query_id: str | None = None
    finished_at: datetime


@dataclass(frozen=True)
class CaseRepeatJournal:
    start: CaseRepeatStart
    start_digest: str
    terminal: CaseRepeatTerminal | None = None
    terminal_digest: str | None = None

    @property
    def event_kinds(self) -> tuple[str, ...]:
        return ("started", "terminal") if self.terminal is not None else ("started",)


class CaseRepeatConflictError(JournalConflictError):
    pass


class InMemoryCaseRepeatSink:
    """Deterministic adapter with the same CAS rules as the Postgres sink.

    Lease state (owner/epoch/expiry) lives on the `JournalState` the sink
    tracks per repeat, separate from the immutable `CaseRepeatStart` each
    repeat began with — the same separation the Postgres sink keeps between
    its lease row and its event log. `finish()` has no attempts.py-style
    extra business rules, so it maps directly onto `commit_terminal`.
    """

    def __init__(self) -> None:
        self._repeats: dict[str, JournalState[CaseRepeatStart, CaseRepeatTerminal]] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = Lock()

    async def start(self, start: CaseRepeatStart) -> str:
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
            existing_by_idempotency_key = self._repeats.get(prior_id) if prior_id else None
            existing_by_entry_id = self._repeats.get(start.case_repeat_id)
            try:
                start_entry(
                    candidate,
                    existing_by_idempotency_key=existing_by_idempotency_key,
                    existing_by_entry_id=existing_by_entry_id,
                )
            except JournalConflictError as exc:
                raise CaseRepeatConflictError(str(exc)) from exc
            if prior_id is not None:
                return prior_id
            self._repeats[start.case_repeat_id] = candidate
            self._idempotency[start.idempotency_key] = start.case_repeat_id
        return start.case_repeat_id

    async def finish(
        self,
        case_repeat_id: str,
        *,
        expected_lease_epoch: int,
        terminal: CaseRepeatTerminal,
    ) -> None:
        digest = journal_digest(terminal)
        async with self._lock:
            state = self._required(case_repeat_id)
            try:
                self._repeats[case_repeat_id] = commit_terminal(
                    state,
                    expected_lease_epoch=expected_lease_epoch,
                    terminal=terminal,
                    terminal_digest=digest,
                )
            except JournalConflictError as exc:
                raise CaseRepeatConflictError(str(exc)) from exc

    async def claim_recovery(
        self,
        case_repeat_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int:
        async with self._lock:
            state = self._required(case_repeat_id)
            try:
                new_state = reclaim_lease(
                    state,
                    now=now,
                    new_owner=owner,
                    new_lease_expires_at=lease_expires_at,
                )
            except JournalConflictError as exc:
                raise CaseRepeatConflictError(str(exc)) from exc
            self._repeats[case_repeat_id] = new_state
            return new_state.lease_epoch

    async def get(self, case_repeat_id: str) -> CaseRepeatJournal:
        state = self._required(case_repeat_id)
        return CaseRepeatJournal(
            start=state.start,
            start_digest=state.start_digest,
            terminal=state.terminal,
            terminal_digest=state.terminal_digest,
        )

    def _required(self, case_repeat_id: str) -> JournalState[CaseRepeatStart, CaseRepeatTerminal]:
        try:
            return require_entry(self._repeats.get(case_repeat_id), message="case repeat not found")
        except JournalConflictError as exc:
            raise CaseRepeatConflictError(str(exc)) from exc


class PostgresCaseRepeatSink:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, start: CaseRepeatStart) -> str:
        digest, event_json = journal_payload(start)
        event_stmt = pg_insert(BusinessQueryCaseRepeatRow).values(
            case_repeat_id=start.case_repeat_id,
            idempotency_key=start.idempotency_key,
            project_id=start.project_id,
            run_epoch=start.run_epoch,
            case_id=start.case_id,
            repeat_index=start.repeat_index,
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
                    select(BusinessQueryCaseRepeatRow)
                    .where(
                        BusinessQueryCaseRepeatRow.idempotency_key == start.idempotency_key,
                        BusinessQueryCaseRepeatRow.event_kind == "started",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if committed is None or committed.event_digest != digest:
                raise CaseRepeatConflictError("case repeat idempotency digest mismatch")
            lease_stmt = pg_insert(BusinessQueryCaseRepeatLeaseRow).values(
                case_repeat_id=committed.case_repeat_id,
                lease_owner=start.lease_owner,
                lease_epoch=start.lease_epoch,
                lease_expires_at=start.lease_expires_at,
            )
            lease_stmt = lease_stmt.on_conflict_do_nothing(index_elements=["case_repeat_id"])
            await self._session.execute(lease_stmt)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return committed.case_repeat_id

    async def finish(
        self,
        case_repeat_id: str,
        *,
        expected_lease_epoch: int,
        terminal: CaseRepeatTerminal,
    ) -> None:
        lease = (
            await self._session.execute(
                select(BusinessQueryCaseRepeatLeaseRow)
                .where(BusinessQueryCaseRepeatLeaseRow.case_repeat_id == case_repeat_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if lease is None or lease.lease_epoch != expected_lease_epoch:
            raise CaseRepeatConflictError("case repeat stale lease epoch")
        journal = await self.get(case_repeat_id)
        digest, event_json = journal_payload(terminal)
        start = journal.start
        stmt = pg_insert(BusinessQueryCaseRepeatRow).values(
            case_repeat_id=case_repeat_id,
            idempotency_key=start.idempotency_key,
            project_id=start.project_id,
            run_epoch=start.run_epoch,
            case_id=start.case_id,
            repeat_index=start.repeat_index,
            event_kind="terminal",
            event_digest=digest,
            event_json=event_json,
            lease_epoch=expected_lease_epoch,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["case_repeat_id", "event_kind"])
        stmt = stmt.returning(BusinessQueryCaseRepeatRow.event_digest)
        await insert_conflict_digest(
            self._session,
            stmt,
            digest=digest,
            existing_stmt=select(BusinessQueryCaseRepeatRow.event_digest).where(
                BusinessQueryCaseRepeatRow.case_repeat_id == case_repeat_id,
                BusinessQueryCaseRepeatRow.event_kind == "terminal",
            ),
            mismatch=CaseRepeatConflictError("case repeat terminal already committed"),
        )

    async def claim_recovery(
        self,
        case_repeat_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int:
        try:
            lease = (
                await self._session.execute(
                    select(BusinessQueryCaseRepeatLeaseRow)
                    .where(BusinessQueryCaseRepeatLeaseRow.case_repeat_id == case_repeat_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if lease is None:
                raise CaseRepeatConflictError("case repeat not found")
            if (await self.get(case_repeat_id)).terminal is not None:
                raise CaseRepeatConflictError("case repeat terminal already committed")
            if now < lease.lease_expires_at:
                raise CaseRepeatConflictError("case repeat lease is active")
            epoch = lease.lease_epoch + 1
            await self._session.execute(
                update(BusinessQueryCaseRepeatLeaseRow)
                .where(BusinessQueryCaseRepeatLeaseRow.case_repeat_id == case_repeat_id)
                .where(BusinessQueryCaseRepeatLeaseRow.lease_epoch == lease.lease_epoch)
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

    async def get(self, case_repeat_id: str) -> CaseRepeatJournal:
        rows = (
            (
                await self._session.execute(
                    select(BusinessQueryCaseRepeatRow)
                    .where(BusinessQueryCaseRepeatRow.case_repeat_id == case_repeat_id)
                    .order_by(BusinessQueryCaseRepeatRow.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise CaseRepeatConflictError("case repeat not found")
        by_kind = {row.event_kind: row for row in rows}
        started = by_kind["started"]
        return CaseRepeatJournal(
            start=CaseRepeatStart(**json.loads(started.event_json)),
            start_digest=started.event_digest,
            terminal=(
                CaseRepeatTerminal(**json.loads(by_kind["terminal"].event_json))
                if "terminal" in by_kind
                else None
            ),
            terminal_digest=(by_kind["terminal"].event_digest if "terminal" in by_kind else None),
        )


@dataclass
class CaseRepeatLifecycle:
    sink: CaseRepeatSink
    project_id: str
    run_epoch: str
    case_id: str
    repeat_index: int
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC)

    @property
    def case_repeat_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.run_epoch}:{self.case_id}:{self.repeat_index}".encode()
        ).hexdigest()
        return f"cr_{digest[:32]}"

    async def start(self) -> str:
        started_at = self.now()
        return await self.sink.start(
            CaseRepeatStart(
                case_repeat_id=self.case_repeat_id,
                idempotency_key=f"{self.run_epoch}:{self.case_id}:{self.repeat_index}",
                project_id=self.project_id,
                run_epoch=self.run_epoch,
                case_id=self.case_id,
                repeat_index=self.repeat_index,
                lease_owner=self.run_epoch,
                lease_epoch=1,
                lease_expires_at=started_at + timedelta(minutes=10),
                started_at=started_at,
            )
        )

    async def finish_outcome(
        self,
        *,
        outcome: str,
        reason_code: str,
        answer_query_id: str | None,
        planner_attempt_id: str | None,
    ) -> None:
        await self._finish(
            CaseTerminalClass(outcome),
            reason_code or outcome,
            answer_query_id=answer_query_id,
            planner_attempt_id=planner_attempt_id,
        )

    async def finish_error(self, terminal_class: str, terminal_code: str) -> None:
        await self._finish(CaseTerminalClass(terminal_class), terminal_code)

    async def _finish(
        self,
        terminal_class: CaseTerminalClass,
        terminal_code: str,
        *,
        answer_query_id: str | None = None,
        planner_attempt_id: str | None = None,
    ) -> None:
        await self.sink.finish(
            self.case_repeat_id,
            expected_lease_epoch=1,
            terminal=CaseRepeatTerminal(
                terminal_class=terminal_class,
                terminal_code=terminal_code,
                final_planner_attempt_id=planner_attempt_id,
                answer_query_id=answer_query_id,
                finished_at=self.now(),
            ),
        )
