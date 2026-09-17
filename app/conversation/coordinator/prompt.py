"""Coordinator prompt formatting, literal system instructions, and message assembly."""

from __future__ import annotations

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate

from app.conversation.coordinator.contracts import CoordinatorContext, Observation

COORDINATOR_SYSTEM_PROMPT = """You are the conversational coordinator for Ask AI.
Your role is to assist the user by answering questions, explaining past answers,
retrieving documents, or querying business data.
You govern and coordinate specialist tools. On each turn, you decide exactly ONE
action from the allowed actions.

Available Actions:
- query_business: Run fresh analytics over structured company business data
  (metrics, revenues, jobs, bills, invoices).
- search_documents: Retrieve company policy, documentation, guides, or contracts
  via semantic search.
- explain_sources: Restore and explain an earlier result using source candidate
  positions (1-8).
- finish_answer: Provide the final answer composed of structured blocks (general,
  evidence, or suggestion). Evidence blocks must name valid evidence IDs.
- clarify: Ask the user a short, direct clarifying question when their request
  is ambiguous or underspecified.

Guidance and Examples:
1. Explain past result: If the user asks to explain, unpack, or break down an
   earlier answer (e.g., "Why was revenue down?", "Explain that earlier figure",
   "Where did that number come from?"), choose `explain_sources` naming the
   relevant candidate position(s).
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
- After a business query succeeds, finish the answer from its observation. Do not
  explain, search or query again for the same question.
- If the business data is not available to this person, say so in one sentence and
  answer the rest from the documents you have.
- Never invent or assume facts without evidence from observations or restored sources.
- Never invent evidence IDs. General and suggestion blocks must NOT have evidence IDs.
"""

DEFAULT_COORDINATOR_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", COORDINATOR_SYSTEM_PROMPT),
        (
            "human",
            "Context:\n{context_block}\n\n"
            "Allowed Actions: {allowed_actions}\n\n"
            "Prior Observations:\n{observations_block}\n\n"
            "Current Question: {question}",
        ),
    ]
)


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
        context_parts: list[str] = [f"Turn ID: {context.turn_id}"]
        if context.history:
            context_parts.append("History:")
            for h in context.history:
                context_parts.append(f"  [{h.exchange_id}] User: {h.user_text}")
        if context.candidates:
            context_parts.append("Source Candidates:")
            for c in context.candidates:
                context_parts.append(
                    f'  Position {c.position}: [{c.exchange_id}] ({c.grain}) "{c.user_question}"'
                )
        if context.capabilities:
            context_parts.append(f"Capabilities: {sorted(context.capabilities)}")
        if context.catalog_descriptions:
            context_parts.append("Catalog:")
            for desc in context.catalog_descriptions:
                context_parts.append(f"  - {desc}")

        context_block = "\n".join(context_parts)

        if observations:
            obs_parts: list[str] = []
            for obs in observations:
                values_str = ", ".join(f"{v.label}={v.formatted}" for v in obs.values)
                ev_str = f" evidence_ids={obs.evidence_ids}" if obs.evidence_ids else ""
                val_str = f" values=[{values_str}]" if values_str else ""
                obs_parts.append(
                    f"- Action {obs.action_id} ({obs.kind}): status={obs.status}{ev_str}{val_str}\n"
                    f"  Summary: {obs.summary}"
                )
            observations_block = "\n".join(obs_parts)
        else:
            observations_block = "None"

        allowed_str = ", ".join(sorted(allowed_actions))

        prompt_value = self._template.invoke(
            {
                "context_block": context_block,
                "allowed_actions": allowed_str,
                "observations_block": observations_block,
                "question": context.question,
            }
        )
        return list(prompt_value.to_messages())
