"""add the turn content columns to query_records (ADR 0085)

Revision ID: 014_query_record_turn_content
Revises: 013_records_entity_created_at
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014_query_record_turn_content"
down_revision: str | None = "013_records_entity_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("query_records", sa.Column("exchange_id", sa.String(length=64), nullable=True))
    op.add_column("query_records", sa.Column("operation", sa.String(length=32), nullable=True))
    op.add_column("query_records", sa.Column("answer_text", sa.Text(), nullable=True))
    op.add_column("query_records", sa.Column("turn_detail_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("query_records", "turn_detail_json")
    op.drop_column("query_records", "answer_text")
    op.drop_column("query_records", "operation")
    op.drop_column("query_records", "exchange_id")
