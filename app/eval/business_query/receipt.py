"""Integrity checks for an answered eval run's executor receipt."""

from __future__ import annotations


def receipt_valid(
    case: dict,
    run: dict,
    *,
    bundle_hash: str,
    manifest_hash: str,
) -> bool:
    if run["outcome"] != "answered":
        return True
    receipt = run["receipt"]
    if receipt is None or not receipt.get("answer_query_id"):
        return False
    if receipt.get("bundle_hash") != bundle_hash or receipt.get("manifest_hash") != manifest_hash:
        return False
    if case.get("scoring") == "total_count":
        return run["total_row_count"] is not None
    return receipt.get("row_count") == run["total_row_count"]
