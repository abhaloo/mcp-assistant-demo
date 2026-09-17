"""Start the canonical prod-ask-e2e run-plan stripper.

The filter lives in `.claude/skills/ai-e2e/assets/make_run_plan.py`. This file
only launches that program so `scripts/eval` stays a thin CLI (ADR 0066).
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ASSET = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "skills"
    / "ai-e2e"
    / "assets"
    / "make_run_plan.py"
)


def main() -> None:
    sys.argv[0] = str(_ASSET)
    runpy.run_path(str(_ASSET), run_name="__main__")


if __name__ == "__main__":
    main()
