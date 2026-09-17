"""Contextual Page Element Highlight & Tab Switch Action Rules (Cycle UX-7A)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.models.schemas import ClientAction

if TYPE_CHECKING:
    from app.business_query.plan import BusinessQueryPlan
    from app.models.schemas import RecordLink

_SELECTOR_REGEX = re.compile(r"^#[a-zA-Z0-9_-]{1,64}$")


def validate_target_selector(selector: str | None) -> str | None:
    """Validate selector against strict element ID whitelist to prevent DOM injection."""
    if not selector or not isinstance(selector, str):
        return None
    cleaned = selector.strip()
    if _SELECTOR_REGEX.match(cleaned):
        return cleaned
    return None


def derive_client_action(
    question: str,
    plan: BusinessQueryPlan | None = None,
    record_links: list[RecordLink] | None = None,
) -> ClientAction | None:
    """Derive contextual client actions from user query and plan."""
    if not question:
        return None

    q_lower = question.lower()

    # Webhook or settings configuration highlight
    if "webhook" in q_lower or "api key" in q_lower:
        selector = validate_target_selector("#webhook-config-card")
        return ClientAction(
            action="highlight_element",
            tab_key="settings",
            target_selector=selector,
            label="Webhook Configuration",
        )

    # Invoices / AR aging navigation
    if (
        "ar aging" in q_lower
        or "ar_aging" in q_lower
        or (plan is not None and plan.bucket_set == "ar_aging")
    ):
        return ClientAction(
            action="switch_tab",
            tab_key="ar_aging",
            label="AR Aging",
        )

    if "invoice" in q_lower or "bill" in q_lower:
        return ClientAction(
            action="switch_tab",
            tab_key="invoices",
            label="Invoices",
        )

    # Customer orders navigation
    if "order" in q_lower or "customer order" in q_lower:
        return ClientAction(
            action="switch_tab",
            tab_key="orders",
            label="Orders",
        )

    # Inventory navigation
    if "inventory" in q_lower or "stock" in q_lower:
        return ClientAction(
            action="switch_tab",
            tab_key="inventory",
            label="Inventory",
        )

    return None
