"""Load the live campaign recovery success contract."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.paths import REPO_ROOT

_CONTRACT_PATH = (
    REPO_ROOT / "evals" / "experiments" / "live-campaign-recovery-success-contract.json"
)


@lru_cache(maxsize=1)
def load_live_contract() -> dict[str, Any]:
    """Return the parsed live-campaign recovery success contract."""
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
