"""The checkpointer the runner compiles. Cap 1 keeps runs in memory; nothing is durable yet."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver


def build_checkpointer() -> BaseCheckpointSaver:
    return InMemorySaver()
