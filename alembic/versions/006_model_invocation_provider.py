"""persist provider identity for model invocation observability

Revision ID: 006_model_invocation_provider
Revises: 005_business_query_plans
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_model_invocation_provider"
down_revision: str | None = "005_business_query_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_invocations",
        sa.Column("provider", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_invocations", "provider")
