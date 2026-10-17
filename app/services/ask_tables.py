"""Table streaming and framing for Ask AI responses."""

from __future__ import annotations

import hashlib
import json
import re
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
    MAX_FRAME_BYTES,
    MAX_ROWS_PER_PAGE,
    frame_wire_bytes,
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


# The title the panel shows for a table with no presentation of its own.
FALLBACK_TABLE_TITLE = "Results"
# ResultPresentation.summary max_length (app/models/result_presentation.py).
_SUMMARY_MAX_CHARS = 240
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _bare_id(column: TableColumn) -> bool:
    """An id column with no look to show: the person would read a raw number."""
    return column.value_type == "id" and column.display_key is None


def _route(column: TableColumn) -> str | None:
    """The record route a link column opens, with the placeholder's resource prefix removed."""
    if column.href_template is None:
        return None
    return _PLACEHOLDER.sub(
        lambda m: "{" + m.group(1).rpartition(".")[2] + "}", column.href_template
    )


def _repeats_earlier(column: TableColumn, kept: list[TableColumn]) -> bool:
    """A column that shows a record an earlier column already shows: the same
    look on a second id column, or a link to the same record route."""
    route = _route(column)
    return any(
        (column.display_key is not None and column.display_key == earlier.display_key)
        or (route is not None and route == _route(earlier))
        for earlier in kept
    )


def _row_keys_read(columns: list[TableColumn]) -> set[str]:
    """Every row key a painted column reads: its own, its look, its currency, its link ids."""
    keys: set[str] = set()
    for column in columns:
        keys.update(k for k in (column.key, column.display_key, column.currency_key) if k)
        if column.href_template is not None:
            keys.update(_PLACEHOLDER.findall(column.href_template))
    return keys


def trim_columns(
    columns: list[TableColumn],
    rows: Sequence[dict[str, Any]],
    presentation: ResultPresentation | None,
) -> tuple[list[TableColumn], list[dict[str, Any]], ResultPresentation | None]:
    """Keep at most MAX_COLUMNS columns and say under the title how many are not shown.

    Bare id columns go first, then columns that show a record an earlier column
    already shows, then the last columns by position. A look column (role
    display) stays with the id column it belongs to, once, and counts toward
    the limit. Rows keep every key a painted column reads.
    """
    row_list = list(rows)
    if len(columns) <= MAX_COLUMNS:
        return columns, row_list, presentation
    looks = {c.key: c for c in columns if c.role == "display"}
    chosen: list[TableColumn] = []
    taken: set[str] = set()
    for column in columns:
        if column.role == "display" or _bare_id(column) or _repeats_earlier(column, chosen):
            continue
        look = looks.get(column.display_key or "")
        if look is not None and look.key in taken:
            look = None
        if len(chosen) + (1 if look is None else 2) > MAX_COLUMNS:
            break
        chosen.append(column)
        taken.add(column.key)
        if look is not None:
            chosen.append(look)
            taken.add(look.key)
    order = {c.key: i for i, c in enumerate(columns)}
    chosen.sort(key=lambda c: order[c.key])
    read = _row_keys_read(chosen)
    notice = f"{len(columns) - len(chosen)} of {len(columns)} columns not shown."
    base = presentation or ResultPresentation(title=FALLBACK_TABLE_TITLE)
    summary = f"{base.summary} · {notice}" if base.summary else notice
    trimmed = base.model_copy(update={"summary": summary[:_SUMMARY_MAX_CHARS]})
    return chosen, [{k: v for k, v in row.items() if k in read} for row in row_list], trimmed


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


def _row_event_bytes(batch: list[dict[str, Any]], *, table_id: str, run_id: str) -> int:
    """Wire size of the ``TableRowsEvent`` these batch rows would ride."""
    return frame_wire_bytes(
        TableRowsEvent(
            protocol_version="2",
            run_id=run_id,
            sequence=0,
            event_type="table_rows",
            table_id=table_id,
            rows=batch,
        )
    )


def _plan_row_batches(
    rows: list[dict[str, Any]], *, table_id: str, run_id: str
) -> list[list[dict[str, Any]]]:
    """Cut row batches so every candidate ``TableRowsEvent`` fits one frame.

    A row that alone exceeds the frame budget fails closed with a message
    naming the row and the budget. Measured, so cell and row-count bounds
    stay subordinate to the per-frame bound."""
    empty_bytes = _row_event_bytes([], table_id=table_id, run_id=run_id)
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    batch_bytes = empty_bytes
    for index, row in enumerate(rows):
        row_bytes = _row_event_bytes([row], table_id=table_id, run_id=run_id)
        if row_bytes > MAX_FRAME_BYTES:
            raise QueueBufferExceededError(
                f"Table row byte limit exceeded: row {index} is {row_bytes} bytes, "
                f"limit is {MAX_FRAME_BYTES}"
            )
        if batch and (len(batch) >= MAX_ROWS_PER_PAGE or batch_bytes + row_bytes > MAX_FRAME_BYTES):
            batches.append(batch)
            batch, batch_bytes = [], empty_bytes
        if batch:
            batch_bytes += 1  # the comma between the previous row and this one
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        batches.append(batch)
    return batches


def _stream_row_batches(
    queue: _EventQueue,
    seq: _SequenceCounter,
    *,
    table_id: str,
    run_id: str,
    rows: list[dict[str, Any]],
) -> None:
    """Stream sliced row batches onto the queue with wire validation."""
    for batch in _plan_row_batches(rows, table_id=table_id, run_id=run_id):
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


def _stream_planned_batches(
    queue: _EventQueue,
    seq: _SequenceCounter,
    *,
    table_id: str,
    run_id: str,
    batches: list[list[dict[str, Any]]],
) -> None:
    """Stream batches already cut by ``_plan_row_batches`` with wire validation."""
    for batch in batches:
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

    Trims the columns by rule, validates row limits, generates the canonical
    table-{run_id}-{ordinal} id, cuts row batches by the frame bound BEFORE the
    start frame so a failing table emits nothing, calculates the deterministic
    SHA-256 digest of rows, and emits TableStartEvent -> TableRowsEvent* ->
    TableEndEvent.
    Returns the table_digest string.
    """
    columns, row_list, presentation = trim_columns(columns, rows, presentation)
    table_id = f"table-{run_id}-{ordinal}"
    planned_batches = _plan_row_batches(row_list, table_id=table_id, run_id=run_id)
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

    _stream_planned_batches(queue, seq, table_id=table_id, run_id=run_id, batches=planned_batches)

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
