"""Table streaming and framing for Ask AI responses."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.errors import QueueBufferExceededError
from app.models.ask_v2_events import (
    TableColumn,
    TableEndEvent,
    TableRowsEvent,
    TableStartEvent,
)
from app.models.result_presentation import ResultPresentation
from app.services.byte_bounded_ask_queue import (
    MAX_CELL_BYTES,
    MAX_COLUMNS,
    MAX_ROWS_PER_PAGE,
)


class _EventQueue(Protocol):
    def put_nowait(self, event: Any) -> None: ...


class _SequenceCounter(Protocol):
    def peek(self) -> int: ...
    def take(self) -> int: ...


def cell_wire_bytes(val: Any) -> int:
    """UTF-8 byte length for a single cell value."""
    if val is None:
        return 0
    if isinstance(val, (int, float, bool)):
        return len(str(val).encode("utf-8"))
    if isinstance(val, str):
        return len(val.encode("utf-8"))
    return len(json.dumps(val, default=str).encode("utf-8"))


def _validate_table_columns(columns: list[TableColumn]) -> None:
    """Fail closed if a table declares more than the allowed column count."""
    if len(columns) > MAX_COLUMNS:
        raise QueueBufferExceededError(
            f"Table column limit exceeded: {len(columns)} columns, limit is {MAX_COLUMNS}"
        )


def _validate_row_batch(batch: list[dict[str, Any]]) -> None:
    """Fail closed if a row batch or any cell in it exceeds its bound."""
    if len(batch) > MAX_ROWS_PER_PAGE:
        raise QueueBufferExceededError(
            f"Table row batch limit exceeded: {len(batch)} rows, limit is {MAX_ROWS_PER_PAGE}"
        )
    for row in batch:
        for value in row.values():
            cb = cell_wire_bytes(value)
            if cb > MAX_CELL_BYTES:
                raise QueueBufferExceededError(
                    f"Table cell byte limit exceeded: {cb} bytes, limit is {MAX_CELL_BYTES}"
                )


def _stream_row_batches(
    queue: _EventQueue,
    seq: _SequenceCounter,
    *,
    table_id: str,
    run_id: str,
    rows: list[dict[str, Any]],
) -> None:
    """Stream sliced row batches onto the queue with wire validation."""
    for i in range(0, len(rows), MAX_ROWS_PER_PAGE):
        batch = rows[i : i + MAX_ROWS_PER_PAGE]
        _validate_row_batch(batch)
        tbl_rows = TableRowsEvent(
            protocol_version="2",
            run_id=run_id,
            sequence=seq.peek(),
            event_type="table_rows",
            table_id=table_id,
            rows=batch,
        )
        queue.put_nowait(tbl_rows)
        seq.take()


def paint_committed_table(
    queue: _EventQueue,
    seq: _SequenceCounter,
    run_id: str,
    ordinal: int,
    rows: Sequence[dict[str, Any]],
    columns: list[TableColumn],
    presentation: ResultPresentation | None,
    total_row_count: int | None,
) -> str:
    """Paint one committed table onto the wire sequence.

    Validates column and row limits, generates the canonical table-{run_id}-{ordinal}
    id, streams rows in batches of MAX_ROWS_PER_PAGE, calculates the deterministic
    SHA-256 digest of rows, and emits TableStartEvent -> TableRowsEvent* -> TableEndEvent.
    Returns the table_digest string.
    """
    _validate_table_columns(columns)
    table_id = f"table-{run_id}-{ordinal}"
    tbl_start = TableStartEvent(
        protocol_version="2",
        run_id=run_id,
        sequence=seq.peek(),
        event_type="table_start",
        table_id=table_id,
        columns=columns,
        presentation=presentation,
    )
    queue.put_nowait(tbl_start)
    seq.take()

    row_list = list(rows)
    _stream_row_batches(queue, seq, table_id=table_id, run_id=run_id, rows=row_list)

    tbl_digest = hashlib.sha256(
        json.dumps(row_list, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    tbl_end = TableEndEvent(
        protocol_version="2",
        run_id=run_id,
        sequence=seq.peek(),
        event_type="table_end",
        table_id=table_id,
        row_count=len(row_list),
        table_digest=tbl_digest,
        total_row_count=total_row_count,
    )
    queue.put_nowait(tbl_end)
    seq.take()

    return tbl_digest
