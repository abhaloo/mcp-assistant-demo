"""Display metadata for one structured result.

The server owns the title and the human-readable filter facts so the panel
never derives them from an answer sentence. This model carries no SQL and no
authorization predicate.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FilterFact = Annotated[str, StringConstraints(min_length=1, max_length=60)]


SCOPE_MAX_CHARS = 300


class ResultPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    summary: str | None = Field(default=None, max_length=240)
    applied_filters: list[FilterFact] = Field(default_factory=list, max_length=8)
    scope: str | None = Field(default=None, max_length=SCOPE_MAX_CHARS)
