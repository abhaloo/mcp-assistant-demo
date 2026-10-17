"""
Access tier management: maps user roles/permissions to data access tiers.

Centralizes tier logic — both the permission-to-tier mapping AND the tier
dependency graph live here.
"""

from __future__ import annotations

from app.auth import Principal
from app.rag.tier_scope import TierScope

# ---------------------------------------------------------------------------
# Tier dependency graph
# ---------------------------------------------------------------------------
# After a user earns a tier from their permissions, they also get
# any tiers listed here. This avoids granting full tier access
# when only a subset of tables is needed (e.g., finance users need
# the customers table but not the full sales tier).
#
# Key = tier the user earned
# Value = list of tiers they also need
#
TIER_DEPENDENCIES: dict[str, list[str]] = {
    "finance": ["customers"],
    "warehouse": ["customers"],
    "printing": ["customers"],
}

PERMISSION_EARNED_TIERS = frozenset(
    {"sales", "finance", "admin", "printing", "graphic design", "warehouse"}
)
DERIVED_TIERS = frozenset({"all", "customers"})  # granted unconditionally or by dependency
ALL_TIERS = tuple(sorted(PERMISSION_EARNED_TIERS | DERIVED_TIERS))

# Permission → tier policy: one data table plus one loop.
TIER_PERMISSIONS: dict[str, frozenset[str]] = {
    "sales": frozenset(
        {
            "view invoice",
            "add invoice",
            "edit invoice",
            "delete invoice",
            "view quotation",
            "add quotation",
            "edit quotation",
            "delete quotation",
            "view customer",
            "add customer",
            "edit customer",
            "view customer order",
            "add customer order",
            "edit customer order",
            "delete customer order",
            "cancel customer order",
            "view product",
            "add product",
            "edit product",
        }
    ),
    "finance": frozenset(
        {
            "add journal entry",
            "view journal entries",
            "profit and loss",
            "balance sheet",
            "account statement",
            "trial balance",
            "cash flow statement",
            "view account",
            "add account",
            "edit account",
            "summary",
            "outstanding receivable",
            "payment",
            "bill",
            "bill overdue",
            "customer statement",
            "supplier statement",
            "full dashboard",
        }
    ),
    "admin": frozenset(
        {
            "system audit",
            "setting",
            "view user",
            "add user",
            "edit user",
            "delete user",
            "change permission",
            "view department",
            "add department",
            "edit department",
            "delete department",
        }
    ),
    "printing": frozenset(
        {
            "view job",
            "add job",
            "edit job",
            "delete job",
            "move job",
            "approve job requests",
            "job dashboard",
        }
    ),
    "graphic design": frozenset(
        {
            "view specification",
            "add specification",
            "edit specification",
            "delete specification",
            "view specification item",
            "add specification item",
            "edit specification item",
            "delete specification item",
        }
    ),
    "warehouse": frozenset(
        {
            "view inventory",
            "issue item",
            "receive item",
            "approve issue",
            "approve receive",
            "edit inventory",
            "delete inventory",
            "inventory dashboard",
        }
    ),
}

# Role-level grants apply AFTER dependency resolution. This ordering is
# intentional, not a bug to fix.
ROLE_TIER_GRANTS: dict[str, frozenset[str]] = {
    "manager": frozenset({"sales", "finance", "printing", "graphic design"}),
}

# Table-to-tier mapping — SQL equivalent of the document tier filter.
TIER_TABLES: dict[str, list[str]] = {
    "all": [
        "products",
        "categories",
        "units",
        "departments",
        "ware_houses",
        "work_orders",
        "work_order_items",
    ],
    "customers": [
        "customers",
        "customer_orders",
    ],
    "sales": [
        "customers",
        "customer_orders",
        "customer_order_histories",
    ],
    "finance": [
        "bills",
        "bill_items",
        "journals",
        "expenses",
        "accounts",
        "credit_notes",
        "suppliers",
    ],
    "warehouse": [
        "inventories",
        "inventory_items",
    ],
    "admin": [],
    "printing": [],
    "graphic design": [],
}


def _resolve_dependencies(tiers: set[str]) -> set[str]:
    """
    Expand a set of tiers by following the dependency graph.

    Iterates the original set and adds dependencies to a copy,
    avoiding mutation during iteration.
    """
    expanded = tiers.copy()
    for tier in tiers:
        if tier in TIER_DEPENDENCIES:
            expanded.update(TIER_DEPENDENCIES[tier])
    return expanded


def get_access_tiers(role: str, permissions: list[str]) -> list[str]:
    """Map role + Spatie permissions to access tiers. Admin short-circuits to all."""
    if role == "admin":
        return list(ALL_TIERS)
    perm_set = set(permissions)
    tiers = {"all"} | {t for t, perms in TIER_PERMISSIONS.items() if perms & perm_set}
    tiers = _resolve_dependencies(tiers)
    tiers |= ROLE_TIER_GRANTS.get(role, frozenset())
    return list(tiers)


def document_tiers_for(principal: Principal) -> list[str]:
    """Resolve document tiers. A signed list, including empty, wins over legacy mapping."""
    if principal.document_tiers is not None:
        return list(principal.document_tiers)
    return get_access_tiers(principal.role, principal.permissions)


def get_allowed_tables(scope: TierScope) -> list[str]:
    """Derive allowed SQL tables for the specified tier scope."""
    if not isinstance(scope, TierScope):
        raise TypeError(
            f"get_allowed_tables requires a TierScope instance, got {type(scope).__name__}"
        )
    tiers = ALL_TIERS if ("admin" in scope.tiers and scope.is_wildcard) else scope.tiers
    allowed = [t for tier in tiers for t in TIER_TABLES.get(tier, [])]
    return list(dict.fromkeys(allowed))
