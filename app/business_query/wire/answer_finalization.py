"""Finalize wire answer text and enforce character bounds (ADR 0073)."""

from __future__ import annotations

from app.business_query.outcomes import Answered, PlanRefused
from app.business_query.plan import BusinessQueryPlan
from app.business_query.wire.presenter import format_set_context
from app.business_query.wire.result_presentation import present_result


def assert_set_context_fits(plan: BusinessQueryPlan, max_answer_chars: int) -> None:
    """Refuse execution if mandatory set context exceeds the character cap."""
    context = format_set_context(plan)
    if len(context) > max_answer_chars:
        raise PlanRefused("grain_unexpressible", check_site="set_context_budget")


def _truncate_at_word_boundary(text: str, limit: int) -> str:
    """Truncate text at the last word boundary before the limit."""
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    truncated = text[:limit]
    last_space = max(truncated.rfind(" "), truncated.rfind("\n"), truncated.rfind("\t"))
    if last_space > 0:
        return truncated[:last_space].rstrip()
    return truncated


def finalize_answer_text(
    plan: BusinessQueryPlan,
    answered: Answered,
    *,
    text: str,
    max_answer_chars: int,
) -> Answered:
    """Attach set context, bound answer body at word boundary, and present result."""
    context = format_set_context(plan)
    if context:
        if text.startswith(context + "\n\n"):
            body = text[len(context) + 2 :]
        elif text == context:
            body = ""
        else:
            body = text

        available = max_answer_chars - (len(context) + 2)
        if available <= 0:
            bounded_body = ""
        elif len(body) <= available:
            bounded_body = body
        else:
            bounded_body = _truncate_at_word_boundary(body, available)

        if bounded_body:
            final_text = f"{context}\n\n{bounded_body}"
        else:
            final_text = context
    else:
        if len(text) <= max_answer_chars:
            final_text = text
        else:
            final_text = _truncate_at_word_boundary(text, max_answer_chars)

    return present_result(
        answered.model_copy(update={"answer_text": final_text}), scope=context or None
    )
