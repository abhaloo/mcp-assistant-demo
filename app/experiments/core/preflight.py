"""Campaign preflight: external run roots and no-matrix-spend checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.core.errors import RagExperimentError

__all__ = [
    "PreflightConfig",
    "PreflightError",
    "PreflightReceipt",
    "run_campaign_preflight",
    "verify_external_run_root",
]


class PreflightError(RagExperimentError):
    """Preflight refused before matrix reservation."""


@dataclass(frozen=True)
class PreflightConfig:
    repo_root: Path
    run_group: Path
    require_openrouter: bool = True
    openrouter_key_present: bool = True
    candidate_guard: Callable[[], None] | None = None


@dataclass(frozen=True)
class PreflightReceipt:
    ok: bool
    checks: dict[str, Any]


def verify_external_run_root(repo_root: Path, run_root: Path) -> None:
    """Refuse run roots nested under the source worktree."""
    repo = repo_root.resolve()
    candidate = run_root.resolve()
    if not candidate.is_absolute():
        raise PreflightError("run root must be absolute and outside the worktree")
    try:
        candidate.relative_to(repo)
        raise PreflightError("run root must be outside the worktree")
    except ValueError:
        return


def run_campaign_preflight(config: PreflightConfig) -> PreflightReceipt:
    verify_external_run_root(config.repo_root, config.run_group)
    if config.require_openrouter and not config.openrouter_key_present:
        raise PreflightError("OPENROUTER_API_KEY missing for preflight")
    if config.candidate_guard is not None:
        config.candidate_guard()
    return PreflightReceipt(ok=True, checks={"external_run_root": str(config.run_group)})
