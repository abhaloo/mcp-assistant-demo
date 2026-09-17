"""Definitional rules channel (A3D) — the SEMANTIC-memory half of the loop.

A confirmed DEFINITIONAL failure (failure_kind="definitional") yields a generalizable rule the
human writes in the record's `rule` field. Unlike A3 exemplars (retrieved per question), rules
are ALWAYS-ON: rendered into one block injected near the top of the SQL system prompt, the way
ESCALATED_PAYMENT_RULES already pins "fully paid = |value-payments| <= 0.01". Rules generalize
where examples mislead (FollowRAG/Mu) — which is exactly why definitional knowledge lives here,
not in A3. Eval- and production-sourced are both valid: a definition is a definition.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def load_definitional_rules(
    failure_dir: str | Path,
    *,
    exclude_case_ids: set[str] | None = None,
    allowed_sources: set[str] | None = None,
) -> list[str]:
    """Confirmed definitional rules, de-duplicated, in filename order."""
    exclude = exclude_case_ids or set()
    rules: list[str] = []
    seen: set[str] = set()
    for fp in sorted(Path(failure_dir).glob("*.json")):
        if fp.name.startswith("_") or fp.name == "episodic_exemplars.json":
            continue
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if rec.get("case_id") in exclude:
            continue
        if allowed_sources is not None and rec.get("source") not in allowed_sources:
            continue
        if rec.get("status") == "confirmed" and rec.get("failure_kind") == "definitional":
            rule = (rec.get("rule") or "").strip()
            if rule and rule not in seen:
                seen.add(rule)
                rules.append(rule)
    return rules


def rules_fingerprint(rules: list[str]) -> str:
    payload = json.dumps(rules, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def build_rules_block(rules: list[str]) -> str:
    if not rules:
        return ""
    body = "\n".join(f"- {r}" for r in rules)
    return (
        "\nLEARNED RULES (confirmed definitions for this database — apply whenever "
        "relevant):\n" + body + "\n"
    )
