"""Campaign verdict representation and downgrade helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CampaignVerdict:
    status: str
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status}
        if self.reasons:
            data["reasons"] = list(self.reasons)
        if self.details is not None:
            data["details"] = self.details
        return data


def downgrade_to_hold(
    verdict: Any,
    *,
    campaign_cap_usd: float,
    actual_spend_usd: float,
) -> CampaignVerdict:
    """Downgrade any passing/verdict result to HOLD when campaign cap is exceeded."""
    if hasattr(verdict, "to_dict"):
        underlying = verdict.to_dict()
    elif hasattr(verdict, "__dataclass_fields__"):
        underlying = asdict(verdict)
    elif isinstance(verdict, dict):
        underlying = dict(verdict)
    else:
        underlying = {"status": str(verdict)}

    return CampaignVerdict(
        status="HOLD",
        reasons=("campaign_cap_exceeded",),
        details={
            "campaign_cap_usd": campaign_cap_usd,
            "accounted_spend_usd": actual_spend_usd,
            "underlying_verdict": underlying,
        },
    )
