"""Campaign Reasoning Persistence — CapturingCallback → reasoning_calls (Contract)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.eval.sql.diagnostics import CapturingCallback


def _llm_response_with_reasoning(text: str) -> SimpleNamespace:
    msg = AIMessage(content="ok", additional_kwargs={"reasoning_content": text})
    gen = SimpleNamespace(message=msg)
    return SimpleNamespace(generations=[[gen]], llm_output={"model_name": "test-model"})


def test_capturing_callback_records_untruncated_reasoning_calls() -> None:
    """Contract: one entry per LLM end; full raw text; no 4k truncate."""
    huge = "PLAN " + ("x" * 5000) + " END"
    cb = CapturingCallback()
    run_id = uuid4()
    cb.on_llm_start({}, ["prompt"], run_id=run_id)
    cb.on_llm_end(_llm_response_with_reasoning(huge), run_id=run_id)

    assert len(cb.reasoning_calls) == 1
    entry = cb.reasoning_calls[0]
    assert entry["i"] == 0
    assert entry["reasoning_text_raw"] == huge
    assert entry["char_count"] == len(huge)
    assert len(entry["reasoning_text_raw"]) > 4000
    assert entry["sha256"] == hashlib.sha256(huge.encode()).hexdigest()
    assert entry["reasoning_kind"] == "provider_cot"


def test_capturing_callback_skips_empty_reasoning() -> None:
    cb = CapturingCallback()
    run_id = uuid4()
    cb.on_llm_start({}, ["prompt"], run_id=run_id)
    cb.on_llm_end(_llm_response_with_reasoning("   "), run_id=run_id)
    assert cb.reasoning_calls == []


def test_capturing_callback_indexes_multiple_calls() -> None:
    cb = CapturingCallback()
    for i, text in enumerate(("first", "second")):
        run_id = uuid4()
        cb.on_llm_start({}, ["p"], run_id=run_id)
        cb.on_llm_end(_llm_response_with_reasoning(text), run_id=run_id)
        assert cb.reasoning_calls[-1]["i"] == i
        assert cb.reasoning_calls[-1]["reasoning_text_raw"] == text
