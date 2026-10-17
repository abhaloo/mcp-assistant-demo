"""Frozen-snapshot wiring and gold verification for the SQL eval harness.

Extracted from ``scripts/eval/evaluate_sql_agent.py`` (S4, harness-sql) so
``app/experiments/live_sql_eval.py`` can reuse it without ``app`` importing
``scripts``. The script re-exports both moved names under their original
identifiers, so ``scripts.eval.evaluate_sql_agent.assess_gold_degeneracy`` (and its
existing test) keeps working unchanged. Behaviour is byte-for-byte the same.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.config import settings

# The snapshot engine this module last installed, so a re-pin can dispose it without
# ever touching the production engine it may be replacing.
_OWNED_SNAPSHOT_ENGINE: Any = None


def point_agent_at_snapshot(concurrency: int = 1) -> None:
    """Swap the module-level engine the SQL chain reads to the frozen snapshot.

    The snapshot is read-only BY CONTRACT. We attach the same read-only-transaction
    guard the production engine uses (app/db/engine.py) — defense in depth on top of
    the read-only DB user the runbook prescribes — so a bad case, prompt injection, or
    a mis-set URL cannot mutate the frozen data (which would also break
    reproducibility). _safety_flags() only *reports* writes after the fact; this
    *prevents* them at the connection.
    """
    if not settings.eval_snapshot_database_url:
        raise RuntimeError(
            "EVAL_SNAPSHOT_DATABASE_URL is not set — see docs/runbooks/sql-eval-snapshot.md"
        )
    from sqlalchemy import create_engine, event

    import app.eval.sql.agent.agent as sql_agent

    # Size the pool for the thread-pool runner: each concurrent run_case checks out a
    # connection for its gold/agent scoring fetches. Too small a pool serializes them.
    snapshot = create_engine(
        settings.eval_snapshot_database_url,
        pool_pre_ping=True,
        pool_size=max(5, concurrency + 2),
        max_overflow=concurrency,
        # Match prod billing engine fail-fast connect + recycle intent (app/db/engine.py).
        pool_recycle=280,
        connect_args={"connect_timeout": 10},
    )

    # Dispose only an engine WE created. sql_agent.engine may be None (nothing has
    # resolved the lazy billing engine yet -- see app/eval/sql/agent/billing_engine.py),
    # the shared production billing engine, or a snapshot from a previous pin --
    # disposing anything but our own previous snapshot would either close a pool
    # this module does not own, or dispose nothing when there is nothing to dispose.
    # Without this, a second pin (freeze then run) leaves the first snapshot
    # engine's pooled MySQL connections checked out until GC.
    global _OWNED_SNAPSHOT_ENGINE
    previous = _OWNED_SNAPSHOT_ENGINE
    if previous is not None and getattr(sql_agent, "engine", None) is previous:
        previous.dispose()
    _OWNED_SNAPSHOT_ENGINE = snapshot

    @event.listens_for(snapshot, "begin")
    def _force_read_only(conn):
        conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
        # Pin the DB session clock to the frozen anchor so dates the DB computes
        # (CURDATE()/NOW() the agent emits INTO its SQL) resolve to the same "today"
        # as the prompt's injected date (settings.eval_today) — not the live clock.
        # Without this, only model-written date literals were pinned; DB-computed
        # dates drifted, breaking aging-bucket boundaries on the frozen snapshot.
        conn.exec_driver_sql(
            f"SET TIMESTAMP = UNIX_TIMESTAMP('{settings.eval_today.isoformat()} 12:00:00')"
        )

    sql_agent.engine = snapshot


def assess_gold_degeneracy(rows: list) -> tuple[bool, str | None]:
    """Flag gold shapes that can match any agent output by coincidence (human-confirmed only)."""
    if not rows:
        return True, "empty_gold"
    if len(rows) == 1 and all(v is None for v in rows[0]):
        return True, "all_null_gold"
    return False, None


def snapshot_identity() -> dict[str, Any]:
    """Identity of the frozen database + pinned clock, without leaking the DSN.

    The snapshot URL carries credentials, so only its hash is recorded — enough to
    prove two runs used the same target, never enough to reconstruct it.
    """
    url = settings.eval_snapshot_database_url or ""
    if not url:
        raise RuntimeError(
            "EVAL_SNAPSHOT_DATABASE_URL is not set — see docs/runbooks/sql-eval-snapshot.md"
        )
    return {
        "eval_snapshot_url_sha256": f"sha256:{hashlib.sha256(url.encode('utf-8')).hexdigest()}",
        "eval_today": settings.eval_today.isoformat(),
        "sql_policy_exemption": "ScopedSqlPolicy.eval_snapshot_fixture",
    }


def gold_verification_receipt(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Execute every gold query once and record what it returned.

    This is the receipt that makes a coincidental match detectable later: a gold whose
    result set is empty, or one all-NULL row, can be matched by ANY agent output on
    this one snapshot. A gold that ERRORS is recorded with its error rather than
    silently skipped — the caller decides, and ``freeze_sql_stage`` refuses.
    Never returns result rows, only counts (PII safety).
    """
    from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access
    from app.eval.sql.agent.agent import build_sql_database
    from app.eval.sql.diagnostics import _fetch, scrub
    from app.experiments.core.secret_redaction import redact_secrets
    from app.rag.access_tiers import get_access_tiers

    # Explicit CLI/eval/test exemption — see app/eval/sql/agent/access.py.
    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())

    receipt: dict[str, dict[str, Any]] = {}
    for case in cases:
        gold_sql = case["gold_sql"]
        entry: dict[str, Any] = {
            "gold_sql_sha256": f"sha256:{hashlib.sha256(gold_sql.encode('utf-8')).hexdigest()}",
        }
        tiers = get_access_tiers(case["role"], case.get("permissions", []))
        db, _ = build_sql_database(tiers, case["role"])
        try:
            rows = _fetch(db, gold_sql)
        except Exception as exc:  # noqa: BLE001 - recorded, then refused by the caller
            # This entry is embedded in the FROZEN contract -- the longest-lived,
            # widest-read artifact the harness writes. It also comes from executing
            # GOLD SQL, so SQLAlchemy's `[parameters: ('Ali Hassan',)]` here carries
            # real customer values, and the connection error that produced it can
            # carry a credential. Both passes, for the same reason run_case uses both:
            # `scrub` masks quoted literals, `redact_secrets` masks key-shaped tokens
            # outside quotes, and neither subsumes the other.
            entry.update({"error": f"{type(exc).__name__}: {redact_secrets(scrub(str(exc)))}"})
            receipt[case["id"]] = entry
            continue
        candidate, reason = assess_gold_degeneracy(rows)
        entry.update(
            {
                "row_count": len(rows),
                "degenerate_candidate": candidate,
                "degenerate_reason": reason,
            }
        )
        receipt[case["id"]] = entry
    return receipt
