"""Async Postgres helpers for the Query Record store.

``ProcessResources`` owns the engine and session factory. This module
is a thin accessor — it never keeps module-level pools.

``session_scope`` commits on clean exit and rolls back on exception. ADR 0030
consumers (import of ``session_scope`` from this module): Ask compose,
Query Record dispatch and late writes, the invocation ledger, and
``scripts/ops/support_timeline.py``. Tests that monkeypatch the helper are
listed by ``tests/architecture/test_answered_query_record_uow.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from app.resources import ProcessResources


def get_async_engine(*, resources: ProcessResources | None = None) -> AsyncEngine:
    from app.resources import current_process_resources

    owner = resources if resources is not None else current_process_resources()
    return owner.query_record_engine


def get_session_factory(
    *, resources: ProcessResources | None = None
) -> async_sessionmaker[AsyncSession]:
    from app.resources import current_process_resources

    owner = resources if resources is not None else current_process_resources()
    return owner.query_record_session_factory


@asynccontextmanager
async def session_scope(
    *, resources: ProcessResources | None = None
) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(resources=resources)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
