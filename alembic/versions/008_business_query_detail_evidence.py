"""add bounded redacted Business Query detail evidence projection

Revision ID: 008_bq_detail_evidence
Revises: 007_bq_plan_hash_widths
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008_bq_detail_evidence"
down_revision: str | None = "007_bq_plan_hash_widths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_query_execution_detail_evidence",
        sa.Column("answer_query_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("detail_digest", sa.String(64), nullable=False),
        sa.Column("family", sa.String(128), nullable=False),
        sa.Column("owner_resource", sa.String(64), nullable=True),
        sa.Column("owner_ref_digest", sa.String(64), nullable=True),
        sa.Column("revision_digest", sa.String(64), nullable=True),
        sa.Column("definition_digest", sa.String(64), nullable=True),
        sa.Column("profile_digest", sa.String(64), nullable=True),
        sa.Column("coverage_status", sa.String(64), nullable=True),
        sa.Column("coverage_digest", sa.String(64), nullable=True),
        sa.Column("provenance_digest", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal < 256",
            name="ck_bq_detail_evidence_ordinal",
        ),
        sa.PrimaryKeyConstraint("answer_query_id", "ordinal"),
    )
    op.create_index(
        "ix_bq_detail_evidence_project_answer",
        "business_query_execution_detail_evidence",
        ["project_id", "answer_query_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bq_detail_evidence_project_answer",
        table_name="business_query_execution_detail_evidence",
    )
    op.drop_table("business_query_execution_detail_evidence")
