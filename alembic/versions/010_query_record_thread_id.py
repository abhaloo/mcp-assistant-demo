"""add thread_id to query_records for conversation-scoped trace invariants

Revision ID: 010_query_record_thread_id
Revises: 009_ask_v2_observability
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_query_record_thread_id"
down_revision: str | None = "009_ask_v2_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_records",
        sa.Column("thread_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_query_records_thread_id", "query_records", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_query_records_thread_id", table_name="query_records")
    op.drop_column("query_records", "thread_id")
