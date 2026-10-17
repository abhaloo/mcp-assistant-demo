"""One request trace lifecycle for Business Query (ADR 0076)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import BusinessQueryOutcome, attach_resolver
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace

FinalizeRequestTraceFn = Callable[[str, QueryTrace], None]


class BqRequestScopeError(RuntimeError):
    """Programming error on the request lifecycle seam."""


@dataclass
class BqRequestScope:
    """Normalized request, one trace, and a single finish point per turn."""

    request: BusinessQueryRequest
    trace: QueryTrace
    _finalize_trace: FinalizeRequestTraceFn = field(repr=False)
    _finished: bool = field(default=False, repr=False)
    _bound_bundle_hash: str | None = field(default=None, repr=False)

    def bind_plan_bundle(self, bundle: DefinitionBundle) -> None:
        """Record the bundle hash from plan_round for later drift checks."""
        self._bound_bundle_hash = bundle.content_hash

    @property
    def bound_bundle_hash(self) -> str | None:
        return self._bound_bundle_hash

    def finish(self, outcome: BusinessQueryOutcome) -> BusinessQueryOutcome:
        """Attach resolver identity and finalize the request trace once."""
        if self._finished:
            raise BqRequestScopeError("finish called twice on the same request scope")
        self._finished = True
        finalized = attach_resolver(outcome, self.trace.resolver_query_id)
        self._finalize_trace(self.request.correlation_id, self.trace)
        return finalized

    def finalize_if_needed(self) -> None:
        """Copy and cache the trace when finish was not called."""
        if not self._finished:
            self._finalize_trace(self.request.correlation_id, self.trace)
