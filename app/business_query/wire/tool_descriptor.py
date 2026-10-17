"""Dispatch gate for business-query plan tools."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    read_only: bool

    def assert_dispatchable(self) -> None:
        if not self.read_only:
            raise PermissionError("tool is not dispatchable")


BUSINESS_QUERY_PLAN_TOOL = ToolDescriptor(name="business_query.plan", read_only=True)
