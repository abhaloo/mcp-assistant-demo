"""Restore evidence endpoint for reauthorizing historical turn content."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import field_validator

from app.auth.jwt import Principal, verify_principal_restore
from app.conversation.evidence.contracts import (
    RestoredEvidence,
    RestoreRequest,
    RestoreResponse,
)
from app.services.tool_composition import is_tool_layer_enabled

router = APIRouter(tags=["Evidence Restore"])


class RestoreRequestPayload(RestoreRequest):
    """Ingress request model with tuple coercion from incoming JSON arrays."""

    @field_validator("references", mode="before")
    @classmethod
    def _coerce_references_tuple(cls, v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v


def _withheld(body: RestoreRequest) -> RestoreResponse:
    """Every reference reads unavailable while the tool layer is off for this caller."""
    return RestoreResponse(
        version=1,
        results=tuple(RestoredEvidence.unavailable(ref.restore_ref) for ref in body.references),
    )


@router.post("/restore-evidence", response_model=RestoreResponse)
async def restore_evidence(
    request: Request,
    body: RestoreRequestPayload,
    principal: Principal = Depends(verify_principal_restore),
) -> RestoreResponse:
    """Authorize and restore retained evidence snapshots without invoking planner/LLM."""
    service = request.app.state.evidence_restore_service
    if service is None or not is_tool_layer_enabled(principal):
        return _withheld(body)
    return await service.restore(body, principal)
