"""Global in-flight LLM-call limiter.

asyncio.Semaphore is loop-bound and NOT thread-safe — acquire it only from the
event loop (the async orchestration layer), never inside a run_in_thread worker.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from app.config import settings

_semaphore = asyncio.Semaphore(settings.llm_max_concurrency)


@asynccontextmanager
async def llm_slot():
    """Hold one LLM concurrency slot for the duration of the block."""
    async with _semaphore:
        yield
