"""Dispatch a PlanPatch against a stored plan before the planner round."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.auth import Principal
from app.business_query.compile.pagination.continued_operation import (
    ContinuationRefused,
    execute_continued_operation,
)
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import (
    ClarificationRequired,
    Denied,
    Incomplete,
    Unsupported,
)
from app.business_query.plan.member_resolver import (
    Ambiguous,
    MemberMatch,
    NotFound,
    resolve_member_phrase,
)
from app.business_query.plan.plan_patch import AddPatchOp, PlanPatch, ReplacePatchOp
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.seal.evidence import BusinessQueryEvidenceContext
from app.business_query.wire.module_scoping import UNSUPPORTED_MESSAGE
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.round_resolution import PlanContinuation, RoundResolution

logger = logging.getLogger(__name__)

_DENY_MESSAGE = "business query tools are currently unavailable"
_MEMBER_LIST_KEYS = frozenset({"dimensions", "measures"})
_MEMBER_FIELD_KEYS = frozenset({"member", "time_dimension"})


def _pointer_segments(path: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]


def _is_member_slot(path: str) -> bool:
    segments = _pointer_segments(path)
    if not segments:
        return False
    leaf = segments[-1]
    if leaf in _MEMBER_FIELD_KEYS:
        return True
    if leaf == "-" or leaf.isdigit():
        parent = segments[-2] if len(segments) > 1 else ""
        return parent in _MEMBER_LIST_KEYS
    return False


def _known_member_names(bundle: DefinitionBundle) -> set[str]:
    names = {dimension.name for dimension in bundle.dimensions}
    names.update(measure.name for measure in bundle.measures)
    names.update(entry.name for entry in bundle.capabilities)
    return names


def resolve_patch_member_phrases(
    patch: PlanPatch,
    bundle: DefinitionBundle,
    principal: Principal,
) -> PlanPatch | ClarificationRequired | Unsupported:
    """Map person-facing phrases on member slots; leave filter values untouched."""
    known = _known_member_names(bundle)
    resolved_ops: list[Any] = []
    for op in patch.ops:
        if op.op == "remove" or not _is_member_slot(op.path):
            resolved_ops.append(op)
            continue
        value = getattr(op, "value", None)
        if not isinstance(value, str):
            resolved_ops.append(op)
            continue
        if value in known:
            resolved_ops.append(op)
            continue
        match = resolve_member_phrase(value, bundle, principal)
        if isinstance(match, Ambiguous):
            return ClarificationRequired(
                question="that name matches more than one member — which one did you mean?",
                continuation="member-ambiguous",
                choices=[
                    {
                        "id": candidate.member,
                        "label": candidate.member,
                        "rewrite": None,
                        "detail": None,
                    }
                    for candidate in match.candidates
                ],
            )
        if isinstance(match, NotFound):
            return Unsupported(reason_code="member_not_found", message=UNSUPPORTED_MESSAGE)
        if isinstance(match, MemberMatch):
            if isinstance(op, AddPatchOp):
                resolved_ops.append(AddPatchOp(path=op.path, value=match.member))
            elif isinstance(op, ReplacePatchOp):
                resolved_ops.append(ReplacePatchOp(path=op.path, value=match.member))
            else:
                resolved_ops.append(op)
            continue
        resolved_ops.append(op)
    return PlanPatch(subject=patch.subject, ops=tuple(resolved_ops), mentions=patch.mentions)


def _refusal_outcome(refused: ContinuationRefused) -> Denied | None:
    """A principal or scope mismatch is a denial; any other refusal falls through
    to planning with the reason logged."""
    if refused.reason == "principal_mismatch":
        return Denied(reason_code="policy_denied", message=_DENY_MESSAGE)
    if refused.reason == "scope_mismatch":
        return Denied(reason_code="cursor_scope_mismatch", message=_DENY_MESSAGE)
    logger.info("continued operation refused reason=%s", refused.reason)
    return None


async def continue_with_patch(
    module: Any,
    request: BusinessQueryRequest,
    *,
    evidence: BusinessQueryEvidenceContext | None = None,
) -> RoundResolution | ClarificationRequired | Denied | Incomplete | Unsupported | None:
    """Run the stored plan with a patch. ``None`` means fall through to planning."""
    patch = request.patch
    if patch is None:
        return None
    bundle_outcome = module.load_bundle(request.principal, request.correlation_id)
    if isinstance(bundle_outcome, (Denied, Incomplete)):
        return bundle_outcome
    bundle = bundle_outcome
    resolved = resolve_patch_member_phrases(patch, bundle, request.principal)
    if not isinstance(resolved, PlanPatch):
        return resolved
    if module._plan_store is None:
        logger.info("continued operation refused reason=%s", "subject_unknown")
        return None
    continued = await execute_continued_operation(
        subject=resolved.subject,
        patch=resolved,
        principal=request.principal,
        bundle=bundle,
        plan_store=module._plan_store,
        scope_fn=module._scope_fn,
        now=datetime.now(tz=UTC),
        business_date=request.business_date,
        project_id=evidence.project_id if evidence is not None else "default",
    )
    if isinstance(continued, ContinuationRefused):
        return _refusal_outcome(continued)
    scoped, stored, changes = continued
    return RoundResolution(
        request=request.model_copy(update={"patch": None}),
        planned_set=PlannedQuerySet(primary=scoped.plan, companions=()),
        bundle=bundle,
        continuation=PlanContinuation(
            subject=resolved.subject,
            stored=stored,
            changes=changes,
        ),
    )
