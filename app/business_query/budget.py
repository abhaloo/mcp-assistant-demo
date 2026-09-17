"""Interactive M2 stage-allocation policy. Core TurnBudget stays policy-neutral."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessQueryBudgetPolicy:
    execution_and_seal_reserve_seconds: float = 3.0
    terminal_reserve_seconds: float = 2.0

    @property
    def planning_reserve_seconds(self) -> float:
        return self.execution_and_seal_reserve_seconds + self.terminal_reserve_seconds


INTERACTIVE_BUDGET_POLICY = BusinessQueryBudgetPolicy()

# Loop caps until the M3-D1 probe pins larger literals. Never read from app/core.
ITERATION_CAP: int = 1
PLANNING_CALLS_PER_TURN: int = 1
