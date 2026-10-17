from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.health.checks import build_check_registry
from app.health.ports import QueryRecordSchemaProbe
from app.health.state import health_cache

logger = logging.getLogger(__name__)


async def run_checks_once(
    *, query_record_schema_probe: QueryRecordSchemaProbe | None = None
) -> None:
    # Checks are independent — run them concurrently so a cycle costs the
    # slowest single check, not the sum of all timeouts. return_exceptions is
    # the single failure funnel: a check that raises (transport error, bad
    # config, import failure) becomes a not-ready result logged with its name
    # below, so checks themselves stay try/except-free.
    if query_record_schema_probe is None:
        checks = build_check_registry()
    else:
        checks = build_check_registry(query_record_schema_probe=query_record_schema_probe)
    outcomes = await asyncio.gather(
        *(check.run() for check in checks),
        return_exceptions=True,
    )
    results: dict[str, bool] = {}
    for check, outcome in zip(checks, outcomes):
        if isinstance(outcome, Exception):
            logger.warning("health check %s raised: %s", check.name, outcome)
            results[check.name] = False
        else:
            results[check.name] = outcome
    health_cache.update(results)


async def _background_loop(
    stop_event: asyncio.Event,
    query_record_schema_probe: QueryRecordSchemaProbe | None,
) -> None:
    interval = settings.health.background_interval_seconds
    while not stop_event.is_set():
        # Guard the whole cycle: an error here (e.g. registry build) must not
        # kill the refresher, or readiness freezes on a stale snapshot forever.
        try:
            await run_checks_once(query_record_schema_probe=query_record_schema_probe)
        except Exception:
            logger.exception("health refresh cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


def start_background_checker(
    *, query_record_schema_probe: QueryRecordSchemaProbe | None = None
) -> tuple[asyncio.Task[None], asyncio.Event]:
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _background_loop(stop_event, query_record_schema_probe), name="health-checker"
    )
    return task, stop_event


async def stop_background_checker(task: asyncio.Task[None], stop_event: asyncio.Event) -> None:
    stop_event.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
