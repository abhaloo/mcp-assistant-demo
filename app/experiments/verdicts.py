"""One status vocabulary. Values are wire format: they appear in written
artifacts and comparisons, and changing them would break replay compatibility.
"""

from __future__ import annotations

from enum import StrEnum


class SqlGateStatus(StrEnum):
    DEV_PASS = "SQL_DEV_PASS"
    ELIGIBLE = "SQL-ELIGIBLE"
    HOLD = "HOLD"
    FAIL = "FAIL"


class RagGateStatus(StrEnum):
    CANDIDATE_DEV = "RAG-CANDIDATE-DEV"
    HOLD = "HOLD"
    FAIL = "FAIL"


class RouteStatus(StrEnum):
    CONFORMANT = "ROUTES-CONFORMANT"


class ReviewVerdict(StrEnum):
    SOUND = "SOUND"
    QUALIFIED = "QUALIFIED"
    UNSOUND = "UNSOUND"


class LaneStatus(StrEnum):
    FROZEN_ONLY = "FROZEN_ONLY"


class EffortProbeVerdict(StrEnum):
    UNPROVEN = "EFFORT-UNPROVEN"
