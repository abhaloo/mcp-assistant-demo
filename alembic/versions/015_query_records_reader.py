"""add the read-only analysis role for query_records (ADR 0085)

Revision ID: 015_query_records_reader
Revises: 014_query_record_turn_content
Create Date: 2026-09-29

The role adds a reader and restricts no one: it can SELECT query_records and
cannot log in. An operator grants it to each login the owner names in ADR 0085
§ Who reads. CONNECT on the database and USAGE on schema public come from
PostgreSQL's grants to PUBLIC. A role belongs to the server, not to one
database, so the downgrade revokes the grant and leaves the role in place.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015_query_records_reader"
down_revision: str | None = "014_query_record_turn_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_records_reader') THEN
                CREATE ROLE query_records_reader NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT SELECT ON TABLE query_records TO query_records_reader")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE query_records FROM query_records_reader")
