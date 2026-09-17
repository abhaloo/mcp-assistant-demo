"""Scoring for the production-ask quality suite.

The scorer turns one observation into one typed score record. It runs the
deterministic checks, records what it could not run and why, and leaves the
judged dimensions to the runner — a judged score is a separate, paid decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.eval.ask_route.case import (
    AskObservation,
    Dimension,
    ProdAskCase,
    SqlOracleRow,
    Transport,
)
from app.eval.ask_route.checks import (
    CheckOutcome,
    CheckResult,
    SkippedCheck,
    check_accuracy,
    check_citations,
    check_follow_ups,
    check_permission_variance,
    check_rich_text,
)
from app.eval.failure_store import failure_path_for

# Dimensions that never resolve without a model verdict. A run that does not
# pay for a judge reports them as pending, never as passed.
JUDGED_DIMENSIONS: frozenset[Dimension] = frozenset({"helpfulness"})


class CheckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    dimension: Dimension
    passed: bool
    detail: str
    degraded: bool = False


class SkipRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    dimension: Dimension
    reason: str


class JudgedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Dimension
    score: int = Field(ge=1, le=5)
    min_score: int = Field(ge=1, le=5)
    evidence_quote: str = ""
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.score >= self.min_score


class ProdAskCaseScore(BaseModel):
    """One case, one transport, one run mode."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    mode: str
    transport: Transport
    dimensions: list[Dimension]
    checks: list[CheckRecord] = Field(default_factory=list)
    skipped: list[SkipRecord] = Field(default_factory=list)
    judged: list[JudgedRecord] = Field(default_factory=list)

    @property
    def deterministic_passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def judged_pending(self) -> list[Dimension]:
        judged_seen = {record.dimension for record in self.judged}
        return sorted((set(self.dimensions) & JUDGED_DIMENSIONS) - judged_seen)

    @property
    def passed(self) -> bool | None:
        """None while a judged dimension this case claims has no verdict yet."""
        if not self.deterministic_passed:
            return False
        if self.judged_pending:
            return None
        return all(record.passed for record in self.judged)

    @property
    def failed_check_ids(self) -> list[str]:
        return [check.check_id for check in self.checks if not check.passed]

    def pass_rate(self) -> float:
        """Fraction of resolved judgements that passed, for failure capture."""
        resolved = [check.passed for check in self.checks] + [
            record.passed for record in self.judged
        ]
        if not resolved:
            return 0.0
        return sum(resolved) / len(resolved)


def _record(outcome: CheckOutcome) -> CheckRecord | SkipRecord:
    if isinstance(outcome, SkippedCheck):
        return SkipRecord(
            check_id=outcome.check_id, dimension=outcome.dimension, reason=outcome.reason
        )
    return CheckRecord(
        check_id=outcome.check_id,
        dimension=outcome.dimension,
        passed=outcome.passed,
        detail=outcome.detail,
        degraded=outcome.degraded,
    )


def score_observation(
    case: ProdAskCase,
    observation: AskObservation,
    *,
    sql_oracle: SqlOracleRow | None = None,
    manifest_resources: frozenset[str] = frozenset(),
) -> ProdAskCaseScore:
    """Run every deterministic check this case claims, in this run mode."""
    outcomes: list[CheckOutcome] = []
    if "accuracy" in case.dimensions:
        outcomes.extend(check_accuracy(case, observation, sql_oracle))
    if "citations" in case.dimensions:
        outcomes.extend(check_citations(case, observation))
    if "rich_text" in case.dimensions:
        outcomes.extend(check_rich_text(case, observation))
    if "follow_ups" in case.dimensions:
        outcomes.extend(check_follow_ups(case, observation, manifest_resources))

    score = ProdAskCaseScore(
        case_id=case.id,
        mode=observation.mode,
        transport=observation.transport,
        dimensions=list(case.dimensions),
    )
    for outcome in outcomes:
        record = _record(outcome)
        if isinstance(record, SkipRecord):
            score.skipped.append(record)
        else:
            score.checks.append(record)
    return score


def add_permission_variance(
    score: ProdAskCaseScore,
    case: ProdAskCase,
    observation: AskObservation,
    twin: AskObservation,
) -> ProdAskCaseScore:
    """Fold the paired-principal comparison into an already-scored case."""
    record = _record(check_permission_variance(case, observation, twin))
    if isinstance(record, SkipRecord):
        score.skipped.append(record)
    else:
        score.checks.append(record)
    return score


def transport_parity(
    json_observation: AskObservation, sse_observation: AskObservation
) -> CheckResult:
    """JSON and SSE must deliver the same answer, citations, and chips.

    The two transports assemble the payload in different modules, so this is a
    real divergence check rather than a restatement of one of them.
    """
    differences: list[str] = []
    left, right = json_observation.answer, sse_observation.answer
    if left.answer != right.answer:
        differences.append("answer text")
    if left.citations.model_dump() != right.citations.model_dump():
        differences.append("citations")
    if left.follow_up_suggestions != right.follow_up_suggestions:
        differences.append("follow_up_suggestions")
    if [s.id for s in left.sources] != [s.id for s in right.sources]:
        differences.append("source ids")
    return CheckResult(
        check_id="transport.json_sse_parity",
        dimension="citations",
        passed=not differences,
        detail=f"transports differ on: {differences}" if differences else "JSON and SSE agree",
    )


def summarize(scores: list[ProdAskCaseScore]) -> dict[str, Any]:
    """Aggregate a run. Every skipped check is counted, never dropped."""
    by_dimension: dict[str, dict[str, int]] = {}
    skipped_by_reason: dict[str, int] = {}
    degraded = 0
    for score in scores:
        for check in score.checks:
            bucket = by_dimension.setdefault(
                check.dimension, {"run": 0, "passed": 0, "degraded": 0}
            )
            bucket["run"] += 1
            bucket["passed"] += int(check.passed)
            bucket["degraded"] += int(check.degraded)
            degraded += int(check.degraded)
        for skip in score.skipped:
            key = f"{skip.check_id}: {skip.reason}"
            skipped_by_reason[key] = skipped_by_reason.get(key, 0) + 1

    resolved = [score.passed for score in scores if score.passed is not None]
    return {
        "cases_scored": len({score.case_id for score in scores}),
        "observations": len(scores),
        "deterministic_passed": sum(1 for s in scores if s.deterministic_passed),
        "resolved_passed": sum(1 for value in resolved if value),
        "resolved": len(resolved),
        "pending_judgement": [
            {"case_id": s.case_id, "dimensions": s.judged_pending}
            for s in scores
            if s.passed is None
        ],
        "by_dimension": dict(sorted(by_dimension.items())),
        "degraded_checks": degraded,
        "skipped_checks": dict(sorted(skipped_by_reason.items(), key=lambda kv: -kv[1])),
        "failing_cases": sorted(
            {s.case_id for s in scores if s.passed is False},
        ),
    }


def failure_record(case: ProdAskCase, score: ProdAskCaseScore, *, run_id: str) -> dict[str, Any]:
    """G4 payload for one low-scoring case.

    Deliberately not the SQL-agent failure shape: nothing here carries a
    generated query, so those fields would always be null and would invite a
    reader to treat this as a SQL failure.
    """
    return {
        "schema_version": 1,
        "suite": "prod_ask",
        "status": "candidate",
        "source": "eval",
        "run_id": run_id,
        "case_id": case.id,
        "mode": score.mode,
        "transport": score.transport,
        "role": case.principal.role,
        "principal_label": case.principal.label,
        "route": case.route,
        "question": case.question,
        "dimensions": list(case.dimensions),
        "pass_rate": round(score.pass_rate(), 3),
        "failed_checks": [
            {"check_id": c.check_id, "dimension": c.dimension, "detail": c.detail}
            for c in score.checks
            if not c.passed
        ],
        "degraded_checks": [c.check_id for c in score.checks if c.degraded],
        "skipped_checks": [{"check_id": s.check_id, "reason": s.reason} for s in score.skipped],
        "judged": [record.model_dump() for record in score.judged],
        "failure_kind": None,
        "lever": None,
        "root_cause": None,
    }


def write_failures(
    cases: dict[str, ProdAskCase],
    scores: list[ProdAskCaseScore],
    *,
    out_dir: str | Path,
    run_id: str,
) -> list[Path]:
    """Write one G4 file per failing observation, using the canonical naming."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for score in scores:
        if score.passed is not False:
            continue
        case = cases[score.case_id]
        path = failure_path_for(
            directory,
            run_id=run_id,
            case_id=score.case_id,
            mode=f"{score.mode}-{score.transport}",
            source="eval",
        )
        path.write_text(
            json.dumps(failure_record(case, score, run_id=run_id), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(path)
    return written
