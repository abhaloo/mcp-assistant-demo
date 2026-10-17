"""create ask_evidence_snapshots table for encrypted immutable evidence restore

Revision ID: 011_ask_evidence_snapshots
Revises: 010_query_record_thread_id
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011_ask_evidence_snapshots"
down_revision: str | None = "010_query_record_thread_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ask_evidence_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("restore_ref", sa.String(length=43), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("exchange_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tombstone", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("restore_ref", name="uq_ask_evidence_snapshots_restore_ref"),
    )
    op.create_index(
        "ix_ask_evidence_snapshots_restore_ref",
        "ask_evidence_snapshots",
        ["restore_ref"],
        unique=True,
    )
    op.create_index(
        "ix_ask_evidence_snapshots_thread_id",
        "ask_evidence_snapshots",
        ["thread_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ask_evidence_snapshots_thread_id", table_name="ask_evidence_snapshots")
    op.drop_index("ix_ask_evidence_snapshots_restore_ref", table_name="ask_evidence_snapshots")
    op.drop_table("ask_evidence_snapshots")
