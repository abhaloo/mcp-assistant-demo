"""Postgres-backed readiness probe for the Query Record projection."""

from __future__ import annotations

import secrets

from sqlalchemy import text

from app.db.postgres import get_async_engine

# Minimum migration the app requires; a newer head is a healthy schema.
QUERY_RECORD_MIN_ALEMBIC_REVISION = 10
QUERY_RECORD_EXPECTED_ALEMBIC_HEAD = "010_query_record_thread_id"


def _revision_number(head: object) -> int:
    """Numeric prefix of an alembic revision id; unknown shapes read as 0."""
    try:
        return int(str(head).split("_", 1)[0])
    except (TypeError, ValueError):
        return 0


QUERY_RECORD_REQUIRED_TABLES = frozenset(
    {
        "alembic_version",
        "query_records",
        "business_query_plans",
        "business_query_planner_attempt_events",
        "business_query_execution_events",
        "business_query_execution_detail_evidence",
        "sql_executions",
        "model_invocations",
    }
)
QUERY_RECORD_REQUIRED_COLUMNS = {
    "query_records": frozenset(
        {"correlation_id", "bq_trace_json", "plan_payload", "plan_expires_at", "thread_id"}
    ),
    "business_query_plans": frozenset({"answer_query_id", "plan_payload", "plan_fingerprint"}),
    "business_query_execution_events": frozenset(
        {"answer_query_id", "project_id", "metadata_json", "integrity_digest"}
    ),
    "business_query_execution_detail_evidence": frozenset(
        {
            "answer_query_id",
            "ordinal",
            "project_id",
            "detail_digest",
            "family",
            "owner_ref_digest",
            "revision_digest",
            "definition_digest",
            "profile_digest",
            "coverage_digest",
            "provenance_digest",
        }
    ),
    "sql_executions": frozenset({"correlation_id", "elapsed_ms", "row_count", "receipt_query_id"}),
    "model_invocations": frozenset(
        {
            "correlation_id",
            "purpose",
            "latency_ms",
            "model",
            "provider",
            "encrypted_payload",
            "payload_digest",
        }
    ),
}


class PostgresQueryRecordSchemaProbe:
    """State adapter for the readiness contract, injected at composition time."""

    async def check(self) -> bool:
        engine = get_async_engine()
        async with engine.connect() as conn:
            head = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            if _revision_number(head) < QUERY_RECORD_MIN_ALEMBIC_REVISION:
                return False

            table_rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            )
            tables = {str(row[0]) for row in table_rows}
            if not QUERY_RECORD_REQUIRED_TABLES.issubset(tables):
                return False

            column_rows = await conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
            )
            columns: dict[str, set[str]] = {}
            for table_name, column_name in column_rows:
                columns.setdefault(str(table_name), set()).add(str(column_name))
            schema_ready = all(
                required <= columns.get(table_name, set())
                for table_name, required in QUERY_RECORD_REQUIRED_COLUMNS.items()
            )
            if not schema_ready:
                return False

            # Prove the runtime role can perform the exact transactional
            # write/read/delete lifecycle used by Answered evidence. The probe
            # is rolled back, so readiness never leaves synthetic rows behind.
            probe_id = secrets.token_hex(16)
            await conn.execute(
                text(
                    "INSERT INTO query_records "
                    "(correlation_id, project_id, environment, terminal_outcome) "
                    "VALUES (:correlation_id, :project_id, :environment, :outcome)"
                ),
                {
                    "correlation_id": probe_id,
                    "project_id": "readiness-probe",
                    "environment": "readiness",
                    "outcome": "probe",
                },
            )
            inserted = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM query_records WHERE correlation_id = :correlation_id"
                    ),
                    {"correlation_id": probe_id},
                )
            ).scalar_one()
            await conn.execute(
                text("DELETE FROM query_records WHERE correlation_id = :correlation_id"),
                {"correlation_id": probe_id},
            )
            deleted = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM query_records WHERE correlation_id = :correlation_id"
                    ),
                    {"correlation_id": probe_id},
                )
            ).scalar_one()

            # Gate A: canary encrypt write/read/delete probe on model_invocations
            canary_cid = f"canary-{secrets.token_hex(8)}"
            await conn.execute(
                text(
                    "INSERT INTO model_invocations "
                    "(scope, correlation_id, purpose, request_messages, "
                    "encrypted_payload, payload_digest) "
                    "VALUES (:scope, :correlation_id, :purpose, :messages, :payload, :digest)"
                ),
                {
                    "scope": "test",
                    "correlation_id": canary_cid,
                    "purpose": "canary",
                    "messages": "[encrypted]",
                    "payload": '{"key_version":"canary","ciphertext":"canary_data"}',
                    "digest": "canary_digest",
                },
            )
            canary_row = (
                await conn.execute(
                    text(
                        "SELECT payload_digest FROM model_invocations WHERE correlation_id = :cid"
                    ),
                    {"cid": canary_cid},
                )
            ).scalar_one_or_none()
            await conn.execute(
                text("DELETE FROM model_invocations WHERE correlation_id = :cid"),
                {"cid": canary_cid},
            )

            # Historical plaintext count is a soft field (never fails check)
            try:
                self.plaintext_count = int(
                    (
                        await conn.execute(
                            text(
                                "SELECT COUNT(*) FROM model_invocations "
                                "WHERE encrypted_payload IS NULL"
                            )
                        )
                    ).scalar_one()
                )
            except Exception:
                self.plaintext_count = 0

            await conn.rollback()
            canary_ok = canary_row == "canary_digest"
            return inserted == 1 and deleted == 0 and canary_ok
