"""One line of advice per refusal rule, in the words the coordinator may repeat to the person."""

# No member keys, no schema words, no "card": the rule id picks the line and never appears in it.
_COORDINATOR_RULE_ADVICE: dict[str, str] = {
    "order_member_not_selected": (
        "The list could not be sorted by that amount. Offer the list sorted by each "
        "record's own amount, or a total per group instead."
    ),
    "not_a_measure": (
        "That field is not a figure that can be totalled; offer the figures that can."
    ),
    "not_a_dimension": "A total cannot be a column in a list; offer the total on its own.",
    "not_a_bucket_set": "That grouping is not available; offer the groupings that are.",
    "unknown_member": (
        "The records do not keep a detail the question asks for: say which part of the "
        "question it was, then offer one to three questions about what the records keep."
    ),
    "planner_unsupported": (
        "This cannot be answered from the records this person can see. When the "
        "question is about a record type the failure lists as not available to this "
        "person, say that this record type is not available to them. Then offer one "
        "to three questions about the record types this person can use."
    ),
    "companion_depends_on_answer": (
        "One part of the question needs the answer to another part, and one query "
        "cannot do both. Say which part needs the other's answer, then offer one to "
        "three questions the person can ask one at a time, the first part first."
    ),
}
_COORDINATOR_CODE_ADVICE: dict[str, str] = {
    "fanout_unsafe": (
        "Two kinds of related records cannot be listed together; offer one kind at a time."
    ),
    "grain_unexpressible": (
        "The records do not keep this figure at the level asked (for example a value "
        "per job). Say that, then offer one to three questions at a level the records "
        "keep, such as per invoice or per customer."
    ),
    "needs_prior_answer": (
        "One part of the question needs the answer to another part first, and one "
        "query cannot do both. Say that, then offer one to three questions the person "
        "can ask one at a time: the first asks for the first part on its own."
    ),
    "no_join_path": "Those records are not connected; offer a question about one kind.",
    "value_not_found": "Nothing matches that name or number; ask the person to check it.",
}
_COORDINATOR_GENERIC = (
    "This could not be answered as asked: say which part of the question it was and why, "
    "then offer one to three questions about the record types this person can use."
)


def coordinator_advice_for(reason_code: str, rule: str | None) -> str:
    """The advice line the coordinator reads, in plain business words."""
    if rule == "planner_unsupported" and reason_code in _COORDINATOR_CODE_ADVICE:
        return _COORDINATOR_CODE_ADVICE[reason_code]
    if rule is not None and rule in _COORDINATOR_RULE_ADVICE:
        return _COORDINATOR_RULE_ADVICE[rule]
    return _COORDINATOR_CODE_ADVICE.get(reason_code, _COORDINATOR_GENERIC)
