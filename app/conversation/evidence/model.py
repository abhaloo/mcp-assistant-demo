"""SQLAlchemy model for Ask evidence snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.query_records.model import Base


class AskEvidenceSnapshot(Base):
    """Encrypted immutable snapshot of authorized evidence for historical restore."""

    __tablename__ = "ask_evidence_snapshots"
    __table_args__ = (
        UniqueConstraint("restore_ref", name="uq_ask_evidence_snapshots_restore_ref"),
        Index("ix_ask_evidence_snapshots_restore_ref", "restore_ref", unique=True),
        Index("ix_ask_evidence_snapshots_thread_id", "thread_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    restore_ref: Mapped[str] = mapped_column(String(43), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    department_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tombstone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
