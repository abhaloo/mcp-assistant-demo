"""Calibrate per-cell ceilings from bounded exact-wire canary receipts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from app.experiments.sql_arm_canary import SqlArmCanaryReceipt

__all__ = [
    "CalibrationError",
    "CellCeilingCalibration",
    "calibrate_cell_ceiling",
    "calibrate_cell_ceiling_by_arm",
]

DEFAULT_SAFETY_FACTOR = 3.0
DEFAULT_HARD_CAP_USD = 0.02


class CalibrationError(ValueError):
    """Canary receipts cannot support a conservative ceiling."""


@dataclass(frozen=True)
class CellCeilingCalibration:
    arm_name: str
    ceiling_usd: float
    observed_cost_usd: float
    safety_factor: float
    hard_cap_usd: float
    route_identity_hash: str
    api_class: str
    source_receipt_hashes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _assert_identity_consistent(receipts: list[SqlArmCanaryReceipt]) -> tuple[str, str]:
    route_hashes = {receipt.route_identity_hash for receipt in receipts}
    api_classes = {receipt.api_class for receipt in receipts}
    if len(route_hashes) != 1 or len(api_classes) != 1:
        raise CalibrationError("identity mismatch across canary receipts")
    return next(iter(route_hashes)), next(iter(api_classes))


def calibrate_cell_ceiling(
    receipts: list[SqlArmCanaryReceipt],
    *,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    hard_cap_usd: float = DEFAULT_HARD_CAP_USD,
) -> CellCeilingCalibration:
    """Derive one conservative ceiling from observed exact-wire cost."""
    if not receipts:
        raise CalibrationError("at least one canary receipt is required")
    if len({receipt.arm_name for receipt in receipts}) != 1:
        raise CalibrationError("receipts must belong to a single arm")
    route_identity_hash, api_class = _assert_identity_consistent(receipts)
    observed = max(receipt.cost_usd for receipt in receipts)
    raw_ceiling = observed * safety_factor
    ceiling = min(raw_ceiling, hard_cap_usd)
    if ceiling <= 0:
        raise CalibrationError("calibrated ceiling must be positive")
    return CellCeilingCalibration(
        arm_name=receipts[0].arm_name,
        ceiling_usd=ceiling,
        observed_cost_usd=observed,
        safety_factor=safety_factor,
        hard_cap_usd=hard_cap_usd,
        route_identity_hash=route_identity_hash,
        api_class=api_class,
        source_receipt_hashes=tuple(receipt.receipt_hash for receipt in receipts),
    )


def calibrate_cell_ceiling_by_arm(
    receipts: list[SqlArmCanaryReceipt],
    *,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    hard_cap_usd: float = DEFAULT_HARD_CAP_USD,
) -> dict[str, CellCeilingCalibration]:
    grouped: dict[str, list[SqlArmCanaryReceipt]] = {}
    for receipt in receipts:
        grouped.setdefault(receipt.arm_name, []).append(receipt)
    return {
        arm_name: calibrate_cell_ceiling(
            arm_receipts,
            safety_factor=safety_factor,
            hard_cap_usd=hard_cap_usd,
        )
        for arm_name, arm_receipts in grouped.items()
    }


def calibration_manifest_hash(calibrations: dict[str, CellCeilingCalibration]) -> str:
    payload = {arm: calibration.to_dict() for arm, calibration in sorted(calibrations.items())}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
