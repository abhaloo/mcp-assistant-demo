"""Cross-lane dry-run assembly and summary for multi-provider fair compare."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.experiments.arm_profiles import build_pairwise_campaign, build_sql_pairwise_campaign
from app.experiments.live_sql_eval import SQL_REPEATS, project_sql_budget_usd
from app.experiments.manifest import EXPERIMENT_ID, load_manifest, verify_manifest
from app.experiments.rag_freeze import project_budget_usd

LaneName = Literal["rag", "sql", "routing"]

COMPOSITE_JOIN_FIELDS = ("lane", "arm_name", "deployment", "reasoning_effort")


@dataclass(frozen=True)
class LanePlan:
    """Declared configuration and budget projection for one experiment lane."""

    lane: str
    baseline_profile_id: str | None = None
    candidate_profile_id: str | None = None
    baseline_arm: str | None = None
    candidate_arm: str | None = None
    uses_openrouter: bool = False
    campaign_cap_usd: float | None = None
    projection_usd: float | None = None
    cells: int = 0
    join_keys: dict[str, str] = field(default_factory=dict)
    cost_ratio_note: str | None = None
    live_gate_note: str | None = None
    router_policy: dict[str, Any] | None = None
    mode: str | None = None
    spend_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class LaneAdapter:
    """Protocol for building per-lane execution plans."""

    def plan(
        self,
        *,
        baseline_profile: str,
        candidate_profile: str,
        n_cases: int,
    ) -> LanePlan: ...


class RagLaneAdapter:
    def plan(
        self,
        *,
        baseline_profile: str,
        candidate_profile: str,
        n_cases: int,
    ) -> LanePlan:
        config = build_pairwise_campaign(baseline_profile, candidate_profile)
        projection = project_budget_usd(campaign=config, n_cases=n_cases)
        return LanePlan(
            lane="rag",
            baseline_profile_id=baseline_profile,
            candidate_profile_id=candidate_profile,
            baseline_arm=config.baseline.arm_name,
            candidate_arm=config.candidate.arm_name,
            uses_openrouter=config.baseline.provider == "openrouter"
            or config.candidate.provider == "openrouter",
            campaign_cap_usd=config.campaign_cap_usd,
            projection_usd=projection["total_usd"],
            cells=n_cases * 2 * 3,
            join_keys={
                "baseline": profile_join_key(lane="rag", profile_id=baseline_profile),
                "candidate": profile_join_key(lane="rag", profile_id=candidate_profile),
            },
        )


class SqlLaneAdapter:
    def plan(
        self,
        *,
        baseline_profile: str,
        candidate_profile: str,
        n_cases: int,
    ) -> LanePlan:
        config = build_sql_pairwise_campaign(baseline_profile, candidate_profile)
        projection = project_sql_budget_usd(
            n_cases=n_cases,
            arms=config.arms,
            repeats=SQL_REPEATS,
            campaign_cap_usd=config.campaign_cap_usd,
        )
        baseline, candidate = config.arms
        return LanePlan(
            lane="sql",
            baseline_profile_id=baseline_profile,
            candidate_profile_id=candidate_profile,
            baseline_arm=baseline.name,
            candidate_arm=candidate.name,
            router_policy=config.router_policy,
            campaign_cap_usd=projection["campaign_cap_usd"],
            projection_usd=projection["answer_reserve_usd"],
            cells=projection["n_cells"],
            join_keys={
                "baseline": profile_join_key(lane="sql", profile_id=baseline_profile),
                "candidate": profile_join_key(lane="sql", profile_id=candidate_profile),
            },
            cost_ratio_note=(
                "Cost ratio gate calibrated for prod-luna baseline only; "
                "other baselines fail closed (HOLD)."
                if baseline_profile != "prod-luna"
                else "Cost ratio gate uses Luna baseline calibration."
            ),
            live_gate_note=(
                "Before full SQL dev matrix: run --run-group live smoke and recalibrate "
                "EST_TOKENS_PER_RUN (mini-era forecasts unsafe for Luna/DeepSeek)."
            ),
        )


class RoutingLaneAdapter:
    def plan(
        self,
        *,
        baseline_profile: str,
        candidate_profile: str,
        n_cases: int,
    ) -> LanePlan:
        del baseline_profile, candidate_profile, n_cases
        return LanePlan(
            lane="routing",
            mode="pre-flight wire conformance only",
            spend_note="route_conformance phase budget; not an answer-arm contest",
        )


LANE_ADAPTERS: dict[str, LaneAdapter] = {
    "rag": RagLaneAdapter(),
    "sql": SqlLaneAdapter(),
    "routing": RoutingLaneAdapter(),
}


class CrossLaneEvalError(ValueError):
    """Cross-lane assembly refused because stamped profile ids disagree with the request."""


def composite_join_key(
    *,
    lane: str,
    arm_name: str,
    deployment: str,
    reasoning_effort: str | None,
) -> str:
    effort = reasoning_effort or "none"
    return f"{lane}|{arm_name}|{deployment}|{effort}"


def profile_join_key(*, lane: str, profile_id: str) -> str:
    return f"{lane}|profile|{profile_id}"


def _validate_stamped_profile_ids(
    *,
    lane: str,
    requested_baseline: str,
    requested_candidate: str,
    stamped: dict[str, Any],
) -> None:
    stamped_baseline = stamped.get("baseline_profile_id")
    stamped_candidate = stamped.get("candidate_profile_id")
    if stamped_baseline and stamped_baseline != requested_baseline:
        raise CrossLaneEvalError(
            f"{lane} lane baseline profile mismatch: requested {requested_baseline!r}, "
            f"frozen {stamped_baseline!r}"
        )
    if stamped_candidate and stamped_candidate != requested_candidate:
        raise CrossLaneEvalError(
            f"{lane} lane candidate profile mismatch: requested {requested_candidate!r}, "
            f"frozen {stamped_candidate!r}"
        )


def _join_keys_for_lane(
    *,
    lane: str,
    baseline_profile: str,
    candidate_profile: str,
    frozen_arms: list[dict[str, Any]] | None,
    stamped: dict[str, Any],
) -> dict[str, str]:
    if stamped.get("baseline_profile_id") and stamped.get("candidate_profile_id"):
        return {
            "baseline": profile_join_key(lane=lane, profile_id=baseline_profile),
            "candidate": profile_join_key(lane=lane, profile_id=candidate_profile),
        }
    if not frozen_arms:
        raise CrossLaneEvalError(
            f"{lane} lane join refused: incomplete lane evidence "
            f"(no stamped profile ids and no frozen arms)"
        )
    baseline_arm = next(
        (arm for arm in frozen_arms if arm.get("name") == stamped.get("baseline_arm")),
        frozen_arms[0] if frozen_arms else None,
    )
    candidate_arm = next(
        (arm for arm in frozen_arms if arm.get("name") == stamped.get("candidate_arm")),
        frozen_arms[-1] if frozen_arms else None,
    )
    if baseline_arm is None or candidate_arm is None:
        raise CrossLaneEvalError(
            f"{lane} lane join refused: incomplete lane evidence "
            f"(frozen arms present but baseline/candidate arm metadata missing)"
        )
    return {
        "baseline": composite_join_key(
            lane=lane,
            arm_name=str(baseline_arm.get("name") or ""),
            deployment=str(
                baseline_arm.get("chat_deployment") or baseline_arm.get("requested_model") or ""
            ),
            reasoning_effort=baseline_arm.get("reasoning_effort"),
        ),
        "candidate": composite_join_key(
            lane=lane,
            arm_name=str(candidate_arm.get("name") or ""),
            deployment=str(
                candidate_arm.get("chat_deployment") or candidate_arm.get("requested_model") or ""
            ),
            reasoning_effort=candidate_arm.get("reasoning_effort"),
        ),
    }


def build_cross_lane_plan(
    *,
    baseline_profile: str,
    candidate_profile: str,
    lanes: tuple[LaneName, ...] = ("rag", "sql"),
) -> dict[str, Any]:
    """Dry-run plan for a profile-id pairwise comparison across lanes."""
    manifest = verify_manifest(load_manifest())
    case_counts = {
        "rag": manifest["rag"]["case_count"],
        "sql": manifest["sql_dev"]["case_count"],
        "routing": 0,
    }
    lane_plans: list[dict[str, Any]] = []
    for lane in lanes:
        adapter = LANE_ADAPTERS.get(lane)
        if adapter is not None:
            plan = adapter.plan(
                baseline_profile=baseline_profile,
                candidate_profile=candidate_profile,
                n_cases=case_counts.get(lane, 0),
            )
            lane_plans.append(plan.to_dict())
    return {
        "experiment_id": EXPERIMENT_ID,
        "mode": "dry-run",
        "baseline_profile_id": baseline_profile,
        "candidate_profile_id": candidate_profile,
        "lanes": lane_plans,
        "join_strategy": {
            "preferred": "profile_id when stamped in lane freeze artifacts",
            "fallback": list(COMPOSITE_JOIN_FIELDS),
        },
        "spend_note": "NO paid joint live matrix in this pass — assemble/plan only.",
    }


def _read_lane_verdict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _extract_rag_verdict(run_dir: Path) -> dict[str, Any] | None:
    for name in ("acceptance-ledger.json", "attempts/primary/verdict.json"):
        doc = _read_lane_verdict(run_dir / name)
        if doc is not None:
            return doc
    return None


def _extract_sql_verdict(run_dir: Path) -> dict[str, Any] | None:
    for name in ("attempts/primary/verdict.json",):
        doc = _read_lane_verdict(run_dir / name)
        if doc is not None:
            return doc
    frozen = _read_lane_verdict(run_dir / "frozen-sql-experiment.json")
    if frozen is None:
        return None
    return {"status": "FROZEN_ONLY", "freeze_hash": frozen.get("freeze_hash")}


def assemble_cross_lane_summary(
    *,
    baseline_profile: str,
    candidate_profile: str,
    rag_run_dir: Path | None = None,
    sql_run_dir: Path | None = None,
    lanes: tuple[LaneName, ...] = ("rag", "sql"),
) -> dict[str, Any]:
    """Join lane verdicts by profile id when present, else composite fallback."""
    plan = build_cross_lane_plan(
        baseline_profile=baseline_profile,
        candidate_profile=candidate_profile,
        lanes=lanes,
    )
    entries: list[dict[str, Any]] = []
    if "rag" in lanes and rag_run_dir is not None:
        rag_doc = _extract_rag_verdict(rag_run_dir)
        rag_frozen = _read_lane_verdict(rag_run_dir / "frozen-experiment.json")
        profile_ids = (rag_frozen or {}).get("profile_ids", {})
        _validate_stamped_profile_ids(
            lane="rag",
            requested_baseline=baseline_profile,
            requested_candidate=candidate_profile,
            stamped=profile_ids,
        )
        policy = (rag_frozen or {}).get("policy") or {}
        raw_arms = policy.get("answer_arms")
        if isinstance(raw_arms, (list, tuple)) and len(raw_arms) >= 2:
            base_arm, cand_arm = raw_arms[0], raw_arms[-1]
        elif isinstance(raw_arms, (list, tuple)) and len(raw_arms) == 1:
            base_arm, cand_arm = raw_arms[0], raw_arms[0]
        else:
            base_arm, cand_arm = None, None
        join_keys = _join_keys_for_lane(
            lane="rag",
            baseline_profile=baseline_profile,
            candidate_profile=candidate_profile,
            frozen_arms=list((rag_frozen or {}).get("arms") or []),
            stamped={
                "baseline_profile_id": profile_ids.get("baseline_profile_id"),
                "candidate_profile_id": profile_ids.get("candidate_profile_id"),
                "baseline_arm": base_arm,
                "candidate_arm": cand_arm,
            },
        )
        entries.append(
            {
                "lane": "rag",
                "run_dir": str(rag_run_dir),
                "join_key_baseline": join_keys["baseline"],
                "join_key_candidate": join_keys["candidate"],
                "profile_ids_stamped": profile_ids,
                "verdict": rag_doc,
            }
        )
    if "sql" in lanes and sql_run_dir is not None:
        sql_doc = _extract_sql_verdict(sql_run_dir)
        sql_frozen = _read_lane_verdict(sql_run_dir / "frozen-sql-experiment.json")
        router = ((sql_frozen or {}).get("policy") or {}).get("router_policy") or {}
        stamped = {
            "baseline_profile_id": router.get("baseline_profile_id"),
            "candidate_profile_id": router.get("candidate_profile_id"),
        }
        _validate_stamped_profile_ids(
            lane="sql",
            requested_baseline=baseline_profile,
            requested_candidate=candidate_profile,
            stamped=stamped,
        )
        join_keys = _join_keys_for_lane(
            lane="sql",
            baseline_profile=baseline_profile,
            candidate_profile=candidate_profile,
            frozen_arms=list((sql_frozen or {}).get("arms") or []),
            stamped={
                **stamped,
                "baseline_arm": router.get("escalated_arm"),
                "candidate_arm": router.get("default_arm"),
            },
        )
        entries.append(
            {
                "lane": "sql",
                "run_dir": str(sql_run_dir),
                "join_key_baseline": join_keys["baseline"],
                "join_key_candidate": join_keys["candidate"],
                "profile_ids_stamped": stamped,
                "verdict": sql_doc,
            }
        )
    return {
        **plan,
        "entries": entries,
        "assembled_at": datetime.now(UTC).isoformat(),
        "mode": "dry-run",
    }


def write_cross_lane_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
