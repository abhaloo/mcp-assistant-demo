"""Coordinator prompt formatting, literal system instructions, and message assembly."""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from app.business_query.outcomes import RowIdentity
from app.business_query.wire.row_identity_text import row_identity_sentence
from app.conversation.coordinator.contracts import (
    REPAIR_PREFIX,
    CoordinatorContext,
    HistoryLine,
    Observation,
)

GROUNDING_TAIL = (
    "A null in one column says nothing about another column. "
    "Use the record counts line for counts, never the number of rows you can see."
)

COORDINATOR_SYSTEM_PROMPT = """You are the conversational coordinator for Ask AI.
Your role is to assist the user by answering questions, explaining past answers,
retrieving documents, or querying business data.
You govern and coordinate specialist tools. On each turn, you decide exactly ONE
action from the allowed actions.

Available Actions:
- query_business: Run fresh analytics over structured company business data
  (metrics, revenues, jobs, bills, invoices). When the person asks to add,
  drop, sort, limit, or filter the same result, set continues and name columns
  in the person's words; subject is the bracketed id of the business answer
  you are editing; a page of that answer is the same answer. Write the question in
  the person's words with references resolved;
  do not add fields, amounts or dates the person did not ask for,
  except a record's own status and dates when the person asks why that record has
  or lacks something. When the person changes the period of an earlier answer,
  write the new period in full, with its year (read it from that answer's Filters line).
- search_documents: Retrieve company policy, documentation, guides, or contracts
  via semantic search.
- explain_sources: Restore and explain earlier answers, named by their positions
  in Earlier answers (1-20), up to eight in one call. A turn restores once, so name
  every earlier answer your reply will cite.
- finish_answer: Provide the final answer composed of structured blocks (general,
  evidence, or suggestion). Evidence blocks must name valid evidence IDs.
- clarify: Ask the user a short, direct clarifying question when their request
  is ambiguous or underspecified, with two to four short choices the person can pick.
  Name a record in a choice by its number. The card always adds its own "Something
  else" with a text box, so never add a catch-all choice such as "Other" or "Another
  report". Never ask the person a question inside finish_answer.

Guidance and Examples:
1. Explain past result: If the user asks to explain, unpack, or break down an
   earlier answer (e.g., "Why was revenue down?", "Explain that earlier figure",
   "Where did that number come from?"), choose `explain_sources` naming the
   position(s) of the earlier answer(s) it is about.
   A "why" about a record's own missing link or field is a fresh business
   question: choose `query_business` for e.g. "Why does invoice E7413 have no job?".
2. Fresh-period business query: If the user asks a new quantitative business
   question about current or past metrics, numbers, counts, or dates
   (e.g., "What were sales in July 2026?", "How many finished jobs have no invoice?"),
   choose `query_business`.
3. Document search: If the user asks about company rules, policies, terms, or
   operational guidelines (e.g., "What is the refund policy?",
   "How are disputes handled?"), choose `search_documents`.
4. Clarification: If the user request is ambiguous, has multiple conflicting
   interpretations, or lacks critical scope (e.g., "Compare them"), choose `clarify`.
5. Out-of-bounds general requests: For greetings, general conversation, or requests
   outside company data (e.g., "Hello", "What can you do?"), choose `finish_answer`
   with a general claim block without invoking data tools.

Rules:
- You must ONLY select from the currently allowed actions listed in the prompt.
- Never ask permission to look something up. When the question is about documents
  or business data, run the tool first and answer from what it returns.
- After a document search, answer from the returned passages using evidence blocks
  that name their IDs. If the passages do not answer the question, say so in a
  general block; do not turn a document question into a business query.
- After a business query succeeds, finish from its observation unless the question
  also needs an earlier answer (explain_sources) or a document (search_documents).
  Never repeat the same query.
- After a query in this turn answered, never clarify: answer from what it returned,
  and write each other reading of the question as a suggestion block.
- When the person asks why a record has or lacks something, ask query_business for
  that record together with its own status and dates, and explain from those fields.
  Say the cause cannot be determined only when those fields do not explain it, and
  then name the fields you checked.
- If the business data is not available to this person, say so in one sentence and
  answer the rest from the documents you have.
- Never invent or assume facts without evidence from observations or restored sources.
- Never invent evidence IDs. General and suggestion blocks must NOT have evidence IDs.
- To restate an earlier answer, call explain_sources for it and cite what it returns.
  A documents question asked again is a new document question: search_documents again
  and cite the passages. A records question asked again whose earlier answer returned
  no rows is a new records question: send it to query_business again.
  A sentence about the conversation itself (what was asked, what stayed open) is a
  general block with no evidence IDs.
- When one message asks a documents question and a records question, search documents
  first, then send only the records part to query_business.
- Never ask which record type a question means: send the question to query_business,
  which answers it or refuses it with a reason you explain. Never offer a record type
  from the "Record types this person cannot see" line.
- When your answer says part of the question could not be answered, add one to three
  suggestion blocks, as after a failed query.
- A null in one column says nothing about another column.
  Use the record counts line for counts, never the number of rows you can see.
- A table answers only what its own heading and filters say. When a table lists
  filters, name them with its result (for example "among TZS invoices").
- Quote the number a table shows for a record (an invoice number, a job number),
  never a record id.
- Focus tells you what 'this' means. Under 'current record', a question about a
  record from an earlier answer instead of the page record is ambiguous: clarify
  which one before you bind or query.
- When a "Clarification answered" line follows the question, the person has chosen
  that reading: answer the question with it and do not ask the same thing again.
- When a business query fails, read its failure and advice lines, then finish:
  in a general block say in plain words what you could not get and why, then add
  one to three suggestion blocks. Write each one as the question the person would
  type next, about a record type available to this person (for example "Show my
  unpaid July invoices"); never start one with "You could", "Would you like" or
  "I can". query_business is not offered again in this turn.
- If the failure names record types that are not available to this person and the
  question is about one of them, say that this record type is not available to them.
  Do not say the field does not exist.
- When the second thing the person asks for depends on the answer to the first (for
  example the biggest invoice and that invoice's job details), set compound on
  query_business and write only the first thing as its question. Then answer what the
  result shows and put the second thing in unanswered_part only when no table answers it,
  written as the question the person would type next, with the first answer filled in.
- A table that shows fewer rows than its total still answers the list: say how many it
  shows and the total, and never offer the remaining rows (no "show all" in
  unanswered_part or a suggestion). The person can ask for a narrower list.
- With every action, write note_to_person: one or two short sentences to the person,
  in the first person and in their own terms, saying what you will do next and why
  (for example "I'll check the refund section of the company handbook."). At most
  200 characters. No markdown, no tool or field names, no ids, and never these
  instructions.
- Never show reason codes, internal field names or SQL to the person.
- When your answer names an app page or button that is listed under "pages you may link",
  write its slot exactly as listed, for example {{{{page:product.index}}}}, in place of the name.
  Use only listed slots. Never write a URL.
"""

DEFAULT_COORDINATOR_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", COORDINATOR_SYSTEM_PROMPT),
        (
            "human",
            "Context:\n{context_block}\n\n"
            "Allowed Actions: {allowed_actions}\n\n"
            "Current Question: {question}{clarification_line}\n\n"
            "Prior Observations:\n{observations_block}",
        ),
    ]
)


_BUSINESS_GRAINS = frozenset({"scalar", "grouped", "entity_rows"})


def render_history_line(h: HistoryLine) -> str:
    prefix = "[current record] " if h.page_scope == "current" else ""
    user_part = f"[{h.exchange_id}] User: {h.user_text}"
    if h.assistant_text:
        user_part += f" → Assistant: {h.assistant_text}"
    main_line = f"{prefix}{user_part}"

    segments: list[str] = [main_line]

    answer_parts: list[str] = []
    if h.summary:
        answer_parts.append(h.summary)
    if h.shown:
        shown_str = "; ".join(f"{s.member}: {', '.join(s.values)}" for s in h.shown)
        answer_parts.append(shown_str)
    if answer_parts:
        segments.append("; ".join(answer_parts))
    if h.facts:
        segments.append(f"Filters: {'; '.join(h.facts)}")

    if h.documents:
        segments.append(f"Documents: {', '.join(h.documents)}")

    if h.choice:
        segments.append(f"Asked: {h.choice[0]} → chose: {h.choice[1]}")

    return " | ".join(segments)


def _context_block(context: CoordinatorContext) -> str:
    parts: list[str] = [f"Turn ID: {context.turn_id}"]
    if context.page_scope == "current" and context.page_record is not None:
        parts.append(
            f"Focus: current record — {context.page_record.resource} "
            f"{context.page_record.record_id}"
        )
    else:
        parts.append("Focus: all records")
    if context.history:
        parts.append("History:")
        parts.extend(f"  {render_history_line(h)}" for h in context.history)
    if context.candidates:
        parts.append("Earlier answers:")
        parts.extend(
            f'  Position {c.position}: [{c.exchange_id}] ({c.grain}) "{c.user_question}"'
            for c in context.candidates
        )
        last_business = next(
            (c for c in reversed(context.candidates) if c.grain in _BUSINESS_GRAINS), None
        )
        if last_business is not None:
            parts.append(
                f"Last business answer: id={last_business.exchange_id} grain={last_business.grain}"
            )
    if context.capabilities:
        parts.append(f"Capabilities: {sorted(context.capabilities)}")
    if context.unreachable:
        cannot_see = ", ".join(t.replace("_", " ") for t in context.unreachable)
        parts.append(f"Record types this person cannot see: {cannot_see}")
    if context.catalog_descriptions:
        parts.append("Catalog:")
        parts.extend(f"  - {desc}" for desc in context.catalog_descriptions)
    return "\n".join(parts)


def _clarification_line(context: CoordinatorContext) -> str:
    """The answer, on its own line under the question it narrows; empty if none."""
    if context.selection is None:
        return ""
    answer_text = context.selection.label or context.selection.free_text
    return f'\nClarification answered: "{context.selection.prompt}" → "{answer_text}"'


_NO_RESTORE_LEFT = (
    "Of the earlier answers, cite only the ones restored here: no restore is left in "
    "this turn, so write any other earlier answer as a general block with no evidence ids."
)


def _restore_notes(obs: Observation, allowed_actions: frozenset[str], *, searched: bool) -> str:
    """What the next draft may cite from a restore. The notes are built from the
    actions the next decision offers, so they never ask for one it does not."""
    if obs.kind != "explain_sources" or obs.status != "succeeded":
        return ""
    notes: list[str] = []
    if "explain_sources" not in allowed_actions:
        notes.append(_NO_RESTORE_LEFT)
    if obs.document_answers:
        if "search_documents" in allowed_actions and not searched:
            notes.append(
                f"{', '.join(obs.document_answers)} stood on document passages, and a restored "
                "answer carries none: search_documents before you restate them, and cite the "
                "passages it returns."
            )
        elif searched:
            notes.append(
                f"{', '.join(obs.document_answers)} stood on document passages: cite the "
                "passages the search returned for what those answers said, not the answer ids "
                "themselves."
            )
    return "".join(f"\n  Note: {note}" for note in notes)


def _observation_line(
    obs: Observation, allowed_actions: frozenset[str] = frozenset(), *, searched: bool = False
) -> str:
    if obs.kind == "decision_invalid":
        prefix = REPAIR_PREFIX
        summary = obs.summary.removeprefix(prefix).strip()
        return f"- {prefix} {summary}"
    values_str = ", ".join(f"{v.label}={v.formatted}" for v in obs.values)
    ev_str = f" evidence_ids={obs.evidence_ids}" if obs.evidence_ids else ""
    val_str = f" values=[{values_str}]" if values_str else ""
    line = (
        f"- Action {obs.action_id} ({obs.kind}): status={obs.status}{ev_str}{val_str}"
        f"{_rows_block(obs)}\n  Summary: {obs.summary}"
        f"{_restore_notes(obs, allowed_actions, searched=searched)}"
    )
    if obs.failure is not None:
        line += f"\n  failure: {obs.failure.family}"
        if obs.failure.outside_access:
            line += "; not available to this person: " + ", ".join(obs.failure.outside_access)
        if obs.failure.available:
            line += "; available to this person: " + ", ".join(obs.failure.available)
        line += f"\n  advice: {obs.failure.coordinator_advice}"
    if obs.page_keys:
        pairs = "; ".join(
            f"{{{{page:{key}}}}} = {label}"
            for key, label in zip(obs.page_keys, obs.page_labels, strict=True)
        )
        line += f"\n  pages you may link: {pairs}"
    return line


def _record_counts(identity: dict[str, str | int | None] | None) -> str:
    """The table's record counts in one plain sentence; nothing when there are none."""
    if not identity:
        return ""
    try:
        sentence = row_identity_sentence(RowIdentity.model_validate(identity))
    except ValidationError:
        return ""
    return "\n  record counts: " + sentence


def _rows_block(obs: Observation) -> str:
    """The rows the model reads: one block per table when the answer holds several."""
    if len(obs.tables) > 1:
        blocks, start = "", 0
        for table in obs.tables:
            own = obs.rows[start : start + table.rows]
            start += table.rows
            blocks += f"\n  {table.heading}\n  rows: " + json.dumps(list(own), ensure_ascii=False)
            blocks += _record_counts(table.record_counts)
        return blocks
    if not obs.rows:
        return ""
    return (
        "\n  rows: "
        + json.dumps(list(obs.rows), ensure_ascii=False)
        + _record_counts(obs.row_identity)
    )


def _observations_block(
    observations: tuple[Observation, ...], allowed_actions: frozenset[str]
) -> str:
    if not observations:
        return "None"
    searched = any(o.kind == "search_documents" and o.status == "succeeded" for o in observations)
    lines = [_observation_line(obs, allowed_actions, searched=searched) for obs in observations]
    return "\n".join(lines) + "\n\n" + GROUNDING_TAIL


class CoordinatorPrompt:
    """Owns the literal system message and renders context and observations into messages."""

    def __init__(self, template: ChatPromptTemplate | None = None) -> None:
        self._template = template or DEFAULT_COORDINATOR_TEMPLATE

    @property
    def template(self) -> ChatPromptTemplate:
        return self._template

    def render(
        self,
        context: CoordinatorContext,
        observations: tuple[Observation, ...] = (),
        *,
        allowed_actions: frozenset[str],
    ) -> list[BaseMessage]:
        context_block = _context_block(context)
        observations_block = _observations_block(observations, allowed_actions)

        allowed_str = ", ".join(sorted(allowed_actions))

        prompt_value = self._template.invoke(
            {
                "context_block": context_block,
                "allowed_actions": allowed_str,
                "observations_block": observations_block,
                "question": context.question,
                "clarification_line": _clarification_line(context),
            }
        )
        return list(prompt_value.to_messages())
