"""One-shot typed repair for planner protocol and semantic failures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business_query.outcomes import Incomplete
from app.business_query.plan.attempts import (
    PlannerRawShapeClass,
    PlannerTerminalClass,
    PlannerTerminalCode,
    PlannerValidationResult,
)
from app.business_query.wire.trace import QueryTrace, mask_planner_payload

PLANNER_REPAIR_MAX = 1
_SEMANTIC_REPAIR_MAX = 1
# A derived-set plan carries two independently-repairable defect sites (the
# outer plan and the inner plan), so one repair round is not enough budget to
# fix both across separate attempts. This bonus round applies only when the
# failing round's own raw payload declares derived_sets -- ordinary plans keep
# PLANNER_REPAIR_MAX unchanged.
DERIVED_SET_REPAIR_BONUS = 1

_REPAIRABLE_CODES = frozenset({"planner_invalid_json", "planner_schema_invalid"})
_SEMANTIC_REPAIRABLE = frozenset({"planner_shape_mismatch"})
_SEMANTIC_REPAIR_HINT = (
    "This question asks for a named entity. Reply with a grouped plan: the entity dimension, "
    "the measure, order by the measure descending, limit 1."
)


def _plan_declares_derived_sets(raw: object) -> bool:
    """True when the round's own (possibly otherwise-invalid) raw payload
    carries a non-empty ``plan.derived_sets`` list."""
    if not isinstance(raw, dict):
        return False
    plan = raw.get("plan")
    if not isinstance(plan, dict):
        return False
    derived_sets = plan.get("derived_sets")
    return isinstance(derived_sets, list) and len(derived_sets) > 0


def _capture_first_planner_payload(writer: QueryTrace | None, raw: object) -> None:
    if (
        writer is None
        or not writer.capture_planner_payload
        or writer.planner_raw_payload is not None
    ):
        return
    if isinstance(raw, dict):
        writer.planner_raw_payload = json.dumps(mask_planner_payload(raw))[:8192]
        return
    writer.planner_raw_payload = f"<non-dict:{type(raw).__name__} len={len(str(raw))}>"


class _PlannerProtocolFailure(Exception):
    def __init__(
        self,
        code: str,
        *,
        response_shape: PlannerRawShapeClass | None = None,
        validation: PlannerValidationResult | None = None,
        raw: object = None,
    ) -> None:
        self.code = code
        self.response_shape = response_shape
        self.validation = validation
        self.raw = raw
        super().__init__(code)


class PlannerRepairMixin:
    """Ledger rotation and the repair-or-terminal branch for protocol failures."""

    async def _apply_protocol_repair(
        self,
        attempt_id: str | None,
        exc: _PlannerProtocolFailure,
        *,
        repair_round: int,
        round_started: float,
        started: float,
        writer: QueryTrace | None,
        correlation_digest: str,
        raw: object,
    ) -> tuple[str | None, str] | Incomplete:
        if exc.code in _REPAIRABLE_CODES:
            _capture_first_planner_payload(writer, raw)
        repair_max = PLANNER_REPAIR_MAX + (
            DERIVED_SET_REPAIR_BONUS if _plan_declares_derived_sets(raw) else 0
        )
        repairable = exc.code in _REPAIRABLE_CODES and repair_round < repair_max
        if repairable:
            new_id = await self._rotate_attempt_for_repair(
                attempt_id,
                shape=exc.response_shape or PlannerRawShapeClass.OTHER,
                validation=exc.validation or PlannerValidationResult.SCHEMA_INVALID,
                code=exc.code,
                round_started=round_started,
                terminal_class=PlannerTerminalClass.PLANNER_PROTOCOL,
            )
            if writer is not None:
                writer.planner_repair_count += 1
            return new_id, str(exc.__cause__ or exc)[:2000]
        self._mark_planner_latency(started, writer)
        if exc.response_shape is not None and exc.validation is not None:
            await self._commit_attempt_response(
                attempt_id,
                shape=exc.response_shape,
                validation=exc.validation,
            )
        return await self._failure_outcome(
            exc.code,
            type_name=type(exc.__cause__ or exc).__name__,
            correlation_digest=correlation_digest,
            attempt_id=attempt_id,
            started=round_started,
            writer=writer,
        )

    async def _apply_semantic_repair(
        self,
        attempt_id: str | None,
        *,
        shape: PlannerRawShapeClass,
        round_started: float,
        writer: QueryTrace | None,
    ) -> tuple[str | None, str]:
        new_id = await self._rotate_attempt_for_repair(
            attempt_id,
            shape=shape,
            validation=PlannerValidationResult.VALID_PLAN,
            code=PlannerTerminalCode.PLANNER_SHAPE_MISMATCH.value,
            round_started=round_started,
            terminal_class=PlannerTerminalClass.PLANNER_SEMANTIC,
        )
        if writer is not None:
            writer.planner_repair_count += 1
        return new_id, _SEMANTIC_REPAIR_HINT

    async def _rotate_attempt_for_repair(
        self,
        attempt_id: str | None,
        *,
        shape: PlannerRawShapeClass,
        validation: PlannerValidationResult,
        code: str,
        round_started: float,
        terminal_class: PlannerTerminalClass,
    ) -> str | None:
        if attempt_id is None or self._attempt_context is None:
            self._repair_hint_code = code
            return None
        closed_id = attempt_id
        await self._commit_attempt_response(closed_id, shape=shape, validation=validation)
        await self._finish_attempt(
            closed_id,
            terminal_class=terminal_class,
            terminal_code=PlannerTerminalCode(code),
            started=round_started,
        )
        self._repair_hint_code = code  # AFTER the first row's response commit
        attempt_id = None  # rotation window: cancel finishes nothing stale
        next_index = self._attempt_context.planner_call_index + 1
        self._attempt_context = self._attempt_context.model_copy(
            update={
                "attempt_id": f"{self._attempt_context.attempt_id}-r{next_index}",
                "planner_call_index": next_index,
                "idempotency_key": (
                    f"{self._attempt_context.run_epoch}:{self._attempt_context.case_id}"
                    f":{self._attempt_context.repeat_index}:{next_index}"
                ),
            }
        )
        self._attempt_consumed = False
        return await self._start_attempt()
