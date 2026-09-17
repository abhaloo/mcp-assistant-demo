"""Shared digest helpers for append-only Business Query journals."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_query.journaling.canonical_json import canonical_json_bytes


def journal_json_bytes(
    model: BaseModel, *, exclude: set[str] | frozenset[str] | None = None
) -> bytes:
    return canonical_json_bytes(model.model_dump(mode="json", exclude=exclude or set()))


def journal_payload(
    model: BaseModel, *, exclude: set[str] | frozenset[str] | None = None
) -> tuple[str, bytes]:
    encoded = journal_json_bytes(model, exclude=exclude)
    return hashlib.sha256(encoded).hexdigest(), encoded


def journal_digest(model: BaseModel) -> str:
    return journal_payload(model)[0]


async def insert_conflict_digest(
    session: AsyncSession,
    stmt,
    *,
    digest: str,
    existing_stmt,
    mismatch: Exception,
) -> None:
    try:
        inserted_digest = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if inserted_digest is None:
        existing = (await session.execute(existing_stmt)).scalar_one()
        if existing != digest:
            raise mismatch
