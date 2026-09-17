"""Detail facts on the Ask AI v2 wire.

A result envelope carries every authorized detail fact twice (flat, and under
the record that owns it) and the terminal frame carries the envelope twice, so
a turn with a few dozen details breaks the per-frame bound while its rows fit
with room to spare. Details therefore stream ahead of the terminal frame in
bounded batches, and the terminal envelope carries none. Each detail names its
owner, so a consumer rebinds it to the record the envelope lists.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from app.business_query.outcomes import (
    BusinessQueryWireOutcome,
    RecordDetail,
    UnifiedResultEnvelope,
)
from app.core.errors import QueueBufferExceededError
from app.models.ask_v2_events import RecordDetailsEvent
from app.services.ask_v2_progress import TurnSequence
from app.services.byte_bounded_ask_queue import MAX_FRAME_BYTES, ByteBoundedAskQueue

# Half the frame bound. The rest is headroom for the frame's own fields and
# for a detail whose provenance is larger than its siblings'.
DETAIL_BATCH_BYTES: int = MAX_FRAME_BYTES // 2


def detail_batches(details: Sequence[RecordDetail]) -> Iterator[list[RecordDetail]]:
    """Consecutive batches whose wire bytes stay within ``DETAIL_BATCH_BYTES``.

    A detail that cannot fit a frame on its own fails closed, the way an
    oversized table cell does."""
    batch: list[RecordDetail] = []
    batch_bytes = 0
    for detail in details:
        detail_bytes = len(detail.model_dump_json().encode("utf-8"))
        if detail_bytes > DETAIL_BATCH_BYTES:
            raise QueueBufferExceededError(
                f"Record detail byte limit exceeded: {detail_bytes} bytes, "
                f"limit is {DETAIL_BATCH_BYTES}"
            )
        if batch and batch_bytes + detail_bytes > DETAIL_BATCH_BYTES:
            yield batch
            batch, batch_bytes = [], 0
        batch.append(detail)
        batch_bytes += detail_bytes
    if batch:
        yield batch


def stream_record_details(
    queue: ByteBoundedAskQueue, seq: TurnSequence, run_id: str, envelope: object
) -> None:
    """Enqueue one envelope's details in bounded frames. A legacy mapping envelope has none."""
    details = getattr(envelope, "record_details", None)
    if not details:
        return
    answer_query_id = getattr(envelope, "answer_query_id", None)
    for batch in detail_batches(list(details)):
        queue.put_nowait(
            RecordDetailsEvent(
                protocol_version="2",
                run_id=run_id,
                sequence=seq.peek(),
                event_type="record_details",
                answer_query_id=answer_query_id,
                record_details=batch,
            )
        )
        seq.take()


def without_record_details(wire: BusinessQueryWireOutcome) -> BusinessQueryWireOutcome:
    """The terminal form of an outcome whose details were streamed.

    Every envelope keeps its rows, records and refs; none keeps a detail. The
    singular envelope stays equal to the first of the list."""
    if wire.envelope is None:
        return wire
    envelopes = [_without_details(envelope) for envelope in wire.envelopes] or [
        _without_details(wire.envelope)
    ]
    return wire.model_copy(update={"envelope": envelopes[0], "envelopes": envelopes})


def _without_details(envelope: UnifiedResultEnvelope) -> UnifiedResultEnvelope:
    records = tuple(
        record.model_copy(update={"details": ()}) for record in (envelope.records or ())
    )
    return envelope.model_copy(update={"records": records, "record_details": []})
