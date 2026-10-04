"""One-time v2 eval tokens carrying the claims billing mints for a person.

The verifier consumes every ``jti`` once, so callers mint one token per
request. Profiles mirror the personas the intent/evidence pack names. Document
tiers come from the same role/permission mapping the product applies. Record
resource grants are only those a profile declares: an undeclared profile has
no record access, so a record-context case needs its grants written here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.eval.ask_route.harness import record_access_claim
from scripts.ops.loadtest_ask import mint_token

DEFAULT_PROFILE = "finance"

# The actions Billing grants on every record resource a "view" permission covers.
_RECORD_ACTIONS: tuple[str, ...] = ("search", "read", "link")


@dataclass(frozen=True)
class EvalPrincipalProfile:
    role: str
    permissions: tuple[str, ...]
    entity_id: int = 1
    # Record resource grants as (resource, actions) pairs; empty means none.
    resources: tuple[tuple[str, tuple[str, ...]], ...] = ()


PROFILES: dict[str, EvalPrincipalProfile] = {
    "finance": EvalPrincipalProfile(
        role="finance",
        permissions=(
            "outstanding receivable",
            "bill overdue",
            "view journal entries",
            "customer statement",
            "view invoice",
        ),
        resources=(("invoice", _RECORD_ACTIONS),),
    ),
    "guest": EvalPrincipalProfile(role="guest", permissions=()),
    "operations": EvalPrincipalProfile(role="printing", permissions=("view job",)),
    "sales": EvalPrincipalProfile(
        role="sales", permissions=("view customer", "view customer order")
    ),
    # Billing's admin holds every "view" permission the definition bundle
    # requires; capability visibility has no admin bypass, so an admin with
    # no permissions would see an empty capability card.
    "admin": EvalPrincipalProfile(
        role="admin",
        permissions=(
            "view customer",
            "view customer order",
            "view inventory",
            "view invoice",
            "view job",
            "view journal entries",
            "view payable quotation",
            "view quotation",
            "view supplier",
        ),
        resources=tuple(
            (name, _RECORD_ACTIONS)
            for name in (
                "invoice",
                "quotation",
                "payable_quotation",
                "credit_note",
                "customer_order",
                "job",
                "customer",
                "supplier",
                "product",
                "inventory",
            )
        ),
    ),
}


def eval_record_access(profile: EvalPrincipalProfile) -> dict[str, object]:
    """The strict v2 ``record_access`` claim for a profile, shaped by the one
    builder every eval caller shares."""
    return record_access_claim(
        role=profile.role,
        permissions=list(profile.permissions),
        entity_id=profile.entity_id,
        resources={name: list(actions) for name, actions in profile.resources},
    )


def mint_eval_token(profile_name: str, *, ask_budget: dict[str, str] | None = None) -> str:
    """A fresh single-use token for ``profile_name``; unknown names raise.

    ``ask_budget`` rides inside the strict ``record_access`` claim the way
    Billing's omission rule emits the key — absent entirely when None."""
    profile = PROFILES[profile_name]
    record_access = eval_record_access(profile)
    if ask_budget is not None:
        record_access["ask_budget"] = dict(ask_budget)
    return mint_token(
        profile.role,
        list(profile.permissions),
        extra_claims={
            "tool_result_version": 1,
            "record_access": record_access,
        },
    )
