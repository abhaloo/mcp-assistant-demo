"""
Prompt Registry: assembles modular prompt sections into complete prompts.
"""

import hashlib
from pathlib import Path

PIPELINE_INSTRUCTIONS: dict[str, str] = {
    "document_rag": """PIPELINE: DOCUMENT RAG
- Answer based on the provided document context below.
- Be specific: quote prices, specs, and details from the context when available.
- If asked about something outside printing services, politely redirect.
- The context lists sources as [Source 1: <file>], [Source 2: <file>], …
  End every factual claim you draw from a source with its marker, e.g. "[Source 2]".
  Cite ONLY sources you actually used. If the context does not contain the answer,
  say so and cite nothing.""",
    "router": """PIPELINE: QUERY ROUTER
Classify the user's question into one of three categories.

SEMANTIC — Questions about what things are, how processes work, company policies,
capabilities, specifications, or general knowledge. These are answered by searching
through company documents.
Examples:
- "What paper stocks do you offer?"
- "How does the approval workflow work?"
- "What's included in the hotel printing package?"
- "What is our refund policy?"

STRUCTURED — Questions about specific quantities, counts, dates, totals, status,
or current state of business data. These need live database queries to answer.
Examples:
- "How many orders this week?"
- "What's pending approval right now?"
- "Which customers ordered last month?"
- "Total revenue in March?"
- "List all products in the stationery category."
- "Which invoices had printing this week?"

BOTH — Questions that explicitly need information from company documents AND
live business data to answer fully. This is rare — only use when the question
clearly requires both a policy/process explanation AND specific numbers/records.
Examples:
- "What printing services do we offer and how many orders has each generated?"
- "Explain our billing process and show the current pending invoices."

If in doubt between SEMANTIC and STRUCTURED, choose STRUCTURED — a database
query that returns no useful results is easier to recover from than a document
search that hallucinates numbers.

Respond with exactly one word: SEMANTIC, STRUCTURED, or BOTH""",
}

PIPELINE_SECTIONS: dict[str, list[str]] = {
    "document_rag": [
        "business_context",
        "safety_guardrails",
        "output_format",
        "pipeline_instructions",
    ],
    "sql_agent": [
        "business_context",
        "safety_guardrails",
        "output_format",
        "sql_business_rules",
    ],
    "router": ["pipeline_instructions"],
}

STATIC_DIR = Path(__file__).parent / "sections" / "static"


class PromptRegistry:
    """Joins known static strings."""

    def __init__(self) -> None:
        self._static_sections: dict[str, str] = {}

    def load_static(self, name: str, path: Path) -> None:
        self._static_sections[name] = path.read_text(encoding="utf-8")

    def assemble(self, pipeline: str) -> str:
        parts = []
        for name in PIPELINE_SECTIONS[pipeline]:
            if name == "pipeline_instructions":
                parts.append(PIPELINE_INSTRUCTIONS[pipeline])
            else:
                parts.append(self._static_sections[name])
        return "\n---\n".join(parts)

    def version(self, pipeline: str) -> str:
        """Content hash — stamped on every request (ADR 0019)."""
        return hashlib.sha256(self.assemble(pipeline).encode("utf-8")).hexdigest()[:12]


registry = PromptRegistry()
registry.load_static("business_context", STATIC_DIR / "business_context.md")
registry.load_static("safety_guardrails", STATIC_DIR / "safety_guardrails.md")
registry.load_static("output_format", STATIC_DIR / "output_format.md")
registry.load_static("sql_business_rules", STATIC_DIR / "sql_business_rules.md")
