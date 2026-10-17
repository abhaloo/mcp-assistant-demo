"""invocation ledger tables + query_records bq_trace_json

Revision ID: 004_invocation_ledger
Revises: 003_bq_resolver
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004_invocation_ledger"
down_revision: str | None = "003_bq_resolver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_invocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("route_key", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("request_messages", sa.Text(), nullable=False),
        sa.Column("response_content", sa.Text(), nullable=True),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("estimated_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("cost_status", sa.String(length=16), nullable=True),
        sa.Column("error_class", sa.String(length=128), nullable=True),
        sa.Column("pii_posture", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_invocations_scope_created",
        "model_invocations",
        ["scope", "created_at"],
    )
    op.create_index(
        "ix_model_invocations_correlation",
        "model_invocations",
        ["correlation_id"],
    )

    op.create_table(
        "sql_executions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("elapsed_ms", sa.BigInteger(), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column(
            "adapter",
            sa.String(length=32),
            nullable=False,
            server_default="internal_compiler",
        ),
        sa.Column("receipt_query_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sql_executions_scope_created",
        "sql_executions",
        ["scope", "created_at"],
    )
    op.create_index(
        "ix_sql_executions_correlation",
        "sql_executions",
        ["correlation_id"],
    )

    op.add_column(
        "query_records",
        sa.Column("bq_trace_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_records", "bq_trace_json")
    op.drop_index("ix_sql_executions_correlation", table_name="sql_executions")
    op.drop_index("ix_sql_executions_scope_created", table_name="sql_executions")
    op.drop_table("sql_executions")
    op.drop_index("ix_model_invocations_correlation", table_name="model_invocations")
    op.drop_index("ix_model_invocations_scope_created", table_name="model_invocations")
    op.drop_table("model_invocations")
