"""Write a finished attempt's review into ``<attempt>/review/``.

R2 of docs/superpowers/specs/2026-08-02-campaign-console-and-review-design.md.

WHY THIS LIVES HERE AND NOT IN ``Campaign``
===========================================
Extracted from ``app/experiments/core/campaign.py``, where it was called from
inside ``run_attempt``'s try block. Three things were wrong with that, and moving it
fixed all three at once:

* **Layering.** ``core/`` is the task-agnostic kernel. Having it import
  ``app.experiments.review`` -- which imports ``app.eval.run_stats`` -- pointed the
  module that PRODUCES ``answers.jsonl`` at the module that READS it. That was
  ``core/``'s only dependency on ``app.eval``, and it was introduced for this.
* **A pass-through parameter.** ``Campaign.__init__`` took a ``narrate_fn`` it never
  used, never reasoned about, and could not type, purely to hand it here.
* **The swallow was load-bearing only because of the placement.** Inside
  ``run_attempt``'s try, a reviewer's exception was indistinguishable from a campaign
  failure: an attempt that had completed, written its artifacts and settled its money
  would be re-reported as failed. Called from the runner AFTER ``run_attempt``
  RETURNS, that hazard does not exist -- the attempt is already a value.

The swallow stays anyway, because the weaker guarantee is still worth having: a
reviewer that raises should not take down a runner that has a finished, paid-for
attempt in hand. But it is now one ordinary ``try`` rather than a hard invariant
needing its own justification, and the failure mode is a FILE -- ``review-error.json``
-- which makes a broken reviewer visible without making it fatal.

WHAT IT MAY NOT TOUCH
=====================
Only ``review/``. It never rewrites ``verdict.json``, and because ``hash_artifacts``
has already run AND skips directories, the review's own output sits outside the
attempt's attestation. The hashes keep meaning exactly what they meant before: a
statement about the campaign, not about its review.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.experiments.review.checks import (
    TRIGGER_IN_CAMPAIGN,
    ReviewResult,
    render_markdown,
    review_attempt,
)

__all__ = ["write_attempt_review"]


def write_attempt_review(
    attempt_dir: Path,
    *,
    narrate_fn: Callable[[ReviewResult], Any] | None = None,
) -> Path | None:
    """Review the finished attempt in place. Returns the review directory, or ``None``.

    ``narrate_fn`` defaults to ``None``: a run that was never handed a narrator does
    not narrate and does not spend, which is what every test and every replay does.
    """
    attempt_dir = Path(attempt_dir)
    review_dir = attempt_dir / "review"
    try:
        result = review_attempt(attempt_dir, trigger=TRIGGER_IN_CAMPAIGN)
        # The narrative is written AFTER the verdict exists and is appended BELOW it.
        narrative = narrate_fn(result) if narrate_fn is not None else None
        review_dir.mkdir(exist_ok=True)
        (review_dir / "review.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (review_dir / "campaign-review.md").write_text(
            render_markdown(result, narrative), encoding="utf-8"
        )
        return review_dir
    except Exception as exc:  # noqa: BLE001 - see module docstring: a review is not the run
        try:
            review_dir.mkdir(exist_ok=True)
            (review_dir / "review-error.json").write_text(
                json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - nothing left to do, and nothing worth failing for
            return None
        return review_dir
