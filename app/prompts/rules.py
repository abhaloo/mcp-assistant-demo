"""One join for every assembled prompt: rules in order, blank blocks dropped."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class PromptRule(Protocol):
    """A named prompt fragment that decides its own text from the context."""

    name: str

    def block(self, ctx: object, /) -> str: ...


@dataclass(frozen=True)
class _StaticBlock:
    name: str
    text: str

    def block(self, ctx: object = None, /) -> str:
        return self.text


def render_rules(rules: Sequence[PromptRule], ctx: object) -> str:
    """Join every non-empty block with one blank line between."""
    return "\n\n".join(filter(None, (rule.block(ctx).strip() for rule in rules)))
