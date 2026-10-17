"""Cross-process Windows-safe spend ledger for the DeepSeek experiment."""

from __future__ import annotations

import json
import math
import os
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from app.experiments.core.money import (
    BUDGET_EPSILON_USD,
    estimate_cost_usd,
)
from app.paths import REPO_ROOT
from app.pricing.prices import require_price_key

TOTAL_CAP_USD = 60.0
LAUNCH_STOP_FRACTION = 0.80
RESERVATION_DECIMALS = 6
PHASE_BUDGETS: dict[str, float] = {
    "tool_smoke": 1.0,
    # /api/ask smoke is $0.10; pairwise A+B plus aborted retries must not brick relaunch.
    "api_smoke": 2.0,
    "rag": 6.0,
    # Retry policy: one repair attempt reserves against the frozen $8
    # per-attempt campaign cap, so the phase holds TWO full attempts plus abort
    # overhead — a committed canary or partial answer spend from an aborted
    # attempt must never brick the retry. The per-attempt bound stays the
    # frozen campaign_cap_usd; the global TOTAL_CAP_USD/launch-stop still govern.
    "rag_repair": 55.0,
    "sql_dev": 7.0,
    "sql_holdout": 6.0,
    # One-token wire probe per eligible route, over-reserved at $0.01 each.
    # Sized for ~50 route-probes so repeated pre-flights across a debugging
    # session stay cheap and cannot crowd out a campaign phase.
    "route_conformance": 0.50,
    "sql_direct_canary": 0.10,
    # Aligned with sql_dev so a recalibrated answer reserve (~$1.7–$2.1) can book
    # against the ledger when the CLI tags direct-matrix spend (was $3.0, too tight).
    "sql_direct_matrix": 7.0,
    # R3: the campaign review's optional LLM narrative. Its OWN phase, sized for ~20
    # narratives, so a review can never be the reason a matrix could not afford its
    # judge. The deterministic verdict does not spend and does not depend on this
    # phase being solvent -- an exhausted budget costs a paragraph, not a verdict.
    "campaign_review": 1.0,
    # Remote /api/ask wiring canary: 3 JSON + 3 SSE (~$3 reserve). Sized for three
    # full armed attempts so one failed committed run cannot brick the next acquire.
    "prod_ask_wiring_canary": 9.0,
}
DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "spend" / "deepseek-experiment-ledger.json"
LEDGER_VERSION = 1


class SpendCoordinatorError(Exception):
    """Base error for spend ledger operations."""


class BudgetExceededError(SpendCoordinatorError):
    """Reservation would exceed phase or total budget."""


class BudgetOverrunError(SpendCoordinatorError):
    """Actual spend exceeded a reservation or a ledger cap; future launches stop."""


class LaunchStoppedError(SpendCoordinatorError):
    """Committed spend reached the launch-stop threshold."""


class ReservationNotFoundError(SpendCoordinatorError):
    """Unknown reservation id."""


def launch_stop_threshold_usd() -> float:
    return TOTAL_CAP_USD * LAUNCH_STOP_FRACTION


def _empty_ledger() -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "spent_usd": 0.0,
        "phases": {phase: {"spent_usd": 0.0} for phase in PHASE_BUDGETS},
        "reservations": {},
        "budget_violations": [],
    }


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_ledger()
    doc = json.loads(path.read_text(encoding="utf-8"))
    for phase in PHASE_BUDGETS:
        doc.setdefault("phases", {}).setdefault(phase, {"spent_usd": 0.0})
    doc.setdefault("reservations", {})
    doc.setdefault("spent_usd", 0.0)
    doc.setdefault("budget_violations", [])
    return doc


def _phase_reserved(doc: dict[str, Any], phase: str) -> float:
    return sum(r["est_usd"] for r in doc["reservations"].values() if r["phase"] == phase)


def _total_reserved(doc: dict[str, Any]) -> float:
    return sum(r["est_usd"] for r in doc["reservations"].values())


def _write_ledger_atomic(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def acquire(
    phase: str,
    model_id: str,
    est_usd: float,
    *,
    ledger_path: Path | None = None,
    prices_path: Path | None = None,
) -> str:
    """Reserve spend under file lock; return reservation_id."""
    if phase not in PHASE_BUDGETS:
        msg = f"unknown phase {phase!r}"
        raise ValueError(msg)
    if not math.isfinite(est_usd) or est_usd <= 0:
        msg = "est_usd must be positive"
        raise ValueError(msg)

    require_price_key(model_id, prices_path=prices_path)

    path = ledger_path or DEFAULT_LEDGER_PATH
    reservation_id = str(uuid.uuid4())
    lock = FileLock(str(path) + ".lock")
    with lock:
        doc = _read_ledger(path)
        if doc["budget_violations"]:
            raise BudgetOverrunError("unresolved budget violation blocks new reservations")
        if doc["spent_usd"] >= launch_stop_threshold_usd():
            msg = (
                f"launch stop: spent ${doc['spent_usd']:.4f} >= ${launch_stop_threshold_usd():.2f}"
            )
            raise LaunchStoppedError(msg)

        phase_spent = doc["phases"][phase]["spent_usd"]
        phase_reserved = _phase_reserved(doc, phase)
        if phase_spent + phase_reserved + est_usd > PHASE_BUDGETS[phase] + BUDGET_EPSILON_USD:
            msg = (
                f"phase budget exceeded for {phase!r}: "
                f"{phase_spent + phase_reserved + est_usd:.4f} > "
                f"{PHASE_BUDGETS[phase]:.2f}"
            )
            raise BudgetExceededError(msg)

        total_reserved = _total_reserved(doc)
        if doc["spent_usd"] + total_reserved + est_usd > TOTAL_CAP_USD + BUDGET_EPSILON_USD:
            msg = (
                f"total budget exceeded: "
                f"{doc['spent_usd'] + total_reserved + est_usd:.4f} > "
                f"{TOTAL_CAP_USD:.2f}"
            )
            raise BudgetExceededError(msg)

        doc["reservations"][reservation_id] = {
            "phase": phase,
            "model_id": model_id,
            "est_usd": est_usd,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _write_ledger_atomic(path, doc)

    return reservation_id


def commit(
    reservation_id: str,
    actual_usd: float,
    *,
    ledger_path: Path | None = None,
) -> None:
    """Finalize a reservation with actual spend."""
    if not math.isfinite(actual_usd) or actual_usd < 0:
        msg = "actual_usd must be finite and non-negative"
        raise ValueError(msg)

    path = ledger_path or DEFAULT_LEDGER_PATH
    lock = FileLock(str(path) + ".lock")
    violation: dict[str, Any] | None = None
    with lock:
        doc = _read_ledger(path)
        reservation = doc["reservations"].pop(reservation_id, None)
        if reservation is None:
            msg = f"reservation not found: {reservation_id!r}"
            raise ReservationNotFoundError(msg)

        phase = reservation["phase"]
        reasons: list[str] = []
        if actual_usd > float(reservation["est_usd"]) + BUDGET_EPSILON_USD:
            reasons.append("reservation_overrun")
        if (
            doc["phases"][phase]["spent_usd"] + actual_usd
            > PHASE_BUDGETS[phase] + BUDGET_EPSILON_USD
        ):
            reasons.append("phase_cap_exceeded")
        if doc["spent_usd"] + actual_usd > TOTAL_CAP_USD + BUDGET_EPSILON_USD:
            reasons.append("total_cap_exceeded")
        doc["spent_usd"] += actual_usd
        doc["phases"][phase]["spent_usd"] += actual_usd
        if reasons:
            violation = {
                "reservation_id": reservation_id,
                "phase": phase,
                "model_id": reservation["model_id"],
                "reserved_usd": reservation["est_usd"],
                "actual_usd": actual_usd,
                "reasons": reasons,
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            doc["budget_violations"].append(violation)
        _write_ledger_atomic(path, doc)
    if violation is not None:
        raise BudgetOverrunError(
            "reservation overrun recorded; future launches are blocked: "
            + ", ".join(violation["reasons"])
        )


def release(
    reservation_id: str,
    *,
    ledger_path: Path | None = None,
) -> None:
    """Drop a stale reservation without charging spend."""
    path = ledger_path or DEFAULT_LEDGER_PATH
    lock = FileLock(str(path) + ".lock")
    with lock:
        doc = _read_ledger(path)
        if reservation_id not in doc["reservations"]:
            msg = f"reservation not found: {reservation_id!r}"
            raise ReservationNotFoundError(msg)
        del doc["reservations"][reservation_id]
        _write_ledger_atomic(path, doc)


def charge_for(
    deployment: str,
    *,
    input_tokens: int,
    output_tokens: int,
    reserved_usd: float,
    cost_fn: Callable[[str, int, int], float] | None = None,
) -> float:
    """What one live call actually cost. The charge rule, once.

    Missing usage or an unpriced deployment charges the reservation rather than
    booking a live call free.
    """
    pricer = cost_fn if cost_fn is not None else estimate_cost_usd
    if input_tokens == 0 and output_tokens == 0:
        return reserved_usd
    try:
        return pricer(deployment, input_tokens, output_tokens)
    except KeyError:
        return reserved_usd


class SpendHandle:
    """Handle yielded by reserved() for marking spend start and recording actual spend."""

    def __init__(self, reservation_id: str, est_usd: float) -> None:
        self.reservation_id = reservation_id
        self.est_usd = est_usd
        self.spend_started = False
        self.actual_usd: float | None = None

    def mark_spend_started(self) -> None:
        self.spend_started = True

    def set_actual_usd(self, actual_usd: float) -> None:
        self.actual_usd = actual_usd


@contextmanager
def reserved(
    phase: str,
    model_id: str,
    est_usd: float,
    *,
    ledger_path: Path | None = None,
    ledger: Any | None = None,
) -> Generator[SpendHandle, None, None]:
    """Context manager scoping spend reservation, committing on exit/spend, releasing otherwise."""
    if ledger is not None:
        res_id = ledger.reserve(phase, model_id, est_usd)
    else:
        res_id = acquire(phase, model_id, est_usd, ledger_path=ledger_path)

    handle = SpendHandle(reservation_id=res_id, est_usd=est_usd)
    try:
        yield handle
    except Exception:
        if handle.spend_started:
            actual = handle.actual_usd if handle.actual_usd is not None else est_usd
            if ledger is not None:
                ledger.commit(res_id, actual)
            else:
                commit(res_id, actual, ledger_path=ledger_path)
        else:
            if ledger is not None:
                ledger.release(res_id)
            else:
                release(res_id, ledger_path=ledger_path)
        raise
    else:
        actual = handle.actual_usd if handle.actual_usd is not None else est_usd
        if ledger is not None:
            ledger.commit(res_id, actual)
        else:
            commit(res_id, actual, ledger_path=ledger_path)
