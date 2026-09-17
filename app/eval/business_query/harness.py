"""Shared spend ceiling, principals, and eval-database helpers for BQ CLIs."""

from __future__ import annotations

from datetime import date

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url

from app.auth import Principal
from app.policy.manifest_loader import manifest_content_hash

EVAL_SNAPSHOT = {
    "views_total": 59,
    "semantic_views": 8,
    "bills": 4830,
}

BUSINESS_DATE = date(2026, 7, 15)
EVAL_ENTITY_ID = 1

BASE_PERMISSIONS = [
    "view invoice",
    "view job",
    "view customer order",
    "view customer",
    "view journal entries",
    "view inventory",
    "view quotation",
]


class SpendCeiling(RuntimeError):
    pass


def make_recorder(counter: dict, ceiling: int):
    def record(label: str) -> None:
        counter[label] = counter.get(label, 0) + 1
        if sum(counter.values()) > ceiling:
            raise SpendCeiling(f"call ceiling {ceiling} reached")

    return record


def eval_principal(case: dict) -> Principal:
    override = case.get("principal_override") or {}
    return Principal(
        user_id=90002,
        role="eval-business-query",
        permissions=list(override.get("permissions", BASE_PERMISSIONS)),
        entity_id=override.get("entity_id", EVAL_ENTITY_ID),
        cross_entity=override.get("cross_entity", False),
        manifest_hash=manifest_content_hash(),
    )


def canary_principal(profile: dict, override: dict | None) -> Principal:
    fields = {
        "user_id": 90001,
        "role": "canary-operational",
        "permissions": list(profile["permissions"]),
        "entity_id": profile["entity_id"],
        "cross_entity": profile["cross_entity"],
        "manifest_hash": manifest_content_hash(),
    }
    if override:
        fields.update(override)
    return Principal(**fields)


def assert_eval_snapshot(*, views_total: int, semantic_views: int, bills: int) -> None:
    if views_total != EVAL_SNAPSHOT["views_total"]:
        raise SystemExit(
            f"PREFLIGHT FAIL: expected {EVAL_SNAPSHOT['views_total']} total views, "
            f"found {views_total}"
        )
    if semantic_views != EVAL_SNAPSHOT["semantic_views"]:
        raise SystemExit(
            f"PREFLIGHT FAIL: expected {EVAL_SNAPSHOT['semantic_views']} semantic views, "
            f"found {semantic_views}"
        )
    if bills != EVAL_SNAPSHOT["bills"]:
        raise SystemExit(f"PREFLIGHT FAIL: expected {EVAL_SNAPSHOT['bills']} bills, found {bills}")


def billing_engine(db_name: str):
    url = dotenv_values(".env").get("MCP_BILLING_DATABASE_URL")
    if not url:
        raise SystemExit("MCP_BILLING_DATABASE_URL is not set in .env")
    return create_engine(make_url(url).set(database=db_name), pool_pre_ping=True)
