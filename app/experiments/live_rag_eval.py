"""Live shell for the immutable two-arm DeepSeek RAG repair campaign.

Ownership: orchestration + Campaign wiring only. Implementations live in:
azure_attestation, rag_answer_path, rag_freeze, rag_gold_answerability, core/.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from app.config import settings
from app.experiments.arming import require_armed
from app.experiments.console import CampaignConsole
from app.experiments.core.campaign import Campaign, CampaignAttemptResult
from app.experiments.core.checkpoint import maybe_raise_recoverable_checkpoint
from app.experiments.core.progress_heartbeat import (
    CoalescedHeartbeatWriter,
    write_progress_heartbeat,
)
from app.experiments.live_errors import LiveRagEvalError
from app.experiments.live_rag_judge import LunaRagasJudge
from app.experiments.manifest import EXPERIMENT_DIR
from app.experiments.rag_answer_path import (
    LIVE_ANSWER_MAX_WORKERS,
    answer_frozen_case,
    enrich_openrouter_answer_rows,
)
from app.experiments.rag_experiment import AnswerArm, FrozenRagExperiment
from app.experiments.rag_freeze import (
    JUDGE_DEPLOYMENT,
    controls_for_frozen_arm,
    verify_live_runtime_invariants,
)
from app.experiments.review.writer import write_attempt_review
from app.experiments.spend_coordinator import acquire, commit, release
from app.experiments.tasks.rag_task import RagOracle, RagTask

B1_LIVE_ENV = "DEEPSEEK_B1_LIVE"
PHASE = "rag_repair"


def require_b1_armed() -> None:
    require_armed(B1_LIVE_ENV, purpose="live RAG campaign")


def run_live_rag_eval(
    *,
    frozen: FrozenRagExperiment,
    attempt_id: str,
    ledger_path: Path | None = None,
    max_workers: int = LIVE_ANSWER_MAX_WORKERS,
    console: CampaignConsole | None = None,
) -> CampaignAttemptResult:
    """Reserve spend, run answers through Campaign + RagTask, judge, and gate."""
    require_b1_armed()
    uses_openrouter = any(
        arm.get("requested_provider") == "openrouter" for arm in frozen.document["arms"]
    )
    if uses_openrouter and not settings.openrouter_api_key:
        raise LiveRagEvalError("OPENROUTER_API_KEY is unset")
    heartbeat_path = frozen.output_path.parent / "heartbeat.json"
    heartbeat_lock = Lock()
    heartbeat_state_lock = Lock()
    heartbeat_state: dict[str, Any] = {"stage": "starting", "completed": 0}
    heartbeat_stop = Event()
    coalesced_heartbeat = CoalescedHeartbeatWriter(
        heartbeat_path,
        write_fn=write_progress_heartbeat,
    )

    def write_heartbeat(progress: dict[str, Any]) -> None:
        with heartbeat_state_lock:
            heartbeat_state.update(progress)
            state = dict(heartbeat_state)
        state["attempt_id"] = attempt_id
        with heartbeat_lock:
            coalesced_heartbeat.update(state)

    view = console if console is not None else CampaignConsole.create(mode="off")
    report_progress = view.tee(write_heartbeat)

    def heartbeat_ticker() -> None:
        while not heartbeat_stop.wait(20.0):
            report_progress({"liveness_tick": True})

    report_progress({"stage": "starting", "completed": 0})
    policy = frozen.document["policy"]
    spend_stages = frozen.document.get("spend_stages") or {}
    harness_caps = dict(policy["harness_output_cap_tokens_by_arm"])
    est_answer_usd = dict(spend_stages.get("answer_cell_forecast_usd") or {})

    def answer_with_frozen_route_controls(
        frozen_case: dict[str, Any],
        arm: AnswerArm,
        repeat: int = 0,
    ) -> dict[str, Any]:
        del repeat
        arm_controls = controls_for_frozen_arm(arm_name=arm.name, document=frozen.document)
        return answer_frozen_case(
            frozen_case,
            arm,
            harness_output_cap_tokens_by_arm=harness_caps,
            est_answer_usd_by_arm=est_answer_usd,
            openrouter_controls=(arm_controls if arm.requested_provider == "openrouter" else None),
        )

    def settle_route_metadata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch OpenRouter /generation metadata for pending rows."""
        report_progress(
            {
                "stage": "route_metadata",
                "completed": 0,
                "total": sum(1 for row in rows if row.get("route_metadata_status") == "pending"),
            }
        )
        return enrich_openrouter_answer_rows(
            rows,
            max_workers=max_workers,
            progress_fn=report_progress,
        )

    campaign = Campaign(
        task=RagTask(
            frozen=frozen,
            answer_fn=answer_with_frozen_route_controls,
            oracle=RagOracle(
                LunaRagasJudge(
                    progress_fn=report_progress,
                    arms=tuple(frozen.document["policy"]["answer_arms"]),
                    repeats=int(frozen.document["policy"]["repeats"]),
                ),
                frozen.document["judge"],
            ),
            settle_fn=settle_route_metadata,
        ),
        reserve_fn=lambda amount: acquire(
            PHASE,
            JUDGE_DEPLOYMENT,
            amount,
            ledger_path=ledger_path,
        ),
        commit_fn=lambda reservation, actual: commit(
            reservation,
            actual,
            ledger_path=ledger_path,
        ),
        release_fn=lambda reservation: release(reservation, ledger_path=ledger_path),
        attempt_root=frozen.output_path.parent / "attempts",
        max_workers=max_workers,
        progress_fn=report_progress,
        runtime_invariant_fn=lambda stage: verify_live_runtime_invariants(
            frozen,
            stage=stage,
        ),
    )
    ticker = Thread(target=heartbeat_ticker, name="rag-eval-heartbeat", daemon=True)
    ticker.start()
    try:
        result = campaign.run_attempt(attempt_id=attempt_id)
        write_attempt_review(result.attempt_dir)
        return result
    except Exception:
        attempt_dir = frozen.output_path.parent / "attempts" / attempt_id
        maybe_raise_recoverable_checkpoint(attempt_dir)
        raise
    finally:
        heartbeat_stop.set()
        ticker.join(timeout=1.0)
        with heartbeat_lock:
            coalesced_heartbeat.flush_best_effort()
        view.close()


def new_run_group_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return EXPERIMENT_DIR / "runs" / f"repair-v2-{stamp}"
