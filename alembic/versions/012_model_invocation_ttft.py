"""add ttft_ms to model_invocations for coordinator telemetry

Revision ID: 012_model_invocation_ttft
Revises: 011_ask_evidence_snapshots
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012_model_invocation_ttft"
down_revision: str | None = "011_ask_evidence_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_invocations",
        sa.Column("ttft_ms", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_invocations", "ttft_ms")
