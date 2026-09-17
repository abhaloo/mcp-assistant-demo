"""Strict-mode JSON schema for the planner response envelope (OpenAI wire).

Hand-authored because the Pydantic-derived schema cannot enter strict mode:
``between`` serializes as a tuple (``prefixItems``, unsupported) and optional
fields carry ``default``. langchain's ``strict=True`` path already auto-forces
``required`` and injects ``additionalProperties: false``; the load-bearing
choices here are the tuple→object ``between``, no defaults, and anyOf-null
nullability. Pydantic models in plan.py remain the validation truth; the
parity tests keep names AND enum values in lockstep.
"""

from app.business_query.plan.derived_sets import _SET_ID_PATTERN

_FILTER = {
    "type": "object",
    "properties": {
        "member": {"type": "string"},
        "operator": {
            "type": "string",
            "enum": [
                "eq",
                "neq",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "not_in",
                "is_null",
                "not_null",
                "contains",
                "in_set",
                "not_in_set",
            ],
        },
        "values": {"type": "array", "items": {"type": ["string", "number", "boolean"]}},
    },
    "required": ["member", "operator", "values"],
    "additionalProperties": False,
}

_ATTRIBUTE_PREDICATE = {
    "type": "object",
    "properties": {
        "attribute_key": {"type": "string"},
        "definition_revision": {"type": ["string", "null"]},
        "value_kind": {
            "type": "string",
            "enum": [
                "integer",
                "numeric",
                "decimal",
                "currency",
                "string",
                "date",
                "datetime",
                "boolean",
                "json",
            ],
        },
        "operator": {
            "type": "string",
            "enum": [
                "eq",
                "neq",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "not_in",
                "is_null",
                "not_null",
                "contains",
            ],
        },
        "values": {"type": "array", "items": {"type": ["string", "number", "boolean"]}},
    },
    "required": ["attribute_key", "definition_revision", "value_kind", "operator", "values"],
    "additionalProperties": False,
}

_FILTER_NODE = {
    "anyOf": [
        _FILTER,
        _ATTRIBUTE_PREDICATE,
        {"$ref": "#/$defs/filter_group"},
    ]
}

_FILTER_GROUP = {
    "type": "object",
    "properties": {
        "all": {"type": "array", "items": _FILTER_NODE},
        "any": {"type": "array", "items": _FILTER_NODE},
    },
    "required": ["all", "any"],
    "additionalProperties": False,
}

_NULLABLE_GROUP = {"anyOf": [{"$ref": "#/$defs/filter_group"}, {"type": "null"}]}

_RELATIVE = {
    "anyOf": [
        {
            "type": "string",
            "enum": [
                "today",
                "this_week",
                "last_week",
                "this_month",
                "last_month",
                "this_quarter",
                "last_quarter",
                "this_year",
                "last_year",
            ],
        },
        {"type": "null"},
    ]
}

_PERIOD_OBJECT = {
    "type": "object",
    "properties": {
        "time_dimension": {"type": "string"},
        "relative": _RELATIVE,
        "on": {"type": ["string", "null"], "format": "date"},
        "since": {"type": ["string", "null"], "format": "date"},
        "between": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "format": "date"},
                        "end": {"type": "string", "format": "date"},
                    },
                    "required": ["start", "end"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
        "granularity": {
            "anyOf": [
                {"type": "string", "enum": ["day", "week", "month", "quarter", "year"]},
                {"type": "null"},
            ]
        },
    },
    "required": ["time_dimension", "relative", "on", "since", "between", "granularity"],
    "additionalProperties": False,
}

_PERIOD = {
    "anyOf": [
        {"$ref": "#/$defs/period"},
        {"type": "null"},
    ]
}

_COMPARE_TO = {
    "anyOf": [
        {"$ref": "#/$defs/period"},
        {
            "type": "string",
            "enum": ["previous_period", "same_period_last_year"],
        },
        {"type": "null"},
    ]
}

_ORDER = {
    "type": "object",
    "properties": {
        "member": {"type": "string"},
        "direction": {"type": "string", "enum": ["asc", "desc"]},
    },
    "required": ["member", "direction"],
    "additionalProperties": False,
}

_DETAIL_SELECTION = {
    "type": "object",
    "properties": {
        "family": {
            "type": "string",
            "description": (
                "Canonical signed detail-family key enumerated by the capability card; "
                "never substitute an unrequested family."
            ),
        },
        "revision_mode": {
            "type": "string",
            "enum": ["recorded", "current", "as_of", "exact"],
        },
        "revision_hash": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"], "format": "date-time"},
    },
    "required": ["family", "revision_mode", "revision_hash", "as_of"],
    "additionalProperties": False,
}

_PLAN_BODY_PROPERTIES = {
    "measures": {"type": "array", "items": {"type": "string"}},
    "dimensions": {"type": "array", "items": {"type": "string"}},
    "bucket_set": {"type": ["string", "null"]},
    "filters": _NULLABLE_GROUP,
    "having": _NULLABLE_GROUP,
    "period": _PERIOD,
    "compare_to": _COMPARE_TO,
    "grain": {"type": "string", "enum": ["scalar", "grouped", "entity_rows"]},
    "order": {"type": "array", "items": _ORDER},
    "attribute_predicates": {"type": "array", "items": _ATTRIBUTE_PREDICATE},
    "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 50},
    "detail_selections": {"type": "array", "items": _DETAIL_SELECTION},
}


_INNER_PLAN_DEFINITION = {
    "type": "object",
    "properties": dict(_PLAN_BODY_PROPERTIES),
    "required": sorted(_PLAN_BODY_PROPERTIES),
    "additionalProperties": False,
}

_DERIVED_SET = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": _SET_ID_PATTERN.pattern, "maxLength": 32},
        "mode": {"type": "string", "enum": ["complete", "ranked"]},
        "key": {"type": "string"},
        "plan": {"$ref": "#/$defs/inner_plan"},
    },
    "required": ["id", "mode", "key", "plan"],
    "additionalProperties": False,
}

_PLAN_DEFINITION = {
    "type": "object",
    "properties": {
        **_PLAN_BODY_PROPERTIES,
        "derived_sets": {
            "type": "array",
            "items": _DERIVED_SET,
            "maxItems": 2,
        },
    },
    "required": sorted([*_PLAN_BODY_PROPERTIES, "derived_sets"]),
    "additionalProperties": False,
}

_PLAN = {
    "anyOf": [
        {"$ref": "#/$defs/plan"},
        {"type": "null"},
    ]
}


_CLARIFICATION_CHOICE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "label": {"type": "string"},
    },
    "required": ["id", "label"],
    "additionalProperties": False,
}

_COMPANION_PLANS = {
    "anyOf": [
        {"type": "array", "items": {"$ref": "#/$defs/plan"}, "maxItems": 3},
        {"type": "null"},
    ]
}

PLANNER_WIRE_SCHEMA = {
    "title": "planner_response",
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["plan", "clarify", "unsupported"]},
        "plan": _PLAN,
        "companion_plans": _COMPANION_PLANS,
        "clarification_question": {"type": ["string", "null"]},
        "clarification_choices": {
            "anyOf": [
                {"type": "array", "items": _CLARIFICATION_CHOICE},
                {"type": "null"},
            ]
        },
        "unsupported_reason": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["member_not_found", "grain_unexpressible", "period_dimension_missing"],
                },
                {"type": "null"},
            ]
        },
    },
    "required": sorted(
        {
            "action",
            "plan",
            "companion_plans",
            "clarification_question",
            "clarification_choices",
            "unsupported_reason",
        }
    ),
    "additionalProperties": False,
    "$defs": {
        "filter_group": _FILTER_GROUP,
        "inner_plan": _INNER_PLAN_DEFINITION,
        "period": _PERIOD_OBJECT,
        "plan": _PLAN_DEFINITION,
    },
}
