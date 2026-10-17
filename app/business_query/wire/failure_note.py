"""What a business query that did not answer tells the coordinator. Never SQL, never on the wire."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FailureNote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reason_code: str
    family: Literal["permission", "capability", "coverage", "transient"]
    # Labels of the record types outside the viewer's access, when the planner named them.
    outside_access: tuple[str, ...] = ()
    available: tuple[str, ...] = ()
    # What the coordinator reads: plain business words, no member keys, no schema words.
    coordinator_advice: str
