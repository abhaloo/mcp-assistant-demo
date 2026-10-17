"""TaskAdapter and Oracle protocols for Campaign-driven experiment tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.experiments.core.work_matrix import Cell

__all__ = [
    "EVIDENCE_STAGES",
    "Cell",
    "Oracle",
    "OracleValidation",
    "PreflightOracle",
    "SettlingTaskAdapter",
    "TaskAdapter",
]

# When Campaign validates row evidence: probe, batch, or final (pre-judge).
EVIDENCE_STAGES = ("probe", "batch", "final")


@dataclass(frozen=True)
class OracleValidation:
    """Independent checks that must pass before an oracle's scores can drive a verdict.

    Shape-identical to (and the successor of) ``rag_experiment.JudgeValidation``,
    renamed to task-agnostic vocabulary now that SQL's and routing's oracles
    are not LLM judges.
    """

    canaries_passed: bool
    repeatability_passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    call_evidence_passed: bool = True


@runtime_checkable
class Oracle(Protocol):
    """Scores a campaign's rows and says whether they can be trusted.

    The base shape, and what SQL's and routing's free oracles are: they skip
    the canary/repeatability/human-calibration apparatus entirely because there
    is no per-call spend to canary.

    ``is_paid`` is the ONE thing ``Campaign`` needs from an oracle beyond its
    score. It drives ``SpendStaging``'s conservative over-charge rules, which
    exist because a PAID oracle may have burned money it never got to report. A
    free oracle cannot have, so telling the ledger otherwise books the whole
    phase budget for a campaign that spent cents.
    """

    is_paid: bool

    def score(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], OracleValidation]:
        """Score every row. Called exactly once per attempt, staged or not."""
        ...


@runtime_checkable
class PreflightOracle(Oracle, Protocol):
    """Oracle with canary preflight and budget handshake before scoring."""

    def preflight(self, config: dict[str, Any]) -> OracleValidation:
        """Run the canary. ``details`` must carry ``cost_usd`` and
        ``forecast_main_cost_usd``: the first is committed against the canary
        reservation, the second sizes the held main-oracle reservation."""
        ...

    def configure_budget(self, *, available_usd: float) -> None:
        """Tell the oracle what it may spend, once the held reservation is promoted."""
        ...

    def reset_for_attempt(self) -> None:
        """Drop any per-attempt accounting carried over from a previous attempt."""
        ...

    def mark_preflight_cost_accounted(self) -> None:
        """The canary's cost is now on the ledger; do not double-count it."""
        ...


@runtime_checkable
class TaskAdapter(Protocol):
    """One task type's (RAG/SQL/routing) knowledge of what to freeze, run and
    validate. ``Campaign`` owns the mechanical parts (attempt dir, staged
    spend, batching, forecast guards, unwind); everything task-shaped goes
    through this seam.

    Row contract: every row ``execute()`` returns (and every row Campaign
    persists) must carry ``case_id``, ``arm`` and ``repeat`` keys -- Campaign
    uses them (via ``Cell``) for matrix-completeness checking and
    deterministic ordering. ``cost_usd`` must be present and finite.

    Every member here is called unconditionally. The one CONDITIONAL stage,
    deferred evidence, is its own protocol -- see ``SettlingTaskAdapter``.
    """

    def freeze_inputs(self) -> dict[str, Any]:
        """Whatever must be immutable before spend. Must include
        ``freeze_hash``, ``runtime`` (for ``attempt.json``) and
        ``campaign_cap_usd``. ``spend_stages``, when staged spend is in play,
        and ``answer_order_seed``, when deterministic batch ordering matters,
        are read from here too (both optional -- absent means "no staged
        spend" / "no reordering")."""
        ...

    def cells(self) -> list[Cell]:
        """The full matrix of work -- every cell that must complete for a verdict."""
        ...

    def probe_cells(self) -> list[Cell]:
        """Cells that must run and validate first, before the rest of the
        matrix spends (RAG's provenance-pilot pattern). A subset of
        ``cells()``. NOT the D1 route-probe rule (blocked, out of scope for
        this slice) -- this is today's existing pilot-cell behaviour,
        generalized."""
        ...

    def execute(self, cell: Cell) -> dict[str, Any]:
        """Run one cell. Must never raise for an ordinary operational failure
        (a 404, a timeout, an empty answer) -- catch it and return a row that
        ``classify_failure()``/``evidence_failures()`` can reason about,
        charging the cell ceiling on failure rather than zero (fail closed)."""
        ...

    def classify_failure(self, row: dict[str, Any]) -> str | None:
        """None when the row is a clean success; a short reason code otherwise."""
        ...

    def evidence_failures(self, row: dict[str, Any], *, stage: str = "final") -> list[str]:
        """Per-row provenance checks. ``stage`` is probe, batch, or final."""
        ...

    def enforce_operational_budget(self, rows: list[dict[str, Any]]) -> None:
        """Raise once the accumulated operational-failure rate exceeds the
        frozen opportunity budget. A no-op implementation is valid for tasks
        without one."""
        ...

    def verify_frozen_contract(self) -> None:
        """Re-verify the in-memory frozen snapshot against disk and its own hash."""
        ...

    def checkpoint_identity(self) -> dict[str, Any]:
        """Identity fields bound into a sealed judge checkpoint."""
        ...

    def finalize(
        self,
        *,
        scored_rows: list[dict[str, Any]],
        oracle_validation: OracleValidation,
        actual_spend_usd: float,
        store: Any,
    ) -> Any:
        """Task-specific gate evaluation + artifact persistence (scores,
        verdict, ...), run after oracle scoring and before ``Campaign`` hashes
        the attempt directory. Returns a verdict-shaped object exposing a
        ``.status`` attribute (used only for the progress callback)."""
        ...

    def oracle(self) -> Oracle: ...


@runtime_checkable
class CellProvidingTask(TaskAdapter, Protocol):
    """TaskAdapter with provider-aware cell concurrency gating."""

    def cell_provider(self, cell: Cell) -> str | None:
        """Provider name for cell concurrency gating, or None when untracked."""
        ...


@runtime_checkable
class SettlingTaskAdapter(TaskAdapter, Protocol):
    """TaskAdapter with post-matrix evidence settlement (e.g. OpenRouter /generation)."""

    def settle_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch deferred OpenRouter /generation metadata for pending rows."""
        ...
