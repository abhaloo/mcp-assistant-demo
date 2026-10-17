"""The prose half of the campaign review -- and the half that cannot decide anything.

R3 of docs/superpowers/specs/2026-08-02-campaign-console-and-review-design.md.

WHY THE NARRATOR CANNOT CHANGE THE VERDICT
==========================================
Not "is instructed not to" -- CANNOT. ``review_attempt`` produces the verdict, the
report stamps it, and this module returns PROSE that is appended underneath. There is
no code path from a model's output to ``ReviewResult.verdict``. A narrator that decided
the verdict would be a reviewer you could talk out of a finding, which is the failure
mode the whole deterministic half exists to avoid.

Ordering alone secures the MACHINE reading -- ``review.json``, ``Finding.to_dict()``,
``review_campaign.py``'s exit code -- and it is not sufficient for the HUMAN one:
prose in the same markdown document could open a second ``**Verdict:`` line that a
skimming reader takes for the real one. ``narrative_section`` blockquotes the prose so
that is unrepresentable too.

So the worst a broken, biased or prompt-injected narrator can do is write misleading
paragraphs, visibly quoted, beneath a verdict that is already correct and already
machine-readable in ``review.json``.

WHAT IT IS ALLOWED TO SEE
=========================
Findings only: ids, statuses, and the structural/numeric evidence the checks produced.
Never raw answers, never retrieved context, never a case's text. The corpus is
role-partitioned company data and the questions are real; there is no version of "send
the answers to a model so it can write nicer prose" that is worth that.

THE THIRD RISK THE DESIGN ACCEPTED
==================================
An LLM call inside a spend-staged critical path. Paid for three ways:

* its own ledger phase (``campaign_review``) with its own small cap, so it cannot
  consume budget a campaign needed;
* its reservation is taken AFTER the campaign's are closed (``Campaign`` calls the
  review past the final settle), so it can never hold money open for the matrix;
* if the budget is unavailable for ANY reason, the deterministic review still stands
  and the narrative section reads ``narrative skipped: <reason>``. A missing paragraph
  is not a missing verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.experiments.core.secret_redaction import redact_secrets
from app.experiments.review.checks import (
    FAIL,
    NOT_APPLICABLE,
    NOT_EVALUABLE,
    PASS,
    VERDICT_STAMP,
    ReviewResult,
)

__all__ = [
    "NARRATIVE_CAP_USD",
    "NARRATIVE_PHASE",
    "Narrative",
    "build_prompt",
    "narrate",
]

#: The narrator's own ledger phase. Separate from every campaign phase so a narrative
#: can never be the reason a matrix could not afford its judge.
NARRATIVE_PHASE = "campaign_review"

#: Per-narrative reservation. One short structured summary in, a few paragraphs out.
NARRATIVE_CAP_USD = 0.05


@dataclass(frozen=True)
class Narrative:
    """Prose, or the reason there is none. Never a verdict."""

    text: str | None = None
    skipped_reason: str | None = None

    @property
    def section(self) -> str:
        if self.text:
            return self.text
        return f"_narrative skipped: {self.skipped_reason or 'unknown reason'}_"


def build_prompt(result: ReviewResult) -> str:
    """The compact, corpus-free summary the narrator is given.

    Deliberately assembled from the FINDINGS rather than from the attempt directory:
    the findings are structural (counts, shares, p-values, file names), so there is no
    path by which a case's question, a retrieved context or a model's answer reaches
    the prompt.

    It is still swept with ``redact_secrets`` on the way out. "Structural" is a property
    of what the checks write TODAY, enforced by nothing -- an earlier version of this
    docstring claimed no redaction was needed, and at that moment
    ``frozen_contract_available`` was interpolating an absolute filesystem path
    (operator username, local directory layout) straight into this prompt. The check
    now reports a bare directory name, but the sweep stays: this is the one string in
    the review that leaves the machine, and a guard on the boundary costs one call.
    """
    lines = [
        "You are reviewing the METHODOLOGY of a completed AI evaluation campaign.",
        "",
        f"The verdict is already decided: {result.verdict}. You cannot change it, and",
        "you must not argue with it. Your job is to explain, for an engineer who was",
        "not watching the run, what the findings below mean in practice and what they",
        "should now do differently.",
        "",
        "Write three short sections: What happened. What is wrong with it. What this",
        "run does not let you claim. Be concrete and specific to the evidence. Do not",
        "invent numbers that are not below. Do not congratulate.",
        "",
        f"Attempt: {result.attempt_id}",
        f"Criteria version: {result.criteria_version}",
        f"Review trigger: {result.trigger}",
        "",
        "FINDINGS",
    ]
    for finding in result.findings:
        marker = {
            PASS: "PASS",
            FAIL: "FAIL",
            NOT_APPLICABLE: "DID NOT APPLY",
            NOT_EVALUABLE: "COULD NOT BE CHECKED",
        }[finding.status]
        lines.append(f"- [{marker}] {finding.id} ({finding.severity}): {finding.evidence}")
        if finding.status == FAIL:
            lines.append(f"    does not license: {finding.criterion.does_not_license}")
    prompt = "\n".join(lines)
    return redact_secrets(prompt) or prompt


def narrate(
    result: ReviewResult,
    *,
    complete_fn: Callable[[str], tuple[str, float]] | None = None,
    reserve_fn: Callable[[float], str] | None = None,
    commit_fn: Callable[[str, float], None] | None = None,
    release_fn: Callable[[str], None] | None = None,
    cap_usd: float = NARRATIVE_CAP_USD,
) -> Narrative:
    """Write the prose section, or say why there is none. NEVER raises.

    ``complete_fn`` takes the prompt and returns ``(text, cost_usd)``. Injected rather
    than imported so that the default -- in ``Campaign``, in every test, and in every
    replay -- is a review that makes NO provider call at all. Narration is opt-in; a
    campaign that was never handed a completion function simply does not narrate.

    The reservation is settled on every path. A narrator that raised after the ledger
    reserved would otherwise leak its phase budget on each attempt, and a phase this
    small would be exhausted by a handful of failures.
    """
    if complete_fn is None:
        return Narrative(skipped_reason="no completion function configured")

    reservation_id: str | None = None
    try:
        if reserve_fn is not None:
            reservation_id = reserve_fn(cap_usd)
    except Exception as exc:  # noqa: BLE001 - an unaffordable narrative is not a failure
        return Narrative(skipped_reason=f"budget unavailable ({type(exc).__name__}: {exc})")

    try:
        text, cost_usd = complete_fn(build_prompt(result))
    except Exception as exc:  # noqa: BLE001 - see module docstring: prose is never fatal
        if reservation_id is not None and release_fn is not None:
            try:
                release_fn(reservation_id)
            except Exception:  # noqa: BLE001 - ledger failure must not mask the real one
                pass
        return Narrative(skipped_reason=f"narrator failed ({type(exc).__name__}: {exc})")

    if reservation_id is not None and commit_fn is not None:
        try:
            commit_fn(reservation_id, float(cost_usd))
        except Exception as exc:  # noqa: BLE001 - the prose is already written
            # The text is kept: the call happened and the money is spent either way, and
            # discarding the output would waste it to report a ledger problem that the
            # ledger itself will surface.
            return Narrative(
                text=str(text),
                skipped_reason=f"spend not booked ({type(exc).__name__}: {exc})",
            )
    return Narrative(text=str(text))


def narrative_section(narrative: Narrative | None) -> list[str]:
    """The markdown appended BELOW the stamped verdict, never above it -- and QUOTED.

    Every line is prefixed with ``> ``. Being below the verdict is enough to stop the
    narrator changing ``review.json`` or the CLI's exit code, but it is NOT enough to
    stop it changing what a person reads: raw model output in the same document can
    open its own ``# Campaign review`` heading and its own ``**Verdict: SOUND**`` line,
    and a reader skimming -- or a tool grepping for the verdict -- can be shown the
    second one. Inside a blockquote neither is a heading and neither is top-level, so
    the document has exactly one verdict line however hostile the prose.
    """
    if narrative is None:
        return []
    quoted = [f"> {_defang(line)}" if line else ">" for line in narrative.section.splitlines()]
    return ["## Narrative", "", *quoted, ""]


def _defang(line: str) -> str:
    """Neutralise the document's own verdict stamp inside prose.

    Blockquoting alone is not enough. It stops a narrator opening a heading, but
    ``> **Verdict: SOUND**`` still renders as a bold verdict line and still matches a
    tool grepping the report for ``**Verdict: ``. Escaped rather than deleted: the
    reader sees exactly what the narrator wrote, it simply cannot be mistaken for -- or
    counted as -- the stamped answer.
    """
    return line.replace(VERDICT_STAMP, r"\*\*Verdict:")
