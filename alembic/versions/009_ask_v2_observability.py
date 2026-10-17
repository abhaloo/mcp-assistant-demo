"""add encrypted payload columns to model_invocations for ask v2 observability

Revision ID: 009_ask_v2_observability
Revises: 008_bq_detail_evidence
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_ask_v2_observability"
down_revision: str | None = "008_bq_detail_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_invocations",
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
    )
    op.add_column(
        "model_invocations",
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "model_invocations",
        sa.Column("key_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "model_invocations",
        sa.Column("nonce", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_model_invocations_payload_digest",
        "model_invocations",
        ["payload_digest"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_invocations_payload_digest", table_name="model_invocations")
    op.drop_column("model_invocations", "nonce")
    op.drop_column("model_invocations", "key_version")
    op.drop_column("model_invocations", "payload_digest")
    op.drop_column("model_invocations", "encrypted_payload")
