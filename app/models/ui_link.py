"""Billing-declared page or action link sealed for this viewer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class UiLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    kind: Literal["page", "action"]
    label: str
    href: str
