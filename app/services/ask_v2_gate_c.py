"""Gate C readiness inputs for the Ask AI v2 route.

The v2 route refuses work unless every readiness input below is satisfied. The
check lives outside the request services so both the JSON and SSE entrypoints
can consult it without importing each other.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app.config import FROZEN_DECRYPT_ROLES, FROZEN_TRACE_RETENTION_DAYS, settings
from app.resources import ProcessResources


@dataclass(frozen=True)
class GateCStatus:
    is_ready: bool
    missing_inputs: tuple[str, ...]


@dataclass(frozen=True)
class ZeroPlaintextBackfillReceipt:
    """Parsed, validated zero-plaintext backfill receipt.

    Matches the JSON shape ``scripts/ops/encrypt_invocation_ledger.py`` emits
    on backfill completion. An arbitrary non-empty string cannot satisfy Gate
    C input 6 -- only a receipt that parses into this shape and proves zero
    remaining plaintext rows does.
    """

    encrypted_count: int
    error_count: int
    verified: bool
    key_version: str

    @property
    def proves_zero_plaintext(self) -> bool:
        return self.verified and self.error_count == 0 and bool(self.key_version)

    @classmethod
    def parse(cls, raw: str) -> ZeroPlaintextBackfillReceipt:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("backfill receipt json must be a dict")
        return cls(
            encrypted_count=int(data["encrypted_count"]),
            error_count=int(data["error_count"]),
            verified=bool(data["verified"]),
            key_version=str(data["key_version"]),
        )


def _zero_plaintext_receipt_is_valid(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return ZeroPlaintextBackfillReceipt.parse(raw).proves_zero_plaintext
    except (KeyError, TypeError, ValueError):
        return False


async def check_gate_c_readiness(resources: ProcessResources) -> GateCStatus:
    """Check all 8 Gate C readiness inputs fail-closed."""
    missing: list[str] = []

    # 1. Business Query capability
    if settings.business_query_mode == "disabled":
        missing.append("business_query_capability")

    # 2. Conversation store
    try:
        store = resources.conversation_store
        if not await store.ping():
            missing.append("conversation_store")
    except Exception:
        missing.append("conversation_store")

    # 3. Join Redis client
    try:
        join_client = resources.join_redis
        if join_client is None:
            missing.append("join_redis")
        elif hasattr(join_client, "ping"):
            res = join_client.ping()
            if asyncio.iscoroutine(res):
                await res
    except Exception:
        missing.append("join_redis")

    # 4. Schema/canary readiness
    if not getattr(settings, "canary_encryption_ready", False):
        missing.append("schema_canary_readiness")

    # 5. Gate B new-write readiness
    if not getattr(settings, "gate_b_new_write_ready", False):
        missing.append("gate_b_new_write_readiness")

    # 6. Zero-plaintext backfill receipt -- parsed and validated, not merely present
    raw_receipt = getattr(settings, "zero_plaintext_backfill_receipt", None)
    if not _zero_plaintext_receipt_is_valid(raw_receipt):
        missing.append("zero_plaintext_backfill_receipt")

    # 7. Frozen trace retention -- must equal the frozen value exactly
    retention = getattr(settings, "frozen_trace_retention_days", None)
    if retention != FROZEN_TRACE_RETENTION_DAYS:
        missing.append("frozen_trace_retention")

    # 8. Frozen decrypt roles -- must equal the frozen single role exactly
    decrypt_roles = getattr(settings, "frozen_decrypt_roles", None)
    if decrypt_roles is None or tuple(decrypt_roles) != FROZEN_DECRYPT_ROLES:
        missing.append("frozen_decrypt_roles")

    return GateCStatus(
        is_ready=len(missing) == 0,
        missing_inputs=tuple(missing),
    )
