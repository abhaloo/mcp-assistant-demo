"""add Business Query resolver started journal and Query Record lookup columns

Revision ID: 003_bq_resolver
Revises: 002_bq_evidence
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003_bq_resolver"
down_revision: str | None = "002_bq_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_query_resolver_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("resolver_query_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("event_json", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resolver_query_id", "event_kind", name="uq_bq_resolver_event_id_kind"),
        sa.CheckConstraint("event_kind IN ('started')", name="ck_bq_resolver_event_kind"),
    )
    op.create_index(
        "ix_bq_resolver_event_project",
        "business_query_resolver_events",
        ["project_id"],
    )
    op.add_column(
        "query_records",
        sa.Column("resolver_query_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "query_records",
        sa.Column("resolver_disposition", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_records", "resolver_disposition")
    op.drop_column("query_records", "resolver_query_id")
    op.drop_index("ix_bq_resolver_event_project", table_name="business_query_resolver_events")
    op.drop_table("business_query_resolver_events")
