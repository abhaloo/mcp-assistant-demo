#!/usr/bin/env python3
"""Run Query Record Alembic migrations (ACA Job / deploy pre-step)."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
