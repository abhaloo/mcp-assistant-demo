"""Exact-wire SQL arm capability probes before matrix reservation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.experiments.core.cost import price_or_ceiling
from app.experiments.sql_freeze import SqlArm

__all__ = [
    "CANARY_HARD_CAP_USD",
    "SQL_TOOL_NAMES",
    "SqlArmCanaryReceipt",
    "SqlPairUnsupported",
    "compute_sql_route_identity_hash",
    "probe_sql_arm_exact_wire",
    "run_sql_arm_canaries",
]

CANARY_HARD_CAP_USD = 0.02
SQL_TOOL_NAMES = ("sql_db_list_tables", "sql_db_schema", "sql_db_query")
TRUSTED_CANARY_CASE_ID = "canary-table-count"
TRUSTED_CANARY_CASE = {
    "id": TRUSTED_CANARY_CASE_ID,
    "role": "warehouse",
    "permissions": ["view inventory", "inventory dashboard"],
    "question": "How many inventory items are there?",
    "gold_sql": "SELECT COUNT(*) FROM inventory_items",
    "ordered": False,
}


class SqlPairUnsupported(RuntimeError):
    """One arm failed exact-wire capability before matrix reservation."""


@dataclass(frozen=True)
class SqlArmCanaryReceipt:
    arm_name: str
    case_id: str
    api_class: str
    tool_names: tuple[str, ...]
    schema_tool_choice: str
    route_identity_hash: str
    successful_tool_calls: int
    cost_usd: float
    request_id: str | None
    actual_model: str | None
    match: bool
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_sql_route_identity_hash(arm: SqlArm) -> str:
    payload = {
        "name": arm.name,
        "chat_deployment": arm.chat_deployment,
        "reasoning_effort": arm.reasoning_effort,
        "requested_provider": arm.requested_provider,
        "provider_order": list(arm.provider_order),
        "verbosity": arm.verbosity,
        "use_responses_api": arm.use_responses_api,
        "api_class": arm.api_class,
    }
    if arm.api_class == "deepseek_chat" or arm.requested_provider == "deepseek_direct":
        base_url = settings.deepseek_direct_base_url or "https://api.deepseek.com"
        payload["base_url_host"] = urlparse(base_url).netloc or "api.deepseek.com"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _receipt_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _api_class_for_arm(arm: SqlArm) -> str:
    if arm.api_class:
        return arm.api_class
    if arm.use_responses_api:
        return "responses"
    if arm.requested_provider == "openrouter":
        return "openrouter_chat"
    return "chat_completions"


def probe_sql_arm_exact_wire(
    arm: SqlArm,
    case: dict[str, Any],
    *,
    run_case_fn: Callable[..., dict[str, Any]],
    hard_cap_usd: float = CANARY_HARD_CAP_USD,
) -> SqlArmCanaryReceipt:
    """Run one trusted case through the same ``run_case`` path as the matrix."""
    run_kwargs: dict[str, Any] = {
        "chat_deployment": arm.chat_deployment,
        "reasoning_effort": arm.reasoning_effort,
        "request_timeout_s": arm.request_timeout_s,
    }
    if arm.verbosity is not None:
        run_kwargs["verbosity"] = arm.verbosity
    if arm.provider_order:
        run_kwargs["provider_order"] = list(arm.provider_order)
    if arm.use_responses_api:
        run_kwargs["use_responses_api"] = True

    result = run_case_fn(case, frozenset(), **run_kwargs)
    tool_events = result.get("tool_events_scrubbed") or result.get("tool_events") or []
    tool_names = tuple(
        sorted({str(event.get("tool")) for event in tool_events if event.get("tool")})
    )
    successful_tool_calls = sum(
        1 for event in tool_events if event.get("tool") and not event.get("error")
    )
    priced = price_or_ceiling(
        deployment=result.get("chat_deployment") or arm.chat_deployment,
        tokens_in=int(result.get("tokens_prompt") or 0),
        tokens_out=int(result.get("tokens_completion") or 0),
        n_llm_calls=int(result.get("n_llm_calls") or 0),
        ceiling_usd=hard_cap_usd,
    )
    if priced.usd > hard_cap_usd:
        raise SqlPairUnsupported(
            f"arm {arm.name!r} canary cost ${priced.usd:.6f} exceeds hard cap ${hard_cap_usd:.4f}"
        )
    if successful_tool_calls < 1:
        raise SqlPairUnsupported(f"arm {arm.name!r} canary produced no successful tool calls")
    if not bool(result.get("match")):
        raise SqlPairUnsupported(f"arm {arm.name!r} canary did not match trusted case")

    route_identity_hash = compute_sql_route_identity_hash(arm)
    body = {
        "arm_name": arm.name,
        "case_id": str(case.get("id")),
        "api_class": _api_class_for_arm(arm),
        "tool_names": tool_names,
        "schema_tool_choice": "any",
        "route_identity_hash": route_identity_hash,
        "successful_tool_calls": successful_tool_calls,
        "cost_usd": priced.usd,
        "request_id": result.get("request_id"),
        "actual_model": result.get("actual_model") or result.get("response_model"),
        "match": bool(result.get("match")),
    }
    return SqlArmCanaryReceipt(
        **body,
        receipt_hash=_receipt_hash(body),
    )


def run_sql_arm_canaries(
    arms: tuple[SqlArm, ...],
    case: dict[str, Any],
    *,
    run_case_fn: Callable[..., dict[str, Any]],
    reserve_fn: Callable[[float], Any] | None = None,
) -> list[SqlArmCanaryReceipt]:
    """Probe every arm; refuse matrix reservation when any arm fails."""
    receipts: list[SqlArmCanaryReceipt] = []
    for arm in arms:
        receipts.append(probe_sql_arm_exact_wire(arm, case, run_case_fn=run_case_fn))
    if reserve_fn is not None:
        reserve_fn(sum(receipt.cost_usd for receipt in receipts))
    return receipts
