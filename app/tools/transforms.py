"""Arg transforms for ToolExecution.

S1 owns the RLS body of ``rls_transform_args``. Bind at graph build:
``partial(rls_transform_args, row_policy=…)`` on ``sql_db_query`` only.
Other tools use ``noop_transform``.
"""

from __future__ import annotations

from app.rag.sql_row_policy import SqlToolRejection
from app.rag.sql_row_policy_audit import record_sql_row_policy_decision
from app.tools.types import ToolRejection


def noop_transform(args, principal):
    """Default transform for non-query tools. S1 RLS attaches only on sql_db_query."""
    return args


def rls_transform_args(args, principal, *, row_policy):
    """sql_db_query transform slot (canonical name — sole export for RLS)."""
    if "query" not in args:
        return args
    if row_policy is None or getattr(row_policy, "is_eval_cli_fixture", False):
        return args

    from app.guardrails.sql_guard import referenced_tables

    query = str(args["query"])
    table_names = sorted(referenced_tables(query))
    transformed = row_policy.transform_sql(query)
    if isinstance(transformed, SqlToolRejection):
        record_sql_row_policy_decision(
            accepted=False,
            reason_code=transformed.reason_code,
            table_names=table_names,
            entity_id=getattr(row_policy, "entity_id", None),
        )
        return ToolRejection(transformed.reason)

    record_sql_row_policy_decision(
        accepted=True,
        reason_code=None,
        table_names=table_names,
        entity_id=getattr(row_policy, "entity_id", None),
    )
    return {**args, "query": transformed.sql, "_params": transformed.params}
