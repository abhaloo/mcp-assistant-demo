"""Pure tests for SqlRunBudget pre-call gate."""

import pytest
from langchain_core.messages import AIMessage

from app.eval.sql.agent.run_budget import (
    SqlContextBudgetExceeded,
    SqlRunBudget,
    estimate_messages_tokens,
)


def test_check_before_call_blocks_when_estimate_exceeds_cap():
    budget = SqlRunBudget(max_llm_calls=8, max_next_prompt_tokens=100)
    huge = AIMessage(content="x" * 4000, additional_kwargs={"reasoning_content": "y" * 4000})
    with pytest.raises(SqlContextBudgetExceeded) as ei:
        budget.check_before_call([huge], system_content="sys")
    assert ei.value.reason == "next_prompt_tokens"


def test_check_before_call_blocks_on_max_llm_calls():
    budget = SqlRunBudget(max_llm_calls=2, max_next_prompt_tokens=1_000_000)
    budget.record_llm_call()
    budget.record_llm_call()
    with pytest.raises(SqlContextBudgetExceeded) as ei:
        budget.check_before_call([], system_content="sys")
    assert ei.value.reason == "max_llm_calls"


def test_estimate_includes_reasoning_content():
    empty = AIMessage(content="", additional_kwargs={"reasoning_content": "z" * 5000})
    bare = AIMessage(content="")
    with_reasoning = estimate_messages_tokens([empty])
    without_reasoning = estimate_messages_tokens([bare])
    assert with_reasoning > without_reasoning


def test_reset_clears_call_counter_for_fresh_turn():
    budget = SqlRunBudget(max_llm_calls=2, max_next_prompt_tokens=1_000_000)
    budget.record_llm_call()
    budget.record_llm_call()
    with pytest.raises(SqlContextBudgetExceeded) as ei:
        budget.check_before_call([])
    assert ei.value.reason == "max_llm_calls"

    budget.reset()
    budget.check_before_call([])  # must not raise
    budget.record_llm_call()
    assert budget._llm_calls == 1


def test_clarify_stop_flag_set_and_consumed():
    budget = SqlRunBudget(max_llm_calls=8, max_next_prompt_tokens=1_000_000)
    assert budget.consume_clarify_stop() is False
    budget.mark_clarify_stop()
    assert budget.consume_clarify_stop() is True
    assert budget.consume_clarify_stop() is False  # one-shot
    budget.mark_clarify_stop()
    budget.reset()
    assert budget.consume_clarify_stop() is False


# ------------------------------------------------------------ cost-aware budget


def test_cumulative_cost_raises_on_the_fifth_call_not_the_fourth():
    """$0.035 ceiling / $0.01 per call: 3 calls = $0.03 (< ceiling), 4 calls = $0.04
    (>= ceiling). The guard is `>=`, checked BEFORE the next call, so the 5th
    check_before_call is the one that raises -- not the 4th."""
    budget = SqlRunBudget(
        max_llm_calls=100,
        max_next_prompt_tokens=1_000_000,
        max_cumulative_cost_usd=0.035,
        price_fn=lambda prompt, completion: 0.01,
    )
    usage_msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    for _ in range(4):
        budget.check_before_call([])  # must not raise
        budget.record_llm_call()
        budget.record_response_usage(usage_msg)

    with pytest.raises(SqlContextBudgetExceeded) as ei:
        budget.check_before_call([])
    assert ei.value.reason == "cumulative_cost_usd"


def test_record_usage_accumulates_and_reset_clears_cost_and_tokens():
    budget = SqlRunBudget(max_llm_calls=100, max_next_prompt_tokens=1_000_000)
    budget.record_usage(prompt_tokens=100, cost_usd=0.02)
    budget.record_usage(prompt_tokens=50, cost_usd=0.01)
    assert budget._prompt_tokens == 150
    assert budget._cost_usd == pytest.approx(0.03)

    budget.reset()
    assert budget._prompt_tokens == 0
    assert budget._cost_usd == 0.0


def test_no_cost_ceiling_never_raises_for_cost():
    """max_cumulative_cost_usd=None is the production default -- no amount of recorded
    cost may raise, only max_llm_calls / prompt-token guards may."""
    budget = SqlRunBudget(max_llm_calls=1000, max_next_prompt_tokens=1_000_000)
    for _ in range(50):
        budget.check_before_call([])
        budget.record_usage(cost_usd=1000.0)

    budget.check_before_call([])  # still must not raise


def test_record_response_usage_with_no_usage_metadata_is_a_noop():
    budget = SqlRunBudget(
        max_llm_calls=100, max_next_prompt_tokens=1_000_000, price_fn=lambda p, c: 1.0
    )
    # Seed real state first so a broken no-op (e.g. one that resets instead of skipping)
    # would be visible, not just indistinguishable from a fresh budget.
    budget.record_response_usage(
        AIMessage(
            content="x",
            usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        )
    )
    before_tokens, before_cost = budget._prompt_tokens, budget._cost_usd

    budget.record_response_usage(AIMessage(content="no usage here"))  # usage_metadata=None

    assert budget._prompt_tokens == before_tokens
    assert budget._cost_usd == before_cost


def test_record_response_usage_without_price_fn_records_tokens_and_zero_cost():
    budget = SqlRunBudget(max_llm_calls=100, max_next_prompt_tokens=1_000_000)  # price_fn=None
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    )

    budget.record_response_usage(msg)

    assert budget._prompt_tokens == 100
    assert budget._cost_usd == 0.0
