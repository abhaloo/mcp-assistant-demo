"""persist Business Query plans used by signed result-page cursors

Revision ID: 005_business_query_plans
Revises: 004_invocation_ledger
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_business_query_plans"
down_revision: str | None = "004_invocation_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 004 introduced the Query Record trace block, while the ORM's plan
    # persistence fields were added afterward.  Keep the 004 -> 005 upgrade
    # usable for existing databases instead of relying on metadata.create_all.
    op.add_column(
        "query_records",
        sa.Column("plan_payload", sa.Text(), nullable=True),
    )
    op.add_column(
        "query_records",
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "business_query_plans",
        sa.Column("answer_query_id", sa.String(length=128), nullable=False),
        sa.Column("plan_payload", sa.Text(), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("principal", sa.String(length=128), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("bundle_hash", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("answer_query_id"),
    )
    op.create_index(
        "ix_bq_plans_expires_at",
        "business_query_plans",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_bq_plans_expires_at", table_name="business_query_plans")
    op.drop_table("business_query_plans")
    op.drop_column("query_records", "plan_expires_at")
    op.drop_column("query_records", "plan_payload")
