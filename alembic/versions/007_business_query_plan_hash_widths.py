"""widen Business Query plan hash columns for canonical prefixed digests

Revision ID: 007_bq_plan_hash_widths
Revises: 006_model_invocation_provider
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_bq_plan_hash_widths"
down_revision: str | None = "006_model_invocation_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_HASH_COLUMNS = ("plan_fingerprint", "manifest_hash", "bundle_hash")


def upgrade() -> None:
    for column_name in _HASH_COLUMNS:
        op.alter_column(
            "business_query_plans",
            column_name,
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=(column_name != "plan_fingerprint"),
        )


def downgrade() -> None:
    for column_name in reversed(_HASH_COLUMNS):
        op.alter_column(
            "business_query_plans",
            column_name,
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=(column_name != "plan_fingerprint"),
        )
