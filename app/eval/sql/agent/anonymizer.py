"""PII anonymization mapping and tokenization for the legacy SQL agent."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from presidio_anonymizer.entities import OperatorConfig

from app.guardrails.analyzer import detect_pii, get_anonymizer
from app.guardrails.audit import audit_findings, audit_log, hash_span
from app.guardrails.pii_register import (
    REGISTER_TABLES,
    FieldPolicy,
    Treatment,
    resolve_policy,
)
from app.guardrails.sql_anonymizer import (
    AnonymizerStats,
    SqlExecutionRecord,
)
from app.rag.tier_scope import TierScope

if TYPE_CHECKING:
    pass

PRIVILEGED_ROLES = {"admin", "finance"}
REDACT_MARKER = "[REDACTED]"
SUPPRESS_MARKER = "[SUPPRESSED]"


def _policy_kw(policy: FieldPolicy | None) -> dict:
    """Audit metadata kwargs from a matched register policy (empty if none)."""
    if policy is None:
        return {}
    return {
        "category": policy.category,
        "sensitivity": policy.sensitivity,
        "subject": policy.subject,
    }


# Primary-key and display-label columns per allowlisted table (deanonymized at boundary).
_TABLE_ID_COLUMNS: dict[str, tuple[str, ...]] = {
    "bills": ("id",),
    "customers": ("id",),
    "customer_orders": ("id",),
    "work_orders": ("id",),
}
_TABLE_LABEL_COLUMNS: dict[str, tuple[str, ...]] = {
    "bills": ("bill_no", "number", "title"),
    "customers": ("name", "company_name"),
    "customer_orders": ("order_no", "number"),
    "work_orders": ("work_no", "number", "title"),
}


class SqlAnonymizer:
    """
    Per-request reversible tokenization for the SQL agent path.

    Lifecycle: one instance per /api/ask request. Holds the entity map
    for the request lifetime; goes out of scope at response return.
    No cross-request leakage by construction.
    """

    def __init__(
        self,
        query_id: str,
        role: str,
        *,
        scope: TierScope | None = None,
        strict_tier_scope: bool = False,
        ner_enabled: bool = True,
    ):
        """TierScope determines whether role widens authorization."""
        self.query_id = query_id
        self.role = role
        is_wildcard = (not strict_tier_scope) if scope is None else bool(scope.is_wildcard)
        self._privileged_pseudonymize = role in PRIVILEGED_ROLES and is_wildcard
        self._ner_enabled = ner_enabled
        self._mapping: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._counter: dict[str, int] = {}
        self.stats = AnonymizerStats()
        self.execution_records: list[SqlExecutionRecord] = []

    def clear_execution_records(self) -> None:
        self.execution_records.clear()

    def record_result_rows(self, sql: str, rows: list[dict]) -> None:
        """Capture successful normalized executions for deterministic record links."""
        if not isinstance(rows, list) or not rows:
            return
        from app.eval.sql.agent.anonymizing_database import _tables_in

        tables = _tables_in(str(sql))
        if len(tables) != 1:
            return
        table = next(iter(tables))
        if table not in _TABLE_ID_COLUMNS:
            return
        id_cols = _TABLE_ID_COLUMNS[table]
        label_cols = _TABLE_LABEL_COLUMNS.get(table, ())
        for row in rows:
            if not isinstance(row, dict):
                continue
            record_id: int | None = None
            for col in id_cols:
                raw = row.get(col)
                if isinstance(raw, int) and raw > 0:
                    record_id = raw
                    break
                if isinstance(raw, str) and raw.isdigit():
                    record_id = int(raw)
                    break
            if record_id is None:
                continue
            label = ""
            for col in label_cols:
                raw = row.get(col)
                if isinstance(raw, str) and raw.strip():
                    label = raw.strip()
                    break
            if not label:
                label = f"{table} #{record_id}"
            self.execution_records.append(
                SqlExecutionRecord(table=table, record_id=record_id, label=label)
            )

    def _token_for(self, original: str, entity_type: str) -> str:
        """Return the stable token for `original`, generating one if new."""
        existing = self._reverse.get(original)
        if existing is not None:
            return existing
        self._counter[entity_type] = self._counter.get(entity_type, 0) + 1
        token = f"<{entity_type}_{self._counter[entity_type]}>"
        self._mapping[token] = original
        self._reverse[original] = token
        self.stats.tokens_created += 1
        return token

    def tokenize_value(
        self,
        value: str,
        entity_type: str,
        source: str = "forced",
        *,
        policy: FieldPolicy | None = None,
    ) -> str:
        token = self._token_for(value, entity_type)
        audit_log(
            query_id=self.query_id,
            source=source,
            entity_type=entity_type,
            score=1.0,
            span_hash=hash_span(value),
            **_policy_kw(policy),
        )
        return token

    def redact_value(
        self,
        value,
        *,
        source: str = "redact",
        policy: FieldPolicy | None = None,
    ) -> str:
        self.stats.redacted_values += 1
        audit_log(
            query_id=self.query_id,
            source=source,
            entity_type="REDACT",
            score=1.0,
            span_hash=hash_span(str(value)),
            **_policy_kw(policy),
        )
        return REDACT_MARKER

    def suppress_value(
        self,
        value,
        *,
        source: str = "suppress",
        policy: FieldPolicy | None = None,
    ) -> str:
        self.stats.suppressed_values += 1
        audit_log(
            query_id=self.query_id,
            source=source,
            entity_type="SUPPRESS",
            score=1.0,
            span_hash=hash_span(str(value)),
            **_policy_kw(policy),
        )
        return SUPPRESS_MARKER

    def anonymize(self, text: str, source: str = "unknown") -> str:
        """
        Detect PII in `text`, replace with stable tokens, log to audit.

        `source` labels the call site for the audit trail (e.g.
        "sql_question", "sql_result", "sql_answer"). Returns the
        tokenized text. Empty input returns unchanged.
        """
        if not text or not self._ner_enabled:
            return text

        self.stats.anonymize_calls += 1
        anonymizer = get_anonymizer()

        detection = detect_pii(text, id_context_filter=False)
        findings = detection.findings
        if not findings:
            return text

        self.stats.entities_detected += len(findings)
        operators = {
            ent: OperatorConfig("custom", {"lambda": lambda x, e=ent: self._token_for(x, e)})
            for ent in {f.entity_type for f in findings}
        }

        result = anonymizer.anonymize(text=text, analyzer_results=findings, operators=operators)
        audit_findings(self.query_id, source, text, findings)

        return result.text

    def anonymize_rows(self, rows: list[dict], tables: set[str]) -> list[dict]:
        """Apply each cell's register treatment before rows enter the agent scratchpad.
        Empty `tables` → resolve against ALL register tables (fail-safe)."""
        if not rows:
            return rows
        keys = {t.lower() for t in tables} or set(REGISTER_TABLES)
        out: list[dict] = []
        for row in rows:
            new_row: dict = {}
            for col, value in row.items():
                policy = resolve_policy(col, keys)

                if policy is None:
                    if value is None or not isinstance(value, str) or not self._ner_enabled:
                        new_row[col] = value
                    else:
                        new_row[col] = self.anonymize(value, source=f"sql_detected:{col}")
                    continue

                if value is None:
                    new_row[col] = None
                elif policy.treatment is Treatment.SUPPRESS:
                    new_row[col] = self.suppress_value(
                        value, source=f"sql_suppress:{col}", policy=policy
                    )
                elif policy.treatment is Treatment.REDACT:
                    new_row[col] = self.redact_value(
                        value, source=f"sql_redact:{col}", policy=policy
                    )
                elif policy.treatment is Treatment.PSEUDONYMIZE and self._privileged_pseudonymize:
                    new_row[col] = value
                elif not isinstance(value, str):
                    new_row[col] = value
                else:
                    new_row[col] = self.tokenize_value(
                        value,
                        policy.entity_type,
                        source=f"sql_forced:{col}",
                        policy=policy,
                    )
            out.append(new_row)
        return out

    def deanonymize(self, text: str) -> str:
        """
        Replace any tokens in `text` with their original values.

        Single regex pass so each character is consumed at most once.
        Tokens are sorted by length descending so that <PERSON_10> is
        tried before <PERSON_1>.
        """
        if not text or not self._mapping:
            return text
        sorted_tokens = sorted(self._mapping.keys(), key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(t) for t in sorted_tokens))
        return pattern.sub(lambda m: self._mapping[m.group(0)], text)
