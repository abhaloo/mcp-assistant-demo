"""Shared probe-cell selection for experiment task adapters."""

from __future__ import annotations

from app.experiments.core.work_matrix import Cell


def repeat_zero_cells_per_arm(cells: list[Cell], arm_names: list[str]) -> list[Cell]:
    """One repeat-0 cell per arm, in arm order."""
    return [
        next(cell for cell in cells if cell.arm_name == arm_name and cell.repeat == 0)
        for arm_name in arm_names
    ]
