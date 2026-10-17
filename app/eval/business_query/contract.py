"""Frozen Business Query suite, run-kind, and comparison equality-key gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.eval.business_query.scorer import CaseScoringSpec

FROZEN_VARIANTS = (
    "clear_english",
    "noisy_english",
    "natural_kiswahili",
    "code_switched",
)

SHARED_EQUALITY_KEYS = (
    "suite_hash",
    "family_manifest_hash",
    "oracle_hash",
    "scoring_spec_hash",
    "bundle_hash",
    "catalog_hash",
    "db_snapshot_hash",
    "db_content_manifest_hash",
    "principal_hash",
    "contract_hash",
    "controlled_route_card_hash",
)

PER_ARM_IDENTITIES = (
    "module_id",
    "adapter_id",
    "prompt_hash",
    "protocol_id",
    "route_id",
)

CAUSAL_ARMS = ("module-control", "module-candidate")
LEGACY_BENCHMARK_ARMS = ("legacy-as-shipped", "legacy-clean")

# B5 fix: an explicit floor independent of the manifest's own size, so a gate
# run cannot pass merely because a suite is internally consistent with a
# manifest that has been silently shrunk. Matches the family-manifest.json
# family/variant count in this repository today (21 families x 4 variants).
MINIMUM_GATE_FAMILY_COUNT = 21
MINIMUM_GATE_CASE_COUNT = 84

RunKind = Literal["gate", "diagnostic", "protocol_smoke"]


class RunKindError(ValueError):
    """Illegal run-kind / subset combination."""


@dataclass(frozen=True)
class FrozenFamilyManifest:
    families: tuple[str, ...]
    relationship_families: tuple[str, ...]
    variants: tuple[str, ...]
    required_deterministic_examples: tuple[str, ...]
    frozen_eligible_domains: tuple[str, ...]
    minimum_relationship_families: int
    digest: str

    @classmethod
    def load(cls, path: Path) -> FrozenFamilyManifest:
        payload = json.loads(path.read_text(encoding="utf-8"))
        families = tuple(payload["families"])
        relationship = tuple(payload["relationship_families"])
        variants = tuple(payload["variants"])
        examples = tuple(payload["required_deterministic_examples"])
        domains = tuple(payload["frozen_eligible_domains"])
        minimum = int(payload["minimum_relationship_families"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls(
            families=families,
            relationship_families=relationship,
            variants=variants,
            required_deterministic_examples=examples,
            frozen_eligible_domains=domains,
            minimum_relationship_families=minimum,
            digest=digest,
        )


@dataclass(frozen=True)
class FrozenSuiteIdentity:
    suite_hash: str
    family_manifest_hash: str
    case_count: int
    family_count: int


@dataclass(frozen=True)
class SuiteValidation:
    identity: FrozenSuiteIdentity | None
    refusals: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.identity is not None and not self.refusals


def _refusal(code: str, detail: str) -> str:
    return f"{code}: {detail}"


def validate_business_query_suite(
    cases: dict[str, dict[str, Any]],
    oracle: dict[str, dict[str, Any]],
    scoring_specs: dict[str, CaseScoringSpec],
    manifest: FrozenFamilyManifest,
    *,
    run_kind: RunKind,
    cases_bytes: bytes | None = None,
) -> SuiteValidation:
    """Return a frozen suite identity or a closed refusal list. Never guesses."""
    refusals: list[str] = []
    families: dict[str, list[dict[str, Any]]] = {}
    ids = list(cases)
    if len(ids) != len(set(ids)):
        refusals.append(_refusal("duplicate_case_id", "case ids must be unique"))

    for case_id, case in cases.items():
        family = case.get("family")
        if not isinstance(family, str) or not family:
            refusals.append(_refusal("family_missing", case_id))
            continue
        families.setdefault(family, []).append(case)
        if run_kind == "gate" and family not in manifest.families:
            refusals.append(_refusal("family_substitution", f"{case_id} uses {family}"))
        phrasing = case.get("phrasing")
        if run_kind == "gate" and phrasing not in manifest.variants:
            refusals.append(_refusal("missing_variant", f"{case_id} phrasing {phrasing!r}"))
        if run_kind == "gate":
            if case.get("review_status") != "approved":
                refusals.append(_refusal("unresolved_review", case_id))
            if case.get("review_note"):
                refusals.append(_refusal("unresolved_review", f"{case_id} still has review_note"))
            domains = case.get("eligible_domains")
            if not domains:
                refusals.append(_refusal("eligibility_missing", case_id))
            elif set(domains) != set(manifest.frozen_eligible_domains):
                refusals.append(_refusal("eligibility_relabel", case_id))
        if case.get("draft_expected") == "answered":
            if case_id not in oracle:
                refusals.append(_refusal("missing_answered_oracle", case_id))
            if case_id not in scoring_specs:
                refusals.append(_refusal("missing_answered_mapping", case_id))

    present_families = tuple(families)
    if run_kind == "gate":
        missing_required = [name for name in manifest.families if name not in families]
        if missing_required:
            refusals.append(_refusal("missing_required_family", ",".join(missing_required)))
        relationship_present = [name for name in manifest.relationship_families if name in families]
        if len(relationship_present) < manifest.minimum_relationship_families:
            refusals.append(
                _refusal(
                    "relationship_slice",
                    (
                        f"need {manifest.minimum_relationship_families}, "
                        f"found {len(relationship_present)}"
                    ),
                )
            )
        missing_examples = [
            name for name in manifest.required_deterministic_examples if name not in families
        ]
        if missing_examples:
            refusals.append(_refusal("missing_required_example", ",".join(missing_examples)))

    for family, family_cases in families.items():
        variants = [case.get("phrasing") for case in family_cases]
        if len(variants) != len(set(variants)):
            refusals.append(_refusal("duplicate_family_member", family))
        if run_kind == "gate":
            if len(family_cases) != 4 or set(variants) != set(manifest.variants):
                refusals.append(_refusal("missing_variant", family))
            for case in family_cases:
                if case.get("phrasing") in {"natural_kiswahili", "code_switched"}:
                    if case.get("language_review_status") != "approved" or not case.get(
                        "back_translation"
                    ):
                        refusals.append(_refusal("language_review", case["id"]))

    if run_kind == "gate":
        if len(manifest.families) < MINIMUM_GATE_FAMILY_COUNT:
            refusals.append(
                _refusal(
                    "family_count_floor",
                    f"gate requires at least {MINIMUM_GATE_FAMILY_COUNT} manifest families, "
                    f"found {len(manifest.families)}",
                )
            )
        expected_case_count = len(manifest.families) * len(manifest.variants)
        if expected_case_count < MINIMUM_GATE_CASE_COUNT:
            refusals.append(
                _refusal(
                    "case_count_floor",
                    f"gate requires at least {MINIMUM_GATE_CASE_COUNT} family-variant pairs, "
                    f"found {expected_case_count}",
                )
            )
        expected_family_count = len(manifest.families)
        if len(cases) != expected_case_count:
            refusals.append(
                _refusal(
                    "case_count",
                    f"gate requires {expected_case_count} cases, found {len(cases)}",
                )
            )
        if len(families) != expected_family_count:
            refusals.append(
                _refusal(
                    "family_count",
                    f"gate requires {expected_family_count} families, found {len(families)}",
                )
            )

    if refusals:
        return SuiteValidation(identity=None, refusals=tuple(refusals))

    raw = (
        cases_bytes
        if cases_bytes is not None
        else json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    )
    identity = FrozenSuiteIdentity(
        suite_hash=hashlib.sha256(raw).hexdigest(),
        family_manifest_hash=manifest.digest,
        case_count=len(cases),
        family_count=len(present_families),
    )
    return SuiteValidation(identity=identity, refusals=())


def assert_run_kind_legal(
    run_kind: RunKind,
    *,
    subset: bool,
    emit_release_verdict: bool,
) -> None:
    if run_kind not in {"gate", "diagnostic", "protocol_smoke"}:
        raise RunKindError(f"unknown run kind {run_kind!r}")
    if run_kind == "gate" and subset:
        raise RunKindError("gate mode cannot accept case subsets")
    if run_kind != "gate" and emit_release_verdict:
        raise RunKindError(f"{run_kind} cannot emit a release verdict")


def call_ceiling(*, case_count: int, repeats: int, repair_budget: int = 0) -> int:
    if case_count < 1 or repeats < 1 or repair_budget < 0:
        raise ValueError("call ceiling inputs must be positive cases/repeats")
    return case_count * repeats * (1 + repair_budget)


def assert_call_ceiling_matches(
    requested: int, *, case_count: int, repeats: int, repair_budget: int = 0
) -> None:
    expected = call_ceiling(case_count=case_count, repeats=repeats, repair_budget=repair_budget)
    if requested != expected:
        raise ValueError(f"call ceiling {requested} does not match frozen budget {expected}")


@dataclass(frozen=True)
class EqualityKeySet:
    values: dict[str, str]

    def __post_init__(self) -> None:
        missing = [key for key in SHARED_EQUALITY_KEYS if key not in self.values]
        extra = [key for key in self.values if key not in SHARED_EQUALITY_KEYS]
        if missing or extra:
            raise ValueError(f"equality keys must be exact; missing={missing} extra={extra}")
        if any(not value for value in self.values.values()):
            raise ValueError("equality keys cannot be empty or null")


@dataclass(frozen=True)
class ArmIdentities:
    values: dict[str, str]
    arm_id: str
    executor_events_complete: bool
    legacy_bridged: bool = False

    def __post_init__(self) -> None:
        missing = [key for key in PER_ARM_IDENTITIES if key not in self.values]
        if missing:
            raise ValueError(f"per-arm identities missing {missing}")


class ComparisonStructurallyInvalid(ValueError):
    """Paired comparison cannot be scored."""


def assert_comparable_arms(
    control: ArmIdentities,
    candidate: ArmIdentities,
    control_keys: EqualityKeySet,
    candidate_keys: EqualityKeySet,
    *,
    preregistered_treatment: frozenset[str],
) -> None:
    if control.arm_id not in CAUSAL_ARMS or candidate.arm_id not in CAUSAL_ARMS:
        if control.arm_id in LEGACY_BENCHMARK_ARMS or candidate.arm_id in LEGACY_BENCHMARK_ARMS:
            if not (control.legacy_bridged and candidate.legacy_bridged):
                raise ComparisonStructurallyInvalid(
                    "legacy benchmark arms are release-ineligible without a real executor bridge"
                )
        else:
            raise ComparisonStructurallyInvalid(
                f"causal pair must be {CAUSAL_ARMS}, found {control.arm_id}/{candidate.arm_id}"
            )
    if not control.executor_events_complete or not candidate.executor_events_complete:
        raise ComparisonStructurallyInvalid(
            "baseline observation without a resolvable executor event is rejected"
        )
    if control_keys.values != candidate_keys.values:
        raise ComparisonStructurallyInvalid("shared equality keys drifted between arms")
    drifted = [
        key
        for key in PER_ARM_IDENTITIES
        if control.values[key] != candidate.values[key] and key not in preregistered_treatment
    ]
    if drifted:
        raise ComparisonStructurallyInvalid(
            f"undeclared per-arm identity drift: {','.join(drifted)}"
        )


def load_experiment_contract(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def causal_arm_ids(contract: dict[str, Any]) -> tuple[str, ...]:
    return tuple(contract["comparison"]["causal_arms"])


def legacy_benchmark_arm_ids(contract: dict[str, Any]) -> tuple[str, ...]:
    return tuple(contract["legacy_benchmark"]["arms"])


def resolve_run_kind(
    *,
    smoke: bool,
    confirm_spend: bool,
    run_kind_flag: Literal["gate", "diagnostic"] = "gate",
) -> RunKind:
    if smoke:
        return "protocol_smoke"
    if confirm_spend:
        return run_kind_flag
    return "diagnostic"


def assert_declared_hash(path: Path, declared: str) -> None:
    actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    if actual != declared:
        raise ValueError(f"drifted equality hash for {path.as_posix()}")


def assert_holdout_hashes_present(frozen_inputs: dict[str, Any]) -> None:
    if (
        frozen_inputs.get("holdout_suite_hash") is None
        or frozen_inputs.get("holdout_oracle_hash") is None
    ):
        raise ValueError("null holdout hash")


HOLDOUT_ARTIFACT_KEYS = frozenset(
    {"holdout_suite_path", "holdout_oracle_path", "holdout_family_path"}
)


def assert_declared_non_holdout_artifacts(contract: dict[str, Any], root: Path) -> None:
    frozen = contract["frozen_inputs"]
    missing: list[str] = []
    for key, value in frozen.items():
        if key in HOLDOUT_ARTIFACT_KEYS or not str(key).endswith("_path"):
            continue
        if isinstance(value, str) and not (root / value).exists():
            missing.append(value)
    if missing:
        raise FileNotFoundError(f"absent declared artifact: {','.join(missing)}")
