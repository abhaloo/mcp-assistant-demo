"""add entity_id and ix_query_records_entity_created_at to query_records

Revision ID: 013_records_entity_created_at
Revises: 012_model_invocation_ttft
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013_records_entity_created_at"
down_revision: str | None = "012_model_invocation_ttft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_records",
        sa.Column("entity_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_query_records_entity_created_at",
        "query_records",
        ["entity_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_query_records_entity_created_at", table_name="query_records")
    op.drop_column("query_records", "entity_id")
