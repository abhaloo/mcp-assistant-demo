"""Live + snapshot registry for per-request QueryTrace objects (ADR 0047).

BusinessQueryModule binds a QueryTrace as "live" when a request starts, and
moves it into a bounded LRU "snapshot" cache when the request finishes, so a
caller entitled to see trace detail (BusinessQueryModule.trace_for, in turn
used by app.services.business_query_telemetry.apply_query_trace) can look one
up either while the query is still running or after it has completed. The
snapshot side is bounded so a long-lived server process does not retain an
unbounded number of finished traces.

Extracted from BusinessQueryModule so the caching responsibility has one
explicit, independently testable seam; BusinessQueryModule keeps a single
`_trace_cache: TraceSnapshotCache` field and delegates.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from app.business_query.wire.trace import QueryTrace


class TraceSnapshotCache:
    """Thread-safe live-trace registry plus a bounded LRU snapshot cache."""

    def __init__(self, *, limit: int) -> None:
        self._limit = limit
        self._live: dict[str, QueryTrace] = {}
        self._snapshots: OrderedDict[str, QueryTrace] = OrderedDict()
        self._lock = threading.Lock()

    def bind(self, correlation_id: str, trace: QueryTrace) -> None:
        """Register `trace` as the live (in-flight) trace for `correlation_id`."""
        with self._lock:
            self._live[correlation_id] = trace

    def record(self, correlation_id: str, trace: QueryTrace) -> None:
        """Move the finished trace into the bounded snapshot cache.

        Evicts the least-recently-recorded snapshot once the cache exceeds
        `limit`. Pops `correlation_id` from the live registry regardless of
        whether it was still present there.
        """
        with self._lock:
            self._live.pop(correlation_id, None)
            self._snapshots[correlation_id] = trace
            self._snapshots.move_to_end(correlation_id)
            while len(self._snapshots) > self._limit:
                self._snapshots.popitem(last=False)

    def snapshot(self, correlation_id: str) -> QueryTrace:
        """Look up a trace: live first, then the finished-trace cache.

        Raises KeyError if `correlation_id` is in neither -- callers (e.g.
        `apply_query_trace`) rely on that to mean "no such query".
        """
        with self._lock:
            live = self._live.get(correlation_id)
            if live is not None:
                return live
            return self._snapshots[correlation_id]

    def is_live(self, correlation_id: str) -> bool:
        """True while `correlation_id`'s query is still in flight."""
        with self._lock:
            return correlation_id in self._live
