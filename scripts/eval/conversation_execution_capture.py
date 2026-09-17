"""Append-only execution capture for coordinator canaries.

Records begin/attempt/end at bound seams. Does not store questions, SQL, or rows.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.telemetry.correlation import current_correlation_id, current_thread_id
from scripts.eval.conversation_capture_fields import extra_from_call, extract_search_fields

OPERATIONS: tuple[str, ...] = (
    "coordinator_decision",
    "answer_generation",
    "evidence_restore",
    "planner",
    "business_sql",
    "document_retrieval",
    "record_rehydration",
    "condense",
)

IO_OPERATIONS: tuple[str, ...] = ("snapshot_io", "transcript_io")

_FORBIDDEN = frozenset(
    {
        "question",
        "sql",
        "statement",
        "rows",
        "record",
        "records",
        "params",
        "content",
        "messages",
    }
)


def forbidden_payload_keys() -> frozenset[str]:
    return _FORBIDDEN


def _thread_binding(explicit: object) -> dict[str, str]:
    """The client thread this event belongs to, when the request bound one."""
    thread_id = explicit if isinstance(explicit, str) and explicit else current_thread_id()
    return {"thread_id": thread_id} if thread_id else {}


def _public_record(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k not in _FORBIDDEN}


class ExecutionCapture:
    """JSONL capture with monotonic sequence and fail-closed write tracking."""

    def __init__(
        self,
        path: Path,
        *,
        session_id: str,
        build_identity: str,
        process_id: int | None = None,
        max_workers: int = 1,
        worker_id: int = 0,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.build_identity = build_identity
        self.process_id = process_id if process_id is not None else os.getpid()
        self.max_workers = max_workers
        self.worker_id = worker_id
        self.failed = False
        self.closed = False
        self.cancelled = False
        self._seq = 0
        self._lock = threading.Lock()
        self._handle = path.open("a", encoding="utf-8")

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        session_id: str,
        build_identity: str,
        process_id: int | None = None,
        max_workers: int = 1,
        worker_id: int = 0,
    ) -> ExecutionCapture:
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(
            path,
            session_id=session_id,
            build_identity=build_identity,
            process_id=process_id,
            max_workers=max_workers,
            worker_id=worker_id,
        )

    def next_sequence(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _append_line(self, line: str) -> None:
        self._handle.write(line)
        self._handle.flush()

    def record(self, phase: str, operation: str, **extra: Any) -> None:
        payload = _public_record(
            {
                "phase": phase,
                "operation": operation,
                "sequence": self.next_sequence(),
                "correlation_id": extra.pop("correlation_id", None)
                or current_correlation_id()
                or "",
                "session_id": self.session_id,
                "build_identity": self.build_identity,
                "process_id": self.process_id,
                "worker_id": extra.pop("worker_id", self.worker_id),
                "max_workers": extra.pop("max_workers", self.max_workers),
                "monotonic_ns": time.monotonic_ns(),
                **_thread_binding(extra.pop("thread_id", None)),
                **extra,
            }
        )
        try:
            self._append_line(json.dumps(payload, separators=(",", ":")) + "\n")
        except OSError:
            self.failed = True
            flag = self.path.with_name(self.path.name + ".failed")
            try:
                flag.write_text("capture write failure\n", encoding="utf-8")
            except OSError:
                pass

    def wrap(self, operation: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        capture = self
        if inspect.iscoroutinefunction(fn):

            async def async_inner(*args: Any, **kwargs: Any) -> Any:
                return await capture._run_async(operation, fn, *args, **kwargs)

            async_inner.__capture_operation__ = operation  # type: ignore[attr-defined]
            return async_inner

        def inner(*args: Any, **kwargs: Any) -> Any:
            return capture._run_sync(operation, fn, *args, **kwargs)

        inner.__capture_operation__ = operation  # type: ignore[attr-defined]
        return inner

    def wrap_admit(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        capture = self

        def inner(lifecycle: Any, action_id: str, kind: str) -> Any:
            try:
                result = fn(lifecycle, action_id, kind)
            except Exception:
                capture.record(
                    "lifecycle",
                    "action_lifecycle",
                    admitted=False,
                    action=str(kind),
                    step_id=str(action_id),
                )
                raise
            capture.record(
                "lifecycle",
                "action_lifecycle",
                admitted=True,
                action=str(kind),
                step_id=str(action_id),
            )
            return result

        inner.__capture_operation__ = "action_lifecycle"  # type: ignore[attr-defined]
        return inner

    def wrap_complete(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        capture = self

        def inner(lifecycle: Any, action_id: str, outcome: Any) -> Any:
            try:
                result = fn(lifecycle, action_id, outcome)
            except Exception:
                capture.record(
                    "lifecycle",
                    "action_lifecycle",
                    completed=False,
                    ok=False,
                    action=str(getattr(outcome, "kind", "")),
                    step_id=str(action_id),
                )
                raise
            extra = extract_search_fields(outcome, lifecycle=lifecycle, action_id=action_id)
            capture.record(
                "lifecycle",
                "action_lifecycle",
                completed=True,
                ok=True,
                action=str(getattr(outcome, "kind", "")),
                step_id=str(action_id),
                **extra,
            )
            return result

        inner.__capture_operation__ = "action_lifecycle"  # type: ignore[attr-defined]
        return inner

    def wrap_stopped_init(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        capture = self

        def inner(instance: Any, *args: Any, **kwargs: Any) -> Any:
            result = fn(instance, *args, **kwargs)
            reason = kwargs.get("reason", args[0] if args else None)
            capture.record("lifecycle", "action_lifecycle", stop_reason=reason)
            return result

        inner.__capture_operation__ = "action_lifecycle"  # type: ignore[attr-defined]
        return inner

    def _run_sync(self, operation: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self.record("begin", operation)
        self.record("attempt", operation)
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record("end", operation, ok=False)
            raise
        extra = extra_from_call(operation, result, args, kwargs)
        self.record("end", operation, ok=True, **extra)
        return result

    async def _run_async(
        self, operation: str, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        self.record("begin", operation)
        self.record("attempt", operation)
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            self.record("end", operation, ok=False)
            raise
        extra = extra_from_call(operation, result, args, kwargs)
        self.record("end", operation, ok=True, **extra)
        return result

    def cancel(self) -> None:
        self.cancelled = True
        self.record("cancelled", "session")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._handle.close()
        except OSError:
            self.failed = True

    def binding_hash(self, sites: dict[str, tuple[tuple[str, str], ...]]) -> str:
        blob = json.dumps(sites, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


from scripts.eval.conversation_capture_status import (  # noqa: E402
    capture_is_complete,
    count_attempts,
    load_events,
)

__all__ = [
    "OPERATIONS",
    "IO_OPERATIONS",
    "ExecutionCapture",
    "capture_is_complete",
    "count_attempts",
    "extract_search_fields",
    "forbidden_payload_keys",
    "load_events",
]
