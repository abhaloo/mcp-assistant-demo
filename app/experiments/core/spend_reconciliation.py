"""Ledger/heartbeat spend reconciliation for checkpoint and recovery gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.experiments.core.ledger_view import LedgerView

SpendStatus = Literal["MATCH", "DISCREPANCY", "UNSETTLED"]


@dataclass(frozen=True)
class SpendReconciliation:
    status: SpendStatus
    authoritative_usd: float
    raw_sources: dict[str, Any] = field(default_factory=dict)


def reconcile_spend_evidence(
    *,
    ledger: LedgerView,
    heartbeat: dict[str, Any],
    staged: dict[str, Any],
) -> SpendReconciliation:
    """Single authority for MATCH / DISCREPANCY / UNSETTLED spend evidence.

    ``ledger`` is a typed :class:`LedgerView` -- callers normalise a foreign
    shape via :meth:`LedgerView.from_any` before calling this, once, at the
    boundary, so this function reads two typed fields and nothing more.
    """
    if ledger.has_open_reservation:
        return SpendReconciliation(
            status="UNSETTLED",
            authoritative_usd=0.0,
            raw_sources={"heartbeat": heartbeat, "staged": staged, "ledger": asdict(ledger)},
        )

    authoritative = ledger.settled_usd
    raw_spend = staged.get("actual_spend_usd")
    staged_usd = float(raw_spend) if raw_spend is not None else 0.0
    heartbeat_usd = float(heartbeat.get("spend_usd") or 0.0)

    status: SpendStatus = "MATCH"
    if abs(authoritative - staged_usd) > 1e-6 or (
        heartbeat_usd and abs(authoritative - heartbeat_usd) > 1e-6
    ):
        status = "DISCREPANCY"

    return SpendReconciliation(
        status=status,
        authoritative_usd=authoritative,
        raw_sources={
            "heartbeat": dict(heartbeat),
            "staged": dict(staged),
            "ledger": asdict(ledger),
        },
    )
