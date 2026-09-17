"""Named changes between two coverage maps."""

from __future__ import annotations

from app.coverage.model import CoverageMap, FieldChange, pivot_by_column


def _cell(surfaces: set[str]) -> str:
    """Format a set of surfaces as a sorted, comma-delimited string."""
    return ",".join(sorted(surfaces))


def diff_maps(previous: CoverageMap, current: CoverageMap) -> list[FieldChange]:
    """Compute difference in surfaces and members between two coverage maps."""
    before = pivot_by_column(previous.rows)
    after = pivot_by_column(current.rows)
    changes: list[FieldChange] = []
    for key in sorted(set(before) | set(after)):
        b = _cell(before[key].surfaces) if key in before else "-"
        a = _cell(after[key].surfaces) if key in after else "-"
        members_moved = key in before and key in after and before[key].members != after[key].members
        if b == a and members_moved:
            a = f"{a} (members changed)"
        if b != a:
            changes.append(FieldChange(key=key, before=b, after=a))
    return changes


def render_diff(changes: list[FieldChange]) -> str:
    """Render field changes as a markdown table or report no change."""
    if not changes:
        return "# Coverage diff\n\nNo field moved.\n"
    lines = ["# Coverage diff", "", "| column | before | after |", "|---|---|---|"]
    lines += [f"| `{c.key}` | {c.before} | {c.after} |" for c in changes]
    return "\n".join(lines) + "\n"
