"""Pre-flight: prove reasoning_effort actually moves the model before spending a campaign."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

# Ratio threshold — model-size independent.
MIN_EFFORT_SEPARATION_RATIO = 2.0
MIN_SAMPLES_PER_EFFORT = 3

VERDICT_SEPARATED = "EFFORT-SEPARATED"
VERDICT_INERT = "EFFORT-INERT"
VERDICT_INSUFFICIENT = "EFFORT-UNPROVEN"
VERDICT_UNMEASURABLE = "EFFORT-UNMEASURABLE"


@dataclass(frozen=True)
class EffortProbeResult:
    verdict: str
    low_median_reasoning_tokens: float
    high_median_reasoning_tokens: float
    ratio: float | None
    detail: str

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_SEPARATED


def evaluate_effort_separation(
    low_reasoning_tokens: list[int | None],
    high_reasoning_tokens: list[int | None],
    *,
    min_ratio: float = MIN_EFFORT_SEPARATION_RATIO,
    min_samples: int = MIN_SAMPLES_PER_EFFORT,
) -> EffortProbeResult:
    """Compare reasoning-token spend between a low- and a high-effort sample."""
    if all(value is None for value in low_reasoning_tokens + high_reasoning_tokens):
        return EffortProbeResult(
            VERDICT_UNMEASURABLE,
            0.0,
            0.0,
            None,
            "no route reported a reasoning-token counter -- effort separation cannot be "
            "measured on this provider (this is NOT evidence the knob is inert)",
        )
    low = [value for value in low_reasoning_tokens if value is not None]
    high = [value for value in high_reasoning_tokens if value is not None]
    if len(low) < min_samples or len(high) < min_samples:
        return EffortProbeResult(
            VERDICT_INSUFFICIENT,
            0.0,
            0.0,
            None,
            f"need >={min_samples} samples per effort; got low={len(low)} high={len(high)}",
        )
    low_med = float(median(low))
    high_med = float(median(high))
    if low_med <= 0 and high_med <= 0:
        return EffortProbeResult(
            VERDICT_INERT,
            low_med,
            high_med,
            None,
            "no reasoning tokens reported at either effort — model may not be reasoning at all",
        )
    if low_med <= 0:
        # High reasons, low does not: that IS separation, and a ratio would divide by zero.
        return EffortProbeResult(
            VERDICT_SEPARATED,
            low_med,
            high_med,
            None,
            "low effort produced no reasoning tokens; high effort did",
        )
    ratio = high_med / low_med
    if ratio < min_ratio:
        return EffortProbeResult(
            VERDICT_INERT,
            low_med,
            high_med,
            ratio,
            f"reasoning tokens separated by {ratio:.2f}x; need >={min_ratio}x. "
            "The effort knob is probably not wired to this model.",
        )
    return EffortProbeResult(
        VERDICT_SEPARATED,
        low_med,
        high_med,
        ratio,
        f"reasoning tokens separated by {ratio:.2f}x",
    )
