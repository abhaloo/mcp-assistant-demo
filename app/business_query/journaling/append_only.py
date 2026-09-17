"""Generic append-only journal primitive for start/terminal CAS lifecycles.

A journal entry begins with a `start` payload and later closes with a
`terminal` payload. Both commits are idempotent-safe: a caller that replays
the same payload (same digest) gets the existing state back instead of a
second write. A `lease_epoch` on the state gives every mutating call an
optimistic-concurrency guard, and a lease can be reclaimed once it expires,
which bumps the epoch for whoever reclaims it.

This module is pure and storage-agnostic: it never computes digests itself
and never touches a dict, a lock, or a database session. A caller looks up
the state it already has (from an in-memory dict, a Postgres row, or
wherever), computes the digest of the payload it wants to commit, and calls
into these functions to get back the next state or a `JournalConflictError`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Generic, TypeVar

StartT = TypeVar("StartT")
TerminalT = TypeVar("TerminalT")


class JournalConflictError(RuntimeError):
    """A journal mutation violates the append-only CAS contract."""


@dataclass(frozen=True)
class JournalState(Generic[StartT, TerminalT]):
    """Durable state for one append-only journal entry.

    `lease_epoch` is the epoch a caller must present as
    `expected_lease_epoch` to `commit_terminal` (or to any other
    lease-guarded mutation a caller layers on top of this primitive).
    `reclaim_lease` is the only way this epoch advances.
    """

    start: StartT
    start_digest: str
    lease_epoch: int
    lease_owner: str
    lease_expires_at: datetime
    terminal: TerminalT | None = None
    terminal_digest: str | None = None


def check_lease_epoch(state: JournalState[StartT, TerminalT], expected_lease_epoch: int) -> None:
    """Guard a state-mutating call against a stale lease epoch.

    Raises `JournalConflictError` when `expected_lease_epoch` does not match
    the epoch currently recorded on `state`. A caller layering an extra
    mutating stage on top of this primitive (a response commit, for example)
    calls this before applying its own mutation, the same way
    `commit_terminal` does.
    """
    if state.lease_epoch != expected_lease_epoch:
        raise JournalConflictError("stale lease epoch")


def start_entry(
    candidate: JournalState[StartT, TerminalT],
    *,
    existing_by_idempotency_key: JournalState[StartT, TerminalT] | None,
    existing_by_entry_id: JournalState[StartT, TerminalT] | None,
) -> JournalState[StartT, TerminalT]:
    """Apply idempotency-key dedup and id-collision guards to a journal start.

    `candidate` is the not-yet-persisted state for this start. `existing_by_idempotency_key`
    is whatever the caller already has stored under this start's idempotency key, if
    anything. `existing_by_entry_id` is whatever the caller already has stored under this
    start's own entry id, if anything.

    Returns the existing state unchanged on an idempotent replay (same digest under the
    same idempotency key). Returns `candidate` when this is a genuinely new entry. Raises
    `JournalConflictError` on a digest mismatch under the same idempotency key, or when the
    entry id is already in use under a different idempotency key.
    """
    if existing_by_idempotency_key is not None:
        if existing_by_idempotency_key.start_digest != candidate.start_digest:
            raise JournalConflictError("start idempotency digest mismatch")
        return existing_by_idempotency_key
    if existing_by_entry_id is not None:
        raise JournalConflictError("entry id already exists")
    return candidate


def commit_terminal(
    state: JournalState[StartT, TerminalT],
    *,
    expected_lease_epoch: int,
    terminal: TerminalT,
    terminal_digest: str,
) -> JournalState[StartT, TerminalT]:
    """Apply the lease-epoch guard and idempotent terminal-commit rule.

    Returns `state` unchanged on an idempotent replay (same digest as the already-recorded
    terminal). Raises `JournalConflictError` on a stale lease epoch or a terminal digest
    mismatch.
    """
    check_lease_epoch(state, expected_lease_epoch)
    if state.terminal is not None:
        if state.terminal_digest != terminal_digest:
            raise JournalConflictError("terminal already committed")
        return state
    return replace(state, terminal=terminal, terminal_digest=terminal_digest)


def reclaim_lease(
    state: JournalState[StartT, TerminalT],
    *,
    now: datetime,
    new_owner: str,
    new_lease_expires_at: datetime,
) -> JournalState[StartT, TerminalT]:
    """Bump the lease epoch when a caller reclaims an expired lease.

    Raises `JournalConflictError` when a terminal is already recorded (nothing left to
    reclaim), or when the current lease has not expired yet (`now` is before
    `state.lease_expires_at`). On success, returns a new state one epoch ahead, owned by
    `new_owner` and expiring at `new_lease_expires_at`; the caller uses the returned epoch
    as its own `expected_lease_epoch` for its eventual terminal commit.
    """
    if state.terminal is not None:
        raise JournalConflictError("terminal already committed")
    if now < state.lease_expires_at:
        raise JournalConflictError("lease is active")
    return replace(
        state,
        lease_epoch=state.lease_epoch + 1,
        lease_owner=new_owner,
        lease_expires_at=new_lease_expires_at,
    )


def require_entry(
    state: JournalState[StartT, TerminalT] | None, *, message: str = "entry not found"
) -> JournalState[StartT, TerminalT]:
    """Raise `JournalConflictError` instead of returning `None` for a missing entry."""
    if state is None:
        raise JournalConflictError(message)
    return state
