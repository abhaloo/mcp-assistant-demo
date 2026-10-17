"""Eval-only instructions for reasoning-model RAG answer arms."""

# Eval-only; prod /api/ask never imports this into the RAG system prompt.
REASONING_FINAL_ANSWER_DISCIPLINE = """\
REASONING-MODEL ANSWER DISCIPLINE (eval):
- Do any private reasoning first if needed.
- Put only the user-facing answer in the final response content.
- Keep the answer concise, cite [Source N] markers you used, and do not narrate
  your chain-of-thought in the answer body.
"""
