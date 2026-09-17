"""Ask-only rich presentation over a sealed Business Query ``Answered``.

ADR 0053 Decision 6: the deterministic module presenter (``present_for_plan``)
stays the eval-side text; Ask additionally tries the LLM rich renderer
(``render_rich``) under its own budget, verified by the numeric-preservation
guard, falling back to the deterministic text on timeout, exception, or a
guard trip. The outcome always stays ``answered`` -- never ``Incomplete``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler
from opentelemetry.trace import StatusCode

from app.business_query.outcomes import Answered, BusinessQueryOutcome
from app.business_query.wire.answer_finalization import finalize_answer_text
from app.business_query.wire.comparison_presenter import present_for_plan
from app.business_query.wire.presenter import render_rich, rendered_values_preserved
from app.providers.factory import get_chat_model
from app.providers.model_purpose import ModelPurpose
from app.telemetry.invocation_ledger import extract_token_usage
from app.telemetry.metrics import record_render_guard_trip
from app.telemetry.spans import presenter_stage_span

logger = logging.getLogger(__name__)

# The rich renderer runs only in Ask, and it waits longer than the module does.
# This is a separate knob from BusinessQueryModule's `step_timeout_seconds`
# (default 10.0), which bounds each module step. See ADR 0053 Decision 6.
_RENDER_WAIT_SECONDS = 30.0

# Same D1 text budget as BusinessQueryRequest.max_answer_chars and
# ResultPageExecutor's default (wire/request.py, compile/pagination/page_executor.py).
_MAX_ANSWER_CHARS = 8_000


class _RendererUsageCapture(BaseCallbackHandler):
    """Captures `render_rich`'s token usage for cost-total folding.

    Deliberately separate from the factory-attached ``InvocationLedgerCallbackHandler``
    (which already logs this same call under ``ModelPurpose.record_reasoning`` --
    see ``app.providers.factory._attach_ledger``): this handler writes nowhere,
    it only reads the same ``usage_metadata`` fields back out so the caller can
    fold them into the turn's token totals. Never invents numbers -- ``usage``
    stays ``None`` unless the model actually reported token counts.
    """

    def __init__(self) -> None:
        super().__init__()
        self.usage: dict[str, int | None] | None = None

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        input_tokens, output_tokens, reasoning_tokens = extract_token_usage(response)
        if input_tokens is None and output_tokens is None:
            return
        self.usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
        }


async def apply_rich_presentation(
    question: str, outcome: BusinessQueryOutcome
) -> tuple[BusinessQueryOutcome, dict[str, int | None] | None]:
    """Ask-only rich prose over a sealed ``Answered`` (ADR 0053 Decision 6).

    Deliberately placed AFTER the OSError->Incomplete mapper in
    ``business_query_service.compose_business_query_answer`` (this function only ever runs
    once ``_module_query`` has already succeeded) -- Python 3.11
    ``TimeoutError`` subclasses ``OSError``, so a renderer timeout caught by
    that earlier mapper would wrongly turn an Answered into Incomplete. Runs
    `render_rich` on the loop's default executor via `asyncio.to_thread` --
    never the composition shared adapter executor, which is reserved for SQL
    adapter work -- under its own `_RENDER_WAIT_SECONDS` budget. Timeout or
    any renderer exception falls back to the deterministic `present_for_plan`
    text; the outcome always stays "answered" (never Incomplete).

    Only runs for an `Answered` outcome that already carries a `plan`
    (module `execute()` sets both together via `answered_from_unsealed`). A
    bare `Answered` fixture with no plan -- legal on `outcome.plan`'s type,
    used by tests that stub `_module_query` directly -- keeps its own
    `answer_text` unchanged: neither `render_rich` nor `present_for_plan`
    can run without a plan.

    Returns the (possibly rewritten) outcome AND the renderer's own token
    usage, captured via a local callback on the model the renderer invokes
    -- ``None`` unless the model actually returned usage (never fabricated
    for a fallback/timeout).
    """
    if not isinstance(outcome, Answered) or outcome.plan is None:
        return outcome, None

    usage_capture = _RendererUsageCapture()

    def _model_factory():
        model = get_chat_model(purpose=ModelPurpose.record_reasoning, temperature=0)
        return model.with_config({"callbacks": [usage_capture]})

    answer_query_id = outcome.receipt.answer_query_id
    with presenter_stage_span(answer_query_id=answer_query_id) as span:
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(
                    render_rich,
                    question,
                    outcome.plan,
                    outcome,
                    model_factory=_model_factory,
                    fallback=present_for_plan,
                ),
                timeout=_RENDER_WAIT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — timeout or any renderer failure stays "answered"
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR)
            span.set_attribute("bq.outcome", "fallback")
            text = present_for_plan(outcome.plan, outcome)
        else:
            # The guard itself must never be able to turn an Answered into a
            # failure (ADR 0053 Decision 6) -- a crash inside the guard degrades
            # to the deterministic text exactly like a guard trip would.
            try:
                preserved = rendered_values_preserved(text, outcome.plan, outcome)
            except Exception:  # noqa: BLE001 — a guard crash must never fail an Answered
                logger.warning("render guard raised; degrading to present_for_plan", exc_info=True)
                preserved = False
            if not preserved:
                record_render_guard_trip()
                span.set_attribute("bq.outcome", "fallback")
                text = present_for_plan(outcome.plan, outcome)
            else:
                span.set_attribute("bq.outcome", "answered")
    finalized = finalize_answer_text(
        outcome.plan, outcome, text=text, max_answer_chars=_MAX_ANSWER_CHARS
    )
    return finalized, usage_capture.usage
