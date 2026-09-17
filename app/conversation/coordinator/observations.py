"""Observation projection and safe business/document presentation for coordinator turns (R5)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.conversation.coordinator.action_lifecycle import ActionOutcome
from app.conversation.coordinator.contracts import Observation, ValueRef
from app.conversation.followup_context import SelectedSources
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult

MAX_OBSERVATION_ROWS = 20

_SQL_CLAUSE_RE = re.compile(
    r"(?i)\bselect\b.*?\bfrom\b\s+[a-zA-Z0-9_.]+(?:\s+where\b.*?)?|"
    r"\bselect\b.*|"
    r"\bwhere:\s*select\b.*|"
    r"\bbilling_[a-zA-Z0-9_]+\b"
)


def _scrub_sql(text: str) -> str:
    return _SQL_CLAUSE_RE.sub("[redacted]", text).strip()


def project_observations(outcomes: Sequence[ActionOutcome]) -> tuple[Observation, ...]:
    """Project completed action outcomes into business-safe Observation objects.

    Preserves completion order exactly. Strips raw SQL and internal identifiers,
    maps formatted values into ValueRef slots, and caps display rows by MAX_OBSERVATION_ROWS.
    """
    observations: list[Observation] = []

    for outcome in outcomes:
        if outcome.status == "succeeded":
            obs = _project_succeeded_outcome(outcome)
        else:
            obs = _project_failed_outcome(outcome)
        observations.append(obs)

    return tuple(observations)


def _project_succeeded_outcome(outcome: ActionOutcome) -> Observation:
    res: Any = outcome.result

    if outcome.kind == "query_business":
        if isinstance(res, CommittedBqResult):
            res = res.result

        envelopes = []
        if isinstance(res, AskBusinessQueryResult) and res.business_query:
            if res.business_query.envelopes:
                envelopes = list(res.business_query.envelopes)
            elif res.business_query.envelope:
                envelopes = [res.business_query.envelope]

        values_list: list[ValueRef] = []
        val_idx = 1
        truncated = False
        summary_parts: list[str] = []

        for env in envelopes:
            rows = env.rows or []
            if len(rows) > MAX_OBSERVATION_ROWS:
                truncated = True
                rows = rows[:MAX_OBSERVATION_ROWS]

            for row in rows:
                for col in env.columns or ():
                    if len(values_list) < 16:
                        val = row.get(col.key)
                        if val is not None:
                            values_list.append(
                                ValueRef(
                                    id=f"v{val_idx}",
                                    label=col.key,
                                    formatted=str(val),
                                )
                            )
                            val_idx += 1

            parts: list[str] = []
            if env.presentation and env.presentation.title:
                parts.append(_scrub_sql(env.presentation.title))
            if env.presentation and env.presentation.applied_filters:
                clean_filters = [_scrub_sql(str(f)) for f in env.presentation.applied_filters]
                parts.append(f"Filters: {', '.join(clean_filters)}")
            if env.presentation and env.presentation.scope:
                parts.append(f"Scope: {_scrub_sql(env.presentation.scope)}")
            parts.append(f"completeness: {env.result_completeness}")
            summary_parts.append("; ".join(parts))

        # The answer the person will read comes first: it is what the model
        # writes over. Envelope metadata follows it.
        answer_text = res.answer_text if isinstance(res, AskBusinessQueryResult) else ""
        if answer_text:
            summary_parts.insert(0, _scrub_sql(answer_text))
        summary = "\n".join(summary_parts) if summary_parts else "Business query completed."
        return Observation(
            action_id=outcome.action_id,
            kind=outcome.kind,
            status=outcome.status,
            evidence_ids=(outcome.action_id,),
            values=tuple(values_list),
            summary=summary[:2000],
            truncated=truncated,
        )

    if outcome.kind == "search_documents":
        evidence_ids: tuple[str, ...] = ()
        passages_summary: list[str] = []
        truncated = False

        if isinstance(res, DocumentSearchResult):
            evidence_ids = tuple(p.id for p in res.passages)
            truncated = res.truncated
            for p in res.passages:
                passages_summary.append(f"[{p.id}] ({p.source_file}): {p.content}")

        summary = "\n".join(passages_summary) if passages_summary else "No documents matched."
        return Observation(
            action_id=outcome.action_id,
            kind=outcome.kind,
            status=outcome.status,
            evidence_ids=evidence_ids,
            summary=summary[:2000],
            truncated=truncated,
        )

    if outcome.kind == "explain_sources":
        evidence_ids = ()
        turns_summary: list[str] = []

        if isinstance(res, SelectedSources):
            evidence_ids = res.exchange_ids
            for t in res.turns:
                title = (
                    _scrub_sql(t.presentation.title)
                    if (t.presentation and t.presentation.title)
                    else ""
                )
                turns_summary.append(f"Restored [{t.exchange_id}] {title}: {t.answer_text}")

        summary = "\n".join(turns_summary) if turns_summary else "No sources restored."
        return Observation(
            action_id=outcome.action_id,
            kind=outcome.kind,
            status=outcome.status,
            evidence_ids=evidence_ids,
            summary=summary[:2000],
            truncated=False,
        )

    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        summary="",
    )


def _project_failed_outcome(outcome: ActionOutcome) -> Observation:
    res = outcome.result
    code = outcome.status

    if isinstance(res, AskBusinessQueryResult):
        # The person-facing text says why the query gave nothing; the model
        # reads the same words and never a row or a statement.
        code = res.answer_text or res.sql_stop_reason or code
    elif isinstance(res, DocumentFailure) and res.code:
        code = res.code
    elif isinstance(res, Exception):
        code = type(res).__name__

    summary = f"Action {outcome.action_id} {outcome.status}: {code}"
    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        summary=summary,
        truncated=False,
    )
