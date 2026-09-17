"""Reauthorize a retained BQ answer under the current signed policy (spec §7).

The oracle is exact scope equality: the retained plan is re-scoped for the
current principal and bundle, and its fingerprint (plan + forced predicates +
response policy) must equal the fingerprint sealed on the original receipt.
Nothing is filtered or narrowed; any difference denies.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.auth import Principal
from app.business_query.authorize.scoping import (
    ScopeDenied,
    ScopedPlan,
    apply_role_scope,
    bind_scope_context,
    scoped_plan_fingerprint,
)
from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.definitions import (
    BundleSelectionError,
    BundleValidationError,
    DefinitionBundle,
    InvalidBundleIndexError,
    bundle_for_manifest,
)
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import BusinessQueryPlan
from app.conversation.evidence.contracts import EvidenceSnapshot

logger = logging.getLogger(__name__)

ScopeFn = Callable[[BusinessQueryPlan, Principal, DefinitionBundle], ScopedPlan]
# Anything the scoping algorithm refuses or cannot process denies the restore;
# ValueError/TypeError cover model validation inside re-scoping and fingerprinting.
_RESCOPE_ERRORS = (
    ScopeDenied,
    PlanRefused,
    BundleSelectionError,
    BundleValidationError,
    InvalidBundleIndexError,
    ValueError,
    TypeError,
)


class RetainedScopeAuthorizer:
    def __init__(
        self,
        *,
        bundle_resolver: Callable[[str], DefinitionBundle] | None = None,
        scope_fn: ScopeFn | None = None,
    ) -> None:
        self._bundle_resolver = bundle_resolver or bundle_for_manifest
        self._scope_fn = scope_fn or apply_role_scope

    def authorize_retained_scope(self, snapshot: EvidenceSnapshot, principal: Principal) -> bool:
        """True only when every retained plan re-scopes to its sealed fingerprint."""
        if not principal.manifest_hash:
            return False
        try:
            bundle = self._bundle_resolver(principal.manifest_hash)
        except _RESCOPE_ERRORS as exc:
            logger.warning("restore bundle unresolved: %s", type(exc).__name__)
            return False
        return all(
            self._plan_matches(stored, principal, bundle)
            for stored in snapshot.payload.stored_plans
        )

    def _plan_matches(
        self, stored: StoredPlan, principal: Principal, bundle: DefinitionBundle
    ) -> bool:
        bindings_agree = (
            (not stored.bundle_hash or bundle.content_hash == stored.bundle_hash)
            and (not stored.policy_hash or principal.manifest_hash == stored.policy_hash)
            and (stored.entity_id is None or str(stored.entity_id) == str(principal.entity_id))
            and (
                stored.department_id is None
                or str(stored.department_id) == str(principal.department_id)
            )
            and (stored.principal is None or str(stored.principal) == str(principal.user_id))
        )
        if not bindings_agree:
            return False
        frozen_date = (
            stored.derived_payload.business_date if stored.derived_payload is not None else None
        )
        try:
            scoped = bind_scope_context(
                self._scope_fn(stored.plan, principal, bundle),
                principal=principal,
                bundle_hash=bundle.content_hash,
                business_date=frozen_date,
                response_policy=stored.response_policy,
            )
            return scoped_plan_fingerprint(scoped) == stored.plan_fingerprint
        except _RESCOPE_ERRORS as exc:
            logger.warning(
                "restore re-scope denied aqid=%s: %s", stored.answer_query_id, type(exc).__name__
            )
            return False
