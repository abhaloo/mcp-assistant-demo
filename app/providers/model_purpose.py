"""Explicit purpose for every chat-model resolution path."""

from enum import StrEnum


class ModelPurpose(StrEnum):
    """Why a chat model is being requested — drives routing and eligibility."""

    classify = "classify"
    rag_answer = "rag_answer"
    sql_agent = "sql_agent"
    conversation = "conversation"
    record_reasoning = "record_reasoning"
    coordinator = "coordinator"
    eval = "eval"


# Capabilities a target must declare before a route may serve this purpose.
# Read at startup (catalog validation) and at resolve time (route policy) --
# one table, so the two can never disagree. ``eval`` has no production route.
PURPOSE_CAPABILITY_REQUIREMENTS: dict[ModelPurpose, frozenset[str]] = {
    ModelPurpose.classify: frozenset({"text"}),
    ModelPurpose.rag_answer: frozenset({"text", "streaming"}),
    ModelPurpose.sql_agent: frozenset({"tools"}),
    ModelPurpose.record_reasoning: frozenset({"tools", "structured_output"}),
    ModelPurpose.conversation: frozenset({"text"}),
    ModelPurpose.coordinator: frozenset({"text", "tools"}),
}
