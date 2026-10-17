"""add append-only Business Query evidence tables

Revision ID: 002_bq_evidence
Revises: 001_query_records
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_bq_evidence"
down_revision: str | None = "001_query_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_query_execution_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("answer_query_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("payload_classification", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.LargeBinary(), nullable=False),
        sa.Column("integrity_digest", sa.String(64), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("retention_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("answer_query_id", name="uq_bq_execution_event_answer_query_id"),
    )
    op.create_index(
        "ix_bq_execution_event_project_retention",
        "business_query_execution_events",
        ["project_id", "retention_at"],
    )
    op.create_table(
        "business_query_execution_event_payloads",
        sa.Column("answer_query_id", sa.String(128), nullable=False),
        sa.Column("payload_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("payload_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("payload_key_version", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("answer_query_id"),
    )
    op.create_table(
        "business_query_execution_event_tombstones",
        sa.Column("answer_query_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("reason_digest", sa.String(64), nullable=False),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("answer_query_id"),
    )

    op.create_table(
        "business_query_planner_attempt_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("run_epoch", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("repeat_index", sa.BigInteger(), nullable=False),
        sa.Column("planner_call_index", sa.BigInteger(), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("event_json", sa.LargeBinary(), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_kind IN ('started', 'response_committed', 'terminal')",
            name="ck_bq_planner_attempt_event_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "event_kind", name="uq_bq_planner_attempt_kind"),
        sa.UniqueConstraint(
            "idempotency_key", "event_kind", name="uq_bq_planner_attempt_idempotency_kind"
        ),
    )
    op.create_index(
        "ix_bq_planner_attempt_project_run",
        "business_query_planner_attempt_events",
        ["project_id", "run_epoch"],
    )
    op.create_table(
        "business_query_planner_attempt_leases",
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("attempt_id"),
    )

    op.create_table(
        "business_query_case_repeat_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("case_repeat_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("run_epoch", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("repeat_index", sa.BigInteger(), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("event_json", sa.LargeBinary(), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("repeat_index >= 0", name="ck_bq_case_repeat_nonnegative"),
        sa.CheckConstraint(
            "event_kind IN ('started', 'terminal')", name="ck_bq_case_repeat_event_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_repeat_id", "event_kind", name="uq_bq_case_repeat_kind"),
        sa.UniqueConstraint(
            "idempotency_key", "event_kind", name="uq_bq_case_repeat_idempotency_kind"
        ),
        sa.UniqueConstraint(
            "project_id",
            "run_epoch",
            "case_id",
            "repeat_index",
            "event_kind",
            name="uq_bq_case_repeat_identity_kind",
        ),
    )
    op.create_index(
        "ix_bq_case_repeat_project_run",
        "business_query_case_repeat_events",
        ["project_id", "run_epoch"],
    )
    op.create_table(
        "business_query_case_repeat_leases",
        sa.Column("case_repeat_id", sa.String(128), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("case_repeat_id"),
    )


def downgrade() -> None:
    op.drop_table("business_query_case_repeat_leases")
    op.drop_index("ix_bq_case_repeat_project_run", table_name="business_query_case_repeat_events")
    op.drop_table("business_query_case_repeat_events")
    op.drop_table("business_query_planner_attempt_leases")
    op.drop_index(
        "ix_bq_planner_attempt_project_run", table_name="business_query_planner_attempt_events"
    )
    op.drop_table("business_query_planner_attempt_events")
    op.drop_table("business_query_execution_event_tombstones")
    op.drop_table("business_query_execution_event_payloads")
    op.drop_index(
        "ix_bq_execution_event_project_retention",
        table_name="business_query_execution_events",
    )
    op.drop_table("business_query_execution_events")
