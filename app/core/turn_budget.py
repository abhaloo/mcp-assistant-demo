"""Policy-neutral turn budget: remaining time, expiry, and bounded waits."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from typing import Protocol, TypeVar, runtime_checkable

from app.core.errors import DeadlineExpiredError

T = TypeVar("T")


@runtime_checkable
class TurnBudget(Protocol):
    @property
    def remaining_seconds(self) -> float: ...

    def check_not_expired(self) -> None: ...


class _UnboundedBudget:
    @property
    def remaining_seconds(self) -> float:
        return math.inf

    def check_not_expired(self) -> None:
        return None


UNBOUNDED_BUDGET: TurnBudget = _UnboundedBudget()


def allowance_seconds(
    budget: TurnBudget,
    *,
    ceiling_seconds: float | None = None,
    reserve_seconds: float = 0.0,
) -> float:
    remaining = budget.remaining_seconds - reserve_seconds
    if remaining <= 0:
        return 0.0
    if ceiling_seconds is None:
        return remaining
    return min(ceiling_seconds, remaining)


def _ceiling_selected(
    budget: TurnBudget,
    *,
    ceiling_seconds: float | None,
    reserve_seconds: float,
) -> bool:
    remaining = budget.remaining_seconds - reserve_seconds
    return ceiling_seconds is not None and ceiling_seconds < remaining


async def _join_task(task: asyncio.Task[object]) -> None:
    """Wait until ``task`` finishes without converting its cancel into ours."""
    if not task.done():
        await asyncio.wait({task})


async def await_with_budget(  # noqa: PLR0913
    operation: Callable[[], Awaitable[T]],
    budget: TurnBudget,
    *,
    ceiling_seconds: float | None = None,
    reserve_seconds: float = 0.0,
    on_budget_expired: Callable[[], None] | None = None,
    join_cancelled_child: bool = True,
) -> T:
    allowance = allowance_seconds(
        budget, ceiling_seconds=ceiling_seconds, reserve_seconds=reserve_seconds
    )
    selected_by_ceiling = _ceiling_selected(
        budget, ceiling_seconds=ceiling_seconds, reserve_seconds=reserve_seconds
    )
    if allowance <= 0:
        budget.check_not_expired()
        raise DeadlineExpiredError("Turn budget has no remaining allowance")
    if allowance == math.inf:
        return await operation()

    child: asyncio.Task[T] | None = None
    timer: asyncio.Task[None] | None = None

    async def _run() -> T:
        return await operation()

    try:
        child = asyncio.create_task(_run())
        timer = asyncio.create_task(asyncio.sleep(allowance))
        done, _pending = await asyncio.wait({child, timer}, return_when=asyncio.FIRST_COMPLETED)
        if child in done:
            timer.cancel()
            await _join_task(timer)
            return child.result()
        if not selected_by_ceiling and on_budget_expired is not None:
            on_budget_expired()
        child.cancel()
        if join_cancelled_child:
            await _join_task(child)
        if selected_by_ceiling:
            raise TimeoutError("stage ceiling exceeded")
        raise DeadlineExpiredError("Turn budget expired")
    except asyncio.CancelledError:
        if child is not None and not child.done():
            child.cancel()
            if join_cancelled_child:
                await _join_task(child)
        if timer is not None and not timer.done():
            timer.cancel()
            await _join_task(timer)
        raise


async def run_blocking_with_budget(  # noqa: PLR0913
    operation: Callable[[], T],
    budget: TurnBudget,
    *,
    executor: Executor | None = None,
    ceiling_seconds: float | None = None,
    reserve_seconds: float = 0.0,
    on_budget_expired: Callable[[], None] | None = None,
    join_cancelled_child: bool = True,
) -> T:
    def factory() -> Awaitable[T]:
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(executor, operation)

    return await await_with_budget(
        factory,
        budget,
        ceiling_seconds=ceiling_seconds,
        reserve_seconds=reserve_seconds,
        on_budget_expired=on_budget_expired,
        join_cancelled_child=join_cancelled_child,
    )
