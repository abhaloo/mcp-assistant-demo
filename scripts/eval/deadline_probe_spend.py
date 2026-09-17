"""Spend envelope for the Ask deadline three-arm probe."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from filelock import FileLock

from app.experiments.core.money import BUDGET_EPSILON_USD, round_reservation_usd
from app.telemetry.invocation_ledger import ledger_store_configured
from scripts.eval.deadline_probe_telemetry import (
    CaseLedgerSlice,
    empty_case_ledger_slice,
    is_zero_llm_greeting,
    load_case_ledger_slice,
)

SOFT_ALERT_FRAC = 0.80
ARM_WALL_S = 7200
DEFAULT_SAFETY_FACTOR = 3.0


def charge_case(records: list[Any] | tuple[Any, ...], *, cell_ceiling_usd: Decimal) -> Decimal:
    """Sum complete durable USD rows or consume the cell ceiling on partial rows."""
    if not records:
        msg = "charge_case requires at least one durable row"
        raise ValueError(msg)
    total = Decimal("0")
    for row in records:
        status = row.cost_status if hasattr(row, "cost_status") else row.get("cost_status")
        estimated = row.estimated_usd if hasattr(row, "estimated_usd") else row.get("estimated_usd")
        if status != "complete" or estimated is None:
            return Decimal(str(round_reservation_usd(float(cell_ceiling_usd))))
        total += Decimal(str(estimated))
    return total.quantize(Decimal("0.000001"))


class SpendLedger:
    """File-locked campaign spend ledger keyed by Ask run_id."""

    def __init__(self, path: Path, doc: dict[str, Any]) -> None:
        self._path = path
        self._doc = doc

    @classmethod
    def create(cls, path: Path, *, campaign_cap_usd: Decimal) -> SpendLedger:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "campaign_cap_usd": str(campaign_cap_usd),
            "spent_usd": "0",
            "cell_ceiling_usd": None,
            "reservations": {},
        }
        ledger = cls(path, doc)
        ledger._save()
        return ledger

    @classmethod
    def load(cls, path: Path) -> SpendLedger:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, doc)

    @property
    def spent_usd(self) -> Decimal:
        return Decimal(str(self._doc.get("spent_usd", "0")))

    @property
    def campaign_cap_usd(self) -> Decimal:
        return Decimal(str(self._doc["campaign_cap_usd"]))

    @property
    def cell_ceiling_usd(self) -> Decimal | None:
        raw = self._doc.get("cell_ceiling_usd")
        return None if raw is None else Decimal(str(raw))

    def _lock(self) -> FileLock:
        return FileLock(str(self._path) + ".lock")

    def _save(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._doc, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def reserve(self, run_id: str, cell_usd: Decimal) -> bool:
        with self._lock():
            self._doc = json.loads(self._path.read_text(encoding="utf-8"))
            reservations = self._doc.setdefault("reservations", {})
            if run_id in reservations:
                return True
            spent = Decimal(str(self._doc.get("spent_usd", "0")))
            outstanding = sum(
                (
                    Decimal(str(entry.get("reserved_usd", "0")))
                    for entry in reservations.values()
                    if entry.get("committed_usd") is None
                ),
                Decimal("0"),
            )
            cap = Decimal(str(self._doc["campaign_cap_usd"]))
            if spent + outstanding + cell_usd > cap + Decimal(str(BUDGET_EPSILON_USD)):
                return False
            reservations[run_id] = {
                "reserved_usd": str(cell_usd),
                "committed_usd": None,
            }
            self._save()
            return True

    def commit(self, run_id: str, committed_usd: Decimal) -> None:
        with self._lock():
            self._doc = json.loads(self._path.read_text(encoding="utf-8"))
            reservations = self._doc.setdefault("reservations", {})
            entry = reservations.setdefault(run_id, {"reserved_usd": str(committed_usd)})
            if entry.get("committed_usd") is not None:
                return
            entry["committed_usd"] = str(committed_usd)
            spent = Decimal(str(self._doc.get("spent_usd", "0"))) + committed_usd
            self._doc["spent_usd"] = str(spent)
            self._save()

    def set_cell_ceiling_usd(self, cell_usd: Decimal) -> None:
        with self._lock():
            self._doc = json.loads(self._path.read_text(encoding="utf-8"))
            self._doc["cell_ceiling_usd"] = str(cell_usd)
            self._save()


def live_spend_required(
    *, live_env_flag: str, live_flag_value: str | None, is_baseline: bool
) -> bool:
    return live_flag_value == "1" and not is_baseline


def refuse_live_spend_start(
    *,
    required: bool,
    campaign_cap_usd: Decimal | None,
    spend_ledger: Path | str | None,
) -> str | None:
    if not required:
        return None
    if campaign_cap_usd is None or spend_ledger is None:
        return "live run requires --campaign-cap-usd and --spend-ledger"
    if not ledger_store_configured():
        return "query record DSN not configured for live spend"
    return None


def open_spend_ledger(spend_ledger: Path | str, *, campaign_cap_usd: Decimal) -> SpendLedger:
    path = Path(spend_ledger)
    if path.is_file():
        return SpendLedger.load(path)
    return SpendLedger.create(path, campaign_cap_usd=campaign_cap_usd)


@dataclass
class SpendEnvelope:
    """Per-arm spend state for the coordinator eval loop."""

    ledger: SpendLedger
    campaign_cap_usd: Decimal
    arm_wall_s: int
    soft_alert_frac: float = SOFT_ALERT_FRAC
    priced_cell: Decimal | None = None
    max_priced_usd: Decimal | None = None
    missing_model_usage_count: int = 0
    budget_exceeded_count: int = 0
    priced_n: int = 0
    last_slice: CaseLedgerSlice = field(default_factory=empty_case_ledger_slice)
    arm_started: float = field(init=False)
    abort_reason: str | None = None
    abort_status: str | None = None

    def __post_init__(self) -> None:
        self.arm_started = time.monotonic()

    @property
    def last_invocation_records(self) -> list[Any]:
        return list(self.last_slice.invocation_rows)

    def wall_exceeded(self) -> bool:
        return time.monotonic() - self.arm_started >= self.arm_wall_s

    def mark_wall_abort(self) -> None:
        self.abort_status = "wall_abort"
        self.abort_reason = f"arm wall {self.arm_wall_s}s exceeded"

    def cell_for_next_case(self) -> Decimal:
        if self.priced_cell is not None:
            return self.priced_cell
        if self.max_priced_usd is not None:
            self.priced_cell = self.max_priced_usd * Decimal(str(DEFAULT_SAFETY_FACTOR))
            self.ledger.set_cell_ceiling_usd(self.priced_cell)
            return self.priced_cell
        remaining = self.campaign_cap_usd - self.ledger.spent_usd
        if remaining < Decimal("0"):
            return Decimal("0")
        return remaining

    def _soft_alert_reached(self) -> bool:
        threshold = self.campaign_cap_usd * Decimal(str(self.soft_alert_frac))
        return self.ledger.spent_usd + Decimal(str(BUDGET_EPSILON_USD)) >= threshold

    def reserve_next(self) -> tuple[str | None, str | None]:
        if self.abort_status is not None:
            return None, self.abort_status
        self.last_slice = empty_case_ledger_slice()
        run_id = uuid.uuid4().hex
        if self._soft_alert_reached():
            self.abort_status = "budget_exceeded"
            self.abort_reason = "soft-alert"
            self.budget_exceeded_count += 1
            return run_id, "budget_exceeded"
        if not self.ledger.reserve(run_id, self.cell_for_next_case()):
            self.abort_status = "budget_exceeded"
            self.abort_reason = "budget_exceeded"
            self.budget_exceeded_count += 1
            return run_id, "budget_exceeded"
        return run_id, None

    def settle_after_post(
        self,
        run_ids: tuple[str, ...],
        *,
        reservation_id: str | None = None,
        model: str | None = None,
    ) -> str | None:
        if self.abort_status is not None:
            return self.abort_status
        if not run_ids:
            self.last_slice = empty_case_ledger_slice()
            self.abort_status = "price_source_missing"
            self.abort_reason = "price_source_missing"
            return self.abort_status
        case_ledger = load_case_ledger_slice(run_ids, poll=True)
        self.last_slice = case_ledger
        question_id = run_ids[-1]
        # Close the uuid placeholder so outstanding reserved_usd does not
        # keep occupying the cap after the question run_id takes the charge.
        if reservation_id and reservation_id != question_id:
            self.ledger.commit(reservation_id, Decimal("0"))
        records = case_ledger.invocation_rows
        if not records:
            if is_zero_llm_greeting(model):
                self.ledger.commit(question_id, Decimal("0"))
                self.priced_n += 1
                return None
            self.abort_status = "price_source_missing"
            self.abort_reason = "price_source_missing"
            return self.abort_status
        cell = self.cell_for_next_case()
        committed = charge_case(records, cell_ceiling_usd=cell)
        self.ledger.commit(question_id, committed)
        self.priced_n += 1
        if committed == cell and any(
            getattr(row, "cost_status", None) != "complete"
            or getattr(row, "estimated_usd", None) is None
            for row in records
        ):
            self.missing_model_usage_count += 1
        if self.max_priced_usd is None or committed > self.max_priced_usd:
            self.max_priced_usd = committed
        return None

    def heartbeat_line(self, *, done_n: int, total_n: int, case_id: str) -> str:
        spent = self.ledger.spent_usd
        remaining = self.campaign_cap_usd - spent
        return (
            f"HEARTBEAT cases={done_n}/{total_n} case={case_id} "
            f"spent={spent} cap={self.campaign_cap_usd} remaining={remaining} "
            f"priced_n={self.priced_n}"
        )

    def capture_status(self, *, incomplete: bool) -> str:
        if self.abort_status == "price_source_missing":
            return "price_source_missing"
        if self.abort_status == "budget_exceeded":
            return "budget_exceeded"
        if self.abort_status == "wall_abort":
            return "wall_abort"
        if incomplete or self.missing_model_usage_count:
            return "unproven"
        return "complete"

    def summary_fields(self) -> dict[str, Any]:
        return {
            "spent_usd": str(self.ledger.spent_usd),
            "campaign_cap_usd": str(self.campaign_cap_usd),
            "cell_ceiling_usd": (
                str(self.ledger.cell_ceiling_usd)
                if self.ledger.cell_ceiling_usd is not None
                else None
            ),
            "priced_n": self.priced_n,
            "missing_model_usage_count": self.missing_model_usage_count,
            "budget_exceeded_count": self.budget_exceeded_count,
        }


def emit_heartbeat(
    envelope: SpendEnvelope | None, *, done_n: int, total_n: int, case_id: str
) -> None:
    if envelope is None:
        return
    print(
        envelope.heartbeat_line(done_n=done_n, total_n=total_n, case_id=case_id),
        file=sys.stderr,
        flush=True,
    )
