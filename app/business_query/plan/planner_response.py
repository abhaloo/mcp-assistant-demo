"""Planner response validation, shape normalization, and failure classifications."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.business_query.plan.query_plan import BusinessQueryPlan, plan_fingerprint

if TYPE_CHECKING:
    from app.business_query.outcomes import ClarificationRequired

PlannerFailureCode = Literal[
    "planner_empty_content",
    "planner_invalid_json",
    "planner_schema_invalid",
    "planner_capability_mismatch",
    "planner_timeout",
    "planner_provider_unavailable",
]
PlannerCheckSite = Literal[
    "response_shape",
    "response_parse",
    "response_validate",
    "structured_bind",
    "provider_call",
]


@dataclass(frozen=True)
class PlannerFailure:
    code: PlannerFailureCode
    check_site: PlannerCheckSite
    public_reason_code: Literal["adapter_invalid", "timeout"]


PLANNER_FAILURES: dict[PlannerFailureCode, PlannerFailure] = {
    "planner_empty_content": PlannerFailure(
        "planner_empty_content", "response_shape", "adapter_invalid"
    ),
    "planner_invalid_json": PlannerFailure(
        "planner_invalid_json", "response_parse", "adapter_invalid"
    ),
    "planner_schema_invalid": PlannerFailure(
        "planner_schema_invalid", "response_validate", "adapter_invalid"
    ),
    "planner_capability_mismatch": PlannerFailure(
        "planner_capability_mismatch", "structured_bind", "adapter_invalid"
    ),
    "planner_timeout": PlannerFailure("planner_timeout", "provider_call", "timeout"),
    "planner_provider_unavailable": PlannerFailure(
        "planner_provider_unavailable", "provider_call", "adapter_invalid"
    ),
}

_ACTION_PAYLOAD_FIELD = {
    "plan": "plan",
    "clarify": "clarification_question",
    "unsupported": "unsupported_reason",
}

_PLAN_BODY_FIELDS = frozenset(
    {
        "measures",
        "dimensions",
        "bucket_set",
        "filters",
        "having",
        "period",
        "compare_to",
        "grain",
        "order",
        "limit",
        "derived_sets",
    }
)


def dialogue_history_digest(
    dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None,
) -> str | None:
    """SHA-256 of role:text pairs joined by newline; None when dialogue is absent/empty."""
    if not dialogue:
        return None
    joined = "\n".join(f"{role}:{text}" for role, text in dialogue)
    return f"sha256:{hashlib.sha256(joined.encode()).hexdigest()}"


class PlannerClarificationChoice(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    label: str


class PlannerModelResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    action: Literal["plan", "clarify", "unsupported"]
    plan: BusinessQueryPlan | None = None
    companion_plans: list[BusinessQueryPlan] | None = Field(default=None, max_length=3)
    clarification_question: str | None = None
    clarification_choices: list[PlannerClarificationChoice] | None = None
    unsupported_reason: (
        Literal["member_not_found", "grain_unexpressible", "period_dimension_missing"] | None
    ) = None

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> PlannerModelResponse:
        payload_field = _ACTION_PAYLOAD_FIELD[self.action]
        if getattr(self, payload_field) is None:
            raise ValueError(f"action={self.action!r} requires {payload_field}")
        for field in _ACTION_PAYLOAD_FIELD.values():
            if field != payload_field and getattr(self, field) is not None:
                raise ValueError(f"action={self.action!r} must not set {field}")
        if self.action != "clarify" and self.clarification_choices:
            raise ValueError("clarification_choices is only valid when action='clarify'")
        if self.action != "plan" and self.companion_plans is not None:
            raise ValueError("companion_plans is only valid when action='plan'")
        if self.action == "plan" and self.plan is not None:
            for s in self.plan.derived_sets:
                if s.plan.derived_sets:
                    raise ValueError("derived sets cannot be nested")
                if s.plan.detail_selections:
                    raise ValueError("derived set inner plan cannot have detail_selections")
            if self.companion_plans:
                fingerprints = [plan_fingerprint(self.plan)] + [
                    plan_fingerprint(companion) for companion in self.companion_plans
                ]
                if len(fingerprints) != len(set(fingerprints)):
                    raise ValueError("companion_plans fingerprints must be unique in the set")
                for companion in self.companion_plans:
                    for s in companion.derived_sets:
                        if s.plan.derived_sets:
                            raise ValueError("derived sets cannot be nested")
                        if s.plan.detail_selections:
                            raise ValueError("derived set inner plan cannot have detail_selections")
        return self


def clarification_required_from_model(
    parsed: PlannerModelResponse, question: str
) -> ClarificationRequired:
    from app.business_query.outcomes import ClarificationRequired

    return ClarificationRequired(
        question=parsed.clarification_question or "",
        continuation=hashlib.sha256(question.encode()).hexdigest()[:16],
        choices=[
            {"id": choice.id, "label": choice.label}
            for choice in (parsed.clarification_choices or [])
        ],
    )


def _drop_empty_order_fields(data: dict) -> None:
    order_by = data.pop("order_by", None)
    if order_by:
        data.setdefault("order", order_by)
    if data.get("order") in (None, []):
        data.pop("order", None)
    if not data.get("sort"):
        data.pop("sort", None)


def _unwrap_grains(data: dict) -> None:
    grains = data.pop("grains", None)
    if grains is None or "grain" in data:
        return
    if isinstance(grains, list) and len(grains) == 1:
        data["grain"] = grains[0]


def _normalize_period_dict(period: dict) -> dict:
    p = {**period}
    if "time_dimension" not in p and "dimension" in p:
        p["time_dimension"] = p.pop("dimension")
    if "relative_range" in p and "relative" not in p:
        p["relative"] = p.pop("relative_range")
    range_val = p.pop("range", None)
    if range_val == "today":
        p["relative"] = "today"
    elif range_val == "between" and "between" not in p:
        start, end = p.pop("start", None), p.pop("end", None)
        if start is not None and end is not None:
            p["between"] = (start, end)
    relative = p.get("relative")
    if isinstance(relative, str):
        p["relative"] = relative.strip().replace(" ", "_")
    return p


def _apply_clarify_aliases(data: dict) -> None:
    if "clarify" in data:
        data.setdefault("action", "clarify")
        data["clarification_question"] = data.pop("clarify")
    if (
        data.get("action") == "clarify"
        and "question" in data
        and "clarification_question" not in data
    ):
        data["clarification_question"] = data.pop("question")


def _normalize_plan_dict(plan: dict) -> dict:
    normalized_plan = {**plan}
    _unwrap_grains(normalized_plan)
    _drop_empty_order_fields(normalized_plan)
    period = normalized_plan.get("period")
    if isinstance(period, dict):
        normalized_plan["period"] = _normalize_period_dict(period)
    compare_to = normalized_plan.get("compare_to")
    if isinstance(compare_to, dict):
        normalized_plan["compare_to"] = _normalize_period_dict(compare_to)

    derived = normalized_plan.get("derived_sets")
    if isinstance(derived, list):
        normalized_sets = []
        for item in derived:
            if isinstance(item, dict) and isinstance(item.get("plan"), dict):
                normalized_sets.append({**item, "plan": _normalize_plan_dict(item["plan"])})
            else:
                normalized_sets.append(item)
        normalized_plan["derived_sets"] = normalized_sets
    return normalized_plan


def _normalize_planner_payload(payload: dict) -> dict:
    """Collapse adjacent shapes the model writes under json_object mode."""
    data = {**payload}
    if "business_query_plan" in data:
        envelope = data.pop("business_query_plan")
        data.setdefault("plan", envelope)
    _apply_clarify_aliases(data)
    _unwrap_grains(data)
    _drop_empty_order_fields(data)
    if not isinstance(data.get("plan"), dict):
        lifted = {key: data.pop(key) for key in list(data) if key in _PLAN_BODY_FIELDS}
        if lifted:
            data.setdefault("action", "plan")
            data["plan"] = _normalize_plan_dict(lifted)
    plan = data.get("plan")
    if isinstance(plan, dict):
        data.setdefault("action", "plan")
        data["plan"] = _normalize_plan_dict(plan)
    if data.get("action") == "unsupported" and data.get("unsupported_reason") is None:
        data["unsupported_reason"] = "member_not_found"
    return data


def validate_model_payload(payload: dict) -> PlannerModelResponse:
    """Validate a model tool-call payload in JSON mode."""
    normalized = _normalize_planner_payload(payload)
    return PlannerModelResponse.model_validate_json(json.dumps(normalized))
