"""Client UI directives and disambiguation payload models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DisambiguationCandidate(BaseModel):
    """Candidate entity suggestion when resolver encounters ambiguous shorthand."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    resource_type: Literal["customer", "job", "invoice", "item", "product"]
    label: str = Field(min_length=1, max_length=128)
    secondary_label: str | None = Field(default=None, max_length=128)
    suggested_query: str = Field(min_length=1, max_length=200)


class DisambiguationPayload(BaseModel):
    """Structured disambiguation card sent to user when entity lookup matches multiple rows."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=64, description="The ambiguous input phrase")
    candidates: list[DisambiguationCandidate] = Field(min_length=2, max_length=5)


class ClientAction(BaseModel):
    """Instruction for frontend UI to highlight elements or switch active tabs."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["highlight_element", "switch_tab"]
    target_selector: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^#[a-zA-Z0-9_-]{1,64}$",
        description=(
            "Strict element ID selector (e.g. #invoices-table) to prevent selector injection"
        ),
    )
    tab_key: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=128)
