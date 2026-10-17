"""One sentence that says what the rows are and how many anchors they cover."""

from __future__ import annotations

from app.business_query.definitions.schema import DefinitionBundle
from app.business_query.outcomes import RowIdentity
from app.business_query.plan.plan_diff import PlanDigest


def _plural(noun: str, count: int) -> str:
    return noun if count == 1 else f"{noun}s"


def row_identity_sentence(identity: RowIdentity, *, scoped: bool = False) -> str:
    """``scoped`` is true when the principal's forced predicates narrow the child; then a
    childless anchor has no *visible* children."""
    visible = "visible " if scoped else ""
    anchors = f"{identity.anchor_count} {_plural(identity.anchor, identity.anchor_count)}"
    requested = identity.requested_anchor_count
    if requested is not None and identity.anchor_count < requested:
        plural_anchor = _plural(identity.anchor, requested)
        anchors = f"{identity.anchor_count} of the {requested} {plural_anchor} on this page"
    if identity.expanded is None:
        return f"{anchors}."
    rows = f"{identity.row_count} {identity.expanded} {_plural('row', identity.row_count)}"
    if identity.anchors_without_children == 0:
        return f"{anchors}, {rows}."
    childless = identity.anchors_without_children
    verb = "has" if childless == 1 else "have"
    return (
        f"{anchors}, {rows}; {childless} {_plural(identity.anchor, childless)} "
        f"{verb} no {visible}{_plural(identity.expanded, 2)}."
    )


def change_note_line(
    changes: tuple[str, ...],
    before: PlanDigest,
    after: PlanDigest,
    *,
    bundle: DefinitionBundle | None = None,
) -> str:
    """One line that names what the continuation changed. Empty when nothing changed."""
    if not changes:
        return ""
    labels: dict[str, str] = {}
    if bundle is not None:
        for dimension in bundle.dimensions:
            if dimension.label:
                labels[dimension.name] = dimension.label.casefold()
    added = [member for member in after.dimensions if member not in before.dimensions]
    added.extend(member for member in after.measures if member not in before.measures)
    parts: list[str] = []
    if added:
        named = ", ".join(labels.get(member, member) for member in added)
        parts.append(f"Added: {named}.")
    parts.append("Kept: the same selection, the same order.")
    return " ".join(parts)
