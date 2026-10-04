"""DeepSeek-compatible strict-mode JSON schema for the planner wire.

Inlines $defs, unrolls the recursive filter_group definition to a fixed depth,
and strips format constraints that DeepSeek's Responses API rejects.
"""

from __future__ import annotations

import copy
from typing import Any

from app.business_query.plan.planner_wire_schema import PLANNER_WIRE_SCHEMA

DEEPSEEK_FILTER_DEPTH = 2


def deepseek_wire_schema(schema: dict[str, Any], filter_depth: int) -> dict[str, Any]:
    """Inline all definitions, unroll recursive filter groups to filter_depth, drop format."""
    defs = schema.get("$defs", {})

    def resolve(node: Any, level: int) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref and isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                next_level = level + 1 if name == "filter_group" else level
                return resolve(copy.deepcopy(defs[name]), next_level)
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "format":
                    continue
                if k == "anyOf" and level >= filter_depth and isinstance(v, list):
                    v = [
                        b
                        for b in v
                        if not (isinstance(b, dict) and b.get("$ref") == "#/$defs/filter_group")
                    ]
                out[k] = resolve(v, level)
            return out
        if isinstance(node, list):
            return [resolve(v, level) for v in node]
        return node

    base = {k: v for k, v in schema.items() if k != "$defs"}
    return resolve(base, 0)


DEEPSEEK_PLANNER_WIRE_SCHEMA = deepseek_wire_schema(PLANNER_WIRE_SCHEMA, DEEPSEEK_FILTER_DEPTH)

# Planner wire schema per provider; a provider not listed uses PLANNER_WIRE_SCHEMA.
PROVIDER_WIRE_SCHEMAS: dict[str, dict[str, Any]] = {"deepseek_direct": DEEPSEEK_PLANNER_WIRE_SCHEMA}
