"""add cached_input_tokens column to model_invocations

Revision ID: 016_invocation_cached_input
Revises: 015_query_records_reader
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016_invocation_cached_input"
down_revision: str | None = "015_query_records_reader"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_invocations",
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_invocations", "cached_input_tokens")
