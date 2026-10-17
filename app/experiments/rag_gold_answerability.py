"""Independent gold answerability checks against frozen retrieval contexts."""

from __future__ import annotations

import re
from typing import Any

from app.experiments.live_errors import LiveRagEvalError
from app.guardrails.analyzer import detect_pii, get_anonymizer


def _redact_oracle_snippet(text: str) -> str:
    findings = detect_pii(text, id_context_filter=True)
    if not findings.findings:
        return text
    return (
        get_anonymizer()
        .anonymize(
            text=text,
            analyzer_results=findings.findings,
        )
        .text
    )


def _detected_phone_literals(text: str) -> list[str]:
    detection = detect_pii(text, id_context_filter=True)
    return [
        text[finding.start : finding.end]
        for finding in detection.findings
        if finding.entity_type == "PHONE_NUMBER"
    ]


def _normalize_evidence(text: str) -> str:
    return " ".join(re.sub(r"[^\w<>@.]+", " ", text.casefold()).split())


def _required_evidence_units(text: str) -> list[tuple[str, str]]:
    units = []
    for line in text.splitlines():
        content = re.sub(r"^\s*(?:(?:[-*+]\s+)|(?:\d+[.)]\s+)|(?:#+\s*))", "", line)
        normalized = _normalize_evidence(content)
        if normalized:
            units.append((content, normalized))
    return units or [(text, _normalize_evidence(text))]


def validate_frozen_gold_answerability(frozen_cases: list[dict[str, Any]]) -> None:
    """Require the redacted independent oracle snippet in every frozen retrieval."""
    failures: list[dict[str, Any]] = []
    for frozen_case in frozen_cases:
        case = frozen_case["case"]
        expected = str(case.get("expected_answer") or "")
        expected_snippet = str(case.get("expected_snippet") or "")
        question = str(case.get("question") or "").casefold()
        contexts = "\n".join(frozen_case["retrieval"].get("contexts") or [])
        if not expected_snippet:
            failures.append({"case_id": case.get("id"), "reason": "missing_expected_snippet"})
            continue
        redacted_snippet = _redact_oracle_snippet(expected_snippet)
        normalized_contexts = _normalize_evidence(contexts)
        missing_units = [
            original
            for original, normalized in _required_evidence_units(redacted_snippet)
            if normalized not in normalized_contexts
        ]
        if missing_units:
            failures.append(
                {
                    "case_id": case.get("id"),
                    "reason": "required_evidence_not_retrieved",
                    "missing_evidence_units": missing_units,
                }
            )
        context_phone_digits = {
            re.sub(r"\D", "", literal) for literal in _detected_phone_literals(contexts)
        }
        expected_phone_literals = _detected_phone_literals(expected_snippet)
        hidden_literals = [
            literal
            for literal in expected_phone_literals
            if re.sub(r"\D", "", literal) not in context_phone_digits
        ]
        asks_for_redacted_phone = bool(
            re.search(r"\b(?:phone|number|contact\s+information)\b", question, re.IGNORECASE)
        )
        placeholder_in_expected = "<PHONE_NUMBER>" in expected
        placeholder_in_context = "<PHONE_NUMBER>" in contexts
        raw_phone_leaked = any(
            re.sub(r"\D", "", literal) in context_phone_digits
            for literal in expected_phone_literals
        )
        if hidden_literals and "<PHONE_NUMBER>" not in redacted_snippet:
            failures.append({"case_id": case.get("id"), "reason": "redaction_policy_drift"})
        if asks_for_redacted_phone and placeholder_in_expected != placeholder_in_context:
            failures.append({"case_id": case.get("id"), "reason": "placeholder_oracle_mismatch"})
        if "<PHONE_NUMBER>" in redacted_snippet and raw_phone_leaked:
            failures.append({"case_id": case.get("id"), "reason": "raw_oracle_phone_leaked"})
    if failures:
        raise LiveRagEvalError(
            "gold answer is not answerable from redacted frozen context: " + repr(failures)
        )
