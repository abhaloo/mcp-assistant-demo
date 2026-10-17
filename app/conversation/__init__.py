"""Multi-turn conversation: transcript storage, condensation, turn resolution.

Must-not-regress invariants (see ADR 0028 Security & data invariants):
- Redact configured entities on **write**, then clamp to TURN_CONTENT_MAX (never clamp first).
- History is **identity-bound** (user_id), not tier-bound — no per-turn tier re-filter.
- Cross-user thread access returns [] and mints a fresh id — **never 403** on ownership mismatch.
- Thread ownership is ALSO entity-scoped (task A7a) — an entity switch between
  requests (same user_id, different principal.entity_id) is treated exactly
  like cross-user access: [] and a fresh id, never a leaked "thread exists".
- Transcript store requires TLS in production (rediss://) and at-rest encryption
  chosen at provision time.
- Both idle (sliding) and absolute session timeouts apply.
"""
