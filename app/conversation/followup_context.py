from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.conversation.evidence.contracts import RestoredTurn, RestoreReference, RestoreRequest
from app.conversation.evidence.ports import EvidenceRestoreService
from app.conversation.followup_contracts import FollowupFocus, SourceCandidate, SourceSelection
from app.conversation.transcript_models import TranscriptTurn

MAX_SOURCE_CANDIDATES = 8
_MAX_QUESTION_CHARS = 160


def _grain(turn: TranscriptTurn) -> str:
    if turn.components:
        tools = {c.tool for c in turn.components if c.status == "succeeded"}
        if "business_query" in tools and "document_search" in tools:
            return "mixed"
    if turn.bq_digest is not None:
        if any(c.tool == "document_search" and c.status == "succeeded" for c in turn.components):
            return "mixed"
        return turn.bq_digest.grain
    return "documents"


def source_candidates(history: Sequence[TranscriptTurn]) -> tuple[SourceCandidate, ...]:
    questions = {t.exchange_id: t.content for t in history if t.role == "user" and t.exchange_id}
    sources = [
        t
        for t in history
        if t.role == "assistant"
        and t.exchange_id in questions
        and t.restore_ref
        and t.answer_mode is None
        and t.tool_result_version == 1
        and t.completeness != "none"
    ]
    window = sources[-MAX_SOURCE_CANDIDATES:]
    return tuple(
        SourceCandidate(
            position=i,
            exchange_id=t.exchange_id,  # type: ignore[arg-type]
            restore_ref=t.restore_ref,  # type: ignore[arg-type]
            grain=_grain(t),  # type: ignore[arg-type]
            user_question=questions[t.exchange_id][:_MAX_QUESTION_CHARS],
        )
        for i, t in enumerate(window, start=1)
    )


def source_focus(
    history: Sequence[TranscriptTurn],
    candidates: Sequence[SourceCandidate],
) -> FollowupFocus:
    questions = {t.exchange_id: t.content for t in history if t.role == "user" and t.exchange_id}
    candidate_by_exchange = {c.exchange_id: c for c in candidates}
    assistant_by_exchange = {
        t.exchange_id: t for t in history if t.role == "assistant" and t.exchange_id
    }

    for t in reversed(history):
        if t.role != "assistant":
            continue

        if t.answer_mode == "explanation":
            if not t.source_exchange_ids:
                return FollowupFocus(status="unavailable")
            positions: list[int] = []
            seen: set[int] = set()

            def _resolve_eids(eids: Sequence[str], visited: set[str]) -> bool:
                for eid in eids:
                    if eid in visited:
                        return False
                    cand = candidate_by_exchange.get(eid)
                    if cand is not None:
                        if cand.position not in seen:
                            seen.add(cand.position)
                            positions.append(cand.position)
                        continue
                    prev = assistant_by_exchange.get(eid)
                    if (
                        prev is not None
                        and prev.answer_mode == "explanation"
                        and prev.source_exchange_ids
                    ):
                        if not _resolve_eids(prev.source_exchange_ids, visited | {eid}):
                            return False
                    else:
                        return False
                return True

            if not _resolve_eids(t.source_exchange_ids, set()):
                return FollowupFocus(status="unavailable")
            return FollowupFocus(status="resolved", source_positions=tuple(positions))

        if t.answer_mode == "direct":
            subject = t.conversation_subject or (
                questions.get(t.exchange_id) if t.exchange_id else None
            )
            if subject:
                return FollowupFocus(
                    status="direct", subject_question=subject[:_MAX_QUESTION_CHARS]
                )
            return FollowupFocus(status="unavailable")

        if t.answer_mode is None and t.restore_ref and t.completeness != "none":
            cand = candidate_by_exchange.get(t.exchange_id) if t.exchange_id else None
            if cand is not None:
                return FollowupFocus(status="resolved", source_positions=(cand.position,))
            return FollowupFocus(status="unavailable")

    return FollowupFocus(status="none")


class SelectedSources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exchange_ids: tuple[str, ...]
    restore_refs: tuple[str, ...]
    turns: tuple[RestoredTurn, ...]


async def restore_selected(
    selection: SourceSelection,
    candidates: Sequence[SourceCandidate],
    *,
    thread_id: str,
    principal: Principal,
    restore: EvidenceRestoreService,
) -> SelectedSources | None:
    by_position = {c.position: c for c in candidates}
    if any(position not in by_position for position in selection.source_positions):
        return None
    chosen = [by_position[position] for position in selection.source_positions]
    if not chosen:
        return None
    request = RestoreRequest(
        thread_id=thread_id,
        references=tuple(RestoreReference(restore_ref=c.restore_ref) for c in chosen),
    )
    response = await restore.restore(request, principal)
    by_ref = {r.restore_ref: r for r in response.results}
    turns: list[RestoredTurn] = []
    for c in chosen:
        verdict = by_ref.get(c.restore_ref)
        if verdict is None or verdict.status != "authorized" or verdict.payload is None:
            return None
        turns.append(verdict.payload)
    return SelectedSources(
        exchange_ids=tuple(c.exchange_id for c in chosen),
        restore_refs=tuple(c.restore_ref for c in chosen),
        turns=tuple(turns),
    )
