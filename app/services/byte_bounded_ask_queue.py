"""Byte-bounded async queue for Ask AI v2 streaming.

Bounds the per-frame wire size and the total buffered wire size so a slow
consumer or a runaway producer cannot grow server memory without limit. The
row/column/cell bounds for table frames are separate named constants here
(single source of truth) but are enforced where table rows are built, in
``app.services.ask_v2_stream``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import QueueBufferExceededError
from app.models.ask_v2_events import AskV2EventBase, StreamErrorEvent

MAX_ROWS_PER_PAGE: int = 50
MAX_COLUMNS: int = 20
MAX_CELL_BYTES: int = 4 * 1024  # 4 KiB
MAX_FRAME_BYTES: int = 32 * 1024  # 32 KiB
MAX_TABLE_BUFFER_BYTES: int = 256 * 1024  # 256 KiB

_CLOSED_SENTINEL = object()


def _compute_frame_bytes(frame: Any) -> int:
    """Compute the wire byte size of an event or string frame."""
    if frame is None:
        return 0
    if isinstance(frame, str):
        return len(frame.encode("utf-8"))
    if isinstance(frame, bytes):
        return len(frame)
    if isinstance(frame, AskV2EventBase) or hasattr(frame, "model_dump_json"):
        return len(frame.model_dump_json().encode("utf-8"))
    return len(str(frame).encode("utf-8"))


class ByteBoundedAskQueue:
    """Async queue bounding per-frame bytes and total buffered bytes.

    A terminal ``StreamErrorEvent`` is always deliverable: it bypasses both
    limits below and is never added to the running totals, so a full buffer
    can still report its own failure to the client.
    """

    def __init__(
        self,
        max_bytes: int = MAX_TABLE_BUFFER_BYTES,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_frame_bytes = max_frame_bytes
        self._current_bytes = 0
        self._current_items = 0
        self._closed = False
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def max_frame_bytes(self) -> int:
        return self._max_frame_bytes

    @property
    def current_bytes(self) -> int:
        return self._current_bytes

    @property
    def current_items(self) -> int:
        return self._current_items

    def is_closed(self) -> bool:
        return self._closed

    def put_nowait(self, frame: Any) -> None:
        """Enqueue a frame, failing closed on the per-frame or buffer byte limit."""
        if self._closed:
            raise RuntimeError("Cannot put into closed queue")

        frame_bytes = _compute_frame_bytes(frame)
        is_terminal_error = isinstance(frame, StreamErrorEvent)

        if not is_terminal_error:
            if frame_bytes > self._max_frame_bytes:
                raise QueueBufferExceededError(
                    f"Frame byte limit exceeded: frame is {frame_bytes} bytes, "
                    f"limit is {self._max_frame_bytes}"
                )
            if (self._current_bytes + frame_bytes) > self._max_bytes:
                raise QueueBufferExceededError(
                    f"Queue byte buffer exceeded: attempted "
                    f"{self._current_bytes + frame_bytes} bytes, limit is {self._max_bytes}"
                )
            self._current_bytes += frame_bytes
            self._current_items += 1

        accounted_bytes = 0 if is_terminal_error else frame_bytes
        self._queue.put_nowait((frame, accounted_bytes))

    async def get(self) -> Any:
        """Retrieve the next frame. Returns None once the queue is closed and drained.

        ``asyncio.Queue.get`` only removes an item from its internal buffer on
        a clean, non-cancelled resume: if the caller is cancelled while this
        call is pending, nothing has been popped, so the frame stays queued
        for the next call instead of being silently dropped.
        """
        item = await self._queue.get()
        if item is _CLOSED_SENTINEL:
            # Re-enqueue sentinel so concurrent/subsequent gets also observe closed state
            self._queue.put_nowait(_CLOSED_SENTINEL)
            return None

        frame, frame_bytes = item
        self._current_bytes = max(0, self._current_bytes - frame_bytes)
        self._current_items = max(0, self._current_items - 1)
        return frame

    def close(self) -> None:
        """Mark queue as closed and enqueue the closed sentinel."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(_CLOSED_SENTINEL)
