"""RAG TaskAdapter: frozen retrieval contract, answer execution, LLM judge."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from app.experiments.core.checkpoint import cell_set_hash
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.hashing import sha256_json
from app.experiments.core.money import BUDGET_EPSILON_USD
from app.experiments.core.probe_cells import repeat_zero_cells_per_arm
from app.experiments.core.protocol import (
    Oracle,
    OracleValidation,
    SettlingTaskAdapter,
)
from app.experiments.core.secret_redaction import redact_secrets
from app.experiments.core.work_matrix import Cell, enumerate_cells
from app.experiments.rag_evidence_gates import evaluate_rag_evidence
from app.experiments.rag_experiment import (
    AnswerArm,
    FrozenRagExperiment,
    RagEvidenceVerdict,
    RagGatePolicy,
)
from app.experiments.rag_experiment import (
    answer_evidence_failures as _answer_evidence_failures,
)
from app.experiments.rag_experiment import (
    classify_answer_failure as _classify_answer_failure,
)
from app.experiments.rag_experiment import (
    enforce_operational_budget as _enforce_operational_budget,
)
from app.experiments.rag_experiment import (
    verify_frozen_contract as _verify_frozen_contract,
)
from app.experiments.rag_judge import (
    rag_oracle_for,
)

__all__ = ["RagOracle", "RagTask"]


RagOracle = rag_oracle_for


class RagTask:
    """RAG TaskAdapter over a frozen RAG contract."""

    def __new__(
        cls,
        *,
        frozen: FrozenRagExperiment,
        answer_fn: Callable[[dict[str, Any], AnswerArm, int], dict[str, Any]],
        oracle: Oracle,
        settle_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> RagTask:
        if cls is RagTask and settle_fn is not None:
            instance = super().__new__(SettlingRagTask)
            instance._init_rag_task(
                frozen=frozen, answer_fn=answer_fn, oracle=oracle, settle_fn=settle_fn
            )
            return instance
        instance = super().__new__(cls)
        instance._init_rag_task(
            frozen=frozen, answer_fn=answer_fn, oracle=oracle, settle_fn=settle_fn
        )
        return instance

    def _init_rag_task(
        self,
        *,
        frozen: FrozenRagExperiment,
        answer_fn: Callable[[dict[str, Any], AnswerArm, int], dict[str, Any]],
        oracle: Oracle,
        settle_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> None:
        del settle_fn
        self._frozen = frozen
        self._answer_fn = answer_fn
        self._oracle = oracle
        arms = tuple(AnswerArm(**arm) for arm in frozen.document["arms"])
        self._arm_by_name = {arm.name: arm for arm in arms}
        self._case_by_id = {
            frozen_case["case"]["id"]: frozen_case for frozen_case in frozen.document["cases"]
        }
        self._repeats = int(frozen.document["policy"]["repeats"])

    def oracle(self) -> Oracle:
        return self._oracle

    def freeze_inputs(self) -> dict[str, Any]:
        document = self._frozen.document
        inputs: dict[str, Any] = {
            "freeze_hash": self._frozen.freeze_hash,
            "runtime": document["runtime"],
            "campaign_cap_usd": document["campaign_cap_usd"],
            "spend_stages": document.get("spend_stages"),
            "judge": document["judge"],
        }
        inputs["answer_order_seed"] = document["policy"]["answer_order_seed"]
        return inputs

    def cells(self) -> list[Cell]:
        return enumerate_cells(list(self._case_by_id), list(self._arm_by_name), self._repeats)

    def cell_provider(self, cell: Cell) -> str | None:
        return self._arm_by_name[cell.arm_name].requested_provider

    def probe_cells(self) -> list[Cell]:
        """One repeat-0 cell per arm, in frozen arm order."""
        return repeat_zero_cells_per_arm(self.cells(), list(self._arm_by_name))

    def execute(self, cell: Cell) -> dict[str, Any]:
        frozen_case = self._case_by_id[cell.case_id]
        arm = self._arm_by_name[cell.arm_name]
        spend_stages = self._frozen.document.get("spend_stages")
        try:
            response = self._answer_fn(frozen_case, arm, cell.repeat)
            response_cost = float(response.get("cost_usd"))
            if not math.isfinite(response_cost) or response_cost < 0:
                raise RagExperimentError("answer adapter returned invalid cost evidence")
        except Exception as exc:  # noqa: BLE001 - preserve per-call evidence, fail closed
            failure_cost = (
                float(spend_stages["answer_cell_ceiling_usd"][arm.name]) if spend_stages else 0.0
            )
            response = {
                "answer": "",
                "requested_provider": arm.requested_provider,
                "requested_model": arm.requested_model,
                "actual_provider": "unknown",
                "actual_provider_source": "unavailable",
                "actual_model": "unknown",
                "actual_model_source": "unavailable",
                "request_id": "unavailable",
                "request_id_source": "callback",
                "finish_reason": "error",
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "max_output_tokens": 0,
                "cost_usd": failure_cost,
                "route_metadata_status": (
                    "error" if arm.requested_provider == "openrouter" else "not_applicable"
                ),
                # Provider errors may embed credentials; redact before persistence.
                "error": redact_secrets(f"{type(exc).__name__}: {exc}"),
            }
        case = frozen_case["case"]
        return {
            "case_id": case["id"],
            "arm": arm.name,
            "repeat": cell.repeat,
            "question": case["question"],
            "expected_answer": case["expected_answer"],
            "retrieved_contexts": frozen_case["retrieval"]["contexts"],
            "sources": frozen_case["retrieval"]["sources"],
            "retrieval_identity": frozen_case["retrieval"]["retrieval_identity"],
            "context_hash": frozen_case["context_hash"],
            **response,
        }

    @staticmethod
    def classify_failure(row: dict[str, Any]) -> str | None:
        """Delegates: the failure vocabulary has ONE definition."""
        return _classify_answer_failure(row)

    def evidence_failures(self, row: dict[str, Any], *, stage: str = "final") -> list[str]:
        """Route attestation is lenient until final when settlement is deferred."""
        require_route_attestation = stage == "final" or not isinstance(self, SettlingTaskAdapter)
        return _answer_evidence_failures(
            row,
            policy=self._frozen.document["policy"],
            spend_stages=self._frozen.document.get("spend_stages"),
            require_route_attestation=require_route_attestation,
        )

    def enforce_operational_budget(self, rows: list[dict[str, Any]]) -> None:
        _enforce_operational_budget(rows, policy=self._frozen.document["policy"])

    def verify_frozen_contract(self) -> None:
        """Delegates to ``rag_experiment.verify_frozen_contract``."""
        _verify_frozen_contract(self._frozen)

    def checkpoint_identity(self) -> dict[str, Any]:
        cells = self.cells()
        cell_keys = [(cell.case_id, cell.arm_name, cell.repeat) for cell in cells]
        cell_set_hash_value = cell_set_hash(cell_keys)
        document = self._frozen.document
        runtime = document["runtime"]
        return {
            "schema_version": 1,
            "task_kind": "rag",
            "freeze_hash": self._frozen.freeze_hash,
            "commit_sha": runtime.get("commit_sha"),
            "expected_row_count": len(cells),
            "expected_cell_set_hash": cell_set_hash_value,
            "judge_policy_hash": sha256_json(document["policy"]),
            "judge_model_hash": sha256_json(document["judge"]),
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
    ) -> RagEvidenceVerdict:
        for row in scored_rows:
            if row.get("operational_failure"):
                for metric in self._frozen.document["policy"]["metrics"]:
                    row[metric] = 0.0

        self.verify_frozen_contract()
        policy_doc = self._frozen.document["policy"]
        policy = RagGatePolicy.from_policy_doc(policy_doc)
        verdict = evaluate_rag_evidence(
            scored_rows,
            policy=policy,
            judge_validation=oracle_validation,
        )
        campaign_cap_usd = float(self._frozen.document["campaign_cap_usd"])
        if actual_spend_usd > campaign_cap_usd + BUDGET_EPSILON_USD:
            verdict = RagEvidenceVerdict(
                status="HOLD",
                reason_codes=("campaign_cap_exceeded",),
                details={
                    "campaign_cap_usd": campaign_cap_usd,
                    "accounted_spend_usd": actual_spend_usd,
                    "underlying_verdict": asdict(verdict),
                },
            )
        store.seal_verdict(
            scored_rows=scored_rows,
            oracle_validation=oracle_validation,
            verdict=verdict,
            oracle_validation_filename="judge_validation.json",
        )
        return verdict


class SettlingRagTask(RagTask):
    """RAG TaskAdapter that settles deferred OpenRouter generation metadata."""

    def _init_rag_task(
        self,
        *,
        frozen: FrozenRagExperiment,
        answer_fn: Callable[[dict[str, Any], AnswerArm, int], dict[str, Any]],
        oracle: Oracle,
        settle_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> None:
        super()._init_rag_task(
            frozen=frozen,
            answer_fn=answer_fn,
            oracle=oracle,
            settle_fn=settle_fn,
        )
        assert settle_fn is not None
        self._settle_fn = settle_fn

    def settle_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch deferred OpenRouter /generation metadata for pending rows."""
        return self._settle_fn(rows)
