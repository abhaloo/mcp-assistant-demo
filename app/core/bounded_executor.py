"""Bounded thread pool executor enforcing admission limits on concurrency and queue depth."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore, Lock
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


class CapacityExhaustedError(Exception):
    """Raised when worker and queue capacity are fully occupied."""


class BoundedExecutor(Executor):
    """ThreadPoolExecutor wrapper that rejects excess work before queueing.

    Admission uses a BoundedSemaphore sized to workers plus queue capacity.
    Permits remain held for the full duration of worker execution, releasing
    only through the future done callback or after submission failure.
    """

    def __init__(
        self,
        max_workers: int,
        max_queue: int,
        *,
        thread_name_prefix: str = "document",
    ) -> None:
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError(f"max_workers must be an integer >= 1, got {max_workers!r}")
        if type(max_queue) is not int or max_queue < 0:
            raise ValueError(f"max_queue must be an integer >= 0, got {max_queue!r}")

        self._max_workers = max_workers
        self._max_queue = max_queue
        self._permits = BoundedSemaphore(max_workers + max_queue)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._shutdown = False
        self._shutdown_lock = Lock()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def max_queue(self) -> int:
        return self._max_queue

    @property
    def capacity(self) -> int:
        return self._max_workers + self._max_queue

    def submit(
        self,
        fn: Callable[P, T],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Future[T]:
        """Submit callable if capacity permits; reject immediately when exhausted."""
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            if not self._permits.acquire(blocking=False):
                raise CapacityExhaustedError("document capacity exhausted")

        try:
            context = copy_context()
            future = self._pool.submit(context.run, fn, *args, **kwargs)
        except BaseException:
            self._permits.release()
            raise

        future.add_done_callback(lambda _future: self._permits.release())
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Signal shutdown and forward options to the underlying pool."""
        with self._shutdown_lock:
            self._shutdown = True
        self._pool.shutdown(wait=wait, cancel_futures=cancel_futures)
