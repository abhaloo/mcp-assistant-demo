"""Builds structured answer heads for table results."""

from __future__ import annotations

from app.business_query.definitions.schema import DefinitionBundle
from app.business_query.outcomes import RowIdentity
from app.business_query.plan.plan_diff import PlanDigest
from app.business_query.plan.query_plan import BusinessQueryPlan
from app.business_query.wire.presenter import format_set_context
from app.business_query.wire.row_identity_text import change_note_line, row_identity_sentence

_HEAD_MAX_CHARS = 1600


def answer_head(  # noqa: PLR0913
    *,
    plan: BusinessQueryPlan | None = None,
    row_identity: RowIdentity | None = None,
    shown: int,
    total: int,
    changes: tuple[str, ...] = (),
    before: PlanDigest | None = None,
    after: PlanDigest | None = None,
    bundle: DefinitionBundle | None = None,
) -> str:
    """Format structured answer head: set context, row identity, changes, and showing count.

    Total length is at most 1600 characters, truncating set context last with ellipsis.
    """
    lines: list[str] = []
    if row_identity is not None:
        sentence = row_identity_sentence(row_identity)
        if sentence:
            lines.append(sentence)
    if changes and before is not None and after is not None:
        note = change_note_line(changes, before, after, bundle=bundle)
        if note:
            lines.append(note)
    lines.append(f"Showing {shown} of {total}")
    body = "\n".join(lines)

    set_context = format_set_context(plan) if plan is not None else ""
    if set_context:
        available = _HEAD_MAX_CHARS - len(body) - 2
        if len(set_context) > available:
            set_context = set_context[: available - 1] + "…" if available >= 1 else ""
        if set_context:
            return f"{set_context}\n\n{body}"
    if len(body) > _HEAD_MAX_CHARS:
        return body[: _HEAD_MAX_CHARS - 1] + "…"
    return body
