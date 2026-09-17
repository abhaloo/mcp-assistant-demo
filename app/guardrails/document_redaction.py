"""One-way PII redaction for retrieved document chunks (RAG path)."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.documents import Document

from app.guardrails.analyzer import detect_pii, get_anonymizer
from app.guardrails.audit import audit_findings
from app.telemetry.context import current_query_id


@dataclass(frozen=True)
class RedactionStats:
    docs_in: int
    docs_affected: int
    entities_detected: int
    entities_redacted: int
    id_context_suppressed: int


@dataclass(frozen=True)
class RedactionResult:
    documents: list[Document]
    stats: RedactionStats


def redact_documents_with_stats(docs: list[Document]) -> RedactionResult:
    """
    Redact PII from each document's page_content.

    Returns new Documents with redacted content and metadata enriched with
    a "redactions" key summarizing what was stripped. Original docs are not
    mutated. Writes one audit log entry per detection.

    If a doc has no detections, it's returned unchanged.
    """
    if not docs:
        stats = RedactionStats(0, 0, 0, 0, 0)
        return RedactionResult(documents=[], stats=stats)

    anonymizer = get_anonymizer()
    query_id = current_query_id()

    redacted_docs: list[Document] = []
    docs_affected = 0
    entities_detected = 0
    entities_redacted = 0
    id_context_suppressed = 0

    for doc in docs:
        source = doc.metadata.get("source", "unknown")

        detection = detect_pii(doc.page_content, id_context_filter=True)
        entities_detected += detection.raw_count
        id_context_suppressed += detection.id_context_suppressed
        findings = detection.findings

        if not findings:
            redacted_docs.append(doc)
            continue

        entities_redacted += len(findings)
        docs_affected += 1

        anonymize_result = anonymizer.anonymize(text=doc.page_content, analyzer_results=findings)
        redacted_text = anonymize_result.text

        redaction_summary = []
        for finding in findings:
            redaction_summary.append(
                {
                    "entity_type": finding.entity_type,
                    "score": finding.score,
                }
            )

        audit_findings(query_id, source, doc.page_content, findings)

        new_doc = Document(
            page_content=redacted_text,
            metadata={**doc.metadata, "redactions": redaction_summary},
        )

        redacted_docs.append(new_doc)

    stats = RedactionStats(
        docs_in=len(docs),
        docs_affected=docs_affected,
        entities_detected=entities_detected,
        entities_redacted=entities_redacted,
        id_context_suppressed=id_context_suppressed,
    )
    return RedactionResult(documents=redacted_docs, stats=stats)
