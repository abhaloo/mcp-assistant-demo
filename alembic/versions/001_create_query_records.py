"""create query_records table

Revision ID: 001_query_records
Revises:
Create Date: 2026-07-31

Hand-written DDL (autogenerate disabled). RLS deferred; project_id filter in repository.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_query_records"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("correlation_id", sa.String(length=32), nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=64),
            nullable=False,
            comment="Tenant key; RLS policy deferred — filter enforced in repository.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("retention_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_question", sa.Text(), nullable=True),
        sa.Column("question_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("raw_question", sa.Text(), nullable=True),
        sa.Column("billing_commit", sa.String(length=40), nullable=True),
        sa.Column("rag_commit", sa.String(length=40), nullable=True),
        sa.Column("service_versions", sa.Text(), nullable=True),
        sa.Column("manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_versions", sa.Text(), nullable=True),
        sa.Column("price_table_version", sa.String(length=64), nullable=True),
        sa.Column("record_dispatch_mode", sa.String(length=32), nullable=True),
        sa.Column("filter_mode", sa.String(length=32), nullable=True),
        sa.Column("analytics_mode", sa.String(length=32), nullable=True),
        sa.Column("conversation_mode", sa.String(length=32), nullable=True),
        sa.Column("citation_mode", sa.String(length=32), nullable=True),
        sa.Column("subject_digest", sa.String(length=64), nullable=True),
        sa.Column("entity_digest", sa.String(length=64), nullable=True),
        sa.Column("role_class", sa.String(length=64), nullable=True),
        sa.Column("context_mode", sa.String(length=32), nullable=True),
        sa.Column("requested_route", sa.String(length=32), nullable=True),
        sa.Column("effective_route", sa.String(length=32), nullable=True),
        sa.Column("route_reason", sa.String(length=128), nullable=True),
        sa.Column("fallback_flag", sa.Boolean(), nullable=True),
        sa.Column("ui_first_text_ms", sa.BigInteger(), nullable=True),
        sa.Column("completion_latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("terminal_outcome", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("estimated_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("cost_status", sa.String(length=16), nullable=True),
        sa.Column("retrieved_count", sa.BigInteger(), nullable=True),
        sa.Column("used_source_count", sa.BigInteger(), nullable=True),
        sa.Column("citation_count", sa.BigInteger(), nullable=True),
        sa.Column("invalid_citation_count", sa.BigInteger(), nullable=True),
        sa.Column("unbound_citation_count", sa.BigInteger(), nullable=True),
        sa.Column("missing_binding_count", sa.BigInteger(), nullable=True),
        sa.Column("repair_attempted", sa.Boolean(), nullable=True),
        sa.Column("finalization_outcome", sa.String(length=32), nullable=True),
        sa.Column("record_outcome", sa.String(length=32), nullable=True),
        sa.Column("sql_present", sa.Boolean(), nullable=True),
        sa.Column("sanitized_sql", sa.Text(), nullable=True),
        sa.Column("sql_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("projected_views", sa.Text(), nullable=True),
        sa.Column("operation_class", sa.String(length=32), nullable=True),
        sa.Column("policy_verdict", sa.String(length=32), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("retry_count", sa.BigInteger(), nullable=True),
        sa.Column("cancelled", sa.Boolean(), nullable=True),
        sa.Column("timeout", sa.Boolean(), nullable=True),
        sa.Column("stable_error_code", sa.String(length=64), nullable=True),
        sa.Column("feedback_verdict", sa.String(length=32), nullable=True),
        sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_case_id", sa.String(length=128), nullable=True),
        sa.Column("evaluation_result", sa.String(length=32), nullable=True),
        sa.Column("evaluation_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id", name="uq_query_records_correlation_id"),
    )
    op.create_index(
        "ix_query_records_project_created_at",
        "query_records",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_query_records_project_subject_digest",
        "query_records",
        ["project_id", "subject_digest"],
    )
    op.create_index(
        "ix_query_records_project_retention_at",
        "query_records",
        ["project_id", "retention_at"],
    )
    op.create_index(
        "ix_query_records_project_terminal_outcome",
        "query_records",
        ["project_id", "terminal_outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_query_records_project_terminal_outcome", table_name="query_records")
    op.drop_index("ix_query_records_project_retention_at", table_name="query_records")
    op.drop_index("ix_query_records_project_subject_digest", table_name="query_records")
    op.drop_index("ix_query_records_project_created_at", table_name="query_records")
    op.drop_table("query_records")
