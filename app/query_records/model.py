"""SQLAlchemy model for the Query Record table."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class QueryRecordRow(Base):
    __tablename__ = "query_records"
    __table_args__ = (
        UniqueConstraint("correlation_id", name="uq_query_records_correlation_id"),
        Index("ix_query_records_project_created_at", "project_id", "created_at"),
        Index("ix_query_records_project_subject_digest", "project_id", "subject_digest"),
        Index("ix_query_records_project_retention_at", "project_id", "retention_at"),
        Index("ix_query_records_project_terminal_outcome", "project_id", "terminal_outcome"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Identity
    correlation_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # RLS deferred — tenancy enforced in repository WHERE clauses until a second project exists.
    project_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Tenant key; RLS policy deferred — filter enforced in repository.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Content
    redacted_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_question: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Release
    billing_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rag_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    service_versions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Contracts
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_versions: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_table_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Configuration modes
    record_dispatch_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filter_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analytics_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conversation_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    citation_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Client conversation thread the turn belongs to; groups multi-turn threads.
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # User context — subject_digest stores the full 64-char HMAC hex (not truncated).
    subject_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Routing
    requested_route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    route_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fallback_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Experience
    ui_first_text_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completion_latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    terminal_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Usage
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Grounding
    retrieved_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_source_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    citation_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    invalid_citation_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unbound_citation_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    missing_binding_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repair_attempted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    finalization_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Structured records
    record_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sql_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sanitized_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    sql_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projected_views: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Reliability
    retry_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cancelled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timeout: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stable_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolver_query_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolver_disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # BQ trace block: sql/sql_ms/planner_ms/planner_repair_count/
    # rows_returned/failure_layer, JSON-encoded. "sql" is the full statement
    # with values -- safe here because query_records is the same owned
    # Postgres instance as the invocation ledger, not a git-committed artifact.
    bq_trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plan persistence: normalized BusinessQueryPlan and TTL expiration
    plan_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Late writes
    feedback_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluation_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BusinessQueryPlanRow(Base):
    __tablename__ = "business_query_plans"
    __table_args__ = (Index("ix_bq_plans_expires_at", "expires_at"),)

    answer_query_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_payload: Mapped[str] = mapped_column(Text, nullable=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    principal: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bundle_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)


class BusinessQueryExecutionEventRow(Base):
    __tablename__ = "business_query_execution_events"
    __table_args__ = (
        UniqueConstraint("answer_query_id", name="uq_bq_execution_event_answer_query_id"),
        Index("ix_bq_execution_event_project_retention", "project_id", "retention_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    answer_query_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    integrity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessQueryExecutionDetailEvidenceRow(Base):
    """Redacted, bounded detail projection for support/evidence joins."""

    __tablename__ = "business_query_execution_detail_evidence"
    __table_args__ = (
        CheckConstraint("ordinal >= 0 AND ordinal < 256", name="ck_bq_detail_evidence_ordinal"),
        Index(
            "ix_bq_detail_evidence_project_answer",
            "project_id",
            "answer_query_id",
        ),
    )

    answer_query_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ordinal: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_resource: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_ref_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    definition_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coverage_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessQueryExecutionEventPayloadRow(Base):
    __tablename__ = "business_query_execution_event_payloads"

    answer_query_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessQueryExecutionEventTombstoneRow(Base):
    __tablename__ = "business_query_execution_event_tombstones"

    answer_query_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    tombstoned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessQueryPlannerAttemptRow(Base):
    __tablename__ = "business_query_planner_attempt_events"
    __table_args__ = (
        UniqueConstraint("attempt_id", "event_kind", name="uq_bq_planner_attempt_kind"),
        UniqueConstraint(
            "idempotency_key", "event_kind", name="uq_bq_planner_attempt_idempotency_kind"
        ),
        Index("ix_bq_planner_attempt_project_run", "project_id", "run_epoch"),
        CheckConstraint(
            "event_kind IN ('started', 'response_committed', 'terminal')",
            name="ck_bq_planner_attempt_event_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    repeat_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    planner_call_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessQueryPlannerAttemptLeaseRow(Base):
    __tablename__ = "business_query_planner_attempt_leases"

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessQueryCaseRepeatRow(Base):
    __tablename__ = "business_query_case_repeat_events"
    __table_args__ = (
        UniqueConstraint("case_repeat_id", "event_kind", name="uq_bq_case_repeat_kind"),
        UniqueConstraint(
            "idempotency_key", "event_kind", name="uq_bq_case_repeat_idempotency_kind"
        ),
        UniqueConstraint(
            "project_id",
            "run_epoch",
            "case_id",
            "repeat_index",
            "event_kind",
            name="uq_bq_case_repeat_identity_kind",
        ),
        Index("ix_bq_case_repeat_project_run", "project_id", "run_epoch"),
        CheckConstraint("repeat_index >= 0", name="ck_bq_case_repeat_nonnegative"),
        CheckConstraint(
            "event_kind IN ('started', 'terminal')", name="ck_bq_case_repeat_event_kind"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_repeat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    repeat_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessQueryCaseRepeatLeaseRow(Base):
    __tablename__ = "business_query_case_repeat_leases"

    case_repeat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessQueryResolverEventRow(Base):
    __tablename__ = "business_query_resolver_events"
    __table_args__ = (
        UniqueConstraint("resolver_query_id", "event_kind", name="uq_bq_resolver_event_id_kind"),
        Index("ix_bq_resolver_event_project", "project_id"),
        CheckConstraint("event_kind IN ('started')", name="ck_bq_resolver_event_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resolver_query_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Columns sortable via the read API.
SORTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "correlation_id",
        "project_id",
        "created_at",
        "environment",
        "retention_at",
        "redacted_question",
        "question_fingerprint",
        "raw_question",
        "billing_commit",
        "rag_commit",
        "service_versions",
        "manifest_hash",
        "prompt_versions",
        "price_table_version",
        "record_dispatch_mode",
        "filter_mode",
        "analytics_mode",
        "conversation_mode",
        "citation_mode",
        "subject_digest",
        "entity_digest",
        "role_class",
        "context_mode",
        "requested_route",
        "effective_route",
        "route_reason",
        "fallback_flag",
        "ui_first_text_ms",
        "completion_latency_ms",
        "terminal_outcome",
        "model",
        "provider",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "estimated_usd",
        "cost_status",
        "retrieved_count",
        "used_source_count",
        "citation_count",
        "invalid_citation_count",
        "unbound_citation_count",
        "missing_binding_count",
        "repair_attempted",
        "finalization_outcome",
        "record_outcome",
        "sql_present",
        "sanitized_sql",
        "sql_fingerprint",
        "projected_views",
        "operation_class",
        "policy_verdict",
        "row_count",
        "retry_count",
        "cancelled",
        "timeout",
        "stable_error_code",
        "feedback_verdict",
        "feedback_at",
        "frozen_case_id",
        "evaluation_result",
        "evaluation_at",
    }
)

# Always-on columns for security tests (raw_question excluded -- gated).
ALWAYS_ON_TEXT_COLUMNS: frozenset[str] = frozenset(
    col
    for col in (
        "redacted_question",
        "question_fingerprint",
        "billing_commit",
        "rag_commit",
        "service_versions",
        "manifest_hash",
        "prompt_versions",
        "price_table_version",
        "record_dispatch_mode",
        "filter_mode",
        "analytics_mode",
        "conversation_mode",
        "citation_mode",
        "subject_digest",
        "entity_digest",
        "role_class",
        "context_mode",
        "requested_route",
        "effective_route",
        "route_reason",
        "terminal_outcome",
        "model",
        "provider",
        "cost_status",
        "finalization_outcome",
        "record_outcome",
        "sanitized_sql",
        "sql_fingerprint",
        "projected_views",
        "operation_class",
        "policy_verdict",
        "stable_error_code",
        "feedback_verdict",
        "frozen_case_id",
        "evaluation_result",
    )
)
