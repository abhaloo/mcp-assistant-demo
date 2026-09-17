"""Scope binding and reauthorization contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.auth import Principal

if TYPE_CHECKING:
    from app.business_query.compile.pagination.plan_store import StoredPlan
    from app.business_query.seal.action_tokens import ResultPageCursor


@dataclass(frozen=True)
class ScopeBinding:
    """Encapsulates tenant, user, project, and policy scope values for equality checks."""

    user_id: str | None
    entity_id: str | None
    department_id: str | None
    policy_hash: str | None
    project_id: str | None
    bundle_hash: str | None = None

    @classmethod
    def from_principal(
        cls,
        principal: Principal,
        *,
        project_id: str | None = None,
        bundle_hash: str | None = None,
    ) -> ScopeBinding:
        dept = (
            principal.scope_values.department_id
            if principal.scope_values is not None
            and principal.scope_values.department_id is not None
            else principal.department_id
        )
        return cls(
            user_id=str(principal.user_id) if principal.user_id is not None else None,
            entity_id=str(principal.entity_id) if principal.entity_id is not None else None,
            department_id=str(dept) if dept is not None else None,
            policy_hash=principal.manifest_hash or "",
            project_id=project_id,
            bundle_hash=bundle_hash,
        )

    @classmethod
    def from_cursor(cls, cursor: ResultPageCursor) -> ScopeBinding:
        cursor_user_id = str(
            cursor.principal.user_id
            if isinstance(cursor.principal, Principal)
            else cursor.principal
        )
        return cls(
            user_id=cursor_user_id,
            entity_id=str(cursor.entity_id) if cursor.entity_id is not None else None,
            department_id=str(cursor.department_id) if cursor.department_id is not None else None,
            policy_hash=cursor.policy_hash,
            project_id=cursor.project_id,
            bundle_hash=cursor.bundle_hash,
        )

    @classmethod
    def from_stored_plan(cls, stored: StoredPlan) -> ScopeBinding:
        return cls(
            user_id=str(stored.principal) if stored.principal is not None else None,
            entity_id=str(stored.entity_id) if stored.entity_id is not None else None,
            department_id=str(stored.department_id) if stored.department_id is not None else None,
            policy_hash=stored.policy_hash,
            project_id=stored.project_id,
            bundle_hash=stored.bundle_hash,
        )

    def verify_caller(self, caller: ScopeBinding, current_project_id: str) -> bool:
        """Verify that a cursor scope matches the executing caller."""
        if self.project_id != current_project_id:
            return False
        if self.user_id != caller.user_id:
            return False
        if self.entity_id is not None and self.entity_id != caller.entity_id:
            return False
        if self.department_id is not None and self.department_id != caller.department_id:
            return False
        if self.policy_hash and self.policy_hash != caller.policy_hash:
            return False
        return True

    def verify_stored(
        self,
        cursor: ScopeBinding,
        caller: ScopeBinding,
        current_project_id: str,
        bundle_content_hash: str,
    ) -> bool:
        """Verify that a stored plan scope matches cursor, caller, and bundle."""
        if self.project_id is not None:
            if self.project_id != current_project_id:
                return False
            if cursor.project_id and self.project_id != cursor.project_id:
                return False
        if self.user_id is not None and self.user_id != cursor.user_id:
            return False
        if self.entity_id is not None:
            if cursor.entity_id is None or self.entity_id != cursor.entity_id:
                return False
            if caller.entity_id is None or self.entity_id != caller.entity_id:
                return False
        if self.department_id is not None:
            if cursor.department_id is None or self.department_id != cursor.department_id:
                return False
            if caller.department_id is None or self.department_id != caller.department_id:
                return False
        if self.policy_hash is not None:
            if cursor.policy_hash != self.policy_hash:
                return False
            if caller.policy_hash != self.policy_hash:
                return False
        if self.bundle_hash is not None:
            if cursor.bundle_hash != self.bundle_hash:
                return False
            if bundle_content_hash != self.bundle_hash:
                return False
        return True
