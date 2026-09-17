"""Structured-access seam for legacy SQL agent evaluation containment.

Every caller that constructs a SQL agent must call ``ensure_scoped_sql_access()``
immediately before building one.

``SQL_POLICY_MODE`` defaults to ``"disabled"``. Passing this seam requires both:
  1. ``settings.sql_policy_mode == "scoped"``, and
  2. an explicit ``ScopedSqlPolicy`` instance passed as ``policy``.

``ScopedSqlPolicy`` ALSO carries a second, separate, unconditional exemption
for the CLI/eval/test row of the plan's behavior matrix:
``ScopedSqlPolicy.eval_snapshot_fixture()`` always satisfies this check,
independent of ``SQL_POLICY_MODE``. Every legitimate caller of that factory
runs off the production request path — never live production traffic. Grep
for ``eval_snapshot_fixture(`` to enumerate every legitimate use; it must
never appear under ``app/api/`` or ``app/services/``.

Callers apply the check twice on purpose:
  1. constructor time — at the top of the SQL-invoking function, before any
     memory/DB work starts (fails fast, keeps the circuit breaker out of a
     pure config decision).
  2. last-mile — immediately before the SQL agent is actually built, so a
     future refactor that adds logic between the two call sites still can't
     smuggle an unscoped construction through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.rag.sql_row_policy import ScopedSqlQuery, SqlToolRejection

_EVAL_MARKER = object()


class StructuredAccessDenied(Exception):
    """Raised when a caller may not construct a SQL agent right now."""


class ScopedSqlPolicy:
    """Per-request scoped-access policy (construction gate + row-scope fields).

    Production instances require ``entity_id`` or ``cross_entity=True``.
    Eval/CLI exemption is only via ``eval_snapshot_fixture()`` — the public
    constructor rejects ``is_eval_cli_fixture=``.
    """

    def __init__(
        self,
        *,
        entity_id: int | None = None,
        cross_entity: bool = False,
        department_id: int | None = None,
        _marker: object | None = None,
        **kwargs: object,
    ) -> None:
        if "is_eval_cli_fixture" in kwargs:
            raise TypeError(
                "is_eval_cli_fixture is not a public constructor argument; "
                "use ScopedSqlPolicy.eval_snapshot_fixture()"
            )
        if kwargs:
            unexpected = ", ".join(sorted(str(k) for k in kwargs))
            raise TypeError(f"unexpected keyword argument(s): {unexpected}")

        if _marker is _EVAL_MARKER:
            self._is_eval_cli_fixture = True
            self.entity_id = None
            self.cross_entity = False
            self.department_id = None
            return

        if entity_id is None and not cross_entity:
            raise ValueError(
                "ScopedSqlPolicy requires entity_id or cross_entity=True "
                "(use eval_snapshot_fixture() for the CLI/eval exemption)"
            )
        self._is_eval_cli_fixture = False
        self.entity_id = entity_id
        self.cross_entity = cross_entity
        self.department_id = department_id

    @property
    def is_eval_cli_fixture(self) -> bool:
        return self._is_eval_cli_fixture

    @classmethod
    def eval_snapshot_fixture(cls) -> ScopedSqlPolicy:
        """The explicit, unconditional CLI/eval/test exemption.

        See the module docstring for the callers this is restricted to by
        convention (never ``app/api/`` or ``app/services/``).
        """
        return cls(_marker=_EVAL_MARKER)

    def transform_sql(self, query: str) -> ScopedSqlQuery | SqlToolRejection:
        """Apply parameterized row scope to ``query`` (thin transform delegate)."""
        from app.rag.sql_row_policy import SqlQueryArgTransform

        return SqlQueryArgTransform().transform(
            query,
            entity_id=self.entity_id,
            cross_entity=self.cross_entity,
            department_id=self.department_id,
        )


def ensure_scoped_sql_access(policy: ScopedSqlPolicy | None = None) -> None:
    """Raise ``StructuredAccessDenied`` unless a real exemption is proven.

    Two ways to pass:
      1. ``policy`` is ``ScopedSqlPolicy.eval_snapshot_fixture()`` — the
         CLI/eval/test row's exemption, unconditional (see the module
         docstring).
      2. ``settings.sql_policy_mode == "scoped"`` AND ``policy`` is some OTHER
         ``ScopedSqlPolicy`` instance. No production call site constructs one
         today (A1 enablement will) — so, for every real request, this function
         denies regardless of ``SQL_POLICY_MODE``. A setting alone can never
         create an unscoped database connection.

    The message is deliberately generic — same tone as the sibling
    "records_only mode refuses SQL" denial. Both flow, unfiltered, into the
    client-visible JSON 503 ``detail`` field, so this string must never name
    the config key, its value, or this function's parameters.
    """
    if isinstance(policy, ScopedSqlPolicy) and policy.is_eval_cli_fixture:
        return
    if settings.sql_policy_mode != "scoped" or not isinstance(policy, ScopedSqlPolicy):
        raise StructuredAccessDenied("structured SQL is currently disabled")
