"""Tier scope value object for SQL and document table-level authorization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.auth import Principal


@dataclass(frozen=True)
class TierScope:
    """Authorized tier collection with explicit expansion semantics."""

    tiers: tuple[str, ...]
    is_wildcard: bool = True

    @classmethod
    def wildcard(cls, tiers: Sequence[str]) -> TierScope:
        """Create a tier scope that expands 'admin' to all tiers."""
        return cls(tiers=tuple(tiers), is_wildcard=True)

    @classmethod
    def exact(cls, tiers: Sequence[str]) -> TierScope:
        """Create a tier scope that never expands 'admin' to all tiers."""
        return cls(tiers=tuple(tiers), is_wildcard=False)

    @classmethod
    def for_principal(cls, principal: Principal) -> TierScope:
        """Construct scope from principal, fail-closed against implicit expansion for v2."""
        is_v2 = getattr(principal, "is_v2", False)
        raw_tiers = getattr(principal, "access_tiers", ()) or ()
        return cls(tiers=tuple(raw_tiers), is_wildcard=not is_v2)
