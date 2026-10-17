"""Public compile seam: scoped plan → SQLAlchemy SELECT."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.sql import Select, Selectable

from app.business_query.authorize.scoping import ScopedDerivedSet, ScopedPlan


class StatementCompiler:
    """Owns SELECT construction. The adapter executes the compiled statement."""

    def __init__(
        self,
        build_select: Callable[[ScopedPlan], Selectable],
        *,
        build_set: Callable[[ScopedDerivedSet], Select[Any]],
    ) -> None:
        self._build_select = build_select
        self._build_set = build_set

    def compile(self, scoped: ScopedPlan) -> Selectable:
        return self._build_select(scoped)

    def compile_set(self, derived: ScopedDerivedSet) -> Select[Any]:
        return self._build_set(derived)
