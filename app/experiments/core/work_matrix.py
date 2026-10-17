"""Cell enumeration, ordering, and batched execution for experiment matrices."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from threading import Lock, Semaphore
from typing import Any

from app.experiments.core.money import BUDGET_EPSILON_USD

# Total in-flight cap for answer-matrix runners.
DEFAULT_MAX_WORKERS = 8
# Per-provider caps — conservative split of total headroom for mixed Azure/OpenRouter campaigns.
DEFAULT_AZURE_MAX_WORKERS = 4
DEFAULT_OPENROUTER_MAX_WORKERS = 4
# Frozen cap for concurrent low/high effort probe samples.
EFFORT_PROBE_MAX_WORKERS = 2


@dataclass(frozen=True)
class ProviderConcurrencyLimits:
    """Bounded concurrency: total in-flight plus per-provider isolation."""

    total: int = DEFAULT_MAX_WORKERS
    azure: int = DEFAULT_AZURE_MAX_WORKERS
    openrouter: int = DEFAULT_OPENROUTER_MAX_WORKERS

    def __post_init__(self) -> None:
        if self.total < 1:
            raise ValueError("total concurrency must be positive")
        if self.azure < 1 or self.openrouter < 1:
            raise ValueError("per-provider concurrency must be positive")
        if self.azure > self.total or self.openrouter > self.total:
            raise ValueError("per-provider caps cannot exceed the total cap")


def _default_provider_limits(max_workers: int) -> ProviderConcurrencyLimits:
    return ProviderConcurrencyLimits(
        total=max_workers,
        azure=min(DEFAULT_AZURE_MAX_WORKERS, max_workers),
        openrouter=min(DEFAULT_OPENROUTER_MAX_WORKERS, max_workers),
    )


@dataclass(frozen=True)
class Cell:
    """One unit of matrix work: one case, one arm, one repeat."""

    case_id: str
    arm_name: str
    repeat: int


def enumerate_cells(case_ids: Sequence[str], arm_names: Sequence[str], repeats: int) -> list[Cell]:
    """Cross product of cases x arms x repeats.

    Case is the OUTER loop and arm the middle loop -- pilot-cell selection (which
    stays in rag_experiment.py, RAG-specific) depends on this exact nesting to pick
    the first case for a given (arm, repeat=0) deterministically.
    """
    return [
        Cell(case_id=case_id, arm_name=arm_name, repeat=repeat)
        for case_id in case_ids
        for arm_name in arm_names
        for repeat in range(repeats)
    ]


def deterministic_order(cells: list[Cell], *, seed: int, label: str = "answers") -> list[Cell]:
    """Byte-identical seeded shuffle: sha256(f"{seed}:{label}:{case_id}:{arm_name}:{repeat}").

    This formula must never change -- changing it changes every future run's
    ordering and breaks comparability with existing runs.
    """
    return sorted(
        cells,
        key=lambda cell: hashlib.sha256(
            f"{seed}:{label}:{cell.case_id}:{cell.arm_name}:{cell.repeat}".encode()
        ).hexdigest(),
    )


def forecast_fits(
    spent_usd: float,
    remaining_cells: Sequence[Cell],
    forecasts: Mapping[str, float],
    reserve_usd: float,
) -> bool:
    """True when spent plus forecast-remaining fits the reservation.

    Uses ``not (spent + remaining <= reserve)`` so NaN fails closed.
    """
    remaining = sum(float(forecasts[cell.arm_name]) for cell in remaining_cells)
    return spent_usd + remaining <= reserve_usd + BUDGET_EPSILON_USD


def run_sliding_window(
    cells: Sequence[Cell],
    *,
    execute_fn: Callable[[Cell], dict[str, Any]],
    max_workers: int,
    cell_provider_fn: Callable[[Cell], str | None] | None = None,
    provider_limits: ProviderConcurrencyLimits | None = None,
    on_row_complete: Callable[[dict[str, Any]], None] | None = None,
    on_checkpoint: Callable[[dict[str, Any], Sequence[Cell]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run cells with a completion-driven sliding window up to max in-flight.

    When ``provider_limits`` and ``cell_provider_fn`` are supplied, each provider
    gets its own cap in addition to the total cap. ``on_checkpoint`` fires after
    every completed row with that row and the cells still pending (preserving
    submission order) so spend-forecast guards can fail closed between completions.
    """
    if not cells:
        return []

    limits = provider_limits or _default_provider_limits(max_workers)
    total_sem = Semaphore(limits.total)
    provider_sems = {
        "azure": Semaphore(limits.azure),
        "openrouter": Semaphore(limits.openrouter),
    }
    pending: list[Cell] = list(cells)
    pending_lock = Lock()
    rows: list[dict[str, Any]] = []
    stop = {"raised": False}

    def acquire_slots(cell: Cell) -> None:
        total_sem.acquire()
        provider = cell_provider_fn(cell) if cell_provider_fn is not None else None
        if provider in provider_sems:
            provider_sems[provider].acquire()

    def release_slots(cell: Cell) -> None:
        provider = cell_provider_fn(cell) if cell_provider_fn is not None else None
        if provider in provider_sems:
            provider_sems[provider].release()
        total_sem.release()

    def wrapped_execute(cell: Cell) -> dict[str, Any]:
        acquire_slots(cell)
        try:
            return execute_fn(cell)
        finally:
            release_slots(cell)

    def next_cell() -> Cell | None:
        with pending_lock:
            if stop["raised"] or not pending:
                return None
            return pending.pop(0)

    with ThreadPoolExecutor(max_workers=limits.total) as pool:
        in_flight: dict[Any, Cell] = {}

        def submit_one() -> None:
            cell = next_cell()
            if cell is None:
                return
            in_flight[pool.submit(wrapped_execute, cell)] = cell

        for _ in range(min(limits.total, len(cells))):
            submit_one()

        while in_flight:
            done, _pending = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                in_flight.pop(future)
                row = future.result()
                rows.append(row)
                if on_row_complete is not None:
                    on_row_complete(row)
                if on_checkpoint is not None:
                    remaining = list(pending)
                    try:
                        on_checkpoint(row, remaining)
                    except Exception:
                        stop["raised"] = True
                        drain_futures = list(in_flight.keys())
                        for drain_future in as_completed(drain_futures):
                            if drain_future not in in_flight:
                                continue
                            in_flight.pop(drain_future)
                            drained_row = drain_future.result()
                            rows.append(drained_row)
                            if on_row_complete is not None:
                                on_row_complete(drained_row)
                        in_flight.clear()
                        raise
                if not stop["raised"]:
                    submit_one()
    return rows
