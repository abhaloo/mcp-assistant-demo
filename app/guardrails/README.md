# Guardrails

Cross-cutting PII detection and redaction for every LLM path. This is **not** FastAPI HTTP middleware — routes and chains import these modules directly so all traffic flows through one governed layer.

## Module layout

| Module | Role |
|--------|------|
| `analyzer.py` | Presidio engine setup (`get_analyzer`, `get_anonymizer`) |
| `audit.py` | HMAC span hashing and JSONL audit log |
| `context_filter.py` | Post-filter: suppress phone detections that are really IDs in prose |
| `document_redaction.py` | One-way redaction for retrieved document chunks (RAG path) |
| `pii_register.py` | Field-level treatment register loader (`pii_register.json`) |
| `sql_anonymizer.py` | Register-driven treatments for the SQL agent path |

## Two strategies (by design)

| Path | Module | Strategy | When it runs |
|------|--------|----------|--------------|
| Document RAG | `document_redaction.py` | **One-way** anonymize | After retrieval, before `format_docs` → LLM (`app/rag/chains/document_chain.py`) |
| SQL agent | `sql_anonymizer.py` | **Reversible** tokenization | Question in, SQL results, schema samples, answer out (`app/rag/chains/sql_chain.py`) |

### Document RAG — one-way redaction

Retrieved chunks may contain names, phones, or emails from the knowledge base. Presidio detects spans, `context_filter.py` drops false positives (phone-shaped IDs near "invoice #"), then values are permanently replaced before the LLM sees them. Audit log stores HMAC hashes only — no plaintext PII.

Toggle: `settings.redaction_enabled`

### SQL agent — three register-driven treatments

`app/guardrails/pii_register.json` maps each `(table, column)` to one treatment:

- **pseudonymize** — reversible token; restored for the user; `admin`/`finance` see real values. This is **LLM-boundary protection, not legal anonymization** — the restored value is still personal data (EDPB 01/2025).
- **redact** — one-way `[REDACTED]`; never restored; all roles.
- **suppress** — one-way `[SUPPRESSED]` + a query-layer guard rejecting any SQL that names the column + schema scrub; never restored; all roles.

Strictest treatment wins on JOIN ambiguity (`suppress > redact > pseudonymize`).

**suppress is defense-in-depth, not a boundary.** The app-layer guard is evadable
(aliases, `CONCAT`, views); the load-bearing control is a least-privilege DB
view/credential that excludes suppress columns from the agent's connection (ADR 0027).

## Eval tooling

| Artifact | Location |
|----------|----------|
| Detection ground truth | `tests/guardrails/fixtures/pii_ground_truth.jsonl` |
| Filter holdout set | `tests/guardrails/fixtures/pii_filter_holdout.jsonl` |
| SQL anonymization ground truth | `tests/guardrails/fixtures/sql_anonymization_ground_truth.jsonl` |
| Detection eval CLI | `scripts/eval/evaluate_pii_detection.py` |
| SQL anonymization eval CLI | `scripts/eval/evaluate_sql_anonymization.py` |
| Unit + regression tests | `tests/guardrails/` |
