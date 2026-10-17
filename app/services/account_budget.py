"""Account spend budget admission gate (ADR 0078, spec §4.7)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from app.auth import Principal
from app.business_query.definitions import current_bundle
from app.config import settings
from app.models.ask_v2_events import TurnBudgetReport
from app.models.tool_results import TurnResult
from app.query_records.context import TerminalUsageCapture

if TYPE_CHECKING:
    from app.query_records.repository import PeriodSpend
    from app.resources import ProcessResources

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("app.services.account_budget")

_LAST_MONTH_NUMBER = 12

DENIED_TURN_RESULT = TurnResult(
    outcome_type="denied",
    completeness="none",
    trusted=False,
    selected=(),
    omissions=(),
    components=(),
)


class BudgetExhaustedError(Exception):
    """Account monthly spend budget is exhausted."""

    def __init__(self, verdict: BudgetVerdict) -> None:
        self.verdict = verdict
        super().__init__(
            f"Account budget exhausted (used ${verdict.used_usd} of ${verdict.limit_usd})"
        )


class BudgetUnconfiguredError(Exception):
    """Account has no budget configured or missing entity_id."""


class BudgetUnverifiableError(Exception):
    """Account budget could not be verified against the ledger."""


class BudgetVerdict(BaseModel):
    """Snapshot of account budget usage and remaining spend for the period."""

    model_config = ConfigDict(frozen=True)

    used_usd: Decimal
    limit_usd: Decimal
    remaining_usd: Decimal
    period_start: datetime
    period_end: date
    reset_at: datetime


def budget_report_for(
    verdict: BudgetVerdict | None, usage: TerminalUsageCapture | None
) -> TurnBudgetReport | None:
    """Outcome-frame report: admission verdict plus the spend the turn's usage
    was priced with when it was filled."""
    if verdict is None:
        return None
    return TurnBudgetReport(
        this_turn_usd=usage.estimated_usd if usage is not None else None,
        used_usd=verdict.used_usd,
        limit_usd=verdict.limit_usd,
        period_end=verdict.period_end,
        reset_at=verdict.reset_at,
    )


def _resolve_business_timezone() -> ZoneInfo:
    """Read the business timezone the signed definition bundle declares."""
    try:
        bundle = current_bundle()
        return ZoneInfo(bundle.business_timezone)
    except (OSError, ValueError, KeyError) as err:
        # The gate cannot meter without the bundle's month boundary; refuse once raised
        raise BudgetUnverifiableError("Could not read the business definition bundle") from err


class SpendReader(Protocol):
    """Reads an entity's spend for one period from the ledger."""

    async def spend_in_period(
        self, *, entity_id: str, period_start: datetime, period_end: datetime, project_id: str
    ) -> PeriodSpend: ...


class AccountBudgetGate:
    """Verifies account-level monthly spend budget from the query records ledger."""

    def __init__(
        self,
        resources: ProcessResources | None = None,
        *,
        spend: SpendReader | None = None,
        highest_rate: Decimal | None = None,
    ) -> None:
        self._resources = resources
        self._spend = spend
        self._highest_rate = highest_rate

    async def _read_period_spend(
        self, entity_id: str, period_start: datetime, period_end: datetime
    ) -> PeriodSpend:
        """Read the ledger's period spend from the configured source."""
        if self._spend is not None:
            return await self._spend.spend_in_period(
                entity_id=entity_id,
                period_start=period_start,
                period_end=period_end,
                project_id=settings.query_record_project_id,
            )
        if self._resources is not None:
            async with self._resources.query_record_session_factory() as session:
                from app.query_records.repository import QueryRecordRepository

                repo = QueryRecordRepository(session)
                return await repo.spend_in_period(
                    entity_id=entity_id,
                    period_start=period_start,
                    period_end=period_end,
                    project_id=settings.query_record_project_id,
                )
        raise RuntimeError("No spend source configured on AccountBudgetGate")

    async def admit(self, principal: Principal, *, now: datetime) -> BudgetVerdict | None:
        """Admit or refuse a turn based on account monthly spend limit."""
        if not settings.ask_account_budget_enabled:
            return None

        with tracer.start_as_current_span("account_budget.admit") as span:
            if principal.ask_budget is None or principal.entity_id is None:
                entity_id_str = str(principal.entity_id) if principal.entity_id else "none"
                span.set_attribute("decision", "unconfigured")
                logger.info(
                    "ask budget refused",
                    extra={
                        "reason": "budget_unconfigured",
                        "entity_id": entity_id_str,
                        "used_usd": "0",
                    },
                )
                raise BudgetUnconfiguredError("Ask AI has no budget set for this account")

            entity_id_str = str(principal.entity_id)
            span.set_attribute("entity_id", entity_id_str)

            tz = _resolve_business_timezone()
            now_aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
            now_tz = now_aware.astimezone(tz)

            period_start = datetime(now_tz.year, now_tz.month, 1, 0, 0, 0, tzinfo=tz)
            if now_tz.month == _LAST_MONTH_NUMBER:
                reset_at = datetime(now_tz.year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
            else:
                reset_at = datetime(now_tz.year, now_tz.month + 1, 1, 0, 0, 0, tzinfo=tz)
            period_end = (reset_at - timedelta(days=1)).date()

            try:
                spend = await self._read_period_spend(entity_id_str, period_start, reset_at)
            except (SQLAlchemyError, OSError, ConnectionError, RuntimeError) as exc:
                # Store read failure mapped to typed BudgetUnverifiableError at repository seam
                logger.warning("Failed to verify account budget from ledger: %s", exc)
                span.set_attribute("decision", "unverifiable")
                logger.info(
                    "ask budget refused",
                    extra={
                        "reason": "budget_unverifiable",
                        "entity_id": entity_id_str,
                        "used_usd": "unknown",
                    },
                )
                raise BudgetUnverifiableError("Could not verify account budget") from exc

            highest_rate = self._highest_rate
            if highest_rate is None:
                from app.pricing.pricer import highest_rate_per_1k
                from app.pricing.prices import PRICES_PATH, load_prices

                highest_rate = highest_rate_per_1k(load_prices(PRICES_PATH))

            unpriced_tokens = spend.unpriced_input_tokens + spend.unpriced_output_tokens
            unpriced_cost = (Decimal(unpriced_tokens) / Decimal("1000")) * highest_rate
            used_usd = spend.priced_usd + unpriced_cost
            limit_usd = principal.ask_budget.limit_usd
            remaining_usd = max(Decimal("0"), limit_usd - used_usd)

            verdict = BudgetVerdict(
                used_usd=used_usd,
                limit_usd=limit_usd,
                remaining_usd=remaining_usd,
                period_start=period_start,
                period_end=period_end,
                reset_at=reset_at,
            )

            if used_usd >= limit_usd:
                span.set_attribute("decision", "exhausted")
                logger.info(
                    "ask budget refused",
                    extra={
                        "reason": "budget_exhausted",
                        "entity_id": entity_id_str,
                        "used_usd": str(used_usd),
                    },
                )
                raise BudgetExhaustedError(verdict)

            span.set_attribute("decision", "admitted")
            return verdict
