"""SQL TaskAdapter: agent execution, execution-match oracle, verdict."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from typing import Any

from app.eval.sql.diagnostics import scrub
from app.experiments.core.checkpoint import cell_set_hash
from app.experiments.core.cost import CEILING_RUNNER_ERROR, price_or_ceiling
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.frozen_contract import verify_frozen_contract as _verify_frozen_contract
from app.experiments.core.money import BUDGET_EPSILON_USD
from app.experiments.core.probe_cells import repeat_zero_cells_per_arm
from app.experiments.core.protocol import Oracle, OracleValidation
from app.experiments.core.secret_redaction import redact_secrets
from app.experiments.core.work_matrix import Cell, enumerate_cells
from app.experiments.holdout_unlock import issue_sql_dev_pass
from app.experiments.sql_eval_gate import (
    SqlGateResult,
    evaluate_sql_gate,
)
from app.experiments.sql_freeze import (
    METRIC_NAME,
    FrozenSqlExperiment,
    SqlArm,
    freeze_sql_experiment,
)
from app.experiments.sql_summary import (
    renamed_replay_metric,
    replay_arm_routing_policy,
    summarize_sql_rows,
)

__all__ = [
    "METRIC_NAME",
    "FrozenSqlExperiment",
    "SqlArm",
    "SqlOracle",
    "SqlTask",
    "freeze_sql_experiment",
]


class SqlOracle:
    """Free execution-match oracle; scoring happens at execute time inside run_case."""

    is_paid = False

    def score(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], OracleValidation]:
        missing_deployment = sorted(
            {
                str(row.get("case_id"))
                for row in rows
                if not row.get("operational_failure") and not row.get("chat_deployment")
            }
        )
        missing_match = sorted(
            {
                str(row.get("case_id"))
                for row in rows
                if not row.get("operational_failure") and not isinstance(row.get("match"), bool)
            }
        )
        validation = OracleValidation(
            canaries_passed=True,
            repeatability_passed=True,
            call_evidence_passed=not (missing_deployment or missing_match),
            details={
                "oracle": METRIC_NAME,
                "cost_usd": 0.0,
                "scored_at": (
                    "execute_time (the comparison needs the agent's own db handle; "
                    "score() is the post-hoc completeness pass)"
                ),
                "canary_stage": "not_applicable - free oracle, nothing to canary",
                "repeatability": (
                    "the scorer is deterministic; agent non-determinism is reported "
                    "per case as flaky_cases, not as an oracle property"
                ),
                "rows_scored": len(rows),
                "rows_missing_deployment": missing_deployment,
                "rows_missing_execution_match": missing_match,
            },
        )
        return rows, validation


class SqlTask:
    """SQL TaskAdapter over a frozen SQL contract."""

    def __init__(
        self,
        *,
        frozen: FrozenSqlExperiment,
        run_case_fn: Callable[..., dict[str, Any]],
        oracle: Oracle,
        issue_unlock_receipt: bool = False,
    ) -> None:
        self._issue_unlock_receipt = issue_unlock_receipt
        self._frozen = frozen
        self._run_case_fn = run_case_fn
        self._oracle = oracle
        self._arm_by_name = {
            arm["name"]: SqlArm(
                name=arm["name"],
                chat_deployment=arm["chat_deployment"],
                reasoning_effort=arm.get("reasoning_effort"),
                request_timeout_s=arm.get("request_timeout_s"),
                requested_provider=arm.get("requested_provider"),
                provider_order=tuple(arm.get("provider_order") or ()),
                verbosity=arm.get("verbosity"),
                allowed_actual_models=tuple(
                    (frozen.document.get("policy") or {})
                    .get("allowed_actual_models", {})
                    .get(arm["name"], ())
                ),
                use_responses_api=bool(arm.get("use_responses_api")),
                api_class=arm.get("api_class"),
            )
            for arm in frozen.document["arms"]
        }
        self._case_by_id = {str(case["id"]): case for case in frozen.document["cases"]}
        self._repeats = int(frozen.document["policy"]["repeats"])
        self._exclude_fold = frozenset(self._case_by_id)

    def oracle(self) -> Oracle:
        return self._oracle

    def freeze_inputs(self) -> dict[str, Any]:
        document = self._frozen.document
        return {
            "freeze_hash": self._frozen.freeze_hash,
            "runtime": document["runtime"],
            "campaign_cap_usd": document["campaign_cap_usd"],
            "spend_stages": document.get("spend_stages"),
            "answer_order_seed": document["policy"]["answer_order_seed"],
        }

    def cells(self) -> list[Cell]:
        return enumerate_cells(list(self._case_by_id), list(self._arm_by_name), self._repeats)

    def cell_provider(self, cell: Cell) -> str | None:
        arm = self._arm_by_name[cell.arm_name]
        if arm.requested_provider:
            return arm.requested_provider
        if arm.chat_deployment:
            from app.config import settings
            from app.providers.model_registry import resolve_model_spec

            return resolve_model_spec(arm.chat_deployment, settings).credential_source
        return None

    def probe_cells(self) -> list[Cell]:
        return repeat_zero_cells_per_arm(self.cells(), list(self._arm_by_name))

    def _cell_ceiling_usd(self, arm_name: str) -> float:
        return float(self._frozen.document["spend_stages"]["answer_cell_ceiling_usd"][arm_name])

    def _derive_cost(
        self, result: dict[str, Any], *, arm: SqlArm, ceiling_usd: float
    ) -> tuple[float, str, str | None]:
        """Price the row via shared fail-closed cost helper."""
        priced = price_or_ceiling(
            deployment=result.get("chat_deployment") or arm.chat_deployment,
            tokens_in=int(result.get("tokens_prompt") or 0),
            tokens_out=int(result.get("tokens_completion") or 0),
            n_llm_calls=int(result.get("n_llm_calls") or 0),
            ceiling_usd=ceiling_usd,
        )
        return priced.usd, priced.source, priced.deployment

    def execute(self, cell: Cell) -> dict[str, Any]:
        case = self._case_by_id[cell.case_id]
        arm = self._arm_by_name[cell.arm_name]
        ceiling_usd = self._cell_ceiling_usd(arm.name)
        try:
            run_kwargs: dict[str, Any] = {
                "chat_deployment": arm.chat_deployment,
                "reasoning_effort": arm.reasoning_effort,
                "request_timeout_s": arm.request_timeout_s,
                # Same frozen number AnswerMatrix.enforce_ceiling reads (answer_matrix.py) --
                # one source of truth for the ceiling makes the run budget self-stop before
                # the matrix-level guard ever has to abort the cell.
                "cell_ceiling_usd": ceiling_usd,
            }
            if arm.verbosity is not None:
                run_kwargs["verbosity"] = arm.verbosity
            if arm.provider_order:
                run_kwargs["provider_order"] = list(arm.provider_order)
            if arm.use_responses_api:
                run_kwargs["use_responses_api"] = True
            result = self._run_case_fn(case, self._exclude_fold, **run_kwargs)
            runner_error: str | None = None
        except Exception as exc:  # noqa: BLE001 - keep evidence, charge the ceiling
            result = {"chat_deployment": arm.chat_deployment}
            # Scrub SQL from errors; redact any embedded credentials.
            runner_error = f"{type(exc).__name__}: {redact_secrets(scrub(str(exc)))}"

        cost_usd, cost_source, priced_deployment = self._derive_cost(
            result, arm=arm, ceiling_usd=ceiling_usd
        )
        if runner_error is not None:
            cost_usd, cost_source = ceiling_usd, CEILING_RUNNER_ERROR

        gold = self._frozen.document["gold_verification"][cell.case_id]
        degenerate = bool(gold.get("degenerate_candidate"))
        matched = bool(result.get("match"))
        return {
            **result,
            "case_id": cell.case_id,
            "arm": arm.name,
            "repeat": cell.repeat,
            # Requested vs actual. For a router arm these DIFFER, and differ per case;
            # persisting only the arm label would make attribution a guess.
            "requested_arm": arm.name,
            "requested_deployment": arm.chat_deployment,
            "requested_provider": arm.requested_provider or result.get("requested_provider"),
            "actual_provider": result.get("actual_provider"),
            "actual_model": result.get("actual_model"),
            "router_arm": arm.chat_deployment is None,
            "chat_deployment": result.get("chat_deployment"),
            "priced_deployment": priced_deployment,
            "cost_usd": cost_usd,
            "cost_source": cost_source,
            "cell_ceiling_usd": ceiling_usd,
            "runner_error": runner_error,
            # Frozen gold shape carried onto the row so a coincidental match on this one
            # snapshot is countable, not indistinguishable from a real one.
            "degenerate_candidate": degenerate,
            "degenerate_reason": gold.get("degenerate_reason"),
            "coincidental_match_suspected": matched and degenerate,
        }

    @staticmethod
    def classify_failure(row: dict[str, Any]) -> str | None:
        """Operational (infrastructure) failure, or None.

        Invalid SQL and a wrong answer are MODEL misses, not operational failures --
        they belong in the metric, not in the abort budget.
        """
        if row.get("runner_error"):
            return "runner_error"
        if row.get("operational_failure") in ("budget_exceeded", "budget_clarify"):
            return row["operational_failure"]
        if row.get("infra_error"):
            return "infra_error"
        if row.get("agent_error") and int(row.get("n_sql_attempts") or 0) == 0:
            # The agent crashed before issuing ANY query, so the model never got a fair
            # chance and there is nothing to score. A crash AFTER the model ran SQL (e.g.
            # an output-parser failure) is downstream of its answer and stays a model
            # result -- misclassifying that as operational would quietly excuse real
            # misses. The distinction is observable, not a tuned threshold.
            return "agent_error"
        if not isinstance(row.get("match"), bool):
            return "no_execution_score"
        return None

    def evidence_failures(self, row: dict[str, Any], *, stage: str = "final") -> list[str]:
        # SQL has no deferred evidence; stage is accepted for protocol parity and ignored.
        del stage
        arm_name = str(row.get("arm"))
        arm = self._arm_by_name.get(arm_name)
        if arm is None:
            return ["unknown_arm"]
        failures: list[str] = []
        if row.get("requested_deployment") != arm.chat_deployment:
            failures.append("requested_deployment_mismatch")
        if row.get("operational_failure"):
            return failures
        actual = row.get("chat_deployment")
        if not actual:
            failures.append("missing_chat_deployment")
        elif arm.chat_deployment is not None and actual != arm.chat_deployment:
            # A forced arm that silently ran on another deployment invalidates every
            # comparison built on it.
            failures.append("deployment_mismatch")
        allowed_models = set(arm.allowed_actual_models)
        actual_model = row.get("actual_model")
        if (
            allowed_models
            and actual_model
            and actual_model not in allowed_models
            and not (arm.requested_provider == "openrouter" and actual_model == arm.chat_deployment)
        ):
            failures.append("unexpected_actual_model")
        if not row.get("requested_provider") and arm.requested_provider:
            failures.append("missing_requested_provider")
        if not row.get("actual_provider") and row.get("requested_provider"):
            failures.append("missing_actual_provider")
        if not isinstance(row.get("match"), bool):
            failures.append("missing_execution_score")
        try:
            cost_usd = float(row.get("cost_usd"))
            latency_ms = float(row.get("latency_total_ms"))
        except (TypeError, ValueError):
            failures.append("invalid_numeric_evidence")
        else:
            if not math.isfinite(cost_usd) or cost_usd < 0:
                failures.append("invalid_cost")
            elif cost_usd > self._cell_ceiling_usd(arm_name) + BUDGET_EPSILON_USD:
                failures.append("cell_ceiling_exceeded")
            if not math.isfinite(latency_ms) or latency_ms < 0:
                failures.append("invalid_latency")
        return failures

    def enforce_operational_budget(self, rows: list[dict[str, Any]]) -> None:
        """Stop a run already destined to HOLD before it spends on the rest of the matrix.

        The denominator is the full expected row count (cases x arms x repeats): one row
        is one opportunity to produce evidence, and every row either produces it or is
        lost, so the rate is directly comparable to the frozen budget. Do NOT scale that
        denominator by a per-row metric count -- SQL reaches exactly one verdict per row,
        and inflating the denominator would silently raise the number of failures the run
        tolerates before it stops spending.

        ``budget_exceeded`` and ``budget_clarify`` (context / LLM-call run caps) stay
        operational for scoring exclusion but do NOT count toward this kill switch —
        those stops are expected fairness infrastructure, not auth/routing infra loss
        (bc-84a4e015).
        """
        failed = [
            row
            for row in rows
            if row.get("operational_failure")
            and row.get("operational_failure") not in ("budget_exceeded", "budget_clarify")
        ]
        if not failed:
            return
        policy = self._frozen.document["policy"]
        expected_rows = (
            len(policy["expected_case_ids"]) * len(policy["arms"]) * int(policy["repeats"])
        )
        max_rate = float(policy["max_opportunity_failure_rate"])
        if expected_rows and len(failed) / expected_rows > max_rate:
            # Carry the diagnosis in the message. A bare "budget exhausted" is a BUDGET
            # -shaped line for what is usually an auth, routing or connectivity fault,
            # and it sends the next human to the wrong file.
            kinds: dict[str, int] = {}
            for row in failed:
                kind = str(row.get("operational_failure"))
                kinds[kind] = kinds.get(kind, 0) + 1
            by_arm: dict[str, int] = {}
            for row in failed:
                arm = str(row.get("arm"))
                by_arm[arm] = by_arm.get(arm, 0) + 1
            details = sorted(
                {
                    str(row.get("runner_error") or row.get("infra_error"))
                    for row in failed
                    if row.get("runner_error") or row.get("infra_error")
                }
            )[:3]
            raise RagExperimentError(
                f"operational failure budget exhausted: {len(failed)} operational SQL "
                f"failures already exceed the {max_rate:.0%} opportunity budget - "
                f"stopping before further spend. kinds={kinds} by_arm={by_arm} "
                f"first_errors={details}"
            )

    def verify_frozen_contract(self) -> None:
        """Re-verify the in-memory snapshot against disk and its own hash."""
        _verify_frozen_contract(self._frozen, error_type=RagExperimentError)

    def checkpoint_identity(self) -> dict[str, Any]:
        cells = self.cells()
        cell_keys = [(cell.case_id, cell.arm_name, cell.repeat) for cell in cells]
        document = self._frozen.document
        runtime = document["runtime"]
        return {
            "schema_version": 1,
            "task_kind": "sql",
            "freeze_hash": self._frozen.freeze_hash,
            "commit_sha": runtime.get("commit_sha"),
            "expected_row_count": len(cells),
            "expected_cell_set_hash": cell_set_hash(cell_keys),
            "judge_policy_hash": (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(document["policy"], sort_keys=True).encode()
                ).hexdigest()
            ),
            "route_identity_hash": runtime.get("route_identity_hash"),
            "azure_deployment_attestations": runtime.get("azure_deployment_attestations"),
        }

    def finalize(
        self,
        *,
        scored_rows: list[dict[str, Any]],
        oracle_validation: OracleValidation,
        actual_spend_usd: float,
        store: Any,
    ) -> SqlGateResult:
        self.verify_frozen_contract()
        document = self._frozen.document
        policy = document["policy"]
        summary = summarize_sql_rows(scored_rows, self._frozen)
        summary["actual_spend_usd"] = actual_spend_usd
        summary["smoke_passed"] = self._smoke_passed(scored_rows)

        router_policy = policy.get("router_policy")
        if router_policy:
            summary["router_policy_replay"] = renamed_replay_metric(
                replay_arm_routing_policy(
                    scored_rows,
                    document["cases"],
                    _routing_predicate(router_policy["predicate"]),
                    default_arm=router_policy["default_arm"],
                    escalated_arm=router_policy["escalated_arm"],
                )
            )

        # Collect every HOLD condition that fired, then downgrade ONCE. Applying the four
        # as consecutive rewraps replaced `reasons` each time and buried the previous
        # verdict a level deeper, so a run that failed its probe AND blew the cap reported
        # only "campaign cap exceeded" -- one of four real problems, with the rest reachable
        # solely by an operator who already suspected them.
        gate_verdict = self._evaluate_gate(summary)
        holds: list[str] = []
        hold_details: dict[str, Any] = {}

        if not summary["smoke_passed"]:
            # HOLD, not FAIL. `evaluate_sql_dev_gate` folds smoke_passed into `reasons`,
            # which produces FAIL -- a verdict that reads as "the candidate is worse". A
            # rate limit on one probe cell is INCONCLUSIVE, the same distinction
            # cases_without_comparable_pair already makes below.
            holds.append("smoke gate did not pass: a probe cell failed operationally")
        unpaired = summary["cases_without_comparable_pair"]
        if unpaired:
            # Fail closed on thin evidence. evaluate_sql_dev_gate cannot see a case that
            # produced no comparable pair, and paired_bootstrap_ci's n==0 branch returns
            # lo=0.0 -- so a run that lost most of one arm to infra failures would come
            # back SQL_DEV_PASS having measured almost nothing.
            holds.append(
                f"{len(unpaired)} of {summary['n_cases']} case(s) produced no "
                f"comparable {summary['baseline_arm']}/{summary['candidate_arm']} "
                f"pair: {', '.join(unpaired)}"
            )
            hold_details["cases_without_comparable_pair"] = unpaired
        if not oracle_validation.call_evidence_passed:
            holds.append("oracle call evidence failed")
            hold_details["oracle_validation"] = dict(oracle_validation.details)
        campaign_cap_usd = float(document["campaign_cap_usd"])
        if actual_spend_usd > campaign_cap_usd + BUDGET_EPSILON_USD:
            holds.append("campaign cap exceeded")
            hold_details["campaign_cap_usd"] = campaign_cap_usd
            hold_details["accounted_spend_usd"] = actual_spend_usd

        verdict = gate_verdict
        if holds:
            # ONE `underlying_verdict`, and it is the GATE's -- not a HOLD wrapper around a
            # HOLD wrapper around it. Each condition keeps its own evidence beside it.
            verdict = SqlGateResult(
                status="HOLD",
                reasons=holds,
                details={**hold_details, "underlying_verdict": gate_verdict.to_dict()},
            )

        store.seal_verdict(
            scored_rows=scored_rows,
            oracle_validation=oracle_validation,
            verdict=verdict,
            oracle_validation_filename="oracle_validation.json",
            extra={"summary.json": summary},
        )

        # LAST, and against the FINAL verdict. evaluate_sql_dev_gate(write_receipt=True)
        # mints the holdout unlock the instant IT decides SQL_DEV_PASS -- before the
        # smoke, thin-evidence, oracle-evidence and campaign-cap downgrades above. A run
        # that ends HOLD would otherwise leave the sealed holdout unlocked on disk, and
        # that receipt is the only thing standing between an operator and it. Writing it
        # after the artifacts also means a failed store write cannot leave an unlocked
        # holdout with no verdict.json beside it.
        if self._issue_unlock_receipt and verdict.status == "SQL_DEV_PASS":
            issue_sql_dev_pass(verdict.to_dict())
        return verdict

    def _smoke_passed(self, rows: list[dict[str, Any]]) -> bool:
        """Every probe cell (one repeat-0 cell per arm) produced a clean, scored row."""
        probes = set(self.probe_cells())
        by_cell = {
            Cell(str(row["case_id"]), str(row["arm"]), int(row["repeat"])): row for row in rows
        }
        return all(
            probe in by_cell
            and not by_cell[probe].get("operational_failure")
            and isinstance(by_cell[probe].get("match"), bool)
            for probe in probes
        )

    def _evaluate_gate(self, summary: dict[str, Any]) -> SqlGateResult:
        document = self._frozen.document
        policy = document["policy"]
        baseline = policy["baseline_arm"]
        candidate = policy["candidate_arm"]
        router = policy.get("router_policy") or {}
        baseline_profile = router.get("baseline_profile_id")
        cost_ratio_calibrated = (
            baseline_profile in (None, "prod-luna") and baseline == "forced-luna"
        )
        common = {
            "pass_rate_pairs": [tuple(pair) for pair in summary["pass_rate_pairs"]],
            "invalid_sql_rate_baseline": 1.0 - summary["by_arm"][baseline]["valid_sql_rate"],
            "invalid_sql_rate_candidate": 1.0 - summary["by_arm"][candidate]["valid_sql_rate"],
            "safety_failures": summary["safety_failures"],
            # The gate's parameter is named ..._luna for the original experiment; it is
            # the BASELINE arm's cost, whatever that arm is called here. It is the
            # MEASURED cost (see summarize_sql_rows) -- a fail-closed ceiling charge is
            # ledger truth, not a price observation, and folding 8x-the-mean synthetic
            # charges into a `candidate <= 0.5 x baseline` ratio moves the economics
            # verdict in whichever direction the infra noise happened to land.
            "cost_usd_baseline_luna": summary["by_arm"][baseline]["cost_usd_for_gate"],
            "cost_usd_candidate": summary["by_arm"][candidate]["cost_usd_for_gate"],
            # The gate's parameter is named infra_failure_rate; every operational
            # failure belongs in it, not only the four openai exception types run_case
            # happens to name (a dropped snapshot connection is just as much "this run
            # measured nothing" as a 429).
            "infra_failure_rate": summary["operational_failure_rate"],
            "smoke_passed": summary["smoke_passed"],
            "pass_threshold": float(policy["pass_threshold"]),
            "cost_ratio_calibrated": cost_ratio_calibrated,
        }
        if document["stage"] == "holdout":
            return evaluate_sql_gate(
                stage="full",
                dev_passed=True,
                holdout_run=True,
                **common,
            )
        # write_receipt is ALWAYS False here: the receipt is written by finalize()
        # against the FINAL verdict, after its HOLD downgrades. See finalize().
        return evaluate_sql_gate(
            stage="dev",
            write_receipt=False,
            **common,
        )


def _routing_predicate(name: str) -> Callable[[str], bool]:
    """Resolve a frozen predicate NAME to a callable. Unknown names fail closed."""
    if name == "is_hard_financial":
        from app.rag.model_router import is_hard_financial

        return is_hard_financial
    raise RagExperimentError(f"unknown frozen routing predicate: {name!r}")
