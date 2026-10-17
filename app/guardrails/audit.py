"""PII audit logging — metadata only, no plaintext spans."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings


def hash_span(text: str) -> str:
    """
    HMAC-SHA256 of a PII span, hex-encoded.

    Used in audit logs so we can identify recurring detections (same value
    seen N times) without storing plaintext PII in the log itself.
    """
    h = hmac.new(
        key=settings.redaction_hmac_key.encode("utf-8"),
        msg=text.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return h.hexdigest()


def audit_log(
    query_id: str,
    source: str,
    entity_type: str,
    score: float,
    span_hash: str,
    *,
    category: str = "",
    sensitivity: str = "",
    subject: str = "",
) -> None:
    """Append a single redaction event to the JSONL audit log. Register
    metadata (category/sensitivity/subject) is recorded when provided."""
    log_path = Path(settings.redaction_audit_log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "query_id": query_id,
        "source": source,
        "entity_type": entity_type,
        "score": score,
        "span_hash": span_hash,
    }
    if category:
        entry["category"] = category
    if sensitivity:
        entry["sensitivity"] = sensitivity
    if subject:
        entry["subject"] = subject

    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def audit_findings(query_id: str, source: str, text: str, findings) -> None:
    """One audit row per finding — span hashed, never plaintext."""
    for f in findings:
        audit_log(query_id, source, f.entity_type, f.score, hash_span(text[f.start : f.end]))


def audit_bq_pii_exposure(
    correlation_id: str,
    *,
    member: str,
    table: str,
    column: str,
    treatment: str,
    category: str = "",
    sensitivity: str = "",
    subject: str = "",
) -> None:
    """Record a plan-derived BQ PII exposure — metadata only, no row values."""
    log_path = Path(settings.redaction_audit_log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "query_id": correlation_id,
        "source": "bq_answer",
        "event_type": "pii_exposure",
        "member": member,
        "table": table,
        "column": column,
        "treatment": treatment.lower(),
    }
    if category:
        entry["category"] = category
    if sensitivity:
        entry["sensitivity"] = sensitivity
    if subject:
        entry["subject"] = subject

    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def emit_bq_pii_audit_for_plan(
    correlation_id: str,
    plan,
    bundle,
) -> int:
    """Emit one audit row per register-classified plan member. Returns emit count."""
    from app.business_query.bundle_lineage import classified_columns_for_plan

    emitted = 0
    for classified in classified_columns_for_plan(plan, bundle):
        audit_bq_pii_exposure(
            correlation_id,
            member=classified.member,
            table=classified.table,
            column=classified.column,
            treatment=classified.treatment.value,
            category=classified.policy.category,
            sensitivity=classified.policy.sensitivity,
            subject=classified.policy.subject,
        )
        emitted += 1
    return emitted


# Dispositions where render_rich may already have sent real rows to the
# provider before the Ask-layer outcome was decided. Shadow mode runs the
# renderer unconditionally over the sealed Answered, then discards the
# answer text -- the exposure already happened, so it must be audited too.
# Every other disposition never reaches render_rich with a plan, so
# `plan is not None` alone would already exclude them.
_EXPOSURE_DISPOSITIONS = frozenset({"answered", "shadowed"})


def emit_bq_pii_audit_for_answered(
    correlation_id: str,
    *,
    disposition: str,
    plan,
    bundle,
) -> int:
    """Finish-policy call site: audit any turn where render_rich may have
    exposed real rows to the provider -- answered, or shadow (see
    ``_EXPOSURE_DISPOSITIONS``)."""
    if disposition not in _EXPOSURE_DISPOSITIONS or plan is None:
        return 0
    return emit_bq_pii_audit_for_plan(correlation_id, plan, bundle)
