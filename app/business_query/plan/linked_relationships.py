"""Declarative linked relationships and cross-turn anaphoric reference resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business_query.definitions import DefinitionBundle
    from app.business_query.wire.request import BusinessQueryRequest

# Shared child noun vocabularies across entity join specifications
JOB_NOUNS: tuple[str, ...] = ("jobs", "work orders", "kazi", "job", "work order")
INVOICE_NOUNS: tuple[str, ...] = ("bills", "invoices", "ankara", "bill", "invoice")
QUOTATION_NOUNS: tuple[str, ...] = ("quotes", "quotations", "nukuu", "quote", "quotation")
PAYABLE_QUOTATION_NOUNS: tuple[str, ...] = ("payable quotes", "payable quotations", "vendor quotes")
ORDER_NOUNS: tuple[str, ...] = ("orders", "customer orders", "oda", "order", "customer order")
WORK_ORDER_ITEM_NOUNS: tuple[str, ...] = (
    "materials",
    "vifaa",
    "items",
    "material",
    "kifaa",
    "item",
)
LINE_ITEM_NOUNS: tuple[str, ...] = (
    "items",
    "lines",
    "invoice items",
    "quote items",
    "payable quote items",
    "products",
    "item",
    "line",
    "product",
    "bidhaa",
)
PAYMENT_NOUNS: tuple[str, ...] = (
    "payments",
    "ledger entries",
    "journals",
    "settlements",
    "malipo",
    "payment",
)
SUPPLIER_INVENTORY_NOUNS: tuple[str, ...] = (
    "receipts",
    "deliveries",
    "stock receipts",
    "inventory",
    "grn",
    "mapokezi",
)
JOB_INVENTORY_NOUNS: tuple[str, ...] = (
    "inventory",
    "stock",
    "materials consumed",
    "vifaa",
)


@dataclass(frozen=True)
class LinkedRelationship:
    """Declarative parent-to-child entity link for semantic joins and anaphora."""

    parent_resource: str
    child_resource: str
    child_nouns: tuple[str, ...]
    link_dimension: str


LINKED_RELATIONSHIPS: tuple[LinkedRelationship, ...] = (
    LinkedRelationship(
        parent_resource="customer_order",
        child_resource="job",
        child_nouns=JOB_NOUNS,
        link_dimension="customer_order_number",
    ),
    LinkedRelationship(
        parent_resource="customer_order",
        child_resource="invoice",
        child_nouns=INVOICE_NOUNS,
        link_dimension="customer_order_number",
    ),
    LinkedRelationship(
        parent_resource="customer_order",
        child_resource="quotation",
        child_nouns=QUOTATION_NOUNS,
        link_dimension="customer_order_number",
    ),
    LinkedRelationship(
        parent_resource="customer",
        child_resource="customer_order",
        child_nouns=ORDER_NOUNS,
        link_dimension="customer_id",
    ),
    LinkedRelationship(
        parent_resource="customer",
        child_resource="invoice",
        child_nouns=INVOICE_NOUNS,
        link_dimension="customer_id",
    ),
    LinkedRelationship(
        parent_resource="customer",
        child_resource="quotation",
        child_nouns=QUOTATION_NOUNS,
        link_dimension="customer_id",
    ),
    LinkedRelationship(
        parent_resource="customer",
        child_resource="job",
        child_nouns=("jobs", "kazi", "job"),
        link_dimension="customer_id",
    ),
    LinkedRelationship(
        parent_resource="customer",
        child_resource="payable_quotation",
        child_nouns=PAYABLE_QUOTATION_NOUNS,
        link_dimension="customer_id",
    ),
    LinkedRelationship(
        parent_resource="job",
        child_resource="work_order_item",
        child_nouns=WORK_ORDER_ITEM_NOUNS,
        link_dimension="work_order_id",
    ),
    LinkedRelationship(
        parent_resource="invoice",
        child_resource="invoice_item",
        child_nouns=LINE_ITEM_NOUNS,
        link_dimension="bill_id",
    ),
    LinkedRelationship(
        parent_resource="quotation",
        child_resource="quotation_item",
        child_nouns=LINE_ITEM_NOUNS,
        link_dimension="bill_id",
    ),
    LinkedRelationship(
        parent_resource="payable_quotation",
        child_resource="payable_quotation_item",
        child_nouns=LINE_ITEM_NOUNS,
        link_dimension="bill_id",
    ),
    LinkedRelationship(
        parent_resource="invoice",
        child_resource="ledger_entry",
        child_nouns=PAYMENT_NOUNS,
        link_dimension="bill_id",
    ),
    LinkedRelationship(
        parent_resource="invoice",
        child_resource="job",
        child_nouns=JOB_NOUNS,
        link_dimension="effective_bill_id",
    ),
    LinkedRelationship(
        parent_resource="supplier",
        child_resource="inventory",
        child_nouns=SUPPLIER_INVENTORY_NOUNS,
        link_dimension="supplier_id",
    ),
    LinkedRelationship(
        parent_resource="job",
        child_resource="inventory",
        child_nouns=JOB_INVENTORY_NOUNS,
        link_dimension="job_id",
    ),
)

_HISTORY_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "customer_order": re.compile(
        r"(?<!\w)(?:customer\s+)?order\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)", re.IGNORECASE
    ),
    "customer": re.compile(
        r"(?<!\w)customer\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)", re.IGNORECASE
    ),
    "job": re.compile(
        r"(?<!\w)(?:work\s+)?job\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)", re.IGNORECASE
    ),
    "invoice": re.compile(
        r"(?<!\w)(?:invoice|bill)\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)", re.IGNORECASE
    ),
    "quotation": re.compile(
        r"(?<!\w)(?:quote|quotation|nukuu)\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)", re.IGNORECASE
    ),
    "payable_quotation": re.compile(
        r"(?<!\w)(?:payable\s+quotation|vendor\s+quote|supplier\s+quote)\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)",
        re.IGNORECASE,
    ),
    "supplier": re.compile(
        r"(?<!\w)(?:supplier|vendor|muuzaji)\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)",
        re.IGNORECASE,
    ),
    "inventory": re.compile(
        r"(?<!\w)(?:inventory|stock\s+receipt|grn|receipt)\s*(?:#|no\.?|id|number)?\s*([0-9]+)(?!\w)",
        re.IGNORECASE,
    ),
}


def find_child_relationship(
    parent_resource: str | None, text: str | None
) -> LinkedRelationship | None:
    """Find the first linked relationship matching parent resource and child nouns."""
    if not parent_resource or not text:
        return None
    lowered = text.casefold()
    for rel in LINKED_RELATIONSHIPS:
        if rel.parent_resource != parent_resource:
            continue
        for noun in sorted(rel.child_nouns, key=len, reverse=True):
            if re.search(rf"(?<!\w){re.escape(noun)}(?!\w)", lowered):
                return rel
    return None


def find_antecedent_record(
    request: BusinessQueryRequest, bundle: DefinitionBundle
) -> tuple[str, str] | None:
    """Find the antecedent entity resource and record id from owner hint or history."""
    from app.business_query.plan.record_referents import extract_explicit_record_reference

    if request.owner_hint is not None:
        return (request.owner_hint.resource_type, str(request.owner_hint.record_id))

    history = getattr(request, "history", ()) or ()
    # Prioritize human queries in dialogue history for conversational topic
    for role_filter in ("human", None):
        for role, text in reversed(history):
            if role_filter is not None and role != role_filter:
                continue
            if not text:
                continue
            ref = extract_explicit_record_reference(text, bundle)
            if ref is not None:
                return (ref.resource, str(ref.record_id))
            for res_type, pat in _HISTORY_ID_PATTERNS.items():
                match = pat.search(text)
                if match:
                    return (res_type, match.group(1))
    return None


def resolve_anaphora(
    question: str, parent_resource: str, record_id: str, rel: LinkedRelationship
) -> str:
    """Rewrite question anaphora into an explicit parent reference."""
    if not question:
        return ""
    parent_label = (
        f"order {record_id}"
        if parent_resource == "customer_order"
        else f"{parent_resource} {record_id}"
    )

    # Swahili possessives: zake, yake, vyake, chake, wake
    if re.search(r"(?i)\b(zake|yake)\b", question):
        return re.sub(r"(?i)\b(zake|yake)\b", f"za {parent_label}", question)
    if re.search(r"(?i)\bvyake\b", question):
        return re.sub(r"(?i)\bvyake\b", f"vya {parent_label}", question)
    if re.search(r"(?i)\bchake\b", question):
        return re.sub(r"(?i)\bchake\b", f"cha {parent_label}", question)
    if re.search(r"(?i)\bwake\b", question):
        return re.sub(r"(?i)\bwake\b", f"wa {parent_label}", question)

    # English: did it use -> were used for <parent>
    if re.search(r"(?i)\bdid\s+it\s+use\b", question):
        return re.sub(r"(?i)\bdid\s+it\s+use\b", f"were used for {parent_label}", question)

    # English: its <noun> / their <noun> -> the <noun> of <parent>
    noun_pattern = "|".join(re.escape(n) for n in rel.child_nouns)
    its_pattern = rf"(?i)\b(its|their)\s+({noun_pattern})\b"
    if re.search(its_pattern, question):
        return re.sub(its_pattern, rf"the \2 of {parent_label}", question)

    # English: this <parent> / the <parent>
    this_parent_pattern = rf"(?i)\b(this|the)\s+{re.escape(parent_resource.replace('_', ' '))}\b"
    if re.search(this_parent_pattern, question):
        return re.sub(this_parent_pattern, parent_label, question)

    # Fallback when antecedent and child noun are present but label is missing
    if parent_label not in question:
        return f"{question.rstrip('?')} for {parent_label}?"
    return question


def resolve_linked_anaphora(
    request: BusinessQueryRequest, bundle: DefinitionBundle
) -> BusinessQueryRequest | None:
    """Resolve anaphora if question references a child of a historical antecedent."""
    if not request.question:
        return None
    antecedent = find_antecedent_record(request, bundle)
    if antecedent is None:
        return None
    parent_resource, parent_id = antecedent
    rel = find_child_relationship(parent_resource, request.question)
    if rel is None:
        return None
    rewritten_q = resolve_anaphora(request.question, parent_resource, parent_id, rel)
    return request.model_copy(update={"question": rewritten_q, "owner_hint": None})
