"""Progress sink that turns Business Query module stages into ask frames."""

from __future__ import annotations

import asyncio
from typing import Any

from app.business_query.wire.module import ProgressStage
from app.services.ask_frames import AskFrame, AskStage, StatusFrame

_MODULE_STAGE: dict[ProgressStage, AskStage] = {
    "understand": "writing_query",
    "planning": "writing_query",
    "authorize": "writing_query",
    "authorized": "writing_query",
    "finding_record": "searching_database",
    "querying": "searching_database",
    "sealing": "searching_database",
    "answering": "writing_answer",
}


class AskProgressSink:
    """Maps Module progress stages and direct AskStage transitions onto ask frames."""

    def __init__(self, queue: asyncio.Queue[AskFrame | None]) -> None:
        self._queue = queue
        self._last: AskStage | None = None

    def stage(self, stage: AskStage) -> None:
        mapped: AskStage = "writing_answer" if stage == "thinking" else stage
        self._last = mapped
        self._queue.put_nowait(StatusFrame(mapped))

    def emit(
        self,
        stage: ProgressStage,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> None:
        _ = (ordinal, of, subject)
        mapped = _MODULE_STAGE[stage]
        if mapped != self._last:
            self.stage(mapped)

    def table(self, section: Any) -> None:
        _ = section
        return

    def emit_thought_delta(self, chunk: str) -> None:
        _ = chunk
        return

    def finish_thought(self) -> None:
        return
