"""Control-plane SQL estimand/candidate guards for DeepSeek-direct and OpenRouter.

Moved out of `app.providers.deepseek_direct_controls` and
`app.providers.openrouter_controls`: these functions read
`app.experiments.live_campaign_contract` / `app.experiments.arm_profiles` --
control-plane, eval-campaign concepts (the frozen live-contract decision and
the arm-profile registry) -- to decide whether a live SQL campaign/canary may
spend against a given provider+profile. Their only callers are
`app.experiments.core.preflight` and `scripts/experiments/deepseek_sql_eval.py`,
both control-plane; `app/providers/` now keeps ONLY wire models and
production routing controls (no per-request answer path ever calls these).

The wire-level status constants and error classes (`DEEPSEEK_DIRECT_SQL_
ESTIMAND_*`, `DeepSeekDirectSqlEstimandError`, `OPENROUTER_SQL_ESTIMAND_*`,
`OpenRouterSqlEstimandError`) stay defined in `app.providers.*_controls` --
they describe each provider's own wire-level estimand vocabulary, not
eval-campaign orchestration -- and are re-imported (and re-exported) here so
callers of these guard functions get everything from one module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.experiments.arm_profiles import load_arm_profiles
from app.experiments.live_campaign_contract import load_live_contract
from app.providers.deepseek_direct_controls import (
    DEEPSEEK_DIRECT_SQL_ESTIMAND_SUPPORTED,
    DEEPSEEK_DIRECT_SQL_ESTIMAND_UNSUPPORTED,
    DeepSeekDirectSqlEstimandError,
)
from app.providers.openrouter_controls import (
    OPENROUTER_SQL_ESTIMAND_SUPPORTED,
    OPENROUTER_SQL_ESTIMAND_UNSUPPORTED,
    OpenRouterSqlEstimandError,
)

__all__ = [
    "DeepSeekDirectSqlEstimandError",
    "OpenRouterSqlEstimandError",
    "SqlEstimandGuard",
    "assert_deepseek_direct_sql_candidate_allowed",
    "assert_deepseek_direct_sql_estimand_decided",
    "assert_openrouter_sql_candidate_allowed",
    "assert_openrouter_sql_estimand_decided",
    "assert_sql_candidate_allowed",
    "load_deepseek_direct_sql_estimand_decision",
    "load_openrouter_sql_estimand_decision",
]


@dataclass(frozen=True)
class SqlEstimandGuard:
    provider: str
    decision_key: str
    error_cls: type[Any]
    supported_status: str
    unsupported_status: str
    extra_decided_check: Callable[[dict[str, Any]], None] | None = None
    refusal_suffix: str = "live SQL refused"

    def load_decision(self) -> dict[str, Any]:
        contract = load_live_contract()
        decision = contract.get("locked_decisions", {}).get(self.decision_key)
        if not isinstance(decision, dict):
            raise self.error_cls(
                purpose=self.decision_key,
                credential_source=self.provider,
                environment="eval",
                model="",
                detail=f"{self.decision_key} must be an object",
            )
        return decision

    def assert_decided(
        self,
        *,
        load_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        decision = load_fn() if load_fn is not None else self.load_decision()
        status = str(decision.get("status") or "")
        if status not in (self.supported_status, self.unsupported_status):
            raise self.error_cls(
                purpose=self.decision_key,
                credential_source=self.provider,
                environment="eval",
                model="",
                detail=(
                    f"{self.decision_key} unresolved "
                    f"(status={status!r}; expected SUPPORTED or UNSUPPORTED_FOR_SQL)"
                ),
            )
        if status == self.supported_status:
            route_hash = decision.get("route_identity_hash")
            if not isinstance(route_hash, str) or not route_hash.startswith("sha256:"):
                raise self.error_cls(
                    purpose=self.decision_key,
                    credential_source=self.provider,
                    environment="eval",
                    model="",
                    detail=f"supported {self.decision_key} requires route_identity_hash",
                )
            if self.extra_decided_check is not None:
                self.extra_decided_check(decision)
        return decision

    def assert_candidate_allowed(
        self,
        *,
        candidate_profile_id: str,
        require_supported: bool = False,
        load_fn: Callable[[], dict[str, Any]] | None = None,
        decided_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        if self.provider == "openrouter":
            decision = (
                decided_fn() if decided_fn is not None else self.assert_decided(load_fn=load_fn)
            )
            if decision["status"] != self.unsupported_status:
                return
            profile = load_arm_profiles()[candidate_profile_id]
            if profile.provider != self.provider:
                return
            raise self.error_cls(
                purpose=self.decision_key,
                credential_source=self.provider,
                environment="eval",
                model=profile.requested_model,
                detail=(
                    f"OpenRouter SQL estimand UNSUPPORTED_FOR_SQL for profile "
                    f"{candidate_profile_id!r} (arm={profile.arm_name!r}); {self.refusal_suffix}"
                ),
            )

        profile = load_arm_profiles()[candidate_profile_id]
        if profile.provider != self.provider:
            return

        decision = load_fn() if load_fn is not None else self.load_decision()
        status = str(decision.get("status") or "")
        if status == self.unsupported_status:
            raise self.error_cls(
                purpose=self.decision_key,
                credential_source=self.provider,
                environment="eval",
                model=profile.requested_model,
                detail=(
                    f"DeepSeek-direct SQL estimand UNSUPPORTED_FOR_SQL for profile "
                    f"{candidate_profile_id!r} (arm={profile.arm_name!r}); {self.refusal_suffix}"
                ),
            )
        if require_supported and status != self.supported_status:
            raise self.error_cls(
                purpose=self.decision_key,
                credential_source=self.provider,
                environment="eval",
                model=profile.requested_model,
                detail=(
                    f"DeepSeek-direct SQL estimand not SUPPORTED for profile "
                    f"{candidate_profile_id!r} (status={status!r}); {self.refusal_suffix}"
                ),
            )


def _check_openrouter_extra(decision: dict[str, Any]) -> None:
    if decision.get("require_parameters") is not True:
        raise OpenRouterSqlEstimandError(
            purpose="openrouter_sql_estimand",
            credential_source="openrouter",
            environment="eval",
            model="",
            detail="supported openrouter_sql_estimand requires require_parameters=True",
        )
    if decision.get("allow_fallbacks") is not False:
        raise OpenRouterSqlEstimandError(
            purpose="openrouter_sql_estimand",
            credential_source="openrouter",
            environment="eval",
            model="",
            detail="supported openrouter_sql_estimand requires allow_fallbacks=False",
        )


_GUARDS: dict[str, SqlEstimandGuard] = {
    "deepseek_direct": SqlEstimandGuard(
        provider="deepseek_direct",
        decision_key="deepseek_direct_sql_estimand",
        error_cls=DeepSeekDirectSqlEstimandError,
        supported_status=DEEPSEEK_DIRECT_SQL_ESTIMAND_SUPPORTED,
        unsupported_status=DEEPSEEK_DIRECT_SQL_ESTIMAND_UNSUPPORTED,
        refusal_suffix="live SQL refused",
    ),
    "openrouter": SqlEstimandGuard(
        provider="openrouter",
        decision_key="openrouter_sql_estimand",
        error_cls=OpenRouterSqlEstimandError,
        supported_status=OPENROUTER_SQL_ESTIMAND_SUPPORTED,
        unsupported_status=OPENROUTER_SQL_ESTIMAND_UNSUPPORTED,
        extra_decided_check=_check_openrouter_extra,
        refusal_suffix="live SQL matrix/canary refused",
    ),
}


def assert_sql_candidate_allowed(
    provider: str,
    *,
    candidate_profile_id: str,
    require_supported: bool = False,
) -> None:
    """Assert candidate profile is allowed for live SQL spend on the given provider."""
    guard = _GUARDS.get(provider)
    if guard is not None:
        guard.assert_candidate_allowed(
            candidate_profile_id=candidate_profile_id,
            require_supported=require_supported,
        )


def load_deepseek_direct_sql_estimand_decision() -> dict[str, Any]:
    """Return the locked DeepSeek-direct SQL estimand decision from the live contract."""
    return _GUARDS["deepseek_direct"].load_decision()


def assert_deepseek_direct_sql_estimand_decided() -> dict[str, Any]:
    """Require a resolved SUPPORTED or UNSUPPORTED_FOR_SQL contract decision."""
    return _GUARDS["deepseek_direct"].assert_decided(
        load_fn=load_deepseek_direct_sql_estimand_decision
    )


def assert_deepseek_direct_sql_candidate_allowed(
    *,
    candidate_profile_id: str,
    require_supported: bool = False,
) -> None:
    """Refuse DeepSeek-direct SQL profiles when the contract blocks live spend."""
    _GUARDS["deepseek_direct"].assert_candidate_allowed(
        candidate_profile_id=candidate_profile_id,
        require_supported=require_supported,
        load_fn=load_deepseek_direct_sql_estimand_decision,
    )


def load_openrouter_sql_estimand_decision() -> dict[str, Any]:
    """Return the locked OpenRouter SQL estimand decision from the live contract."""
    return _GUARDS["openrouter"].load_decision()


def assert_openrouter_sql_estimand_decided() -> dict[str, Any]:
    """Require a resolved SUPPORTED or UNSUPPORTED_FOR_SQL contract decision."""
    return _GUARDS["openrouter"].assert_decided(load_fn=load_openrouter_sql_estimand_decision)


def assert_openrouter_sql_candidate_allowed(
    *,
    candidate_profile_id: str,
    require_supported: bool = False,
) -> None:
    """Refuse OpenRouter SQL candidate profiles when the contract marks them unsupported."""
    _GUARDS["openrouter"].assert_candidate_allowed(
        candidate_profile_id=candidate_profile_id,
        require_supported=require_supported,
        decided_fn=assert_openrouter_sql_estimand_decided,
    )
