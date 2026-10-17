"""The abort-path termination receipt: is a sealed judge checkpoint recoverable?"""

from __future__ import annotations

import json
from typing import Any

from app.experiments.core.attempt_store import AttemptStore
from app.experiments.core.ledger_view import LedgerView
from app.experiments.core.spend_reconciliation import reconcile_spend_evidence
from app.experiments.core.spend_staging import SpendStaging
from app.experiments.core.stage_receipts import write_stage_receipt

__all__ = ["write_termination_evidence"]


def _ledger_view_for_termination(
    staging: SpendStaging,
    *,
    reconcile_ledger: Any | None,
) -> LedgerView:
    if reconcile_ledger is not None:
        return LedgerView.from_any(reconcile_ledger)
    spend = staging.checkpoint_spend_evidence()
    # In-flight reservations are unwound after the terminal receipt; eligibility
    # for recovery follows sealed checkpoint spend totals unless an external
    # ledger view is injected (integration tests / live coordinator).
    return LedgerView(
        settled_usd=float(spend.get("actual_spend_usd") or 0.0),
        has_open_reservation=False,
    )


def _heartbeat_view_for_reconcile(store: AttemptStore) -> dict[str, Any]:
    heartbeat_path = store.dir / "heartbeat.json"
    if not heartbeat_path.is_file():
        return {}
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_termination_evidence(
    *,
    store: AttemptStore,
    staging: SpendStaging,
    reconcile_ledger: Any | None,
) -> None:
    """Write the abort receipt, if a judge checkpoint was sealed before the failure.

    A no-op when no checkpoint was sealed (nothing to reconcile) or a
    termination receipt already exists (an earlier abort already decided this
    attempt's fate). Otherwise reconciles spend against the ledger and the
    heartbeat and seals ``RECOVERABLE_CHECKPOINT`` or ``ABORTED_NO_RECOVERY``.
    """
    if not store.exists("judge-checkpoint.json") or store.exists("receipts/termination.json"):
        return
    spend_evidence = staging.checkpoint_spend_evidence()
    reconciliation = reconcile_spend_evidence(
        ledger=_ledger_view_for_termination(staging, reconcile_ledger=reconcile_ledger),
        heartbeat=_heartbeat_view_for_reconcile(store),
        staged=spend_evidence,
    )
    if reconciliation.status == "UNSETTLED":
        terminal_state = "ABORTED_NO_RECOVERY"
        required_action = None
    else:
        terminal_state = "RECOVERABLE_CHECKPOINT"
        required_action = "finalize-from-checkpoint"
    write_stage_receipt(
        store=store,
        stage="termination",
        inputs={"judge-checkpoint.json": "sealed"},
        outputs={},
        spend=spend_evidence,
        terminal_state=terminal_state,
        required_action=required_action,
    )
